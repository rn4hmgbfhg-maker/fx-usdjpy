# -*- coding: utf-8 -*-
"""FXスキャル専用ボードのデータ生成（2026-10-15 追加）（全ペア横断・順位付け・今入るなら）

入力: 各ペアの15分足・1時間足（UTC index, Open/High/Low/Close）
出力: ranking.json（1ペア1行）・bars.json（直近24時間の15分足とEMA）・hours.json（時間帯別の値幅）

判定の考え方（FX運用ボードのスキャル研究に合わせる）
  ・損切り ATR(15分)×1.0／利確 ATR×1.5／最長16本（4時間）／往復コストはJFX水準
  ・想定勝率 = 直近60日の15分足で、4つの入り方（RSI行き過ぎの戻り・ボリンジャー外→内・
    直近4本ブレイク・EMA20押し目）を「1時間足EMA200と同方向のみ」で検証し、PFが最良の入り方の勝率
  ・今入るなら（参考）の向き = 1時間足のトレンド（EMA200の傾きと位置）＋15分足のEMA20/50の並び＋
    直近1時間の勢い の合成。合成が弱いときも向きは出すが「様子見寄り」と明記
  ・狙い目スコア(0-100) = 方向のそろい30 + 検証の優位性25 + 値幅の適度さ20 + コストの軽さ15 + 時間帯の活発さ10
"""
import json
import sys
from datetime import timezone, timedelta

import numpy as np
import pandas as pd

JST = timezone(timedelta(hours=9))

# (コード, 表示名, Yahoo, pipサイズ, 往復コスト[pips], コスト根拠)  ※pair_scan.py のJFX水準（片道＝スプ＋滑り）×2
PAIRS = [
    ("USDJPY", "米ドル/円", "USDJPY=X", 0.01, 0.8, "スプ0.2銭+滑り（公式確認済）"),
    ("EURJPY", "ユーロ/円", "EURJPY=X", 0.01, 1.2, "スプ0.4銭+滑り（公式確認済）"),
    ("GBPJPY", "ポンド/円", "GBPJPY=X", 0.01, 2.2, "スプ0.9銭+滑り（公式確認済）"),
    ("AUDJPY", "豪ドル/円", "AUDJPY=X", 0.01, 1.6, "スプ0.6銭想定+滑り"),
    ("NZDJPY", "NZドル/円", "NZDJPY=X", 0.01, 2.4, "スプ1.0銭想定+滑り"),
    ("CADJPY", "加ドル/円", "CADJPY=X", 0.01, 3.6, "スプ1.6銭想定+滑り"),
    ("CHFJPY", "フラン/円", "CHFJPY=X", 0.01, 4.0, "スプ1.8銭想定+滑り"),
    ("EURUSD", "ユーロ/ドル", "EURUSD=X", 0.0001, 1.2, "スプ0.4p想定+滑り"),
    ("GBPUSD", "ポンド/ドル", "GBPUSD=X", 0.0001, 2.4, "スプ1.0p想定+滑り"),
    ("AUDUSD", "豪ドル/ドル", "AUDUSD=X", 0.0001, 1.6, "スプ0.6p想定+滑り"),
    ("EURGBP", "ユーロ/ポンド", "EURGBP=X", 0.0001, 2.0, "スプ1.0p想定+滑り"),
]
SL_K, TP_K, HOLD = 1.0, 1.5, 16
RR_MIN = 1.2   # 2026-10-15: コスト込みのRRがこれを下回らないよう利確を広げる（ATRが小さくコスト比が高いペアで利益＜損失になる不具合の対策）


def tp_pips_for(atr, cost):
    """利確幅(pips)。基本はATR×1.5、ただし(利確−コスト)÷(損切り＋コスト)がRR_MIN以上になるまで広げる"""
    sl = SL_K * atr
    return max(TP_K * atr, RR_MIN * (sl + cost) + cost)
