# -*- coding: utf-8 -*-
"""スキャルピング研究 v2（多時間足×多数の時間帯幅・2026-10-12 落合さん指示）

目的: ボードの「スキャル分析」の勝率を上げるため、どの時間足・どの入り方・
どの時間帯なら勝ちやすいかを過去データで数字にする。結果は
results/research_scalp_latest.json に保存し、ボードのスキャル欄と
「スキャル分析」のプロンプトがそのまま読む。

検証の軸（全組み合わせを総当たり）:
  時間足   : 15分・30分・1時間・2時間・4時間・日足（15分足を束ねて作る）
  入り方   : rsi_rev（RSI行き過ぎの戻り）／bb_rev（ボリンジャー外→内）／
             range_brk（直近4本レンジ抜け）／ema_pull（EMA20押し目・戻り）
  順行     : その時間足のEMA200の向きと同方向のみ／制限なし
  損切り   : ATR×0.5／×1.0   利確: ATR×0.75／×1.0／×1.5（時間足ごとのATRで統一）
  保有上限 : 4本／8本／16本（時間足が長いほど長く持つ）
  時間帯幅 : 入った時刻（JST）を 1時間／2時間／3時間／6時間 の幅で区切り、
             幅ごとの勝率を出す（「時間帯幅を多数設けて」）

共通: 往復コスト0.8pips・同じ足で損切りと利確の両方に触れたら損切り扱い。

使い方:
  python3 src/research_scalp.py          # 全部（約1分）
  python3 src/research_scalp.py --quick  # 直近1年だけ
"""
import argparse
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE_DIR, "data")
RES = os.path.join(BASE_DIR, "results")
OUT_JSON = os.path.join(RES, "research_scalp_latest.json")
LOG_CSV = os.path.join(RES, "research_scalp_log.csv")

COST_PIPS = 0.8
TFS = {"15分": "15min", "30分": "30min", "1時間": "1h", "2時間": "2h",
       "4時間": "4h", "日足": "1D"}
METHODS = {"rsi_rev": "RSI行き過ぎの戻り", "bb_rev": "ボリンジャー外→内の戻り",
           "range_brk": "直近4本レンジのブレイク", "ema_pull": "EMA20押し目/戻り"}
SL_ATR = [0.5, 1.0]
TP_ATR = [0.75, 1.0, 1.5]
HOLDS = [4, 8, 16]
BAND_WIDTHS = [1, 2, 3, 6]
MIN_TRADES = {"15分": 150, "30分": 120, "1時間": 100, "2時間": 80, "4時間": 60, "日足": 40}
MIN_BAND = 40


