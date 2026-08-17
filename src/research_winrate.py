# -*- coding: utf-8 -*-
"""日足2システム（長期トレンド・短期スイング）の勝率向上研究

背景（2026-08-17 ユーザー目標「勝率50%超え」）:
  現行の日足2システムは利確を持たず、手仕舞いはドンチャン逆側タッチと
  ATRトレイリングストップだけで決まる。この構造は「小さく負けて大きく勝つ」
  ため勝率は構造的に40%台に留まる（長期43.9% / 短期46.7%）。
  勝率を上げる直接の打ち手は (1) 固定利確の追加 (2) エントリー品質の絞り込み
  の2つなので、両方をグリッドに乗せて頑健性ゲートと同時に評価する。

research.py（既存の日次研究）との関係:
  research.py はスコア = min(PF前半, PF後半) だけで採否を決め、利確もフィルタも
  探索していない。本スクリプトはその探索空間を「利確係数 × エントリーフィルタ」
  へ広げ、勝率ゲートを追加したもの。config.json は書き換えない（分析専用）。
  採用可否はユーザー判断。

使い方:  python3 src/research_winrate.py            # 全システム探索
         python3 src/research_winrate.py --system 短期スイング
"""
import argparse
import itertools
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester, sma  # noqa: E402
from strategies import donchian  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUT_DIR = os.path.join(BASE_DIR, "results")

SPLIT = "2018-12-31"          # research.py と同じ前後半分割点
MAX_DD_LIMIT = -0.12          # research.py と同じDD上限
WIN_TARGET = 0.50             # ユーザー目標

# 2026-08-17 初回探索の知見でグリッドを組み替えた:
#   - 勝率を動かす主因は「エントリー期間の長さ」だった。research.py の上限55は
#     山の途中で切れていたので120まで伸ばす（70付近に勝率・PFの同時ピーク）。
#   - 固定利確は勝率をほとんど上げずPFだけ削る（例: 長期55/10でTP×3にすると
#     PF2.46→1.49、勝率は50.7→50.8%で横ばい）。確認用に3点だけ残す。
GRIDS = {
    "長期トレンド": {
        "entry": [15, 20, 25, 30, 40, 45, 50, 55, 60, 70, 80, 100, 120],
        "exit": [5, 8, 10, 12, 15, 20],
        "atr": [2.5, 3.0, 3.5],
        "tp": [None, 6.0, 10.0],
        "min_trades": 50,     # 長期化で取引数が減るため research.py の60より緩め
        "min_pf_half": 1.3,
    },
    "短期スイング": {
        "entry": [5, 8, 10, 12, 14, 16, 20, 25],
        "exit": [3, 4, 5, 6, 7, 8, 10],
        "atr": [1.2, 1.5, 2.0, 2.5],
        "tp": [None, 6.0],
        "min_trades": 250,
        "min_pf_half": 1.25,
    },
}

# エントリーフィルタ（研究用。採用時は signal_engine 側の実装が別途必要）
FILTERS = ["なし", "SMA200順行", "SMA100順行", "SMA50順行"]


def apply_filter(sig: pd.Series, px: pd.DataFrame, kind: str) -> pd.Series:
    """長期移動平均と同方向のシグナルだけ残す（逆行分はノーポジ化）。"""
    if kind == "なし":
        return sig
    n = int(kind.replace("SMA", "").replace("順行", ""))
    ma = sma(px["Close"], n)
    out = sig.copy()
    out[(sig > 0) & ~(px["Close"] > ma)] = 0
    out[(sig < 0) & ~(px["Close"] < ma)] = 0
    return out.fillna(0).astype(int)


def _run(px, sig, atr_k, tp, risk, lev):
    bt = Backtester(px, risk_per_trade=risk, max_leverage=lev,
                    atr_stop_mult=atr_k, tp_atr_mult=tp)
    return bt.run(sig)["metrics"]


