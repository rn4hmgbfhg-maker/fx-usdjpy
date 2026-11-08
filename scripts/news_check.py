# -*- coding: utf-8 -*-
"""results/fundamental_news.json の形式点検（2026-11-09 新設）

クラウド定期タスクが Web 検索で書いたニュース JSON を push する前に通す。
ボード（dashboard_web.py）・指示書（fund_brief.py）・定量研究（research_fundamental.py）
が読むキーが揃っているか、値域が正しいかを確かめる。NG なら終了コード1（push しない）。

使い方:  python3 scripts/news_check.py [ファイル]
"""
import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, "results", "fundamental_news.json")
REQUIRED = ["更新", "収集元", "総括", "テーマ", "次の焦点", "保有ポジションへの含意",
            "スキャル指針", "ユーロドル", "スキャル指針_ユーロドル", "ユーロ円",
            "スキャル指針_ユーロ円", "ポンド円", "スキャル指針_ポンド円"]
IMPL = {"円安", "円高", "中立", "両方向リスク"}
SCALP_KEYS = ["方向バイアス", "狙い目", "避ける時間帯", "節目", "一言"]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else PATH
    errs = []
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"NG: JSON として読めません（{e}）")
        return 1
    for k in REQUIRED:
        if k not in d:
            errs.append(f"キー「{k}」がありません")
    try:
        datetime.fromisoformat(str(d.get("更新")))
    except ValueError:
        errs.append("「更新」が YYYY-MM-DDTHH:MM 形式ではありません")
    themes = d.get("テーマ")
    if not isinstance(themes, list) or not themes:
        errs.append("「テーマ」が空です")
    else:
        for i, t in enumerate(themes, 1):
            for k in ("見出し", "要旨", "含意", "重要度"):
                if k not in t:
                    errs.append(f"テーマ{i}: 「{k}」がありません")
            if t.get("含意") not in IMPL:
                errs.append(f"テーマ{i}: 含意「{t.get('含意')}」は {sorted(IMPL)} のどれかにする")
            if not isinstance(t.get("重要度"), int) or not 1 <= t["重要度"] <= 5:
                errs.append(f"テーマ{i}: 重要度は1〜5の整数にする")
    for k in [x for x in REQUIRED if x.startswith("スキャル指針")]:
        v = d.get(k)
        if isinstance(v, dict):
            for s in SCALP_KEYS:
                if s not in v:
                    errs.append(f"{k}: 「{s}」がありません")
    if errs:
        print("NG:\n  " + "\n  ".join(errs))
        return 1
    print(f"OK: テーマ{len(themes)}件・更新 {d['更新']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
