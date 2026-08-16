# -*- coding: utf-8 -*-
"""ドル円 15分足データ取得（15分デイトレシグナル用）

2系統のデータを組み合わせて15分足の連続履歴を作る:

1) Yahoo Finance 公開チャートAPI（interval=15m・直近60日まで）
   → data/usdjpy_15m.csv にマージ保存して履歴を育てる（1時間足と同方式）。
2) Dukascopy 公開データフィード（BID 1分足キャンドル・数年分）
   → 15分足へリサンプルして data/usdjpy_15m_hist.csv に保存（バックテスト用の
     長期履歴。1回バックフィルすれば以後は不要）。

load_15m() は両者をマージして返す（重複はYahoo側を優先）。
未確定の最新バーは常に捨て、確定バーのみで判定する。

使い方:
  python3 src/data_fetch_15m.py             # Yahoo取得+マージ保存して概要表示
  python3 src/data_fetch_15m.py --backfill 2023-01-01   # Dukascopy長期バックフィル
"""
import lzma
import os
import struct
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_15M = os.path.join(BASE_DIR, "data", "usdjpy_15m.csv")
CSV_15M_HIST = os.path.join(BASE_DIR, "data", "usdjpy_15m_hist.csv")

# UAは既存1時間足フェッチャ(data_fetch_intraday)と同じ短い文字列を使う。
# YahooのレートリミットはUA単位でかかるため、長いChrome風UAが429の間も
# このUAは通る（2026-08-13確認）。
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer": "https://freeserv.dukascopy.com/",
}
_SESSION = requests.Session()
_SESSION.headers.update(_HEADERS)


# ---------------------------------------------------------------- Yahoo 15分足
def fetch_15m(range_str: str = "60d") -> pd.DataFrame:
    """15分足OHLCを取得する。indexはバー開始時刻(UTC, tz-aware)。
    query1/query2×レンジ縮小の順でフォールバックする。"""
    last_err = None
    res = None
    for host in ("query1", "query2"):
        for rng in (range_str, "30d", "7d"):
            url = f"https://{host}.finance.yahoo.com/v8/finance/chart/USDJPY=X"
            params = {"range": rng, "interval": "15m"}
            try:
                r = requests.get(url, params=params, headers=_HEADERS,
                                 timeout=15)
                if r.status_code == 429:
                    # レート制限中は粘っても悪化するだけなので即あきらめる
                    raise RuntimeError("Yahoo 15分足取得失敗: 429 レート制限中")
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                break
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
        if res is not None:
            break
    if res is None:
        raise RuntimeError(f"Yahoo 15分足取得失敗: {last_err}")
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Open": q["open"], "High": q["high"], "Low": q["low"],
        "Close": q["close"],
    }, index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    df.index.name = "Time"
    df = df.dropna()
    # 未確定バー(開始から15分経過していない)は捨てる
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + pd.Timedelta(minutes=15) <= now]
    # Yahooの15分グリッドに乗らない端数タイムスタンプ(稀にある)を除外
    df = df[(df.index.minute % 15 == 0) & (df.index.second == 0)]
    return df


