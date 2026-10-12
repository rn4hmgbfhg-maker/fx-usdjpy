# -*- coding: utf-8 -*-
"""ユーロドル（EUR/USD）データ取得（2026-10-12 2通貨運用の開始に合わせて新設）

ドル円と同じ3系統をそろえる:
  日足   Yahoo（EURUSD=X・15年）            → data/eurusd_daily.csv
  1時間足 Yahoo（730日まで・差分マージ）      → data/eurusd_1h.csv
  15分足 Yahoo（60日まで・差分マージ）        → data/eurusd_15m.csv
         Dukascopy（BID 1分足→15分足・数年）  → data/eurusd_15m_hist.csv（研究用の長期履歴）

価格は小数5桁（1pip＝0.0001ドル）。損益はドル建てで出るので、円換算は
その時点のドル円（data/usdjpy_daily.csv の終値）を掛けて行う（eurusd_engine.py）。

使い方:
  python3 src/data_fetch_eurusd.py                       # 日足・1時間足・15分足を更新
  python3 src/data_fetch_eurusd.py --backfill 2023-08-01  # 15分足の長期履歴を作る
"""
import lzma
import os
import struct
import sys
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = lambda *p: os.path.join(BASE_DIR, "data", *p)  # noqa: E731
CSV_DAILY = D("eurusd_daily.csv")
CSV_1H = D("eurusd_1h.csv")
CSV_15M = D("eurusd_15m.csv")
CSV_15M_HIST = D("eurusd_15m_hist.csv")
SYMBOL = "EURUSD=X"
PIP = 0.0001

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://freeserv.dukascopy.com/"}
_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)


def _yahoo(interval, rng):
    last = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{SYMBOL}",
                             params={"range": rng, "interval": interval},
                             headers=_HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()["chart"]["result"][0]
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"Yahoo {SYMBOL} {interval} 取得失敗: {last}")


def fetch_daily(rng="15y"):
    res = _yahoo("1d", rng)
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Date": pd.to_datetime(res["timestamp"], unit="s")
                  .tz_localize("UTC").tz_convert("Asia/Tokyo").date,
        "Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]})
    return df.dropna().reset_index(drop=True)


def _intraday(interval, rng, minutes):
    res = _yahoo(interval, rng)
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"],
                       "Close": q["close"]},
                      index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    df.index.name = "Time"
    df = df.dropna()
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + pd.Timedelta(minutes=minutes) <= now]     # 未確定バーは捨てる
    return df[(df.index.minute % minutes == 0) | (minutes >= 60)]


def _merge_save(path, fresh):
    cached = None
    if os.path.exists(path):
        cached = pd.read_csv(path, index_col="Time", parse_dates=["Time"])
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
    merged = fresh if cached is None else pd.concat([cached, fresh])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged.to_csv(path)
    return merged


def update_all(full=False):
    """日足は毎回15年を取り直す（軽い）。1時間足・15分足は差分マージ。"""
    out = {}
    d = fetch_daily()
    d.to_csv(CSV_DAILY, index=False)
    out["日足"] = len(d)
    out["1時間足"] = len(_merge_save(CSV_1H, _intraday("60m", "730d" if full or not os.path.exists(CSV_1H) else "5d", 60)))
    out["15分足"] = len(_merge_save(CSV_15M, _intraday("15m", "60d" if full or not os.path.exists(CSV_15M) else "5d", 15)))
    return out


def load_daily():
    df = pd.read_csv(CSV_DAILY)
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    return df


def load_intraday(kind="1h"):
    """kind='1h' or '15m'。15分足は長期履歴と結合して返す（UTC index）。"""
    paths = [CSV_1H] if kind == "1h" else [CSV_15M_HIST, CSV_15M]
    frames = []
    for p in paths:
        if os.path.exists(p):
            x = pd.read_csv(p, index_col="Time", parse_dates=["Time"])
            if x.index.tz is None:
                x.index = x.index.tz_localize("UTC")
            frames.append(x[["Open", "High", "Low", "Close"]].astype(float))
    if not frames:
        raise RuntimeError(f"ユーロドル {kind} データがありません")
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


# ------------------------------------------------------- Dukascopy 長期履歴
def _duka_day(d, retries=3):
    url = (f"https://datafeed.dukascopy.com/datafeed/EURUSD/"
           f"{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, timeout=20)
        except Exception:  # noqa: BLE001
            _time.sleep(2 + attempt * 3)
            continue
        if r.status_code == 404 or (r.status_code == 200 and not r.content):
            return None
        if r.status_code != 200:
            _time.sleep(2 + attempt * 3)
            continue
        try:
            raw = lzma.decompress(r.content)
        except Exception:  # noqa: BLE001
            return None
        n = len(raw) // 24
        if not n:
            return None
        rows = [struct.unpack(">5if", raw[i * 24:(i + 1) * 24]) for i in range(n)]
        df = pd.DataFrame(rows, columns=["sec", "o", "c", "l", "h", "v"])
        df.index = pd.Timestamp(d, tz="UTC") + pd.to_timedelta(df["sec"], unit="s")
        # 価格はポイント値（非円ペアは1/100000）
        m1 = pd.DataFrame({"Open": df["o"] / 1e5, "High": df["h"] / 1e5,
                           "Low": df["l"] / 1e5, "Close": df["c"] / 1e5, "V": df["v"]})
        m1 = m1[m1["V"] > 0]
        if m1.empty:
            return None
        out = pd.DataFrame({"Open": m1["Open"].resample("15min").first(),
                            "High": m1["High"].resample("15min").max(),
                            "Low": m1["Low"].resample("15min").min(),
                            "Close": m1["Close"].resample("15min").last()}).dropna()
        out.index.name = "Time"
        return out
    return None


def backfill(start, end=None, sleep_sec=0.1):
    end = end or (date.today() - timedelta(days=1))
    frames = []
    if os.path.exists(CSV_15M_HIST):
        ex = pd.read_csv(CSV_15M_HIST, index_col="Time", parse_dates=["Time"])
        if ex.index.tz is None:
            ex.index = ex.index.tz_localize("UTC")
        if len(ex):
            frames.append(ex)
            start = max(start, ex.index.max().date() + timedelta(days=1))
    d, got = start, 0
    while d <= end:
        if d.weekday() != 5:
            x = _duka_day(d)
            if x is not None and len(x):
                frames.append(x)
                got += 1
                if got % 40 == 0:
                    m = pd.concat(frames)
                    m[~m.index.duplicated(keep="last")].sort_index().to_csv(CSV_15M_HIST)
                    print(f"  {d} まで {got}日", flush=True)
            _time.sleep(sleep_sec)
        d += timedelta(days=1)
    m = pd.concat(frames)
    m = m[~m.index.duplicated(keep="last")].sort_index()
    m.to_csv(CSV_15M_HIST)
    print(f"バックフィル完了: 取得{got}日 計{len(m)}本 {m.index[0]}〜{m.index[-1]}")
    return m


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        backfill(datetime.strptime(sys.argv[sys.argv.index("--backfill") + 1], "%Y-%m-%d").date())
    else:
        print(update_all(full="--full" in sys.argv))
