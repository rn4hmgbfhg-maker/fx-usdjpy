# -*- coding: utf-8 -*-
"""ユーロドル研究（2026-10-12 2通貨運用の開始に合わせて新設・毎朝の日次研究にも組込み）

3本立て:
  ① 日足2システムの専用パラメータ探索（ドル円と同じドンチャン＋ATRトレイル）
     ・グリッド: エントリー/イグジット/ATR係数。前半(〜2018)・後半(2019〜)のPFの小さい方を
       スコアにし、取引数・最大DDのゲートを通った中で最良を「長期」「短期」それぞれ選ぶ。
     ・参考: ドル円の現行パラメータをそのまま当てた成績も並べる。
  ② ドル円との関係（2大通貨の連動）
     ・日次リターンの相関（全期間・直近250日・60日）。値が負＝ドルが両通貨で同時に動く。
     ・「ドル全面主導／円主導／ユーロ主導」の日の割合（どちらの通貨が動きの主役か）。
     ・1時間足の先行・遅行（ユーロドルの1本前の動きが次のドル円を説明するか）。
  ③ 相乗効果の検証（ドル円の売買をユーロドルで確認すると成績が上がるか）
     ・ドル円の日足2システムの新規建てを「ユーロドルの20日の向きがドル高/ドル安で一致する時だけ」
       に絞った場合の前半/後半PF・取引数を、絞らない場合と比べる（採用は落合さん判断）。

結果: results/research_eurusd_latest.json（ボード・指示書が読む）／research_eurusd_log.csv
使い方: python3 src/research_eurusd.py            # 研究のみ（config は変えない）
        python3 src/research_eurusd.py --adopt    # 選ばれたパラメータを config.json「ユーロドル」へ書く
"""
import argparse
import itertools
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from strategies import donchian  # noqa: E402
import data_fetch_eurusd as dfe  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731
OUT_JSON = R("results", "research_eurusd_latest.json")
LOG_CSV = R("results", "research_eurusd_log.csv")
CONFIG = R("config.json")
SPLIT = pd.Timestamp("2018-12-31").date()
COST = 0.00006            # 片道0.6pips（スプレッド0.3〜0.4pips＋滑り）
GRID = ([10, 15, 20, 25, 30, 40, 55, 70], [5, 8, 10, 15, 20], [1.5, 2.0, 2.5, 3.0, 3.5])
# 頑健性ゲート（ドル円の research.py と同じ考え方）
GATE = {"長期": {"entry_min": 25, "trades_min": 60, "dd_max": -0.12},
        "短期": {"entry_max": 20, "trades_min": 200, "dd_max": -0.12}}


def _bt(df, n, x, k, risk):
    bt = Backtester(df, risk_per_trade=risk, max_leverage=1.5, atr_stop_mult=k,
                    cost_per_side=COST)
    return bt.run(donchian(df, n=n, exit_n=x))


def _metrics(df, n, x, k, risk, sig=None):
    out = {}
    d = pd.to_datetime(df["Date"]).dt.date
    for key, sub in (("全", df), ("前", df[d <= SPLIT]), ("後", df[d > SPLIT])):
        sub = sub.reset_index(drop=True)
        bt = Backtester(sub, risk_per_trade=risk, max_leverage=1.5, atr_stop_mult=k,
                        cost_per_side=COST)
        s = donchian(sub, n=n, exit_n=x) if sig is None else sig(sub)
        m = bt.run(s)["metrics"]
        out[key] = m
    a, b = out["前"]["プロフィットファクター"], out["後"]["プロフィットファクター"]
    score = min(a, b) if a == a and b == b else 0.0
    m = out["全"]
    return {"エントリー": n, "イグジット": x, "ATR": k, "スコア": round(float(score), 3),
            "PF全": round(float(m["プロフィットファクター"]), 2),
            "PF前": round(float(a), 2), "PF後": round(float(b), 2),
            "勝率": round(float(m["勝率"]), 3), "最大DD": round(float(m["最大ドローダウン"]), 3),
            "取引数": int(m["取引回数"]), "総リターン": round(float(m["総リターン"]), 3)}


