# -*- coding: utf-8 -*-
"""ドル円 時間足データ取得（デイトレ系用）

Yahoo Finance 公開チャートAPIから USD/JPY の1時間足を取得する。
APIの1時間足は直近730日までしか遡れないため、data/usdjpy_1h.csv に
マージ保存して履歴を育てる（重複はタイムスタンプで排除・未確定の
最新バーは保存しない）。2時間足・4時間足は1時間足からリサンプルで作る
（UTC基準の固定グリッド。JSTでは 2H=奇数時起点/4H=1・5・9…時起点になるが、
判定の一貫性が目的なのでグリッドが固定であることが重要）。

使い方:  python3 src/data_fetch_intraday.py   # 取得+マージ保存して概要表示
"""
import os

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_1H = os.path.join(BASE_DIR, "data", "usdjpy_1h.csv")


def fetch_1h(range_str: str = "730d") -> pd.DataFrame:
    """1時間足OHLCを取得する。indexはバー開始時刻(UTC, tz-aware)。"""
    url = "https://query1.finance.yahoo.com/v8/finance/chart/USDJPY=X"
    params = {"range": range_str, "interval": "1h"}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Open": q["open"], "High": q["high"], "Low": q["low"],
        "Close": q["close"],
    }, index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    df.index.name = "Time"
    df = df.dropna()
    # 最終バーは進行中(未確定)の可能性が高いので、開始から1時間経過して
    # いないバーは捨てる(確定バーのみで判定する方針)
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + pd.Timedelta(hours=1) <= now]
    return df


def load_merged(fetch: bool = True) -> pd.DataFrame:
    """キャッシュと最新取得をマージした1時間足を返す(取得失敗時はキャッシュ)。"""
    cached = None
    if os.path.exists(CSV_1H):
        cached = pd.read_csv(CSV_1H, index_col="Time", parse_dates=["Time"])
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
    if fetch:
        try:
            fresh = fetch_1h()
        except Exception:
            fresh = None
        if fresh is not None and len(fresh):
            merged = fresh if cached is None else pd.concat([cached, fresh])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            os.makedirs(os.path.dirname(CSV_1H), exist_ok=True)
            merged.to_csv(CSV_1H)
            return merged
    if cached is None:
        raise RuntimeError("1時間足データが取得できずキャッシュもありません")
    return cached


def resample(df1h: pd.DataFrame, hours: int) -> pd.DataFrame:
    """1時間足→N時間足(UTC固定グリッド)。未完成の端バーは捨てる。"""
    if hours == 1:
        out = df1h.copy()
    else:
        rule = f"{hours}h"
        out = pd.DataFrame({
            "Open": df1h["Open"].resample(rule).first(),
            "High": df1h["High"].resample(rule).max(),
            "Low": df1h["Low"].resample(rule).min(),
            "Close": df1h["Close"].resample(rule).last(),
            "bars": df1h["Close"].resample(rule).count(),
        }).dropna()
        # 端の未完成バー(構成1時間足が欠ける)を除外。ただし週末をまたぐ
        # バーは元々本数が少ないため、最終バーのみ厳密に判定する
        if len(out) and out["bars"].iloc[-1] < hours:
            last_end = out.index[-1] + pd.Timedelta(hours=hours)
            if last_end > df1h.index[-1] + pd.Timedelta(hours=1):
                out = out.iloc[:-1]
        out = out.drop(columns="bars")
    # Backtester互換: Date列=バー終了時刻(JST表記)を持つ通常DataFrameへ
    res = out.reset_index()
    res["Date"] = (res["Time"] + pd.Timedelta(hours=hours)).dt.tz_convert(
        "Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M")
    return res[["Date", "Open", "High", "Low", "Close"]].reset_index(drop=True)


if __name__ == "__main__":
    df = load_merged()
    print(f"1時間足: {len(df)}本  {df.index[0]} -> {df.index[-1]}")
    for h in (2, 4):
        r = resample(df, h)
        print(f"{h}時間足: {len(r)}本  {r['Date'].iloc[0]} -> {r['Date'].iloc[-1]}")
