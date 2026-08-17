# -*- coding: utf-8 -*-
"""cowork/*.yaml と現行実装の突合（仕様の腐り防止）

YAMLは書いた瞬間から実装とズレ始める。ズレたまま Cowork へ移行すると、
「YAMLには40/10と書いてあるが実際は70/10で動いている」という事故になる。
同じ数字が2箇所にあるなら必ず突合する（memory feedback_fix_all_output_targets）。

読み取り専用。何も書き換えない。

使い方:
    python3 cowork/verify_spec.py          # 全項目を突合
    python3 cowork/verify_spec.py --quiet  # 不一致だけ表示（CI向け）

終了コード: 0=全一致 / 1=不一致あり
"""
import argparse
import json
import os
import re
import sys

import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COWORK_DIR = os.path.join(BASE_DIR, "cowork")
SPEC = os.path.join(COWORK_DIR, "fx_usdjpy_cowork.yaml")
ROUTINES = os.path.join(COWORK_DIR, "routines.yaml")
CONFIG = os.path.join(BASE_DIR, "config.json")

# YAMLのシステムid → config.json 上の位置
SYSTEM_MAP = {
    "long_trend":   ("システム", "長期トレンド"),
    "short_swing":  ("システム", "短期スイング"),
    "intraday_2h":  ("デイトレ", None),
    "intraday_15m": ("デイトレ15分", None),
}
# YAMLのパラメータ名 → config.json のキー（デイトレ15分は「パラメータ」の下）
PARAM_KEYS = ["エントリー期間", "イグジット期間", "ATRストップ係数",
              "リスク率", "レバ上限", "利確ATR係数", "4Hフィルタ期間"]


def _cfg_system(cfg, sid):
    top, sub = SYSTEM_MAP[sid]
    node = cfg.get(top, {})
    return node.get(sub, {}) if sub else node


def _cfg_param(node, key):
    """config.json 側の値を取る。デイトレ15分は一部が「パラメータ」の下にある。"""
    if key in node:
        return node[key]
    return (node.get("パラメータ") or {}).get(key)


def check_systems(spec, cfg, issues, oks):
    for s in spec["システム"]:
        sid = s["id"]
        node = _cfg_system(cfg, sid)
        if not node:
            issues.append(f"[システム] {sid}: config.json に該当ノードが無い")
            continue
        for key in PARAM_KEYS:
            want = s["パラメータ"].get(key)
            if want is None:
                continue
            got = _cfg_param(node, key)
            if got is None:
                issues.append(f"[{s['名称']}] {key}: YAML={want} / config.json に無い")
            elif float(got) != float(want):
                issues.append(f"[{s['名称']}] {key}: YAML={want} ≠ config.json={got}")
            else:
                oks.append(f"{s['名称']}.{key}={want}")
        # 有効／試験運転フラグ
        for flag, ckey in (("有効", "有効"), ("試験運転", "試験運転")):
            want = s.get(flag)
            got = node.get(ckey)
            if got is None:
                continue
            if bool(got) != bool(want):
                issues.append(f"[{s['名称']}] {flag}: YAML={want} ≠ config.json={got}")
            else:
                oks.append(f"{s['名称']}.{flag}={want}")


def check_money(spec, cfg, issues, oks):
    pairs = [("口座資金_円", spec["資金"]["口座資金_円"], cfg.get("口座資金_円")),
             ("取引単位_通貨", spec["資金"]["取引単位_通貨"], cfg.get("取引単位_通貨"))]
    sw_spec = spec["スワップポイント"]["円_1万通貨_1日"]
    sw_cfg = (cfg.get("スワップポイント") or {}).get("円_1万通貨_1日") or {}
    pairs += [("スワップ買", sw_spec["買"], sw_cfg.get("買")),
              ("スワップ売", sw_spec["売"], sw_cfg.get("売"))]
    for name, want, got in pairs:
        if got is None:
            issues.append(f"[資金] {name}: config.json に無い（YAML={want}）")
        elif float(got) != float(want):
            issues.append(f"[資金] {name}: YAML={want} ≠ config.json={got}")
        else:
            oks.append(f"資金.{name}={want}")


