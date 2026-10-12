# -*- coding: utf-8 -*-
"""ドル円 売買戦略定義

各戦略は OHLC DataFrame を受け取り、各バー引け時点の目標ポジション
(+1ロング / -1ショート / 0ノーポジ) の Series を返す。
執行(翌バー寄付)・ストップ・数量計算はバックテストエンジン側が担う。
"""
import numpy as np
import pandas as pd

from backtest import sma, rsi


def ma_cross(df: pd.DataFrame, fast: int = 20, slow: int = 60) -> pd.Series:
    """トレンドフォロー: 短期SMAが長期SMAの上ならロング、下ならショート。"""
    f, s = sma(df["Close"], fast), sma(df["Close"], slow)
    sig = pd.Series(0, index=df.index)
    sig[f > s] = 1
    sig[f < s] = -1
    return sig


def donchian(df: pd.DataFrame, n: int = 20, exit_n: int = 10) -> pd.Series:
    """ドンチャンブレイクアウト: n日高値更新でロング、n日安値更新でショート。
    exit_n日逆側チャネルタッチで手仕舞い。"""
    hi = df["High"].rolling(n).max().shift(1)
    lo = df["Low"].rolling(n).min().shift(1)
    exit_hi = df["High"].rolling(exit_n).max().shift(1)
    exit_lo = df["Low"].rolling(exit_n).min().shift(1)
    sig = np.zeros(len(df))
    pos = 0
    close = df["Close"].values
    for i in range(len(df)):
        if np.isnan(hi.iloc[i]) or np.isnan(lo.iloc[i]):
            sig[i] = 0
            continue
        if pos == 0:
            if close[i] > hi.iloc[i]:
                pos = 1
            elif close[i] < lo.iloc[i]:
                pos = -1
        elif pos == 1 and close[i] < exit_lo.iloc[i]:
            pos = 0
            if close[i] < lo.iloc[i]:
                pos = -1
        elif pos == -1 and close[i] > exit_hi.iloc[i]:
            pos = 0
            if close[i] > hi.iloc[i]:
                pos = 1
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


def donchian_exit_levels(df: pd.DataFrame, exit_n: int, pos: int):
    """保有中ポジションの手仕舞い水準を (今回の引け用, 次回以降用) で返す。

    donchian() の手仕舞い条件そのもの。ショートは終値 > exit_n日高値、
    ロングは終値 < exit_n日安値 で決済になる。donchian() は shift(1) 済みの
    系列を当日終値と比べるので、判定に効く水準は2つある。

      今回用 = 前日までの exit_n 日レンジ。いま進行中のバーの引けで実際に
               判定される水準。日中に見るべきはこちら。
      次回用 = 当日バーを含む exit_n 日レンジ。次の引けで効く水準だが、
               当日バーが未確定のうちは暫定値（高値更新で切り上がる）。

    ★2026-08-14 修正: 当初は次回用だけを返しており、ボードが「今日にも決済
    されそう」に見える表示になっていた。長期トレンド(exit_n=10)では介入前の
    高値160.836円がまだ窓に残っていて今回用は160.836円なのに、次回用の
    159.528円だけを出していたため 1.3円ものズレがあった（ユーザー指摘）。
    窓落ちで水準が急に降りてくる局面を可視化するため2段で返す。

    ATRトレール逆指値とは別系統の出口で、トレンドが伸びるほどこちらが手前に
    降りてくる。トレンドフォローの実質的な出口はこの線であり、逆指値だけを
    見ていると「まだ161円まで持てる」と誤読するため表示側で必ず併記する
    （2026-08-14 ユーザー指示）。★利益が出ている保証はなく、含み損のまま
    トレンド転換すれば損切りとして働く線なので「利確」とは呼ばない。
    ノーポジなら (None, None)。
    """
    if not pos:
        return None, None
    n = int(exit_n)
    if len(df) < n + 1:
        return None, None
    ser = (df["High"].rolling(n).max() if pos < 0
           else df["Low"].rolling(n).min())
    now, nxt = ser.iloc[-2], ser.iloc[-1]
    return (None if pd.isna(now) else round(float(now), 3),
            None if pd.isna(nxt) else round(float(nxt), 3))


def donchian_exit_level(df: pd.DataFrame, exit_n: int, pos: int):
    """互換用。donchian_exit_levels() の「次回以降用」だけを返す。"""
    return donchian_exit_levels(df, exit_n, pos)[1]


