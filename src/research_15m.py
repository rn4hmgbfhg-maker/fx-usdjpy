# -*- coding: utf-8 -*-
"""デイトレ15分システムの研究モジュール（初期選定＋日次自動進化）

世界のデイトレ実務で実績のある5系統（ドンチャンMTF・オープニングレンジ
ブレイクアウト・ロンドンのアジアレンジ抜け・EMA+RSI押し目・スクイーズ
ブレイク）を15分足で横断バックテストし、頑健性ゲートで選別する。

ゲート（全て満たした候補のみ採用対象）:
  ・売買頻度: 1営業日あたり0.3〜4.0回転（デイトレ帯）
  ・PF: 前半・後半とも1.10以上（期間分割の頑健性。15分足は片道0.4銭の
    コスト負担が重いためハードルは2H系よりやや低く設定）
  ・最大DD: -10%以内（リスク率0.3%想定）
  ・スコア: min(PF前, PF後)。現行がある場合は1.05倍以上で置換
  ・1次選抜後、上位候補に利確ATR係数(0/2/3/4)を重ねて最終決定

使い方:
  python3 src/research_15m.py            # 探索+判定+必要なら採用
  python3 src/research_15m.py --no-adopt # 探索のみ
  python3 src/research_15m.py --seed     # config未設定時の初期採用
"""
import argparse
import json
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from data_fetch_15m import load_15m  # noqa: E402
import strategies_15m as s15  # noqa: E402
import tech_filters  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "results", "research_15m_log.csv")

IMPROVE_RATIO = 1.05
MAX_DD_LIMIT = -0.10
MIN_PF_HALF = 1.10
FREQ_RANGE = (0.3, 4.0)      # 回転/営業日（デイトレ帯）
RISK = 0.003                 # リスク率0.3%（第4システムとして控えめに）
LEV = 1.5
# 利確ATR係数グリッド。0(利確なし)は含めない=利確指値は必ず装備する
# （2026-08-13ユーザー指示。OCOで逆指値とセット運用が前提のため）
TP_GRID = [2.0, 3.0, 4.0, 6.0]

CFG_KEY = "デイトレ15分"


def candidate_specs():
    """探索する戦略スペック一覧（15分足に加え1時間足・4時間足も候補にする。
    2026-08-13ユーザー指示）。セッション系（ORB・アジアレンジ）は
    セッション構造が15分粒度に依存するため15分足のみ。"""
    specs = []
    # --- 15分足
    for n, x in [(24, 6), (24, 12), (48, 6), (48, 12), (96, 12), (96, 24)]:
        for filt in [None, 2]:
            specs.append({"時間足分": 15, "戦略ID": "donchian",
                          "エントリー期間": n, "イグジット期間": x,
                          "フィルタ時間足": filt, "フィルタ期間": 30})
    for sess in [0, 7]:
        for rb in [2, 4]:
            specs.append({"時間足分": 15, "戦略ID": "orb",
                          "セッション開始UTC": sess, "レンジ本数": rb,
                          "窓時間": 6, "手仕舞いUTC": 21})
    specs.append({"時間足分": 15, "戦略ID": "london_asian"})
    for f, s in [(50, 200), (20, 100)]:
        specs.append({"時間足分": 15, "戦略ID": "ema_rsi", "EMA短期": f,
                      "EMA長期": s, "RSI期間": 2, "RSI閾値": 10,
                      "RSI手仕舞い": 50})
    specs.append({"時間足分": 15, "戦略ID": "squeeze", "BB期間": 20,
                  "BB係数": 2.0, "収縮分位": 0.2, "収縮参照本数": 400,
                  "イグジット期間": 12})
    # --- 1時間足
    for n, x in [(24, 6), (24, 12), (36, 8), (48, 12)]:
        for filt in [None, 4]:
            specs.append({"時間足分": 60, "戦略ID": "donchian",
                          "エントリー期間": n, "イグジット期間": x,
                          "フィルタ時間足": filt, "フィルタ期間": 30})
    for f, s in [(50, 200), (20, 100)]:
        specs.append({"時間足分": 60, "戦略ID": "ema_rsi", "EMA短期": f,
                      "EMA長期": s, "RSI期間": 2, "RSI閾値": 10,
                      "RSI手仕舞い": 50})
    specs.append({"時間足分": 60, "戦略ID": "squeeze", "BB期間": 20,
                  "BB係数": 2.0, "収縮分位": 0.2, "収縮参照本数": 400,
                  "イグジット期間": 12})
    # --- 4時間足
    for n, x in [(12, 4), (12, 6), (18, 6), (24, 8)]:
        specs.append({"時間足分": 240, "戦略ID": "donchian",
                      "エントリー期間": n, "イグジット期間": x,
                      "フィルタ時間足": None, "フィルタ期間": 30})
    specs.append({"時間足分": 240, "戦略ID": "ema_rsi", "EMA短期": 20,
                  "EMA長期": 100, "RSI期間": 2, "RSI閾値": 10,
                  "RSI手仕舞い": 50})
    specs.append({"時間足分": 240, "戦略ID": "squeeze", "BB期間": 20,
                  "BB係数": 2.0, "収縮分位": 0.2, "収縮参照本数": 200,
                  "イグジット期間": 12})
    return specs


