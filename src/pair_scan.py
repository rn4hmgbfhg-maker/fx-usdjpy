# -*- coding: utf-8 -*-
"""通貨ペア横断研究 — 現行2システムを主要ペアに適用して定期比較する

毎週のスケジュールタスクから実行され、以下を行う:
  1. 主要11ペアの日足15年を取得し、現行2システム(config.jsonに追随)で検証
  2. ドル円以外の上位Nペアには専用パラメータ最適化(ミニグリッド)も実施
  3. 「ドル円優位継続/接近/超過」を判定して標準出力に出す
  4. results/pair_scan.csv(最新) と pair_scan_log.csv(履歴) に保存し、
     Driveダッシュボードの「ペア研究」シートへ反映

各ペアのコストはJFX水準のスプレッド(一部想定値)+滑りの片道換算。
PF・勝率・頑健スコア(前後半PFの小さい方)はポジションサイズにほぼ依存しないため、
クォート通貨の違い(円建て/ドル建て)があっても比較指標として有効。

使い方: python3 src/pair_scan.py [--tuned 2]
"""
import argparse
import itertools
import json
import os
import sys
import time
from datetime import date

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from strategies import donchian  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUT_CSV = os.path.join(BASE_DIR, "results", "pair_scan.csv")
LOG_CSV = os.path.join(BASE_DIR, "results", "pair_scan_log.csv")
SPLIT = "2018-12-31"
HOME_PAIR = "米ドル/円"

# (Yahooシンボル, 表示名, 片道コスト[クォート通貨], コスト根拠)
PAIRS = [
    ("USDJPY=X", "米ドル/円", 0.004, "スプ0.2銭+滑り(公式確認済)"),
    ("EURJPY=X", "ユーロ/円", 0.006, "スプ0.4銭+滑り(公式確認済)"),
    ("GBPJPY=X", "ポンド/円", 0.011, "スプ0.9銭+滑り(公式確認済)"),
    ("AUDJPY=X", "豪ドル/円", 0.008, "スプ0.6銭想定+滑り"),
    ("NZDJPY=X", "NZドル/円", 0.012, "スプ1.0銭想定+滑り"),
    ("CADJPY=X", "加ドル/円", 0.018, "スプ1.6銭想定+滑り"),
    ("CHFJPY=X", "フラン/円", 0.020, "スプ1.8銭想定+滑り"),
    ("EURUSD=X", "ユーロ/ドル", 0.00006, "スプ0.4p想定+滑り"),
    ("GBPUSD=X", "ポンド/ドル", 0.00012, "スプ1.0p想定+滑り"),
    ("AUDUSD=X", "豪ドル/ドル", 0.00008, "スプ0.6p想定+滑り"),
    ("EURGBP=X", "ユーロ/ポンド", 0.00010, "スプ1.0p想定+滑り"),
]

TUNE_GRID = ([20, 30, 40, 55], [5, 10, 15], [2.5, 3.5])


def load_systems():
    """現行パラメータをconfig.jsonから読む(研究進化に自動追随)。"""
    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    out = {}
    for name, s in cfg["システム"].items():
        if not s.get("有効", True):
            continue
        key = "長期" if "長期" in name else "短期"
        out[key] = {"n": int(s["エントリー期間"]), "x": int(s["イグジット期間"]),
                    "atr": float(s["ATRストップ係数"]),
                    "risk": float(s["リスク率"])}
    return out


