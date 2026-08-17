# -*- coding: utf-8 -*-
"""リスクリワード管理（2026-08-14 ユーザー指示で新設）

ボード・指示書・メールが同じ数字を出すための唯一の計算元。同じ指標を出力先
ごとに書くと片方だけ直し残る事故になるため、必ずこのモジュール経由で出す
（memory feedback_fix_all_output_targets）。

用語（このプロジェクトでの定義）:
  リスク幅   現値からストップまでの距離。実際に市場へ置いてある注文なので
             「確定した最大損失」に最も近い。スリッページと窓は考慮しない。
  リワード幅 現値から利確目標までの距離。デイトレ2系統は指値を置くので確定。
             日足2系統はドンチャン追随＝目標を置かない設計なので None を返し、
             代わりに検証実績のペイオフレシオ（平均利益÷平均損失）を使う。
  RR         リワード幅 ÷ リスク幅。1.0 未満は「損切り幅の方が広い」状態。

★ドンチャンの決済ラインは利確ではない。トレンド転換で降りる線であり、含み損
のまま到達すれば損切りとして働く。RR の分子には入れない。
"""

PIP = 0.01   # 対円ペアの1pip（USD/JPY = 0.01円）


def to_pips(width_yen):
    """円建ての値幅を pips に換算する（符号はそのまま保つ）。"""
    return None if width_yen is None else float(width_yen) / PIP


def order_widths(side, base, stop=None, tp=None, units=0, entry=None):
    """発注カードの「損切り幅／利確幅 pips」の唯一の計算元
    （2026-08-18 ユーザー指示。カード側で幅を再計算しないこと）。

    引数:
      side   "買"/"売" または +1/-1
      base   幅の起点。新規建て＝成行の想定約定値（現値）、
             既存玉の注文変更＝現値。
      stop   決済逆指値のレート（無ければ None）
      tp     決済指値(利確)のレート（無ければ None）
      units  数量（通貨）。円換算の到達時損益に使う
      entry  建玉の建値（既存玉のみ）。トレール後の逆指値は建値より有利側に
             あることがあり、その注文に当たった時は「損」ではなく確定利益に
             なる。到達時の金額は base ではなく必ず建値基準で測る。

    戻り値（幅は常に正の距離。逆行していれば 逆行 フラグが立つ）:
      損切り幅pips / 損切り幅円 / 損切り到達額円 / 損切り逆行
      利確幅pips   / 利確幅円   / 利確到達額円
      RR
    """
    sign = 1 if (side in ("買", "買(ロング)", "ロング") or
                 (isinstance(side, (int, float)) and side > 0)) else -1
    base = float(base)
    ref = float(entry) if entry else base
    units = float(units or 0)
    out = {"損切り幅pips": None, "損切り幅円": None, "損切り到達額円": None,
           "損切り逆行": False, "利確幅pips": None, "利確幅円": None,
           "利確到達額円": None, "RR": None}

    d_stop = d_tp = None
    if stop:
        d_stop = (base - float(stop)) * sign      # 正なら現値はストップの有利側
        out["損切り逆行"] = d_stop < 0
        out["損切り幅円"] = abs(d_stop)
        out["損切り幅pips"] = abs(to_pips(d_stop))
        out["損切り到達額円"] = (float(stop) - ref) * units * sign
    if tp:
        d_tp = (float(tp) - base) * sign
        out["利確幅円"] = abs(d_tp)
        out["利確幅pips"] = abs(to_pips(d_tp))
        out["利確到達額円"] = (float(tp) - ref) * units * sign
    if d_stop and d_tp and d_stop > 0 and d_tp > 0:
        out["RR"] = d_tp / d_stop
    return out


