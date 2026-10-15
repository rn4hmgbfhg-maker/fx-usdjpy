# -*- coding: utf-8 -*-
"""ユーロ円研究（2026-10-15 落合さん指示「ユーロ円も通貨取引に入れたい」で新設）

ユーロドル研究（research_eurusd.py）と同じ作りで、3本立て:
  ① 日足の売買ルール探索（型ごと: 押し目・戻り＝BB逆張り／移動平均クロス／ドンチャン）
     ・前半(〜2018)・後半(2019〜)のPFの小さい方をスコアにし、近い設定（周辺）の平均スコアでも
       頑健さを確かめる。参考に、ドル円の現行パラメータをそのまま当てた成績も並べる。
  ② ドル円・ユーロドルとの関係（ユーロ円＝ドル円×ユーロドル）
     ・日次の相関（ユーロ円とドル円・ユーロ円とユーロドル）。
     ・ユーロ円の動きの主役: 円主導（ドル円が主に動いた）／ユーロ主導（ユーロドルが主に動いた）／
       円・ユーロ同時（両方が同じ向き＝ユーロ円が大きく動く）／打ち消し（ドルだけ動いた）。
     ・1時間足の先行・遅行。
  ③ 相乗効果の検証（ドル円の日足2システムを「ユーロ円も同じ向き＝円全体が動いている」時だけに
     絞ると成績は上がるか）。採用は落合さん判断。

結果: results/research_eurjpy_latest.json（ボードが読む）／research_eurjpy_log.csv
使い方: python3 src/research_eurjpy.py            # 研究のみ（config は変えない）
        python3 src/research_eurjpy.py --adopt    # 選ばれたルールを config.json「ユーロ円」へ書く（落合さん承認後のみ）
"""
import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from strategies import donchian, make_signal  # noqa: E402
import data_fetch_eurjpy as dfj  # noqa: E402
import data_fetch_eurusd as dfe  # noqa: E402
import research_eurusd as reu  # noqa: E402  型の候補・ラベル・NYクローズ補正を共用

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731
OUT_JSON = R("results", "research_eurjpy_latest.json")
LOG_CSV = R("results", "research_eurjpy_log.csv")
CONFIG = R("config.json")
SPLIT = pd.Timestamp("2018-12-31").date()
COST = 0.006              # 片道0.6銭（JFXのユーロ円スプレッド0.4〜0.5銭の半分＋滑り。ドル円は0.4銭）
MIN_TRADES_FAMILY = 60
ADOPT_SCORE = 1.10        # 採用の下限（前半・後半とも PF 1.10 以上）
FAMILY_GRID = dict(reu.FAMILY_GRID)
# ドル円で使っている長め・短めのドンチャンも候補に入れる（ユーロ円はドル円と同じ円の通貨なので）
FAMILY_GRID["ドンチャン"] = [{"戦略": "ドンチャン", "エントリー期間": n, "イグジット期間": x, "ATRストップ係数": k}
                         for n in (20, 25, 40, 55, 70) for x in (8, 10, 20) for k in (1.5, 2.5, 3.5) if x < n]


def _family_metrics(px, scfg, risk=0.01, sig=None):
    d = pd.to_datetime(px["Date"]).dt.date
    out = {}
    for key, sub in (("全", px), ("前", px[d <= SPLIT]), ("後", px[d > SPLIT])):
        sub = sub.reset_index(drop=True)
        bt = Backtester(sub, risk_per_trade=risk, max_leverage=1.5,
                        atr_stop_mult=float(scfg["ATRストップ係数"]), cost_per_side=COST)
        s = make_signal(sub, scfg) if sig is None else sig(sub)
        out[key] = bt.run(s)["metrics"]
    a, b = out["前"]["プロフィットファクター"], out["後"]["プロフィットファクター"]
    m = out["全"]
    return {"スコア": round(float(min(a, b)), 3) if a == a and b == b else 0.0,
            "PF全": round(float(m["プロフィットファクター"]), 2), "PF前": round(float(a), 2),
            "PF後": round(float(b), 2), "勝率": round(float(m["勝率"]), 3),
            "最大DD": round(float(m["最大ドローダウン"]), 3), "取引数": int(m["取引回数"]),
            "総リターン": round(float(m["総リターン"]), 3),
            "年率": round(float(m["年率リターン(CAGR)"]), 4)}


