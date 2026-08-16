# -*- coding: utf-8 -*-
"""運用成績の合算 — 4システムの決済履歴を1つの表にまとめる共通モジュール

2026-08-14 ユーザー指摘「運用成績（ボード表示）が、決済などされても
反映されていない」に対応して新設。

原因: ボードの累計実現損益・勝率・PF・決済取引回数は results/trades.csv
（日足2システム専用）だけを読んでいた。デイトレ複合時間軸は
results/trades_intraday.csv、デイトレ15分は results/trades_intraday15.csv へ
別々に決済を書くため、これらの決済が成績に一切入らなかった。
一方で「含み損益」はデイトレ2系統の建玉も評価していたので、
デイトレが決済されると含み益から消えるだけで実現損益にも現れず、
損益が蒸発したように見えていた（＝今回の症状）。

本モジュールは3ファイルを列名の違いごと吸収して1枚に正規化する。
以後、成績系の集計はすべてここを通す。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swap  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731

# (パス, 既定システム名, 建玉列, 決済列)
SOURCES = [
    (R("results", "trades.csv"), None, "建玉日", "決済日"),
    (R("results", "trades_intraday.csv"), "デイトレ複合時間軸",
     "建玉バー", "決済バー"),
    (R("results", "trades_intraday15.csv"), "デイトレ15分",
     "建玉バー", "決済バー"),
]

COLUMNS = ["システム", "建玉", "決済", "決済日", "売買", "建値", "決済値",
           "数量", "価格損益円", "スワップ円", "スワップ日数", "損益円",
           "決済理由"]

# ボードの並び順（日足2システム → デイトレ2システム）
ORDER = ["長期トレンド", "短期スイング", "デイトレ複合時間軸", "デイトレ15分"]


def _num(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(f) else f


def load_all():
    """3つの決済ファイルを正規化して結合し、決済日時の昇順で返す。

    列: システム/建玉/決済/決済日/売買/建値/決済値/数量/価格損益円/
        スワップ円/スワップ日数/損益円/決済理由
    ファイルが無い・空でも落ちない（空のDataFrameを返す）。
    """
    frames = []
    for path, sysname, c_in, c_out in SOURCES:
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if not len(df):
            continue
        out = pd.DataFrame()
        out["システム"] = (df["システム"] if "システム" in df.columns
                       else pd.Series([sysname] * len(df)))
        out["建玉"] = df[c_in].astype(str)
        out["決済"] = df[c_out].astype(str)
        for c in ("売買", "決済理由"):
            out[c] = df[c].astype(str) if c in df.columns else ""
        for c in ("建値", "決済値", "数量", "価格損益円", "スワップ円",
                  "スワップ日数", "損益円"):
            out[c] = (pd.to_numeric(df[c], errors="coerce")
                      if c in df.columns else pd.NA)
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    t = pd.concat(frames, ignore_index=True)
    # 損益円が欠けている行は 価格損益円(+スワップ円) で補う
    t["損益円"] = t["損益円"].fillna(
        t["価格損益円"].fillna(0) + t["スワップ円"].fillna(0))
    t["決済日"] = t["決済"].str.slice(0, 10)
    t = t.sort_values("決済", kind="stable").reset_index(drop=True)
    return t[COLUMNS]


def summary(trades=None, pending=None):
    """合算KPI（累計実現・勝率・PF・件数）と、システム別の内訳を返す。

    pending: {システム名: 暫定実現損益} — 朝の速報段階でのストップ到達分。
    """
    t = load_all() if trades is None else trades
    pending = pending or {}
    pnl = [float(v) for v in t["損益円"].tolist()] + list(pending.values())
    s = pd.Series(pnl, dtype="float64")
    loss = s[s <= 0].sum() if len(s) else 0.0
    by_sys = []
    for name in ORDER + [n for n in t["システム"].unique() if n not in ORDER]:
        rows = t[t["システム"] == name]
        add = pending.get(name)
        if not len(rows) and add is None:
            continue
        v = [float(x) for x in rows["損益円"].tolist()]
        if add is not None:
            v.append(add)
        sv = pd.Series(v, dtype="float64")
        by_sys.append({
            "システム": name,
            "件数": len(v),
            "損益円": float(sv.sum()),
            "勝率": float((sv > 0).mean()) if len(sv) else 0.0,
            "スワップ円": float(rows["スワップ円"].fillna(0).sum()),
        })
    return {
        "件数": len(pnl),
        "累計実現損益": float(s.sum()) if len(s) else 0.0,
        "勝率": float((s > 0).mean()) if len(s) else None,
        "PF": (float(s[s > 0].sum() / -loss) if loss < 0 else None),
        "スワップ円": float(t["スワップ円"].fillna(0).sum()) if len(t) else 0.0,
        "システム別": by_sys,
    }


def realized_by_date(trades=None, systems=None):
    """決済日 -> その日までの累計実現損益（資産カーブ補正用）。"""
    t = load_all() if trades is None else trades
    if systems is not None:
        t = t[t["システム"].isin(systems)]
    if not len(t):
        return {}
    g = t.groupby("決済日")["損益円"].sum().sort_index().cumsum()
    return {str(k): float(v) for k, v in g.items()}


def equity_curve(daily, init_equity, equity_now=None, trades=None, today=None):
    """資産推移カーブ（全4システム合算）を [(日付, 資産), ...] で返す。

    Webボードとxlsxダッシュボードの「唯一の資産カーブ生成元」。
    2026-08-14: 4システム合算への改修が Webボードのカーブと xlsx の
    ダッシュボードKPIには入ったが xlsx「パフォーマンス」シートには
    入らず、同じブック内で資産が2つ（957,505円 と 955,593円）並ぶ
    事故が起きた。以後どちらのボードも必ずこの関数を通す。

    各点 = 口座資金 + その日までの4システム累計実現損益(スワップ込)
           + その日の日足2システムの評価損益
    daily_log.csv の「モデル資産」列は使わない。同列は
      (a) 日足2システムのみ
      (b) 記録した当時の値で凍結（スワップ遡及補正が反映されない）
    の二重の理由で4システム合算KPIと一致しないため。

    equity_now を渡すと末尾を現在の厳密値にする。daily_log にまだ当日行が
    無い朝の速報段階では、前日の点を上書きせず当日の点として追加する
    （上書きすると前日の実績が消え、日付と値がずれる）。
    """
    t = load_all() if trades is None else trades
    cum = realized_by_date(t)
    keys = sorted(cum)

    def _realized_upto(d):
        past = [k for k in keys if k <= str(d)]
        return cum[past[-1]] if past else 0.0

    init = float(init_equity)
    curve = []
    if daily is not None and len(daily):
        eq_daily = daily.drop_duplicates("日付", keep="last")
        for d, mtm in zip(eq_daily["日付"],
                          pd.to_numeric(eq_daily["評価損益計"], errors="coerce")):
            curve.append((str(d), round(init + _realized_upto(d) + _num(mtm))))
    if equity_now is not None:
        today = today or __import__("datetime").date.today().isoformat()
        if curve and curve[-1][0] == today:
            curve[-1] = (today, round(float(equity_now)))
        else:
            curve.append((today, round(float(equity_now))))
    return curve


def check(daily=None, init_equity=1_000_000.0):
    """整合性の自己点検。問題を日本語1行ずつのリストで返す（空＝正常）。

    朝の配信ラッパとボード生成から呼ばれ、「決済したのに成績に乗らない」
    「同じ数字が2画面で違う」類の再発を無言で通さないための番人。
    """
    issues = []
    t = load_all()
    s = summary(t)
    # 1. 3ファイルの行数合計 = 合算後の行数（読み落としの検出）
    raw = 0
    for path, _sysname, _ci, _co in SOURCES:
        if os.path.exists(path):
            try:
                raw += len(pd.read_csv(path))
            except Exception:
                issues.append(f"決済履歴が読めない: {os.path.basename(path)}")
    if raw != len(t):
        issues.append(f"決済履歴の行数不一致: 生{raw}行 → 合算{len(t)}行")
    # 2. 損益円 = 価格損益円 + スワップ円（行ごと）
    for _, r in t.iterrows():
        want = _num(r["価格損益円"]) + _num(r["スワップ円"])
        if abs(_num(r["損益円"]) - want) > 1.5:
            issues.append(
                f"損益円が内訳と不一致: {r['システム']} {r['決済']} "
                f"{_num(r['損益円']):.0f} ≠ {want:.0f}")
    # 3. daily_log の実現損益累計が、その日の決済を取りこぼしていないか。
    #    累計の絶対値ではなく「前営業日からの増分」で見る。累計にはスワップ
    #    遡及補正(2026-08-14)より前に凍結された行の既知ズレ(約104円)が
    #    恒久的に残るため、絶対値で見ると毎日鳴り続けて番人にならない。
    if daily is not None and len(daily) >= 2:
        dd = daily.drop_duplicates("日付", keep="last")
        prev, last = dd.iloc[-2], dd.iloc[-1]
        dsys = t[t["システム"].isin(["長期トレンド", "短期スイング"])]
        win = dsys[(dsys["決済日"] > str(prev["日付"]))
                   & (dsys["決済日"] <= str(last["日付"]))]
        d_log = _num(last["実現損益累計"]) - _num(prev["実現損益累計"])
        d_tr = float(win["損益円"].sum())
        if abs(d_log - d_tr) > 1.5:
            # 窓内に決済が1件も無いのに増分が出た場合は「決済の取りこぼし」では
            # ありえず、確定済み決済のスワップ遡及補正（JFX公表値の更新）が
            # 累計に効いた回。原因が違うと打ち手も違うので文言を分ける。
            # 補正は1回きりなので、翌営業日の増分は0に戻り警告も消える。
            cause = ("（決済がログに乗っていない可能性）" if len(win)
                     else "（当日の決済は0件。確定済み決済のスワップ遡及補正が"
                          "累計に効いた回とみられる。翌営業日も続く場合のみ要調査）")
            issues.append(
                f"daily_log({last['日付']})の実現損益増分 {d_log:+,.0f}円 が "
                f"当日の決済 {d_tr:+,.0f}円 と不一致" + cause)
    return issues


# ---------------------------------------------------------------- 移行ツール
def backfill_swap(dry_run=True):
    """スワップ列が空のまま記録された過去の決済行を遡って埋める。

    swap.py 新設(2026-08-13)より前に決済した行は スワップ円/スワップ日数 が
    空欄で、損益円 も値動きのみだった。現在のレート（JFX公表値＋金利差補正）で
    遡及計算して埋める。過去のレートそのものではないため概算だが、
    空欄のまま「スワップ0円」として集計されるよりは実態に近い。
    """
    sw = swap.load()
    changed = []
    for path, sysname, c_in, c_out in SOURCES:
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path)
        if not len(df) or "スワップ円" not in df.columns:
            continue
        hit = False
        for i, r in df.iterrows():
            if not pd.isna(pd.to_numeric(r.get("スワップ円"), errors="coerce")):
                continue
            pos = 1 if str(r.get("売買", "")).strip() == "買" else -1
            units = _num(r.get("数量"))
            if not units:
                continue
            yen, days = swap.accrued(pos, units, str(r[c_in]),
                                     swap.parse_dt(str(r[c_out])), sw=sw)
            df.at[i, "スワップ円"] = round(yen)
            df.at[i, "スワップ日数"] = days
            df.at[i, "損益円"] = round(_num(r.get("価格損益円")) + yen)
            hit = True
            changed.append({
                "ファイル": os.path.basename(path),
                "システム": r.get("システム", sysname),
                "建玉": r[c_in], "決済": r[c_out],
                "スワップ円": round(yen), "日数": days,
                "損益円": round(_num(r.get("価格損益円")) + yen),
            })
        if hit and not dry_run:
            bak = path + ".bak"
            if not os.path.exists(bak):
                pd.read_csv(path).to_csv(bak, index=False)
            df.to_csv(path, index=False)
    return changed


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="運用成績の合算・点検")
    ap.add_argument("--backfill-swap", action="store_true",
                    help="スワップ列が空の過去行を遡って埋める（.bakを残す）")
    ap.add_argument("--dry-run", action="store_true", help="変更せず内容だけ表示")
    a = ap.parse_args()
    if a.backfill_swap:
        ch = backfill_swap(dry_run=a.dry_run)
        print(json.dumps(ch, ensure_ascii=False, indent=2))
        print(f"{'対象' if a.dry_run else '更新'} {len(ch)}行")
        return
    t = load_all()
    print(t.to_string(index=False))
    print()
    print(json.dumps(summary(t), ensure_ascii=False, indent=2, default=float))


if __name__ == "__main__":
    main()
