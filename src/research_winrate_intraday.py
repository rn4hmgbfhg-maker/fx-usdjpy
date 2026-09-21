# -*- coding: utf-8 -*-
"""デイトレ複合時間軸（2H）系の勝率向上研究

background（2026-08-19 ユーザー要望）:
  日足長期トレンドの勝率研究（research_winrate.py）で判明した知見
  「勝率を動かす主因はエントリー期間の長さ（利確では上がらない）」を
  デイトレ2H系にも移植して検証する。現行グリッド(research_intraday.py の
  GRIDS[2]: entry 12-36)はエントリー期間の探索幅が狭いため、日足と同じ
  比率感覚（現行下限の1〜20倍）まで広げて再探索する。

  config.json は書き換えない（分析専用・research_winrate.py と同じ方針）。
  採用可否はユーザー判断。

使い方:  python3 src/research_winrate_intraday.py
"""
import itertools
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from data_fetch_intraday import load_merged, resample  # noqa: E402
from strategies_intraday import align_trend, donchian_mtf  # noqa: E402
import tech_filters  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

TF = 2
FILTER_TF = 4
FILTER_N = 30
RISK = 0.004
LEV = 1.5
MAX_DD_LIMIT = -0.10
FREQ_RANGE = (0.4, 1.6)
WIN_TARGET = 0.50

# 現行12-36(24h-72h)の下限〜約20倍(480h=20日)まで広げる。
# 日足長期トレンドが15→120(8倍)の探索で70前後にピークを見た先例に倣う。
ENTRY_GRID = [12, 18, 24, 36, 48, 60, 72, 96, 120, 150, 180, 240, 300, 360]
EXIT_GRID = [4, 6, 8, 10, 12, 16, 20, 24, 30]
ATR_GRID = [1.5, 2.0, 2.5, 3.0, 3.5]
TP_GRID = [0.0, 6.0, 10.0]   # 0.0=固定利確なし（ATRトレイルのみ）


def trading_days(df):
    return pd.to_datetime(df["Date"]).dt.date.nunique()


def build_dfs(df1h):
    dfe = resample(df1h, TF)
    df4 = resample(df1h, FILTER_TF)
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


def evaluate(dfs, n_e, n_x, atr_k, use_filter, tp_k=0.0, confirm_spec=None):
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
                        atr_stop_mult=atr_k, tp_atr_mult=tp_k or None)
        m = bt.run(sig)["metrics"]
        out[f"PF{key}"] = m["プロフィットファクター"]
        out[f"勝率{key}"] = m["勝率"]
        if key == "全":
            out.update({"最大DD": m["最大ドローダウン"], "取引数": m["取引回数"],
                        "総リターン": m["総リターン"],
                        "頻度": m["取引回数"] / max(trading_days(dfe), 1)})
    pf_a, pf_b = out["PF前"], out["PF後"]
    out["スコア"] = min(pf_a, pf_b) if pf_a == pf_a and pf_b == pf_b else 0.0
    w_a, w_b = out["勝率前"], out["勝率後"]
    out["勝率下限"] = min(w_a, w_b) if w_a == w_a and w_b == w_b else 0.0
    return out


def passes_gate(r):
    return (FREQ_RANGE[0] <= r["頻度"] <= FREQ_RANGE[1]
            and r["PF前"] >= 1.20 and r["PF後"] >= 1.20
            and r["最大DD"] >= MAX_DD_LIMIT)


