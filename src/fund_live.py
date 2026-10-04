# -*- coding: utf-8 -*-
"""ファンダ研究の本運用（発注判定への自動組込み）（2026-10-04 新設・落合さん指示）

落合さんの指示「ファンダ研究を本運用に変更を、自動的におこなってください」により、
これまで「検証して提案まで（採否はユーザー承認）」だったファンダの発注判定への
組込みを、事前に決めた機械的な条件で自動的に昇格・降格させる。

仕組み
  1) research_fund_overlay.py（平日朝6時台）が、方向予測フィルタ（A系）と
     ボラ予測の数量調整（B系）を現行4システムへ重ねた学習外成績をゲート判定する。
  2) その結果を update() が受け取り、ルールごとに「ゲート通過の連続日数」を数える。
       ・3営業日連続で通過 → 自動で本運用へ昇格（適用開始）
       ・本運用中のルールが3営業日連続で不通過 → 自動で降格（適用停止）
     1日の揺らぎで出たり入ったりしないよう、昇格・降格とも連続3日を要する。
     追加条件:
       A系（方向予測）… 予測モデルの学習外的中率が「常に上昇」を上回っていること
                         （2026-08-11 の落合さん基準。下回った日は不通過として数える）
       B系（ボラ予測）… ボラ予測の学習外順位相関がプラスであること
     C系（レジーム見送り）は後知恵で選んだ案なので自動昇格の対象にしない。
  3) 各エンジン（signal_engine／intraday_engine／intraday15_engine）は新規建ての
     直前に decide() を呼び、本運用中のルールだけを適用する。
       ・方向予測フィルタ … 予測と逆向きの新規建てを見送る（既存玉の決済・トレイルは不変）
       ・数量調整       … 新規建ての数量に倍率を掛ける（レバ上限は従来どおり）
     予測が古い（研究タスク停止など）ときは安全側＝ルール適用を止め、従来の判定に戻す。

発注は従来どおり本人が MATRIX TRADER で手動で行う（JFX は EA 禁止）。
本モジュールは指示書の中身（建てる／見送る・数量）を変えるだけで、発注はしない。

状態ファイル: results/fund_live.json（Mac の研究タスクが書き、GitHub へ push する）
"""
import json
import os
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_JSON = os.path.join(BASE_DIR, "results", "fund_live.json")
FUND_JSON = os.path.join(BASE_DIR, "results", "fundamental_latest.json")

PROMOTE_STREAK = 3        # 連続ゲート通過で昇格
DEMOTE_STREAK = 3         # 本運用中に連続不通過で降格
STALE_HOURS = 80          # 予測の更新がこれより古ければ適用停止（週末を跨いでも可）
VOL_HI, VOL_LO = 0.15, -0.15   # research_fund_overlay の B 系と同じ閾値

# ルール定義（research_fund_overlay.py の「重ね方」ラベルと1対1）
RULES = {
    "A0": {"種別": "方向", "幅": 0.00, "ラベル": "A 方向予測フィルタ（0.5±0.00）"},
    "A3": {"種別": "方向", "幅": 0.03, "ラベル": "A 方向予測フィルタ（0.5±0.03）"},
    "A6": {"種別": "方向", "幅": 0.06, "ラベル": "A 方向予測フィルタ（0.5±0.06）"},
    "B":  {"種別": "数量", "荒れ": 0.7, "凪": 1.3,
           "ラベル": "B ボラ予測で数量調整（荒れ予測×0.7／凪×1.3）"},
    "B2": {"種別": "数量", "荒れ": 0.5, "凪": 1.0,
           "ラベル": "B' 荒れ予測のときだけ数量×0.5"},
}
LABEL_TO_ID = {v["ラベル"]: k for k, v in RULES.items()}
SYSTEMS = ("長期トレンド", "短期スイング", "デイトレ複合時間軸", "デイトレ15分")


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                   # noqa: BLE001
        return default


def _short_name(rid):
    r = RULES[rid]
    if r["種別"] == "方向":
        return f"方向予測フィルタ（0.5±{r['幅']:.2f}）"
    return (f"ボラ数量調整（荒れ×{r['荒れ']}／凪×{r['凪']}）" if r["凪"] != 1.0
            else f"荒れ予測時のみ数量×{r['荒れ']}")


