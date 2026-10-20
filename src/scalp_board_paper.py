"""FXスキャル専用ボード：自己実行（紙の上の売買）と自己解析・月次の守り。

2026-10-20 落合さんの依頼：「日々1日一回適切な時間にて、スキャルピングの自己実行及び自己解析を行い、研究開発して、
このアプリにて毎月最小限の利益が出るようにする。少なくとも赤字にはしないような指標を提供する。
ただし毎回、現在もっともポジションを取るべき通貨ペアを今より精度をあげて提供する」

やること（毎日1回・ニューヨーク終値の後の終値研究と同じ回）
1. 自己実行：前回の続きから今までの15分足を1本ずつ進め、「その時点でボードが出していたはずの順位」を作り直して、
   1位が「入る候補」のときだけ入る、という決まりで紙の上の売買をする（同時に持つのは1つだけ。発注はしない）。
   - 影の記録（shadow）：研究の条件だけで判定した1位に入った場合（守りの決まりを掛けない）。仕組みの実力を測る物差し。
   - 実際の記録（follow）：守りの決まりも掛けた、ボードの表示どおりに入った場合。＝このアプリの月次成績。
2. 自己解析：影の記録から
   - ペアごとの通行証（直近30日の影の記録が10回以上・合計プラス・PF1.1以上のペアだけ「入る候補」にできる）
   - 想定勝率の補正係数（実際の勝率÷ボードが示した勝率。0.7〜1.05）
   を作る。
3. 月次の守り：今月の実際の記録が
   - マイナス → 慎重（期待値0.15R以上だけ入る候補）
   - −40pips以下 → 停止（その月は入る候補を出さない。影の記録は続け、通行証の判定は毎日更新）
4. 最初の1回だけ「初期化」：直近60日のうち前40日で条件を選び、後20日で自己実行する（後から当てはめない＝本当の試験）。

結果は results/scalp_board/paper.jsonl（取引の控え）・paper_state.json（通行証・補正・守り・月次）に書き、
params.json の gate/calib/guard と research_latest.json の paper にも載せる。
"""
import json
import os

import numpy as np
import pandas as pd

import scalp_board as sb

JST = "Asia/Tokyo"
PRIOR_A = 20            # 想定勝率を損益分岐の勝率へ引き寄せる強さ（回数の少ない検証ほど控えめにする）
GATE_DAYS, GATE_N, GATE_PF = 30, 10, 1.1
CALIB_N, CALIB_LO, CALIB_HI = 30, 0.70, 1.05
GUARD_CAUTION_EVR, GUARD_STOP_PIPS = 0.15, -40.0
METHOD_KEYS = list(sb.METHODS)


# ---------------------------------------------------------------- 共通の式（analyze() とボードのJSも同じ式）
def breakeven(tp_p, sl_p, cost):
    a, b = tp_p - cost, sl_p + cost
    return b / (a + b) if a + b > 0 else 1.0


def win_adjusted(win, n, be, calib=1.0):
    """検証の勝率を、回数に応じて損益分岐の勝率へ引き寄せ（縮小推定）、実際の成績で補正する"""
    if win is None or not n:
        return None
    w = (win * n + PRIOR_A * be) / (n + PRIOR_A)
    return float(np.clip(w * (calib if calib else 1.0), 0.02, 0.98))


def stance_of(align, fire, ev_r, gate_ok, guard):
    """2026-10-20 改め：検証した入り方（研究の入り方の点灯＋トレンド絞り）が今の足で点灯し、補正後の期待値がプラスのときだけ入る候補。
    （以前は「そろい度0.6以上・勝率5割超」だけで、検証した入り方と違う場面でも入る候補になり、想定勝率52%に対し実際30%だった）"""
    pre = bool(fire) and (ev_r or 0) > 0
    st = "入る候補" if pre else ("条件待ち" if align >= 0.35 else "様子見寄り")
    if st == "入る候補":
        if gate_ok is False or guard == "停止" or (guard == "慎重" and (ev_r or 0) < GUARD_CAUTION_EVR):
            st = "条件待ち"
    return pre, st


