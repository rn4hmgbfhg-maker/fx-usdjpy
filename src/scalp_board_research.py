# -*- coding: utf-8 -*-
"""FXスキャル専用ボード 毎日の終値研究（2026-10-16 落合さん指示）

「毎日おおよそ終値の時間に研究時間を設け、1日1回確認研究して、結果を新たなマーケットでの
FXスキャルピング指標に反映する。全ての研究結果を第二の脳に溜め込んでいく」

いつ: ニューヨーク市場の終値（夏時間 6:00・冬時間 7:00 日本時間）の後、火〜土の朝に1日1回。
      signals.yml（15分ごと）の scalp_board.run() から呼ばれ、その日の研究が済んでいれば何もしない。
何を:
  ① 確認（答え合わせ）: 前日にボードが出した「今入るなら」（順位1〜3位）を、その後の15分足で
     利確・損切り・時間切れのどれになったか採点する（往復コスト込み）。
  ② 研究: 主要11ペアそれぞれで、直近60日の15分足を前半・後半に分け、
     入り方4種 × 1時間足トレンドの絞り込み有無 × 損切りATR倍率 × 利確ATR倍率 × 最長保有 を検証。
     前半・後半の両方で崩れない（小さい方のPFが高い）条件を採用する。届かなければ「優位なし」。
  ③ 時間帯: 採用した条件の、日本時間の時間帯別の勝率と平均pips。勝てる時間帯を指標に入れる。
反映: results/scalp_board/params.json → 次の15分の計算から scalp_board.analyze() が使う。
記録: research_latest.json（全文）・research_log.csv（1日1ペア1行）・research_note.md（第二の脳へ渡す要点）・
      history/<日付>.jsonl（ボードの案の控え。答え合わせ用）。第二の脳への書き込みは claude.ai の定期タスクが行う。
"""
import csv
import json
import os
from datetime import timedelta

import numpy as np
import pandas as pd

import scalp_board as sb

JST = "Asia/Tokyo"
GRID_SL = (0.75, 1.0, 1.5)
GRID_TP = (1.0, 1.5, 2.0)
GRID_HOLD = (8, 16, 32)
GRID_TREND = (True, False)
MIN_N, MIN_HALF = 30, 10          # 60日で30回以上・前半後半とも10回以上
ADOPT_PF = 1.05                   # 前半・後半の小さい方のPFがこれ以上で採用
HOUR_MIN_N = 8                    # 時間帯の判定に要る最低回数


def wilson(k, n, z=1.96):
    """勝率の95%の幅（ウィルソン法）。回数が少ないほど幅が広い＝信ぴょう性が低い"""
    if not n:
        return None, None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)


def _paths(base):
    d = os.path.join(base, "results", "scalp_board")
    os.makedirs(os.path.join(d, "history"), exist_ok=True)
    return d


def us_close_hour(now_utc):
    """ニューヨーク終値の日本時間（夏時間6時・冬時間7時）。米国夏時間＝3月第2日曜〜11月第1日曜"""
    y = now_utc.year
    mar = pd.Timestamp(y, 3, 1, tz="UTC")
    start = mar + pd.Timedelta(days=(6 - mar.weekday()) % 7 + 7, hours=7)
    nov = pd.Timestamp(y, 11, 1, tz="UTC")
    end = nov + pd.Timedelta(days=(6 - nov.weekday()) % 7, hours=6)
    return 6 if start <= now_utc < end else 7


def is_due(base, now_utc, force=False):
    """研究の時間か（火〜土・終値の後〜正午）・今日はまだか"""
    jst = now_utc.tz_convert(JST)
    today = jst.strftime("%Y-%m-%d")
    try:
        done = json.load(open(os.path.join(_paths(base), "params.json"), encoding="utf-8")).get("date")
    except (OSError, ValueError):
        done = None
    if done == today:
        return False, today
    if force:
        return True, today
    if jst.weekday() not in (1, 2, 3, 4, 5):       # 火〜土（月曜の朝は直前に終値が無い）
        return False, today
    return us_close_hour(now_utc) <= jst.hour < 12, today