def search_daily(px):
    """ドンチャン（ドル円と同じ型）のグリッド。参考用（ユーロドルでは効かないことの確認）。"""
    rows = []
    for n, x, k in itertools.product(*GRID):
        if x >= n:
            continue
        rows.append(_metrics(px, n, x, k, 0.01))
    df = pd.DataFrame(rows)
    best = {}
    for name, g in GATE.items():
        c = df[(df["取引数"] >= g["trades_min"]) & (df["最大DD"] >= g["dd_max"])]
        c = c[c["エントリー"] >= g["entry_min"]] if "entry_min" in g else c[c["エントリー"] <= g["entry_max"]]
        c = c.sort_values(["スコア", "PF全"], ascending=False)
        best[name] = c.head(5).to_dict("records")
    return best, len(rows)


# 型ごとの探索（2026-10-12 ユーロドルはトレンド追随が2019年以降効かないため、型を広げる）
FAMILY_GRID = {
    "BB逆張り": [{"戦略": "BB逆張り", "BB期間": n, "BB係数": z, "トレンド期間": t, "ATRストップ係数": k}
              for n in (10, 15, 20) for z in (1.5, 2.0) for t in (100, 200) for k in (1.5, 2.5, 3.5)],
    "MAクロス": [{"戦略": "MAクロス", "短期MA": f, "長期MA": s, "ATRストップ係数": k}
              for f in (10, 20, 50) for s in (50, 100, 200) for k in (2.5, 3.5) if f < s],
    "ドンチャン": [{"戦略": "ドンチャン", "エントリー期間": n, "イグジット期間": x, "ATRストップ係数": k}
              for n in (20, 25, 40, 55) for x in (8, 10, 20) for k in (2.5, 3.5) if x < n],
}
MIN_TRADES_FAMILY = 60
ADOPT_SCORE = 1.10          # 採用の下限（前半・後半とも PF 1.10 以上）


def _family_metrics(px, scfg, risk=0.01):
    from strategies import make_signal                              # noqa: PLC0415
    d = pd.to_datetime(px["Date"]).dt.date
    out = {}
    for key, sub in (("全", px), ("前", px[d <= SPLIT]), ("後", px[d > SPLIT])):
        sub = sub.reset_index(drop=True)
        bt = Backtester(sub, risk_per_trade=risk, max_leverage=1.5,
                        atr_stop_mult=float(scfg["ATRストップ係数"]), cost_per_side=COST)
        out[key] = bt.run(make_signal(sub, scfg))["metrics"]
    a, b = out["前"]["プロフィットファクター"], out["後"]["プロフィットファクター"]
    m = out["全"]
    return {"スコア": round(float(min(a, b)), 3) if a == a and b == b else 0.0,
            "PF全": round(float(m["プロフィットファクター"]), 2), "PF前": round(float(a), 2),
            "PF後": round(float(b), 2), "勝率": round(float(m["勝率"]), 3),
            "最大DD": round(float(m["最大ドローダウン"]), 3), "取引数": int(m["取引回数"]),
            "総リターン": round(float(m["総リターン"]), 3),
            "年率": round(float(m["年率リターン(CAGR)"]), 4)}


def search_families(px):
    """型ごとに最良を選び、近い設定（周辺）の平均スコアで頑健性も確かめる。"""
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
            # 周辺＝ATR係数以外が1段違うもの（設定の当たり外れに依存していないかを見る）
            keys = [k for k in b if k not in ("戦略", "ATRストップ係数")]
            nb = [r["スコア"] for r in rows if r["設定"] is not b and
                  sum(r["設定"][k] != b[k] for k in keys) == 1 and
                  r["設定"]["ATRストップ係数"] == b["ATRストップ係数"]]
            top[0]["周辺の平均スコア"] = round(float(np.mean(nb)), 3) if nb else None
        res[fam] = {"候補数": len(grid), "上位": top}
    return res


