# -*- coding: utf-8 -*-
"""ボードの「現在価格に更新」用データ（2026-10-15 落合さん指示「現在価格を表示したいので更新ボタンを。
全通貨・チャートも」）。

claude.ai のボードは外部サイト（GitHub・Yahoo）を直接読めない（安全設定で fetch が止まる）ため、
Mac の15分ごとの処理（scripts/fx_github_tick.py）がこのファイルを Google Drive の
「FXドル円自動売買/ライブ価格/」へ書き、ボードは Google Drive コネクタで読む。

中身: 4通貨の現在値（Yahoo の regularMarketPrice）と、スキャル用チャートの足
（15分・30分・1時間・4時間・8時間・12時間・24時間。各足 [分, 始, 高, 安, 終, EMA20, EMA50]）。

★launchd から動く Python は Drive 上の既存ファイルを上書きできない（macOS の権限。新規作成だけ通る）。
  上書きできないときは何もしない（ファイルを増やさない）。書き手は
  ①Claude の毎時タスク（Claude アプリ経由なので上書きできる）②launchd の15分ごとの処理（権限があれば）。

使い方: python3 src/live_board.py [--out DIR]   # 既定は local_settings の drive_dir/ライブ価格
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PAIRS = {"usdjpy": "ドル円", "eurusd": "ユーロドル", "eurjpy": "ユーロ円", "gbpjpy": "ポンド円"}
DEC = {"usdjpy": 3, "eurusd": 5, "eurjpy": 3, "gbpjpy": 3}
FRAMES = {"15分": ("m15", "15min", 320), "30分": ("m15", "30min", 240), "1時間": ("h1", "1h", 220),
          "4時間": ("h1", "4h", 200), "8時間": ("h1", "8h", 180), "12時間": ("h1", "12h", 160),
          "24時間": ("h1", "1D", 160)}


def _bars(pair):
    import research_scalp as rs                                  # noqa: PLC0415
    m15, h1 = rs._load_pair(pair) if pair != "usdjpy" else rs._load_pair("usdjpy")
    m15 = m15[m15.index >= m15.index[-1] - __import__("pandas").Timedelta(days=20)]
    hsrc = rs._base_for("1時間", m15, h1)
    dec = DEC[pair]
    out = {}
    for name, (src, rule, keep) in FRAMES.items():
        base = m15 if src == "m15" else hsrc
        if src == "h1":
            base = base[base.index >= base.index[-1] - __import__("pandas").Timedelta(days=400)]
        df = rs.add_indicators(rs.resample(base, rule)).tail(keep)
        out[name] = [[int(t.timestamp() // 60), round(r.Open, dec), round(r.High, dec), round(r.Low, dec),
                      round(r.Close, dec), round(r.ema20, dec), round(r.ema50, dec)] for t, r in df.iterrows()]
    return out, f"{m15.index[-1]:%m/%d %H:%M}"


def build():
    import data_fetch_yenpair as dyp                             # noqa: PLC0415
    live = {}
    try:
        live = dyp.write_live_prices(path=os.devnull).get("pairs", {})
    except Exception as e:                                       # noqa: BLE001
        live = {"error": str(e)[:80]}
    out = {"更新": datetime.now().astimezone().isoformat(timespec="seconds"), "pairs": {}}
    for pair, nm in PAIRS.items():
        row = {"名": nm}
        lp = live.get(pair) if isinstance(live, dict) else None
        if lp and lp.get("price"):
            row["price"], row["time"] = lp["price"], lp["time"]
        try:
            row["tf"], row["最終"] = _bars(pair)
        except Exception as e:                                   # noqa: BLE001
            row["error"] = str(e)[:80]
        out["pairs"][pair] = row
    return out


def _out_dir(out_dir):
    if out_dir is None:
        import local_settings                                    # noqa: PLC0415
        gd = local_settings.get("drive_dir")
        if not gd or not os.path.isdir(gd):
            return None
        out_dir = os.path.join(gd, "ライブ価格")
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def write_prices(out_dir=None):
    """価格だけの小さいファイル fx_live_prices.json（約0.5KB）。launchd が毎分書く（2026-10-15
    落合さん指摘「更新ボタンを押しても時刻が更新されない」＝毎時の書き込みだけでは古いままだった）。"""
    import data_fetch_yenpair as dyp                             # noqa: PLC0415
    out_dir = _out_dir(out_dir)
    if not out_dir:
        return "Driveフォルダなし: スキップ"
    live = dyp.write_live_prices(path=os.devnull)
    data = json.dumps(live, ensure_ascii=False, separators=(",", ":"))
    p = os.path.join(out_dir, "fx_live_prices.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        return f"上書き: {p}"
    except PermissionError:
        return "上書き不可（launchd の権限）: スキップ"


def write(out_dir=None):
    out_dir = _out_dir(out_dir)
    if not out_dir:
        return "Driveフォルダなし: スキップ"
    data = json.dumps(build(), ensure_ascii=False, separators=(",", ":"))
    try:
        write_prices(out_dir)
    except Exception:                                            # noqa: BLE001
        pass
    p = os.path.join(out_dir, "fx_live_latest.json")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        return f"上書き: {p}（{len(data):,}バイト）"
    except PermissionError:
        # 日時入りの新しいファイルを毎回作ると Drive が散らかる（1日100本・30MB超）ため、作らない。
        # launchd 側で書けるようにするには python3 にフルディスクアクセスが要る（落合さんの操作）。
        return "上書き不可（launchd の権限）: スキップ"


if __name__ == "__main__":
    od = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    if "--prices-only" in sys.argv:
        msg = write_prices(od)
        if "スキップ" in msg:   # 毎分のログを汚さないよう、失敗は1日1回だけ
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            mark = os.path.join(base, "logs", ".live_prices_skip_day")
            today = f"{datetime.now():%F}"
            os.makedirs(os.path.dirname(mark), exist_ok=True)
            if not os.path.exists(mark) or open(mark).read() != today:
                open(mark, "w").write(today)
                with open(os.path.join(base, "logs", "live_prices.log"), "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now():%F %T} {msg}\n")
        print(msg)
    else:
        print(write(od))