# ---------------------------------------------------------------- ボードの案の控え（答え合わせ用）
def record_suggestions(base, rows, now_utc):
    """順位1〜3位の「今入るなら」を、同じ足・同じペア・同じ向きは1回だけ控える"""
    path = os.path.join(_paths(base), "history", now_utc.tz_convert(JST).strftime("%Y-%m-%d") + ".jsonl")
    seen = set()
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            try:
                x = json.loads(ln)
                seen.add((x["code"], x["bar_jst"], x["dir_sign"]))
            except (ValueError, KeyError):
                pass
    with open(path, "a", encoding="utf-8") as f:
        for r in rows[:3]:
            key = (r["code"], r["bar_jst"], r["dir_sign"])
            if key in seen:
                continue
            f.write(json.dumps({k: r.get(k) for k in (
                "code", "pair", "rank", "bar_jst", "dir_sign", "stance", "score", "entry", "sl_pips", "tp_pips",
                "cost_pips", "hold", "win", "rr", "best_method", "pip")}, ensure_ascii=False) + "\n")


def grade(base, m15_by_code, now_utc):
    """前日（日本時間）の案を、その後の15分足で採点する"""
    d = _paths(base)
    day = (now_utc.tz_convert(JST) - timedelta(days=1)).strftime("%Y-%m-%d")
    path = os.path.join(d, "history", day + ".jsonl")
    if not os.path.exists(path):
        return {"day": day, "n": 0, "note": "控えなし"}
    recs = []
    for ln in open(path, encoding="utf-8"):
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass
    out = []
    year = day[:4]
    for x in recs:
        m = m15_by_code.get(x["code"])
        if m is None or not len(m):
            continue
        t_end = pd.Timestamp(f"{year}-{x['bar_jst'].replace('/', '-')}:00", tz=JST).tz_convert("UTC")
        after = m[m.index >= t_end]                       # 案を出した足の次の足から
        hold = int(x.get("hold") or sb.HOLD)
        if len(after) < 1:
            continue
        pip, dsign = x["pip"], x["dir_sign"]
        e = float(after["Open"].iloc[0])
        stop, take = e - dsign * x["sl_pips"] * pip, e + dsign * x["tp_pips"] * pip
        res, pnl = "時間切れ", None
        for _, b in after.iloc[:hold].iterrows():
            if (b["Low"] <= stop) if dsign > 0 else (b["High"] >= stop):
                res, pnl = "損切り", -x["sl_pips"]
                break
            if (b["High"] >= take) if dsign > 0 else (b["Low"] <= take):
                res, pnl = "利確", x["tp_pips"]
                break
        if pnl is None:
            if len(after) < hold:
                res = "判定中"
                continue
            pnl = (float(after["Close"].iloc[hold - 1]) - e) * dsign / pip
        out.append({**x, "result": res, "pips": round(pnl - x["cost_pips"], 1)})
    if not out:
        return {"day": day, "n": 0, "note": "採点できる案なし"}
    gpath = os.path.join(d, "graded.jsonl")
    done_days = set()
    if os.path.exists(gpath):
        for ln in open(gpath, encoding="utf-8"):
            try:
                done_days.add(json.loads(ln).get("day"))
            except ValueError:
                pass
    if day not in done_days:
        with open(gpath, "a", encoding="utf-8") as f:
            for x in out:
                f.write(json.dumps({"day": day, **{k: x.get(k) for k in ("code", "pair", "rank", "stance", "dir_sign", "win", "result", "pips")}}, ensure_ascii=False) + "\n")
    df = pd.DataFrame(out)

    def summ(g):
        k = int((g["pips"] > 0).sum())
        lo, hi = wilson(k, len(g))
        return {"n": int(len(g)), "win": round(float((g["pips"] > 0).mean()), 3), "win_lo": lo, "win_hi": hi,
                "pred": round(float(g["win"].dropna().mean()), 3) if g["win"].notna().any() else None,
                "pips": round(float(g["pips"].sum()), 1), "tp": int((g["result"] == "利確").sum()),
                "sl": int((g["result"] == "損切り").sum()), "to": int((g["result"] == "時間切れ").sum())}
    cum = None
    try:
        allg = pd.DataFrame([json.loads(ln) for ln in open(gpath, encoding="utf-8") if ln.strip()])
        if len(allg):
            cum = {"days": int(allg["day"].nunique()), **summ(allg),
                   "by_stance": {k: summ(g) for k, g in allg.groupby("stance")}}
    except (OSError, ValueError):
        pass
    res = {"day": day, **summ(df), "cum": cum, "by_stance": {k: summ(g) for k, g in df.groupby("stance")},
           "by_pair": {k: summ(g) for k, g in df.groupby("pair")}, "rank1": summ(df[df["rank"] == 1]) if (df["rank"] == 1).any() else None}
    return res


