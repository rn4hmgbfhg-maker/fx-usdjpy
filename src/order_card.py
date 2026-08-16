# -*- coding: utf-8 -*-
"""発注カード（MATRIX TRADERへの転記用）の描画（2026-08-05・B案）

JFXのMATRIX TRADERにはブラウザ版が無く（提供はWindows/Mac/スマホの
ネイティブアプリのみ）、外部プログラムからの自動発注も規約で禁止のため、
「そのまま転記すれば発注が終わる確定値の一覧」を出すところまでを担当する。

signal_engine.process_system() が返す発注操作リスト(ops)を受け取り、
入力欄と1対1で対応する体裁に整えて返す。値は一切ここで計算しない
（エンジンの確定値をそのまま流すことで転記ミスと二重計算を防ぐ）。
"""

# 表示順と表示幅（項目名は入力欄の並びに合わせる）
FIELD_ORDER = ["通貨ペア", "売買", "対象", "数量", "注文方法", "レート",
               "変更前", "変更後", "決済逆指値", "決済指値(利確)",
               "ストップ幅pips", "有効期限", "内容", "手順"]
RATE_FIELDS = {"レート", "変更前", "変更後", "決済逆指値", "決済指値(利確)"}
LABEL_W = 6


def _fmt(key, val):
    if key in RATE_FIELDS:
        return f"{float(val):.3f} 円"
    if key == "数量":
        return f"{int(val):,} 通貨" if int(val) else "—"
    if key == "ストップ幅pips":
        return f"約{int(val)} pips"
    return str(val)


def render(ops_by_system, final=True):
    """ops_by_system: {システム名: [op, ...]} → 発注カード文字列"""
    flat = [(name, op) for name, ops in ops_by_system.items() for op in ops]
    todo = [x for x in flat if x[1].get("種別") != "要確認"]
    check = [x for x in flat if x[1].get("種別") == "要確認"]

    out = ["=" * 46, "◆ 発注カード（MATRIX TRADERへそのまま転記）"]
    if not flat:
        out += ["  本日の発注作業: なし（両システムとも注文変更不要）", "=" * 46]
        return "\n".join(out)

    out.append(f"  本日の発注作業: {len(todo)}件"
               + (f"／要確認 {len(check)}件" if check else ""))
    out.append("")

    for i, (name, op) in enumerate(todo + check, 1):
        out.append(f"［{i}/{len(flat)}］{name} ─ {op.get('種別', '')}")
        for key in FIELD_ORDER:
            if key not in op:
                continue
            mark = ("   ← この値を入力"
                    if key in ("変更後", "決済逆指値", "決済指値(利確)") else "")
            pad = "　" * max(0, LABEL_W - len(key))   # 全角スペースで桁揃え
            out.append(f"  {key}{pad}: {_fmt(key, op[key])}{mark}")
        out.append("")

    out += ["※値はエンジンの確定値。転記後、送信は必ずご本人が行うこと。"]
    if not final:
        out.append("※速報段階のため暫定。発注は【最終確定】のカードで行うこと。")
    out.append("=" * 46)
    return "\n".join(out)