# ------------------------------------------------------------ 関係・相乗効果
def _nyclose(px):
    """Yahoo日足の終値は1本後ろにずれている既知の癖を補正（research_fundamental と同じ）。"""
    c = pd.Series(px["Close"].values, index=pd.to_datetime(px["Date"]))
    return pd.Series(c.values[1:], index=c.index[:-1])


def relation(uj, eu, uj1h, eu1h):
    a, b = _nyclose(uj).pct_change(), _nyclose(eu).pct_change()
    both = pd.concat([a, b], axis=1, keys=["UJ", "EU"]).dropna()
    corr = {"全期間": round(float(both["UJ"].corr(both["EU"])), 2),
            "直近250日": round(float(both["UJ"].iloc[-250:].corr(both["EU"].iloc[-250:])), 2),
            "直近60日": round(float(both["UJ"].iloc[-60:].corr(both["EU"].iloc[-60:])), 2)}
    # 主役の判定: 各日の変化を直近60日の標準偏差で割り、大きい方を主役とする。
    # ドル全面主導＝ドル円↑かつユーロドル↓（またはその逆）で両方とも0.5σ以上動いた日
    za = both["UJ"] / both["UJ"].rolling(60).std()
    zb = both["EU"] / both["EU"].rolling(60).std()
    z = pd.concat([za, zb], axis=1, keys=["a", "b"]).dropna()

    def kind(r):
        if r.a * r.b < 0 and abs(r.a) >= 0.5 and abs(r.b) >= 0.5:
            return "ドル全面主導"
        if abs(r.a) >= 0.5 and abs(r.b) < 0.5:
            return "円主導"
        if abs(r.b) >= 0.5 and abs(r.a) < 0.5:
            return "ユーロ主導"
        return "小動き・その他"
    kinds = z.apply(kind, axis=1)
    share = {k: round(float(v), 3) for k, v in kinds.value_counts(normalize=True).items()}
    share_60 = {k: round(float(v), 3) for k, v in kinds.iloc[-60:].value_counts(normalize=True).items()}
    # 1時間足の先行・遅行
    h = pd.concat([uj1h["Close"].pct_change(), eu1h["Close"].pct_change()], axis=1,
                  keys=["UJ", "EU"]).dropna()
    lead = {"同時": round(float(h["UJ"].corr(h["EU"])), 3),
            "ユーロドル1本前→ドル円": round(float(h["UJ"].corr(h["EU"].shift(1))), 3),
            "ドル円1本前→ユーロドル": round(float(h["EU"].corr(h["UJ"].shift(1))), 3)}
    return {"日次相関": corr, "主役の割合(全期間)": share, "主役の割合(直近60日)": share_60,
            "最近の主役": kinds.iloc[-1], "1時間足の先行遅行(相関)": lead}


def _filtered_signal(uj, eu, n, x, mom=20):
    """ドル円のドンチャン目標ポジションに、ユーロドルの mom 日の向きで新規建てを絞る。
    ドル円の買い（ドル高）はユーロドルが下向き（ドル高）の時だけ建てる。決済は元のまま。"""
    base = donchian(uj, n=n, exit_n=x).values
    e = pd.Series(eu["Close"].values, index=pd.to_datetime(eu["Date"]))
    e = e.reindex(pd.to_datetime(uj["Date"])).ffill()
    edir = np.sign(e.diff(mom)).values          # +1=ユーロ高ドル安
    out, cur = np.zeros(len(base), dtype=int), 0
    for i, s in enumerate(base):
        if s == 0:
            cur = 0
        elif s != cur:
            if cur != 0 and s != cur:
                cur = 0
            ok = not np.isnan(edir[i]) and edir[i] == -s
            cur = s if ok else 0
        out[i] = cur
    return pd.Series(out, index=uj.index)


