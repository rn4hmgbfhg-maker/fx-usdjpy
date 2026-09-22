# -*- coding: utf-8 -*-
"""ファンダ研究の成果物（Macが唯一の書き手）を GitHub へ送る。
GitHub一本化(2026-09-21)後、指示書・ボードは GitHub Actions がリポジトリ内容から生成する。
ファンダ研究は WebSearch を要するため Mac 側でしか動かせず、成果物を push しないと
Actions 側の fund_brief / dashboard_web が8月版を読み続けて「N日更新なし」と出る。
流れ: Actions管理ファイルのローカル変更を退避→ファンダ成果物だけコミット→rebase→push。
使い方: python3 scripts/fx_fund_push.py
"""
import os, sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))
os.environ["PATH"] = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
os.makedirs("logs", exist_ok=True)

from mirror_util import FUND_PATHS, discard_actions_owned, sh  # noqa: E402


def log(msg):
    print(msg)
    with open("logs/fund_push.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} {msg}\n")


def main():
    discard_actions_owned(log)
    existing = [p for p in FUND_PATHS if os.path.exists(p)]
    sh("git", "add", "--", *existing)
    rc, _, _ = sh("git", "diff", "--staged", "--quiet")
    if rc:
        rc, _, err = sh("git", "commit", "-q", "-m",
                        f"fundamental {datetime.now():%F %H:%M} JST")
        if rc:
            return log(f"commit失敗: {err[:160]}")
        log("ファンダ成果物をコミット")
    else:
        log("ファンダ成果物に変更なし")

    rc, _, err = sh("git", "fetch", "-q", "origin", "main", timeout=120)
    if rc:
        return log(f"fetch失敗: {err[:160]}")
    _, ahead, _ = sh("git", "rev-list", "--count", "origin/main..HEAD")
    _, behind, _ = sh("git", "rev-list", "--count", "HEAD..origin/main")
    if int(behind or 0):
        rc, _, err = sh("git", "rebase", "-q", "origin/main", timeout=120)
        if rc:
            sh("git", "rebase", "--abort")
            return log(f"rebase失敗(中止した・要確認): {err[:200]}")
        log(f"origin/main の {behind} コミットへ追いついた")
    if int(ahead or 0):
        rc, _, err = sh("git", "push", "-q", "origin", "main", timeout=120)
        if rc:
            return log(f"push失敗: {err[:200]}")
        log(f"{ahead} コミットを push した")
    else:
        log("push するものなし")


if __name__ == "__main__":
    main()
