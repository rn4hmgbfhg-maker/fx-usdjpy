# -*- coding: utf-8 -*-
"""ファンダ・フィルタ検証（日次研究の一部）

「マクロ（ドル指数・米金利・VIX・株・原油）の向きに合わせてドンチャンの
新規エントリーを絞ると、現行2システムの成績は上がるか」を毎日検証する。
ファンダを“表示だけ”で終わらせず、研究（採否判定）に組み込むための工程。

方針:
  * 検証対象は現行パラメータ（config.json）のまま。マクロ条件だけを足す。
  * マクロ特徴量は価格に対して1日ラグ（ルックアヘッド防止・research_fundamental.py と同じ）。
  * 候補は少数の固定ファミリー（特徴量×窓）に限り、多重検定を避ける。
  * 採用判定は research.py と同じ頑健性ゲート（前後半PF・取引数・最大DD）に
    加え、ベースライン比1.10倍以上を要求（フィルタは取引を減らすため厳しめ）。
  * 「ずらしマクロ」対照実験（同じファミリーを時間をずらした偽マクロで
    走らせ、最良候補の改善率が偶然でも出る確率 p）を毎回行う。
    p≦0.10 かつ基準通過の時だけ「有望候補」とする（多重検定対策）。
  * 発注には一切使わない（ライブ判定への組み込みは要ユーザー承認）。
    結果は results/research_fund_filter.csv と results/fund_filter_latest.json。

使い方:
  python3 src/research_fund_filter.py            # 検証＋ログ＋JSON
  python3 src/research_fund_filter.py --null 100  # 対照実験のずらし回数（既定60）
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")
MACRO_PATH = os.path.join(BASE_DIR, "data", "macro_daily.csv")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "results", "research_fund_filter.csv")
OUT_JSON = os.path.join(BASE_DIR, "results", "fund_filter_latest.json")

SPLIT = "2018-12-31"
IMPROVE_RATIO = 1.10
MAX_DD_LIMIT = -0.12
GATES = {
    "長期トレンド": {"min_trades": 60, "min_pf_half": 1.3},
    "短期スイング": {"min_trades": 250, "min_pf_half": 1.25},
}
# (表示名, macro列, 種別, 窓)  種別: mom=窓日騰落 / lvl=水準の窓平均比 / chg=窓日の差
FAMILY = [
    ("ドル指数モメンタム10日", "ドル指数", "mom", 10),
    ("ドル指数モメンタム20日", "ドル指数", "mom", 20),
    ("ドル指数モメンタム60日", "ドル指数", "mom", 60),
    ("米10年金利変化10日", "米10年金利", "chg", 10),
    ("米10年金利変化20日", "米10年金利", "chg", 20),
    ("米10年金利変化60日", "米10年金利", "chg", 60),
    ("S&P500モメンタム20日", "S&P500", "mom", 20),
    ("S&P500モメンタム60日", "S&P500", "mom", 60),
    ("日経225モメンタム20日", "日経225", "mom", 20),
    ("VIX平常（リスクオフ時は買い見送り）", "VIX", "vix", 60),
    ("原油モメンタム20日", "原油", "mom", 20),
]


def load_macro(px_dates):
    m = pd.read_csv(MACRO_PATH)
    m["Date"] = pd.to_datetime(m["Date"])
    m = m.set_index("Date").sort_index()
    return m.reindex(pd.to_datetime(px_dates)).ffill()


def feature_bias(macro, col, kind, win):
    """+1=ドル高/円安方向を支持, -1=ドル安/円高方向を支持, 0=中立。1日ラグ済み。"""
    s = macro[col].astype(float)
    if kind == "mom":
        f = s / s.shift(win) - 1.0
    elif kind == "chg":
        f = s - s.shift(win)
    else:  # vix: 直近が窓平均の1.15倍超=リスクオフ → 買い側を不支持、それ以外は中立
        f = -((s > s.rolling(win).mean() * 1.15).astype(float))
    bias = np.sign(f).fillna(0.0)
    if kind == "vix":
        bias = bias.where(bias < 0, 0.0)   # 買い不支持(-1)のみ。売りは制限しない
        # 中立(0)は両方向許可、-1は買いのみ禁止 → 下のallowで扱う
    return bias.shift(1).fillna(0.0)       # 1日ラグ


def allow_masks(bias, kind):
    """(買い許可, 売り許可) の bool 配列。"""
    b = bias.to_numpy()
    if kind == "vix":
        return b >= 0, np.ones(len(b), dtype=bool)
    # 金利・ドル指数・株・原油: 方向が明確な時だけその向きを許可（中立=両方許可）
    if kind in ("mom", "chg"):
        return b >= 0, b <= 0
    return np.ones(len(b), dtype=bool), np.ones(len(b), dtype=bool)


def donchian_gated(df, n, exit_n, allow_long, allow_short):
    """strategies.donchian と同一ロジック。新規エントリー/ドテン時のみ許可を確認する。"""
    hi = df["High"].rolling(n).max().shift(1).to_numpy()
    lo = df["Low"].rolling(n).min().shift(1).to_numpy()
    xh = df["High"].rolling(exit_n).max().shift(1).to_numpy()
    xl = df["Low"].rolling(exit_n).min().shift(1).to_numpy()
    close = df["Close"].to_numpy()
    sig = np.zeros(len(df), dtype=int)
    pos = 0
    for i in range(len(df)):
        if np.isnan(hi[i]) or np.isnan(lo[i]):
            continue
        c = close[i]
        if pos == 0:
            if c > hi[i] and allow_long[i]:
                pos = 1
            elif c < lo[i] and allow_short[i]:
                pos = -1
        elif pos == 1 and c < xl[i]:
            pos = 0
            if c < lo[i] and allow_short[i]:
                pos = -1
        elif pos == -1 and c > xh[i]:
            pos = 0
            if c > hi[i] and allow_long[i]:
                pos = 1
        sig[i] = pos
    return pd.Series(sig, index=df.index)


def run_one(px, n_e, n_x, atr_k, risk, lev, al, ash):
    m = Backtester(px, risk_per_trade=risk, max_leverage=lev,
                   atr_stop_mult=atr_k).run(
        donchian_gated(px, n_e, n_x, al, ash))["metrics"]
    return m


def evaluate(px, mask_l, mask_s, cur, risk, lev):
    d = pd.to_datetime(px["Date"]).to_numpy()
    cut = pd.Timestamp(SPLIT).to_datetime64()
    ia = d <= cut
    out = {}
    for key, sl in (("全", slice(None)), ("前", ia), ("後", ~ia)):
        sub = px[sl].reset_index(drop=True)
        m = run_one(sub, *cur, risk, lev, mask_l[sl], mask_s[sl])
        out[f"PF{key}"] = m["プロフィットファクター"]
        if key == "全":
            out.update({"勝率": m["勝率"], "最大DD": m["最大ドローダウン"],
                        "取引数": m["取引回数"], "総リターン": m["総リターン"]})
    pa, pb = out["PF前"], out["PF後"]
    out["スコア"] = min(pa, pb) if pa == pa and pb == pb else 0.0
    return out


def run_family(px, macro, cur, risk, lev, gate):
    ones = np.ones(len(px), dtype=bool)
    base = evaluate(px, ones, ones, cur, risk, lev)
    rows = []
    for name, col, kind, win in FAMILY:
        bias = feature_bias(macro, col, kind, win)
        al, ash = allow_masks(bias, kind)
        r = evaluate(px, al, ash, cur, risk, lev)
        r["フィルタ"] = name
        r["改善率"] = (r["スコア"] / base["スコア"]) if base["スコア"] else np.nan
        r["合格"] = bool(r["取引数"] >= gate["min_trades"]
                       and r["PF前"] >= gate["min_pf_half"]
                       and r["PF後"] >= gate["min_pf_half"]
                       and r["最大DD"] >= MAX_DD_LIMIT
                       and r["改善率"] >= IMPROVE_RATIO)
        rows.append(r)
    return base, pd.DataFrame(rows)


def best_improve(df, gate):
    """取引数の下限を外し、前後半PF・DDだけを満たす候補のうち最大の改善率。"""
    ok = df[(df["PF前"] >= gate["min_pf_half"]) &
            (df["PF後"] >= gate["min_pf_half"]) &
            (df["最大DD"] >= MAX_DD_LIMIT)]
    return float(ok["改善率"].max()) if len(ok) else 0.0


def null_test(px, macro, cur, risk, lev, gate, observed_best, n_shift, rng):
    """マクロを時間方向にずらした偽データでファミリー全体を回し、
    ファミリー最大の改善率が観測値以上になる割合(p)を返す。
    多重検定（11候補から最良を選ぶ）の偶然分を織り込んだ確率。"""
    hits = 0
    for _ in range(n_shift):
        k = int(rng.integers(250, len(px) - 250))
        fake = macro.copy()
        fake.iloc[:, :] = np.roll(macro.to_numpy(), k, axis=0)
        _, df = run_family(px, fake, cur, risk, lev, gate)
        if best_improve(df, gate) >= observed_best:
            hits += 1
    return (hits + 1) / (n_shift + 1)


def today_state(macro, px):
    """今日の各フィルタの向き（参考表示）。"""
    st = {}
    for name, col, kind, win in FAMILY:
        bias = feature_bias(macro, col, kind, win)
        b = float(bias.iloc[-1])
        if kind == "vix":
            st[name] = "買い見送り（リスクオフ）" if b < 0 else "平常"
        else:
            st[name] = "円安(買い)支持" if b > 0 else ("円高(売り)支持" if b < 0 else "中立")
    return st


def log_rows(rows):
    # 同日の再実行で二重記録しない（同じ日付の行は置き換える）
    if os.path.exists(LOG_PATH):
        keep = [ln for ln in open(LOG_PATH, encoding="utf-8")
                if not ln.startswith(rows[0][0] + ",")]
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(keep)
    new = not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) == 0
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        if new:
            f.write("日付,システム,フィルタ,PF全,PF前半,PF後半,勝率,最大DD,取引数,"
                    "スコア,ベーススコア,改善率,合格,偶然確率p\n")
        for r in rows:
            f.write(",".join(map(str, r)) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--null", type=int, default=60)
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
    px = pd.read_csv(CSV_PATH)
    macro = load_macro(px["Date"])
    rng = np.random.default_rng(20260921)
    today = date.today().isoformat()

    summary = {"更新": datetime.now().isoformat(timespec="minutes"),
               "システム": {}, "今日の向き": today_state(macro, px)}
    log = []
    for name, scfg in cfg["システム"].items():
        gate = GATES.get(name)
        if gate is None or not scfg.get("有効", True):
            continue
        cur = (int(scfg["エントリー期間"]), int(scfg["イグジット期間"]),
               float(scfg["ATRストップ係数"]))
        risk, lev = float(scfg["リスク率"]), float(scfg["レバ上限"])
        base, df = run_family(px, macro, cur, risk, lev, gate)
        df = df.sort_values("改善率", ascending=False)
        print(f"\n=== {name} 現行 ドンチャン{cur[0]}/{cur[1]}・ATR×{cur[2]}: "
              f"ベーススコア{base['スコア']:.2f} PF全{base['PF全']:.2f} "
              f"取引{int(base['取引数'])}")
        show = df[["フィルタ", "スコア", "PF全", "PF前", "PF後", "勝率", "最大DD",
                   "取引数", "改善率", "合格"]].copy()
        print(show.head(5).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

        passed = df[df["合格"]]
        obs_best = best_improve(df, gate)
        p_null = null_test(px, macro, cur, risk, lev, gate, obs_best,
                           args.null, rng)
        near = df[(df["改善率"] >= IMPROVE_RATIO) & ~df["合格"] &
                  (df["PF前"] >= gate["min_pf_half"]) &
                  (df["PF後"] >= gate["min_pf_half"])]
        if len(passed) and p_null <= 0.10:
            best = passed.iloc[0]
            verdict = (f"有望候補: {best['フィルタ']}（改善率{best['改善率']:.2f}倍・"
                       f"ファミリー偶然確率p={p_null:.2f}）→ライブ適用は要承認")
        elif len(passed):
            best = passed.iloc[0]
            t0 = df.iloc[0]
            extra = ("" if t0["フィルタ"] == best["フィルタ"] else
                     f"。最良は{t0['フィルタ']}（改善率{t0['改善率']:.2f}倍・"
                     f"取引{int(t0['取引数'])}回）")
            verdict = (f"参考: {best['フィルタ']}が基準通過（改善率{best['改善率']:.2f}倍）"
                       f"だが偶然でも出る水準（p={p_null:.2f}）→採用せず{extra}")
        elif len(near):
            n0 = near.iloc[0]
            verdict = (f"惜しい: {n0['フィルタ']}（改善率{n0['改善率']:.2f}倍）は"
                       f"取引数{int(n0['取引数'])}が下限{gate['min_trades']}未満"
                       f"（p={p_null:.2f}）→採用せず")
        else:
            verdict = f"有望なし（p={p_null:.2f}）→現状維持"
        print(f"ファミリー最大改善率 {obs_best:.2f}倍 / 偶然確率 p={p_null:.2f}")
        print("判定:", verdict)

        top = df.iloc[0]
        summary["システム"][name] = {
            "現行": f"ドンチャン{cur[0]}/{cur[1]}・ATR×{cur[2]}",
            "ベーススコア": round(float(base["スコア"]), 2),
            "ベストフィルタ": str(top["フィルタ"]),
            "ベスト改善率": round(float(top["改善率"]), 2),
            "ベスト合格": bool(top["合格"]),
            "合格数": int(df["合格"].sum()),
            "ファミリー最大改善率": round(obs_best, 2),
            "偶然確率p": round(p_null, 2),
            "判定": verdict,
        }
        for _, r in df.iterrows():
            log.append([today, name, r["フィルタ"], f"{r['PF全']:.2f}",
                        f"{r['PF前']:.2f}", f"{r['PF後']:.2f}", f"{r['勝率']:.1%}",
                        f"{r['最大DD']:.1%}", int(r["取引数"]), f"{r['スコア']:.2f}",
                        f"{base['スコア']:.2f}", f"{r['改善率']:.2f}", r["合格"],
                        f"{p_null:.2f}"])

    print("\n今日のマクロの向き（1日ラグ済み）:")
    for k, v in summary["今日の向き"].items():
        print(f"  {k}: {v}")
    if not args.no_log:
        log_rows(log)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
