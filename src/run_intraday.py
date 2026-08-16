# -*- coding: utf-8 -*-
"""デイトレ配信の実行ラッパ（毎時・launchd から起動）

朝の run_morning.py と同じ思想: 成果物の生成をClaudeセッションから切り離し、
launchdが毎時確実にエンジンを回す。通知(スマホプッシュ)だけをClaude側が担う。

機能:
  1) intraday_engine.py を実行（失敗時60秒待って最大2回リトライ）
  2) 成否を results/run_log_intraday.csv に必ず記録
  3) 市場休場（土曜7時〜月曜6時JST）は静かにスキップ

使い方:
  python3 src/run_intraday.py           # 通常実行
  python3 src/run_intraday.py --status  # エンジンの現況JSONを表示
  python3 src/run_intraday.py --check   # 完成率（毎時実行の抜け漏れ）を表示
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE_DIR, "src", "intraday_engine.py")
RUN_LOG = os.path.join(BASE_DIR, "results", "run_log_intraday.csv")
RETRY_WAIT_SEC = 60


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
    """その日にlaunchdが実行すべき時間(hour)のリスト（JST・市場開場時間のみ）。"""
    wd = d.weekday()
    if wd == 5:                       # 土曜: 0〜6時(NYクローズまで)
        return list(range(0, 7))
    if wd == 6:                       # 日曜: 休場
        return []
    if wd == 0:                       # 月曜: 6時(ウェリントン)から
        return list(range(6, 24))
    return list(range(24))            # 火〜金: 終日


def completion_rate(days=5):
    """直近N市場日の「毎時実行が漏れなく成功したか」を返す。

    稼働開始前の時間帯は分母に入れない（開始日は初回実行時刻以降で評価）。
    返り値: (行リスト[(日付, 成功時間数, 期待時間数, 欠落hourリスト)], 合計成功, 合計期待)
    """
    runs = {}          # date -> set(hour) 成功した時間
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
                    runs.setdefault(dt.date(), set()).add(dt.hour)
    if first_dt is None:
        return [], 0, 0

    now = datetime.now()
    rows, d, checked = [], now.date(), 0
    while checked < days and d >= first_dt.date():
        hours = open_hours(d)
        if d == now.date():
            hours = [h for h in hours if h < now.hour or
                     (h == now.hour and now.minute >= 10)]
        if d == first_dt.date():
            hours = [h for h in hours if h >= first_dt.hour]
        if hours:
            got = runs.get(d, set())
            miss = [h for h in hours if h not in got]
            rows.append((d, len(hours) - len(miss), len(hours), miss))
            checked += 1
        d -= timedelta(days=1)
    total_got = sum(r[1] for r in rows)
    total_exp = sum(r[2] for r in rows)
    return rows, total_got, total_exp


def print_check():
    rows, got, exp = completion_rate()
    rate = 100 * got / exp if exp else 100
    print(f"=== デイトレ完成率（直近5市場日・毎時実行ベース）: "
          f"{got}/{exp} = {rate:.0f}% ===")
    for d, g, e, miss in rows:
        mark = "○" if g >= e else "▲"
        print(f"  {mark} {d} {g}/{e}"
              + (f"  欠落時間帯: {','.join(f'{h}時' for h in miss)}" if miss
                 else ""))
    return rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="実行せず完成率だけ表示")
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
            print_check()
            return
        if attempt < 2:
            time.sleep(RETRY_WAIT_SEC)
    append_run_log([datetime.now().isoformat(timespec="seconds"), "失敗",
                    f"{time.time()-started:.0f}", "",
                    out.strip().splitlines()[-1][:200] if out.strip()
                    else "出力なし"])
    print(out)
    print_check()
    sys.exit(1)


if __name__ == "__main__":
    main()