def check_state(spec, issues, oks):
    """YAMLに書いた『現在の建玉』が state.json と一致するか。

    建玉は毎日動くので不一致自体は異常ではない。ただし YAML を読んで運用判断を
    する以上、古い値が残っていることは明示する必要がある。
    """
    path = os.path.join(BASE_DIR, "state.json")
    if not os.path.exists(path):
        issues.append("[状態] state.json が無い")
        return
    st = json.load(open(path, encoding="utf-8"))
    for s in spec["システム"]:
        cur = s.get("現在の建玉")
        if not cur:
            continue
        node = st.get(s["名称"])
        if not node:
            continue
        want_dir = 1 if cur["方向"] == "買" else -1
        diffs = []
        if node.get("position") != want_dir:
            diffs.append(f"方向 {cur['方向']}→{node.get('position')}")
        if float(node.get("units") or 0) != float(cur["数量"]):
            diffs.append(f"数量 {cur['数量']}→{node.get('units')}")
        if abs(float(node.get("stop") or 0) - float(cur["ストップ"])) > 0.001:
            diffs.append(f"ストップ {cur['ストップ']}→{node.get('stop')}")
        if diffs:
            issues.append(f"[状態] {s['名称']}: YAMLの建玉が古い（{' / '.join(diffs)}）"
                          f" 基準日={cur.get('基準日')}")
        else:
            oks.append(f"状態.{s['名称']} 一致")


def check_gates(spec, issues, oks):
    """研究ゲートの値が src/research.py の定数と一致するか。"""
    src = open(os.path.join(BASE_DIR, "src", "research.py"), encoding="utf-8").read()
    g = spec["ランブック"]["daily_research"]["パラメータ探索"]
    checks = [
        ("前後半分割", g["前後半分割"], re.search(r'SPLIT\s*=\s*"([^"]+)"', src)),
        ("改善比", g["共通ゲート"]["改善比"], re.search(r"IMPROVE_RATIO\s*=\s*([\d.]+)", src)),
        ("最大DD下限", g["共通ゲート"]["最大DD下限"],
         re.search(r"MAX_DD_LIMIT\s*=\s*(-?[\d.]+)", src)),
    ]
    for name, want, m in checks:
        if not m:
            issues.append(f"[研究ゲート] {name}: research.py から定数を読めない")
            continue
        got = m.group(1)
        same = (got == want) if isinstance(want, str) else (float(got) == float(want))
        (oks.append(f"研究ゲート.{name}={want}") if same else
         issues.append(f"[研究ゲート] {name}: YAML={want} ≠ research.py={got}"))

    for sysname, key in (("長期トレンド", "長期トレンド"), ("短期スイング", "短期スイング")):
        blk = re.search(r'"%s":\s*\{(.+?)\n    \}' % sysname, src, re.S)
        if not blk:
            issues.append(f"[研究ゲート] {sysname}: GRIDS を読めない")
            continue
        body = blk.group(1)
        y = g["システム別ゲート"][key]
        for label, pat in (("最小取引数", r"min_trades\":\s*(\d+)"),
                           ("前後半PF下限", r"min_pf_half\":\s*([\d.]+)")):
            m = re.search(pat, body)
            if not m:
                issues.append(f"[研究ゲート] {sysname}.{label}: 読めない")
            elif float(m.group(1)) != float(y[label]):
                issues.append(f"[研究ゲート] {sysname}.{label}: "
                              f"YAML={y[label]} ≠ research.py={m.group(1)}")
            else:
                oks.append(f"研究ゲート.{sysname}.{label}={y[label]}")


