# -*- coding: utf-8 -*-
"""経済指標カレンダー取得(Forex Factory 週間公開フィード)

今後N時間以内の最重要(★5級)USD/JPYイベントのみを返す。
Forex Factoryの重要度はHigh/Medium/Lowの3段階しかないため、
「High impact かつ ★5級イベント名(STAR5_PATTERNS)に一致」の2段フィルタで
変動率★5(最上位)のみを警戒対象とする(2026-07-30 ユーザー指示。
GDP速報・PCE・小売売上高・ISM等の★4級は警戒対象外)。
取得失敗時は None を返し、エンジン側は「手動確認推奨」の注意書きを出して
新規見送りは行わない(フェイルオープン)。
"""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

FEED_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
JST = ZoneInfo("Asia/Tokyo")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "results", "cache")
CACHE_FRESH_H = 3    # これ以内なら再取得しない（429対策の本体）
CACHE_STALE_H = 72   # 取得失敗時に代替として許容する上限

# 主要イベント名の日本語補足(部分一致)
JP_NAMES = {
    "Non-Farm": "米雇用統計",
    "FOMC": "FOMC",
    "Federal Funds Rate": "米政策金利",
    "CPI": "消費者物価指数",
    "BOJ": "日銀",
    "Monetary Policy": "金融政策",
    "GDP": "GDP",
    "PCE": "PCEデフレータ",
    "Unemployment": "失業率",
    "Retail Sales": "小売売上高",
    "ISM": "ISM景況指数",
}


# ★5級(最上位)イベント名の部分一致パターン。これ以外はHigh impactでも警戒しない
STAR5_PATTERNS = [
    "non-farm",                 # 米雇用統計(NFP)
    "average hourly earnings",  # 平均時給(雇用統計同時発表)
    "fomc",                     # FOMC声明・会見・議事要旨
    "federal funds rate",       # 米政策金利
    "cpi",                      # 米消費者物価指数
    "boj",                      # 日銀(政策金利・会見)
    "monetary policy",          # 金融政策(日銀声明等)
]


def _jp_hint(title):
    for k, v in JP_NAMES.items():
        if k.lower() in title.lower():
            return v
    return ""


def _is_star5(title):
    t = title.lower()
    return any(p in t for p in STAR5_PATTERNS)


def _cache_path(url):
    name = url.rsplit("/", 1)[-1]
    return os.path.join(CACHE_DIR, name)


def _load_cache(url, max_age_h):
    """キャッシュが max_age_h 以内なら (data, 経過時間h) を返す。無ければ None。"""
    p = _cache_path(url)
    try:
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(blob["fetched"])).total_seconds() / 3600
        if age <= max_age_h:
            return blob["data"], age
    except Exception:
        pass
    return None


def _save_cache(url, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(url), "w", encoding="utf-8") as f:
            json.dump({"fetched": datetime.now(timezone.utc).isoformat(),
                       "data": data}, f)
    except Exception:
        pass


def _feed(url):
    """週次フィードを取得。鮮度優先でキャッシュ→HTTP→古いキャッシュの順。

    2026-08-13: 本日08:03の最終確定で取得に失敗し、指標見送りフィルタが丸一日
    無効になった。原因を調べたところ一過性の通信断ではなく HTTP 429（配信元の
    レート制限）だった。本スキームは朝3回＋デイトレ毎時で1日約50回このフィードを
    叩いており、制限に触れるのが当然の頻度だった。そこで
      ・取得結果をディスクにキャッシュし、3時間以内なら再取得しない（叩く回数を
        1日約50回→数回へ減らす。これが429の根本対策）
      ・それでも失敗したときは72時間以内の旧キャッシュで代替する（週次フィード
        なので前日分でも当日のイベントを正しく含む）
    とし、「取得失敗＝警戒フィルタ全面停止」に落ちる確率を大きく下げる。
    """
    fresh = _load_cache(url, CACHE_FRESH_H)
    if fresh:
        return fresh[0]
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json()
        _save_cache(url, data)
        return data
    except Exception:
        stale = _load_cache(url, CACHE_STALE_H)
        return stale[0] if stale else None


def upcoming_events(hours=24):
    """(JST日時, 通貨, 英名, 日本語補足) のリスト。取得失敗は None。"""
    events = []
    ok = False
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=hours)
    for url in FEED_URLS:
        # nextweek フィードは平日は 404 を返すことがあり（2026-08-13確認）、
        # 週末に地平線が翌週へまたぐときだけ必要になる。無駄な request は
        # レート制限を招くので、必要な曜日以外は取りにいかない。
        if "nextweek" in url and horizon.weekday() not in (4, 5, 6):
            continue
        data = _feed(url)
        if data is None:
            continue
        ok = True
        for e in data:
            if e.get("impact") != "High":
                continue
            if e.get("country") not in ("USD", "JPY"):
                continue
            if not _is_star5(e.get("title", "")):
                continue  # ★4以下は警戒対象外
            try:
                dt = datetime.fromisoformat(e["date"])
            except (KeyError, ValueError):
                continue
            if now <= dt <= horizon:
                events.append((dt.astimezone(JST), e["country"],
                               e.get("title", ""), _jp_hint(e.get("title", ""))))
    if not ok:
        return None
    return sorted(set(events))


if __name__ == "__main__":
    ev = upcoming_events(24)
    if ev is None:
        print("カレンダー取得失敗")
    else:
        print(f"今後24時間の★5級イベント: {len(ev)}件")
        for jst, cur, title, jp in ev:
            print(f"  {jst:%m/%d(%a) %H:%M} {cur} {title} {jp}")
