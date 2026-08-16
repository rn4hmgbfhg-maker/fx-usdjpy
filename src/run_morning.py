# -*- coding: utf-8 -*-
"""朝の配信を確実に完遂させる実行ラッパ（完成率100%化・2026-08-05）

背景:
  従来はClaudeのスケジュールタスクが signal_engine.py を直接叩いていたため、
  セッション側が落ちると成果物がゼロになった（2026-08-05に6時・7時が該当）。
  本ラッパを launchd から直接起動することで、成果物の生成を
  Claudeセッションから切り離し、通知だけをセッションの担当にする。

機能:
  1) 現在時刻から対象ステージを決める（6時→1 / 7時→2 / それ以外→3）
  2) 当日の未生成ステージを手前から順に追いつき生成する（バックフィル）
  3) 生成済みステージは再実行しない（冪等・二重ログ防止）
  4) 失敗時はネットワーク障害を想定して最大2回リトライ
  5) 実行の成否を results/run_log.csv に必ず記録する
  6) 直近営業日の完成率を算出して表示する

使い方:
  python3 src/run_morning.py             # 時刻からステージ自動判定
  python3 src/run_morning.py --stage 3   # ステージ明示
  python3 src/run_morning.py --check     # 実行せず現状の完成率だけ表示
"""
import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE_DIR, "src", "signal_engine.py")
ORDERS_DIR = os.path.join(BASE_DIR, "results", "orders")
RUN_LOG = os.path.join(BASE_DIR, "results", "run_log.csv")

TAG = {1: "速報1", 2: "速報2", 3: "確定"}
LABEL = {1: "速報①", 2: "速報②", 3: "最終確定"}
RUN_LOG_COLS = ["実行日時", "対象日", "ステージ", "結果", "所要秒", "備考"]
RETRY_WAIT_SEC = 90
STAGE_TIMEOUT_SEC = 300   # 1試行の上限（通常3〜5秒・ハング時は打ち切って再試行）


def order_path(stage, d=None):
    d = d or date.today()
    return os.path.join(ORDERS_DIR, f"{d.isoformat()}_指示_{TAG[stage]}.txt")


def stage_done(stage, d=None):
    return os.path.exists(order_path(stage, d))


def stage_for_hour(hour):
    """6時台=速報① 7時台=速報② それ以外(8時以降・手動)=最終確定"""
    return {6: 1, 7: 2}.get(hour, 3)