def fetch(symbol, tries=3):
    for i in range(tries):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            r = requests.get(url, params={"range": "15y", "interval": "1d"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({
                "Date": pd.to_datetime(res["timestamp"], unit="s")
                          .tz_localize("UTC").tz_convert("Asia/Tokyo").date,
                "Open": q["open"], "High": q["high"], "Low": q["low"],
                "Close": q["close"],
            })
            return df.dropna().reset_index(drop=True)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(8)


def split3(px):
    d = pd.to_datetime(px["Date"])
    return {"全": px,
            "前": px[d <= SPLIT].reset_index(drop=True),
            "後": px[d > SPLIT].reset_index(drop=True)}


def run_bt(sub, n, x, atr_k, risk, cost):
    bt = Backtester(sub, risk_per_trade=risk, max_leverage=1.5,
                    atr_stop_mult=atr_k, cost_per_side=cost)
    return bt.run(donchian(sub, n=n, exit_n=x))["metrics"]


def tuned_best(px, cost):
    subs = split3(px)
    best = None
    for ne, nx, ak in itertools.product(*TUNE_GRID):
        if nx >= ne:
            continue
        pf = {}
        for k, sub in subs.items():
            m = run_bt(sub, ne, nx, ak, 0.01, cost)
            pf[k] = (m["プロフィットファクター"], m["勝率"], m["取引回数"])
        s = min(pf["前"][0], pf["後"][0])
        if s == s and (best is None or s > best["score"]):
            best = {"score": round(s, 2), "params": f"{ne}/{nx}・ATR×{ak}",
                    "PF全": round(pf["全"][0], 2),
                    "勝率": round(pf["全"][1] * 100, 1), "N": pf["全"][2]}
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tuned", type=int, default=2,
                    help="ドル円以外の上位Nペアに専用最適化チェックを行う")
    args = ap.parse_args()

    systems = load_systems()
    rows = []
    fetched = {}
    for symbol, name, cost, basis in PAIRS:
        try:
            px = fetch(symbol)
        except Exception as e:
            print(f"{name}: データ取得失敗 {e}")
            continue
        fetched[name] = (px, cost)
        subs = split3(px)
        rec = {"ペア": name, "コスト": basis, "本数": len(px)}
        for sname, sp in systems.items():
            pf = {}
            for key, sub in subs.items():
                m = run_bt(sub, sp["n"], sp["x"], sp["atr"], sp["risk"], cost)
                pf[key] = m["プロフィットファクター"]
                if key == "全":
                    rec[f"{sname}_PF"] = round(m["プロフィットファクター"], 2)
                    rec[f"{sname}_勝率"] = round(m["勝率"] * 100, 1)
                    rec[f"{sname}_N"] = m["取引回数"]
            a, b = pf["前"], pf["後"]
            rec[f"{sname}_score"] = round(min(a, b), 2) \
                if a == a and b == b else 0.0
        rec["総合score"] = round((rec.get("長期_score", 0)
                                  + rec.get("短期_score", 0)) / 2, 2)
        rows.append(rec)
        print(f"{name}: 済 (総合score {rec['総合score']})")

    df = pd.DataFrame(rows).sort_values("総合score", ascending=False)
    cols = ["ペア", "総合score", "長期_score", "長期_PF", "長期_勝率", "長期_N",
            "短期_score", "短期_PF", "短期_勝率", "短期_N", "本数", "コスト"]
    df = df[cols]
    print("\n" + df.to_string(index=False))

    # ドル円以外の上位Nペアへ専用最適化チェック
    home_row = df[df["ペア"] == HOME_PAIR]
    home_score = float(home_row["長期_score"].iloc[0]) if len(home_row) else 0.0
    tuned_results = {}
    if args.tuned > 0:
        alts = [p for p in df["ペア"] if p != HOME_PAIR][:args.tuned]
        for name in alts:
            if name not in fetched:
                continue
            px, cost = fetched[name]
            b = tuned_best(px, cost)
            tuned_results[name] = b
            print(f"\n{name} 専用最適化: score{b['score']}"
                  f" (ドンチャン{b['params']}) PF全{b['PF全']} 勝率{b['勝率']}% N{b['N']}")

    # 判定
    best_alt_name, best_alt_score = None, 0.0
    for name, b in tuned_results.items():
        if b["score"] > best_alt_score:
            best_alt_name, best_alt_score = name, b["score"]
    if best_alt_score >= home_score:
        verdict = (f"要注目: {best_alt_name}(最適化score{best_alt_score})が"
                   f"ドル円(長期score{home_score})に並ぶ/超過")
    elif best_alt_score >= home_score * 0.9:
        verdict = (f"接近: {best_alt_name}(最適化score{best_alt_score})が"
                   f"ドル円(長期score{home_score})の9割圏内")
    else:
        verdict = (f"ドル円優位継続(長期score{home_score}、"
                   f"次点{best_alt_name} {best_alt_score})")
    print(f"\n判定: {verdict}")

    # 保存(最新+履歴) → ダッシュボード反映
    df.to_csv(OUT_CSV, index=False)
    hist = df.copy()
    hist.insert(0, "実行日", date.today().isoformat())
    hist["判定"] = verdict
    new = not os.path.exists(LOG_CSV)
    hist.to_csv(LOG_CSV, mode="a", header=new, index=False)
    print(f"保存: {OUT_CSV} / 履歴: {LOG_CSV}")
    try:
        import dashboard
        dashboard.build()
    except Exception as e:
        print("ダッシュボード更新失敗:", e)


if __name__ == "__main__":
    main()