def donchian_exit_proximity(df: pd.DataFrame, n: int, exit_n: int, pos: int,
                            atr_series: pd.Series):
    """利確ライン(donchian_exit_level)が、過去の同方向保有時と比べて

    「終値からATR何倍離れているか」「それが過去分布の下位何%か」を返す。

    2026-08-14: 利確ラインを常時表示した初日、値が終値のわずか0.07ATR先
    （過去の同方向保有時の下位1%＝ほぼ最も近い部類）という結果になり、
    ユーザーから「近すぎるのでは」と指摘を受けた。値自体はロジック通り
    正しいが、近さが平常運転かどうかは表示しないと判断できないため追加。
    戻り値: (dist_atr, percentile) いずれも pos==0 なら (None, None)。
    percentile は「今日の近さ以下だった過去の割合」＝小さいほど異例に近い。

    ★2026-08-14 修正: 当初 exit_hi/exit_lo を shift(1) せずに当日バーを含めて
    いたため、当日高値がそのまま水準になり「距離＝当日の高値と終値の差」に
    なっていた。まだレンジの狭い朝方は必ず極小になり、長期・短期のどちらも
    一律「0.07ATR・下位0%＝かなり接近中」と誤警告していた（確定バーで測り
    直すと長期トレンドは1.17ATR・下位31%＝平常運転）。donchian() 本体と同じ
    shift(1) を当てて、実際の判定式・過去分布と条件を揃える。
    """
    if not pos:
        return None, None
    sig_hist = donchian(df, n=n, exit_n=exit_n)
    # donchian() の判定と同じ「前日までのレンジ」対「当日終値」で測る。
    exit_hi = df["High"].rolling(exit_n).max().shift(1)
    exit_lo = df["Low"].rolling(exit_n).min().shift(1)
    dist_short = (exit_hi - df["Close"]) / atr_series
    dist_long = (df["Close"] - exit_lo) / atr_series
    dist = dist_short.where(sig_hist < 0, dist_long.where(sig_hist > 0)).dropna()
    if dist.empty:
        return None, None
    today = float(dist.iloc[-1])
    pct = float((dist <= today).mean())
    return today, pct


def rsi_reversion(df: pd.DataFrame, n: int = 2, buy_th: int = 10,
                  sell_th: int = 90, trend_n: int = 200) -> pd.Series:
    """逆張り: 200日SMAより上でRSI(2)が買われすぎ/売られすぎ水準に達したら
    トレンド方向へ押し目買い(上昇トレンド中の急落を買う)。RSIが50を跨いだら手仕舞い。"""
    r = rsi(df["Close"], n)
    trend = df["Close"] > sma(df["Close"], trend_n)
    sig = np.zeros(len(df))
    pos = 0
    for i in range(len(df)):
        if np.isnan(r.iloc[i]):
            sig[i] = 0
            continue
        if pos == 0:
            if trend.iloc[i] and r.iloc[i] < buy_th:
                pos = 1          # 上昇トレンド中の急落 → 押し目買い
            elif (not trend.iloc[i]) and r.iloc[i] > sell_th:
                pos = -1         # 下降トレンド中の急騰 → 戻り売り
        elif pos == 1 and r.iloc[i] > 50:
            pos = 0
        elif pos == -1 and r.iloc[i] < 50:
            pos = 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


def bb_reversion(df: pd.DataFrame, n: int = 10, z: float = 1.5,
                 trend_n: int = 200) -> pd.Series:
    """押し目買い・戻り売り（ボリンジャー逆張り＋長期線フィルタ）。2026-10-12 ユーロドル用に追加。
    終値が trend_n 日線より上で n日ボリンジャーの -zσ を割ったら買い、下で +zσ を超えたら売り。
    n日移動平均へ戻ったら手仕舞い（加えてバックテスト・エンジン側の ATR トレイルが効く）。
    ユーロドルは2019年以降トレンド追随（ドンチャン）が効かず、この型が前後半とも安定した。"""
    c = df["Close"]
    ma = c.rolling(n).mean()
    sd = c.rolling(n).std()
    up, lo = (ma + z * sd).values, (ma - z * sd).values
    tr = (c > sma(c, trend_n)).values
    tr_ok = sma(c, trend_n).notna().values
    cv, mv = c.values, ma.values
    sig = np.zeros(len(df))
    pos = 0
    for i in range(len(df)):
        if np.isnan(mv[i]) or not tr_ok[i]:
            sig[i] = 0
            continue
        if pos == 0:
            if cv[i] < lo[i] and tr[i]:
                pos = 1
            elif cv[i] > up[i] and not tr[i]:
                pos = -1
        elif (pos == 1 and cv[i] >= mv[i]) or (pos == -1 and cv[i] <= mv[i]):
            pos = 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


def bb_reversion_exit_level(df: pd.DataFrame, n: int = 10):
    """押し目・戻り系の手仕舞い線（n日移動平均）。ボード・指示書の決済ライン表示用。"""
    return float(df["Close"].rolling(n).mean().iloc[-1])


def make_signal(df: pd.DataFrame, scfg: dict) -> pd.Series:
    """config のシステム定義から目標ポジション系列を作る共通関数（2通貨共通）。"""
    kind = scfg.get("戦略", "ドンチャン")
    if kind == "ドンチャン":
        return donchian(df, n=int(scfg["エントリー期間"]), exit_n=int(scfg["イグジット期間"]))
    if kind == "BB逆張り":
        return bb_reversion(df, n=int(scfg["BB期間"]), z=float(scfg["BB係数"]),
                            trend_n=int(scfg.get("トレンド期間", 200)))
    if kind == "MAクロス":
        return ma_cross(df, fast=int(scfg["短期MA"]), slow=int(scfg["長期MA"]))
    raise ValueError(f"未知の戦略: {kind}")


STRATEGIES = {
    "MAクロス(20/60)": ma_cross,
    "ドンチャン(20/10)": donchian,
    "RSI押し目(2/200SMA)": rsi_reversion,
}