def append_run_log(row):
    os.makedirs(os.path.dirname(RUN_LOG), exist_ok=True)
    new = not os.path.exists(RUN_LOG)
    with open(RUN_LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(RUN_LOG_COLS)
        w.writerow(row)


def run_stage(stage, backfill=False):
    """エンジンを1ステージ実行する。成功可否と出力を返す。"""
    started = time.time()
    out = ""
    for attempt in range(3):
        # ハング上限を必ず設ける。2026-08-12にエンジンが約63分ハングし(Drive書込か
        # 通信の詰まり)、最終確定が8:03→9:34まで遅れた。無制限待機は再発防止できない。
        try:
            p = subprocess.run(
                [sys.executable, ENGINE, "--stage", str(stage)],
                cwd=BASE_DIR, capture_output=True, text=True,
                timeout=STAGE_TIMEOUT_SEC)
            out = (p.stdout or "") + (p.stderr or "")
            rc = p.returncode
        except subprocess.TimeoutExpired as e:
            out = ((e.stdout or "") if isinstance(e.stdout, str)
                   else (e.stdout or b"").decode("utf-8", "replace"))
            out += f"\n[異常] {STAGE_TIMEOUT_SEC}秒でタイムアウト（強制終了）"
            print(f"[{LABEL[stage]}] {STAGE_TIMEOUT_SEC}秒でタイムアウト→中断",
                  flush=True)
            rc = -1
        # 成果物が実在することを成功条件にする（終了コードだけでは信用しない）
        if rc == 0 and stage_done(stage):
            note = "追いつき生成" if backfill else ""
            if backfill:
                _mark_backfill(stage)
            if attempt:
                note = (note + f" リトライ{attempt}回で成功").strip()
            append_run_log([datetime.now().isoformat(timespec="seconds"),
                            date.today().isoformat(), LABEL[stage], "成功",
                            f"{time.time()-started:.0f}", note])
            return True, out
        if attempt < 2:
            time.sleep(RETRY_WAIT_SEC)
    append_run_log([datetime.now().isoformat(timespec="seconds"),
                    date.today().isoformat(), LABEL[stage], "失敗",
                    f"{time.time()-started:.0f}",
                    out.strip().splitlines()[-1][:200] if out.strip() else "出力なし"])
    return False, out


def _mark_backfill(stage):
    """定刻に出せず後から生成した指示書には、その旨を明記しておく。"""
    try:
        with open(order_path(stage), "a", encoding="utf-8") as f:
            f.write(f"※本書は定刻に生成できず {datetime.now():%H:%M} に"
                    "追いつき生成したもの。値は生成時点の最新データによる。\n")
    except OSError:
        pass


def scheme_start():
    """指示書が1件でも存在する最も古い日＝スキーム稼働開始日。"""
    days = []
    for n in os.listdir(ORDERS_DIR) if os.path.isdir(ORDERS_DIR) else []:
        try:
            days.append(date.fromisoformat(n[:10]))
        except ValueError:
            continue
    return min(days) if days else date.today()


def completion_rate(days=10):
    """直近N営業日について「3段階そろったか」を数える。

    稼働開始前の日は対象外（分母に入れると完成率を不当に下げるため）。
    """
    rows, d, checked = [], date.today(), 0
    start = scheme_start()
    while checked < days and d >= start:
        if d.weekday() < 5:
            done = sum(1 for s in (1, 2, 3) if stage_done(s, d))
            # 当日はまだ進行中なので「これまでに到達すべき段階」までで評価する
            expect = 3
            if d == date.today():
                expect = stage_for_hour(datetime.now().hour)
                if expect == 3 and datetime.now().hour < 8:
                    expect = 2
            rows.append((d, min(done, 3), expect))
            checked += 1
        d -= timedelta(days=1)
    got = sum(r[1] for r in rows)
    exp = sum(r[2] for r in rows)
    return rows, got, exp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=[1, 2, 3])
    ap.add_argument("--check", action="store_true",
                    help="実行せず完成率と当日の欠落状況だけ表示")
    args = ap.parse_args()

    target = args.stage or stage_for_hour(datetime.now().hour)
    print(f"=== 朝の配信ラッパ {date.today().isoformat()} "
          f"{datetime.now():%H:%M} 対象={LABEL[target]} ===")

    ok_all = True
    if not args.check:
        for st in range(1, target + 1):
            if stage_done(st):
                print(f"[{LABEL[st]}] 生成済み（スキップ）")
                continue
            backfill = st < target
            tag = "追いつき生成" if backfill else "生成"
            print(f"[{LABEL[st]}] {tag}を実行...")
            ok, out = run_stage(st, backfill=backfill)
            print(f"[{LABEL[st]}] {'成功' if ok else '★失敗'}")
            if not ok:
                ok_all = False
                print(out.strip()[-600:])

    # 最終確定はメールでも本人宛に自動送信する（2026-08-12 ユーザー指示）。
    # 失敗してもシグナル配信は続ける（メールは冗長経路であり必須経路ではない）。
    if not args.check and target == 3 and stage_done(3):
        try:
            import mailer
            ok_mail, msg = mailer.send()
            print(f"[メール] {msg}")
            if not ok_mail:
                append_run_log([datetime.now().isoformat(timespec="seconds"),
                                date.today().isoformat(), "メール", "失敗",
                                "0", msg[:200]])
        except Exception as e:                                  # noqa: BLE001
            print(f"[メール] 送信処理でエラー: {type(e).__name__}: {e}")

    rows, got, exp = completion_rate()
    print(f"\n=== 完成率（直近10営業日）: {got}/{exp} "
          f"= {100*got/exp if exp else 0:.0f}% ===")
    for d, done, expct in rows[:5]:
        mark = "○" if done >= expct else "▲"
        # まだ配信時刻が来ていない段階は「欠落」に数えない
        miss = [LABEL[s] for s in range(1, expct + 1) if not stage_done(s, d)]
        print(f"  {mark} {d} {done}/{expct}"
              + (f"  欠落: {', '.join(miss)}" if miss else ""))

    # 決済履歴→運用成績の反映を毎朝自動点検する。
    # 2026-08-14: 「決済しても成績に反映されない」不具合が、ユーザーの指摘まで
    # 誰にも気づかれずに残った。以後は毎回ここで声を上げる。
    print("\n=== 決済履歴→運用成績の整合点検 ===")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import pandas as _pd
        import perf as _perf
        _dl = os.path.join(BASE_DIR, "results", "daily_log.csv")
        _d = _pd.read_csv(_dl, dtype={"日付": str}) if os.path.exists(_dl) else None
        _iss = _perf.check(_d)
        if _iss:
            for _i in _iss:
                print(f"  ⚠ {_i}")
        else:
            _s = _perf.summary()
            print(f"  ○ 異常なし（決済{_s['件数']}件・累計実現 "
                  f"{_s['累計実現損益']:+,.0f}円が4システム分すべて反映）")
    except Exception as _e:
        print(f"  ⚠ 点検自体が失敗: {type(_e).__name__}: {_e}")

    if not args.check:
        print("\n=== 本日の指示書（対象ステージ） ===")
        if stage_done(target):
            with open(order_path(target), encoding="utf-8") as f:
                print(f.read().rstrip())
        else:
            print("★指示書を生成できていません。通知で必ずエラーを報告すること。")

    sys.exit(0 if ok_all and (args.check or stage_done(target)) else 1)


if __name__ == "__main__":
    main()