# ---------------------------------------------------------------- 15分足ごとの「その時点のボード」
def board_frame(code, pip, cost, m15, h1, rp, calib=1.0, gate_ok=None, guard="通常", act_by_hour=None):
    """m15・h1（UTCの足の始まり）から、各15分足の確定時点でボードが出す判定を作る（analyze() と同じ式）。"""
    M = sb.indicators(m15, pip)
    H = sb.indicators(h1, pip)
    end15 = M.index + pd.Timedelta(minutes=15)
    hidx = H.index + pd.Timedelta(hours=1)          # 1時間足は確定してから使う

    def h_at(series):
        s = pd.Series(series.values, index=hidx)
        return s.reindex(end15, method="ffill").values

    slope = np.sign(h_at(H["ema200"] - H["ema200"].shift(4)))
    above = np.where(h_at(H["Close"]) > h_at(H["ema200"]), 1.0, -1.0)
    t1h = 0.6 * np.nan_to_num(slope) + 0.4 * above
    e15 = np.where(M["ema20"] > M["ema50"], 1.0, -1.0)
    atr = M["atr"].values
    mom = np.where(atr > 0, (M["Close"] - M["Close"].shift(4)).values / pip / atr, 0.0)
    comp = 0.5 * t1h + 0.3 * e15 + 0.2 * np.clip(np.nan_to_num(mom), -1, 1)
    d = np.where(comp >= 0, 1, -1)
    align = np.abs(comp)
    # 検証（simulate）と同じトレンド：1時間足EMA200の4本の傾き（足の始まりの時点で確定済みのもの）
    tr_sim = pd.Series(np.sign((H["ema200"] - H["ema200"].shift(4)).values), index=hidx).reindex(M.index, method="ffill").fillna(0).values
    s15 = {k: sb.signals(M, k).values for k in METHOD_KEYS}
    s1h = {k: h_at(sb.signals(H, k).astype(float)) for k in METHOD_KEYS}
    fw = sum(((s15[k] == d) | (s1h[k] == d)).astype(int) for k in METHOD_KEYS)
    fa = sum(((s15[k] == -d) | (s1h[k] == -d)).astype(int) for k in METHOD_KEYS)
    use_rp = bool(rp and rp.get("adopt"))
    sl_k = rp["sl_k"] if use_rp else sb.SL_K
    tp_k = rp["tp_k"] if use_rp else sb.TP_K
    hold = int(rp["hold"]) if use_rp else sb.HOLD
    rp_same = (s15[rp["method"]] == d) if use_rp else np.zeros(len(M), bool)
    if use_rp:
        sig = s15[rp["method"]]
        fire = (sig != 0) & ((not rp.get("trend_only", True)) | (tr_sim == sig))
        d = np.where(fire, sig, d)          # 点灯した入り方の向きで入る（検証と同じ）
    else:
        fire = np.zeros(len(M), bool)
    win = rp.get("win") if use_rp else None
    n = rp.get("n") if use_rp else None
    pf = (rp.get("pf") or 0.0) if use_rp else 0.0
    hours_jst = end15.tz_convert(JST).hour
    rng = M["range"]
    if act_by_hour is None:
        by_h = rng.groupby(M.index.tz_convert(JST).hour).mean()
        overall = float(rng.mean()) or 1.0
        act_by_hour = {int(h): float(v / overall) for h, v in by_h.items()}
    act = np.array([act_by_hour.get(int(h), 1.0) for h in hours_jst])
    good = set((rp or {}).get("good_hours") or [])
    in_good = np.array([int(h) in good for h in hours_jst]) if good else np.zeros(len(M), bool)
    cost_ratio = np.where(atr > 0, cost / np.where(atr > 0, atr, 1), 1.0)
    score = (30 * align + 25 * np.clip((pf - 0.8) / 0.7, 0, 1)
             + 20 * np.clip(1 - np.abs(np.log(np.maximum(np.nan_to_num(atr, nan=0.1), 0.1) / 10.0)) / 1.2, 0, 1)
             + 15 * np.clip(1 - cost_ratio / 0.4, 0, 1) + 10 * np.clip((act - 0.5) / 1.0, 0, 1))
    score = score + 3 * (fw > 0) - 3 * (fa > 0) + 3 * in_good + 4 * rp_same
    score = np.clip(score, 0, 100)
    sl_p = sl_k * atr
    tp_p = np.maximum(tp_k * atr, sb.RR_MIN * (sl_p + cost) + cost)
    out = pd.DataFrame({"code": code, "d": d, "fire": fire, "align": align, "score": score, "sl_p": sl_p, "tp_p": tp_p,
                        "hold": hold, "atr": atr}, index=M.index)
    wr, wa, evr, pre, st = [], [], [], [], []
    for i in range(len(M)):
        be = breakeven(tp_p[i], sl_p[i], cost)
        w = win_adjusted(win, n, be, calib)
        e = (w * (tp_p[i] - cost) - (1 - w) * (sl_p[i] + cost)) / (sl_p[i] + cost) if w is not None and sl_p[i] + cost > 0 else None
        p_, s_ = stance_of(align[i], fire[i], e, gate_ok, guard)
        wa.append(w); evr.append(e); pre.append(p_); st.append(s_)
    out["win_adj"], out["ev_r"], out["pre"], out["stance"] = wa, evr, pre, st
    out["valid"] = np.isfinite(atr) & (atr > 0)
    return out