# ---------------------------------------------------------------- 研究（条件の選び直し）
def _pf(arr):
    a = np.asarray(arr, dtype=float)
    gp, gl = a[a > 0].sum(), -a[a < 0].sum()
    return float(gp / gl) if gl > 0 else (9.99 if gp > 0 else 0.0)


def research_pair(code, name, pip, cost, m15, h1):
    m15 = sb.indicators(m15, pip)
    h1 = sb.indicators(h1, pip)
    h1_tr = np.sign(h1["ema200"].diff(4)).fillna(0)
    h1_tr.index = h1_tr.index + pd.Timedelta(hours=1)
    tr = h1_tr.reindex(m15.index, method="ffill").fillna(0)
    since = m15.index[-1] - pd.Timedelta(days=60)
    w = m15[m15.index >= since]
    trw = tr[tr.index >= since]
    mid = w.index[0] + (w.index[-1] - w.index[0]) / 2
    half_a = w.index < mid
    cands = []
    for mk in sb.METHODS:
        sig = sb.signals(w, mk)
        for t_only in GRID_TREND:
            for sl_k in GRID_SL:
                for tp_k in GRID_TP:
                    for hold in GRID_HOLD:
                        tr_list = sb.simulate(w, sig, trw, pip, cost, sl_k, tp_k, hold, t_only, detail=True)
                        if len(tr_list) < MIN_N:
                            continue
                        idx = np.array([t[0] for t in tr_list])
                        pn = np.array([t[2] for t in tr_list])
                        ina = half_a[np.minimum(idx, len(w) - 1)]
                        if ina.sum() < MIN_HALF or (~ina).sum() < MIN_HALF:
                            continue
                        pa, pb = _pf(pn[ina]), _pf(pn[~ina])
                        cands.append({"method": mk, "method_label": sb.METHODS[mk], "trend_only": t_only,
                                      "sl_k": sl_k, "tp_k": tp_k, "hold": hold, "n": int(len(pn)),
                                      "win": round(float((pn > 0).mean()), 3), "pf": round(_pf(pn), 2),
                                      "win_lo": wilson(int((pn > 0).sum()), len(pn))[0], "win_hi": wilson(int((pn > 0).sum()), len(pn))[1],
                                      "pf_a": round(pa, 2), "pf_b": round(pb, 2), "robust": round(min(pa, pb), 2),
                                      "avg": round(float(pn.mean()), 2), "_idx": idx, "_pn": pn})
    cands.sort(key=lambda c: (c["robust"], c["pf"], c["n"]), reverse=True)
    best = cands[0] if cands else None
    adopt = bool(best and best["robust"] >= ADOPT_PF and best["avg"] > 0)
    hours_tbl, good = [], []
    if best:
        jh = w.index.tz_convert(JST).hour
        hh = np.array([jh[min(i, len(w) - 1)] for i in best["_idx"]])
        for h in range(24):
            m = hh == h
            if m.sum() == 0:
                continue
            p = best["_pn"][m]
            hours_tbl.append({"hour": h, "n": int(m.sum()), "win": round(float((p > 0).mean()), 3), "avg": round(float(p.mean()), 2)})
        good = [x["hour"] for x in hours_tbl if x["n"] >= HOUR_MIN_N and x["avg"] > 0 and x["win"] >= best["win"]]
    top = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands[:5]]
    pick = {k: v for k, v in best.items() if not k.startswith("_")} if best else None
    return {"code": code, "pair": name, "adopt": adopt, "pick": pick, "top5": top,
            "tested": len(cands), "hours": hours_tbl, "good_hours": good,
            "atr_now": round(float(m15["atr"].iloc[-1]), 1), "cost": cost}