def evaluate(sigs, pxs, atr_k, tp, risk, lev):
    """全期間・前半・後半の3スライスを評価して1行にまとめる。"""
    out = {}
    for key in ("全", "前", "後"):
        m = _run(pxs[key], sigs[key], atr_k, tp, risk, lev)
        out[f"PF{key}"] = m["プロフィットファクター"]
        out[f"勝率{key}"] = m["勝率"]
        if key == "全":
            out.update({"シャープ": m["シャープレシオ"],
                        "最大DD": m["最大ドローダウン"],
                        "取引数": m["取引回数"],
                        "総リターン": m["総リターン"],
                        "CAGR": m["年率リターン(CAGR)"]})
    pf_a, pf_b = out["PF前"], out["PF後"]
    out["スコア"] = (min(pf_a, pf_b)
                     if pf_a == pf_a and pf_b == pf_b else 0.0)
    # 勝率も前後半で崩れていないか（最小値）を見る
    w_a, w_b = out["勝率前"], out["勝率後"]
    out["勝率下限"] = min(w_a, w_b) if w_a == w_a and w_b == w_b else 0.0
    return out


def research_system(name, scfg, px, px_a, px_b, grid):
    risk, lev = float(scfg["リスク率"]), float(scfg["レバ上限"])
    pxs = {"全": px, "前": px_a, "後": px_b}
    cur = (int(scfg["エントリー期間"]), int(scfg["イグジット期間"]),
           float(scfg["ATRストップ係数"]))

    sig_cache = {}

    def signals(n_e, n_x, filt):
        key = (n_e, n_x, filt)
        if key not in sig_cache:
            d = {}
            for k, sub in pxs.items():
                s = donchian(sub, n=n_e, exit_n=n_x)
                d[k] = apply_filter(s, sub, filt)
            sig_cache[key] = d
        return sig_cache[key]

    base = evaluate(signals(cur[0], cur[1], "なし"), pxs, cur[2], None,
                    risk, lev)
    print(f"\n=== {name} 現行 ドンチャン{cur[0]}/{cur[1]}・ATR×{cur[2]}・利確なし")
    print(f"    勝率{base['勝率全']:.1%}（前{base['勝率前']:.1%}/"
          f"後{base['勝率後']:.1%}） PF{base['PF全']:.2f} "
          f"スコア{base['スコア']:.2f} 取引{int(base['取引数'])} "
          f"DD{base['最大DD']:.1%} 総リターン{base['総リターン']:.1%}")

    rows = []
    combos = [(e, x) for e, x in itertools.product(grid["entry"], grid["exit"])
              if x < e]
    total = len(combos) * len(grid["atr"]) * len(grid["tp"]) * len(FILTERS)
    done = 0
    for n_e, n_x in combos:
        for filt in FILTERS:
            sg = signals(n_e, n_x, filt)
            for atr_k, tp in itertools.product(grid["atr"], grid["tp"]):
                r = evaluate(sg, pxs, atr_k, tp, risk, lev)
                r.update({"エントリー": n_e, "イグジット": n_x,
                          "ATR係数": atr_k, "利確ATR": tp if tp else 0.0,
                          "フィルタ": filt})
                rows.append(r)
                done += 1
        if done % 300 < len(grid["atr"]) * len(grid["tp"]) * len(FILTERS):
            print(f"    ... {done}/{total}", flush=True)

    res = pd.DataFrame(rows)
    res["システム"] = name
    return base, res