def _rank1(rows):
    """sb.rank_key と同じ並び（入る候補→期待値プラス→判定→期待値→スコア）"""
    best, bk = None, None
    for r in rows:
        ev = r["ev_r"]
        k = (0 if r["stance"] == "入る候補" else 1, 0 if (ev is not None and ev > 0) else 1,
             sb.STANCE_ORDER.get(r["stance"], 3), -(ev if ev is not None else -9), -r["score"])
        if bk is None or k < bk:
            best, bk = r, k
    return best


def _exit(m15, i, d, sl_p, tp_p, hold, pip):
    """i 本目の確定で入ると決め、次の足の始値で入る。戻り値 (損益pips, 出た足の位置, 結果) か None（未決着）"""
    o, h, l, c = (m15[k].values for k in ("Open", "High", "Low", "Close"))
    if i + 1 >= len(o):
        return None
    e = o[i + 1]
    stop, take = e - d * sl_p * pip, e + d * tp_p * pip
    last = min(i + hold, len(o) - 1)
    for j in range(i + 1, min(i + 1 + hold, len(o))):
        if (l[j] <= stop) if d > 0 else (h[j] >= stop):
            return -sl_p, j, "損切り", e
        if (h[j] >= take) if d > 0 else (l[j] <= take):
            return tp_p, j, "利確", e
    if i + hold >= len(o):
        return None
    return (c[last] - e) * d / pip, last, "時間切れ", e


def replay(m15_by_code, h1_by_code, params_pairs, start_utc, end_utc, state, label):
    """start〜end の15分足を順に進めて、影の記録と実際の記録の取引を返す"""
    frames, pinfo = {}, {p[0]: p for p in sb.PAIRS}
    gate = (state or {}).get("gate") or {}
    calib = (state or {}).get("calib", 1.0)
    guard = ((state or {}).get("guard") or {}).get("state", "通常")
    for code, m15 in m15_by_code.items():
        if code not in h1_by_code or code not in pinfo:
            continue
        _c, name, _s, pip, cost, _n = pinfo[code]
        rp = (params_pairs or {}).get(code)
        g = gate.get(code)
        frames[code] = (board_frame(code, pip, cost, m15, h1_by_code[code], rp, calib,
                                    None if g is None else bool(g.get("ok")), guard), m15, pip, cost, name)
    times = sorted({t for f in frames.values() for t in f[0].index if start_utc <= t + pd.Timedelta(minutes=15) <= end_utc})
    trades = []
    busy = {"shadow": None, "follow": None}      # 持っている間は次を取らない（出た足の終わりの時刻）
    pos = {c: {t: i for i, t in enumerate(f[0].index)} for c, f in frames.items()}
    for t in times:
        rows = []
        for c, (fr, m15, pip, cost, name) in frames.items():
            i = pos[c].get(t)
            if i is None or not fr["valid"].iat[i]:
                continue
            rows.append({"code": c, "i": i, "stance": fr["stance"].iat[i], "pre": bool(fr["pre"].iat[i]),
                         "ev_r": fr["ev_r"].iat[i], "score": float(fr["score"].iat[i])})
        if not rows:
            continue
        for book in ("shadow", "follow"):
            if busy[book] is not None and t < busy[book]:
                continue
            if book == "shadow":
                cand = [dict(r, stance="入る候補" if r["pre"] else ("条件待ち" if r["stance"] == "入る候補" else r["stance"])) for r in rows]
            else:
                cand = rows
            top = _rank1(cand)
            if top is None or top["stance"] != "入る候補":
                continue
            fr, m15, pip, cost, name = frames[top["code"]]
            i = top["i"]
            res = _exit(m15, i, int(fr["d"].iat[i]), float(fr["sl_p"].iat[i]), float(fr["tp_p"].iat[i]), int(fr["hold"].iat[i]), pip)
            if res is None:
                continue
            pn, j, kind, e = res
            busy[book] = m15.index[j] + pd.Timedelta(minutes=15)
            trades.append({"book": book, "label": label, "code": top["code"], "pair": name,
                           "signal_end": (t + pd.Timedelta(minutes=15)).tz_convert(JST).strftime("%Y-%m-%d %H:%M"),
                           "exit_end": busy[book].tz_convert(JST).strftime("%Y-%m-%d %H:%M"),
                           "dir_sign": int(fr["d"].iat[i]), "entry": round(float(e), 5), "sl_pips": round(float(fr["sl_p"].iat[i]), 1),
                           "tp_pips": round(float(fr["tp_p"].iat[i]), 1), "hold": int(fr["hold"].iat[i]),
                           "win_pred": None if fr["win_adj"].iat[i] is None else round(float(fr["win_adj"].iat[i]), 3),
                           "ev_r": None if fr["ev_r"].iat[i] is None else round(float(fr["ev_r"].iat[i]), 3),
                           "result": kind, "pips": round(float(pn - cost), 1), "cost": cost})
    return trades


