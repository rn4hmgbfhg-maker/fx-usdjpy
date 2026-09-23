# -*- coding: utf-8 -*-
"""ファンダ研究の「運用への反映」＝全指示書共通のファンダ警戒欄（2026-09-21 新設）

反映の方針（research_fund_overlay.py の検証結果に基づく）:
  方向予測・ボラ予測・レジームを発注判定（フィルタ／数量）へ組み込んでも、
  学習外期間の成績は4システムとも改善しなかった（採用候補ゼロ）。
  よって発注ロジックは変えず、発注する本人が注文のたびに目にする
  「朝の指示書／デイトレ指示書／デイトレ15分指示書」の3つ全部へ、同じ
  ファンダ警戒欄を載せる。計算はこのモジュール一本（出力先ごとの別計算禁止）。

警戒度の決め方（定性ニュース fundamental_news.json のテーマから機械的に算出）:
  重要度5のテーマを含意別に数え、
    高 … 重要度5の【新規】テーマがある
    中 … 重要度5のテーマがある（継続のみ）
    低 … 重要度5なし
  保有玉と逆向きの重要度5材料があるシステムは名指しで注意を出す。

失敗しても指示書の生成を止めない（例外は握りつぶして空リストを返す）。
"""
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUND_JSON = os.path.join(BASE_DIR, "results", "fundamental_latest.json")
NEWS_JSON = os.path.join(BASE_DIR, "results", "fundamental_news.json")
OVERLAY_JSON = os.path.join(BASE_DIR, "results", "fund_overlay_latest.json")
STALE_HOURS = 30          # 平日3時間ごと更新。週末を除きこれを超えたら「古い」


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                   # noqa: BLE001
        return None


def _short(text, n):
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _strip_tag(title):
    """見出し先頭の【新規・重要度5】等のタグを外す。"""
    t = str(title)
    if t.startswith("【") and "】" in t:
        t = t.split("】", 1)[1]
    return t.strip()


def all_positions(override=None):
    """4システムの現在の建玉方向を state ファイルから集める。
    override={"システム名": pos} は呼び出し元エンジンの最新値で上書きする
    （エンジンは state 保存前に指示書を組み立てるため）。"""
    pos = {}
    daily = _load(os.path.join(BASE_DIR, "state.json")) or {}
    for name, st in daily.items():
        if isinstance(st, dict):
            pos[name] = int(st.get("position") or 0)
    for name, fn in (("デイトレ複合時間軸", "state_intraday.json"),
                     ("デイトレ15分", "state_intraday15.json")):
        st = (_load(os.path.join(BASE_DIR, fn)) or {}).get("state") or {}
        pos[name] = int(st.get("position") or 0)
    pos.update(override or {})
    return pos


def assess(positions=None):
    """警戒度と根拠を dict で返す。positions={"システム名": +1/-1/0}。"""
    news = _load(NEWS_JSON) or {}
    themes = [t for t in (news.get("テーマ") or [])
              if isinstance(t, dict) and int(t.get("重要度") or 0) >= 5]
    fresh = [t for t in themes if "新規" in str(t.get("見出し", ""))[:12]]
    by = {"円高": [], "円安": [], "両方向リスク": [], "中立": []}
    for t in themes:
        by.setdefault(t.get("含意") or "中立", []).append(t)
    level = "高" if fresh else "中" if themes else "低"

    # 保有玉と逆向きの重要度5材料（買い玉×円高材料／売り玉×円安材料）
    # positions 未指定時は state から自前で読む（既定値 None で警告が
    # 黙って消えるのを防ぐ。2026-09-23 修正）
    if positions is None:
        positions = all_positions()
    against = []
    for name, pos in positions.items():
        if not pos:
            continue
        key = "円高" if pos > 0 else "円安"
        hits = by.get(key, []) + by.get("両方向リスク", [])
        if hits:
            against.append((name, "買" if pos > 0 else "売", key, len(hits)))

    age_h = None
    try:
        age_h = (datetime.now() - datetime.fromisoformat(news["更新"])
                 ).total_seconds() / 3600
    except Exception:                                   # noqa: BLE001
        pass
    return {"警戒度": level, "重要度5": themes, "新規": fresh, "含意別": by,
            "逆風": against, "更新": news.get("更新"), "経過時間": age_h,
            "次の焦点": news.get("次の焦点") or []}


def lines(positions=None, max_focus=2):
    """指示書に差し込む行リスト。positions は assess() と同じ。"""
    try:
        return _lines(positions, max_focus)
    except Exception as e:                              # noqa: BLE001
        return ["", f"◆ ファンダ警戒: 生成失敗（{type(e).__name__}）"
                    "＝発注判定には影響なし"]


def _lines(positions, max_focus):
    a = assess(positions)
    fund = _load(FUND_JSON) or {}
    if not a["更新"] and not fund:
        return []
    out = ["", "◆ ファンダ警戒（発注前に一読・売買判定そのものは従来どおり）"]
    n = {k: len(v) for k, v in a["含意別"].items()}
    out.append(f"  警戒度: {a['警戒度']}（重要度5の材料 円高{n.get('円高', 0)}件"
               f"／円安{n.get('円安', 0)}件／両方向{n.get('両方向リスク', 0)}件"
               f"・ニュース更新 {a['更新'] or '不明'}）")
    if a["経過時間"] is not None and a["経過時間"] > STALE_HOURS \
            and datetime.now().weekday() < 5:
        out.append(f"  ※ニュースが{a['経過時間']:.0f}時間更新されていない"
                   "（ファンダ研究タスクの停止を疑う）")
    for t in (a["新規"] or a["重要度5"])[:2]:
        out.append(f"  ・[{t.get('含意', '—')}] "
                   f"{_short(_strip_tag(t.get('見出し', '')), 70)}")
    grp = {}
    for name, side, key, cnt in a["逆風"]:
        grp.setdefault((side, key, cnt), []).append(name)
    for (side, key, cnt), names in grp.items():
        out.append(f"  ⚠ {side}玉（{'・'.join(names)}）に逆風となりうる"
                   f"重要度5材料 {cnt}件（{key}方向）→ 逆指値の設定を再確認")
    if fund.get("レジーム"):
        out.append(f"  レジーム: {fund['レジーム']}")
    yf, pm = fund.get("翌日予測") or {}, fund.get("予測モデル") or {}
    if yf:
        prob, rng = yf.get("上昇確率"), yf.get("想定レンジ") or ["—", "—"]
        hit, base = pm.get("的中率"), pm.get("上昇日比率")
        edge = (f"検証{hit:.1%}対『常に上昇』{base:.1%}＝"
                + ("エッジなし" if hit <= base else "優位")
                if hit is not None and base is not None else "検証値なし")
        out.append(f"  翌日予測: {yf.get('方向', '—')}"
                   + (f"（上昇確率{prob:.0%}）" if prob is not None else "")
                   + f" 想定レンジ {rng[0]}〜{rng[1]}円（{edge}・表示のみ）")
    focus = a["次の焦点"]
    if isinstance(focus, str):
        focus = [focus]
    for f in focus[:max_focus]:
        out.append(f"  焦点: {_short(f, 78)}")
    ov = _load(OVERLAY_JSON)
    if ov:
        c = ov.get("採用候補") or []
        out.append(f"  発注判定への組込み検証（{str(ov.get('更新', ''))[:10]}）: "
                   + ("採用候補なし＝売買ルールは変更せず、本欄の注意喚起のみ"
                      if not c else
                      f"採用候補{len(c)}件あり（要承認・未適用）"))
    return out


if __name__ == "__main__":
    print("\n".join(lines(all_positions())))
