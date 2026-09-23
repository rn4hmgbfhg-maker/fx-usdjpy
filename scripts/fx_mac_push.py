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


def stash_untracked_collisions(log):
    """origin/main が持つパスと同名の未追跡ファイルを退避する（削除はしない）。

    2026-09-23: results/research_fund_filter.csv が未追跡のまま残っており、
    rebase の checkout が「上書きになる」として中止された。財務まわりの
    ファイルは消さない方針なので backup/mirror_discard/ へ移して保存する。
    """
    _, untracked, _ = sh("git", "ls-files", "--others", "--exclude-standard")
    if not untracked:
        return
    _, tracked, _ = sh("git", "ls-tree", "-r", "--name-only", "origin/main")
    upstream = set(tracked.splitlines())
    hit = [f for f in untracked.splitlines() if f in upstream]
    if not hit:
        return
    dest = os.path.join("backup", "mirror_discard",
                        f"{datetime.now():%F_%H%M%S}_untracked")
    os.makedirs(dest, exist_ok=True)
    for f in hit:
        target = os.path.join(dest, f.replace(os.sep, "_"))
        os.replace(f, target)
    log(f"未追跡ファイル{len(hit)}件を{dest}へ退避した: " + ", ".join(hit))


def resolve_mac_owned_conflicts(log):
    """Mac が正であるファイルの衝突だけ Mac 版を採用して rebase を続ける。

    macro_daily.csv は Mac と Actions の双方が当日行を書くため毎回衝突しうる。
    Mac 側は research_fundamental.py が実測値を取り直した直後なのでこちらを採る。
    Mac 管理外のファイルが1つでも衝突していたら手を出さず中止させる。
    """
    _, conflicted, _ = sh("git", "diff", "--name-only", "--diff-filter=U")
    files = [f for f in conflicted.splitlines() if f]
    if not files:
        return 1
    outside = [f for f in files if f not in MAC_PATHS]
    if outside:
        log("Mac管理外のファイルが衝突: " + ", ".join(outside))
        return 1
    for f in files:
        sh("git", "checkout", "--theirs", "--", f)   # rebase中の theirs = Mac側
        sh("git", "add", "--", f)
    env_rc, _, err = sh("git", "-c", "core.editor=true", "rebase", "--continue",
                        timeout=120)
    if env_rc:
        log(f"衝突解決後のrebase継続に失敗: {err[:160]}")
        return 1
    log("衝突をMac側の値で解決して続行: " + ", ".join(files))
    return 0


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
        stash_untracked_collisions(log)
        # --autostash: src/*.py などの未コミット作業があっても rebase を止めない
        # （2026-09-23: fund_brief.py の未コミット修正で rebase が中止され push が
        #   まるごと失敗した。研究成果がクラウドへ渡らない不具合の再発防止）
        rc, _, err = sh("git", "rebase", "-q", "--autostash", "origin/main", timeout=120)
        if rc:
            rc = resolve_mac_owned_conflicts(log)
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
