# -*- coding: utf-8 -*-
"""Mac が唯一の書き手であるファイルを GitHub へ送る（GitHub一本化後の受け渡し口）。
対象は mirror_util.MAC_PATHS:
  ・ファンダ研究の成果物（fundamental_*.json/csv・forecast_log.csv・fund_overlay_*・macro_daily.csv）
  ・日次研究の「進化」が書く戦略パラメータ config.json と研究ログ research_*_log.csv
指示書・ボード・エンジン判定は GitHub Actions がリポジトリ内容から生成するため、
ここで push しないと Mac の研究成果・パラメータ変更はクラウドの判定に一切反映されない。
流れ: Actions管理ファイルのローカル変更を退避→対象だけコミット→fetch→rebase→push。
使い方: python3 scripts/fx_mac_push.py
"""
import os, sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
sys.path.insert(0, os.path.join(BASE, "scripts"))
os.environ["PATH"] = os.path.expanduser("~/.local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
os.makedirs("logs", exist_ok=True)

from mirror_util import MAC_PATHS, discard_actions_owned, sh  # noqa: E402


def log(msg):
    print(msg)
    with open("logs/mac_push.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%F %T} {msg}\n")


def main():
    discard_actions_owned(log)
    existing = [p for p in MAC_PATHS if os.path.exists(p)]
    sh("git", "add", "--", *existing)
    _, staged, _ = sh("git", "diff", "--staged", "--name-only")
    if staged:
        rc, _, err = sh("git", "commit", "-q", "-m",
                        f"mac research {datetime.now():%F %H:%M} JST\n\n" + staged)
        if rc:
            return log(f"commit失敗: {err[:160]}")
        log("Mac成果物をコミット: " + ", ".join(staged.splitlines()))
    else:
        log("Mac成果物に変更なし")

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