def check_events(spec, issues, oks):
    """イベントキャッシュ方針と★5パターンが src/events.py と一致するか。"""
    src = open(os.path.join(BASE_DIR, "src", "events.py"), encoding="utf-8").read()
    ev = spec["データ"]["イベントカレンダー"]
    for label, pat, want in (("フレッシュ時間", r"CACHE_FRESH_H\s*=\s*(\d+)",
                              ev["キャッシュ"]["フレッシュ時間"]),
                             ("ステール許容時間", r"CACHE_STALE_H\s*=\s*(\d+)",
                              ev["キャッシュ"]["ステール許容時間"])):
        m = re.search(pat, src)
        if not m:
            issues.append(f"[イベント] {label}: events.py から読めない")
        elif int(m.group(1)) != int(want):
            issues.append(f"[イベント] {label}: YAML={want} ≠ events.py={m.group(1)}")
        else:
            oks.append(f"イベント.{label}={want}")

    m = re.search(r"STAR5_PATTERNS\s*=\s*\[(.+?)\]", src, re.S)
    if not m:
        issues.append("[イベント] STAR5_PATTERNS を読めない")
    else:
        got = set(re.findall(r'"([^"]+)"', m.group(1)))
        want = set(ev["star5パターン"])
        if got != want:
            issues.append(f"[イベント] ★5パターン不一致: YAMLのみ={sorted(want-got)} "
                          f"/ events.pyのみ={sorted(got-want)}")
        else:
            oks.append(f"イベント.★5パターン {len(want)}件一致")


def check_workflows(spec, issues, oks):
    """YAMLに書いた cron が実ワークフローと一致するか。"""
    for wf in spec["スケジュール"]["github_actions"]:
        path = os.path.join(BASE_DIR, ".github", "workflows", wf["ワークフロー"])
        if not os.path.exists(path):
            issues.append(f"[Actions] {wf['ワークフロー']} が存在しない")
            continue
        body = open(path, encoding="utf-8").read()
        if wf["cron_utc"] not in body:
            issues.append(f"[Actions] {wf['ワークフロー']}: YAML cron='{wf['cron_utc']}' "
                          "がワークフロー本体に見当たらない")
        else:
            oks.append(f"Actions.{wf['ワークフロー']} cron一致")


def check_runbook_files(spec, issues, oks):
    """参照セクションに書いたファイルが実在するか。"""
    missing = []
    for group in spec["参照"].values():
        items = group if isinstance(group, list) else []
        if isinstance(group, dict):
            items = [v for sub in group.values() for v in sub]
        for rel in items:
            if not os.path.exists(os.path.join(BASE_DIR, rel)):
                missing.append(rel)
    if missing:
        issues.append(f"[参照] 実在しないファイル: {', '.join(missing)}")
    else:
        oks.append("参照ファイル すべて実在")


def check_board_url(spec, issues, oks):
    """ボードURLとfaviconが2つのYAMLで食い違っていないか。"""
    r = yaml.safe_load(open(ROUTINES, encoding="utf-8"))
    a = spec["通知"]["チャネル"]["board"]
    b = r["共通"]
    if a["url"] != b["ボードURL"]:
        issues.append(f"[ボード] URL不一致: spec={a['url']} / routines={b['ボードURL']}")
    elif a["favicon"] != b["ボードfavicon"]:
        issues.append("[ボード] favicon不一致")
    else:
        oks.append("ボードURL/favicon 2ファイル一致")
    # 各routineのpromptに別URLが紛れ込んでいないか
    for ro in r["routines"]:
        for u in re.findall(r"https://claude\.ai/code/artifact/[0-9a-f-]+", ro["prompt"]):
            if u != a["url"]:
                issues.append(f"[ボード] routine {ro['id']} に別URL: {u}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="不一致だけ表示")
    args = ap.parse_args()

    spec = yaml.safe_load(open(SPEC, encoding="utf-8"))
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    issues, oks = [], []

    check_systems(spec, cfg, issues, oks)
    check_money(spec, cfg, issues, oks)
    check_state(spec, issues, oks)
    check_gates(spec, issues, oks)
    check_events(spec, issues, oks)
    check_workflows(spec, issues, oks)
    check_runbook_files(spec, issues, oks)
    check_board_url(spec, issues, oks)

    print(f"=== cowork仕様の突合 v{spec['メタ']['版']} ===")
    if not args.quiet:
        for o in oks:
            print(f"  ○ {o}")
    if issues:
        print(f"\n★不一致 {len(issues)}件")
        for i in issues:
            print(f"  ✕ {i}")
        print("\nYAMLか実装のどちらかを直すこと。放置するとCowork移行後に事故になる。")
    else:
        print(f"\n○ 不一致なし（{len(oks)}項目すべて一致）")
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
