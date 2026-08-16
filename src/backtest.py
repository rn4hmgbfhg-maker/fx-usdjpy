# -*- coding: utf-8 -*-
"""ドル円 自動売買バックテストエンジン

前提・執行モデル:
  - 日足ベース。シグナルはバー引け(Close)で判定し、翌バーの寄付(Open)で約定する
    （ルックアヘッドバイアス防止）。
  - コスト: スプレッド+スリッページとして片道0.4銭(0.004円)を約定ごとに控除
    （国内主要業者の原則固定0.2銭に滑りを上乗せした保守的設定）。
  - スワップポイント(金利差損益)は「検証では」モデル化しない。15年分の付与実績が
    手元に無く、現在の金利差を過去に当てはめると成績が歪むため。実運用側の損益は
    2026-08-13からスワップ込みで計上する(src/swap.py)ので、検証のPF・勝率と
    実運用の損益は前提が異なる点に注意する。

リスク管理(エンジン組込み):
  - 1トレードのリスクは口座資金の1%(ATRベースの初期ストップ幅から数量を逆算)
  - シャンデリア式ATRトレイリングストップ(建玉後の最良値からATR×係数)
  - 実効レバレッジ上限3倍で数量をキャップ
  - 口座資金がピークから20%減で新規建て停止(キルスイッチ)
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 指標
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


# ---------------------------------------------------------------- エンジン
class Backtester:
    def __init__(self, df: pd.DataFrame,
                 initial_capital: float = 1_000_000.0,   # 円
                 risk_per_trade: float = 0.01,            # 資金の1%
                 atr_stop_mult: float = 3.0,              # 初期/トレイリングストップ = ATR×3
                 max_leverage: float = 3.0,               # 実効レバレッジ上限
                 cost_per_side: float = 0.004,            # 円/USD 片道(スプレッド+滑り)
                 dd_kill_switch: float = 0.20,            # ピーク比20%減で新規停止
                 tp_atr_mult: float = None):              # 固定利確 = ATR×係数(None=無し)
        self.df = df.reset_index(drop=True).copy()
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.atr_stop_mult = atr_stop_mult
        self.max_leverage = max_leverage
        self.cost_per_side = cost_per_side
        self.dd_kill_switch = dd_kill_switch
        self.tp_atr_mult = tp_atr_mult

    def run(self, signal: pd.Series) -> dict:
        """signal: 各バー引け時点の目標ポジション(+1ロング/-1ショート/0ノーポジ)。
        翌バーOpenで執行する。"""
        df = self.df
        sig = signal.reindex(df.index).fillna(0).astype(int)
        atr_s = atr(df)

        equity = self.initial_capital
        peak = equity
        pos = 0          # 現在ポジション方向
        units = 0.0      # 保有USD数量
        entry = 0.0      # 建値
        stop = np.nan    # 現在のストップ水準
        tp = np.nan      # 固定利確水準(使用時のみ)
        best = np.nan    # 建玉後の最良終値(トレイリング基準)
        halted = False   # キルスイッチ発動フラグ

        equity_curve = []
        trades = []      # (entry_date, exit_date, dir, entry, exit, pnl)
        ent_date = None

        def close_position(price, date, reason):
            nonlocal equity, pos, units, entry, stop, tp, best, ent_date
            pnl = units * (price - entry) * pos - units * self.cost_per_side
            equity += pnl
            trades.append({"entry_date": ent_date, "exit_date": date,
                           "dir": "L" if pos > 0 else "S",
                           "entry": entry, "exit": price, "pnl": pnl,
                           "reason": reason})
            pos = 0; units = 0.0; stop = np.nan; tp = np.nan
            best = np.nan; ent_date = None

        for i in range(1, len(df)):
            o, h, l, c = df.loc[i, ["Open", "High", "Low", "Close"]]
            date = df.loc[i, "Date"]
            a = atr_s.iloc[i - 1]

            # --- 1) 寄付: 前日引けシグナルに基づく執行
            target = sig.iloc[i - 1] if not halted else 0
            if pos != 0 and target != pos:
                close_position(o, date, "signal")
            if pos == 0 and target != 0 and not halted and not np.isnan(a) and a > 0:
                stop_dist = self.atr_stop_mult * a
                risk_units = (equity * self.risk_per_trade) / stop_dist
                lev_units = (equity * self.max_leverage) / o
                units = min(risk_units, lev_units)
                pos = target
                equity -= units * self.cost_per_side  # 建玉時の片道コスト
                entry = o
                best = o
                stop = o - pos * stop_dist
                if self.tp_atr_mult:
                    tp = o + pos * self.tp_atr_mult * a
                ent_date = date

            # --- 2) バー中: ストップ/利確判定(High/Lowで到達チェック)
            #     同一バーで両方到達し得る場合は不利な方(ストップ)を優先(保守的)
            if pos > 0 and l <= stop:
                close_position(stop, date, "stop")
            elif pos < 0 and h >= stop:
                close_position(stop, date, "stop")
            elif pos > 0 and not np.isnan(tp) and h >= tp:
                close_position(tp, date, "tp")
            elif pos < 0 and not np.isnan(tp) and l <= tp:
                close_position(tp, date, "tp")

            # --- 3) 引け: トレイリングストップ更新
            if pos != 0 and not np.isnan(atr_s.iloc[i]):
                if pos > 0:
                    best = max(best, c)
                    stop = max(stop, best - self.atr_stop_mult * atr_s.iloc[i])
                else:
                    best = min(best, c)
                    stop = min(stop, best + self.atr_stop_mult * atr_s.iloc[i])

            # --- 4) 評価損益込みのエクイティとキルスイッチ
            mtm = equity + (units * (c - entry) * pos if pos != 0 else 0.0)
            peak = max(peak, mtm)
            if mtm < peak * (1 - self.dd_kill_switch):
                halted = True   # 新規建て停止(既存玉はストップ/シグナルで決済)
            equity_curve.append({"Date": date, "Equity": mtm})

        # 期末に建玉が残っていれば最終値で決済
        if pos != 0:
            close_position(df["Close"].iloc[-1], df["Date"].iloc[-1], "eod")
            equity_curve[-1]["Equity"] = equity

        ec = pd.DataFrame(equity_curve)
        tr = pd.DataFrame(trades)
        return {"equity_curve": ec, "trades": tr, "halted": halted,
                "metrics": self._metrics(ec, tr)}

    def _metrics(self, ec: pd.DataFrame, tr: pd.DataFrame) -> dict:
        eq = ec["Equity"]
        ret_total = eq.iloc[-1] / self.initial_capital - 1
        years = len(eq) / 252
        cagr = (eq.iloc[-1] / self.initial_capital) ** (1 / years) - 1 if years > 0 else np.nan
        daily_ret = eq.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(252)
                  if daily_ret.std() > 0 else np.nan)
        dd = (eq / eq.cummax() - 1).min()
        if len(tr):
            wins = tr[tr.pnl > 0]
            losses = tr[tr.pnl <= 0]
            win_rate = len(wins) / len(tr)
            pf = (wins.pnl.sum() / -losses.pnl.sum()
                  if len(losses) and losses.pnl.sum() < 0 else np.nan)
        else:
            win_rate, pf = np.nan, np.nan
        return {
            "最終資産(円)": round(eq.iloc[-1]),
            "総リターン": ret_total,
            "年率リターン(CAGR)": cagr,
            "シャープレシオ": sharpe,
            "最大ドローダウン": dd,
            "取引回数": len(tr),
            "勝率": win_rate,
            "プロフィットファクター": pf,
        }