def load_15m():
    frames = []
    for name in ("usdjpy_15m_hist.csv", "usdjpy_15m.csv"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            df = pd.read_csv(p)
            df["Time"] = pd.to_datetime(df["Time"], utc=True)
            frames.append(df)
    if not frames:
        raise SystemExit("15分足データがありません")
    df = pd.concat(frames).drop_duplicates("Time", keep="last") \
        .sort_values("Time").set_index("Time")
    df = df[["Open", "High", "Low", "Close"]].astype(float).dropna()
    df.index = df.index.tz_convert("Asia/Tokyo")
    return df


def resample(df, rule):
    if rule == "15min":
        return df.copy()
    o = df.resample(rule, label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return o


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def add_indicators(df):
    c = df["Close"]
    df["rsi"] = rsi(c)
    for n in (20, 50, 200):
        df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_up"], df["bb_lo"] = ma + 2 * sd, ma - 2 * sd
    df["hh4"] = df["High"].rolling(4).max().shift(1)
    df["ll4"] = df["Low"].rolling(4).min().shift(1)
    df["trend"] = np.sign(df["ema200"].diff(4)).fillna(0)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                    (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean() * 100          # pips
    df["hour"] = df.index.hour
    return df


def signals(df, name):
    c, s = df["Close"], pd.Series(0, index=df.index)
    if name == "rsi_rev":
        s[df["rsi"] < 30] = 1
        s[df["rsi"] > 70] = -1
    elif name == "bb_rev":
        s[(c.shift(1) < df["bb_lo"].shift(1)) & (c >= df["bb_lo"])] = 1
        s[(c.shift(1) > df["bb_up"].shift(1)) & (c <= df["bb_up"])] = -1
    elif name == "range_brk":
        s[c > df["hh4"]] = 1
        s[c < df["ll4"]] = -1
    elif name == "ema_pull":
        up = df["ema20"] > df["ema50"]
        s[up & (c.shift(1) < df["ema20"].shift(1)) & (c > df["ema20"])] = 1
        s[(~up) & (c.shift(1) > df["ema20"].shift(1)) & (c < df["ema20"])] = -1
    return s


def simulate(df, sig, sl_k, tp_k, hold, trend_only):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    atr, tr, hours = df["atr"].values, df["trend"].values, df["hour"].values
    idx = np.where(sig.values != 0)[0]
    out, last_exit, n = [], -1, len(df)
    for i in idx:
        if i + 1 >= n or i <= last_exit or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        d = int(sig.values[i])
        if trend_only and tr[i] != d:
            continue
        sl, tp = sl_k * atr[i], tp_k * atr[i]
        entry = o[i + 1]
        stop, take = entry - d * sl / 100, entry + d * tp / 100
        pnl, j_exit = None, None
        for j in range(i + 1, min(i + 1 + hold, n)):
            hit_sl = (l[j] <= stop) if d > 0 else (h[j] >= stop)
            hit_tp = (h[j] >= take) if d > 0 else (l[j] <= take)
            if hit_sl:
                pnl, j_exit = -sl, j
                break
            if hit_tp:
                pnl, j_exit = tp, j
                break
        if pnl is None:
            j_exit = min(i + hold, n - 1)
            pnl = (c[j_exit] - entry) * d * 100
        out.append((d, pnl - COST_PIPS, hours[i + 1]))
        last_exit = j_exit
    return pd.DataFrame(out, columns=["dir", "pips", "hour"])


def metrics(t):
    if len(t) == 0:
        return {"取引数": 0, "勝率": 0, "PF": 0, "PF前": 0, "PF後": 0, "平均pips": 0}
    w = t["pips"] > 0
    pf = lambda x: round(float(x[x > 0].sum() / max(1e-9, -x[x <= 0].sum())), 2) if len(x) else 0  # noqa: E731
    half = len(t) // 2
    return {"取引数": int(len(t)), "勝率": round(float(w.mean()), 3),
            "PF": min(pf(t["pips"]), 9.99), "PF前": pf(t["pips"].iloc[:half]),
            "PF後": pf(t["pips"].iloc[half:]), "平均pips": round(float(t["pips"].mean()), 2)}


def band_tables(t):
    """時間帯幅 1/2/3/6 時間ごとの勝率表。{幅: {"開始時": {...}}}"""
    tabs = {}
    for w in BAND_WIDTHS:
        rows = {}
        for start in range(0, 24, w):
            x = t[(t["hour"] >= start) & (t["hour"] < start + w)]
            if len(x) >= MIN_BAND:
                m = metrics(x)
                rows[f"{start:02d}-{start + w:02d}時"] = {"勝率": m["勝率"], "PF": m["PF"],
                                                          "取引数": m["取引数"]}
        tabs[f"{w}時間幅"] = rows
    return tabs


def label(m):
    return (f"{METHODS[m['入り方']]}" + ("・EMA200と同方向のみ" if m["順行のみ"] else "")
            + f"（損切りATR×{m['損切りATR']}／利確ATR×{m['利確ATR']}／最長{m['保有本数']}本）")


def run(quick=False):
    base = load_15m()
    if quick:
        base = base[base.index >= base.index[-1] - pd.Timedelta(days=365)]
    report, all_best = {}, []
    for tf_name, rule in TFS.items():
        df = add_indicators(resample(base, rule))
        results = []
        for mname in METHODS:
            sig = signals(df, mname)
            for trend_only in (False, True):
                for sl_k in SL_ATR:
                    for tp_k in TP_ATR:
                        for hold in HOLDS:
                            t = simulate(df, sig, sl_k, tp_k, hold, trend_only)
                            m = metrics(t)
                            if m["取引数"] < MIN_TRADES[tf_name]:
                                continue
                            m.update({"入り方": mname, "順行のみ": trend_only,
                                      "損切りATR": sl_k, "利確ATR": tp_k, "保有本数": hold,
                                      "スコア": round(min(m["PF前"], m["PF後"]), 2),
                                      "_trades": t})
                            results.append(m)
        results.sort(key=lambda m: (m["スコア"], m["PF"], m["勝率"]), reverse=True)
        top = results[:6]
        # 最良条件の時間帯幅ごとの表＋同じ入り方の全変種合算の時間帯表（件数を確保）
        bands_best = band_tables(top[0]["_trades"]) if top else {}
        same = pd.concat([m["_trades"] for m in results if top and m["入り方"] == top[0]["入り方"]]) \
            if top else pd.DataFrame(columns=["dir", "pips", "hour"])
        bands_all = band_tables(same) if len(same) else {}
        good, bad = [], []
        for w, rows in bands_all.items():
            for k, v in rows.items():
                if v["勝率"] >= 0.52 and v["PF"] >= 1.05:
                    good.append(f"{k}（{w}・勝率{v['勝率']:.0%}）")
                if v["勝率"] <= 0.42 or v["PF"] <= 0.85:
                    bad.append(f"{k}（{w}・勝率{v['勝率']:.0%}）")
        atr_now = float(df["atr"].dropna().iloc[-1]) if len(df["atr"].dropna()) else None
        report[tf_name] = {
            "本数": int(len(df)), "現在ATRpips": round(atr_now, 1) if atr_now else None,
            "上位条件": [{"条件": label(m), "勝率": m["勝率"], "PF": m["PF"], "PF前": m["PF前"],
                       "PF後": m["PF後"], "取引数": m["取引数"], "平均pips": m["平均pips"],
                       "損切りpips目安": round(m["損切りATR"] * atr_now, 1) if atr_now else None,
                       "利確pips目安": round(m["利確ATR"] * atr_now, 1) if atr_now else None}
                      for m in top],
            "最良条件の時間帯幅別": bands_best,
            "同じ入り方の時間帯幅別(全変種合算)": bands_all,
            "狙い目の時間帯": good[:8], "避ける時間帯": bad[:8],
        }
        if top:
            b = top[0]
            all_best.append({"時間足": tf_name, "条件": label(b), "勝率": b["勝率"], "PF": b["PF"],
                             "スコア": b["スコア"], "取引数": b["取引数"], "平均pips": b["平均pips"]})
        print(f"[{tf_name}] {len(results)}条件 → "
              + (f"最良: {label(top[0])} 勝率{top[0]['勝率']:.1%} PF{top[0]['PF']} "
                 f"({top[0]['取引数']}回)" if top else "該当なし"))
    all_best.sort(key=lambda x: (x["スコア"], x["PF"]), reverse=True)
    concl = ""
    if all_best:
        b = all_best[0]
        concl = (f"時間足を横断して最も安定したのは【{b['時間足']}】の「{b['条件']}」"
                 f"（勝率{b['勝率']:.0%}・PF{b['PF']}・{b['取引数']}回）。"
                 "スキャル分析は、いま見ている時間足で『上位条件』に合うときだけ案を出し、"
                 "合わなければ見送り。見送りでも各時間足の内部予想は毎回出す。")
    summary = {
        "更新": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "期間": f"{base.index[0]:%Y-%m-%d}〜{base.index[-1]:%Y-%m-%d}",
        "前提": f"往復コスト{COST_PIPS}pips・損切り/利確はその時間足のATR倍率・同時到達は損切り扱い・"
                f"時間帯幅は{BAND_WIDTHS}時間の4通り",
        "時間足横断の最良": all_best,
        "時間足別": report,
        "結論": concl,
    }
    os.makedirs(RES, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,最良時間足,最良条件,勝率,PF,取引数\n")
        if all_best:
            b = all_best[0]
            f.write(f"{summary['更新'][:10]},{b['時間足']},{b['条件']},{b['勝率']},{b['PF']},{b['取引数']}\n")
    print(concl)
    print(f"保存: {OUT_JSON}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    run(quick=ap.parse_args().quick)
