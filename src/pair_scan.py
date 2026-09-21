# -*- coding: utf-8 -*-
"""通貨ペア横断研究 — 現行2システムを主要ペアに適用して定期比較する

毎週のスケジュールタスクから実行され、以下を行う:
  1. 主要11ペアの日足15年を取得し、現行2システム(config.jsonに追随)で検証
  2. ドル円以外の上位Nペアには専用パラメータ最適化(ミニグリッド)も実施
  3. 「ドル円優位継続/接近/超過」を判定して標準出力に出す
  4. ファンダ加味(2026-09-21追加): 政策金利差(キャリー)・政策方向・介入警戒・
     マクロ連動(ドル指数/VIX相関)を各ペアに付け、現在のテクニカル方向と
     整合するか(追い風/逆風)を判定に注記する。判定の閾値そのものは
     テクニカルの頑健性スコアのまま(ファンダは過去の金利履歴が無く
     バックテスト不能＝注記に留め、勝手に順位を入れ替えない)
  5. results/pair_scan.csv(最新) と pair_scan_log.csv(履歴) に保存し、
     Driveダッシュボードの「ペア研究」シートへ反映

ファンダ入力は results/pair_fundamentals.json(週次タスクがWebSearchで更新)。

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

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from strategies import donchian  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUT_CSV = os.path.join(BASE_DIR, "results", "pair_scan.csv")
LOG_CSV = os.path.join(BASE_DIR, "results", "pair_scan_log.csv")
FUND_JSON = os.path.join(BASE_DIR, "results", "pair_fundamentals.json")
MACRO_CSV = os.path.join(BASE_DIR, "data", "macro_daily.csv")
FUND_STALE_DAYS = 21
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


# ------------------------------------------------------------ ファンダ加味
CCY_JA = {"USD": "米ドル", "JPY": "円", "EUR": "ユーロ", "GBP": "ポンド",
          "AUD": "豪ドル", "NZD": "NZドル", "CAD": "加ドル", "CHF": "フラン"}


def load_fund_inputs():
    """pair_fundamentals.json を読む。無い/壊れている場合はNone(ファンダ欄は空)。"""
    try:
        f = json.load(open(FUND_JSON, encoding="utf-8"))
        f["_stale"] = (date.today() - date.fromisoformat(f["更新"])).days
        return f
    except Exception as e:  # noqa: BLE001
        print(f"※ファンダ入力を読めません({type(e).__name__})→ファンダ欄は空で継続")
        return None


def load_macro():
    try:
        m = pd.read_csv(MACRO_CSV, index_col=0)
        m.index = pd.Index([pd.Timestamp(d).date() for d in m.index])
        return m
    except Exception:  # noqa: BLE001
        return None


def _nydaily(px):
    """日足Closeを1日前へ寄せる(Yahooは Close だけ前セッション終値のため。
    research_fundamental.load_usdjpy と同じ補正)。当日進行中バーは使わない。"""
    c = pd.Series(px["Close"].values, index=list(px["Date"]), dtype="float64")
    s = pd.Series(c.values[1:], index=c.index[:-1])
    return s[s.index < date.today()].dropna()


def trend_dir(c):
    """テクニカル方向: 60日騰落と100日平均線との位置が一致したときだけ方向あり。"""
    if len(c) < 130:
        return 0
    r60 = c.iloc[-1] / c.iloc[-61] - 1
    above = c.iloc[-1] > c.iloc[-100:].mean()
    if r60 > 0 and above:
        return 1
    if r60 < 0 and not above:
        return -1
    return 0


def macro_corr(c, macro, col, win=60):
    if macro is None or col not in macro.columns:
        return None
    ret = c.pct_change()
    m = macro[col].reindex(c.index).ffill()
    both = pd.concat([ret, m.pct_change()], axis=1).dropna().iloc[-win:]
    if len(both) < win * 0.8:
        return None
    return round(float(both.iloc[:, 0].corr(both.iloc[:, 1])), 2)


def fund_view(symbol, px, macro, fund):
    """1ペア分のファンダ指標。ファンダ入力が無ければ空dict。"""
    if fund is None:
        return {}
    base, quote = symbol[:3], symbol[3:6]
    rate, bias = fund["政策金利"], fund["バイアス"]
    if base not in rate or quote not in rate:
        return {}
    carry = round(rate[base] - rate[quote], 2)
    bdiff = round(bias.get(base, 0) - bias.get(quote, 0), 1)
    # ファンダ方向: 金利差が0.5pt以上ある側をキャリー方向とし、政策方向が逆なら中立
    fdir = 0
    if abs(carry) >= 0.5:
        fdir = 1 if carry > 0 else -1
        if bdiff * fdir < 0:
            fdir = 0
    elif bdiff != 0:
        fdir = 1 if bdiff > 0 else -1
    c = _nydaily(px)
    tdir = trend_dir(c)
    note = []
    # 介入警戒: 円が売られる方向(=クォートが円で買い方向)の追い風は割り引く
    warn = fund.get("介入警戒", {})
    if quote in warn and fdir > 0:
        fdir_eff, note = 0, [f"{CCY_JA[quote]}介入警戒で買い方向の追い風は割引"]
    else:
        fdir_eff = fdir
    if fdir_eff == 0 or tdir == 0:
        align = "中立"
    else:
        align = "追い風" if fdir_eff == tdir else "逆風"
    if 0 < abs(carry) < 0.5 and bdiff == 0:
        note.append("金利差ほぼ無し=ファンダ材料薄")
    label = {1: "買い", -1: "売り", 0: "中立"}
    return {
        "金利差pt": carry,
        "政策方向差": bdiff,
        "ファンダ方向": label[fdir],
        "テク方向": label[tdir],
        "ファンダ整合": align,
        "ドル指数相関60": macro_corr(c, macro, "ドル指数"),
        "VIX相関60": macro_corr(c, macro, "VIX"),
        "ファンダ注記": "・".join(note),
    }


def fund_verdict(verdict, df, home, alt):
    """テクニカル判定にファンダ整合を注記した行を返す(閾値・順位は変えない)。"""
    def al(name):
        r = df[df["ペア"] == name]
        return r["ファンダ整合"].iloc[0] if len(r) and "ファンダ整合" in r else None
    h, a = al(home), al(alt) if alt else None
    if h is None:
        return None
    hs = {"追い風": "ファンダも追い風", "逆風": "ただしファンダは逆風＝建玉の伸ばし過ぎに注意",
          "中立": "ファンダは中立"}[h]
    line = f"{verdict}／ドル円: {hs}"
    if alt and a:
        line += f"／{alt}: ファンダ{a}"
        if verdict.startswith(("接近", "要注目")):
            line += {"追い風": "＝有力な候補", "逆風": "＝見送り寄り",
                     "中立": "＝テクニカルのみで判断"}[a]
    return line


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


def append_log(hist):
    """履歴CSVへ追記。列が増えた場合は既存行を保ったまま列を拡張して書き直す
    (単純追記だと旧ヘッダーと新列がずれてCSVが壊れる)。"""
    if not os.path.exists(LOG_CSV):
        hist.to_csv(LOG_CSV, index=False)
        return
    old = pd.read_csv(LOG_CSV)
    if list(old.columns) == list(hist.columns):
        hist.to_csv(LOG_CSV, mode="a", header=False, index=False)
        return
    merged = pd.concat([old, hist], ignore_index=True)
    merged = merged[list(hist.columns) + [c for c in old.columns
                                          if c not in hist.columns]]
    merged.to_csv(LOG_CSV, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tuned", type=int, default=2,
                    help="ドル円以外の上位Nペアに専用最適化チェックを行う")
    args = ap.parse_args()

    systems = load_systems()
    fund = load_fund_inputs()
    macro = load_macro()
    if fund and fund["_stale"] > FUND_STALE_DAYS:
        print(f"⚠ ファンダ入力(pair_fundamentals.json)が{fund['_stale']}日前の値です。"
              "政策金利・バイアスをWebSearchで更新してください")
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
        rec.update(fund_view(symbol, px, macro, fund))
        rows.append(rec)
        print(f"{name}: 済 (総合score {rec['総合score']})")

    df = pd.DataFrame(rows).sort_values("総合score", ascending=False)
    cols = ["ペア", "総合score", "長期_score", "長期_PF", "長期_勝率", "長期_N",
            "短期_score", "短期_PF", "短期_勝率", "短期_N", "本数", "コスト"]
    fcols = [c for c in ("金利差pt", "政策方向差", "ファンダ方向", "テク方向",
                         "ファンダ整合", "ドル指数相関60", "VIX相関60",
                         "ファンダ注記") if c in df.columns]
    df = df[cols + fcols]
    print("\n" + df[cols].to_string(index=False))
    if fcols:
        print("\n【ファンダ加味】(政策金利=%s時点)" % fund["更新"])
        print(df[["ペア"] + fcols].to_string(index=False))

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
    fv = fund_verdict(verdict, df, HOME_PAIR, best_alt_name) if fcols else None
    if fv:
        print(f"ファンダ加味: {fv}")

    # 保存(最新+履歴) → ダッシュボード反映
    df.to_csv(OUT_CSV, index=False)
    hist = df.copy()
    hist.insert(0, "実行日", date.today().isoformat())
    hist["判定"] = verdict
    if fv:
        hist["ファンダ加味判定"] = fv
    append_log(hist)
    print(f"保存: {OUT_CSV} / 履歴: {LOG_CSV}")
    try:
        import dashboard
        dashboard.build()
    except Exception as e:
        print("ダッシュボード更新失敗:", e)


if __name__ == "__main__":
    main()