def search_families(px):
    res = {}
    for fam, grid in FAMILY_GRID.items():
        rows = []
        for scfg in grid:
            m = _family_metrics(px, scfg)
            if m["取引数"] >= MIN_TRADES_FAMILY:
                rows.append({"設定": scfg, **m})
        rows.sort(key=lambda r: (r["スコア"], r["PF全"]), reverse=True)
        top = rows[:5]
        if top:
            b = top[0]["設定"]
            keys = [k for k in b if k not in ("戦略", "ATRストップ係数")]
            nb = [r["スコア"] for r in rows if r["設定"] is not b and
                  sum(r["設定"][k] != b[k] for k in keys) == 1 and
                  r["設定"]["ATRストップ係数"] == b["ATRストップ係数"]]
            top[0]["周辺の平均スコア"] = round(float(np.mean(nb)), 3) if nb else None
        res[fam] = {"候補数": len(grid), "上位": top}
    return res


# ------------------------------------------------------------ 関係・相乗効果
def relation(ej, uj, eu, ej1h, uj1h, eu1h):
    c = pd.concat([reu._nyclose(ej).pct_change(), reu._nyclose(uj).pct_change(),
                   reu._nyclose(eu).pct_change()], axis=1, keys=["EJ", "UJ", "EU"]).dropna()
    corr = {}
    for lbl, n in (("全期間", None), ("直近250日", 250), ("直近60日", 60)):
        s = c if n is None else c.iloc[-n:]
        corr[lbl] = {"ドル円": round(float(s["EJ"].corr(s["UJ"])), 2),
                     "ユーロドル": round(float(s["EJ"].corr(s["EU"])), 2)}
    # 主役: ユーロ円の変化 ≒ ドル円の変化 ＋ ユーロドルの変化。各脚を直近60日の標準偏差で割って判定
    za = c["UJ"] / c["UJ"].rolling(60).std()
    zb = c["EU"] / c["EU"].rolling(60).std()
    z = pd.concat([za, zb], axis=1, keys=["a", "b"]).dropna()

    def kind(r):
        big_a, big_b = abs(r.a) >= 0.5, abs(r.b) >= 0.5
        if big_a and big_b:
            return "円・ユーロ同時" if r.a * r.b > 0 else "打ち消し(ドル主導)"
        if big_a:
            return "円主導"
        if big_b:
            return "ユーロ主導"
        return "小動き・その他"
    kinds = z.apply(kind, axis=1)
    share = {k: round(float(v), 3) for k, v in kinds.value_counts(normalize=True).items()}
    share_60 = {k: round(float(v), 3) for k, v in kinds.iloc[-60:].value_counts(normalize=True).items()}
    # ユーロ円の値動きのうち、ドル円の脚・ユーロドルの脚が説明する割合（分散の寄与・直近250日）
    s = c.iloc[-250:]
    var = float(s["EJ"].var())
    contrib = {"ドル円の脚": round(float(s["EJ"].cov(s["UJ"]) / var), 2),
               "ユーロドルの脚": round(float(s["EJ"].cov(s["EU"]) / var), 2)}
    h = pd.concat([ej1h["Close"].pct_change(), uj1h["Close"].pct_change()], axis=1,
                  keys=["EJ", "UJ"]).dropna()
    lead = {"同時": round(float(h["EJ"].corr(h["UJ"])), 3),
            "ユーロ円1本前→ドル円": round(float(h["UJ"].corr(h["EJ"].shift(1))), 3),
            "ドル円1本前→ユーロ円": round(float(h["EJ"].corr(h["UJ"].shift(1))), 3)}
    return {"日次相関": corr, "主役の割合(全期間)": share, "主役の割合(直近60日)": share_60,
            "最近の主役": kinds.iloc[-1], "値動きの寄与(直近250日)": contrib,
            "1時間足の先行遅行(相関)": lead}