def synergy(uj, eu, cfg):
    """ドル円日足2システムに『ユーロドルの向き確認』を足したら成績は上がるか。"""
    res = {}
    for name, s in cfg["システム"].items():
        n, x, k, risk = int(s["エントリー期間"]), int(s["イグジット期間"]), \
            float(s["ATRストップ係数"]), float(s["リスク率"])
        base = _metrics_usd(uj, n, x, k, risk)
        rows = {"現行": base}
        for mom in (10, 20, 60):
            f = _metrics_usd(uj, n, x, k, risk,
                             sig=lambda sub, m=mom: _filtered_signal(sub, eu, n, x, m))
            rows[f"ユーロドル{mom}日の向きで確認"] = f
        best = max((v for kk, v in rows.items() if kk != "現行"), key=lambda v: v["スコア"])
        ok = (best["スコア"] >= base["スコア"] * 1.05 and best["取引数"] >= base["取引数"] * 0.6)
        res[name] = {"比較": rows,
                     "判定": ("改善あり（採用候補・落合さん判断）" if ok else "改善なし（現行のまま）")}
    return res


def _metrics_usd(uj, n, x, k, risk, sig=None):
    out = {}
    d = pd.to_datetime(uj["Date"]).dt.date
    for key, sub in (("全", uj), ("前", uj[d <= SPLIT]), ("後", uj[d > SPLIT])):
        sub = sub.reset_index(drop=True)
        bt = Backtester(sub, risk_per_trade=risk, max_leverage=1.5, atr_stop_mult=k)
        s = donchian(sub, n=n, exit_n=x) if sig is None else sig(sub)
        out[key] = bt.run(s)["metrics"]
    a, b = out["前"]["プロフィットファクター"], out["後"]["プロフィットファクター"]
    m = out["全"]
    return {"スコア": round(float(min(a, b)), 3) if a == a and b == b else 0.0,
            "PF全": round(float(m["プロフィットファクター"]), 2), "PF前": round(float(a), 2),
            "PF後": round(float(b), 2), "勝率": round(float(m["勝率"]), 3),
            "取引数": int(m["取引回数"]), "最大DD": round(float(m["最大ドローダウン"]), 3)}


def param_label(s):
    k = s.get("戦略")
    if k == "BB逆張り":
        return (f"押し目買い・戻り売り（BB{s['BB期間']}日±{s['BB係数']}σ・{s.get('トレンド期間', 200)}日線の向き）"
                f"・ATR×{s['ATRストップ係数']}")
    if k == "MAクロス":
        return f"移動平均クロス{s['短期MA']}/{s['長期MA']}・ATR×{s['ATRストップ係数']}"
    return f"ドンチャン{s['エントリー期間']}/{s['イグジット期間']}・ATR×{s['ATRストップ係数']}"


