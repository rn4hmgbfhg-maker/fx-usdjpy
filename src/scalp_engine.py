# -*- coding: utf-8 -*-
"""スキャルピング シグナル配信（2026-10-13 落合さん指示）

「ボタンを押した時しか判断しないと、入るタイミングが押すタイミング次第になる」ため、
15分ごとの GitHub Actions（signals.yml）で機械的に判定し、条件がそろった瞬間にメールで配信する。
入る・入らないの正式な判断はこの配信。ボードの「スキャル分析」は『今入るとしたら』の参考。

判定（研究 results/research_scalp_*_latest.json に寄せる）:
  対象   : ドル円・ユーロドル × 15分足・30分足・1時間足
  条件   : 各時間足の研究上位6条件のうち、全期間PF1.0以上のもの（入り方・EMA200順行・損切り/利確ATR倍率）
  発火   : 直近の確定足でその条件のシグナルが出た
  想定勝率: その条件の過去の勝率（同じ入り方のいまの時間帯の勝率は参考として併記）
  配信   : 想定勝率が50%を超えたものを配信（同じペアで複数なら想定勝率が最も高い1件）。★5級指標の
           前後30分は配信するが注意を明記（発注は本人判断）
  重複防止: ペア・時間足・足の時刻で1回だけ。さらに同じペア・同じ向きは、前回配信から保有上限の時間が
           過ぎるまで出さない（行き過ぎが続く間に毎本出る連発を防ぐ。検証の「保有中は次を取らない」と同じ）

結果の自動採点: 配信後の15分足で、入った値（次の足の始値）から損切り・利確のどちらに先に触れたか、
保有上限を過ぎたら時間切れで評価（往復コスト0.8pips）。results/scalp_signals.json に保存しボードが表示する。

使い方:
  python3 src/scalp_engine.py            # 判定して配信（通常は data_fetch_eurusd.py の後に自動で呼ばれる）
  python3 src/scalp_engine.py --dry-run  # 配信せずに判定だけ表示
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "src"))
import research_scalp as rs  # noqa: E402

RES = os.path.join(BASE_DIR, "results")
SIG_JSON = os.path.join(RES, "scalp_signals.json")
TF_MIN = {"15分": 15, "30分": 30, "1時間": 60}
TF_RULE = {"15分": "15min", "30分": "30min", "1時間": "1h"}
WIN_MIN = 0.50
RISKS = (0.01, 0.02)          # 配信に載せる1回のリスク（口座資金に対する割合）
JST = timezone(timedelta(hours=9))


def _cfg():
    try:
        return json.load(open(os.path.join(BASE_DIR, "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _usdjpy_rate():
    try:
        d = pd.read_csv(os.path.join(BASE_DIR, "data", "usdjpy_15m.csv"))
        return float(d["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        return 158.0


def _market_closed(now):
    wd, h = now.weekday(), now.hour
    return (wd == 5 and h >= 7) or wd == 6 or (wd == 0 and h < 6)


def _label_params(lab):
    import re
    m = re.match(r"(.+?)(・EMA200と同方向のみ)?（損切りATR×([\d.]+)／利確ATR×([\d.]+)／最長(\d+)本）", lab or "")
    rev = {v: k for k, v in rs.METHODS.items()}
    if not m or m.group(1) not in rev:
        return None
    return rev[m.group(1)], bool(m.group(2)), float(m.group(3)), float(m.group(4)), int(m.group(5))


def _band_win(report_tf, method_name, hour):
    """同じ入り方（研究の最良条件と同じ入り方の時だけ）の、いまの時間帯（1時間幅）の勝率。"""
    tops = report_tf.get("上位条件") or []
    if not tops or not tops[0]["条件"].startswith(rs.METHODS[method_name]):
        return None
    rows = (report_tf.get("同じ入り方の時間帯幅別(全変種合算)") or {}).get("1時間幅") or {}
    v = rows.get(f"{hour:02d}-{hour + 1:02d}時")
    return float(v["勝率"]) if v else None


def detect(pair, now):
    """いま発火している候補（想定勝率の高い順）を返す。"""
    cfgp = rs.PAIRS[pair]
    rs.MULT = cfgp["MULT"]
    dec = 5 if pair == "eurusd" else 3
    res = _load_json(os.path.join(RES, cfgp["out"]), {})
    m15, h1 = rs._load_pair(pair)
    out = []
    for tf, rule in TF_RULE.items():
        rep = (res.get("時間足別") or {}).get(tf) or {}
        src = rs._base_for(tf, m15, h1)
        df = rs.add_indicators(rs.resample(src, rule))
        # 確定した最後の足（足の始まり＋足の長さ ≤ いま）
        closed = df[df.index + pd.Timedelta(minutes=TF_MIN[tf]) <= now]
        if len(closed) < 210:
            continue
        i = len(closed) - 1
        bar_t = closed.index[i]
        if now - (bar_t + pd.Timedelta(minutes=TF_MIN[tf])) > pd.Timedelta(minutes=40):
            continue      # データが古い（週明け・取得遅延）
        for cond in rep.get("上位条件") or []:
            if float(cond.get("PF") or 0) < 1.0:
                continue
            prm = _label_params(cond["条件"])
            if not prm:
                continue
            meth, trend_only, sl_k, tp_k, hold = prm
            s = int(rs.signals(closed, meth).iloc[i])
            if not s or (trend_only and int(closed["trend"].iloc[i]) != s):
                continue
            atr = float(closed["atr"].iloc[i])
            if not np.isfinite(atr) or atr <= 0:
                continue
            next_hour = (bar_t + pd.Timedelta(minutes=TF_MIN[tf])).hour
            bw = _band_win(rep, meth, next_hour)
            # 想定勝率＝その条件の過去の勝率（時間帯の勝率は参考として併記。平均を取ると
            # ドル円は30日で0件になり配信が来なくなるため採らない＝2026-10-13 検証）
            win = float(cond["勝率"])
            e = float(closed["Close"].iloc[i])
            out.append({
                "pair": cfgp["名"], "code": "EUR/USD" if pair == "eurusd" else "USD/JPY", "tf": tf,
                "bar": f"{bar_t:%Y-%m-%d %H:%M}", "side": "買" if s > 0 else "売", "d": s,
                "entry": round(e, dec), "stop": round(e - s * sl_k * atr / rs.MULT, dec),
                "tp": round(e + s * tp_k * atr / rs.MULT, dec), "sl_pips": round(sl_k * atr, 1),
                "tp_pips": round(tp_k * atr, 1), "hold_min": hold * TF_MIN[tf],
                "win": round(win, 3), "cond_win": cond["勝率"], "band_win": bw, "pf": cond["PF"],
                "n": cond["取引数"], "rule": cond["条件"], "dec": dec, "mult": cfgp["MULT"],
            })
    out.sort(key=lambda x: x["win"], reverse=True)
    return out


def _events_near(pair, now):
    try:
        import events
        cur = ("USD", "EUR") if pair == "eurusd" else ("USD", "JPY")
        ev = events.upcoming_events(2, currencies=cur) or []
        return [f"{t:%m/%d %H:%M} {c} {jp or name}" for t, c, name, jp in ev
                if t - now <= timedelta(minutes=90)]
    except Exception:  # noqa: BLE001
        return []


def compose(sig, cap, ujr, events_note):
    jpy = ujr if sig["code"] == "EUR/USD" else 1.0
    d_px = abs(sig["entry"] - sig["stop"])
    lines = [f"■ スキャル シグナル【正式配信】 {sig['pair']}（{sig['code']}）",
             f"  {sig['side']}　想定勝率 {sig['win'] * 100:.0f}%（{sig['tf']}足・{sig['bar']} 確定足）",
             "----------------------------------------------",
             f"  入る値（成行の目安）: {sig['entry']:.{sig['dec']}f}",
             f"  利確（指値）        : {sig['tp']:.{sig['dec']}f}（+{sig['tp_pips']:.0f} pips）",
             f"  損切り（逆指値）    : {sig['stop']:.{sig['dec']}f}（-{sig['sl_pips']:.0f} pips）",
             f"  有効                : 配信から15分以内。それを過ぎた・現在値が入る値から{max(3, round(sig['sl_pips'] / 3))}pips以上離れたら見送り",
             f"  保有の上限          : 約{sig['hold_min'] // 60}時間{sig['hold_min'] % 60}分（届かなければ時間切れで手仕舞い）",
             "",
             "◆ 数量（1回のリスク別・円換算）"]
    for r in RISKS:
        u = int(cap * r / (d_px * jpy) // 1000 * 1000) if d_px > 0 else 0
        lines.append(f"  リスク{r * 100:.0f}%（{cap * r:,.0f}円）: {u:,} 通貨 → 損切りなら -{d_px * jpy * u:,.0f}円／"
                     f"利確なら +{abs(sig['tp'] - sig['entry']) * jpy * u:,.0f}円")
    lines += ["",
              "◆ 根拠（過去の検証）",
              f"  条件: {sig['rule']}",
              f"  条件の勝率 {float(sig['cond_win']) * 100:.0f}%・PF{sig['pf']}（{sig['n']}回）"
              + (f"／いまの時間帯の勝率 {sig['band_win'] * 100:.0f}%" if sig.get("band_win") is not None else ""),
              "",
              "◆ MATRIX TRADER の手順",
              f"  1) 通貨ペア {sig['code']} → 新規注文 →「IFO」",
              f"  2) 新規: {sig['side']}・上の数量・成行",
              f"  3) 決済(OCO): 指値 {sig['tp']:.{sig['dec']}f} ／ 逆指値 {sig['stop']:.{sig['dec']}f}",
              "  4) 約定したら触らない（利確か損切りで終わる）"]
    if events_note:
        lines += ["", "⚠ 重要指標が近い（値が飛びやすい・スプレッド拡大）: " + "／".join(events_note)]
    lines += ["", "※機械判定のシグナル（過去の検証に基づく想定勝率50%超）。発注するかはご本人の判断。",
              "※発注は MATRIX TRADER で本人が手動で行うこと（JFXはEA等の自動発注禁止）。"]
    subject = f"【スキャル】{sig['pair']} {sig['side']} {sig['entry']:.{sig['dec']}f}／想定勝率{sig['win'] * 100:.0f}%（{sig['tf']}足）"
    return subject, "\n".join(lines)


def grade(rec, now):
    """配信済みシグナルを15分足で採点（未決着なら None のまま）。"""
    if rec.get("result"):
        return rec
    pair = "eurusd" if rec["code"] == "EUR/USD" else "usdjpy"
    rs.MULT = rs.PAIRS[pair]["MULT"]
    m15, _ = rs._load_pair(pair)
    t_in = pd.Timestamp(rec["sent_at"])
    if t_in.tzinfo is None:
        t_in = t_in.tz_localize("Asia/Tokyo")
    bars = m15[m15.index >= t_in.floor("15min")]
    if not len(bars):
        return rec
    d, e, sl, tp = rec["d"], float(bars["Open"].iloc[0]), rec["stop"], rec["tp"]
    lim = max(1, rec["hold_min"] // 15)
    for k in range(min(lim, len(bars))):
        b = bars.iloc[k]
        if (b.Low <= sl) if d > 0 else (b.High >= sl):
            px, why = sl, "損切り"
            break
        if (b.High >= tp) if d > 0 else (b.Low <= tp):
            px, why = tp, "利確"
            break
    else:
        if len(bars) <= lim:
            return rec           # まだ決着していない
        px, why = float(bars["Close"].iloc[lim - 1]), "時間切れ"
    pips = round((px - e) * d * rs.MULT - rs.COST_PIPS, 1)
    rec.update({"fill": round(e, rec["dec"]), "exit": round(float(px), rec["dec"]), "why": why,
                "pips": pips, "result": "勝ち" if pips > 0 else "負け",
                "out_at": f"{bars.index[min(k, len(bars) - 1)]:%Y-%m-%d %H:%M}"})
    return rec


def run(dry_run=False):
    now = pd.Timestamp.now(tz="Asia/Tokyo")
    book = _load_json(SIG_JSON, {"signals": [], "sent_keys": []})
    # 既存シグナルの採点
    for rec in book["signals"]:
        try:
            grade(rec, now)
        except Exception:  # noqa: BLE001
            pass
    msgs = []
    if _market_closed(now.to_pydatetime().replace(tzinfo=None)):
        msgs.append("休場中のため判定なし")
    else:
        cfg = _cfg()
        cap = float(cfg.get("口座資金_円") or 1000000)
        ujr = _usdjpy_rate()
        for pair in ("usdjpy", "eurusd"):
            try:
                cands = [c for c in detect(pair, now) if c["win"] > WIN_MIN]
            except Exception as e:  # noqa: BLE001
                msgs.append(f"{pair}: 判定失敗 {e}")
                continue
            if not cands:
                msgs.append(f"{rs.PAIRS[pair]['名']}: 想定勝率50%超のシグナルなし")
                continue
            # 同じペア・同じ向きで、前回の配信から保有上限の時間が過ぎていないものは除く
            def _busy(c):
                for r in reversed(book["signals"]):
                    if r.get("code") == c["code"] and r.get("side") == c["side"]:
                        t0 = pd.Timestamp(r["sent_at"])
                        return now < t0 + pd.Timedelta(minutes=int(r.get("hold_min", 60)))
                return False
            cands = [c for c in cands if not _busy(c)]
            if not cands:
                msgs.append(f"{rs.PAIRS[pair]['名']}: 直前の配信の保有時間内のため見送り")
                continue
            sig = cands[0]
            key = f"{pair}|{sig['tf']}|{sig['bar']}|{sig['side']}"
            if key in book["sent_keys"]:
                msgs.append(f"{sig['pair']}: 配信済み（{key}）")
                continue
            subject, text = compose(sig, cap, ujr, _events_near(pair, now.to_pydatetime()))
            if dry_run:
                msgs.append("[dry-run] " + subject + "\n" + text)
                continue
            ok, detail = False, "未送信"
            try:
                import mailer
                ok, detail = mailer.send_signal(subject, text)
            except Exception as e:  # noqa: BLE001
                detail = str(e)
            sig.update({"sent_at": now.isoformat(timespec="minutes"), "mailed": bool(ok), "key": key,
                        "subject": subject})
            book["signals"].append(sig)
            book["sent_keys"] = (book["sent_keys"] + [key])[-500:]
            msgs.append(f"配信 {subject}（{'メール送信済み' if ok else 'メール失敗: ' + detail}）")
    book["signals"] = book["signals"][-300:]
    book["updated"] = now.isoformat(timespec="minutes")
    if not dry_run:
        os.makedirs(RES, exist_ok=True)
        with open(SIG_JSON, "w", encoding="utf-8") as f:
            json.dump(book, f, ensure_ascii=False, indent=1, default=float)
    print("\n".join(msgs))
    return msgs


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