# ---------------------------------------------------------------- 円換算（1万通貨あたり）
def yen_per_pip(code, last_close):
    q = code[3:]
    if q == "JPY":
        return 100.0
    conv = last_close.get(q + "JPY")
    return 10000 * 0.0001 * conv if conv else None


# ---------------------------------------------------------------- 状態（通行証・補正・守り・月次）
def summarize(trades, now_utc, last_close):
    df = pd.DataFrame(trades)
    st = {"at": now_utc.tz_convert(JST).strftime("%Y-%m-%d %H:%M"), "rules": {
        "gate": f"直近{GATE_DAYS}日の影の記録が{GATE_N}回以上・合計プラス・PF{GATE_PF}以上のペアだけ入る候補",
        "calib": f"直近{GATE_DAYS}日の影の記録が{CALIB_N}回以上で、実際の勝率÷想定勝率（回数で1.0へ引き寄せ・{CALIB_LO}〜{CALIB_HI}）を想定勝率に掛ける",
        "guard": f"今月の実際の記録がマイナス→慎重（期待値{GUARD_CAUTION_EVR}R以上だけ）／{GUARD_STOP_PIPS:.0f}pips以下→停止（その月は入る候補なし）",
        "prior": f"想定勝率は検証回数に応じて損益分岐の勝率へ引き寄せる（強さ{PRIOR_A}回分）"}}
    if df.empty:
        st.update({"gate": {}, "calib": 1.0, "guard": {"state": "通常", "reason": "記録なし"}, "month": None, "shadow30": None})
        return st
    df["end"] = pd.to_datetime(df["exit_end"]).dt.tz_localize(JST)
    df["yen"] = [p * (yen_per_pip(c, last_close) or np.nan) for p, c in zip(df["pips"], df["code"])]
    now_j = now_utc.tz_convert(JST)
    sh = df[df["book"] == "shadow"]
    recent = sh[sh["end"] >= now_j - pd.Timedelta(days=GATE_DAYS)]

    def s(g):
        if g.empty:
            return {"n": 0}
        gp, gl = g["pips"][g["pips"] > 0].sum(), -g["pips"][g["pips"] < 0].sum()
        return {"n": int(len(g)), "win": round(float((g["pips"] > 0).mean()), 3), "pips": round(float(g["pips"].sum()), 1),
                "yen": None if g["yen"].isna().all() else int(round(float(g["yen"].sum()))),
                "pf": round(float(gp / gl), 2) if gl > 0 else None,
                "pred": round(float(g["win_pred"].dropna().mean()), 3) if g["win_pred"].notna().any() else None}
    gate = {}
    for code, _n, _s, _p, _c, _x in sb.PAIRS:
        g = s(recent[recent["code"] == code])
        ok = g["n"] >= GATE_N and g.get("pips", 0) > 0 and (g.get("pf") or 0) >= GATE_PF
        gate[code] = {**g, "ok": bool(ok)}
    calib = 1.0
    if len(recent) >= CALIB_N and recent["win_pred"].notna().any():
        pred = float(recent["win_pred"].dropna().mean())
        act = float((recent["pips"] > 0).mean())
        n = len(recent)
        act_s = (act * n + pred * CALIB_N) / (n + CALIB_N)     # 回数が少ないうちは1.0寄りにする
        calib = float(np.clip(act_s / pred, CALIB_LO, CALIB_HI)) if pred > 0 else 1.0
    fo = df[(df["book"] == "follow") & (df["end"].dt.strftime("%Y-%m") == now_j.strftime("%Y-%m")) & (df["label"] == "本番")]
    month = {"ym": now_j.strftime("%Y-%m"), **s(fo)}
    mp = month.get("pips", 0) or 0
    if mp <= GUARD_STOP_PIPS:
        guard = {"state": "停止", "reason": f"今月の記録が {mp:+.1f} pips（{GUARD_STOP_PIPS:.0f}以下）のため、月末まで入る候補を出しません"}
    elif mp < 0:
        guard = {"state": "慎重", "reason": f"今月の記録が {mp:+.1f} pips のため、期待値{GUARD_CAUTION_EVR}R以上の案だけ入る候補にします"}
    else:
        guard = {"state": "通常", "reason": f"今月の記録 {mp:+.1f} pips"}
    by_month = []
    fol = df[(df["book"] == "follow") & (df["label"] == "本番")]
    for ym, g in fol.groupby(fol["end"].dt.strftime("%Y-%m")):
        by_month.append({"ym": ym, **s(g)})
    st.update({"gate": gate, "calib": round(calib, 3), "guard": guard, "month": month, "by_month": by_month,
               "shadow30": s(recent), "shadow_all": s(sh), "follow_all": s(fol),
               "init": s(df[df["label"] == "初期化"]) if (df["label"] == "初期化").any() else None,
               "init_follow": s(df[(df["label"] == "初期化") & (df["book"] == "follow")]) if (df["label"] == "初期化").any() else None,
               "last_trades": df.sort_values("end").tail(12).drop(columns=["end"]).to_dict("records")})
    return st


