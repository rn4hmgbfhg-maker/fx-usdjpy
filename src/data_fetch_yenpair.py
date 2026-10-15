# -*- coding: utf-8 -*-
"""円の通貨ペア（クロス円）の共通データ取得（2026-10-15 ユーロ円・ポンド円で共用）

ペアごとに同じ3系統をそろえる（data_fetch_eurjpy.py から共通化）:
  日足   Yahoo（<CODE>=X・15年）            → data/<pair>_daily.csv
  1時間足 Yahoo（730日まで・差分マージ）      → data/<pair>_1h.csv
  15分足 Yahoo（60日まで・差分マージ）        → data/<pair>_15m.csv
         Dukascopy（BID 1分足→15分足・数年）  → data/<pair>_15m_hist.csv（研究用の長期履歴）
価格は小数3桁（1pip＝0.01円）。損益は円建て。

使い方（各ペアの薄い入口から呼ぶ）: data_fetch_eurjpy.py / data_fetch_gbpjpy.py
"""
import lzma
import os
import struct
import sys
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_fetch_eurusd as _eu  # noqa: E402  取得の部品（Yahoo・保存）を共用

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIP = 0.01
NAMES = {"eurjpy": "ユーロ円", "gbpjpy": "ポンド円"}


class Pair:
    def __init__(self, pair):
        self.pair = pair
        self.name = NAMES.get(pair, pair)
        self.SYMBOL = pair.upper() + "=X"
        self.DUKA = pair.upper()
        d = lambda *p: os.path.join(BASE_DIR, "data", *p)  # noqa: E731
        self.CSV_DAILY = d(f"{pair}_daily.csv")
        self.CSV_1H = d(f"{pair}_1h.csv")
        self.CSV_15M = d(f"{pair}_15m.csv")
        self.CSV_15M_HIST = d(f"{pair}_15m_hist.csv")
        self.PIP = PIP

    def _yahoo(self, interval, rng):
        last = None
        for host in ("query1", "query2"):
            try:
                r = _eu.requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{self.SYMBOL}",
                                     params={"range": rng, "interval": interval},
                                     headers=_eu._HEADERS, timeout=30)
                r.raise_for_status()
                return r.json()["chart"]["result"][0]
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"Yahoo {self.SYMBOL} {interval} 取得失敗: {last}")

    def fetch_daily(self, rng="15y"):
        res = self._yahoo("1d", rng)
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({
            "Date": pd.to_datetime(res["timestamp"], unit="s")
                      .tz_localize("UTC").tz_convert("Asia/Tokyo").date,
            "Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]})
        return df.dropna().reset_index(drop=True)

    def _intraday(self, interval, rng, minutes):
        res = self._yahoo(interval, rng)
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"],
                           "Close": q["close"]},
                          index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
        df.index.name = "Time"
        df = df.dropna()
        now = pd.Timestamp.now(tz="UTC")
        df = df[df.index + pd.Timedelta(minutes=minutes) <= now]     # 未確定バーは捨てる
        return df[(df.index.minute % minutes == 0) | (minutes >= 60)]

    def update_all(self, full=False):
        """日足は毎回15年を取り直す（軽い）。1時間足・15分足は差分マージ。"""
        out = {}
        d = self.fetch_daily()
        d.to_csv(self.CSV_DAILY, index=False)
        out["日足"] = len(d)
        out["1時間足"] = len(_eu._merge_save(self.CSV_1H, self._intraday(
            "60m", "730d" if full or not os.path.exists(self.CSV_1H) else "5d", 60)))
        out["15分足"] = len(_eu._merge_save(self.CSV_15M, self._intraday(
            "15m", "60d" if full or not os.path.exists(self.CSV_15M) else "5d", 15)))
        return out

    def load_daily(self):
        df = pd.read_csv(self.CSV_DAILY)
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        return df

    def load_intraday(self, kind="1h"):
        """kind='1h' or '15m'。15分足は長期履歴と結合して返す（UTC index）。"""
        paths = [self.CSV_1H] if kind == "1h" else [self.CSV_15M_HIST, self.CSV_15M]
        frames = []
        for p in paths:
            if os.path.exists(p):
                x = pd.read_csv(p, index_col="Time", parse_dates=["Time"])
                if x.index.tz is None:
                    x.index = x.index.tz_localize("UTC")
                frames.append(x[["Open", "High", "Low", "Close"]].astype(float))
        if not frames:
            raise RuntimeError(f"{self.name} {kind} データがありません")
        df = pd.concat(frames)
        return df[~df.index.duplicated(keep="last")].sort_index()

    # --------------------------------------------------- Dukascopy 長期履歴
    def _duka_day(self, d, retries=3):
        url = (f"https://datafeed.dukascopy.com/datafeed/{self.DUKA}/"
               f"{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
        for attempt in range(retries):
            try:
                r = _eu._SESSION.get(url, timeout=20)
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
            # 価格はポイント値（円ペアは1/1000）
            m1 = pd.DataFrame({"Open": df["o"] / 1e3, "High": df["h"] / 1e3,
                               "Low": df["l"] / 1e3, "Close": df["c"] / 1e3, "V": df["v"]})
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

    def backfill(self, start, end=None, sleep_sec=0.1):
        end = end or (date.today() - timedelta(days=1))
        frames = []
        if os.path.exists(self.CSV_15M_HIST):
            ex = pd.read_csv(self.CSV_15M_HIST, index_col="Time", parse_dates=["Time"])
            if ex.index.tz is None:
                ex.index = ex.index.tz_localize("UTC")
            if len(ex):
                frames.append(ex)
                start = max(start, ex.index.max().date() + timedelta(days=1))
        d, got = start, 0
        while d <= end:
            if d.weekday() != 5:
                x = self._duka_day(d)
                if x is not None and len(x):
                    frames.append(x)
                    got += 1
                    if got % 40 == 0:
                        m = pd.concat(frames)
                        m[~m.index.duplicated(keep="last")].sort_index().to_csv(self.CSV_15M_HIST)
                        print(f"  {d} まで {got}日", flush=True)
                _time.sleep(sleep_sec)
            d += timedelta(days=1)
        if not frames:
            print("バックフィル: 新しい日なし")
            return None
        m = pd.concat(frames)
        m = m[~m.index.duplicated(keep="last")].sort_index()
        m.to_csv(self.CSV_15M_HIST)
        print(f"バックフィル完了: 取得{got}日 計{len(m)}本 {m.index[0]}〜{m.index[-1]}")
        return m


def main(pair):
    p = Pair(pair)
    if "--backfill" in sys.argv:
        p.backfill(datetime.strptime(sys.argv[sys.argv.index("--backfill") + 1], "%Y-%m-%d").date())
    else:
        print(p.update_all(full="--full" in sys.argv))
