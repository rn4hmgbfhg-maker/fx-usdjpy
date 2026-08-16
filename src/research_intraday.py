# -*- coding: utf-8 -*-
"""デイトレ複合時間軸システムの研究モジュール（初期選定+日次自動進化）

4H/2H/1Hの各執行足×ドンチャン期間×ATR係数×4Hフィルタ有無をグリッド探索し、
「1日1回程度の売買」を目標頻度として頑健性ゲートで選別する。

ゲート（全て満たした候補のみ採用対象）:
  ・売買頻度: 1営業日あたり0.4〜1.6回転（目標1回/日のレンジ）
  ・PF: 前半・後半とも1.20以上（期間分割の頑健性）
  ・最大DD: -10%以内（リスク率0.4%想定）
  ・スコア: min(PF前, PF後)。現行がある場合は1.05倍以上で置換

使い方:
  python3 src/research_intraday.py            # 探索+判定+必要なら採用
  python3 src/research_intraday.py --no-adopt # 探索のみ
  python3 src/research_intraday.py --seed     # config未設定時の初期採用
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
from data_fetch_intraday import load_merged, resample  # noqa: E402
from strategies_intraday import align_trend, donchian_mtf  # noqa: E402
import tech_filters  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "results", "research_intraday_log.csv")

IMPROVE_RATIO = 1.05
MAX_DD_LIMIT = -0.10
MIN_PF_HALF = 1.20
FREQ_RANGE = (0.4, 1.6)      # 回転/営業日（目標1回/日）
RISK = 0.004                 # リスク率0.4%（第3システムとして控えめに）
LEV = 1.5
FILTER_N = 30                # 4Hドンチャン中心線フィルタ期間

GRIDS = {
    1: {"entry": [24, 36, 48, 72], "exit": [6, 8, 12, 24]},
    2: {"entry": [12, 18, 24, 36], "exit": [4, 6, 8, 12]},
    4: {"entry": [12, 18, 24, 30], "exit": [4, 6, 8, 10]},
}
ATRS = [1.5, 2.0, 2.5, 3.0]


def trading_days(df):
    return pd.to_datetime(df["Date"]).dt.date.nunique()


def evaluate(dfs, tf, n_e, n_x, atr_k, use_filter, tp_k=0.0,
             confirm_spec=None):
    """dfs = {"全":(exec,4h), "前":..., "後":...}

    確認フィルタのマスクは全期間の執行足で1回計算し、前半/後半へは
    位置スライスで渡す（分割フレーム上で再計算すると指標が分割点から
    再ウォームアップされ、後半のPF測定が本番=全履歴計算のエンジンと
    異なる条件になるため。前=全期間の先頭連続部分・後=末尾連続部分）。"""
    full_mask = (tech_filters.entry_mask(dfs["全"][0], confirm_spec)
                 if confirm_spec else None)
    out = {}
    for key, (dfe, df4) in dfs.items():
        trend = align_trend(dfe, df4, FILTER_N) if use_filter else None
        confirm = None
        if full_mask is not None:
            n = len(dfe)
            confirm = (full_mask if key == "全" else
                       (full_mask[0][:n], full_mask[1][:n]) if key == "前"
                       else (full_mask[0][-n:], full_mask[1][-n:]))
        sig = donchian_mtf(dfe, n_e, n_x, trend, confirm)
        bt = Backtester(dfe, risk_per_trade=RISK, max_leverage=LEV,
                        atr_stop_mult=atr_k,
                        tp_atr_mult=tp_k or None)
        m = bt.run(sig)["metrics"]
        out[f"PF{key}"] = m["プロフィットファクター"]
        if key == "全":
            out.update({"勝率": m["勝率"], "最大DD": m["最大ドローダウン"],
                        "取引数": m["取引回数"], "総リターン": m["総リターン"],
                        "頻度": m["取引回数"] / max(trading_days(dfe), 1)})
    pf_a, pf_b = out["PF前"], out["PF後"]
    out["スコア"] = min(pf_a, pf_b) if pf_a == pf_a and pf_b == pf_b else 0.0
    return out


def build_dfs(df1h, tf):
    dfe = resample(df1h, tf)
    df4 = resample(df1h, 4)
    half = len(dfe) // 2
    split_date = dfe["Date"].iloc[half]
    d4 = pd.to_datetime(df4["Date"])
    sd = pd.to_datetime(split_date)
    return {
        "全": (dfe, df4),
        "前": (dfe.iloc[:half].reset_index(drop=True),
               df4[d4 <= sd].reset_index(drop=True)),
        "後": (dfe.iloc[half:].reset_index(drop=True),
               df4[d4 > sd - pd.Timedelta(days=30)].reset_index(drop=True)),
    }


def passes_gate(r):
    return (FREQ_RANGE[0] <= r["頻度"] <= FREQ_RANGE[1]
            and r["PF前"] >= MIN_PF_HALF and r["PF後"] >= MIN_PF_HALF
            and r["最大DD"] >= MAX_DD_LIMIT)


def log_row(top, verdict):
    """CSV列は既存15列を維持する。確認フィルタは判定欄に付記
    （ラベルはカンマ不使用なのでCSV安全）。"""
    if top.get("確認フィルタ"):
        verdict += f"(確認={tech_filters.describe_filter(top['確認フィルタ'])})"
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,時間足,エントリー,イグジット,ATR係数,4Hフィルタ,"
                    "PF全,PF前,PF後,勝率,最大DD,取引数,頻度_回転毎営業日,スコア,判定\n")
        f.write(",".join(map(str, [
            date.today().isoformat(), f"{int(top['時間足'])}H",
            int(top["エントリー"]), int(top["イグジット"]), top["ATR係数"],
            "有" if top["フィルタ"] else "無",
            f"{top['PF全']:.2f}", f"{top['PF前']:.2f}", f"{top['PF後']:.2f}",
            f"{top['勝率']:.1%}", f"{top['最大DD']:.1%}", int(top["取引数"]),
            f"{top['頻度']:.2f}", f"{top['スコア']:.2f}", verdict])) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-adopt", action="store_true")
    ap.add_argument("--seed", action="store_true",
                    help="config未設定でも最良候補を初期採用する")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    scfg = cfg.get("デイトレ")
    # 運用中の目標利確係数込みで全候補を評価する（実運用との整合）
    tp_k = float((scfg or {}).get("利確ATR係数", 0))

    df1h = load_merged()
    print(f"データ: 1時間足{len(df1h)}本 "
          f"{df1h.index[0]:%Y-%m-%d} -> {df1h.index[-1]:%Y-%m-%d}")

    results = []
    dfs_cache = {}
    for tf, grid in GRIDS.items():
        dfs_cache[tf] = build_dfs(df1h, tf)
        for n_e, n_x, atr_k, flt in itertools.product(
                grid["entry"], grid["exit"], ATRS, [False, True]):
            if n_x >= n_e:
                continue
            r = evaluate(dfs_cache[tf], tf, n_e, n_x, atr_k, flt, tp_k)
            r.update({"時間足": tf, "エントリー": n_e, "イグジット": n_x,
                      "ATR係数": atr_k, "フィルタ": flt, "確認フィルタ": None})
            results.append(r)
    res = pd.DataFrame([{k: v for k, v in r.items() if k != "確認フィルタ"}
                        for r in results]).sort_values("スコア",
                                                       ascending=False)

    ok = res[(res["頻度"] >= FREQ_RANGE[0]) & (res["頻度"] <= FREQ_RANGE[1]) &
             (res["PF前"] >= MIN_PF_HALF) & (res["PF後"] >= MIN_PF_HALF) &
             (res["最大DD"] >= MAX_DD_LIMIT)]

    show = ["時間足", "エントリー", "イグジット", "ATR係数", "フィルタ", "スコア",
            "PF全", "PF前", "PF後", "勝率", "最大DD", "取引数", "頻度"]
    print("\n=== ゲート通過 上位10 ===")
    print(ok[show].head(10).to_string(index=False) if len(ok)
          else "（ゲート通過なし）")
    print("\n=== 参考: 全体上位5（ゲート無視） ===")
    print(res[show].head(5).to_string(index=False))

    # --- 確認フィルタ精緻化（5系統のテクニカルでエントリーを選別）
    # ゲート通過上位3（無ければ全体上位3）に確認フィルタ候補を重ね、
    # 頑健性ゲートを再適用して最良を探す。
    base_pool = sorted([r for r in results if passes_gate(r)] or results,
                       key=lambda r: -r["スコア"])
    refined = []
    for row in base_pool[:3]:
        for fspec in tech_filters.DEFAULT_GRID:
            r = evaluate(dfs_cache[int(row["時間足"])], row["時間足"],
                         row["エントリー"], row["イグジット"], row["ATR係数"],
                         row["フィルタ"], tp_k, fspec)
            r.update({"時間足": row["時間足"], "エントリー": row["エントリー"],
                      "イグジット": row["イグジット"],
                      "ATR係数": row["ATR係数"], "フィルタ": row["フィルタ"],
                      "確認フィルタ": fspec})
            refined.append(r)
    ref_ok = [r for r in refined if passes_gate(r)]
    if refined:
        print(f"\n=== 確認フィルタ探索: {len(refined)}件中ゲート通過"
              f"{len(ref_ok)}件  上位5 ===")
        for r in sorted(refined, key=lambda x: -x["スコア"])[:5]:
            print(f"  {int(r['時間足'])}H {int(r['エントリー'])}/"
                  f"{int(r['イグジット'])} ATR×{r['ATR係数']}"
                  f" 確認{tech_filters.describe_filter(r['確認フィルタ'])}: "
                  f"スコア{r['スコア']:.2f}"
                  f"（PF前{r['PF前']:.2f}/後{r['PF後']:.2f}"
                  f"・{r['頻度']:.2f}回転/日"
                  f"{'・ゲート通過' if passes_gate(r) else ''}）")

    # 採用候補 = ベースのゲート通過 ∪ フィルタ付きのゲート通過
    cand = sorted([r for r in base_pool if passes_gate(r)] + ref_ok,
                  key=lambda r: -r["スコア"])
    best = cand[0] if cand else None

    verdict = "現状維持"
    base_score = None
    if scfg:
        # 現行configを確認フィルタ込みで直接評価する（グリッド表引きだと
        # フィルタ付き現行が見つからないため）
        cur_tf = int(scfg["時間足"])
        dfs_cur = dfs_cache.get(cur_tf) or build_dfs(df1h, cur_tf)
        rcur = evaluate(dfs_cur, cur_tf, int(scfg["エントリー期間"]),
                        int(scfg["イグジット期間"]),
                        float(scfg["ATRストップ係数"]),
                        bool(scfg.get("4Hフィルタ期間", 0)), tp_k,
                        scfg.get("確認フィルタ"))
        base_score = float(rcur["スコア"])
        print(f"\n現行 {scfg['時間足']}H {scfg['エントリー期間']}/"
              f"{scfg['イグジット期間']}・ATR×{scfg['ATRストップ係数']}"
              f"・確認{tech_filters.describe_filter(scfg.get('確認フィルタ'))}: "
              f"スコア{base_score:.2f}")

    if best is not None:
        def key_of(tf_, ne_, nx_, ak_, flt_, cf_):
            return (int(tf_), int(ne_), int(nx_), float(ak_), bool(flt_),
                    json.dumps(cf_ or None, ensure_ascii=False,
                               sort_keys=True))
        is_current = scfg and key_of(
            best["時間足"], best["エントリー"], best["イグジット"],
            best["ATR係数"], best["フィルタ"], best.get("確認フィルタ")) == \
            key_of(scfg["時間足"], scfg["エントリー期間"],
                   scfg["イグジット期間"], scfg["ATRストップ係数"],
                   bool(scfg.get("4Hフィルタ期間", 0)),
                   scfg.get("確認フィルタ"))
        adopt_new = (scfg is None and args.seed) or (
            scfg is not None and base_score is not None and
            float(best["スコア"]) >= base_score * IMPROVE_RATIO)
        if is_current:
            verdict = "現行が最良"
        elif adopt_new and not args.no_adopt:
            new_cfg = {
                "有効": True, "試験運転": True, "戦略": "ドンチャンMTF",
                "時間足": int(best["時間足"]),
                "エントリー期間": int(best["エントリー"]),
                "イグジット期間": int(best["イグジット"]),
                "ATRストップ係数": float(best["ATR係数"]),
                "4Hフィルタ期間": FILTER_N if best["フィルタ"] else 0,
                "リスク率": RISK, "レバ上限": LEV,
                "通知時間帯": [7, 23],
                **({k: v for k, v in (scfg or {}).items()
                    if k in ("有効", "試験運転", "通知時間帯", "利確ATR係数")}),
            }
            if best.get("確認フィルタ"):
                new_cfg["確認フィルタ"] = best["確認フィルタ"]
            cfg["デイトレ"] = new_cfg
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            verdict = "採用"
    top = best if best is not None else base_pool[0]
    log_row(top, verdict)
    print(f"\n判定: {verdict}")
    if verdict == "採用":
        print(f"→ デイトレ: {int(top['時間足'])}H ドンチャン{int(top['エントリー'])}"
              f"/{int(top['イグジット'])}・ATR×{top['ATR係数']}"
              f"・4Hフィルタ{'有' if top['フィルタ'] else '無'}"
              f"・確認{tech_filters.describe_filter(top.get('確認フィルタ'))}")
    print(f"進化したシステム: {'デイトレ' if verdict == '採用' else 'なし'}")


if __name__ == "__main__":
    main()
