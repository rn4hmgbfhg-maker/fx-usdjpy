# -*- coding: utf-8 -*-
"""デイトレ複合時間軸（4H/2H/1H）戦略定義

役割: 1日1回程度の売買を目標に、時間足のドンチャンブレイクを
4時間足の環境認識（トレンドフィルタ）で選別する第3のシステム。

構成:
  ・執行時間足(1H/2H/4H)のドンチャン n/exit_n ブレイク（既存2システムと同族）
  ・4時間足ドンチャン中心線フィルタ: 4H終値が中心線より上なら買いのみ、
    下なら売りのみ新規建てを許可（保有中の玉はフィルタ反転でも維持し、
    手仕舞いは執行足のイグジット/ATRストップに任せる）
  ・ストップ/数量計算はバックテストエンジン側（既存Backtesterと同一執行モデル:
    引けシグナル→翌バー寄付執行・ATRトレイル・片道0.4銭）

シグナル関数は OHLC DataFrame を受け取り、各バー引け時点の目標ポジション
(+1/-1/0) の Series を返す（既存 strategies.py と同じ規約）。
"""
import numpy as np
import pandas as pd


def trend_4h(df_4h: pd.DataFrame, n: int = 30) -> pd.Series:
    """4時間足の環境認識: 終値がドンチャン中心線の上=+1 / 下=-1。
    返り値indexは df_4h と同じ（Date=バー終了時刻）。"""
    mid = (df_4h["High"].rolling(n).max() + df_4h["Low"].rolling(n).min()) / 2
    t = pd.Series(0, index=df_4h.index)
    t[df_4h["Close"] > mid] = 1
    t[df_4h["Close"] < mid] = -1
    return t


def align_trend(df_exec: pd.DataFrame, df_4h: pd.DataFrame,
                n: int = 30) -> pd.Series:
    """4Hトレンドを執行足の各バーへ「そのバー引け時点で確定済みの4Hバー」
    基準で割り当てる（ルックアヘッド防止: 4HのDate=バー終了時刻 <= 執行足の
    Date=バー終了時刻 のうち最新のものを使う）。"""
    t4 = trend_4h(df_4h, n)
    left = pd.DataFrame({"Date": pd.to_datetime(df_exec["Date"])})
    right = pd.DataFrame({"Date": pd.to_datetime(df_4h["Date"]),
                          "trend": t4.values})
    merged = pd.merge_asof(left, right, on="Date", direction="backward")
    return merged["trend"].fillna(0).astype(int)


def donchian_mtf(df_exec: pd.DataFrame, n: int, exit_n: int,
                 trend: pd.Series = None, confirm=None) -> pd.Series:
    """ドンチャンブレイク+任意の4Hトレンドゲート+任意の確認フィルタ。
    エントリーはトレンド方向と一致する時のみ許可。ドテンも同様にゲートし、
    不一致ならノーポジで待機する。trend=None でフィルタ無し。
    confirm=(allow_long, allow_short) は tech_filters.entry_mask() の
    bool配列。新規建て・ドテンの方向がFalseなら建てずに待機する
    （保有中の玉の手仕舞いには関与しない＝イグジット/ストップが正）。"""
    hi = df_exec["High"].rolling(n).max().shift(1)
    lo = df_exec["Low"].rolling(n).min().shift(1)
    exit_hi = df_exec["High"].rolling(exit_n).max().shift(1)
    exit_lo = df_exec["Low"].rolling(exit_n).min().shift(1)
    close = df_exec["Close"].values
    tr = trend.values if trend is not None else np.zeros(len(df_exec))
    use_f = trend is not None
    cf_l = confirm[0] if confirm is not None else None
    cf_s = confirm[1] if confirm is not None else None

    sig = np.zeros(len(df_exec))
    pos = 0
    for i in range(len(df_exec)):
        if np.isnan(hi.iloc[i]) or np.isnan(lo.iloc[i]):
            sig[i] = 0
            continue

        def allowed(d):
            if use_f and tr[i] != d:
                return False
            if confirm is not None:
                return bool(cf_l[i]) if d > 0 else bool(cf_s[i])
            return True

        if pos == 0:
            if close[i] > hi.iloc[i] and allowed(1):
                pos = 1
            elif close[i] < lo.iloc[i] and allowed(-1):
                pos = -1
        elif pos == 1 and close[i] < exit_lo.iloc[i]:
            pos = -1 if (close[i] < lo.iloc[i] and allowed(-1)) else 0
        elif pos == -1 and close[i] > exit_hi.iloc[i]:
            pos = 1 if (close[i] > hi.iloc[i] and allowed(1)) else 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df_exec.index)