def main():
    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    scfg = cfg["デイトレ"]
    df1h = load_merged(fetch=False)
    print(f"データ: 1時間足{len(df1h)}本 "
          f"{df1h.index[0]:%Y-%m-%d} -> {df1h.index[-1]:%Y-%m-%d}")
    dfs = build_dfs(df1h)

    cur = evaluate(dfs, int(scfg["エントリー期間"]), int(scfg["イグジット期間"]),
                  float(scfg["ATRストップ係数"]),
                  bool(scfg.get("4Hフィルタ期間", 0)),
                  float(scfg.get("利確ATR係数", 0)), scfg.get("確認フィルタ"))
    print(f"\n=== 現行 2H {scfg['エントリー期間']}/{scfg['イグジット期間']}・"
          f"ATR×{scfg['ATRストップ係数']}・利確ATR×{scfg.get('利確ATR係数', 0)}・"
          f"確認{tech_filters.describe_filter(scfg.get('確認フィルタ'))}")
    print(f"    勝率{cur['勝率全']:.1%}（前{cur['勝率前']:.1%}/後{cur['勝率後']:.1%}・"
          f"下限{cur['勝率下限']:.1%}） PF{cur['PF全']:.2f} スコア{cur['スコア']:.2f} "
          f"取引{int(cur['取引数'])} 頻度{cur['頻度']:.2f} DD{cur['最大DD']:.1%}")

    print(f"\n=== 広域探索: entry{len(ENTRY_GRID)}×exit{len(EXIT_GRID)}×"
          f"ATR{len(ATR_GRID)}×フィルタ2×TP{len(TP_GRID)} ===")
    rows = []
    combos = [(e, x) for e, x in itertools.product(ENTRY_GRID, EXIT_GRID)
              if x < e]
    total = len(combos) * len(ATR_GRID) * 2 * len(TP_GRID)
    done = 0
    for n_e, n_x in combos:
        for flt in (False, True):
            for atr_k in ATR_GRID:
                for tp in TP_GRID:
                    r = evaluate(dfs, n_e, n_x, atr_k, flt, tp)
                    r.update({"エントリー": n_e, "イグジット": n_x,
                              "ATR係数": atr_k, "フィルタ": flt, "利確ATR": tp})
                    rows.append(r)
                    done += 1
        if done % 500 < len(ATR_GRID) * 2 * len(TP_GRID):
            print(f"  ...{done}/{total}", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(BASE_DIR, "results",
                            "research_winrate_intraday_raw.csv"),
              index=False, encoding="utf-8")

    # --- エントリー期間 vs 勝率の傾向（ゲート無視・各entryの最高スコア行）
    print("\n=== エントリー期間ごとの最良スコア候補（傾向確認） ===")
    show = ["エントリー", "イグジット", "ATR係数", "フィルタ", "利確ATR",
            "スコア", "PF全", "勝率全", "勝率下限", "最大DD", "取引数", "頻度"]
    for e in ENTRY_GRID:
        sub = res[res["エントリー"] == e]
        if not len(sub):
            continue
        top = sub.sort_values("スコア", ascending=False).iloc[0]
        print(f"  entry={e:>3}: 勝率{top['勝率全']:.1%}(下限{top['勝率下限']:.1%}) "
              f"PF{top['PF全']:.2f} スコア{top['スコア']:.2f} "
              f"頻度{top['頻度']:.2f} 取引{int(top['取引数'])} "
              f"DD{top['最大DD']:.1%}"
              f"{' [頻度未達]' if top['頻度'] < FREQ_RANGE[0] else ''}")

    ok = res[res.apply(passes_gate, axis=1)]
    print(f"\n=== 頻度・PFゲート通過: {len(ok)}/{len(res)}件 ===")
    if len(ok):
        print("--- 勝率順 上位10 ---")
        print(ok.sort_values("勝率全", ascending=False)[show]
              .head(10).to_string(index=False))
        print("\n--- スコア順 上位5 ---")
        print(ok.sort_values("スコア", ascending=False)[show]
              .head(5).to_string(index=False))
    else:
        print("（ゲート通過なし。参考: 全体スコア上位5）")
        print(res.sort_values("スコア", ascending=False)[show]
              .head(5).to_string(index=False))

    # --- 上位候補に確認フィルタを重ねる（2段目）
    base_pool = sorted((ok.to_dict("records") if len(ok)
                        else res.to_dict("records")),
                       key=lambda r: -r["勝率全"])[:5]
    print(f"\n=== 勝率上位{len(base_pool)}候補へ確認フィルタを重ねる ===")
    refined = []
    for row in base_pool:
        for fspec in [None] + tech_filters.DEFAULT_GRID:
            r = evaluate(dfs, int(row["エントリー"]), int(row["イグジット"]),
                        row["ATR係数"], row["フィルタ"], row["利確ATR"], fspec)
            r.update({"エントリー": row["エントリー"], "イグジット": row["イグジット"],
                      "ATR係数": row["ATR係数"], "フィルタ": row["フィルタ"],
                      "利確ATR": row["利確ATR"], "確認": fspec})
            refined.append(r)
    ref_ok = [r for r in refined if passes_gate(r)]
    print(f"確認フィルタ込み{len(refined)}件中ゲート通過{len(ref_ok)}件")
    for r in sorted(ref_ok, key=lambda x: -x["勝率全"])[:8]:
        print(f"  entry{int(r['エントリー'])}/exit{int(r['イグジット'])} "
              f"ATR×{r['ATR係数']} TP×{r['利確ATR']} "
              f"フィルタ{'有' if r['フィルタ'] else '無'} "
              f"確認{tech_filters.describe_filter(r['確認'])}: "
              f"勝率{r['勝率全']:.1%}(下限{r['勝率下限']:.1%}) "
              f"PF{r['PF全']:.2f} スコア{r['スコア']:.2f} "
              f"頻度{r['頻度']:.2f} 取引{int(r['取引数'])}")

    print(f"\n現行との比較: 勝率 {cur['勝率全']:.1%} → "
          f"最良 {max([cur['勝率全']] + [r['勝率全'] for r in ref_ok] if ref_ok else [cur['勝率全']]):.1%}")


if __name__ == "__main__":
    main()
