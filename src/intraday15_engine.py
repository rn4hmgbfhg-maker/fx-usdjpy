# -*- coding: utf-8 -*-
"""デイトレ15分シグナルエンジン — 第4のシステム「デイトレ15分」

15分足を15分ごとに判定し、アクションが発生した時だけ指示書と通知を出す。
テクニカル（research_15m.pyが選定した戦略）にファンダメンタルズを統合する:

  ・指標イベントゲート: ★5級が近い時間にあれば新規建てを見送り（既存2H系と同じ
    events.py。15分足は反応が速いのでブロック窓は45分に短縮）
  ・ファンダ参考情報: results/fundamental_latest.json のレジーム・翌日予測・
    次の焦点を指示書に併記（予測モデルは検証でエッジなしのため発注判定には
    使わない=表示のみ。ニュース総括の含意も1行添える）
  ・テクニカル合議: TradingView型のMA+オシレーター多数決を15分足・1時間足で
    算出して指示書に併記（世界で最も参照されている合議形式。強い逆行時は
    指示書に注意書きを出す。ゲートはしない=バックテスト済みの戦略が正）

設計（既存と同じ半自動思想）:
  ・JFX(MATRIX TRADER)はEA禁止 → 判定と指示書までを自動化し発注は本人が手動
  ・状態は state_intraday15.json（実際に指示した建玉のみが正）
  ・通知キュー: results/intraday15_status.json の「未通知」。毎時のClaude
    タスクがプッシュ通知し --ack-all で既読化。加えてシグナル発生時は
    macOS通知＋Gmail SMTPで即時メール（15分粒度は1時間待てないため）

使い方:
  python3 src/intraday15_engine.py            # 通常実行（15分ごとlaunchd）
  python3 src/intraday15_engine.py --dry-run  # 通知・状態更新なしで内容確認
  python3 src/intraday15_engine.py --status   # 現況JSONを表示して終了
  python3 src/intraday15_engine.py --ack-all  # 未通知キューを既読化
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import atr  # noqa: E402
from data_fetch_15m import load_15m  # noqa: E402
from research_15m import build_df15, CFG_KEY  # noqa: E402
import strategies_15m as s15  # noqa: E402
from events import upcoming_events  # noqa: E402
import order_card  # noqa: E402
import swap  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "state_intraday15.json")
STATUS_PATH = os.path.join(BASE_DIR, "results", "intraday15_status.json")
ORDERS_DIR = os.path.join(BASE_DIR, "results", "orders", "intraday15")
LATEST_TXT = os.path.join(BASE_DIR, "results", "intraday15_latest.txt")
LOG_CSV = os.path.join(BASE_DIR, "results", "intraday15_log.csv")
TRADES_CSV = os.path.join(BASE_DIR, "results", "trades_intraday15.csv")
FUND_JSON = os.path.join(BASE_DIR, "results", "fundamental_latest.json")
COST_PER_SIDE = 0.004
SYSTEM_NAME = "デイトレ15分"
EVENT_BLOCK_MIN = 45        # ★5イベントのこの分数前から新規建てブロック
TRAIL_NOTIFY_ATR = 1.0      # トレイル変更を通知する最小幅 = ATR×この係数
                            # （15分足ATRは小さいので係数は2H系より大きめ）

FLAT = {"position": 0, "entry": None, "stop": None, "tp": None, "units": 0,
        "best": None, "entry_date": None}


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def notify_mac(title, message):
    # GitHub Actions等のLinuxではosascriptが無くFileNotFoundErrorで落ちる。
    # macOS以外では通知をスキップする（判定・メールには影響しない）。
    if sys.platform != "darwin":
        return
    subprocess.run([
        "osascript", "-e",
        f'display notification "{message}" with title "{title}" sound name "Glass"',
    ], check=False)


def notify_mail(subject, text):
    """シグナル即時メール（新規・決済など全アクションで必ず送る。
    経路はmailer.send_signalに一本化=2H系と共通）。"""
    try:
        import mailer
        return mailer.send_signal(subject, text)
    except Exception as e:
        return False, f"メール送信例外: {e}"


def append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(",".join(header) + "\n")
        f.write(",".join(str(v) for v in row) + "\n")


TRADES_HEADER = ["建玉バー", "決済バー", "売買", "建値", "決済値", "数量",
                 "価格損益円", "スワップ円", "スワップ日数", "損益円", "決済理由"]


def append_trade(st, exit_px, exit_bar, reason, exit_dt=None):
    pos = int(st["position"])
    units = float(st["units"])
    entry = float(st["entry"])
    price_pnl = units * (exit_px - entry) * pos - units * COST_PER_SIDE * 2
    sw_yen, sw_days = swap.accrued(pos, units, st.get("entry_date"),
                                   exit_dt or datetime.now())
    swap.ensure_columns(TRADES_CSV, TRADES_HEADER)
    append_csv(TRADES_CSV, TRADES_HEADER,
               [st.get("entry_date", ""), exit_bar,
                "買" if pos > 0 else "売",
                f"{entry:.3f}", f"{exit_px:.3f}", int(units),
                f"{price_pnl:.0f}", f"{sw_yen:.0f}", sw_days,
                f"{price_pnl + sw_yen:.0f}", reason])
    return price_pnl + sw_yen


def blocking_events(minutes=EVENT_BLOCK_MIN):
    ev = upcoming_events(hours=24)
    if ev is None:
        return None, []
    soon = [e for e in ev
            if (e[0] - datetime.now(e[0].tzinfo)).total_seconds()
            <= minutes * 60]
    return soon, ev


def fundamental_lines():
    """ファンダ研究の要点（表示のみ・発注判定には使わない）。"""
    f = load_json(FUND_JSON, None)
    if not f:
        return []
    lines = ["", "◆ ファンダメンタルズ（参考・発注判定には未使用）"]
    if f.get("レジーム"):
        lines.append(f"  レジーム: {f['レジーム']}")
    yf = f.get("翌日予測") or {}
    if yf:
        prob = yf.get("上昇確率")
        rng = yf.get("想定レンジ") or ["—", "—"]
        lines.append(f"  翌日予測: {yf.get('方向', '—')}"
                     + (f"（上昇確率{prob:.0%}）" if prob is not None else "")
                     + f" 想定レンジ {rng[0]}〜{rng[1]}円"
                     "（検証52%＝エッジなし・表示のみ）")
    news = f.get("ニュース") or {}
    if news.get("次の焦点"):
        lines.append(f"  次の焦点: {news['次の焦点'][:80]}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ack-all", action="store_true")
    ap.add_argument("--force-notify", action="store_true")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(load_json(STATUS_PATH, {}), ensure_ascii=False,
                         indent=2))
        return
    if args.ack_all:
        st = load_json(STATUS_PATH, {})
        st["未通知"] = []
        save_json(STATUS_PATH, st)
        print("未通知キューを既読化しました")
        return

    cfg = load_json(CONFIG_PATH, None)
    scfg = (cfg or {}).get(CFG_KEY)
    if not scfg:
        raise SystemExit(f"config.json に「{CFG_KEY}」がありません"
                         "（research_15m.py --seed を先に実行）")
    if not scfg.get("有効", True):
        print(f"{SYSTEM_NAME}システムは無効化されています")
        return

    spec = scfg["パラメータ"]
    atr_k = float(scfg["ATRストップ係数"])
    tp_k = float(scfg.get("利確ATR係数", 0))
    lot = int(cfg["取引単位_通貨"])
    equity = float(cfg["口座資金_円"])
    trial = scfg.get("試験運転", True)

    # 1) データ（確定バーのみ）とシグナル
    # 選定戦略の時間足（15分/1時間/4時間）へリサンプルして判定する。
    # 判定は直近6,000本で行う（全履歴を回すと15分ごとの実行が重くなる。
    # ウォームアップにはEMA200・ドンチャン96でも十分な長さ）
    raw = load_15m()
    df15 = build_df15(raw)
    tf_min = int(spec.get("時間足分", 15))
    df = s15.to_tf(df15, tf_min).tail(6000).reset_index(drop=True)
    tf_label = s15.TF_LABEL.get(tf_min, f"{tf_min}分") + "足"
    sig_series = s15.build_signal(df, spec)
    last = df.iloc[-1]
    close = float(last["Close"])
    a = float(atr(df).iloc[-1])
    signal = int(sig_series.iloc[-1])
    last_bar = str(last["Date"])

    # データ鮮度ガード: 最終確定バーが古すぎる時は売買判定を出さない
    # （Yahoo障害・レート制限中に過去のブレイクを「今のシグナル」として
    # 通知してしまう事故を防ぐ。閾値 = 時間足の2本分+30分）
    last_end_utc = (pd.to_datetime(last["utc"])
                    + pd.Timedelta(minutes=tf_min))
    age_min = (pd.Timestamp.now(tz="UTC") - last_end_utc).total_seconds() / 60
    stale = age_min > tf_min * 2 + 30

    st_all = load_json(STATE_PATH, {})
    st = {**FLAT, **st_all.get("state", {})}
    prev_bar = st_all.get("last_bar")
    new_bar = last_bar != prev_bar

    if stale:
        now = datetime.now()
        print(f"⚠データ鮮度不足: 最終確定バー {last_bar}（{age_min:.0f}分前）。"
              f"売買判定を見送り（データ回復後に自動再開）")
        if not args.dry_run:
            status = load_json(STATUS_PATH, {})
            status.update({
                "更新": now.isoformat(timespec="minutes"),
                "確定バー": last_bar, "終値": round(close, 3),
                "アクション": "データ鮮度不足",
                "鮮度警告": True,
                "ポジション": int(st.get("position", 0)),
                "数量": int(st.get("units", 0) or 0),
                "ストップ": st.get("stop"),
                "目標利確": st.get("tp"),
                "試験運転": trial,
                "戦略": scfg.get("戦略", ""),
            })
            status.setdefault("未通知", [])
            save_json(STATUS_PATH, status)
        return

    # 2) 指標イベントゲート
    soon, ev24 = blocking_events()
    calendar_fail = soon is None
    gate_new = bool(soon) and not calendar_fail and \
        cfg.get("指標フィルタ", {}).get("有効", True)

    # 3) テクニカル合議（表示・注意書き用。戦略の時間足に関わらず15分足と
    #    1時間足で算出する）
    rate15 = s15.tech_rating(df15.tail(600))
    rate1h = s15.tech_rating(s15.to_tf(df15.tail(9600).reset_index(drop=True),
                                       60).tail(400))

    lines = [f"■ ドル円 デイトレ15分指示書 {datetime.now():%Y-%m-%d %H:%M}"
             + ("【試験運転】" if trial else ""),
             f"  確定バー: {last_bar}（{tf_label}）  終値: {close:.3f}円"
             f"  ATR(14): {a:.3f}円",
             "-" * 46,
             f"▼ {SYSTEM_NAME}（{scfg.get('戦略', s15.describe(spec))}"
             f"・ATR×{atr_k}"
             + (f"・利確ATR×{tp_k}" if tp_k else "")
             + f"・リスク{float(scfg['リスク率']):.1%}）"]

    ops = []
    action = "待機"
    pos = int(st.get("position", 0))
    stop_dist = atr_k * a

    # --- ストップ到達検知
    if new_bar and pos != 0 and st.get("stop") is not None:
        sv = float(st["stop"])
        hit = (pos > 0 and float(last["Low"]) <= sv) or \
              (pos < 0 and float(last["High"]) >= sv)
        if hit:
            gap = (pos > 0 and float(last["Open"]) <= sv) or \
                  (pos < 0 and float(last["Open"]) >= sv)
            exit_px = float(last["Open"]) if gap else sv
            pnl = float(st["units"]) * (exit_px - float(st["entry"])) * pos
            sw_y, sw_d = swap.accrued(pos, st["units"], st.get("entry_date"))
            lines.append(f"【確認】直近バーでストップ({sv:.3f}円)到達 → 逆指値で"
                         f"決済済みのはず。モデル損益 {pnl + sw_y:+,.0f}円。"
                         f"未決済なら手動決済すること。")
            ops.append({"種別": "要確認", "通貨ペア": "USD/JPY",
                        "内容": f"逆指値{sv:.3f}円の約定をポジション一覧で確認。"
                                f"残っていれば成行で決済",
                        "数量": int(float(st.get("units") or 0))})
            if not args.dry_run:
                append_trade(st, exit_px, last_bar, "stop")
            st = dict(FLAT)
            pos = 0
            action = "ストップ決済確認"

    # --- 目標利確到達検知
    if new_bar and pos != 0 and st.get("tp"):
        tv = float(st["tp"])
        tp_hit = (pos > 0 and float(last["High"]) >= tv) or \
                 (pos < 0 and float(last["Low"]) <= tv)
        if tp_hit:
            pnl = float(st["units"]) * (tv - float(st["entry"])) * pos
            sw_y, sw_d = swap.accrued(pos, st["units"], st.get("entry_date"))
            lines.append(f"【確認】直近バーで目標利確({tv:.3f}円)到達 → 決済指値で"
                         f"約定済みのはず。モデル損益 {pnl + sw_y:+,.0f}円。"
                         f"未約定なら成行で利確決済すること。")
            ops.append({"種別": "要確認", "通貨ペア": "USD/JPY",
                        "内容": f"利確指値{tv:.3f}円の約定をポジション一覧で確認。"
                                f"残っていれば成行で決済し、残った逆指値も取消",
                        "数量": int(float(st.get("units") or 0))})
            if not args.dry_run:
                append_trade(st, tv, last_bar, "tp")
            st = dict(FLAT)
            pos = 0
            action = "利確決済確認"

    if signal != pos:
        if pos != 0:
            lines.append(f"【決済】{'買' if pos > 0 else '売'}ポジションを全決済"
                         f"（先に決済逆指値を取消→成行）")
            ops.append({"種別": "決済", "通貨ペア": "USD/JPY",
                        "売買": "買" if pos < 0 else "売",
                        "数量": int(float(st.get("units") or 0)),
                        "注文方法": "成行（決済）", "有効期限": "—",
                        "手順": "先に既存の決済逆指値を取消 → 成行で全決済"})
            action = "決済"
            if not args.dry_run:
                append_trade(st, close, last_bar, "signal")
            st = dict(FLAT)
        if signal != 0:
            if gate_new:
                nxt = soon[0]
                lines.append(f"【見送り】新規{'買' if signal > 0 else '売'}シグナル"
                             f"ありだが、{nxt[0]:%H:%M}の★5指標"
                             f"（{nxt[3] or nxt[2]}）通過待ちのため建てない"
                             f"（通過後の確定バーで再判定）")
                action = action if action == "決済" else "イベント見送り"
            else:
                side = "買(ロング)" if signal > 0 else "売(ショート)"
                risk_units = equity * float(scfg["リスク率"]) / stop_dist
                lev_units = equity * float(scfg["レバ上限"]) / close
                units = int(min(risk_units, lev_units) // lot * lot)
                stop_px = close - signal * stop_dist
                tp_px = close + signal * tp_k * a if tp_k else None
                lines += [
                    f"【新規】USD/JPY {side}  {units:,}通貨（{units // lot}Lot）",
                    f"  発注: 成行 → 建玉に決済OCO（逆指値 {stop_px:.3f}円"
                    + (f" ／ 目標利確指値 {tp_px:.3f}円" if tp_px else "")
                    + "）を必ずセット",
                    f"  ストップ幅: {stop_dist:.3f}円 ≒{stop_dist * 100:.0f}pips"
                    + (f"  利確幅: {tp_k * a:.3f}円 ≒{tp_k * a * 100:.0f}pips"
                       if tp_px else ""),
                    f"  想定リスク: 約{units * stop_dist:,.0f}円"
                    f"（資金の{units * stop_dist / equity:.1%}）"
                    + (f"  目標利益: 約{units * tp_k * a:,.0f}円" if tp_px else ""),
                ]
                # テクニカル合議が強く逆行している時は注意書き（ゲートはしない）
                opp = -1 if signal > 0 else 1
                if (rate1h["スコア"] * opp >= 0.5):
                    lines.append(f"  ⚠参考: 1時間足テクニカル合議は"
                                 f"「{rate1h['判定']}」で逆方向。サイズ注意")
                op = {"種別": "新規建て", "通貨ペア": "USD/JPY",
                      "売買": "買" if signal > 0 else "売",
                      "数量": units, "注文方法": "成行",
                      "決済逆指値": round(stop_px, 3),
                      "ストップ幅pips": round(stop_dist * 100),
                      "有効期限": "無期限（OCO）",
                      "手順": "成行で建てた直後に決済OCO"
                              "（逆指値+利確指値）を必ずセット"}
                if tp_px:
                    op["決済指値(利確)"] = round(tp_px, 3)
                ops.append(op)
                action = f"新規{side}"
                st = {"position": signal, "entry": close,
                      "stop": round(stop_px, 3),
                      "tp": round(tp_px, 3) if tp_px else None,
                      "units": units, "best": close, "entry_date": last_bar}
    elif pos != 0:
        # 目標利確が未設定の既存玉（機能追加前の建玉）には後付けで設定を指示
        if tp_k and not st.get("tp"):
            tp_px = float(st["entry"]) + pos * tp_k * a
            lines.append(f"【設定】既存{'買' if pos > 0 else '売'}ポジションに"
                         f"目標利確指値 {tp_px:.3f}円 を追加設定"
                         f"（建値{float(st['entry']):.3f}円±ATR×{tp_k}）")
            ops.append({"種別": "決済指値(利確)の設定", "通貨ペア": "USD/JPY",
                        "対象": f"{'買' if pos > 0 else '売'}ポジション",
                        "数量": int(float(st.get("units") or 0)),
                        "決済指値(利確)": round(tp_px, 3),
                        "注文方法": "指値（決済）", "有効期限": "無期限",
                        "手順": "既存の決済逆指値とOCOになるよう利確指値を追加"
                                "（OCO化できない場合は指値を単独追加）"})
            action = "利確設定"
            st["tp"] = round(tp_px, 3)
        best = float(st.get("best") or st.get("entry") or close)
        best = max(best, close) if pos > 0 else min(best, close)
        new_stop = best - pos * stop_dist
        cur_stop = float(st.get("stop") or new_stop)
        new_stop = max(cur_stop, new_stop) if pos > 0 else min(cur_stop, new_stop)
        if abs(new_stop - cur_stop) >= TRAIL_NOTIFY_ATR * a:
            lines.append(f"【変更】決済逆指値を {cur_stop:.3f} → {new_stop:.3f}円"
                         f"へ変更（トレイリング。利確指値はそのまま）")
            ops.append({"種別": "決済逆指値の変更", "通貨ペア": "USD/JPY",
                        "対象": f"{'買' if pos > 0 else '売'}ポジション",
                        "数量": int(float(st.get("units") or 0)),
                        "変更前": round(cur_stop, 3), "変更後": round(new_stop, 3),
                        "注文方法": "逆指値（決済）", "有効期限": "無期限",
                        "手順": "注文一覧から対象の決済逆指値を選び、レートのみ変更"})
            action = "ストップ変更"
            st["stop"] = round(new_stop, 3)
        elif action == "待機":
            tp_s = (f"／目標利確 {float(st['tp']):.3f}円" if st.get("tp") else "")
            lines.append(f"【継続】ポジション保有継続。注文変更なし"
                         f"（逆指値 {cur_stop:.3f}円{tp_s} を維持）。")
            action = "継続"
        st["best"] = round(best, 3)
    else:
        lines.append("【待機】新規シグナルなし。発注不要。")

    # テクニカル合議（毎回表示）
    lines += ["",
              f"◆ テクニカル合議（TradingView型・参考）",
              f"  15分足: {rate15['判定']}（{rate15['スコア']:+.2f}"
              f" MA{rate15['MA']:+.2f}/OSC{rate15['オシレーター']:+.2f}）"
              f"　1時間足: {rate1h['判定']}（{rate1h['スコア']:+.2f}）"]

    # ファンダ参考情報
    lines += fundamental_lines()

    # 保有中の建玉損益
    cur_pos = int(st.get("position", 0) or 0)
    swap_now = (0.0, 0)
    if cur_pos and st.get("entry"):
        u_now = float(st.get("units") or 0)
        px_pnl = u_now * (close - float(st["entry"])) * cur_pos
        swap_now = swap.accrued(cur_pos, u_now, st.get("entry_date"))
        lines += ["",
                  "◆ 現在の建玉損益（終値評価・概算）",
                  f"  値動き {px_pnl:+,.0f}円　スワップ {swap_now[0]:+,.0f}円"
                  f"（{swap_now[1]}日分）　合計 {px_pnl + swap_now[0]:+,.0f}円"]

    # 指標イベント欄
    lines.append("")
    lines.append(f"◆ 指標イベント（★5級のみ・{EVENT_BLOCK_MIN}分前から新規停止）")
    if calendar_fail:
        lines.append("  カレンダー取得失敗。FOMC・雇用統計・日銀会合等の有無を"
                     "手動確認推奨（新規見送りフィルタ無効）")
    elif ev24:
        for jst, cur, title, jp in ev24:
            jp_s = f"（{jp}）" if jp else ""
            mark = " ★直前" if any(e[0] == jst for e in soon) else ""
            lines.append(f"  ・{jst:%m/%d %H:%M} [{cur}] {title}{jp_s}{mark}")
    else:
        lines.append("  今後24時間: なし")

    lines += ["-" * 46,
              "※発注はMATRIX TRADERで本人が手動で行うこと(JFXはEA等の自動発注禁止)。",
              "※逆指値は建玉と必ずセットで維持すること。",
              "", order_card.render({SYSTEM_NAME: ops}, final=True)]
    text = "\n".join(lines)

    has_action = bool(ops) or args.force_notify
    now = datetime.now()

    if not args.dry_run:
        status = load_json(STATUS_PATH, {})
        if has_action:
            os.makedirs(ORDERS_DIR, exist_ok=True)
            fname = f"{now:%Y-%m-%d_%H%M}_デイトレ15分指示.txt"
            fpath = os.path.join(ORDERS_DIR, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            with open(LATEST_TXT, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            summary = f"デイトレ15分: {action}"
            if ops and ops[0].get("種別") == "新規建て":
                summary += (f" {ops[0]['数量']:,}通貨"
                            f"・逆指値{ops[0]['決済逆指値']:.3f}円")
            elif ops and "変更後" in ops[0]:
                summary += f" → {ops[0]['変更後']:.3f}円"
            notify_mac(f"ドル円デイトレ15分{'(試験運転)' if trial else ''}",
                       summary)
            mail_ok, mail_msg = notify_mail(
                f"【FXデイトレ15分】{action} {now:%m/%d %H:%M}"
                + ("（試験運転）" if trial else ""), text)
            q = status.get("未通知", [])
            q.append({"id": f"{now:%Y%m%d%H%M%S}", "時刻": f"{now:%H:%M}",
                      "アクション": action, "要旨": summary,
                      "指示書": fpath, "メール": mail_msg})
            status["未通知"] = q[-20:]
            append_csv(LOG_CSV,
                       ["日時", "確定バー", "終値", "シグナル", "アクション",
                        "ポジション", "ストップ", "数量"],
                       [now.isoformat(timespec="minutes"), last_bar,
                        f"{close:.3f}",
                        "買" if signal > 0 else "売" if signal < 0 else "無",
                        action,
                        "買" if int(st.get("position", 0)) > 0 else
                        "売" if int(st.get("position", 0)) < 0 else "なし",
                        st.get("stop") or "", int(st.get("units", 0))])
            gd = cfg.get("Driveフォルダ")
            if gd and os.path.isdir(gd):
                try:
                    import shutil
                    shutil.copy2(fpath, os.path.join(gd, "指示書", fname))
                except Exception as e:
                    print("Drive指示書コピー失敗:", e)

        save_json(STATE_PATH, {"state": st, "last_bar": last_bar,
                               "last_close": close,
                               "updated": now.isoformat(timespec="minutes")})
        status.update({
            "更新": now.isoformat(timespec="minutes"),
            "確定バー": last_bar, "終値": round(close, 3),
            "ATR": round(a, 3), "アクション": action,
            "ポジション": int(st.get("position", 0)),
            "数量": int(st.get("units", 0) or 0),
            "ストップ": st.get("stop"),
            "目標利確": st.get("tp"),
            "試験運転": trial,
            "イベント直前ブロック": bool(gate_new),
            "カレンダー取得失敗": bool(calendar_fail),
            "テクニカル合議15分": rate15,
            "テクニカル合議1時間": rate1h,
            "戦略": scfg.get("戦略", ""),
            "スワップ円": round(swap_now[0]),
            "スワップ日数": swap_now[1],
        })
        status.setdefault("未通知", [])
        save_json(STATUS_PATH, status)

        try:
            import dashboard_web
            dashboard_web.build()
        except Exception as e:
            print("Webボード生成失敗:", e)

    print(text)
    print(f"\n[アクション: {action}]"
          + ("（通知対象）" if has_action else "（変化なし・通知なし）"))


if __name__ == "__main__":
    main()