def build_df15(raw: pd.DataFrame) -> pd.DataFrame:
    """UTC索引の15分足→Backtester互換DataFrame（Date=バー終了JST・utc列付き）。"""
    df = raw.reset_index()
    df["utc"] = df["Time"]
    df["Date"] = (df["Time"] + pd.Timedelta(minutes=15)).dt.tz_convert(
        "Asia/Tokyo").dt.strftime("%Y-%m-%d %H:%M")
    return df[["Date", "utc", "Open", "High", "Low", "Close"]]


def trading_days(df):
    return pd.to_datetime(df["Date"]).dt.date.nunique()


def evaluate(tf_dfs, spec, atr_k, tp_k=0.0):
    dfs = tf_dfs[int(spec.get("時間足分", 15))]
    # 確認フィルタのマスクは全期間で1回計算し、前半/後半へは位置スライスで
    # 渡す（分割フレーム上の再計算は指標が分割点から再ウォームアップされ、
    # 後半PFが本番=全履歴計算のエンジンと異なる条件で測られるため）
    full_mask = None
    if spec.get("戦略ID") == "donchian" and spec.get("確認フィルタ"):
        full_mask = tech_filters.entry_mask(dfs["全"], spec["確認フィルタ"])
    out = {}
    for key, dfe in dfs.items():
        confirm = None
        if full_mask is not None:
            n = len(dfe)
            confirm = (full_mask if key == "全" else
                       (full_mask[0][:n], full_mask[1][:n]) if key == "前"
                       else (full_mask[0][-n:], full_mask[1][-n:]))
        sig = s15.build_signal(dfe, spec, confirm)
        bt = Backtester(dfe, risk_per_trade=RISK, max_leverage=LEV,
                        atr_stop_mult=atr_k, tp_atr_mult=tp_k or None)
        m = bt.run(sig)["metrics"]
        out[f"PF{key}"] = m["プロフィットファクター"]
        if key == "全":
            out.update({"勝率": m["勝率"], "最大DD": m["最大ドローダウン"],
                        "取引数": m["取引回数"], "総リターン": m["総リターン"],
                        "頻度": m["取引回数"] / max(trading_days(dfe), 1)})
    pf_a, pf_b = out["PF前"], out["PF後"]
    out["スコア"] = min(pf_a, pf_b) if pf_a == pf_a and pf_b == pf_b else 0.0
    return out


def passes_gate(r):
    return (FREQ_RANGE[0] <= r["頻度"] <= FREQ_RANGE[1]
            and r["PF前"] >= MIN_PF_HALF and r["PF後"] >= MIN_PF_HALF
            and r["最大DD"] >= MAX_DD_LIMIT and r["取引数"] >= 60)


