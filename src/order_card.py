# -*- coding: utf-8 -*-
"""発注カード（MATRIX TRADERへの転記用）の描画（2026-08-05・B案）

JFXのMATRIX TRADERにはブラウザ版が無く（提供はWindows/Mac/スマホの
ネイティブアプリのみ）、外部プログラムからの自動発注も規約で禁止のため、
「そのまま転記すれば発注が終わる確定値の一覧」を出すところまでを担当する。

signal_engine.process_system() が返す発注操作リスト(ops)を受け取り、
入力欄と1対1で対応する体裁に整えて返す。レートは一切ここで計算しない
（エンジンの確定値をそのまま流すことで転記ミスと二重計算を防ぐ）。
"""

import risk

# 表示順と表示幅（項目名は入力欄の並びに合わせる）
FIELD_ORDER = ["通貨ペア", "売買", "対象", "数量", "注文方法", "レート",
               "変更前", "変更後", "決済逆指値", "決済指値(利確)",
               "有効期限", "内容", "手順"]
RATE_FIELDS = {"レート", "変更前", "変更後", "決済逆指値", "決済指値(利確)"}
LABEL_W = 6

# 幅（pips）欄。2026-08-18 ユーザー指示で発注カードに常設。
# 幅そのものは risk.order_widths() だけが計算し、カード本文・通知・機械可読
# JSONは enrich() が入れた同じop辞書を見る（出力先ごとに計算を書くと片方だけ
# 直し残る＝過去の事故の再発防止。memory feedback_fix_all_output_targets）。
PIPS_ORDER = ["利確幅pips", "損切り幅pips", "現値から", "RR"]  # 並びはユーザー文言どおり
PIPS_LABEL = {"損切り幅pips": "損切り幅", "利確幅pips": "利確幅",
              "現値から": "現値から", "RR": "RR"}


def _fmt(key, val):
    if key in RATE_FIELDS:
        return f"{float(val):.3f} 円"
    if key == "数量":
        return f"{int(val):,} 通貨" if int(val) else "—"
    return str(val)


def _side(op):
    """opの向き（買=+1／売=-1）。「対象」は保有玉、「売買」は発注する側。"""
    tgt = str(op.get("対象") or "")
    if tgt:
        return 1 if "買" in tgt else -1
    s = str(op.get("売買") or "")
    if s:
        return 1 if "買" in s else -1
    return 0


def _pad(label):
    """項目名の後ろを桁揃えする。全角・半角混在（RR等）でも列が揃うよう、
    全角=2・半角=1で数えて半角スペースで埋める。"""
    w = sum(2 if ord(c) > 0x2000 else 1 for c in str(label))
    return " " * max(1, LABEL_W * 2 - w)


def _yen(amount, units):
    """その注文に当たったとき確定する金額。数量の無いopには付けない。"""
    return f"／{amount:+,.0f}円" if units and amount is not None else ""


def enrich(ops, mark=None, entry=None, stop=None, tp=None):
    """発注操作に「損切り幅・利確幅（pips）」を必ず持たせる。

    mark : 想定約定価格（新規建ての基準。通常は判定に使った終値）
    entry: 保有玉の建値（ストップ変更・利確設定の基準）
    stop / tp: opに載っていないときに使う現在の逆指値・利確（保有玉の値）

    基準価格を「新規＝想定約定／保有玉＝建値」に分けているのは、前者は
    これから取るリスク幅、後者は到達したときに確定する損益幅であり、
    転記者が知りたい数字が違うため。保有玉には別途「現値からあと何pipsで
    その注文に当たるか」も併記する（転記直後に効くのはこちらの距離）。

    幅は risk.order_widths() だけが計算する。ここで *100 などを書き直さない
    こと（同じ数字を2箇所で計算して片方だけ直し残った過去の事故の再発防止＝
    memory feedback_fix_all_output_targets）。表示用の文字列とは別に、下流が
    数値として読めるよう 〜pips_数値 も必ず入れる。
    """
    for op in ops:
        op.pop("ストップ幅pips", None)      # 旧表記は損切り幅pipsへ一本化
        kind = str(op.get("種別") or "")
        if kind in ("決済", "要確認"):
            why = "—（決済のため設定なし）" if kind == "決済" \
                else "—（約定確認のみ。新たな注文は出さない）"
            op["損切り幅pips"] = why
            op["利確幅pips"] = why
            op["損切り幅pips_数値"] = None
            op["利確幅pips_数値"] = None
            continue

        new_entry = (kind == "新規建て")
        ref = mark if new_entry else (entry if entry is not None else mark)
        ref_label = "想定約定" if new_entry else \
            ("建値" if entry is not None else "現値")
        pos = _side(op)

        sp = op.get("決済逆指値", op.get("変更後"))
        sp = float(sp) if sp is not None else \
            (float(stop) if stop is not None else None)
        tpx = op.get("決済指値(利確)")
        tpx = float(tpx) if tpx is not None else \
            (float(tp) if tp is not None else None)

        w = (risk.order_widths(pos or 1, ref, stop=sp, tp=tpx,
                               units=op.get("数量") or 0)
             if ref is not None else {})
        units = int(float(op.get("数量") or 0))

        d = w.get("損切り幅pips")
        if d is None:
            op["損切り幅pips"] = "—（逆指値の設定なし）"
            op["損切り幅pips_数値"] = None
        else:
            head = f"{ref_label}{float(ref):.3f} → 逆指値{sp:.3f}"
            if new_entry or not pos:
                tail = head
            elif w["損切り逆行"] or not round(d):
                # 建値より有利側へトレール済み＝到達しても損ではなく利益確定
                tail = f"{head}／到達しても +{d:.0f} pips の利益確保"
            else:
                tail = f"{head}／到達なら -{d:.0f} pips の損失"
            op["損切り幅pips"] = (f"約{d:.0f} pips"
                               f"{_yen(w['損切り到達額円'], units)}（{tail}）")
            op["損切り幅pips_数値"] = round(d, 1)

        d = w.get("利確幅pips")
        if d is None:
            op["利確幅pips"] = "—（利確目標を置かない設計）"
            op["利確幅pips_数値"] = None
        else:
            op["利確幅pips"] = (f"約{d:.0f} pips"
                              f"{_yen(w['利確到達額円'], units)}"
                              f"（{ref_label}{float(ref):.3f} → 利確{tpx:.3f}）")
            op["利確幅pips_数値"] = round(d, 1)

        if w.get("RR") is not None:
            op["RR"] = f"{w['RR']:.2f}（利確幅 ÷ 損切り幅）"

        # 保有玉は「いま値段からあと何pips」を併記（基準が建値のときのみ有効）
        if not new_entry and mark is not None and entry is not None:
            now = risk.order_widths(pos or 1, mark, stop=sp, tp=tpx)
            bits = []
            if now["損切り幅pips"] is not None:
                bits.append(f"逆指値まで {now['損切り幅pips']:.0f} pips"
                            + ("（★既に超過）" if now["損切り逆行"] else ""))
                op["現値から逆指値pips_数値"] = round(now["損切り幅pips"], 1)
            if now["利確幅pips"] is not None:
                bits.append(f"利確まで {now['利確幅pips']:.0f} pips")
                op["現値から利確pips_数値"] = round(now["利確幅pips"], 1)
            if bits:
                op["現値から"] = f"{float(mark):.3f}円 → " + "／".join(bits)
    return ops


