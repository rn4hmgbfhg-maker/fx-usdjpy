# -*- coding: utf-8 -*-
"""GitHub一本化(2026-09-21): Macはエンジンを動かさない。「時計」と「ミラー」だけを担う。
 1) GitHub Actions(signals.yml)を手動起動 … GitHubのcronは実測2〜5時間に1回しか回らないため
 2) 完了を待って git pull --ff-only      … tenki_latest.py / 転記が読む results を最新化
 3) 指示書をDriveへコピー＋ダッシュボード更新 … 旧 signal_engine のDrive連動の代替
状態(state*.json)の書き手は GitHub Actions ただ一つ。このスクリプトは状態を書かない。
launchd から /usr/bin/python3 で直接起動すること(zsh経由だとDriveへのアクセス権が外れる)。
使い方: python3 scripts/fx_github_tick.py [--mirror-only] [--force-drive]
"""
import filecmp, glob, os, shutil, subprocess, sys, time
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
os.environ["PATH"] = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
os.makedirs("logs", exist_ok=True)


def log(msg):
    with open("logs/github_tick.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} {msg}\n")


def sh(*a, timeout=60):
    r = subprocess.run(a, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def weekend():
    n = datetime.now(); d, h = n.isoweekday(), n.hour   # JST 土9:00〜月6:00
    return (d == 6 and h >= 9) or d == 7 or (d == 1 and h < 6)


def trigger():
    rc, _, err = sh("gh", "workflow", "run", "signals.yml", "--ref", "main")
    if rc:
        return log(f"workflow起動失敗: {err[:120]}")
    time.sleep(10)
    rc, rid, _ = sh("gh", "run", "list", "--workflow", "signals.yml", "-e", "workflow_dispatch",
                    "-L", "1", "--json", "databaseId", "-q", ".[0].databaseId")
    if not rid:
        return log("run id 取得失敗")
    try:
        rc, _, _ = sh("gh", "run", "watch", rid, "--exit-status", timeout=300)
    except subprocess.TimeoutExpired:
        rc = "timeout"
    log(f"run {rid} rc={rc}")


def mirror():
    # 2026-09-22: Mac側タスクがActions管理ファイルを書き換えると pull が詰まるため、
    # 先に退避して戻す（ファンダ成果物は mirror_util.FUND_PATHS で保護）
    sys.path.insert(0, os.path.join(BASE, "scripts"))
    try:
        from mirror_util import discard_actions_owned
        discard_actions_owned(log)
    except Exception as e:  # noqa: BLE001
        log(f"作業ツリー整理失敗: {e}")
    _, before, _ = sh("git", "rev-parse", "HEAD")
    rc, _, err = sh("git", "pull", "--ff-only", "-q", "origin", "main", timeout=120)
    if rc:
        log(f"pull失敗(ローカルに未pushコミットがあれば scripts/fx_fund_push.py で整える): {err[:160]}")
    _, after, _ = sh("git", "rev-parse", "HEAD")
    if before != after:
        log(f"mirror {before[:7]} -> {after[:7]}")
    return before != after


def drive():
    sys.path.insert(0, os.path.join(BASE, "src"))
    import local_settings
    gd = local_settings.get("drive_dir")
    if not gd or not os.path.isdir(gd):
        return log("Driveフォルダなし: スキップ")
    dst = os.path.join(gd, "指示書"); os.makedirs(dst, exist_ok=True)
    for p in sorted(glob.glob(os.path.join(BASE, "results", "orders", "*_指示_確定.txt")))[-5:]:
        q = os.path.join(dst, os.path.basename(p))
        try:
            if not os.path.exists(q) or not filecmp.cmp(p, q, shallow=False):
                shutil.copy2(p, q); log("Drive指示書コピー: " + os.path.basename(p))
        except PermissionError:
            pass  # launchd配下は既存Driveファイルを読めない(TCC)。新規作成は通るので実害なし
        except OSError as e:
            log(f"Driveコピー失敗 {os.path.basename(p)}: {e}")
    try:
        import dashboard
        dashboard.build()
    except Exception as e:  # noqa: BLE001
        mark = "logs/.dash_err_day"   # 同じ失敗は1日1回だけ記録
        today = f"{datetime.now():%F}"
        if not os.path.exists(mark) or open(mark).read() != today:
            open(mark, "w").write(today)
            log(f"ダッシュボード更新失敗(python3にフルディスクアクセス未付与の場合は既知): {e}")


if __name__ == "__main__":
    if "--mirror-only" not in sys.argv and not weekend():
        trigger()
    changed = mirror()
    if changed or "--force-drive" in sys.argv:
        drive()
