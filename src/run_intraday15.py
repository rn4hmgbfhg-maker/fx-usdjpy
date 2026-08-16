# -*- coding: utf-8 -*-
"""デイトレ15分配信の実行ラッパ（15分ごと・launchd から起動）

run_intraday.py（毎時）と同じ思想の15分版。launchdが毎時4/19/34/49分に
エンジンを回し、シグナル時はエンジン自身がmacOS通知＋即時メールを出す。
スマホプッシュは毎時のClaudeタスクが未通知キューを見て補完する。

機能:
  1) intraday15_engine.py を実行（失敗時30秒待って最大2回リトライ）
  2) 成否を results/run_log_intraday15.csv に必ず記録
  3) 市場休場（土曜7時〜月曜6時JST）は静かにスキップ

使い方:
  python3 src/run_intraday15.py           # 通常実行
  python3 src/run_intraday15.py --status  # エンジンの現況JSONを表示
  python3 src/run_intraday15.py --check   # 完成率（15分実行の抜け漏れ）を表示
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE_DIR, "src", "intraday15_engine.py")
RUN_LOG = os.path.join(BASE_DIR, "results", "run_log_intraday15.csv")
RETRY_WAIT_SEC = 30
SLOT_MINUTES = (4, 19, 34, 49)


def market_closed(now=None):
    """JST基準: 土曜7時〜月曜6時は休場（NY金曜クローズ〜ウェリントン月曜）。"""
    now = now or datetime.now()
    wd, h = now.weekday(), now.hour
    return (wd == 5 and h >= 7) or wd == 6 or (wd == 0 and h < 6)


def append_run_log(row):
    os.makedirs(os.path.dirname(RUN_LOG), exist_ok=True)
    new = not os.path.exists(RUN_LOG)
    with open(RUN_LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["実行日時", "結果", "所要秒", "アクション", "備考"])
        w.writerow(row)


def open_hours(d):
    wd = d.weekday()
    if wd == 5:
        return list(range(0, 7))
    if wd == 6:
        return []
    if wd == 0:
        return list(range(6, 24))
    return list(range(24))


def open_slots(d):
    """その日に実行すべき (hour, slot_minute) の一覧。"""
    return [(h, m) for h in open_hours(d) for m in SLOT_MINUTES]


def completion_rate(days=3):
    """直近N市場日の「15分実行が漏れなく成功したか」を返す。"""
    runs = {}          # date -> set((hour, slot))
    first_dt = None
    if os.path.exists(RUN_LOG):
        with open(RUN_LOG, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(row["実行日時"])
                except (KeyError, ValueError):
                    continue
                first_dt = min(first_dt or dt, dt)
                if row.get("結果") == "成功":
                    slot = max((m for m in SLOT_MINUTES if m <= dt.minute),
                               default=SLOT_MINUTES[0])
                    runs.setdefault(dt.date(), set()).add((dt.hour, slot))
    if first_dt is None:
        return [], 0, 0

    now = datetime.now()
    rows, d, checked = [], now.date(), 0
    while checked < days and d >= first_dt.date():
        slots = open_slots(d)
        if d == now.date():
            slots = [(h, m) for h, m in slots
                     if (h, m) <= (now.hour, now.minute - 3)]
        if d == first_dt.date():
            slots = [(h, m) for h, m in slots
                     if (h, m) >= (first_dt.hour,
                                   max((x for x in SLOT_MINUTES
                                        if x <= first_dt.minute),
                                       default=SLOT_MINUTES[0]))]
        if slots:
            got = runs.get(d, set())
            miss = [s for s in slots if s not in got]
            rows.append((d, len(slots) - len(miss), len(slots), miss))
            checked += 1
        d -= timedelta(days=1)
    total_got = sum(r[1] for r in rows)
    total_exp = sum(r[2] for r in rows)
    return rows, total_got, total_exp


def print_check():
    rows, got, exp = completion_rate()
    rate = 100 * got / exp if exp else 100
    print(f"=== デイトレ15分完成率（直近3市場日・15分実行ベース）: "
          f"{got}/{exp} = {rate:.0f}% ===")
    for d, g, e, miss in rows:
        mark = "○" if g >= e else "▲"
        miss_s = ",".join(f"{h}:{m:02d}" for h, m in miss[:8])
        if len(miss) > 8:
            miss_s += f" 他{len(miss) - 8}件"
        print(f"  {mark} {d} {g}/{e}" + (f"  欠落: {miss_s}" if miss else ""))
    return rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        print_check()
        return

    if args.status:
        subprocess.run([sys.executable, ENGINE, "--status"], cwd=BASE_DIR)
        return

    if market_closed():
        print("市場休場のためスキップ")
        return

    started = time.time()
    out = ""
    for attempt in range(3):
        p = subprocess.run([sys.executable, ENGINE], cwd=BASE_DIR,
                           capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            act = ""
            for ln in out.splitlines():
                if ln.startswith("[アクション:"):
                    act = ln.strip("[]").replace("アクション: ", "")
            note = f"リトライ{attempt}回で成功" if attempt else ""
            append_run_log([datetime.now().isoformat(timespec="seconds"),
                            "成功", f"{time.time()-started:.0f}", act, note])
            print(out)
            return
        if attempt < 2:
            time.sleep(RETRY_WAIT_SEC)
    append_run_log([datetime.now().isoformat(timespec="seconds"), "失敗",
                    f"{time.time()-started:.0f}", "",
                    out.strip().splitlines()[-1][:200] if out.strip()
                    else "出力なし"])
    print(out)
    sys.exit(1)


if __name__ == "__main__":
    main()
