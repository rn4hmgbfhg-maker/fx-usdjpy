# -*- coding: utf-8 -*-
"""ユーロドル 日足シグナルエンジン（2026-10-12 2通貨運用の開始・落合さん承認）

ドル円の signal_engine.py と同じ考え方（判定と注文指示書・発注カードの作成まで。発注は本人が
MATRIX TRADER で手動）。違いは次のとおり:
  ・戦略は config.json「ユーロドル」→「システム」で定義（研究 research_eurusd.py の結果）
      押し目戻り … 200日線の向きに沿ってボリンジャー10日±1.5σの行き過ぎを逆張り（リスク1%）
      トレンド   … 移動平均クロス10/50（リスク0.5%）
    どちらもATRトレイル逆指値を常に置く（ドル円と同じシャンデリア方式）。
  ・価格は小数5桁、1pip=0.0001ドル。損益はドル建てなので、その時点のドル円終値で円換算する。
  ・数量（ユーロ）＝ 口座資金(円)×リスク率 ÷（逆指値までの幅(ドル)×ドル円）。
  ・指標見送りは USD と EUR の★5級（ECB政策金利・会見・ユーロ圏CPI速報を含む）。
  ・状態 state_eurusd.json／決済 results/trades_eurusd.csv／指示書 {日付}_ユーロドル指示_{段階}.txt
    （ドル円の「_指示_」とファイル名が重ならないようにしている＝ボード・メールの既存処理を壊さない）

使い方:
  python3 src/eurusd_engine.py --stage 1|2|3 [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import atr  # noqa: E402
from strategies import make_signal  # noqa: E402
from events import upcoming_events  # noqa: E402
import data_fetch_eurusd as dfe  # noqa: E402
import order_card  # noqa: E402
import swap  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731
CONFIG_PATH = R("config.json")
STATE_PATH = R("state_eurusd.json")
ORDERS_DIR = R("results", "orders")
TRADES_CSV = R("results", "trades_eurusd.csv")
DAILY_CSV = R("results", "daily_log_eurusd.csv")
PIP = dfe.PIP
COST_PER_SIDE = 0.00006          # 片道0.6pips（研究と同じ）
PAIR = "EUR/USD"
TAG = {1: "速報1", 2: "速報2", 3: "確定"}
LABEL = {1: "速報①", 2: "速報②", 3: "最終確定"}
FLAT = {"position": 0, "entry": None, "stop": None, "units": 0, "best": None,
        "entry_date": None, "jpy_at_entry": None}
TRADES_HEADER = ["システム", "建玉日", "決済日", "売買", "建値", "決済値", "数量",
                 "価格損益円", "スワップ円", "スワップ日数", "損益円", "決済理由", "円換算レート"]


def order_path(stage, d=None):
    d = d or date.today()
    return os.path.join(ORDERS_DIR, f"{d.isoformat()}_ユーロドル指示_{TAG[stage]}.txt")


def card_path(stage, d=None):
    d = d or date.today()
    return os.path.join(ORDERS_DIR, f"{d.isoformat()}_ユーロドル発注_{TAG[stage]}.json")


def _load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def usdjpy_now():
    """円換算に使うドル円（最新の日足終値）。"""
    df = pd.read_csv(R("data", "usdjpy_daily.csv"))
    return float(df["Close"].iloc[-1])


def swap_cfg(cfg):
    sw = dict(swap.DEFAULT)
    sw.update((cfg.get("ユーロドル") or {}).get("スワップポイント") or {})
    sw["基準"] = {}            # ドル円用の自動補正（米2年金利×ドル円）は使わない
    sw["自動補正"] = False
    return sw


def append_trade(system, st, exit_px, exit_date, reason, jpy, sw):
    pos, units, entry = int(st["position"]), float(st["units"]), float(st["entry"])
    price_pnl = (units * (exit_px - entry) * pos - units * COST_PER_SIDE * 2) * jpy
    sw_yen, sw_days = swap.accrued(pos, units, st.get("entry_date"),
                                   swap.parse_dt(exit_date), sw=sw)
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write(",".join(TRADES_HEADER) + "\n")
        f.write(",".join(str(v) for v in [
            system, st.get("entry_date", ""), exit_date, "買" if pos > 0 else "売",
            f"{entry:.5f}", f"{exit_px:.5f}", int(units), f"{price_pnl:.0f}",
            f"{sw_yen:.0f}", sw_days, f"{price_pnl + sw_yen:.0f}", reason, f"{jpy:.3f}"]) + "\n")


def exit_line_text(scfg, df, pos):
    """決済ラインの説明（ドンチャン以外の型の手仕舞い条件を言い切る）。"""
    kind = scfg.get("戦略")
    if not pos:
        return ""
    if kind == "BB逆張り":
        ma = float(df["Close"].rolling(int(scfg["BB期間"])).mean().iloc[-1])
        return (f"  決済ライン: {int(scfg['BB期間'])}日移動平均 {ma:.5f}ドル（終値でここへ戻ったら"
                f"翌朝の指示書で決済＝押し目・戻りの目標）")
    if kind == "MAクロス":
        f = float(df["Close"].rolling(int(scfg["短期MA"])).mean().iloc[-1])
        s = float(df["Close"].rolling(int(scfg["長期MA"])).mean().iloc[-1])
        return (f"  決済ライン: {scfg['短期MA']}日線 {f:.5f} と {scfg['長期MA']}日線 {s:.5f} の"
                f"逆クロス（終値で交差したら翌朝の指示書で反対売買）")
    return ""


def describe(scfg):
    k = scfg.get("戦略")
    if k == "BB逆張り":
        return (f"押し目買い・戻り売り（BB{scfg['BB期間']}日±{scfg['BB係数']}σ・"
                f"{scfg.get('トレンド期間', 200)}日線の向き）・ATR×{scfg['ATRストップ係数']}")
    if k == "MAクロス":
        return f"移動平均クロス{scfg['短期MA']}/{scfg['長期MA']}・ATR×{scfg['ATRストップ係数']}"
    return f"ドンチャン{scfg.get('エントリー期間')}/{scfg.get('イグジット期間')}・ATR×{scfg['ATRストップ係数']}"


def _fund_decide(system, side):
    """ファンダ本運用（fund_live）はドル円で検証したルールのため、ユーロドルには使わない。"""
    return {"許可": True, "倍率": 1.0, "理由": ""}


def process_system(name, scfg, df, st, cfg, final, gate_new, jpy, sw):
    ops = []
    last = df.iloc[-1]
    entry0 = st.get("entry")
    a = float(atr(df).iloc[-1])
    close = float(last["Close"])
    signal = int(make_signal(df, scfg).iloc[-1])
    atr_k = float(scfg["ATRストップ係数"])
    lot = int(cfg["ユーロドル"].get("取引単位_通貨", 1000))
    equity = float(cfg["口座資金_円"])
    pos = int(st.get("position", 0))
    lines = [f"▼ ユーロドル {name}（{describe(scfg)}・リスク{float(scfg['リスク率']):.1%}）"]
    action = "待機"

    if pos != 0 and st.get("stop") is not None:
        sv = float(st["stop"])
        hit = (pos > 0 and float(last["Low"]) <= sv) or (pos < 0 and float(last["High"]) >= sv)
        if hit:
            gap = (pos > 0 and float(last["Open"]) <= sv) or (pos < 0 and float(last["Open"]) >= sv)
            exit_px = float(last["Open"]) if gap else sv
            pnl = float(st["units"]) * (exit_px - float(st["entry"])) * pos * jpy
            lines.append(f"【確認】前バーでストップ({sv:.5f}ドル)到達 → 逆指値で決済済みのはず。"
                         f"モデル損益 {pnl:+,.0f}円。未決済なら手動決済すること。")
            ops.append({"種別": "要確認", "通貨ペア": PAIR,
                        "内容": f"逆指値{sv:.5f}ドルが約定済みかポジション一覧で確認。残っていれば成行で決済",
                        "数量": int(float(st.get("units") or 0))})
            if final:
                append_trade(f"ユーロドル{name}", st, exit_px, str(last["Date"]), "stop", jpy, sw)
            st, pos, action = dict(FLAT), 0, "ストップ決済確認"

    stop_dist = atr_k * a
    if signal != pos:
        if pos != 0:
            lines.append(f"【決済】{name}の{'買' if pos > 0 else '売'}ポジションを全決済"
                         f"（先に決済逆指値を取消→成行）")
            ops.append({"種別": "決済", "通貨ペア": PAIR, "売買": "買" if pos < 0 else "売",
                        "数量": int(float(st.get("units") or 0)), "注文方法": "成行（決済）",
                        "有効期限": "—", "手順": "先に既存の決済逆指値を取消 → 成行で全決済"})
            action = "決済"
            if final:
                append_trade(f"ユーロドル{name}", st, close, str(last["Date"]), "signal", jpy, sw)
            st = dict(FLAT)
        if signal != 0:
            if gate_new:
                lines.append(f"【見送り】新規{'買' if signal > 0 else '売'}シグナルありだが、"
                             f"最重要(★5級)指標イベント通過待ちのため本日は建てない（明朝再判定）")
                action = action if action == "決済" else "イベント見送り"
            else:
                side = "買(ロング)" if signal > 0 else "売(ショート)"
                risk_units = equity * float(scfg["リスク率"]) / (stop_dist * jpy)
                lev_units = equity * float(scfg["レバ上限"]) / (close * jpy)
                units = int(min(risk_units, lev_units) // lot * lot)
                stop_px = close - signal * stop_dist
                lines += [f"【新規】{PAIR} {side}  {units:,}通貨（{units // lot}Lot）",
                          f"  発注: IF-DONE（新規=成行／決済=逆指値 {stop_px:.5f}ドル）を1回の送信で同時発注",
                          f"  ストップ幅: {stop_dist:.5f}ドル ≒{stop_dist / PIP:.0f}pips（参考値。入力は上の逆指値レートで行う）",
                          f"  想定リスク: 約{units * stop_dist * jpy:,.0f}円（資金の{units * stop_dist * jpy / equity:.1%}・"
                          f"ドル円{jpy:.2f}円で換算）"]
                ops.append({"種別": "新規建て", "通貨ペア": PAIR, "売買": "買" if signal > 0 else "売",
                            "数量": units, "注文方法": "IF-DONE（新規=成行／決済=逆指値）",
                            "決済逆指値": round(stop_px, 5), "有効期限": "無期限（逆指値）",
                            "手順": "通貨ペアをEUR/USDに切替→新規注文→IF-DONEタブ。IF=成行、DONE=逆指値にレート入力→確認画面で照合→送信は1回"})
                action = f"新規{side}"
                st = {"position": signal, "entry": close, "stop": round(stop_px, 5), "units": units,
                      "best": close, "entry_date": str(last["Date"]), "jpy_at_entry": round(jpy, 3)}
    elif pos != 0:
        best = float(st.get("best") or st.get("entry") or close)
        best = max(best, close) if pos > 0 else min(best, close)
        new_stop = best - pos * stop_dist
        cur_stop = float(st.get("stop") or new_stop)
        new_stop = max(cur_stop, new_stop) if pos > 0 else min(cur_stop, new_stop)
        if abs(new_stop - cur_stop) >= 0.0001:          # 1pip 以上動いたら変更指示
            lines.append(f"【変更】決済逆指値を {cur_stop:.5f} → {new_stop:.5f}ドルへ変更（トレイリング）")
            ops.append({"種別": "決済逆指値の変更", "通貨ペア": PAIR,
                        "対象": f"{'買' if pos > 0 else '売'}ポジション",
                        "数量": int(float(st.get("units") or 0)),
                        "変更前": round(cur_stop, 5), "変更後": round(new_stop, 5),
                        "注文方法": "逆指値（決済）", "有効期限": "無期限",
                        "手順": "注文一覧から EUR/USD の対象の決済逆指値を選び、レートのみ変更"})
            action = "ストップ変更"
        else:
            lines.append("【継続】ポジション保有継続。注文変更なし。"
                         + order_card.hold_pips(pos, close, cur_stop, pip=PIP))
            action = "継続"
        st["stop"] = round(new_stop, 5)
        st["best"] = round(best, 5)
        el = exit_line_text(scfg, df, pos)
        if el:
            lines.append(el)
    else:
        lines.append("【待機】新規シグナルなし。発注不要。")
    order_card.enrich(ops, mark=close, entry=entry0, pip=PIP, jpy=jpy)
    return lines, action, st, signal, ops


def run(stage=3, dry_run=False):
    final = stage == 3
    cfg = _load(CONFIG_PATH, None)
    ecfg = (cfg or {}).get("ユーロドル") or {}
    if not ecfg.get("有効"):
        print("ユーロドルは無効（config.json）")
        return None
    systems = {k: v for k, v in ecfg.get("システム", {}).items() if v.get("有効", True)}
    state = _load(STATE_PATH, {})
    for n in systems:
        state.setdefault(n, dict(FLAT))
    sw = swap_cfg(cfg)

    try:
        dfe.update_all()
    except Exception as e:                                       # noqa: BLE001
        print(f"ユーロドル データ更新失敗（保存済みデータで判定）: {e}")
    df = dfe.load_daily()
    jpy = usdjpy_now()
    last = df.iloc[-1]
    close = float(last["Close"])
    a = float(atr(df).iloc[-1])
    ev_cfg = cfg.get("指標フィルタ", {})
    events = upcoming_events(24, currencies=("USD", "EUR")) if ev_cfg.get("有効", True) else []
    gate_new = bool(events) and ev_cfg.get("高重要度イベント時は新規見送り", True)

    lines = [f"■ ユーロドル 注文指示書【{LABEL[stage]}】 {date.today().isoformat()}",
             f"  終値: {close:.5f}ドル  ATR(14): {a / PIP:.1f}pips  円換算: ドル円 {jpy:.3f}円",
             "-" * 46]
    actions, signals, ops_by = {}, {}, {}
    for name, scfg in systems.items():
        sl, act, state[name], sig, ops = process_system(name, scfg, df, state[name], cfg,
                                                       final, gate_new, jpy, sw)
        lines += sl + [""]
        actions[name], signals[name], ops_by[f"ユーロドル {name}"] = act, sig, ops

    holds = [(n, s) for n, s in state.items() if int(s.get("position", 0) or 0) and s.get("entry")]
    if holds:
        lines.append("◆ 現在の建玉損益（終値評価・概算／スワップ込み・円換算）")
        tot = 0.0
        for n, s in holds:
            p, u = int(s["position"]), float(s.get("units") or 0)
            ppl = u * (close - float(s["entry"])) * p * jpy
            sy, sd = swap.accrued(p, u, s.get("entry_date"), sw=sw)
            tot += ppl + sy
            lines.append(f"  {n}: {ppl + sy:+,.0f}円（値動き {ppl:+,.0f}円／スワップ {sy:+,.0f}円・{sd}日分）")
        lines.append(f"  合計 {tot:+,.0f}円")
        lines.append(f"  ※スワップは{sw.get('出典', '')}")
        lines.append("")

    lines.append("◆ 指標イベント（今後24時間・USD/EURの★5級のみ警戒）")
    if events is None:
        lines.append("  カレンダー取得失敗。FOMC・ECB・雇用統計・CPI等の有無を手動確認推奨（本日は新規見送りフィルタ無効）")
    elif events:
        for jst, cur, title, jp in events:
            lines.append(f"  ・{jst:%m/%d %H:%M} [{cur}] {title}" + (f"（{jp}）" if jp else ""))
        if gate_new:
            lines.append("  → 新規建ては見送り。既存玉の決済・トレイルは通常どおり。")
    else:
        lines.append("  なし")
    lines += ["-" * 46, "※発注はMATRIX TRADERで本人が手動で行うこと(JFXはEA等の自動発注禁止)。",
              "※逆指値は建玉と必ずセットで維持すること。通貨ペアの選び間違い（USD/JPY と EUR/USD）に注意。"]
    if not final:
        lines.append("※本書は速報(参考)。発注判断は8時配信の【最終確定】に従うこと。")
    lines += ["", order_card.render(ops_by, final=final, dec=5, unit="ドル")]
    text = "\n".join(lines)

    if not dry_run:
        os.makedirs(ORDERS_DIR, exist_ok=True)
        with open(order_path(stage), "w", encoding="utf-8") as f:
            f.write(text + "\n")
        _save(card_path(stage), {"日付": date.today().isoformat(), "段階": LABEL[stage],
                                 "通貨ペア": PAIR, "終値": round(close, 5), "ATRpips": round(a / PIP, 1),
                                 "ドル円": round(jpy, 3), "操作": ops_by})
        if final:
            _save(STATE_PATH, state)
            new = not os.path.exists(DAILY_CSV)
            with open(DAILY_CSV, "a", encoding="utf-8") as f:
                if new:
                    f.write("日付,システム,終値,シグナル,アクション,ポジション,ストップ,数量,ドル円\n")
                for n in systems:
                    s = state[n]
                    p = int(s.get("position", 0))
                    f.write(f"{date.today().isoformat()},{n},{close:.5f},"
                            f"{'買' if signals[n] > 0 else '売' if signals[n] < 0 else '無'},{actions[n]},"
                            f"{'買' if p > 0 else '売' if p < 0 else 'なし'},{s.get('stop') or ''},"
                            f"{int(s.get('units', 0))},{jpy:.3f}\n")
    print(text)
    return {"actions": actions, "ops": ops_by, "text": text}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", type=int, choices=[1, 2, 3], default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.stage, a.dry_run)
