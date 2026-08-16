# -*- coding: utf-8 -*-
"""MATRIX TRADER 半自動運用シグナルエンジン（2システム構成）

JFX(MATRIX TRADER)はEA・外部プログラムによる自動発注が規約で禁止のため、
本エンジンは「判定と注文指示書の作成」までを自動化し、発注は本人が
MATRIX TRADER上で手動(成行+決済逆指値)で行う設計とする。

シグナル系統(config.jsonの「システム」で定義・研究モジュールが自動進化):
  ・長期トレンド … ドンチャン40/10型。大トレンドを数週間〜数か月保有
  ・短期スイング … ドンチャン15/5型。数日保有で回転を上げる

指標イベント勘案(config「指標フィルタ」):
  Forex Factory公開フィードから今後24時間の最重要(★5級)USD/JPYイベントを取得し、
  該当日はどのシステムも新規建てを見送る(既存玉の決済・トレイルは通常どおり)。
  取得失敗時はフィルタせず「手動確認推奨」を指示書に明記する。

毎営業日の朝に3段階で実行する(スケジュールタスクが自動実行):
  6時台: --stage 1 【速報①】 参考配信。モデル状態は更新しない
  7時台: --stage 2 【速報②】 参考配信。モデル状態は更新しない
  8時台: --stage 3 【最終確定】 正式な注文指示。モデル状態を更新する

使い方:
  python3 src/signal_engine.py            # 最終確定として実行(既定)
  python3 src/signal_engine.py --stage 1  # 速報①(状態を更新しない)
  python3 src/signal_engine.py --dry-run  # 通知なし(内容確認のみ)
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import atr  # noqa: E402
from data_fetch import fetch_usdjpy_daily, CSV_PATH  # noqa: E402
from strategies import donchian  # noqa: E402
from events import upcoming_events  # noqa: E402
import order_card  # noqa: E402
import swap  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
ORDERS_DIR = os.path.join(BASE_DIR, "results", "orders")
TRADES_CSV = os.path.join(BASE_DIR, "results", "trades.csv")
DAILY_CSV = os.path.join(BASE_DIR, "results", "daily_log.csv")
COST_PER_SIDE = 0.004   # 円/USD 片道(スプレッド+滑り、バックテストと同一)

FLAT = {"position": 0, "entry": None, "stop": None, "units": 0,
        "best": None, "entry_date": None}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def notify(title, message):
    # GitHub Actions等のLinuxではosascriptが無くFileNotFoundErrorで落ちる。
    # macOS以外では通知をスキップする（判定・メールには影響しない）。
    if sys.platform != "darwin":
        return
    subprocess.run([
        "osascript", "-e",
        f'display notification "{message}" with title "{title}" sound name "Glass"',
    ], check=False)


def append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(",".join(header) + "\n")
        f.write(",".join(str(v) for v in row) + "\n")


TRADES_HEADER = ["システム", "建玉日", "決済日", "売買", "建値", "決済値", "数量",
                 "価格損益円", "スワップ円", "スワップ日数", "損益円", "決済理由"]


def append_trade(system, st, exit_px, exit_date, reason):
    pos = int(st["position"])
    units = float(st["units"])
    entry = float(st["entry"])
    price_pnl = units * (exit_px - entry) * pos - units * COST_PER_SIDE * 2
    sw_yen, sw_days = swap.accrued(pos, units, st.get("entry_date"),
                                   swap.parse_dt(exit_date))
    swap.ensure_columns(TRADES_CSV, TRADES_HEADER)
    append_csv(TRADES_CSV, TRADES_HEADER,
               [system, st.get("entry_date", ""), exit_date,
                "買" if pos > 0 else "売",
                f"{entry:.3f}", f"{exit_px:.3f}", int(units),
                f"{price_pnl:.0f}", f"{sw_yen:.0f}", sw_days,
                f"{price_pnl + sw_yen:.0f}", reason])


def realized_total():
    """日足2システムのみの累計実現損益（daily_log.csv 用）。

    ここは意図的に trades.csv だけを見る。daily_log.csv の「実現損益累計/
    モデル資産」は日足2システムの記録として一貫させ、デイトレ2系統を含む
    4システム合算はボード側（perf.load_all）で足し込む設計にしている。
    ここを4システム合算にすると資産カーブが二重計上になる（2026-08-14）。
    """
    if not os.path.exists(TRADES_CSV):
        return 0.0
    df = pd.read_csv(TRADES_CSV)
    return float(df["損益円"].sum()) if len(df) else 0.0


def load_state(systems):
    """state.jsonを読み、旧形式(単一システム)なら新形式へ移行する。"""
    st = load_json(STATE_PATH, {})
    if "position" in st:                      # 旧形式
        st = {"長期トレンド": st}
    for name in systems:
        st.setdefault(name, dict(FLAT))
    return st


def make_signal(scfg, df):
    if scfg.get("戦略") == "ドンチャン":
        return donchian(df, n=int(scfg["エントリー期間"]),
                        exit_n=int(scfg["イグジット期間"]))
    raise ValueError(f"未知の戦略: {scfg.get('戦略')}")


def process_system(name, scfg, df, st, cfg, final, gate_new):
    """1システム分の判定を行う。

    返り値: (指示行リスト, アクション, 更新後state, シグナル, 発注操作リスト)
    発注操作リストは MATRIX TRADER の入力欄へそのまま転記できる確定値の集合で、
    order_card.py が発注カードの描画に使う（1システムで決済+新規の2操作が
    同時に出ることがあるためリストで返す）。
    """
    ops = []
    last = df.iloc[-1]
    a = float(atr(df).iloc[-1])
    close = float(last["Close"])
    signal = int(make_signal(scfg, df).iloc[-1])
    atr_k = float(scfg["ATRストップ係数"])
    lot = int(cfg["取引単位_通貨"])
    equity = float(cfg["口座資金_円"])
    pos = int(st.get("position", 0))

    lines = [f"▼ {name}（ドンチャン{scfg['エントリー期間']}/{scfg['イグジット期間']}"
             f"・ATR×{atr_k}・リスク{float(scfg['リスク率']):.1%}）"]
    action = "待機"

    # --- ストップ到達検知(前回確定バーのHigh/Low)
    if pos != 0 and st.get("stop") is not None:
        sv = float(st["stop"])
        hit = (pos > 0 and float(last["Low"]) <= sv) or \
              (pos < 0 and float(last["High"]) >= sv)
        if hit:
            gap = (pos > 0 and float(last["Open"]) <= sv) or \
                  (pos < 0 and float(last["Open"]) >= sv)
            exit_px = float(last["Open"]) if gap else sv
            pnl = float(st["units"]) * (exit_px - float(st["entry"])) * pos
            lines.append(f"【確認】前バーでストップ({sv:.3f}円)到達 → 逆指値で決済済みの"
                         f"はず。モデル損益 {pnl:+,.0f}円。未決済なら手動決済すること。")
            ops.append({"種別": "要確認", "通貨ペア": "USD/JPY",
                        "内容": f"逆指値{sv:.3f}円が約定済みかポジション一覧で確認。"
                                f"残っていれば成行で決済",
                        "数量": int(float(st.get("units") or 0))})
            if final:
                append_trade(name, st, exit_px, str(last["Date"]), "stop")
            st = dict(FLAT)
            pos = 0
            action = "ストップ決済確認"

    stop_dist = atr_k * a

    if signal != pos:
        if pos != 0:
            lines.append(f"【決済】{name}の{'買' if pos > 0 else '売'}ポジションを"
                         f"全決済（先に決済逆指値を取消→成行）")
            ops.append({"種別": "決済", "通貨ペア": "USD/JPY",
                        "売買": "買" if pos < 0 else "売",
                        "数量": int(float(st.get("units") or 0)),
                        "注文方法": "成行（決済）",
                        "有効期限": "—",
                        "手順": "先に既存の決済逆指値を取消 → 成行で全決済"})
            action = "決済"
            if final:
                append_trade(name, st, close, str(last["Date"]), "signal")
            st = dict(FLAT)
        if signal != 0:
            if gate_new:
                lines.append(f"【見送り】新規{'買' if signal > 0 else '売'}シグナルあり"
                             f"だが、最重要(★5級)指標イベント通過待ちのため本日は建てない"
                             f"（明朝再判定）")
                action = action if action == "決済" else "イベント見送り"
            else:
                side = "買(ロング)" if signal > 0 else "売(ショート)"
                risk_units = equity * float(scfg["リスク率"]) / stop_dist
                lev_units = equity * float(scfg["レバ上限"]) / close
                units = int(min(risk_units, lev_units) // lot * lot)
                stop_px = close - signal * stop_dist
                lines += [
                    f"【新規】USD/JPY {side}  {units:,}通貨（{units // lot}Lot）",
                    f"  発注: 成行 → 建玉に決済逆指値 {stop_px:.3f}円 を必ずセット",
                    f"  ストップ幅: {stop_dist:.3f}円 ≒{stop_dist * 100:.0f}pips"
                    f"（決済pip差設定に使う場合はこの値）",
                    f"  想定リスク: 約{units * stop_dist:,.0f}円"
                    f"（資金の{units * stop_dist / equity:.1%}）",
                ]
                ops.append({"種別": "新規建て", "通貨ペア": "USD/JPY",
                            "売買": "買" if signal > 0 else "売",
                            "数量": units, "注文方法": "成行",
                            "決済逆指値": round(stop_px, 3),
                            "ストップ幅pips": round(stop_dist * 100),
                            "有効期限": "無期限（逆指値）",
                            "手順": "成行で建てた直後に決済逆指値を必ずセット"})
                action = f"新規{side}"
                st = {"position": signal, "entry": close,
                      "stop": round(stop_px, 3), "units": units,
                      "best": close, "entry_date": str(last["Date"])}
    elif pos != 0:
        best = float(st.get("best") or st.get("entry") or close)
        best = max(best, close) if pos > 0 else min(best, close)
        new_stop = best - pos * stop_dist
        cur_stop = float(st.get("stop") or new_stop)
        new_stop = max(cur_stop, new_stop) if pos > 0 else min(cur_stop, new_stop)
        if abs(new_stop - cur_stop) >= 0.01:
            lines.append(f"【変更】決済逆指値を {cur_stop:.3f} → {new_stop:.3f}円へ変更"
                         f"（トレイリング）")
            ops.append({"種別": "決済逆指値の変更", "通貨ペア": "USD/JPY",
                        "対象": f"{'買' if pos > 0 else '売'}ポジション",
                        "数量": int(float(st.get("units") or 0)),
                        "変更前": round(cur_stop, 3), "変更後": round(new_stop, 3),
                        "注文方法": "逆指値（決済）",
                        "有効期限": "無期限",
                        "手順": "注文一覧から対象の決済逆指値を選び、レートのみ変更"})
            action = "ストップ変更"
        else:
            lines.append("【継続】ポジション保有継続。注文変更なし。")
            action = "継続"
        st["stop"] = round(new_stop, 3)
        st["best"] = round(best, 3)
    else:
        lines.append("【待機】新規シグナルなし。発注不要。")

    return lines, action, st, signal, ops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stage", type=int, choices=[1, 2, 3], default=3,
                    help="1/2=速報(状態を更新しない) 3=最終確定(状態を更新)")
    args = ap.parse_args()
    label = {1: "速報①", 2: "速報②", 3: "最終確定"}[args.stage]
    fname_tag = {1: "速報1", 2: "速報2", 3: "確定"}[args.stage]
    final = args.stage == 3

    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        raise SystemExit("config.json がありません")
    systems = {k: v for k, v in cfg["システム"].items() if v.get("有効", True)}
    state = load_state(systems)

    # 1) 最新データ
    df = fetch_usdjpy_daily()
    df.to_csv(CSV_PATH, index=False)
    last = df.iloc[-1]
    close = float(last["Close"])
    a = float(atr(df).iloc[-1])

    # 2) 指標イベント(今後24時間・★5級のみ。events.py側で絞り込み)
    ev_cfg = cfg.get("指標フィルタ", {})
    events = upcoming_events(24) if ev_cfg.get("有効", True) else []
    gate_new = bool(events) and ev_cfg.get("高重要度イベント時は新規見送り", True)

    lines = [
        f"■ ドル円 注文指示書【{label}】 {date.today().isoformat()}",
        f"  終値: {close:.3f}円  ATR(14): {a:.3f}円",
        "-" * 46,
    ]
    actions = {}
    signals = {}
    ops_by_system = {}
    for name, scfg in systems.items():
        sys_lines, action, state[name], sig, ops = process_system(
            name, scfg, df, state[name], cfg, final, gate_new)
        lines += sys_lines + [""]
        actions[name] = action
        signals[name] = sig
        ops_by_system[name] = ops

    # デイトレ複合時間軸の現況（第3システム。判定・発注カードは毎時の
    # intraday_engine.py が担当し、朝の指示書には現況を載せて3系統を一覧にする）
    dcfg = cfg.get("デイトレ")
    dst, dclose = {}, close
    if dcfg and dcfg.get("有効", True):
        dstate_path = os.path.join(BASE_DIR, "state_intraday.json")
        if os.path.exists(dstate_path):
            _draw = load_json(dstate_path, {})
            dst = _draw.get("state", {})
            dclose = float(_draw.get("last_close") or close)
        dpos = int(dst.get("position", 0))
        tag = "【試験運転】" if dcfg.get("試験運転") else ""
        lines.append(f"▼ デイトレ複合時間軸（{dcfg['時間足']}Hドンチャン"
                     f"{dcfg['エントリー期間']}/{dcfg['イグジット期間']}"
                     f"・ATR×{dcfg['ATRストップ係数']}"
                     f"・利確ATR×{dcfg.get('利確ATR係数', 0)}・毎時判定）{tag}")
        if dpos != 0:
            tp_s = (f"／目標利確 {float(dst['tp']):.3f}円"
                    if dst.get("tp") else "")
            lines.append(f"【現況】{'買' if dpos > 0 else '売'} "
                         f"{int(dst.get('units', 0)):,}通貨 保有"
                         f"（建値{float(dst.get('entry') or 0):.3f}円） "
                         f"逆指値 {float(dst.get('stop') or 0):.3f}円{tp_s} を維持")
        else:
            lines.append("【現況】ノーポジで待機。シグナル発生時に即時通知。")
        lines.append("  ※発注カード・変更指示は毎時のデイトレ通知が正"
                     "（本欄は朝時点の現況）。")
        lines.append("")

    # 現在の建玉損益（値動き＋スワップ。2026-08-13ユーザー指示で追加。
    # 建玉を持ち越すほどスワップの累積が効くため、3系統まとめて可視化する）
    sw_cfg = swap.load(cfg)
    holds = [(n, state.get(n, {}), close) for n in systems]
    holds.append(("デイトレ複合時間軸", dst, dclose))
    holds = [h for h in holds if int(h[1].get("position", 0) or 0)
             and h[1].get("entry")]
    if holds:
        lines.append("◆ 現在の建玉損益（終値評価・概算／スワップ込み）")
        t_px = t_sw = 0.0
        for nm, s, mk in holds:
            p = int(s["position"])
            u = float(s.get("units") or 0)
            ppl = u * (mk - float(s["entry"])) * p
            sy, sd = swap.accrued(p, u, s.get("entry_date"), sw=sw_cfg)
            t_px += ppl
            t_sw += sy
            lines.append(f"  {nm}: {ppl + sy:+,.0f}円"
                         f"（値動き {ppl:+,.0f}円／スワップ {sy:+,.0f}円"
                         f"・{sd}日分）")
        lines.append(f"  合計 {t_px + t_sw:+,.0f}円"
                     f"（値動き {t_px:+,.0f}円／スワップ {t_sw:+,.0f}円）")
        lines.append(f"  ※{swap.note(sw_cfg)}")
        lines.append("")

    # 指標イベント欄
    lines.append("◆ 指標イベント（今後24時間・変動率★5級のみ警戒）")
    if events is None:
        lines.append("  カレンダー取得失敗。FOMC・雇用統計・日銀会合等の有無を手動確認"
                     "推奨（本日は新規見送りフィルタ無効）")
    elif events:
        for jst, cur, title, jp in events:
            jp_s = f"（{jp}）" if jp else ""
            lines.append(f"  ・{jst:%m/%d %H:%M} [{cur}] {title}{jp_s}")
        if gate_new:
            lines.append("  → 新規建ては全システム見送り。既存玉の決済・トレイルは通常どおり。")
    else:
        lines.append("  なし")

    lines += ["-" * 46,
              "※発注はMATRIX TRADERで本人が手動で行うこと(JFXはEA等の自動発注禁止)。",
              "※逆指値は建玉と必ずセットで維持すること。"]
    if not final:
        lines.append("※本書は速報(参考)。発注判断は8時配信の【最終確定】に従うこと。")

    # 発注カード（転記用）を同じ指示書に載せる。1ファイルで完結させるため。
    lines += ["", order_card.render(ops_by_system, final=final)]
    text = "\n".join(lines)

    os.makedirs(ORDERS_DIR, exist_ok=True)
    out = os.path.join(ORDERS_DIR,
                       f"{date.today().isoformat()}_指示_{fname_tag}.txt")
    if not args.dry_run:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")

        # 機械可読版（将来の入力自動化・突合検証用）
        save_json(os.path.join(
            ORDERS_DIR, f"{date.today().isoformat()}_発注_{fname_tag}.json"),
            {"日付": date.today().isoformat(), "段階": label,
             "終値": round(close, 3), "ATR": round(a, 3),
             "操作": ops_by_system})

    if final and args.dry_run:
        print("※dry-runのため状態保存・日次ログ・Drive連動はスキップ")
    if final and not args.dry_run:
        save_json(STATE_PATH, state)   # 速報段階ではモデル状態を確定させない

        # 日次ログ(システムごとに1行。資産系の列は全システム合算値)
        realized = realized_total()
        # 評価損益は値動き＋未受渡スワップ（2026-08-13ユーザー指示）
        unreal = sum(
            float(s["units"]) * (close - float(s["entry"])) * int(s["position"])
            + swap.accrued(int(s["position"]), float(s["units"]),
                           s.get("entry_date"))[0]
            for s in state.values() if int(s.get("position", 0)) != 0)
        model_equity = float(cfg["口座資金_円"]) + realized + unreal
        for name in systems:
            s = state[name]
            p = int(s.get("position", 0))
            append_csv(DAILY_CSV,
                       ["日付", "システム", "終値", "シグナル", "アクション",
                        "ポジション", "ストップ", "数量", "実現損益累計",
                        "評価損益計", "モデル資産"],
                       [date.today().isoformat(), name, f"{close:.3f}",
                        "買" if signals[name] > 0 else "売"
                        if signals[name] < 0 else "無",
                        actions[name],
                        "買" if p > 0 else "売" if p < 0 else "なし",
                        s.get("stop") or "", int(s.get("units", 0)),
                        f"{realized:.0f}", f"{unreal:.0f}",
                        f"{model_equity:.0f}"])

        # Google Drive連動: 指示書コピー + ダッシュボード更新
        import local_settings
        gd = local_settings.get("drive_dir") or cfg.get("Driveフォルダ")
        if gd and os.path.isdir(gd):
            try:
                import shutil
                shutil.copy2(out, os.path.join(gd, "指示書",
                                               os.path.basename(out)))
            except Exception as e:
                print("Drive指示書コピー失敗:", e)
            try:
                import dashboard
                dashboard.build()
            except Exception as e:
                print("ダッシュボード更新失敗:", e)

    # Webボード(モバイル運用ボード)HTML生成 — 速報①②・最終確定の全ステージで更新
    # ※公開(アーティファクト更新)は朝のスケジュールタスク内のClaudeが行う
    try:
        import dashboard_web
        dashboard_web.build()
    except Exception as e:
        print("Webボード生成失敗:", e)

    print(text)
    if not args.dry_run:
        summary = " / ".join(f"{n}={a}" for n, a in actions.items())
        notify(f"ドル円 {label}", summary)
        if final:
            if sys.platform == "darwin":
                subprocess.run(["open", out], check=False)


if __name__ == "__main__":
    main()
