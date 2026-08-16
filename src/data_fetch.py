# -*- coding: utf-8 -*-
"""ドル円 日足データ更新スクリプト
Yahoo Finance 公開チャートAPIから USD/JPY の日足OHLCを取得し data/usdjpy_daily.csv を上書きする。
使い方:  python3 src/data_fetch.py
"""
import os
import requests
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")


def fetch_usdjpy_daily(range_str: str = "15y") -> pd.DataFrame:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/USDJPY=X"
    params = {"range": range_str, "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Date": pd.to_datetime(res["timestamp"], unit="s")
                  .tz_localize("UTC").tz_convert("Asia/Tokyo").date,
        "Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"],
    })
    return df.dropna().reset_index(drop=True)


if __name__ == "__main__":
    df = fetch_usdjpy_daily()
    df.to_csv(CSV_PATH, index=False)
    print(f"saved {len(df)} rows: {df.Date.iloc[0]} -> {df.Date.iloc[-1]} -> {CSV_PATH}")
