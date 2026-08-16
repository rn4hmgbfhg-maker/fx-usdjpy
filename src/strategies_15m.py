# -*- coding: utf-8 -*-
"""15分足デイトレ戦略定義 — 第4のシステム「デイトレ15分」

世界のデイトレ実務で実績が広く報告されている手法系を15分足へ実装する:

  1. donchian_15m   : ドンチャンブレイク＋上位足(1H/2H)中心線フィルタ
                      （既存3システムと同族。タートルズ由来の順張り）
  2. orb            : オープニングレンジブレイクアウト（Toby Crabel系。
                      東京/ロンドンのセッション開始レンジのブレイクで順張り、
                      セッション終了で手仕舞い）
  3. london_asian   : ロンドン時間のアジアレンジブレイク（欧州勢参入で
                      東京レンジを抜ける定番。USDJPYで古典的に有効とされる）
  4. ema_rsi_pull   : EMAトレンド＋短期RSI押し目（Larry Connors系の
                      プルバック。トレンド方向へRSI(2)の行き過ぎで参入）
  5. squeeze_break  : ボリンジャーバンド収縮(スクイーズ)後のブレイク
                      （ボラティリティサイクルの拡張局面を取る）

シグナル関数は OHLC DataFrame（Date列=バー終了時刻JST・indexは連番、
別途 utc列=バー開始UTC時刻）を受け取り、各バー引け時点の目標ポジション
(+1/-1/0) の Series を返す（既存 strategies.py と同じ規約）。
執行・ATRストップ・数量計算は既存 Backtester がそのまま担う。

補助:
  tech_rating(df) : TradingView型のテクニカル合議スコア(-1..+1)。
                    移動平均群＋オシレーター群の多数決。表示・参考用。
"""
import numpy as np
import pandas as pd

from backtest import atr, rsi