def run(adopt=False):
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    eu = dfe.load_daily()
    uj = pd.read_csv(R("data", "usdjpy_daily.csv"))
    uj["Date"] = pd.to_datetime(uj["Date"]).dt.date
    best, ncand = search_daily(eu)
    usd_params = {name: _metrics(eu, int(s["エントリー期間"]), int(s["イグジット期間"]),
                                 float(s["ATRストップ係数"]), float(s["リスク率"]))
                  for name, s in cfg["システム"].items()}
    uj1h = pd.read_csv(R("data", "usdjpy_1h.csv"), index_col="Time", parse_dates=["Time"])
    eu1h = dfe.load_intraday("1h")
    rel = relation(uj, eu, uj1h, eu1h)
    syn = synergy(uj, eu, cfg)

    fam = search_families(eu)
    # 採用ルール: ①押し目・戻り（BB逆張り）の最良 ②トレンド型（MAクロス／ドンチャン）の最良。
    # どちらも前半・後半PFの小さい方が ADOPT_SCORE 以上で、周辺の平均スコアが1.0以上のときだけ。
    chosen = {}
    picks = [("押し目戻り", ["BB逆張り"], 0.01), ("トレンド", ["MAクロス", "ドンチャン"], 0.005)]
    for sysname, fams, risk in picks:
        cands = [r for f in fams for r in (fam.get(f, {}).get("上位") or [])[:1]]
        cands.sort(key=lambda r: r["スコア"], reverse=True)
        if not cands:
            continue
        b = cands[0]
        nb = b.get("周辺の平均スコア")
        if b["スコア"] < ADOPT_SCORE or (nb is not None and nb < 1.0):
            continue
        chosen[sysname] = {"有効": True, **b["設定"], "リスク率": risk, "レバ上限": 1.5,
                           "検証": {k: b[k] for k in ("スコア", "PF全", "PF前", "PF後", "勝率",
                                                     "最大DD", "取引数", "総リターン", "年率")}
                           | {"周辺の平均スコア": nb}}
    summary = {"更新": datetime.now().strftime("%Y-%m-%dT%H:%M"),
               "期間": f"{eu['Date'].iloc[0]}〜{eu['Date'].iloc[-1]}（日足{len(eu)}本）",
               "前提": f"片道コスト{COST / dfe.PIP:.1f}pips・前半〜2018/後半2019〜のPFの小さい方をスコア・候補{ncand}通り",
               "選定パラメータ": chosen, "型ごとの探索": fam,
               "ドンチャン(ドル円と同じ型)の上位": best,
               "ドル円の現行パラメータをユーロドルに当てた場合": usd_params,
               "ドル円との関係": rel, "相乗効果(ドル円日足×ユーロドル確認)": syn}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,システム,パラメータ,スコア,PF全,勝率,取引数,日次相関250\n")
        for k, v in chosen.items():
            f.write(f"{summary['更新'][:10]},{k},{param_label(v)},{v['検証']['スコア']},"
                    f"{v['検証']['PF全']},{v['検証']['勝率']},{v['検証']['取引数']},"
                    f"{rel['日次相関']['直近250日']}\n")
    if adopt and chosen:
        cfg.setdefault("ユーロドル", {"有効": True, "シンボル": "EURUSD=X", "取引単位_通貨": 1000})
        cfg["ユーロドル"]["システム"] = {k: {kk: vv for kk, vv in v.items() if kk != "検証"}
                                   for k, v in chosen.items()}
        cfg["ユーロドル"]["採用日"] = summary["更新"][:10]
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    # 表示
    print(f"=== ユーロドル研究 {summary['期間']} ===")
    for f, r in fam.items():
        t = (r["上位"] or [None])[0]
        if t:
            print(f"  型[{f}] 最良 {param_label(t['設定'])} スコア{t['スコア']} PF{t['PF全']}"
                  f"(前{t['PF前']}/後{t['PF後']}) 勝率{t['勝率']:.0%} {t['取引数']}回"
                  f" 周辺{t.get('周辺の平均スコア')}")
    for k, v in chosen.items():
        t = v["検証"]
        print(f"[採用候補 {k}] {param_label(v)}  スコア{t['スコア']} PF{t['PF全']}"
              f"(前{t['PF前']}/後{t['PF後']}) 勝率{t['勝率']:.0%} DD{t['最大DD']:.1%}"
              f" {t['取引数']}回 年率{t['年率']:.1%}")
    for k, v in usd_params.items():
        print(f"  参考: ドル円の{k}の値をそのまま当てると スコア{v['スコア']} PF{v['PF全']} {v['取引数']}回")
    print("ドル円との関係:", json.dumps(rel, ensure_ascii=False))
    for k, v in syn.items():
        print(f"相乗効果[{k}]: {v['判定']}  " + " / ".join(
            f"{kk}: スコア{vv['スコア']} PF{vv['PF全']} {vv['取引数']}回" for kk, vv in v["比較"].items()))
    print(f"保存: {OUT_JSON}" + ("（config.json に採用）" if adopt else ""))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--adopt", action="store_true")
    run(adopt=ap.parse_args().adopt)