def empty_state():
    return {"更新": None, "方針": "連続3営業日のゲート通過で自動昇格・連続3営業日の"
                                  "不通過で自動降格（2026-10-04 落合さん指示で自動化）",
            "連続": {}, "適用中": {s: {} for s in SYSTEMS}, "履歴": []}


def load_state():
    st = _load(LIVE_JSON) or empty_state()
    st.setdefault("連続", {})
    st.setdefault("適用中", {})
    for s in SYSTEMS:
        st["適用中"].setdefault(s, {})
    st.setdefault("履歴", [])
    return st


# ------------------------------------------------------------ 研究側：昇格・降格
def update(rows, fund=None, today=None, save=True):
    """research_fund_overlay の結果行（dict のリスト）から連続日数を更新し、
    昇格・降格を行う。戻り値: 変化のリスト [{"システム","ルール","変化","理由"}]。
    同じ日に何度呼ばれても連続日数は1日分しか進めない。"""
    today = (today or date.today()).isoformat()
    fund = fund if fund is not None else (_load(FUND_JSON) or {})
    pm, vm = fund.get("予測モデル") or {}, fund.get("ボラ予測") or {}
    dir_edge = (pm.get("的中率") is not None and pm.get("上昇日比率") is not None
                and pm["的中率"] > pm["上昇日比率"])
    vol_edge = (vm.get("順位相関") or 0) > 0

    st = load_state()
    passed = {}
    for r in rows:
        rid = LABEL_TO_ID.get(r.get("重ね方"))
        if not rid or r.get("システム") not in SYSTEMS:
            continue
        ok = r.get("採用候補") == "◎"
        kind = RULES[rid]["種別"]
        why = "ゲート通過" if ok else "ゲート不通過"
        if ok and kind == "方向" and not dir_edge:
            ok, why = False, "ゲート通過だが予測的中率が『常に上昇』以下"
        if ok and kind == "数量" and not vol_edge:
            ok, why = False, "ゲート通過だがボラ予測の順位相関がプラスでない"
        passed[(r["システム"], rid)] = (ok, why, r)

    changes = []
    for (sysname, rid), (ok, why, r) in passed.items():
        key = f"{sysname}|{rid}"
        c = st["連続"].setdefault(key, {"通過": 0, "不通過": 0, "最終判定日": None})
        if c.get("最終判定日") != today:
            if ok:
                c["通過"], c["不通過"] = c["通過"] + 1, 0
            else:
                c["通過"], c["不通過"] = 0, c["不通過"] + 1
            c["最終判定日"] = today
        c["直近"] = why
        kind = RULES[rid]["種別"]
        live = st["適用中"][sysname].get(kind)
        if live and live.get("ルール") == rid and c["不通過"] >= DEMOTE_STREAK:
            st["適用中"][sysname].pop(kind, None)
            changes.append({"システム": sysname, "ルール": rid, "変化": "降格",
                            "理由": f"{DEMOTE_STREAK}営業日連続で不通過（{why}）"})
    # 昇格は種別ごとに1つだけ。候補が複数なら学習外PF（全期間）が最も高いもの
    for sysname in SYSTEMS:
        for kind in ("方向", "数量"):
            if st["適用中"][sysname].get(kind):
                continue
            cands = [(rid, r) for (s, rid), (ok, _w, r) in passed.items()
                     if s == sysname and RULES[rid]["種別"] == kind and ok
                     and st["連続"][f"{s}|{rid}"]["通過"] >= PROMOTE_STREAK]
            if not cands:
                continue
            rid, r = max(cands, key=lambda x: x[1].get("PF全") or 0)
            st["適用中"][sysname][kind] = {
                "ルール": rid, "名称": _short_name(rid), "開始": today,
                "根拠": {k: r.get(k) for k in
                         ("PF前", "PF後", "総リターン", "最大DD", "取引数")}}
            changes.append({"システム": sysname, "ルール": rid, "変化": "昇格",
                            "理由": f"{PROMOTE_STREAK}営業日連続でゲート通過"})
    stamp = datetime.now().isoformat(timespec="minutes")
    for ch in changes:
        st["履歴"].append({"日時": stamp, **ch})
    st["履歴"] = st["履歴"][-100:]
    st["更新"] = stamp
    if save:
        os.makedirs(os.path.dirname(LIVE_JSON), exist_ok=True)
        with open(LIVE_JSON, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    return changes


# ------------------------------------------------------------ エンジン側：適用
def decide(system, side, now=None):
    """新規建て直前の判定。side=+1買/-1売。
    戻り値: {"許可": bool, "倍率": float, "理由": str, "適用中": [名称…]}
    例外時・本運用ルールなし・予測が古い時は従来どおり（許可・倍率1）。"""
    out = {"許可": True, "倍率": 1.0, "理由": "", "適用中": []}
    try:
        live = (load_state()["適用中"].get(system) or {})
        if not live:
            return out
        out["適用中"] = [v.get("名称", k) for k, v in live.items()]
        fund = _load(FUND_JSON) or {}
        yf = fund.get("翌日予測") or {}
        age = (( now or datetime.now()) - datetime.fromisoformat(fund["更新"])
               ).total_seconds() / 3600
        if age > STALE_HOURS:
            out["理由"] = f"予測が{age:.0f}時間更新されていないためファンダ本運用ルールを停止中"
            return out
        notes = []
        d = live.get("方向")
        p = yf.get("上昇確率")
        if d and p is not None:
            m = RULES[d["ルール"]]["幅"]
            if side > 0 and p < 0.5 - m:
                out["許可"] = False
                notes.append(f"方向予測が下落（上昇確率{p:.0%}）のため新規買いを見送り")
            elif side < 0 and p > 0.5 + m:
                out["許可"] = False
                notes.append(f"方向予測が上昇（上昇確率{p:.0%}）のため新規売りを見送り")
        q = live.get("数量")
        v = yf.get("ボラ相対")
        if q and v is not None and out["許可"]:
            r = RULES[q["ルール"]]
            mult = r["荒れ"] if v > VOL_HI else r["凪"] if v < VOL_LO else 1.0
            if mult != 1.0:
                out["倍率"] = mult
                notes.append(f"{'荒れ' if v > VOL_HI else '凪'}予測（ボラ相対{v:+.2f}）"
                             f"のため数量×{mult}")
        out["理由"] = "／".join(notes)
    except Exception as e:                              # noqa: BLE001
        out.update({"許可": True, "倍率": 1.0,
                    "理由": f"ファンダ本運用の判定失敗（{type(e).__name__}）＝従来どおり"})
    return out


def sized_units(base_units, lev_units, mult, lot):
    """数量調整後の通貨数（レバ上限と取引単位を守る）。"""
    u = min(base_units * mult, lev_units)
    return int(max(u, lot if base_units >= lot else 0) // lot * lot)


# ------------------------------------------------------------ 表示
def status_lines():
    """指示書・ボード用の状況行（先頭の見出し行は含めない）。"""
    st = load_state()
    out = []
    any_live = False
    for s in SYSTEMS:
        live = st["適用中"].get(s) or {}
        if live:
            any_live = True
            out.append(f"  本運用中 {s}: " + "・".join(
                f"{v['名称']}（{v['開始']}〜）" for v in live.values()))
    if not any_live:
        best = sorted(((v.get("通過", 0), k) for k, v in st["連続"].items()),
                      reverse=True)
        near = [f"{k.split('|')[0]}:{_short_name(k.split('|')[1])} {n}/{PROMOTE_STREAK}日"
                for n, k in best if n > 0][:2]
        out.append("  ファンダ本運用（自動昇格）: 適用中ルールなし＝売買判定は従来どおり"
                   + (f"／昇格待ち {'、'.join(near)}" if near else
                      f"／{PROMOTE_STREAK}営業日連続でゲート通過したルールから自動適用"))
    return out


def summary():
    """ボード用の短い要約 dict。"""
    st = load_state()
    live = {s: [v["名称"] for v in (st["適用中"].get(s) or {}).values()]
            for s in SYSTEMS}
    return {"適用中": {k: v for k, v in live.items() if v},
            "更新": st.get("更新"), "履歴": st.get("履歴", [])[-5:],
            "行": status_lines()}


if __name__ == "__main__":
    print("\n".join(status_lines()))
    for s in SYSTEMS:
        for side in (1, -1):
            d = decide(s, side)
            if d["適用中"]:
                print(s, "買" if side > 0 else "売", d)