# ------------------------------------------------------------ ユーティリティ
def to_tf(df15: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """15分足の標準DataFrame（Date/utc/OHLC）をN分足へリサンプルする。
    UTC固定グリッド。未完成の端バーは捨てる（確定バーのみで判定）。"""
    if minutes == 15:
        return df15
    ts = pd.to_datetime(df15["utc"], utc=True)
    tmp = df15[["Open", "High", "Low", "Close"]].copy()
    tmp.index = ts
    rule = f"{minutes}min"
    out = pd.DataFrame({
        "Open": tmp["Open"].resample(rule).first(),
        "High": tmp["High"].resample(rule).max(),
        "Low": tmp["Low"].resample(rule).min(),
        "Close": tmp["Close"].resample(rule).last(),
        "bars": tmp["Close"].resample(rule).count(),
    }).dropna()
    # 最終バーが構成15分足を満たしていなければ未確定として捨てる
    if len(out) and out["bars"].iloc[-1] < minutes // 15:
        last_end = out.index[-1] + pd.Timedelta(minutes=minutes)
        if last_end > ts.iloc[-1] + pd.Timedelta(minutes=15):
            out = out.iloc[:-1]
    out = out.drop(columns="bars").reset_index()
    out.rename(columns={"index": "Time"}, inplace=True)
    if "Time" not in out.columns:
        out.rename(columns={out.columns[0]: "Time"}, inplace=True)
    res = pd.DataFrame({
        "Date": (out["Time"] + pd.Timedelta(minutes=minutes))
                .dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M"),
        "utc": out["Time"],
        "Open": out["Open"], "High": out["High"],
        "Low": out["Low"], "Close": out["Close"],
    })
    return res.reset_index(drop=True)


def _utc_fields(df: pd.DataFrame):
    """utc列（バー開始UTC）から時・分・セッション日を取り出す。"""
    ts = pd.to_datetime(df["utc"], utc=True)
    return ts.dt.hour.values, ts.dt.minute.values, ts.dt.date.values


def higher_tf_mid(df15: pd.DataFrame, hours: int, n: int) -> pd.Series:
    """15分足から上位足ドンチャン中心線トレンド(+1/-1/0)を作り、
    各15分バー引け時点で確定済みの上位バーの値を割り当てる
    （ルックアヘッド防止のためshift(1)した確定値のみ使う）。"""
    ts = pd.to_datetime(df15["utc"], utc=True)
    tmp = df15[["High", "Low", "Close"]].copy()
    tmp.index = ts
    rule = f"{hours}h"
    agg = pd.DataFrame({
        "High": tmp["High"].resample(rule).max(),
        "Low": tmp["Low"].resample(rule).min(),
        "Close": tmp["Close"].resample(rule).last(),
    }).dropna()   # 週末等の空ビンを除去（残すとrollingがNaN汚染される）
    mid = (agg["High"].rolling(n).max() + agg["Low"].rolling(n).min()) / 2
    tr = pd.Series(0, index=agg.index)
    tr[agg["Close"] > mid] = 1
    tr[agg["Close"] < mid] = -1
    tr = tr.shift(1)  # 確定済み上位バーのみ（進行中バーは使わない）
    aligned = tr.reindex(ts, method="ffill").fillna(0).astype(int)
    return pd.Series(aligned.values, index=df15.index)


# ------------------------------------------------------- 1) ドンチャンMTF 15分
def donchian_15m(df: pd.DataFrame, n: int, exit_n: int,
                 trend: pd.Series = None, confirm=None) -> pd.Series:
    """15分足ドンチャンブレイク＋任意の上位足トレンドゲート＋任意の確認フィルタ。
    confirm=(allow_long, allow_short) は tech_filters.entry_mask() のbool配列。
    新規建て・ドテンの方向がFalseなら建てずに待機する
    （保有中の玉の手仕舞いには関与しない＝イグジット/ストップが正）。"""
    hi = df["High"].rolling(n).max().shift(1)
    lo = df["Low"].rolling(n).min().shift(1)
    exit_hi = df["High"].rolling(exit_n).max().shift(1)
    exit_lo = df["Low"].rolling(exit_n).min().shift(1)
    close = df["Close"].values
    tr = trend.values if trend is not None else np.zeros(len(df))
    use_f = trend is not None
    cf_l = confirm[0] if confirm is not None else None
    cf_s = confirm[1] if confirm is not None else None

    sig = np.zeros(len(df))
    pos = 0
    hi_v, lo_v = hi.values, lo.values
    ehi_v, elo_v = exit_hi.values, exit_lo.values
    for i in range(len(df)):
        if np.isnan(hi_v[i]) or np.isnan(lo_v[i]):
            continue

        def allowed(d):
            if use_f and tr[i] != d:
                return False
            if confirm is not None:
                return bool(cf_l[i]) if d > 0 else bool(cf_s[i])
            return True

        if pos == 0:
            if close[i] > hi_v[i] and allowed(1):
                pos = 1
            elif close[i] < lo_v[i] and allowed(-1):
                pos = -1
        elif pos == 1 and close[i] < elo_v[i]:
            pos = -1 if (close[i] < lo_v[i] and allowed(-1)) else 0
        elif pos == -1 and close[i] > ehi_v[i]:
            pos = 1 if (close[i] > hi_v[i] and allowed(1)) else 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


# ------------------------------------------- 2) オープニングレンジブレイクアウト
def orb(df: pd.DataFrame, session_start_utc: int, range_bars: int,
        window_hours: int = 6, flatten_utc: int = 21) -> pd.Series:
    """セッション開始からrange_bars本のレンジをブレイクしたら順張り。
    エントリーはレンジ確定後window_hours時間以内、flatten_utc時に手仕舞い。
    1セッション1回まで（ドテンなし）。"""
    hh, mm, dd = _utc_fields(df)
    close = df["Close"].values
    n = len(df)
    sig = np.zeros(n)
    pos = 0
    r_hi = r_lo = np.nan
    traded = False
    cur_day = None
    range_end_min = range_bars * 15
    for i in range(n):
        t_min = ((hh[i] - session_start_utc) % 24) * 60 + mm[i]
        # セッション日の切替（開始時刻ちょうどのバーで初期化）
        if t_min < 15 and dd[i] != cur_day:
            cur_day = dd[i]
            r_hi = r_lo = np.nan
            traded = False
        # 手仕舞い時刻（UTC）以降はノーポジ
        if hh[i] >= flatten_utc:
            pos = 0
            sig[i] = 0
            continue
        # レンジ形成中
        if t_min < range_end_min:
            h_v, l_v = df["High"].values[i], df["Low"].values[i]
            r_hi = h_v if np.isnan(r_hi) else max(r_hi, h_v)
            r_lo = l_v if np.isnan(r_lo) else min(r_lo, l_v)
            sig[i] = pos
            continue
        # ブレイク判定（1セッション1回・レンジ確定後window_hours時間以内）
        if (pos == 0 and not traded and not np.isnan(r_hi)
                and t_min < window_hours * 60 + range_end_min):
            if close[i] > r_hi:
                pos = 1
                traded = True
            elif close[i] < r_lo:
                pos = -1
                traded = True
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


# ------------------------------------------------- 3) ロンドンのアジアレンジ抜け
def london_asian(df: pd.DataFrame, asian_end_utc: int = 7,
                 trade_end_utc: int = 12,
                 flatten_utc: int = 20) -> pd.Series:
    """アジア時間(00:00〜asian_end UTC)のレンジを、ロンドン時間
    (asian_end〜trade_end UTC)に終値でブレイクしたら順張り。
    flatten_utc時に手仕舞い。1日1回まで。"""
    hh, mm, dd = _utc_fields(df)
    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values
    n = len(df)
    sig = np.zeros(n)
    pos = 0
    r_hi = r_lo = np.nan
    traded = False
    cur_day = None
    for i in range(n):
        if dd[i] != cur_day and hh[i] < asian_end_utc:
            cur_day = dd[i]
            r_hi = r_lo = np.nan
            traded = False
        if hh[i] >= flatten_utc:
            pos = 0
            sig[i] = 0
            continue
        if hh[i] < asian_end_utc:
            r_hi = high[i] if np.isnan(r_hi) else max(r_hi, high[i])
            r_lo = low[i] if np.isnan(r_lo) else min(r_lo, low[i])
            sig[i] = pos
            continue
        if (pos == 0 and not traded and not np.isnan(r_hi)
                and hh[i] < trade_end_utc):
            if close[i] > r_hi:
                pos = 1
                traded = True
            elif close[i] < r_lo:
                pos = -1
                traded = True
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


# ------------------------------------------------- 4) EMAトレンド＋RSI押し目
def ema_rsi_pull(df: pd.DataFrame, fast: int = 50, slow: int = 200,
                 rsi_n: int = 2, th: float = 10.0,
                 exit_mid: float = 50.0) -> pd.Series:
    """EMA fast>slow の上昇トレンド中、RSI(rsi_n)<th で買い、
    RSI>exit_midで手仕舞い。下降トレンドは対称。"""
    ema_f = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["Close"].ewm(span=slow, adjust=False).mean()
    r = rsi(df["Close"], rsi_n)
    up = (ema_f > ema_s).values
    dn = (ema_f < ema_s).values
    rv = r.values
    n = len(df)
    sig = np.zeros(n)
    pos = 0
    for i in range(n):
        if np.isnan(rv[i]):
            continue
        if pos == 0:
            if up[i] and rv[i] < th:
                pos = 1
            elif dn[i] and rv[i] > 100 - th:
                pos = -1
        elif pos == 1 and (rv[i] > exit_mid or dn[i]):
            pos = 0
        elif pos == -1 and (rv[i] < 100 - exit_mid or up[i]):
            pos = 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


# ------------------------------------------------- 5) スクイーズブレイク
def squeeze_break(df: pd.DataFrame, n: int = 20, k: float = 2.0,
                  width_pct: float = 0.2, lookback: int = 400,
                  exit_n: int = 12) -> pd.Series:
    """BB幅が過去lookbackのwidth_pct分位以下（スクイーズ）の状態から、
    終値がバンドを抜けたら順張り。反対側の短期ドンチャンで手仕舞い。"""
    ma = df["Close"].rolling(n).mean()
    sd = df["Close"].rolling(n).std()
    upper = ma + k * sd
    lower = ma - k * sd
    width = (upper - lower) / ma
    thresh = width.rolling(lookback).quantile(width_pct)
    squeezed = (width <= thresh).shift(1).fillna(False).astype(bool).values
    exit_hi = df["High"].rolling(exit_n).max().shift(1).values
    exit_lo = df["Low"].rolling(exit_n).min().shift(1).values
    close = df["Close"].values
    up_v, lo_v = upper.shift(1).values, lower.shift(1).values
    n_len = len(df)
    sig = np.zeros(n_len)
    pos = 0
    for i in range(n_len):
        if np.isnan(up_v[i]) or np.isnan(exit_hi[i]):
            continue
        if pos == 0 and squeezed[i]:
            if close[i] > up_v[i]:
                pos = 1
            elif close[i] < lo_v[i]:
                pos = -1
        elif pos == 1 and close[i] < exit_lo[i]:
            pos = 0
        elif pos == -1 and close[i] > exit_hi[i]:
            pos = 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


# ------------------------------------------------- 戦略レジストリ（研究・エンジン共用）
def build_signal(df: pd.DataFrame, spec: dict, confirm=None) -> pd.Series:
    """spec（config「デイトレ15分」のパラメータ辞書）からシグナル系列を作る。
    研究(research_15m.py)とエンジン(intraday15_engine.py)が同じ関数を使う
    ことで、探索と本番の判定を常に一致させる。
    confirm: 事前計算済みマスク(allow_long, allow_short)の注入口。
    研究の期間分割評価が「全期間で計算→位置スライス」したマスクを渡すために
    使う（分割フレーム上の再計算はウォームアップ歪みを生むため）。
    Noneならspec「確認フィルタ」からこの場で計算する（エンジン経路）。"""
    kind = spec["戦略ID"]
    if kind == "donchian":
        trend = None
        if spec.get("フィルタ時間足"):
            trend = higher_tf_mid(df, int(spec["フィルタ時間足"]),
                                  int(spec.get("フィルタ期間", 30)))
        if confirm is None and spec.get("確認フィルタ"):
            import tech_filters
            confirm = tech_filters.entry_mask(df, spec["確認フィルタ"])
        return donchian_15m(df, int(spec["エントリー期間"]),
                            int(spec["イグジット期間"]), trend, confirm)
    if kind == "orb":
        return orb(df, int(spec["セッション開始UTC"]),
                   int(spec["レンジ本数"]),
                   int(spec.get("窓時間", 6)), int(spec.get("手仕舞いUTC", 21)))
    if kind == "london_asian":
        return london_asian(df, int(spec.get("アジア終了UTC", 7)),
                            int(spec.get("参入終了UTC", 12)),
                            int(spec.get("手仕舞いUTC", 20)))
    if kind == "ema_rsi":
        return ema_rsi_pull(df, int(spec["EMA短期"]), int(spec["EMA長期"]),
                            int(spec.get("RSI期間", 2)),
                            float(spec.get("RSI閾値", 10)),
                            float(spec.get("RSI手仕舞い", 50)))
    if kind == "squeeze":
        return squeeze_break(df, int(spec.get("BB期間", 20)),
                             float(spec.get("BB係数", 2.0)),
                             float(spec.get("収縮分位", 0.2)),
                             int(spec.get("収縮参照本数", 400)),
                             int(spec.get("イグジット期間", 12)))
    raise ValueError(f"未知の戦略ID: {kind}")


TF_LABEL = {15: "15分", 60: "1時間", 240: "4時間"}


def describe(spec: dict) -> str:
    """指示書・ボード表示用の戦略ラベル。"""
    kind = spec["戦略ID"]
    tf = int(spec.get("時間足分", 15))
    prefix = TF_LABEL.get(tf, f"{tf}分") + "足" if tf != 15 else ""
    if kind == "donchian":
        f = (f"・{spec['フィルタ時間足']}Hフィルタ{spec.get('フィルタ期間', 30)}"
             if spec.get("フィルタ時間足") else "・フィルタ無")
        if spec.get("確認フィルタ"):
            import tech_filters
            f += f"・確認{tech_filters.describe_filter(spec['確認フィルタ'])}"
        return (f"{prefix or '15分'}ドンチャン{spec['エントリー期間']}/"
                f"{spec['イグジット期間']}{f}")
    if kind == "orb":
        s = "東京" if int(spec["セッション開始UTC"]) == 0 else "ロンドン"
        return (f"{s}オープニングレンジ{spec['レンジ本数']}本ブレイク"
                f"（{spec.get('手仕舞いUTC', 21)}時UTC手仕舞い）")
    if kind == "london_asian":
        return "ロンドンのアジアレンジブレイク"
    if kind == "ema_rsi":
        return (f"{prefix}EMA{spec['EMA短期']}/{spec['EMA長期']}トレンド"
                f"＋RSI({spec.get('RSI期間', 2)})押し目")
    if kind == "squeeze":
        return f"{prefix}BBスクイーズブレイク({spec.get('BB期間', 20)})"
    return kind


# ------------------------------------------------- テクニカル合議（表示・参考）
def tech_rating(df: pd.DataFrame) -> dict:
    """TradingView型の合議スコア。MA群とオシレーター群の多数決。
    返り値: {"スコア": -1..+1, "MA": ..., "オシレーター": ..., "判定": 文字列}"""
    c = df["Close"]
    votes_ma = []
    for span in (10, 20, 50, 100, 200):
        if len(c) >= span:
            ema_v = c.ewm(span=span, adjust=False).mean().iloc[-1]
            sma_v = c.rolling(span).mean().iloc[-1]
            votes_ma.append(1 if c.iloc[-1] > ema_v else -1)
            votes_ma.append(1 if c.iloc[-1] > sma_v else -1)
    votes_osc = []
    r14 = rsi(c, 14).iloc[-1]
    if r14 == r14:
        votes_osc.append(1 if r14 > 55 else -1 if r14 < 45 else 0)
    macd = (c.ewm(span=12, adjust=False).mean()
            - c.ewm(span=26, adjust=False).mean())
    sig_l = macd.ewm(span=9, adjust=False).mean()
    votes_osc.append(1 if macd.iloc[-1] > sig_l.iloc[-1] else -1)
    if len(c) >= 14:
        hh14 = df["High"].rolling(14).max().iloc[-1]
        ll14 = df["Low"].rolling(14).min().iloc[-1]
        if hh14 > ll14:
            stoch = (c.iloc[-1] - ll14) / (hh14 - ll14) * 100
            votes_osc.append(1 if stoch > 60 else -1 if stoch < 40 else 0)
    if len(c) >= 10:
        mom = c.iloc[-1] - c.iloc[-10]
        votes_osc.append(1 if mom > 0 else -1)
    ma_s = float(np.mean(votes_ma)) if votes_ma else 0.0
    osc_s = float(np.mean(votes_osc)) if votes_osc else 0.0
    score = round((ma_s + osc_s) / 2, 2)
    label = ("強い買い" if score >= 0.5 else "買い" if score >= 0.1 else
             "強い売り" if score <= -0.5 else "売り" if score <= -0.1 else
             "中立")
    return {"スコア": score, "MA": round(ma_s, 2),
            "オシレーター": round(osc_s, 2), "判定": label}