def _filtered_signal(uj, ej, n, x, mom=20):
    """ドル円のドンチャン目標ポジションを、ユーロ円の mom 日の向きが同じ時だけ新規に建てる。
    ドル円の買い（円安）はユーロ円も上向き（円安）の時だけ。決済は元のまま。"""
    base = donchian(uj, n=n, exit_n=x).values
    e = pd.Series(ej["Close"].values, index=pd.to_datetime(ej["Date"]))
    e = e.reindex(pd.to_datetime(uj["Date"])).ffill()
    edir = np.sign(e.diff(mom)).values
    out, cur = np.zeros(len(base), dtype=int), 0
    for i, s in enumerate(base):
        if s == 0:
            cur = 0
        elif s != cur:
            if cur != 0 and s != cur:
                cur = 0
            ok = not np.isnan(edir[i]) and edir[i] == s
            cur = s if ok else 0
        out[i] = cur
    return pd.Series(out, index=uj.index)


def synergy(uj, ej, cfg):
    res = {}
    for name, s in cfg["システム"].items():
        n, x, k, risk = int(s["エントリー期間"]), int(s["イグジット期間"]), \
            float(s["ATRストップ係数"]), float(s["リスク率"])
        base = reu._metrics_usd(uj, n, x, k, risk)
        rows = {"現行": base}
        for mom in (10, 20, 60):
            rows[f"ユーロ円{mom}日の向きで確認"] = reu._metrics_usd(
                uj, n, x, k, risk, sig=lambda sub, m=mom: _filtered_signal(sub, ej, n, x, m))
        best = max((v for kk, v in rows.items() if kk != "現行"), key=lambda v: v["スコア"])
        ok = (best["スコア"] >= base["スコア"] * 1.05 and best["取引数"] >= base["取引数"] * 0.6)
        res[name] = {"比較": rows,
                     "判定": ("改善あり（採用候補・落合さん判断）" if ok else "改善なし（現行のまま）")}
    return res