METHODS = {"rsi_rev": "RSI行き過ぎの戻り", "bb_rev": "ボリンジャー外→内の戻り",
           "range_brk": "直近4本ブレイク", "ema_pull": "EMA20押し目・戻り"}


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def indicators(df, pip):
    df = df.copy()
    c = df["Close"]
    df["rsi"] = rsi(c)
    for n in (20, 50, 200):
        df[f"ema{n}"] = c.ewm(span=n, adjust=False).mean()
    ma, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_up"], df["bb_lo"] = ma + 2 * sd, ma - 2 * sd
    df["hh4"] = df["High"].rolling(4).max().shift(1)
    df["ll4"] = df["Low"].rolling(4).min().shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                    (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean() / pip
    df["range"] = (df["High"] - df["Low"]) / pip
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


def simulate(df, sig, trend, pip, cost):
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    atr, tr = df["atr"].values, trend.values
    out, last_exit, n = [], -1, len(df)
    for i in np.where(sig.values != 0)[0]:
        if i + 1 >= n or i <= last_exit or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        d = int(sig.values[i])
        if tr[i] != d:
            continue
        sl, tp = SL_K * atr[i], tp_pips_for(atr[i], cost)
        e = o[i + 1]
        stop, take = e - d * sl * pip, e + d * tp * pip
        pnl, jx = None, None
        for j in range(i + 1, min(i + 1 + HOLD, n)):
            if (l[j] <= stop) if d > 0 else (h[j] >= stop):
                pnl, jx = -sl, j
                break
            if (h[j] >= take) if d > 0 else (l[j] <= take):
                pnl, jx = tp, j
                break
        if pnl is None:
            jx = min(i + HOLD, n - 1)
            pnl = (c[jx] - e) * d / pip
        out.append(pnl - cost)
        last_exit = jx
    return np.array(out)


def analyze(code, name, pip, cost, cost_note, m15, h1, now_utc):
    m15 = indicators(m15, pip)
    h1 = indicators(h1, pip)
    # 1時間足のトレンド（EMA200の4本の傾き）を15分足へ前方補完（未来を見ないよう確定済みの1時間足のみ）
    h1_tr = np.sign(h1["ema200"].diff(4)).fillna(0)
    h1_tr.index = h1_tr.index + pd.Timedelta(hours=1)
    trend15 = h1_tr.reindex(m15.index, method="ffill").fillna(0)

    last, h_last = m15.iloc[-1], h1.iloc[-1]
    px, atr = float(last["Close"]), float(last["atr"])

    # 直近60日の検証
    since = m15.index[-1] - pd.Timedelta(days=60)
    win = m15[m15.index >= since]
    trw = trend15[trend15.index >= since]
    best = None
    tests = []
    for k, label in METHODS.items():
        r = simulate(win, signals(win, k), trw, pip, cost)
        if len(r) == 0:
            continue
        gp, gl = r[r > 0].sum(), -r[r < 0].sum()
        pf = float(gp / gl) if gl > 0 else None
        t = {"method": label, "n": int(len(r)), "win": float((r > 0).mean()),
             "pf": pf, "avg": float(r.mean())}
        tests.append(t)
        if len(r) >= 15 and (best is None or (pf or 0) > (best["pf"] or 0)):
            best = t

    # 方向の合成
    slope = float(np.sign(h1["ema200"].diff(4).iloc[-1]))
    above = 1.0 if float(h_last["Close"]) > float(h_last["ema200"]) else -1.0
    t1h = 0.6 * slope + 0.4 * above
    ema15 = 1.0 if last["ema20"] > last["ema50"] else -1.0
    mom = float((m15["Close"].iloc[-1] - m15["Close"].iloc[-5]) / pip / atr) if atr > 0 else 0.0
    momc = float(np.clip(mom, -1, 1))
    comp = 0.5 * t1h + 0.3 * ema15 + 0.2 * momc
    d = 1 if comp >= 0 else -1
    align = abs(comp)

    # 点灯中のシグナル（確定足）
    lamps = []
    for k, label in METHODS.items():
        s15 = int(signals(m15, k).iloc[-1])
        s1h = int(signals(h1, k).iloc[-1])
        lamps.append({"method": label, "m15": s15, "h1": s1h})
    fired_with = sum(1 for x in lamps if x["m15"] == d or x["h1"] == d)
    fired_against = sum(1 for x in lamps if x["m15"] == -d or x["h1"] == -d)

    # 時間帯（JST）別の平均値幅（直近60日・15分足1本あたり）
    rng = win["range"]
    hours_jst = (win.index.tz_convert("Asia/Tokyo").hour)
    by_h = rng.groupby(hours_jst).mean()
    overall = float(rng.mean())
    now_h = int(pd.Timestamp(now_utc).tz_convert("Asia/Tokyo").hour)
    act = float(by_h.get(now_h, overall) / overall) if overall > 0 else 1.0

    # スコア
    s_align = 30 * align
    pf = (best or {}).get("pf") or 0.0
    s_edge = 25 * float(np.clip((pf - 0.8) / 0.7, 0, 1))
    cost_ratio = cost / atr if atr > 0 else 1.0
    s_vol = 20 * float(np.clip(1 - abs(np.log(max(atr, 0.1) / 10.0)) / 1.2, 0, 1))
    s_cost = 15 * float(np.clip(1 - cost_ratio / 0.4, 0, 1))
    s_act = 10 * float(np.clip((act - 0.5) / 1.0, 0, 1))
    score = s_align + s_edge + s_vol + s_cost + s_act
    if fired_with:
        score += 3
    if fired_against:
        score -= 3
    score = float(np.clip(score, 0, 100))

    sl_p, tp_p = SL_K * atr, tp_pips_for(atr, cost)
    entry = px
    stop = entry - d * sl_p * pip
    take = entry + d * tp_p * pip
    rr_net = (tp_p - cost) / (sl_p + cost)
    winp = (best or {}).get("win")
    ev = (winp * (tp_p - cost) - (1 - winp) * (sl_p + cost)) if winp is not None else None
    stance = "入る候補" if align >= 0.6 and (winp or 0) > 0.5 and (ev or 0) > 0 else ("条件待ち" if align >= 0.35 else "様子見寄り")
    age_min = int((pd.Timestamp(now_utc) - (m15.index[-1] + pd.Timedelta(minutes=15))).total_seconds() // 60)

    row = {
        "pair": name, "code": code, "price": round(px, 5 if pip < 0.01 else 3),
        "bar_jst": (m15.index[-1] + pd.Timedelta(minutes=15)).tz_convert("Asia/Tokyo").strftime("%m/%d %H:%M"),
        "age_min": age_min,
        "dir": "ロング" if d > 0 else "ショート", "dir_sign": d,
        "stance": stance, "score": round(score, 1),
        "align": round(align, 2), "trend_1h": "上" if t1h > 0 else "下",
        "ema_15m": "上向き並び" if ema15 > 0 else "下向き並び",
        "momentum_atr": round(mom, 2), "rsi_15m": round(float(last["rsi"]), 1),
        "atr_pips": round(atr, 1), "cost_pips": cost, "cost_ratio": round(cost_ratio, 3), "cost_note": cost_note,
        "session_act": round(act, 2),
        "entry": round(entry, 5 if pip < 0.01 else 3),
        "stop": round(stop, 5 if pip < 0.01 else 3), "take": round(take, 5 if pip < 0.01 else 3),
        "sl_pips": round(sl_p, 1), "tp_pips": round(tp_p, 1), "rr": round(rr_net, 2),
        "tp_net_pips": round(tp_p - cost, 1), "sl_net_pips": round(sl_p + cost, 1),
        "tp_widened": bool(tp_p > TP_K * atr + 1e-9),
        "win": round(winp, 3) if winp is not None else None,
        "pf": round(pf, 2) if best else None, "n": (best or {}).get("n"),
        "best_method": (best or {}).get("method"),
        "ev_pips": round(ev, 1) if ev is not None else None,
        "lamps_with": fired_with, "lamps_against": fired_against,
        "lamps": "／".join(f'{x["method"]}:{"買" if x["m15"]>0 else "売" if x["m15"]<0 else "－"}(15分)・{"買" if x["h1"]>0 else "売" if x["h1"]<0 else "－"}(1時間)' for x in lamps),
        "pip": pip,
    }
    bars = []
    tail = m15.iloc[-96:]
    for t, r in tail.iterrows():
        bars.append({"pair": name, "t": (t + pd.Timedelta(minutes=15)).tz_convert("Asia/Tokyo").strftime("%Y-%m-%dT%H:%M"),
                     "open": float(r["Open"]), "high": float(r["High"]), "low": float(r["Low"]),
                     "close": float(r["Close"]), "ema20": float(r["ema20"]), "ema50": float(r["ema50"])})
    hours = [{"pair": name, "hour": int(hh), "range_pips": round(float(v), 2), "rel": round(float(v / overall), 2)}
             for hh, v in by_h.items()]
    return row, bars, hours, tests


def _yahoo(sym, interval, rng):
    import requests
    hdr = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    last = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}",
                             params={"range": rng, "interval": interval}, headers=hdr, timeout=30)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]},
                              index=pd.to_datetime(res["timestamp"], unit="s", utc=True)).dropna()
            mins = 15 if interval == "15m" else 60
            now = pd.Timestamp.now(tz="UTC")
            df = df[df.index + pd.Timedelta(minutes=mins) <= now]        # 未確定の足は捨てる
            if mins == 15:
                df = df[df.index.minute % 15 == 0]
            return df[~df.index.duplicated(keep="last")].sort_index()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{sym} {interval} 取得失敗: {last}")


