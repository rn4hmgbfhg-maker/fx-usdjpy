# -*- coding: utf-8 -*-
"""エントリー確認フィルタ（テクニカル多系統） — デイトレ2H系・15分系 共用

ドンチャンブレイクの「精度」を上げるため、性質の異なる5系統の
テクニカル指標でエントリーを選別する確認フィルタ群。

  1. adx          : ADX(Wilder) がしきい値以上 = トレンド相場のみ許可
                    （レンジ内のダマシブレイクを弾く。方向は問わない）
  2. rsi          : RSI が 50±マージンで方向一致した時のみ許可
                    （モメンタムの順行確認）
  3. macd         : MACDライン vs シグナルラインの上下で方向一致のみ許可
  4. ma_consensus : EMA/SMA(10/20/50/100/200)の多数決スコアが
                    しきい値以上で方向一致のみ許可（TradingView型MA合議）
  5. vol          : ATR(14) が過去分位以上 = ボラ拡張局面のみ許可
                    （閑散相場のブレイクは追わない。方向は問わない）

仕様:
  - entry_mask(df, spec) が (allow_long, allow_short) のbool配列を返す。
    spec は単体dictまたはdictのリスト（リストはAND結合）。
  - すべて各バー引け時点までのデータのみで計算する（判定=引け、
    執行=翌バー寄付という既存の執行モデルと同じ。ルックアヘッド無し）。
  - ウォームアップ中(NaN)は「許可しない」＝保守的に新規を見送る。
  - 研究(research_intraday.py / research_15m.py)とエンジン
    (intraday_engine.py / intraday15_engine.py)が本モジュールを共用し、
    探索と本番の判定を常に一致させる。

specの形式（configにもこのまま保存される）:
  {"種別": "adx", "期間": 14, "閾値": 20}
  {"種別": "rsi", "期間": 14, "マージン": 0}
  {"種別": "macd", "短期": 12, "長期": 26, "シグナル": 9}
  {"種別": "ma_consensus", "閾値": 0.5}
  {"種別": "vol", "参照本数": 200, "分位": 0.3}
"""
import numpy as np
import pandas as pd

from backtest import rsi


# ---------------------------------------------------------------- 指標
def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's ADX。トレンドの強さ(0-100)。方向は含まない。"""
    up = df["High"].diff()
    dn = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0),
                        index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0),
                         index=df.index)
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_w
    mdi = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_w
    denom = (pdi + mdi).replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / denom
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def ma_consensus_score(close: pd.Series,
                       spans=(10, 20, 50, 100, 200)) -> pd.Series:
    """MA合議スコア(-1..+1)。終値がEMA/SMA各期間の上なら+1票・下なら-1票の
    平均（tech_ratingのMA部と同族のベクトル版）。"""
    votes = []
    for span in spans:
        ema = close.ewm(span=span, adjust=False).mean()
        sma = close.rolling(span).mean()
        votes.append(np.sign(close - ema))
        votes.append(np.sign(close - sma))
    return pd.concat(votes, axis=1).mean(axis=1)


def atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------- マスク生成
def _single_mask(df: pd.DataFrame, spec: dict):
    """単体フィルタの (allow_long, allow_short)。NaNはFalse(見送り)。"""
    kind = spec["種別"]
    if kind == "adx":
        a = adx(df, int(spec.get("期間", 14)))
        ok = (a >= float(spec.get("閾値", 20))).fillna(False).values
        return ok, ok.copy()
    if kind == "rsi":
        r = rsi(df["Close"], int(spec.get("期間", 14)))
        m = float(spec.get("マージン", 0))
        lg = (r > 50 + m).fillna(False).values
        sh = (r < 50 - m).fillna(False).values
        return lg, sh
    if kind == "macd":
        c = df["Close"]
        macd = (c.ewm(span=int(spec.get("短期", 12)), adjust=False).mean()
                - c.ewm(span=int(spec.get("長期", 26)), adjust=False).mean())
        sig_l = macd.ewm(span=int(spec.get("シグナル", 9)),
                         adjust=False).mean()
        diff = macd - sig_l
        lg = (diff > 0).fillna(False).values
        sh = (diff < 0).fillna(False).values
        return lg, sh
    if kind == "ma_consensus":
        s = ma_consensus_score(df["Close"])
        th = float(spec.get("閾値", 0.5))
        lg = (s >= th).fillna(False).values
        sh = (s <= -th).fillna(False).values
        return lg, sh
    if kind == "vol":
        a = atr_series(df)
        q = a.rolling(int(spec.get("参照本数", 200))).quantile(
            float(spec.get("分位", 0.3)))
        ok = (a >= q).fillna(False).values
        return ok, ok.copy()
    raise ValueError(f"未知の確認フィルタ種別: {kind}")


def entry_mask(df: pd.DataFrame, spec):
    """spec(単体dict or リスト=AND結合)から (allow_long, allow_short) を返す。"""
    specs = spec if isinstance(spec, list) else [spec]
    lg = np.ones(len(df), dtype=bool)
    sh = np.ones(len(df), dtype=bool)
    for s in specs:
        l1, s1 = _single_mask(df, s)
        lg &= l1
        sh &= s1
    return lg, sh


# ---------------------------------------------------------------- 探索グリッド
# 研究モジュール（research_intraday.py / research_15m.py）が共用する
# 確認フィルタの候補一覧。5系統＋代表的な組合せ。
DEFAULT_GRID = [
    [{"種別": "adx", "期間": 14, "閾値": 20}],
    [{"種別": "adx", "期間": 14, "閾値": 25}],
    [{"種別": "rsi", "期間": 14, "マージン": 0}],
    [{"種別": "rsi", "期間": 14, "マージン": 5}],
    [{"種別": "macd"}],
    [{"種別": "ma_consensus", "閾値": 0.5}],
    [{"種別": "vol", "参照本数": 200, "分位": 0.3}],
    [{"種別": "adx", "期間": 14, "閾値": 20},
     {"種別": "rsi", "期間": 14, "マージン": 0}],
    [{"種別": "macd"},
     {"種別": "vol", "参照本数": 200, "分位": 0.3}],
]


# ---------------------------------------------------------------- ラベル
def _single_label(spec: dict) -> str:
    kind = spec["種別"]
    if kind == "adx":
        return f"ADX{int(spec.get('期間', 14))}≥{spec.get('閾値', 20):g}"
    if kind == "rsi":
        m = float(spec.get("マージン", 0))
        return (f"RSI{int(spec.get('期間', 14))}順行"
                + (f"±{m:g}" if m else ""))
    if kind == "macd":
        return "MACD順行"
    if kind == "ma_consensus":
        return f"MA合議≥{float(spec.get('閾値', 0.5)):g}"
    if kind == "vol":
        return f"ATR分位≥{float(spec.get('分位', 0.3)) * 100:.0f}%"
    return kind


def describe_filter(spec) -> str:
    """指示書・ボード・研究ログ用の短いラベル（カンマ不使用=CSV安全）。"""
    if not spec:
        return "無"
    specs = spec if isinstance(spec, list) else [spec]
    return "＋".join(_single_label(s) for s in specs)
