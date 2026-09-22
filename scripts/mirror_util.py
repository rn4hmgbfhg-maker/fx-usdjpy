# -*- coding: utf-8 -*-
"""Mac鏡の作業ツリー整理（GitHub一本化後の共通処理）。
GitHub Actions が唯一の書き手であるファイル（state*.json・results/・data/ の大半）が
Mac側で書き換わっていると `git pull --ff-only` が詰まる。それらは backup/ へ退避した上で
HEAD の内容に戻す。Mac が唯一の書き手であるファンダ研究の成果物（FUND_PATHS）は触らない。
"""
import os, shutil, subprocess
from datetime import datetime

FUND_PATHS = [
    "results/fundamental_latest.json",
    "results/fundamental_news.json",
    "results/fundamental_run_log.csv",
    "results/forecast_log.csv",
    "results/fund_overlay_latest.json",
    "results/fund_overlay_log.csv",
    "results/fund_filter_latest.json",
    "data/macro_daily.csv",
]
# Macの日次研究（research.py / research_intraday.py / research_15m.py）が「進化」で書く
# 戦略パラメータと研究ログ。Actions は config.json をコミットしないので Mac が唯一の書き手。
RESEARCH_PATHS = [
    "config.json",
    "results/research_log.csv",
    "results/research_intraday_log.csv",
    "results/research_15m_log.csv",
]
MAC_PATHS = FUND_PATHS + RESEARCH_PATHS
BACKUP_ROOT = "backup/mirror_discard"


def sh(*a, timeout=60):
    r = subprocess.run(a, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def dirty_tracked():
    """変更のある追跡済みファイル（未追跡 ?? は含めない）"""
    # sh() は stdout 全体を strip するため先頭行の状態欄（" M"）が欠けてパスが1文字ずれる。
    # ここだけ生の出力を使う。
    r = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                       capture_output=True, text=True, timeout=60)
    paths = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        paths.append(line[3:].strip().strip('"'))
    return paths


def discard_actions_owned(log=print):
    """Actionsが書き手のファイルのローカル変更を退避してHEADへ戻す。戻したパス一覧を返す。"""
    targets = [p for p in dirty_tracked() if p not in MAC_PATHS]
    if not targets:
        return []
    stamp = f"{datetime.now():%Y-%m-%d_%H%M%S}"
    for p in targets:
        if os.path.exists(p):
            dst = os.path.join(BACKUP_ROOT, stamp, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(p, dst)
    rc, _, err = sh("git", "checkout", "--", *targets)
    if rc:
        log(f"checkout失敗: {err[:160]}")
        return []
    log(f"Actions管理ファイル{len(targets)}件を{BACKUP_ROOT}/{stamp}へ退避しHEADへ戻した: "
        + ", ".join(targets))
    return targets
