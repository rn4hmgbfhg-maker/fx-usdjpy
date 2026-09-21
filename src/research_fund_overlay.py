# -*- coding: utf-8 -*-
"""ファンダ研究を運用（発注判定）へ反映してよいかの検証（2026-09-21 新設）

research_fundamental.py が毎日出している3つの定量成果
  A) 翌日の方向予測（上昇確率）
  B) 翌日の変動幅（ボラ）予測
  C) マクロ連関レジーム（60日相関の主導ドライバ）
を、現行4システム（長期トレンド／短期スイング／デイトレ2H／デイトレ15分＝
1時間足ドンチャン）へ「フィルタ」「数量調整」として重ねた場合に、学習外期間の
成績が良くなるかを調べる。

判定は事前に決めたゲートで機械的に行う（後付けの都合のよい解釈を防ぐ）:
  ・PFが前半・後半の両方で現行比 +5% 以上
  ・総リターンが現行以上
  ・最大DDの悪化が1ポイント以内
  ・取引数の減少が40%以内（絞りすぎて偶然良く見えるだけ、を避ける）
全て満たした重ね方だけを「採用候補」とする。config や エンジンは書き換えない
（本スクリプトは検証と報告のみ）。

先読み防止:
  予測の基準日D（NYクローズD＝JST D+1 朝6時に確定）は、日足CSVでは
  「Dより後の最初の行」、時間足では「JST D+1 06:00 以降に終わるバー」から
  しか使わない（実運用の朝の判定と同じ時点）。

使い方:  python3 src/research_fund_overlay.py
出力:    results/fund_overlay_latest.json ／ results/fund_overlay_log.csv
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester  # noqa: E402
from data_fetch_intraday import load_merged, resample  # noqa: E402
from strategies_intraday import align_trend, donchian_mtf  # noqa: E402
import research_fundamental as rf  # noqa: E402
import tech_filters  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
OUT_JSON = os.path.join(BASE_DIR, "results", "fund_overlay_latest.json")
LOG_CSV = os.path.join(BASE_DIR, "results", "fund_overlay_log.csv")

MARGINS = (0.0, 0.03, 0.06)        # 方向フィルタの確信幅（0.5±m）
GATE_PF = 1.05
GATE_DD = 0.01
GATE_TRADES = 0.60


# ------------------------------------------------------------ 学習外の予測系列
def oos_series():
    """学習外の 上昇確率・予測変動幅・レジーム を日次系列で返す。"""
    macro = rf.update_macro()
    px = rf.load_usdjpy()
    feat = rf.build_features(px, macro)
    ret_next = px.pct_change().shift(-1)
    target = (ret_next > 0).astype(float)
    target[ret_next.isna()] = np.nan
    wf, _ = rf.walk_forward(feat, target)
    p_up = wf["上昇確率"]

    # 変動幅（research_fundamental.walk_forward_range と同じ手順で系列を得る）
    rng = ret_next.abs()
    data = feat.join(rng.rename("r")).dropna()
    X = data.drop(columns="r").to_numpy(dtype="float64")
    y = np.log(np.clip(data["r"].to_numpy(dtype="float64"), 1e-6, None))
    rows = []
    w = mu = sd = None
    for i in range(rf.MIN_TRAIN, len(data)):
        if (i - rf.MIN_TRAIN) % rf.REFIT_EVERY == 0:
            Xtr, ytr = X[:i], y[:i]
            mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
            sd = np.where(sd < 1e-12, 1.0, sd)
            Z = np.hstack([np.ones((len(Xtr), 1)), (Xtr - mu) / sd])
            reg = np.eye(Z.shape[1]) * rf.RIDGE
            reg[0, 0] = 0.0
            w = np.linalg.solve(Z.T @ Z + reg, Z.T @ ytr)
            base = float(ytr[-250:].mean())
        z = np.hstack([[1.0], (X[i] - mu) / sd])
        rows.append((data.index[i], float(z @ w) - base))
    vol_rel = pd.Series(dict(rows))          # 対数: 予測 − 直近平均（>0=荒れる予測）

    # レジーム（60日相関の首位ドライバ）を日次で。マクロは1日ラグで先読み防止
    ret = px.pct_change()
    m = macro.reindex(px.index).ffill().shift(1)
    cors = {}
    for name in macro.columns:
        chg = m[name].diff() if name in rf.YIELD_KEYS else m[name].pct_change()
        cors[name] = ret.rolling(rf.CORR_SHORT).corr(chg)
    cdf = pd.DataFrame(cors)
    top = cdf.abs().idxmax(axis=1, skipna=True)
    topv = cdf.abs().max(axis=1)

    def label(d):
        if pd.isna(topv.get(d)) or pd.isna(top.get(d)):
            return None
        if topv[d] < 0.20:
            return "無相関"
        k = top[d]
        return ("金利連動" if k in rf.YIELD_KEYS else
                "リスク回避連動" if k == "VIX" else
                "ドル全面主導" if k == "ドル指数" else "その他主導")
    regime = pd.Series({d: label(d) for d in px.index}).dropna()
    return p_up, vol_rel, regime


def asof_daily(series, dates):
    """日足CSVの各行日付Rに対し「R より前の最新基準日」の値を返す。"""
    idx = pd.Index([pd.Timestamp(d).date() for d in series.index])
    vals = series.to_numpy()
    out = []
    for r in dates:
        k = idx.searchsorted(pd.Timestamp(r).date(), side="left") - 1
        out.append(vals[k] if k >= 0 else None)
    return out


def asof_intraday(series, bar_end_jst):
    """時間足バー（終了時刻JST）に対し、その時点で確定済みの最新予測を返す。
    基準日D は JST D+1 06:00 に確定する。"""
    idx = pd.Index([pd.Timestamp(d) + pd.Timedelta(days=1, hours=6)
                    for d in series.index])
    vals = series.to_numpy()
    out = []
    for t in pd.to_datetime(bar_end_jst):
        k = idx.searchsorted(t, side="right") - 1
        out.append(vals[k] if k >= 0 else None)
    return out


# ------------------------------------------------------------ 評価
def run_bt(df, sig, risk, atr_k, lev, tp_k=None):
    bt = Backtester(df, risk_per_trade=risk, atr_stop_mult=atr_k,
                    max_leverage=lev, tp_atr_mult=tp_k)
    return bt.run(sig)


def pf_of(pnl):
    g, l = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    return float(g / l) if l > 0 else float("nan")


def summarize(res, split_date, mult=None):
    """前半/後半PF・総リターン・DD・取引数。mult=取引ごとの数量倍率（近似）。"""
    tr = res["trades"].copy()
    if not len(tr):
        return None
    if mult is not None:
        tr["pnl"] = tr["pnl"] * mult
    d = pd.to_datetime(tr["entry_date"])
    a, b = tr[d <= split_date], tr[d > split_date]
    m = res["metrics"]
    total = (float(tr["pnl"].sum()) / 1_000_000 if mult is not None
             else float(m["総リターン"]))
    if mult is not None:
        eq = 1_000_000 + tr["pnl"].cumsum()
        dd = float((eq / eq.cummax() - 1).min())
    else:
        dd = float(m["最大ドローダウン"])
    return {"PF全": pf_of(tr["pnl"]), "PF前": pf_of(a["pnl"]),
            "PF後": pf_of(b["pnl"]), "総リターン": total, "最大DD": dd,
            "取引数": int(len(tr)), "勝率": float((tr["pnl"] > 0).mean())}


def gate(base, new):
    if not base or not new:
        return False
    ok_pf = (new["PF前"] >= base["PF前"] * GATE_PF
             and new["PF後"] >= base["PF後"] * GATE_PF)
    ok_ret = new["総リターン"] >= base["総リターン"]
    ok_dd = new["最大DD"] >= base["最大DD"] - GATE_DD
    ok_n = new["取引数"] >= base["取引数"] * GATE_TRADES
    return bool(ok_pf and ok_ret and ok_dd and ok_n)


def masks_from_p(p_list, margin, base_mask=None):
    n = len(p_list)
    al = np.ones(n, dtype=bool)
    as_ = np.ones(n, dtype=bool)
    for i, p in enumerate(p_list):
        if p is None or p != p:
            continue
        if p < 0.5 - margin:
            al[i] = False           # 下落予測が強い日は新規買いを見送る
        if p > 0.5 + margin:
            as_[i] = False          # 上昇予測が強い日は新規売りを見送る
    if base_mask is not None:
        al &= np.asarray(base_mask[0], dtype=bool)
        as_ &= np.asarray(base_mask[1], dtype=bool)
    return al, as_


def test_system(name, df, n_e, n_x, risk, atr_k, lev, tp_k, p_list, v_list,
                r_list, trend=None, base_mask=None):
    rows = []
    split = pd.to_datetime(df["Date"]).iloc[len(df) // 2]
    base_res = run_bt(df, donchian_mtf(df, n_e, n_x, trend, base_mask),
                      risk, atr_k, lev, tp_k)
    base = summarize(base_res, split)
    rows.append({"システム": name, "重ね方": "現行（ファンダ不使用）", **base,
                 "採用候補": ""})

    # A) 方向予測フィルタ
    for m in MARGINS:
        mk = masks_from_p(p_list, m, base_mask)
        res = run_bt(df, donchian_mtf(df, n_e, n_x, trend, mk),
                     risk, atr_k, lev, tp_k)
        s = summarize(res, split)
        if s:
            rows.append({"システム": name,
                         "重ね方": f"A 方向予測フィルタ（0.5±{m:.2f}）", **s,
                         "採用候補": "◎" if gate(base, s) else "×"})

    # B) ボラ予測で数量調整（荒れる予測の日は数量を落とす／凪は増やす）
    tr = base_res["trades"]
    if len(tr):
        date_pos = {d: i for i, d in enumerate(df["Date"])}
        vv = []
        for d in tr["entry_date"]:
            i = date_pos.get(d, 0)
            v = v_list[i - 1] if i > 0 else None     # 判定バー時点の予測
            vv.append(0.0 if v is None or v != v else float(v))
        vv = np.array(vv)
        for lab, mult in (
                ("B ボラ予測で数量調整（荒れ予測×0.7／凪×1.3）",
                 np.where(vv > 0.15, 0.7, np.where(vv < -0.15, 1.3, 1.0))),
                ("B' 荒れ予測のときだけ数量×0.5",
                 np.where(vv > 0.15, 0.5, 1.0))):
            s = summarize(base_res, split, mult=mult)
            rows.append({"システム": name, "重ね方": lab, **s,
                         "採用候補": "◎" if gate(base, s) else "×"})

        # C) レジーム別の損益（まず実態を見る）→ 最悪レジームだけ見送る案
        rg = []
        for d in tr["entry_date"]:
            i = date_pos.get(d, 0)
            rg.append(r_list[i - 1] if i > 0 and r_list[i - 1] else "不明")
        tr2 = tr.assign(レジーム=rg)
        by = tr2.groupby("レジーム")["pnl"].agg(["sum", "count"])
        for k, r in by.iterrows():
            rows.append({"システム": name, "重ね方": f"C 参考: {k}の時の損益",
                         "PF全": pf_of(tr2[tr2["レジーム"] == k]["pnl"]),
                         "総リターン": float(r["sum"]) / 1_000_000,
                         "取引数": int(r["count"]), "採用候補": "参考"})
        worst = by["sum"].idxmin()
        if by.loc[worst, "sum"] < 0:
            mult = np.where(tr2["レジーム"].to_numpy() == worst, 0.0, 1.0)
            s = summarize(base_res, split, mult=mult)
            s["取引数"] = int((mult > 0).sum())
            rows.append({"システム": name,
                         "重ね方": f"C レジーム『{worst}』の時は新規見送り"
                                   "（※後知恵で選んだ案＝参考扱い）", **s,
                         "採用候補": "△" if gate(base, s) else "×"})
    return rows


def main():
    print("=== ファンダ→運用 反映検証 ===")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    p_up, vol_rel, regime = oos_series()
    start = pd.Timestamp(p_up.index[0])
    print(f"学習外の予測 {len(p_up)}日  {p_up.index[0]} -> {p_up.index[-1]}")
    rows = []

    # --- 日足2システム（学習外期間のみで比較）
    dfd = pd.read_csv(os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")).dropna()
    dfd = dfd[pd.to_datetime(dfd["Date"]) >= start - pd.Timedelta(days=120)
              ].reset_index(drop=True)
    pl, vl, rl = (asof_daily(s, dfd["Date"]) for s in (p_up, vol_rel, regime))
    for name in ("長期トレンド", "短期スイング"):
        c = cfg["システム"][name]
        rows += test_system(name, dfd, c["エントリー期間"], c["イグジット期間"],
                            c["リスク率"], c["ATRストップ係数"], c["レバ上限"],
                            None, pl, vl, rl)

    # --- デイトレ2系統
    df1h = load_merged(fetch=False)
    c = cfg["デイトレ"]
    tf = int(c["時間足"])
    dfe = resample(df1h, tf)
    df4 = resample(df1h, 4)
    trend = align_trend(dfe, df4, int(c["4Hフィルタ期間"]))
    bm = tech_filters.entry_mask(dfe, c.get("確認フィルタ")) \
        if c.get("確認フィルタ") else None
    pl, vl, rl = (asof_intraday(s, dfe["Date"]) for s in (p_up, vol_rel, regime))
    rows += test_system("デイトレ複合時間軸", dfe, c["エントリー期間"],
                        c["イグジット期間"], c["リスク率"], c["ATRストップ係数"],
                        c["レバ上限"], c.get("利確ATR係数") or None,
                        pl, vl, rl, trend=trend, base_mask=bm)

    c = cfg["デイトレ15分"]
    p = c["パラメータ"]
    if p.get("戦略ID") == "donchian" and int(p.get("時間足分", 60)) == 60 \
            and not p.get("フィルタ時間足"):
        df1 = resample(df1h, 1)
        pl, vl, rl = (asof_intraday(s, df1["Date"])
                      for s in (p_up, vol_rel, regime))
        rows += test_system("デイトレ15分", df1, p["エントリー期間"],
                            p["イグジット期間"], c["リスク率"],
                            c["ATRストップ係数"], c["レバ上限"],
                            c.get("利確ATR係数") or None, pl, vl, rl)
    else:
        print("※デイトレ15分は現行戦略が1時間足ドンチャン以外のため対象外")

    out = pd.DataFrame(rows)
    disp = out.copy()
    for col in ("PF全", "PF前", "PF後"):
        disp[col] = disp[col].map(lambda v: "" if v != v else f"{v:.2f}")
    for col in ("総リターン", "最大DD", "勝率"):
        disp[col] = disp[col].map(lambda v: "" if v != v else f"{v:+.1%}")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    print(disp.to_string(index=False))

    adopt = out[out["採用候補"] == "◎"]
    print("\n採用候補（ゲート通過）:",
          "なし" if not len(adopt) else "")
    for _, r in adopt.iterrows():
        print(f"  ・{r['システム']}: {r['重ね方']}")

    stamp = datetime.now().isoformat(timespec="minutes")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"更新": stamp,
                   "学習外期間": [str(p_up.index[0]), str(p_up.index[-1])],
                   "ゲート": {"PF前後とも": GATE_PF, "DD悪化許容": GATE_DD,
                              "取引数下限比": GATE_TRADES},
                   "採用候補": adopt[["システム", "重ね方"]].to_dict("records"),
                   "結果": json.loads(out.to_json(orient="records",
                                                  force_ascii=False))},
                  f, ensure_ascii=False, indent=1)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", encoding="utf-8") as f:
        if new:
            f.write("日時,採用候補数,採用候補\n")
        f.write(f"{stamp},{len(adopt)},"
                + "／".join(f"{r['システム']}:{r['重ね方']}"
                            for _, r in adopt.iterrows()) + "\n")
    print(f"\n保存: {OUT_JSON}")


if __name__ == "__main__":
    main()