def scan(tf_dfs, atr_grid=(2.0, 3.0)):
    rows = []
    for spec in candidate_specs():
        for atr_k in atr_grid:
            try:
                r = evaluate(tf_dfs, spec, atr_k)
            except Exception as e:
                print(f"評価失敗 {spec.get('戦略ID')}: {e}")
                continue
            rows.append({"spec": spec, "ATR係数": atr_k, "利確ATR係数": 0.0,
                         "戦略": s15.describe(spec), **r})
    return rows


def refine_filters(tf_dfs, top_rows):
    """上位候補（ドンチャン系のみ）に5系統の確認フィルタ
    （ADX・RSIレジーム・MACD・MA合議・ATR分位）を重ねた候補を作る。
    確認フィルタはspec（=configパラメータ）に内包されるため、
    エンジン側 build_signal と自動的に整合する。"""
    out = []
    for row in top_rows:
        if row["spec"].get("戦略ID") != "donchian":
            continue
        for fspec in tech_filters.DEFAULT_GRID:
            spec2 = {**row["spec"], "確認フィルタ": fspec}
            r = evaluate(tf_dfs, spec2, row["ATR係数"])
            out.append({"spec": spec2, "ATR係数": row["ATR係数"],
                        "利確ATR係数": 0.0, "戦略": s15.describe(spec2), **r})
    return out


def refine_tp(tf_dfs, top_rows):
    """上位候補に利確係数グリッドを重ねて最終候補を作る。"""
    refined = []
    for row in top_rows:
        for tp_k in TP_GRID:
            if tp_k == 0.0:
                refined.append(row)
                continue
            r = evaluate(tf_dfs, row["spec"], row["ATR係数"], tp_k)
            refined.append({**row, **r, "利確ATR係数": tp_k})
    return refined


def current_score(tf_dfs, cfg):
    scfg = cfg.get(CFG_KEY)
    if not scfg or "パラメータ" not in scfg:
        return None, None
    spec = scfg["パラメータ"]
    r = evaluate(tf_dfs, spec, float(scfg["ATRストップ係数"]),
                 float(scfg.get("利確ATR係数", 0)))
    return r["スコア"], r