def run(adopt=False):
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    ej = dfj.load_daily()
    eu = dfe.load_daily()
    uj = pd.read_csv(R("data", "usdjpy_daily.csv"))
    uj["Date"] = pd.to_datetime(uj["Date"]).dt.date
    fam = search_families(ej)
    usd_params = {}
    for name, s in cfg["システム"].items():
        scfg = {"戦略": "ドンチャン", "エントリー期間": int(s["エントリー期間"]),
                "イグジット期間": int(s["イグジット期間"]), "ATRストップ係数": float(s["ATRストップ係数"])}
        usd_params[name] = {"設定": scfg, **_family_metrics(ej, scfg, float(s["リスク率"]))}
    uj1h = pd.read_csv(R("data", "usdjpy_1h.csv"), index_col="Time", parse_dates=["Time"])
    rel = relation(ej, uj, eu, dfj.load_intraday("1h"), uj1h, dfe.load_intraday("1h"))
    syn = synergy(uj, ej, cfg)

    # 採用候補: ①押し目・戻り（BB逆張り）②トレンド型（MAクロス／ドンチャン）の各最良。
    # 前半・後半PFの小さい方が ADOPT_SCORE 以上・周辺の平均スコア1.0以上のときだけ候補にする。
    chosen = {}
    for sysname, fams, risk in (("押し目戻り", ["BB逆張り"], 0.01),
                                ("トレンド", ["MAクロス", "ドンチャン"], 0.005)):
        cands = [r for f in fams for r in (fam.get(f, {}).get("上位") or [])[:1]]
        cands.sort(key=lambda r: r["スコア"], reverse=True)
        # 最良が周辺で崩れる（当たり外れ）なら、次の型の最良を見る
        ok = [r for r in cands if r["スコア"] >= ADOPT_SCORE and
              (r.get("周辺の平均スコア") is None or r["周辺の平均スコア"] >= 1.0)]
        if not ok:
            continue
        b = ok[0]
        nb = b.get("周辺の平均スコア")
        chosen[sysname] = {"有効": True, **b["設定"], "リスク率": risk, "レバ上限": 1.5,
                           "検証": {k: b[k] for k in ("スコア", "PF全", "PF前", "PF後", "勝率",
                                                     "最大DD", "取引数", "総リターン", "年率")}
                           | {"周辺の平均スコア": nb}}
    summary = {"更新": datetime.now().strftime("%Y-%m-%dT%H:%M"),
               "期間": f"{ej['Date'].iloc[0]}〜{ej['Date'].iloc[-1]}（日足{len(ej)}本）",
               "前提": f"片道コスト{COST / dfj.PIP:.1f}銭・前半〜2018/後半2019〜のPFの小さい方をスコア",
               "終値": round(float(ej["Close"].iloc[-1]), 3),
               "採用状況": ("採用済み" if (cfg.get("ユーロ円") or {}).get("システム") else "研究のみ（売買ルール未採用・落合さん判断待ち）"),
               "選定パラメータ": chosen, "型ごとの探索": fam,
               "ドル円の現行パラメータをユーロ円に当てた場合": usd_params,
               "ドル円・ユーロドルとの関係": rel, "相乗効果(ドル円日足×ユーロ円確認)": syn}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,システム,パラメータ,スコア,PF全,勝率,取引数,ドル円相関250\n")
        for k, v in chosen.items():
            f.write(f"{summary['更新'][:10]},{k},{reu.param_label(v)},{v['検証']['スコア']},"
                    f"{v['検証']['PF全']},{v['検証']['勝率']},{v['検証']['取引数']},"
                    f"{rel['日次相関']['直近250日']['ドル円']}\n")
    if adopt and chosen:
        cfg.setdefault("ユーロ円", {"有効": True, "シンボル": "EURJPY=X", "取引単位_通貨": 1000})
        cfg["ユーロ円"]["システム"] = {k: {kk: vv for kk, vv in v.items() if kk != "検証"}
                                  for k, v in chosen.items()}
        cfg["ユーロ円"]["採用日"] = summary["更新"][:10]
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"=== ユーロ円研究 {summary['期間']} ===")
    for f_, r in fam.items():
        t = (r["上位"] or [None])[0]
        if t:
            print(f"  型[{f_}] 最良 {reu.param_label(t['設定'])} スコア{t['スコア']} PF{t['PF全']}"
                  f"(前{t['PF前']}/後{t['PF後']}) 勝率{t['勝率']:.0%} DD{t['最大DD']:.1%} {t['取引数']}回"
                  f" 年率{t['年率']:.1%} 周辺{t.get('周辺の平均スコア')}")
    for k, v in chosen.items():
        t = v["検証"]
        print(f"[採用候補 {k}] {reu.param_label(v)}  スコア{t['スコア']} PF{t['PF全']}"
              f"(前{t['PF前']}/後{t['PF後']}) 勝率{t['勝率']:.0%} DD{t['最大DD']:.1%}"
              f" {t['取引数']}回 年率{t['年率']:.1%}")
    for k, v in usd_params.items():
        print(f"  参考: ドル円の{k}の値をそのまま当てると スコア{v['スコア']} PF{v['PF全']}"
              f"(前{v['PF前']}/後{v['PF後']}) {v['取引数']}回")
    print("関係:", json.dumps(rel, ensure_ascii=False))
    for k, v in syn.items():
        print(f"相乗効果[{k}]: {v['判定']}  " + " / ".join(
            f"{kk}: スコア{vv['スコア']} PF{vv['PF全']} {vv['取引数']}回" for kk, vv in v["比較"].items()))
    print(f"保存: {OUT_JSON}" + ("（config.json に採用）" if adopt else ""))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--adopt", action="store_true")
    run(adopt=ap.parse_args().adopt)