def run(out_dir=None):
    """主要11ペアを取得して results/scalp_board/ に ranking.json・bars.json・hours.json・meta.json を書く。
    signals.yml（15分ごと）の data_fetch_eurusd.py に相乗りして動く（ワークフローは編集しない）。
    FXスキャル専用ボード（claude.ai アーティファクト）への反映は claude.ai の定期タスクが行う。"""
    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = out_dir or os.path.join(base, "results", "scalp_board")
    os.makedirs(out_dir, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    rows, bars, hours, errs = [], [], [], []
    for code, name, sym, pip, cost, note in PAIRS:
        try:
            m15 = _yahoo(sym, "15m", "60d")
            h1 = _yahoo(sym, "60m", "90d")
            r, b, h, _ = analyze(code, name, pip, cost, note, m15, h1, now)
            rows.append(r); bars += b; hours += h
        except Exception as e:  # noqa: BLE001
            errs.append(f"{name}: {e}")
    if not rows:
        print("スキャルボード: 全ペア取得失敗 " + "; ".join(errs))
        return
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    meta = {"at": now.tz_convert("Asia/Tokyo").isoformat(timespec="seconds"),
            "asof": rows[0]["bar_jst"], "pairs": len(rows), "errors": errs,
            "source": "Yahoo Finance（GitHub Actions・15分ごと）"}
    for fn, obj in (("ranking.json", rows), ("bars.json", bars), ("hours.json", hours), ("meta.json", meta)):
        json.dump(obj, open(os.path.join(out_dir, fn), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"スキャルボード {len(rows)}/{len(PAIRS)}ペア" + (f"（失敗 {'; '.join(errs)}）" if errs else ""))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