# ---------------------------------------------------------------- まとめ・記録
def _hour_label(hs):
    if not hs:
        return "—"
    hs = sorted(hs)
    out, s, p = [], hs[0], hs[0]
    for h in hs[1:] + [None]:
        if h is not None and h == p + 1:
            p = h
            continue
        out.append(f"{s}時台" if s == p else f"{s}〜{p}時台")
        if h is not None:
            s = p = h
    return "・".join(out)


def note_md(today, at_jst, graderes, results, prev):
    L = [f"# FXスキャル 終値研究 {today}", "",
         f"- 実行: {at_jst}（日本時間・ニューヨーク終値の後）／対象 {len(results)} ペア・直近60日の15分足",
         f"- 決まり: 前半・後半とも崩れない条件（小さい方のPF {ADOPT_PF} 以上・60日で{MIN_N}回以上）を採用。届かないペアは「優位なし」＝その日は入る候補にしない", ""]
    L.append("## ① 前日のボードの案の答え合わせ")
    g = graderes or {}
    if g.get("n"):
        L.append(f"- {g['day']}: 案 {g['n']} 件 → 勝率 {round(g['win']*100)}%（95%の幅 {round((g['win_lo'] or 0)*100)}〜{round((g['win_hi'] or 0)*100)}%）・合計 {g['pips']:+.1f} pips（利確 {g['tp']}・損切り {g['sl']}・時間切れ {g['to']}）"
                 + (f"・ボードが示した想定勝率の平均 {round(g['pred']*100)}%" if g.get("pred") is not None else ""))
        c = g.get("cum")
        if c:
            L.append(f"- 累計（{c['days']}日）: 案 {c['n']} 件 → 実際の勝率 {round(c['win']*100)}%（95%の幅 {round((c['win_lo'] or 0)*100)}〜{round((c['win_hi'] or 0)*100)}%）・合計 {c['pips']:+.1f} pips"
                     + (f"・想定勝率の平均 {round(c['pred']*100)}%（{'幅の中＝想定は外れていない' if c['pred'] is not None and c['win_lo'] <= c['pred'] <= c['win_hi'] else '幅の外＝想定がずれている'}）" if c.get("pred") is not None else ""))
        for k, v in (g.get("by_stance") or {}).items():
            L.append(f"  - {k}: {v['n']}件・勝率 {round(v['win']*100)}%・{v['pips']:+.1f} pips")
        if g.get("rank1"):
            v = g["rank1"]
            L.append(f"  - 1位の案だけ: {v['n']}件・勝率 {round(v['win']*100)}%・{v['pips']:+.1f} pips")
    else:
        L.append(f"- {g.get('day', '前日')}: {g.get('note', '採点なし')}")
    L += ["", "## ② 今日から使う条件（ペア別）", "",
          "| ペア | 採用 | 入り方 | トレンド絞り | 損切り | 利確 | 最長 | 勝率（95%の幅） | PF（前半/後半） | 回数 | 勝てる時間帯 | 前日からの変化 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        p = r["pick"]
        pv = (prev or {}).get(r["code"]) or {}
        if p:
            chg = []
            if pv:
                if pv.get("method") != p["method"]:
                    chg.append(f"入り方 {sb.METHODS.get(pv.get('method'), '—')}→{p['method_label']}")
                for k, lab in (("sl_k", "損切り"), ("tp_k", "利確"), ("hold", "最長"), ("trend_only", "絞り")):
                    if pv.get(k) != p[k]:
                        chg.append(f"{lab} {pv.get(k)}→{p[k]}")
                if bool(pv.get("adopt")) != r["adopt"]:
                    chg.append("採用→優位なし" if pv.get("adopt") else "優位なし→採用")
            L.append(f"| {r['pair']} | {'採用' if r['adopt'] else '優位なし'} | {p['method_label']} | {'あり' if p['trend_only'] else 'なし'} | ATR×{p['sl_k']} | ATR×{p['tp_k']} | {p['hold']}本 | {round(p['win']*100)}%（{round(p['win_lo']*100)}〜{round(p['win_hi']*100)}%） | {p['pf']}（{p['pf_a']}/{p['pf_b']}） | {p['n']} | {_hour_label(r['good_hours'])} | {'、'.join(chg) if chg else ('初回' if not pv else '変化なし')} |")
        else:
            L.append(f"| {r['pair']} | 優位なし | — | — | — | — | — | — | — | — | — | 検証できる条件なし |")
    ad = [r for r in results if r["adopt"]]
    L += ["", "## ③ 要点", f"- 採用 {len(ad)} ペア／優位なし {len(results) - len(ad)} ペア"]
    if ad:
        b = max(ad, key=lambda r: r["pick"]["robust"])
        L.append(f"- いちばん崩れにくいのは {b['pair']}（{b['pick']['method_label']}・前半後半の小さい方のPF {b['pick']['robust']}）")
    L.append("- 反映: 今日の15分ごとの計算から、ボードの想定勝率・損切り・利確・最長保有はペアごとにこの条件で出す（スコアは研究の入り方が同じ向きで点灯中なら+4、勝てる時間帯なら+3）")
    return "\n".join(L) + "\n"


def paper_md(p):
    def line(x, label):
        if not x or not x.get("n"):
            return f"- {label}: まだ記録なし"
        return (f"- {label}: {x['n']}回・勝率 {round((x.get('win') or 0) * 100)}%・{x.get('pips', 0):+.1f} pips"
                + (f"（1万通貨で {x['yen']:+,}円）" if x.get("yen") is not None else "") + (f"・PF {x['pf']}" if x.get("pf") else "")
                + (f"・想定勝率の平均 {round(x['pred'] * 100)}%" if x.get("pred") else ""))
    out = ["", "## ④ 自己実行（紙の上の売買）と月次の守り", ""]
    out.append(line(p.get("month"), f"今月（{(p.get('month') or {}).get('ym', '')}）ボードの表示どおりに入った場合"))
    out.append(line(p.get("shadow30"), "直近30日・守りを掛けない場合（仕組みの実力）"))
    if p.get("init"):
        out.append(line(p.get("init_follow"), "初期化（前40日で条件を選び、後20日で試験）"))
    g = p.get("guard") or {}
    out.append(f"- 守りの状態: **{g.get('state', '通常')}**（{g.get('reason', '')}）")
    out.append(f"- 想定勝率の補正係数: {p.get('calib', 1.0)}（実際の勝率÷ボードの想定勝率）")
    ok = [c for c, v in (p.get("gate") or {}).items() if v.get("ok")]
    out.append(f"- 入る候補にできるペア（通行証）: {'・'.join(ok) if ok else 'なし（自己実行の記録がそろうまで入る候補は出ません）'}")
    for k, v in (p.get("rules") or {}).items():
        out.append(f"  - 決まり（{k}）: {v}")
    return "\n".join(out) + "\n"


def run(m15_by_code, h1_by_code, now_utc, base=None, force=False):
    base = base or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    due, today = is_due(base, now_utc, force)
    if not due:
        return None
    d = _paths(base)
    at_jst = now_utc.tz_convert(JST).strftime("%Y-%m-%d %H:%M")
    try:
        prev = json.load(open(os.path.join(d, "params.json"), encoding="utf-8")).get("pairs", {})
    except (OSError, ValueError):
        prev = {}
    graderes = grade(base, m15_by_code, now_utc)
    results = []
    for code, name, _sym, pip, cost, _note in sb.PAIRS:
        if code not in m15_by_code or code not in h1_by_code:
            continue
        try:
            results.append(research_pair(code, name, pip, cost, m15_by_code[code], h1_by_code[code]))
        except Exception as e:  # noqa: BLE001
            print(f"終値研究 {name} 失敗: {e}")
    if not results:
        return None
    params = {"date": today, "at": at_jst, "pairs": {}}
    for r in results:
        p = r["pick"]
        params["pairs"][r["code"]] = ({"adopt": r["adopt"], "method": p["method"], "sl_k": p["sl_k"], "tp_k": p["tp_k"],
                                       "hold": p["hold"], "trend_only": p["trend_only"], "good_hours": r["good_hours"] if r["adopt"] else [],
                                       "win": p["win"], "win_lo": p["win_lo"], "win_hi": p["win_hi"], "pf": p["pf"], "robust": p["robust"], "n": p["n"], "date": today}
                                      if p else {"adopt": False, "date": today, "good_hours": []})
    latest = {"date": today, "at": at_jst, "grade": graderes, "results": results,
              "rules": {"grid": {"sl": GRID_SL, "tp": GRID_TP, "hold": GRID_HOLD, "trend": GRID_TREND},
                        "adopt_pf": ADOPT_PF, "min_n": MIN_N, "rr_min": sb.RR_MIN}}
    # 2026-10-20 自己実行（紙の上の売買）と自己解析：通行証・補正係数・月次の守りを作り、今日の条件に載せる
    paper = None
    try:
        import scalp_board_paper as sp
        paper = sp.run(m15_by_code, h1_by_code, params["pairs"], now_utc, d, research_fn=research_pair)
        params["gate"] = paper.get("gate") or {}
        params["calib"] = paper.get("calib", 1.0)
        params["guard"] = paper.get("guard") or {"state": "通常"}
        latest["paper"] = {k: paper.get(k) for k in ("at", "rules", "gate", "calib", "guard", "month", "by_month",
                                                       "shadow30", "shadow_all", "follow_all", "init", "init_follow", "last_trades")}
    except Exception as e:  # noqa: BLE001
        print(f"自己実行 失敗（守りなしで続行）: {e}")
    note = note_md(today, at_jst, graderes, results, prev)
    if paper:
        note += paper_md(paper)
    json.dump(params, open(os.path.join(d, "params.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(latest, open(os.path.join(d, "research_latest.json"), "w", encoding="utf-8"), ensure_ascii=False)
    open(os.path.join(d, "research_note.md"), "w", encoding="utf-8").write(note)
    logp = os.path.join(d, "research_log.csv")
    new = not os.path.exists(logp)
    with open(logp, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日付", "ペア", "採用", "入り方", "トレンド絞り", "損切りATR", "利確ATR", "最長本数", "勝率", "PF", "PF前半", "PF後半", "回数", "勝てる時間帯", "前日の案の勝率", "前日の案のpips"])
        for r in results:
            p = r["pick"] or {}
            w.writerow([today, r["pair"], int(r["adopt"]), p.get("method_label", ""), p.get("trend_only", ""), p.get("sl_k", ""),
                        p.get("tp_k", ""), p.get("hold", ""), p.get("win", ""), p.get("pf", ""), p.get("pf_a", ""), p.get("pf_b", ""),
                        p.get("n", ""), _hour_label(r["good_hours"]),
                        (graderes.get("by_pair", {}).get(r["pair"]) or {}).get("win", ""),
                        (graderes.get("by_pair", {}).get(r["pair"]) or {}).get("pips", "")])
    print(f"終値研究 {today}: 採用 {sum(r['adopt'] for r in results)}/{len(results)} ペア・前日の案 {graderes.get('n', 0)} 件")
    return params
