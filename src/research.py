# -*- coding: utf-8 -*-
"""日次研究モジュール — 2システムの自動進化

毎朝の最終確定配信後に実行し、最新データ(15年日足)で各システムの
パラメータグリッド探索を行う。カーブフィッティングを避けるため、
システムごとの頑健性ゲートを全て満たした候補だけが現行を置き換える。

採用時は config.json の該当システムを書き換え(翌朝から適用)、
research_log.csv に記録し、ダッシュボードを再生成する。

使い方:  python3 src/research.py            # 全システム探索+判定+必要なら採用
         python3 src/research.py --no-adopt # 探索のみ(採用しない)
"""
import argparse
import itertools
import json
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from strategies import donchian  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "results", "research_log.csv")

SPLIT = "2018-12-31"
IMPROVE_RATIO = 1.05
MAX_DD_LIMIT = -0.12

GRIDS = {
    "長期トレンド": {
        "entry": [10, 15, 20, 25, 30, 40, 55],
        "exit": [5, 8, 10, 15, 20],
        "atr": [2.5, 3.0, 3.5],
        "min_trades": 60,
        "min_pf_half": 1.3,
    },
    "短期スイング": {
        # 短期システムは「回転を上げる」役割のため取引頻度の下限を厳しくする
        # (250回/15.5年 ≒ 年16回以上。これを下回る候補は役割違いとして不採用)
        "entry": [5, 8, 10, 15, 20, 25],
        "exit": [3, 4, 5, 8, 10],
        "atr": [1.5, 2.0, 2.5],
        "min_trades": 250,
        "min_pf_half": 1.25,
    },
}


def evaluate(px, px_a, px_b, n_e, n_x, atr_k, risk, lev):
    out = {}
    for key, sub in (("全", px), ("前", px_a), ("後", px_b)):
        bt = Backtester(sub, risk_per_trade=risk, max_leverage=lev,
                        atr_stop_mult=atr_k)
        m = bt.run(donchian(sub, n=n_e, exit_n=n_x))["metrics"]
        out[f"PF{key}"] = m["プロフィットファクター"]
        if key == "全":
            out.update({"シャープ": m["シャープレシオ"], "勝率": m["勝率"],
                        "最大DD": m["最大ドローダウン"], "取引数": m["取引回数"],
                        "総リターン": m["総リターン"]})
    pf_a, pf_b = out["PF前"], out["PF後"]
    out["スコア"] = min(pf_a, pf_b) if pf_a == pf_a and pf_b == pf_b else 0.0
    return out


def log_row(system, top, verdict):
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,システム,エントリー,イグジット,ATR係数,PF全,PF前半,PF後半,"
                    "シャープ,勝率,最大DD,取引数,スコア,判定\n")
        f.write(",".join(map(str, [
            date.today().isoformat(), system,
            int(top["エントリー"]), int(top["イグジット"]), top["ATR係数"],
            f"{top['PF全']:.2f}", f"{top['PF前']:.2f}", f"{top['PF後']:.2f}",
            f"{top['シャープ']:.2f}", f"{top['勝率']:.1%}", f"{top['最大DD']:.1%}",
            int(top["取引数"]), f"{top['スコア']:.2f}", verdict])) + "\n")


def research_system(name, scfg, px, px_a, px_b, grid, adopt):
    risk = float(scfg["リスク率"])
    lev = float(scfg["レバ上限"])
    cur = (int(scfg["エントリー期間"]), int(scfg["イグジット期間"]),
           float(scfg["ATRストップ係数"]))
    base = evaluate(px, px_a, px_b, *cur, risk, lev)
    print(f"\n=== {name} 現行 ドンチャン{cur[0]}/{cur[1]}・ATR×{cur[2]}: "
          f"スコア{base['スコア']:.2f} PF全{base['PF全']:.2f} 勝率{base['勝率']:.0%}")

    results = []
    for n_e, n_x, atr_k in itertools.product(grid["entry"], grid["exit"],
                                             grid["atr"]):
        if n_x >= n_e:
            continue
        r = evaluate(px, px_a, px_b, n_e, n_x, atr_k, risk, lev)
        r.update({"エントリー": n_e, "イグジット": n_x, "ATR係数": atr_k})
        results.append(r)
    res = pd.DataFrame(results).sort_values("スコア", ascending=False)

    ok = res[(res["取引数"] >= grid["min_trades"]) &
             (res["PF前"] >= grid["min_pf_half"]) &
             (res["PF後"] >= grid["min_pf_half"]) &
             (res["最大DD"] >= MAX_DD_LIMIT)]
    best = ok.iloc[0] if len(ok) else None

    verdict = "現状維持"
    if best is not None:
        is_current = (int(best["エントリー"]), int(best["イグジット"]),
                      float(best["ATR係数"])) == cur
        if is_current:
            verdict = "現行が最良"
        elif adopt and best["スコア"] >= base["スコア"] * IMPROVE_RATIO:
            scfg["エントリー期間"] = int(best["エントリー"])
            scfg["イグジット期間"] = int(best["イグジット"])
            scfg["ATRストップ係数"] = float(best["ATR係数"])
            verdict = "採用"
    top = best if best is not None else res.iloc[0]
    log_row(name, top, verdict)

    show = ["エントリー", "イグジット", "ATR係数", "スコア", "PF全", "PF前", "PF後",
            "勝率", "最大DD", "取引数"]
    print(ok[show].head(3).to_string(index=False) if len(ok)
          else "（ゲート通過なし）")
    print(f"判定: {verdict}")
    if verdict == "採用":
        print(f"→ 新パラメータ: ドンチャン{int(best['エントリー'])}/"
              f"{int(best['イグジット'])}・ATR×{best['ATR係数']}（翌朝から適用）")
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-adopt", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    px = pd.read_csv(CSV_PATH)
    d = pd.to_datetime(px["Date"])
    px_a = px[d <= SPLIT].reset_index(drop=True)
    px_b = px[d > SPLIT].reset_index(drop=True)

    adopted = []
    for name, scfg in cfg["システム"].items():
        if not scfg.get("有効", True):
            continue
        grid = GRIDS.get(name)
        if grid is None:
            continue
        v = research_system(name, scfg, px, px_a, px_b, grid,
                            adopt=not args.no_adopt)
        if v == "採用":
            adopted.append(name)

    if adopted:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        try:
            import dashboard
            dashboard.build()
        except Exception as e:
            print("ダッシュボード更新失敗:", e)
    print(f"\n進化したシステム: {', '.join(adopted) if adopted else 'なし'}")


if __name__ == "__main__":
    main()