def _load(path):
    out = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def run(m15_by_code, h1_by_code, params_pairs, now_utc, base_dir, research_fn=None):
    """毎日1回（終値研究の回）に呼ぶ。初回は初期化（前40日で条件を選び後20日で自己実行）を行う。"""
    path = os.path.join(base_dir, "paper.jsonl")
    spath = os.path.join(base_dir, "paper_state.json")
    trades = _load(path)
    try:
        state = json.load(open(spath, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    new = []
    if not trades and research_fn is not None:
        cut = min(m.index[-1] for m in m15_by_code.values()) - pd.Timedelta(days=20)
        m40 = {c: m[m.index < cut] for c, m in m15_by_code.items()}
        h40 = {c: h[h.index < cut] for c, h in h1_by_code.items()}
        init_params = {}
        for code, name, _s, pip, cost, _n in sb.PAIRS:
            if code in m40 and len(m40[code]) > 500:
                try:
                    r = research_fn(code, name, pip, cost, m40[code], h40[code])
                    p = r["pick"]
                    if p:
                        init_params[code] = {"adopt": r["adopt"], "method": p["method"], "sl_k": p["sl_k"], "tp_k": p["tp_k"],
                                             "hold": p["hold"], "trend_only": p["trend_only"],
                                             "good_hours": r["good_hours"] if r["adopt"] else [], "win": p["win"], "n": p["n"], "pf": p["pf"]}
                except Exception as e:  # noqa: BLE001
                    print(f"自己実行の初期化 {name} 失敗: {e}")
        new = replay(m15_by_code, h1_by_code, init_params, cut, now_utc, {"guard": {"state": "通常"}}, "初期化")
        start = now_utc
    else:
        start = now_utc - pd.Timedelta(days=3)      # 3日分を進め直し、控え済み・持ち中と重なる取引は捨てる
    if start < now_utc:
        new += replay(m15_by_code, h1_by_code, params_pairs, start, now_utc, state, "本番")
    seen = {(t["book"], t["code"], t["signal_end"]) for t in trades}
    busy_until = {}
    for t in trades:
        busy_until[t["book"]] = max(busy_until.get(t["book"], ""), t["exit_end"])
    add = []
    for t in sorted(new, key=lambda x: x["signal_end"]):
        if (t["book"], t["code"], t["signal_end"]) in seen or t["signal_end"] < busy_until.get(t["book"], ""):
            continue
        add.append(t); busy_until[t["book"]] = t["exit_end"]
    with open(path, "a", encoding="utf-8") as f:
        for t in add:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    trades += add
    last_close = {c: float(m["Close"].iloc[-1]) for c, m in m15_by_code.items()}
    st = summarize(trades, now_utc, last_close)
    json.dump(st, open(spath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"自己実行: 追加 {len(add)} 件・今月 {((st.get('month') or {}).get('pips'))} pips・守り {st['guard']['state']}・補正 {st['calib']}")
    return st