def update_yahoo_cache() -> pd.DataFrame:
    """Yahoo 15分足をキャッシュにマージ保存して返す(取得失敗時はキャッシュ)。

    レート制限(429)対策: キャッシュが十分新しい時は差分だけ取れる小さな
    レンジ(1d)で取得し、リクエスト負荷を最小にする。"""
    cached = None
    if os.path.exists(CSV_15M):
        cached = pd.read_csv(CSV_15M, index_col="Time", parse_dates=["Time"])
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
    rng = "60d"
    if cached is not None and len(cached):
        age_h = (pd.Timestamp.now(tz="UTC")
                 - cached.index.max()).total_seconds() / 3600
        rng = "1d" if age_h <= 12 else "5d" if age_h <= 96 else "60d"
    try:
        fresh = fetch_15m(rng)
    except Exception:
        fresh = None
    if fresh is not None and len(fresh):
        merged = fresh if cached is None else pd.concat([cached, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        os.makedirs(os.path.dirname(CSV_15M), exist_ok=True)
        merged.to_csv(CSV_15M)
        return merged
    if cached is None:
        raise RuntimeError("15分足データが取得できずキャッシュもありません")
    return cached


# ------------------------------------------------------- Dukascopy 長期履歴
def _fetch_dukascopy_day(d: date, retries: int = 3) -> "pd.DataFrame | None":
    """1日分のBID 1分足を取得して15分足にリサンプルする。休場日はNone。"""
    url = (f"https://datafeed.dukascopy.com/datafeed/USDJPY/"
           f"{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, timeout=20)
        except Exception:
            _time.sleep(2 + attempt * 3)
            continue
        if r.status_code == 404 or (r.status_code == 200 and not r.content):
            return None
        if r.status_code != 200:
            _time.sleep(2 + attempt * 3)
            continue
        try:
            raw = lzma.decompress(r.content)
        except Exception:
            return None
        n = len(raw) // 24
        if n == 0:
            return None
        rows = [struct.unpack(">5if", raw[i * 24:(i + 1) * 24])
                for i in range(n)]
        base = pd.Timestamp(d, tz="UTC")
        df = pd.DataFrame(rows, columns=["sec", "o", "c", "l", "h", "v"])
        df.index = base + pd.to_timedelta(df["sec"], unit="s")
        # 価格はポイント値(JPYペアは1/1000)
        m1 = pd.DataFrame({"Open": df["o"] / 1000, "High": df["h"] / 1000,
                           "Low": df["l"] / 1000, "Close": df["c"] / 1000,
                           "Volume": df["v"]})
        # 出来高ゼロの詰め物バー(休場時間帯)を除外してからリサンプル
        m1 = m1[m1["Volume"] > 0]
        if m1.empty:
            return None
        out = pd.DataFrame({
            "Open": m1["Open"].resample("15min").first(),
            "High": m1["High"].resample("15min").max(),
            "Low": m1["Low"].resample("15min").min(),
            "Close": m1["Close"].resample("15min").last(),
        }).dropna()
        out.index.name = "Time"
        return out
    return None


def backfill_dukascopy(start: date, end: "date | None" = None,
                       sleep_sec: float = 0.12) -> pd.DataFrame:
    """start〜endの15分足履歴を構築して CSV_15M_HIST に保存する。

    既存ファイルがあれば続きから取得する(再実行に安全)。
    """
    end = end or (date.today() - timedelta(days=1))
    existing = None
    if os.path.exists(CSV_15M_HIST):
        existing = pd.read_csv(CSV_15M_HIST, index_col="Time",
                               parse_dates=["Time"])
        if existing.index.tz is None:
            existing.index = existing.index.tz_localize("UTC")
        if len(existing):
            last_day = existing.index.max().date()
            if last_day >= start:
                start = last_day + timedelta(days=1)
    frames = [] if existing is None else [existing]
    d = start
    got, miss, fail_streak = 0, 0, 0
    while d <= end:
        if d.weekday() == 5:  # 土曜は完全休場
            d += timedelta(days=1)
            continue
        day_df = _fetch_dukascopy_day(d)
        if day_df is not None and len(day_df):
            frames.append(day_df)
            got += 1
            fail_streak = 0
        else:
            miss += 1
            fail_streak += 1
            # 直近日はDukascopy側が未公開のことがある。10営業日連続欠落で打切り
            if fail_streak >= 10 and (end - d).days < 20:
                print(f"打切り: {d} 以降は未公開の可能性")
                break
        if got % 20 == 0 and got:
            merged = pd.concat(frames)
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            merged.to_csv(CSV_15M_HIST)
        _time.sleep(sleep_sec)
        d += timedelta(days=1)
    merged = pd.concat(frames) if frames else pd.DataFrame()
    if len(merged):
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        os.makedirs(os.path.dirname(CSV_15M_HIST), exist_ok=True)
        merged.to_csv(CSV_15M_HIST)
    print(f"バックフィル完了: 取得{got}日 欠落{miss}日 計{len(merged)}本")
    return merged


# ------------------------------------------------------------------- 統合
def load_15m(fetch: bool = True) -> pd.DataFrame:
    """長期履歴+Yahooキャッシュを統合した15分足を返す(Yahoo優先)。"""
    frames = []
    if os.path.exists(CSV_15M_HIST):
        hist = pd.read_csv(CSV_15M_HIST, index_col="Time",
                           parse_dates=["Time"])
        if hist.index.tz is None:
            hist.index = hist.index.tz_localize("UTC")
        frames.append(hist[["Open", "High", "Low", "Close"]])
    if fetch:
        try:
            yahoo = update_yahoo_cache()
        except RuntimeError:
            if not frames:
                raise
            yahoo = None   # 長期履歴があればYahoo一時障害でも動かす
    else:
        yahoo = (pd.read_csv(CSV_15M, index_col="Time", parse_dates=["Time"])
                 if os.path.exists(CSV_15M) else None)
    if yahoo is not None:
        if yahoo.index.tz is None:
            yahoo.index = yahoo.index.tz_localize("UTC")
        frames.append(yahoo[["Open", "High", "Low", "Close"]])
    if not frames:
        raise RuntimeError("15分足データがありません")
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        start = datetime.strptime(sys.argv[i + 1], "%Y-%m-%d").date()
        backfill_dukascopy(start)
    else:
        df = load_15m()
        print(f"15分足: {len(df)}本  {df.index[0]} -> {df.index[-1]}")