def report(name, base, res, grid):
    """頑健性ゲート → 勝率ゲートの順に絞り、勝率とPFの両立点を示す。"""
    robust = res[(res["取引数"] >= grid["min_trades"]) &
                 (res["PF前"] >= grid["min_pf_half"]) &
                 (res["PF後"] >= grid["min_pf_half"]) &
                 (res["最大DD"] >= MAX_DD_LIMIT)]
    print(f"\n--- {name}: 全{len(res)}候補中 頑健性ゲート通過{len(robust)}件")

    win = robust[(robust["勝率全"] >= WIN_TARGET) &
                 (robust["勝率下限"] >= WIN_TARGET)]
    print(f"    うち勝率50%以上（前後半とも）: {len(win)}件")
    if not len(win):
        loose = robust[robust["勝率全"] >= WIN_TARGET]
        print(f"    （全期間のみ50%以上なら {len(loose)}件）")

    show = ["エントリー", "イグジット", "ATR係数", "利確ATR", "フィルタ",
            "勝率全", "勝率前", "勝率後", "PF全", "PF前", "PF後",
            "スコア", "最大DD", "取引数", "総リターン"]

    def fmt(d):
        d = d.copy()
        for c in ["勝率全", "勝率前", "勝率後", "最大DD", "総リターン"]:
            d[c] = d[c].map(lambda v: f"{v:.1%}")
        for c in ["PF全", "PF前", "PF後", "スコア"]:
            d[c] = d[c].map(lambda v: f"{v:.2f}")
        d["取引数"] = d["取引数"].astype(int)
        return d[show].to_string(index=False)

    if len(win):
        print("\n  ◆ 勝率50%超かつ頑健（スコア降順 上位5）")
        print(fmt(win.sort_values("スコア", ascending=False).head(5)))
    print("\n  ◆ 頑健候補の勝率上位5（50%到達可否の実像）")
    print(fmt(robust.sort_values("勝率全", ascending=False).head(5)))
    print("\n  ◆ 参考: 頑健候補のスコア上位3（収益性重視）")
    print(fmt(robust.sort_values("スコア", ascending=False).head(3)))

    # 利確係数が勝率に与える効果（頑健候補内の平均）
    if len(robust):
        g = robust.groupby("利確ATR").agg(
            件数=("勝率全", "size"), 平均勝率=("勝率全", "mean"),
            平均PF=("PF全", "mean"), 最高勝率=("勝率全", "max"))
        print("\n  ◆ 利確ATR係数別（頑健候補の平均。0=利確なし）")
        print(g.assign(平均勝率=lambda d: d["平均勝率"].map("{:.1%}".format),
                       最高勝率=lambda d: d["最高勝率"].map("{:.1%}".format),
                       平均PF=lambda d: d["平均PF"].map("{:.2f}".format))
              .to_string())
        f = robust.groupby("フィルタ").agg(
            件数=("勝率全", "size"), 平均勝率=("勝率全", "mean"),
            平均PF=("PF全", "mean"), 最高勝率=("勝率全", "max"))
        print("\n  ◆ エントリーフィルタ別（頑健候補の平均）")
        print(f.assign(平均勝率=lambda d: d["平均勝率"].map("{:.1%}".format),
                       最高勝率=lambda d: d["最高勝率"].map("{:.1%}".format),
                       平均PF=lambda d: d["平均PF"].map("{:.2f}".format))
              .to_string())
    return win


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default=None,
                    help="長期トレンド / 短期スイング のどちらかだけ実行")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    px = pd.read_csv(CSV_PATH)
    d = pd.to_datetime(px["Date"])
    px_a = px[d <= SPLIT].reset_index(drop=True)
    px_b = px[d > SPLIT].reset_index(drop=True)
    print(f"データ: {px['Date'].iloc[0]} 〜 {px['Date'].iloc[-1]} "
          f"({len(px)}本 / 前半{len(px_a)}・後半{len(px_b)})")

    all_res = []
    for name, scfg in cfg["システム"].items():
        if args.system and name != args.system:
            continue
        grid = GRIDS.get(name)
        if grid is None:
            continue
        base, res = research_system(name, scfg, px, px_a, px_b, grid)
        report(name, base, res, grid)
        all_res.append(res)

    if all_res:
        out = pd.concat(all_res, ignore_index=True)
        path = os.path.join(OUT_DIR,
                            f"research_winrate_{date.today().isoformat()}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"\n全候補を保存: {path}")


if __name__ == "__main__":
    main()