def position_rr(pos, units, entry, price, stop, tp=None, swap_yen=0.0):
    """1ポジションのリスクリワードを dict で返す。pos==0 なら None。

    戻り値のキー:
      建玉方向 / 数量 / 建値 / 現値 / 含み損益円
      リスク幅円 / リスク額円   … ストップ到達で失う額（スワップ込み）
      リワード幅円 / リワード額円 … 利確到達で得る額（tp が無ければ None）
      RR / 当初RR              … 現時点／建値時点のリスクリワード比
      ストップ超過             … True なら既にストップを追い越している異常
    """
    if not pos:
        return None
    units = float(units or 0)
    entry = float(entry or 0)
    price = float(price or 0)
    sign = 1 if pos > 0 else -1
    pnl = (price - entry) * units * sign + float(swap_yen or 0)

    risk_w = risk_y = None
    over = False
    if stop:
        stop = float(stop)
        risk_w = (price - stop) * sign      # 正なら現値はストップの有利側
        over = risk_w < 0
        # ストップ到達時の損益は建値との差で測る（含み益が乗っていれば
        # ストップ到達でも利益になりうる＝トレール後の正常な状態）。
        risk_y = (stop - entry) * units * sign + float(swap_yen or 0)

    rew_w = rew_y = None
    if tp:
        tp = float(tp)
        rew_w = (tp - price) * sign
        rew_y = (tp - entry) * units * sign + float(swap_yen or 0)

    rr = (rew_w / risk_w) if (rew_w is not None and risk_w
                              and risk_w > 0) else None
    rr0 = None
    if tp and stop and entry:
        d_up = abs(float(tp) - entry)
        d_dn = abs(entry - float(stop))
        rr0 = (d_up / d_dn) if d_dn else None

    return {"建玉方向": "買" if pos > 0 else "売", "数量": units,
            "建値": entry, "現値": price, "含み損益円": pnl,
            "リスク幅円": risk_w, "リスク額円": risk_y,
            "リワード幅円": rew_w, "リワード額円": rew_y,
            "RR": rr, "当初RR": rr0, "ストップ超過": over}


def portfolio_risk(rows, capital):
    """全システム合算のリスク。rows は position_rr() の戻り値のリスト。

    同時に全ストップへ到達する最悪ケースを見る。日足2系統が売り・デイトレが
    買いのように方向が割れている場合、値段が一方向へ動けば片側は利益になる
    ので単純合計は保守的すぎる。そこで「合計」と「ネット建玉」を併記する。
    """
    rows = [r for r in rows if r]
    tot_risk = sum(r["リスク額円"] for r in rows if r["リスク額円"] is not None)
    tot_pnl = sum(r["含み損益円"] for r in rows)
    net = sum(r["数量"] * (1 if r["建玉方向"] == "買" else -1) for r in rows)
    long_u = sum(r["数量"] for r in rows if r["建玉方向"] == "買")
    short_u = sum(r["数量"] for r in rows if r["建玉方向"] == "売")
    cap = float(capital or 0)
    return {"建玉数": len(rows), "合計リスク額円": tot_risk,
            "合計リスク率": (abs(tot_risk) / cap) if cap else None,
            "合計含み損益円": tot_pnl,
            "買建玉": long_u, "売建玉": short_u, "ネット建玉": net,
            "両建て度": (min(long_u, short_u) / max(long_u, short_u))
            if max(long_u, short_u) else 0.0}


def rr_comment(rr, pf=None, has_tp=False, over=False):
    """RR の一言評価。判断はユーザーが行うので断定しすぎない文言にする。

    2026-08-15: rr is None は「TPが無い設計」と「ストップ超過等でRR算出不能」の
    2通りあるのに、一律「利確目標を置かない設計」と出していた。利確指値を
    実際に置いているデイトレ2系統にこの文言が出て誤解を招いた（ユーザー指摘）
    ため、TPの有無(has_tp)とストップ超過(over)を明示的に受け取って分岐する。
    """
    if not has_tp:
        return ("利確目標を置かない設計（トレンド追随）。"
                f"検証PF {pf:.2f} が実質のリスクリワード"
                if pf else "利確目標を置かない設計（トレンド追随）")
    if over:
        return "ストップ到達済みの可能性。実口座の約定状況を確認"
    if rr is None:
        return "－"
    if rr >= 2.0:
        return "リスク1に対し利益2以上を狙える位置"
    if rr >= 1.0:
        return "利益余地がリスクを上回る位置"
    if rr >= 0.5:
        return "★利益余地がリスクを下回る。伸ばすか手仕舞うかの判断どころ"
    return "★★残り利益余地がリスクの半分未満。利確接近か、伸びしろが乏しい"