def pips_tail(op):
    """プッシュ通知の1行要旨に足す「（損切りNpips／利確Npips）」。

    enrich() が入れた数値をそのまま読むだけで再計算はしない。カードと通知で
    数字が食い違わないようにするための共通部品。
    """
    bits = []
    d = op.get("損切り幅pips_数値")
    if d is not None:
        near = op.get("現値から逆指値pips_数値")
        bits.append(f"損切り{d:.0f}pips"
                    + (f"・現値から{near:.0f}pips" if near is not None else ""))
    d = op.get("利確幅pips_数値")
    if d is not None:
        bits.append(f"利確{d:.0f}pips")
    return f"（{'／'.join(bits)}）" if bits else ""


def hold_pips(pos, price, stop=None, tp=None):
    """保有継続（発注作業なし）の日に指示書へ出す「現値からの距離」。

    発注カードが空でも、いま置いてある注文まであと何pipsかは毎回見えている
    べきなので、指示書の【継続】行に添える。計算元は同じ risk.order_widths()。
    """
    if not pos or price is None or (stop is None and tp is None):
        return ""
    w = risk.order_widths(pos, price, stop=stop, tp=tp)
    bits = []
    if w["損切り幅pips"] is not None:
        bits.append(f"逆指値まで {w['損切り幅pips']:.0f} pips"
                    + ("（★既に超過）" if w["損切り逆行"] else ""))
    if w["利確幅pips"] is not None:
        bits.append(f"利確まで {w['利確幅pips']:.0f} pips")
    return f"　現値{float(price):.3f}円 → " + "／".join(bits) if bits else ""


def render(ops_by_system, final=True):
    """ops_by_system: {システム名: [op, ...]} → 発注カード文字列"""
    flat = [(name, op) for name, ops in ops_by_system.items() for op in ops]
    todo = [x for x in flat if x[1].get("種別") != "要確認"]
    check = [x for x in flat if x[1].get("種別") == "要確認"]

    out = ["=" * 46, "◆ 発注カード（MATRIX TRADERへそのまま転記）"]
    if not flat:
        # デイトレ2系統も同じカードを使うため「両システム」とは書かない
        out += ["  本日の発注作業: なし（新規・注文変更ともに不要）", "=" * 46]
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
            out.append(f"  {key}{_pad(key)}: {_fmt(key, op[key])}{mark}")
        # 幅（pips）は転記後の答え合わせに使うので、罫線で区切って必ず出す
        if any(k in op for k in PIPS_ORDER):
            out.append("  ──── 幅（pips）────")
            for key in PIPS_ORDER:
                if key not in op:
                    continue
                label = PIPS_LABEL[key]
                out.append(f"  {label}{_pad(label)}: {op[key]}")
        out.append("")

    out += ["※値はエンジンの確定値。転記後、送信は必ずご本人が行うこと。"]
    if not final:
        out.append("※速報段階のため暫定。発注は【最終確定】のカードで行うこと。")
    out.append("=" * 46)
    return "\n".join(out)