def adopt(cfg, row):
    cfg[CFG_KEY] = {
        "有効": True,
        "試験運転": True,
        "戦略ID": row["spec"]["戦略ID"],
        "戦略": row["戦略"],
        "パラメータ": row["spec"],
        "ATRストップ係数": row["ATR係数"],
        "利確ATR係数": row["利確ATR係数"],
        "リスク率": RISK,
        "レバ上限": LEV,
        "採用日": date.today().isoformat(),
        "採用スコア": round(row["スコア"], 3),
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def log_row(row, verdict):
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,戦略,ATR係数,利確ATR係数,PF全,PF前,PF後,勝率,最大DD,"
                    "取引数,頻度_回転毎営業日,スコア,判定\n")
        f.write(",".join(map(str, [
            date.today().isoformat(), row["戦略"].replace(",", "・"),
            row["ATR係数"], row["利確ATR係数"],
            round(row["PF全"], 2), round(row["PF前"], 2),
            round(row["PF後"], 2), f"{row['勝率']:.1%}",
            f"{row['最大DD']:.1%}", int(row["取引数"]),
            round(row["頻度"], 2), round(row["スコア"], 2), verdict])) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-adopt", action="store_true")
    ap.add_argument("--seed", action="store_true")
    args = ap.parse_args()

    raw = load_15m()
    df = build_df15(raw)
    print(f"データ: 15分足{len(df)}本 {df['Date'].iloc[0]} -> "
          f"{df['Date'].iloc[-1]}")
    tf_dfs = {}
    for tf in (15, 60, 240):
        dtf = s15.to_tf(df, tf)
        half = len(dtf) // 2
        tf_dfs[tf] = {"全": dtf,
                      "前": dtf.iloc[:half].reset_index(drop=True),
                      "後": dtf.iloc[half:].reset_index(drop=True)}
        if tf != 15:
            print(f"  {tf}分足: {len(dtf)}本")

    rows = scan(tf_dfs)
    gated = [r for r in rows if passes_gate(r)]
    pool = sorted(gated or rows, key=lambda r: -r["スコア"])

    disp = pd.DataFrame([{k: v for k, v in r.items() if k != "spec"}
                         for r in pool[:10]])
    print(f"\n=== ゲート通過 {len(gated)}件 / 全{len(rows)}件  上位10 ===")
    print(disp.to_string(index=False))

    # 上位3候補に確認フィルタ（5系統テクニカル）を重ねてから利確グリッドへ
    fil = refine_filters(tf_dfs, pool[:3])
    fil_ok = [r for r in fil if passes_gate(r)]
    if fil:
        print(f"\n確認フィルタ探索: {len(fil)}件中ゲート通過{len(fil_ok)}件")
        for r in sorted(fil, key=lambda x: -x["スコア"])[:5]:
            print(f"  {r['戦略']} ATR×{r['ATR係数']}: スコア{r['スコア']:.2f}"
                  f"（PF前{r['PF前']:.2f}/後{r['PF後']:.2f}"
                  f"・{r['頻度']:.2f}回転/日"
                  f"{'・ゲート通過' if passes_gate(r) else ''}）")
    pool2 = sorted(pool[:3] + fil_ok, key=lambda r: -r["スコア"])[:3]

    # 上位3候補に利確グリッドを重ねる
    refined = refine_tp(tf_dfs, pool2)
    ref_pass = [r for r in refined if passes_gate(r)]
    # 頑健性ゲートは進化採用の必要条件。全滅時はスコア最大を記録のみして
    # 現状維持（無検証の候補をconfigへ流さない）。--seedは初期起動用の例外。
    gate_ok = bool(ref_pass)
    best = max(ref_pass or refined, key=lambda r: r["スコア"])
    print(f"\n最終候補: {best['戦略']} ATR×{best['ATR係数']}"
          f" 利確×{best['利確ATR係数']} スコア{best['スコア']:.2f}"
          f"（PF前{best['PF前']:.2f}/後{best['PF後']:.2f}"
          f"・{best['頻度']:.2f}回転/日"
          f"{'・ゲート通過' if gate_ok else '・⚠ゲート全滅'}）")

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    cur_score, cur = current_score(tf_dfs, cfg)

    if args.no_adopt:
        log_row(best, "探索のみ")
        return
    if cur_score is None:
        adopt(cfg, best)
        log_row(best, "初期採用" if gate_ok else "初期採用(ゲート外)")
        print("→ 初期採用しました")
        return
    print(f"現行 {cfg[CFG_KEY]['戦略']}: スコア{cur_score:.2f}")
    # 同一判定はspecに加えATRストップ係数・利確ATR係数も比較する
    # （spec同一でも係数だけの改善は採用対象。従来はここで棄却されていた）
    same_as_current = (
        best["spec"] == cfg[CFG_KEY].get("パラメータ")
        and float(best["ATR係数"]) == float(cfg[CFG_KEY]["ATRストップ係数"])
        and float(best["利確ATR係数"]) == float(
            cfg[CFG_KEY].get("利確ATR係数", 0)))
    if not gate_ok:
        log_row(best, "ゲート全滅・現状維持")
        print("→ 現状維持（判定: 最終候補が頑健性ゲート全滅のため採用見送り）")
    elif best["スコア"] >= cur_score * IMPROVE_RATIO and not same_as_current:
        adopt(cfg, best)
        log_row(best, "進化採用")
        print("→ 進化採用しました")
    else:
        log_row(best, "現状維持")
        print("→ 現状維持（判定: 現行が最良またはスコア差1.05倍未満）")


if __name__ == "__main__":
    main()
