# -*- coding: utf-8 -*-
"""短期スイング戦略群（保有1〜4日目安）

長期トレンドシステム(ドンチャン)と併走する2本目のシグナル系統。
各関数は日足OHLCを受け取り、目標ポジション(+1/-1/0)のSeriesを返す。
時間切れ決済(max_hold)は戦略側でシグナルを0に落とすことで表現する。
ストップはエンジン側のATRトレイリング(係数は短期用に小さく設定)が担う。
"""
import numpy as np
import pandas as pd

from backtest import sma, rsi


def rsi_dip(df, rsi_n=2, buy_th=25, exit_th=65, max_hold=3, trend_n=200):
    """トレンド方向への短期押し目/戻り: 上昇トレンド中にRSI(短期)が売られすぎ→買い。
    RSIが回復するか max_hold 日経過で手仕舞い。下降トレンドは対称のショート。"""
    r = rsi(df["Close"], rsi_n)
    up = df["Close"] > sma(df["Close"], trend_n)
    sig = np.zeros(len(df)); pos = 0; held = 0
    for i in range(len(df)):
        if np.isnan(r.iloc[i]):
            continue
        if pos == 0:
            if up.iloc[i] and r.iloc[i] < buy_th:
                pos, held = 1, 0
            elif (not up.iloc[i]) and r.iloc[i] > 100 - buy_th:
                pos, held = -1, 0
        else:
            held += 1
            if pos == 1 and (r.iloc[i] > exit_th or held >= max_hold):
                pos = 0
            elif pos == -1 and (r.iloc[i] < 100 - exit_th or held >= max_hold):
                pos = 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


def bar_dip(df, max_hold=2, trend_n=200):
    """陰線押し目: 上昇トレンド中の陰線(終値<始値)の翌日に買い、
    前日高値を上抜くか max_hold 日で手仕舞い。下降トレンドは対称。"""
    up = df["Close"] > sma(df["Close"], trend_n)
    dn_bar = df["Close"] < df["Open"]
    up_bar = df["Close"] > df["Open"]
    sig = np.zeros(len(df)); pos = 0; held = 0
    for i in range(1, len(df)):
        if np.isnan(up.iloc[i]):
            continue
        if pos == 0:
            if up.iloc[i] and dn_bar.iloc[i]:
                pos, held = 1, 0
            elif (not up.iloc[i]) and up_bar.iloc[i]:
                pos, held = -1, 0
        else:
            held += 1
            recovered = (pos == 1 and df["Close"].iloc[i] > df["High"].iloc[i - 1]) or \
                        (pos == -1 and df["Close"].iloc[i] < df["Low"].iloc[i - 1])
            if recovered or held >= max_hold:
                pos = 0
                # 手仕舞い当日に再度押し目条件が成立していれば継続的に再エントリー
                if up.iloc[i] and dn_bar.iloc[i]:
                    pos, held = 1, 0
                elif (not up.iloc[i]) and up_bar.iloc[i]:
                    pos, held = -1, 0
        sig[i] = pos
    return pd.Series(sig.astype(int), index=df.index)


def momentum(df, n=3):
    """短期モメンタム追随: 直近n日リターンの符号に毎日従う(ほぼ常時建玉、数日で反転)。"""
    ret = df["Close"].diff(n)
    sig = np.sign(ret).fillna(0)
    return sig.astype(int)


def donchian_short(df, n=8, exit_n=4):
    """短期ドンチャン(strategies.donchianの短期版ラッパ)。"""
    from strategies import donchian
    return donchian(df, n=n, exit_n=exit_n)


SHORT_STRATEGIES = {
    "RSI押し目": rsi_dip,
    "陰線押し目": bar_dip,
    "モメンタム": momentum,
    "短期ドンチャン": donchian_short,
}
