# -*- coding: utf-8 -*-
"""ファンダメンタルズ研究＋翌日予測研究（日次自動）

テクニカル（ドンチャン）だけでは拾えない「なぜ動いているか」と
「明日どちらへ動きやすいか」を毎日研究し、日々の進化の土台にする。

3本立て:
  ① マクロ連関  : 米金利・ドル指数・VIX・株・商品とドル円の連動を
                  60日/250日の相関で測り、現在の主導ドライバ＝レジームを判定
  ② 翌日予測    : 上記マクロ＋価格モメンタムから翌営業日の方向確率と
                  想定変動レンジを推定。ウォークフォワード（拡大窓・20日ごと
                  再学習）で「学習に使っていない期間」の的中率のみを報告する
  ③ 前向き記録  : 毎日の予測を forecast_log.csv に残し、翌日以降に実測で
                  採点する。バックテストの的中率と実運用の的中率を並べて
                  過学習を検知できるようにする（＝日々の進化の証拠）

定性ニュース（要人発言・政策・地政学）はPythonから取れないため、
毎日15時台のスケジュールタスクでClaudeがWeb検索して
results/fundamental_news.json に書き込み、本スクリプトが読み込んで統合する。

先読み（ルックアヘッド）防止:
  米国系の指標はJST基準では「翌日早朝」に確定するため、マクロ特徴量は
  価格に対してさらに1日ラグを掛けて使う。予測対象は「最後に確定した
  日足終値」→「次の確定日足終値」の方向。

使い方:
  python3 src/research_fundamental.py            # 採点→研究→当日の予測を記録
  python3 src/research_fundamental.py --no-log   # 記録せず研究結果だけ表示
"""
import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
RES_DIR = os.path.join(BASE_DIR, "results")
USDJPY_CSV = os.path.join(DATA_DIR, "usdjpy_daily.csv")
MACRO_CSV = os.path.join(DATA_DIR, "macro_daily.csv")
FORECAST_LOG = os.path.join(RES_DIR, "forecast_log.csv")
NEWS_JSON = os.path.join(RES_DIR, "fundamental_news.json")
OUT_JSON = os.path.join(RES_DIR, "fundamental_latest.json")
JST = ZoneInfo("Asia/Tokyo")

# Yahoo Finance のシンボル。取得に失敗したものは自動で除外する
MACRO_SYMBOLS = {
    "米10年金利": "^TNX",
    "米5年金利": "^FVX",
    "米2年金利": "^IRX",
    "ドル指数": "DX-Y.NYB",
    "VIX": "^VIX",
    "S&P500": "^GSPC",
    "日経225": "^N225",
    "金": "GC=F",
    "原油": "CL=F",
}
# 金利は「差分（%ポイント）」、それ以外は「変化率」で扱う
YIELD_KEYS = {"米10年金利", "米5年金利", "米2年金利"}

MIN_TRAIN = 750       # 最低学習日数（約3年）
REFIT_EVERY = 20      # 何日ごとに再学習するか
RIDGE = 1.0           # L2正則化の強さ
CORR_SHORT, CORR_LONG = 60, 250


# ---------------------------------------------------------------- データ取得
def fetch_close(symbol: str, range_str: str = "10y") -> pd.Series:
    """Yahoo Finance 公開チャートAPIから日足終値を取る（indexはJST日付）。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_str, "interval": "1d"}
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    idx = pd.to_datetime(res["timestamp"], unit="s", utc=True) \
            .tz_convert(JST).date
    s = pd.Series(closes, index=pd.Index(idx, name="Date"), dtype="float64")
    return s[~s.index.duplicated(keep="last")].dropna()


def update_macro(force: bool = False) -> pd.DataFrame:
    """マクロ系列を取得して data/macro_daily.csv にマージ保存する。"""
    frames, failed = {}, []
    for name, sym in MACRO_SYMBOLS.items():
        try:
            frames[name] = fetch_close(sym)
        except Exception as e:                                   # noqa: BLE001
            failed.append(f"{name}({type(e).__name__})")
    new = pd.DataFrame(frames)
    if os.path.exists(MACRO_CSV):
        old = pd.read_csv(MACRO_CSV, index_col=0, parse_dates=False)
        old.index = pd.Index([pd.Timestamp(d).date() for d in old.index],
                             name="Date")
        new = new.combine_first(old) if len(new) else old
    new = new.sort_index()
    os.makedirs(DATA_DIR, exist_ok=True)
    new.to_csv(MACRO_CSV)
    if failed:
        print(f"  ※マクロ取得失敗: {', '.join(failed)}（既存データで継続）")
    return new


def load_usdjpy() -> pd.Series:
    """日次の「その日のNYクローズ」系列を組み立てて返す。

    ★データ品質の注意（2026-08-11 検証）:
    Yahooの日足行は High/Low はその日のレンジなのに、Close だけが
    「前セッションの終値」になっている（724日で平均0.57円ずれ・
    前NYクローズとは平均0.16円/符号一致86.7%）。そのまま使うと
    終値と高安が1日ずれた系列で学習することになり予測が壊れるため、

      ・直近（1時間足がある期間）: 1時間足からJST6時境界でNYクローズを再構成
      ・それ以前              : 日足Closeを1日前へずらして日付を揃える

    の2段構えで正しい終値系列を作る。当日の進行中バーは使わない。
    """
    df = pd.read_csv(USDJPY_CSV)
    raw = pd.Series(df["Close"].values,
                    index=pd.Index([pd.Timestamp(d).date() for d in df["Date"]],
                                   name="Date"), dtype="float64").dropna()
    # 日足Close(d+1) ≒ d日のNYクローズ なので1日前へ寄せる
    shifted = pd.Series(raw.values[1:], index=raw.index[:-1], name="Close")

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from data_fetch_intraday import load_merged      # noqa: PLC0415
        h = load_merged().tz_convert(JST)
        # NYクローズ = JST6時境界。境界の直前バーの終値をその日の終値とする
        ny_day = pd.Index((h.index - pd.Timedelta(hours=6)).date, name="Date")
        cnt = h.groupby(ny_day)["Close"].size()
        recon = h.groupby(ny_day)["Close"].last()[cnt >= 12]
    except Exception as e:                                   # noqa: BLE001
        print(f"  ※1時間足からの終値再構成に失敗（{type(e).__name__}）"
              f"→ 日足ずらしのみで継続")
        recon = pd.Series(dtype="float64")

    s = shifted.copy()
    if len(recon):
        s = recon.combine_first(shifted)
        overlap = shifted.index.intersection(recon.index)
        if len(overlap) > 30:
            gap = (shifted[overlap] - recon[overlap]).abs().mean()
            print(f"  終値系列: 1時間足再構成{len(recon)}日＋日足ずらし"
                  f"{len(shifted) - len(overlap)}日"
                  f"（重複期間の平均差 {gap:.3f}円）")
    today = datetime.now(JST).date()
    return s[s.index < today].sort_index().dropna()


# ---------------------------------------------------------------- 特徴量
def build_features(px: pd.Series, macro: pd.DataFrame) -> pd.DataFrame:
    """t日終値時点で確定している情報のみから特徴量を作る。"""
    df = pd.DataFrame(index=px.index)
    ret = px.pct_change()
    df["モメンタム5"] = px.pct_change(5)
    df["モメンタム20"] = px.pct_change(20)
    vol = ret.rolling(20).std()
    df["ボラ比"] = (ret.rolling(5).std() / vol).replace([np.inf, -np.inf],
                                                        np.nan)
    hi, lo = px.rolling(20).max(), px.rolling(20).min()
    df["レンジ内位置"] = ((px - lo) / (hi - lo)).replace([np.inf, -np.inf],
                                                         np.nan)

    # マクロは米国時間の確定がJST翌朝になるため、さらに1日ラグを掛ける
    m = macro.reindex(px.index).ffill().shift(1)
    for name in macro.columns:
        if name not in m:
            continue
        col = m[name]
        if name in YIELD_KEYS:
            df[f"{name}Δ1"] = col.diff()
            df[f"{name}Δ5"] = col.diff(5)
        else:
            df[f"{name}変化1"] = col.pct_change()
            df[f"{name}変化5"] = col.pct_change(5)
    if "米10年金利" in m and "米2年金利" in m:
        df["日米長短差Δ5"] = (m["米10年金利"] - m["米2年金利"]).diff(5)
    if "VIX" in m:
        df["VIX水準"] = ((m["VIX"] - m["VIX"].rolling(250).mean())
                         / m["VIX"].rolling(250).std())
    return df.replace([np.inf, -np.inf], np.nan)


# ---------------------------------------------------------------- 学習
def _standardize(train: np.ndarray, x: np.ndarray):
    mu, sd = train.mean(axis=0), train.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    return (x - mu) / sd


def fit_logistic(X: np.ndarray, y: np.ndarray, ridge: float = RIDGE,
                 iters: int = 60) -> np.ndarray:
    """IRLS（ニュートン法）によるL2正則化ロジスティック回帰。"""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    reg = np.eye(Xb.shape[1]) * ridge
    reg[0, 0] = 0.0                       # 切片は正則化しない
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Xb @ w, -30, 30)))
        W = np.clip(p * (1 - p), 1e-6, None)
        grad = Xb.T @ (y - p) - reg @ w
        H = Xb.T @ (Xb * W[:, None]) + reg
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        w += step
        if np.max(np.abs(step)) < 1e-7:
            break
    return w


def predict_logistic(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    Xb = np.hstack([np.ones((len(X), 1)), X])
    return 1.0 / (1.0 + np.exp(-np.clip(Xb @ w, -30, 30)))


def walk_forward(feat: pd.DataFrame, target: pd.Series):
    """拡大窓ウォークフォワード。学習に使っていない日だけの予測を返す。"""
    data = feat.join(target.rename("y")).dropna()
    if len(data) < MIN_TRAIN + REFIT_EVERY:
        return pd.DataFrame(), None
    X_all = data.drop(columns="y").to_numpy(dtype="float64")
    y_all = data["y"].to_numpy(dtype="float64")
    rows, w = [], None
    for i in range(MIN_TRAIN, len(data)):
        if (i - MIN_TRAIN) % REFIT_EVERY == 0:
            Xtr = X_all[:i]
            w = fit_logistic(_standardize(Xtr, Xtr), y_all[:i])
            mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
            sd = np.where(sd < 1e-12, 1.0, sd)
        p = float(predict_logistic(w, ((X_all[i] - mu) / sd)[None, :])[0])
        rows.append({"日付": data.index[i], "上昇確率": p,
                     "実測": int(y_all[i])})
    return pd.DataFrame(rows).set_index("日付"), data


def walk_forward_range(feat: pd.DataFrame, rng: pd.Series):
    """変動幅モデルも学習外期間で検証する（順位相関と誤差改善率）。"""
    data = feat.join(rng.rename("r")).dropna()
    if len(data) < MIN_TRAIN + REFIT_EVERY:
        return {}
    X_all = data.drop(columns="r").to_numpy(dtype="float64")
    y_all = np.log(np.clip(data["r"].to_numpy(dtype="float64"), 1e-6, None))
    preds, actual, naive = [], [], []
    w = mu = sd = None
    for i in range(MIN_TRAIN, len(data)):
        if (i - MIN_TRAIN) % REFIT_EVERY == 0:
            Xtr, ytr = X_all[:i], y_all[:i]
            mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
            sd = np.where(sd < 1e-12, 1.0, sd)
            Z = np.hstack([np.ones((len(Xtr), 1)), (Xtr - mu) / sd])
            reg = np.eye(Z.shape[1]) * RIDGE
            reg[0, 0] = 0.0
            w = np.linalg.solve(Z.T @ Z + reg, Z.T @ ytr)
            base = float(ytr[-250:].mean())     # 直近平均だけの素朴予測
        z = np.hstack([[1.0], (X_all[i] - mu) / sd])
        preds.append(float(z @ w))
        actual.append(float(y_all[i]))
        naive.append(base)
    p, a, n = np.array(preds), np.array(actual), np.array(naive)
    mae, mae_n = np.mean(np.abs(a - p)), np.mean(np.abs(a - n))
    return {
        "検証日数": len(p),
        "順位相関": float(pd.Series(p).corr(pd.Series(a), method="spearman")),
        "誤差改善率": float(1 - mae / mae_n) if mae_n else 0.0,
    }


def fit_range_model(feat: pd.DataFrame, rng: pd.Series):
    """翌日の変動幅（|終値変化率|）を線形回帰で推定（対数空間）。"""
    data = feat.join(rng.rename("r")).dropna()
    if len(data) < MIN_TRAIN:
        return None
    X = data.drop(columns="r").to_numpy(dtype="float64")
    y = np.log(np.clip(data["r"].to_numpy(dtype="float64"), 1e-6, None))
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    reg = np.eye(Z.shape[1]) * RIDGE
    reg[0, 0] = 0.0
    w = np.linalg.solve(Z.T @ Z + reg, Z.T @ y)
    return {"w": w, "mu": mu, "sd": sd, "残差sd": float(np.std(y - Z @ w))}


def predict_range(model, x: np.ndarray) -> float:
    z = np.hstack([[1.0], (x - model["mu"]) / model["sd"]])
    return float(np.exp(z @ model["w"]))


# ---------------------------------------------------------------- 分析
def correlations(px: pd.Series, macro: pd.DataFrame) -> pd.DataFrame:
    ret = px.pct_change()
    m = macro.reindex(px.index).ffill()
    rows = []
    for name in macro.columns:
        chg = m[name].diff() if name in YIELD_KEYS else m[name].pct_change()
        both = pd.concat([ret, chg], axis=1).dropna()
        if len(both) < CORR_LONG:
            continue
        rows.append({
            "指標": name,
            f"相関{CORR_SHORT}日": both.iloc[-CORR_SHORT:, 0].corr(
                both.iloc[-CORR_SHORT:, 1]),
            f"相関{CORR_LONG}日": both.iloc[-CORR_LONG:, 0].corr(
                both.iloc[-CORR_LONG:, 1]),
        })
    df = pd.DataFrame(rows)
    if len(df):
        df["変化"] = df[f"相関{CORR_SHORT}日"] - df[f"相関{CORR_LONG}日"]
        df = df.reindex(df[f"相関{CORR_SHORT}日"].abs()
                        .sort_values(ascending=False).index)
    return df


def regime_label(corr: pd.DataFrame) -> str:
    if not len(corr):
        return "判定不能（データ不足）"
    top = corr.iloc[0]
    c = top[f"相関{CORR_SHORT}日"]
    if abs(c) < 0.20:
        return "無相関（テクニカル優位・独自要因主導）"
    driver = top["指標"]
    if driver in YIELD_KEYS:
        return f"金利連動（{driver}と{'順' if c > 0 else '逆'}相関 {c:+.2f}）"
    if driver in ("VIX",):
        return f"リスク回避連動（VIXと{'順' if c > 0 else '逆'}相関 {c:+.2f}）"
    if driver == "ドル指数":
        return f"ドル全面主導（ドル指数と{'順' if c > 0 else '逆'}相関 {c:+.2f}）"
    return f"{driver}主導（相関 {c:+.2f}）"


def bucket_edge(wf: pd.DataFrame, ret_next: pd.Series) -> pd.DataFrame:
    """予測確率の分位ごとに翌日リターンの実測平均を出す（エッジの有無）。"""
    d = wf.join(ret_next.rename("翌日変化率")).dropna()
    if len(d) < 100:
        return pd.DataFrame()
    d["区分"] = pd.qcut(d["上昇確率"], 5,
                        labels=["最も弱気", "弱気", "中立", "強気", "最も強気"])
    g = d.groupby("区分", observed=True).agg(
        日数=("実測", "size"), 上昇率=("実測", "mean"),
        平均変化率=("翌日変化率", "mean"))
    g["上昇率"] = g["上昇率"].map(lambda v: f"{v:.1%}")
    g["平均変化率"] = g["平均変化率"].map(lambda v: f"{v*100:+.3f}%")
    return g.reset_index()


# ---------------------------------------------------------------- 前向き採点
def score_forecast_log(px: pd.Series) -> dict:
    """過去に記録した予測を実測で採点する（真の前向き検証）。"""
    if not os.path.exists(FORECAST_LOG):
        return {"件数": 0, "的中率": None}
    log = pd.read_csv(FORECAST_LOG)
    if "実測方向" not in log.columns:
        log["実測方向"] = np.nan
        log["的中"] = np.nan
    changed = False
    for i, row in log.iterrows():
        if pd.notna(row.get("的中")):
            continue
        base = pd.Timestamp(row["基準日"]).date()
        later = px[px.index > base]
        if not len(later):
            continue
        realized = 1 if later.iloc[0] > float(row["基準終値"]) else 0
        log.at[i, "対象日"] = str(later.index[0])
        log.at[i, "実測終値"] = float(later.iloc[0])
        log.at[i, "実測方向"] = realized
        log.at[i, "的中"] = int(realized == int(row["予測方向"]))
        changed = True
    if changed:
        log.to_csv(FORECAST_LOG, index=False)
    done = log[log["的中"].notna()]
    if not len(done):
        return {"件数": 0, "的中率": None}
    conf = done[abs(done["上昇確率"] - 0.5) >= 0.05]
    return {
        "件数": int(len(done)),
        "的中率": float(done["的中"].mean()),
        "高信頼件数": int(len(conf)),
        "高信頼的中率": float(conf["的中"].mean()) if len(conf) else None,
    }


def append_forecast(base_date, base_close, p_up, rng_pct):
    os.makedirs(RES_DIR, exist_ok=True)
    row = {
        "記録時刻": datetime.now(JST).strftime("%Y-%m-%dT%H:%M"),
        "基準日": str(base_date), "基準終値": round(float(base_close), 3),
        "上昇確率": round(float(p_up), 4),
        "予測方向": int(p_up >= 0.5),
        "想定変動率": round(float(rng_pct), 5),
        "対象日": "", "実測終値": "", "実測方向": "", "的中": "",
    }
    if os.path.exists(FORECAST_LOG):
        log = pd.read_csv(FORECAST_LOG)
        if len(log) and str(log["基準日"].iloc[-1]) == str(base_date):
            return False        # 同じ基準日の二重記録はしない
        log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
    else:
        log = pd.DataFrame([row])
    log.to_csv(FORECAST_LOG, index=False)
    return True


# ---------------------------------------------------------------- ニュース
def load_news() -> dict:
    if not os.path.exists(NEWS_JSON):
        return {"状態": "未収集", "テーマ": [], "総括": ""}
    try:
        news = json.load(open(NEWS_JSON, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {"状態": "読み込み失敗", "テーマ": [], "総括": ""}
    stamp = news.get("更新", "")
    try:
        age_h = (datetime.now(JST)
                 - datetime.fromisoformat(stamp).replace(tzinfo=JST)
                 ).total_seconds() / 3600
        news["状態"] = "最新" if age_h <= 36 else f"古い（{age_h:.0f}時間前）"
    except Exception:                                            # noqa: BLE001
        news["状態"] = "日時不明"
    return news


# ---------------------------------------------------------------- 本体
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-log", action="store_true",
                    help="予測をforecast_log.csvへ記録しない")
    args = ap.parse_args()

    print("=== ファンダ・予測研究 ===")
    macro = update_macro()
    px = load_usdjpy()
    print(f"データ: ドル円日足{len(px)}本 {px.index[0]} -> {px.index[-1]}"
          f" ／ マクロ{macro.shape[1]}系列")

    fwd = score_forecast_log(px)

    corr = correlations(px, macro)
    print("\n--- ① マクロ連関（ドル円 日次変化との相関）---")
    if len(corr):
        disp = corr.copy()
        for c in disp.columns[1:]:
            disp[c] = disp[c].map(lambda v: f"{v:+.2f}")
        print(disp.to_string(index=False))
    regime = regime_label(corr)
    print(f"現在のレジーム: {regime}")

    feat = build_features(px, macro)
    ret_next = px.pct_change().shift(-1)
    target = (ret_next > 0).astype(float)
    target[ret_next.isna()] = np.nan

    print("\n--- ② 翌日予測（ウォークフォワード＝学習外期間のみ）---")
    wf, data = walk_forward(feat, target)
    wf_stats = {}
    if len(wf):
        hit = float((wf["上昇確率"].round() == wf["実測"]).mean())
        recent = wf.iloc[-250:]
        hit250 = float((recent["上昇確率"].round() == recent["実測"]).mean())
        base = float(wf["実測"].mean())
        wf_stats = {"検証日数": len(wf), "的中率": hit, "直近250日的中率": hit250,
                    "上昇日比率": base}
        print(f"検証日数 {len(wf)}日（{wf.index[0]} -> {wf.index[-1]}）")
        print(f"的中率 {hit:.1%}（直近250日 {hit250:.1%}／"
              f"常に上昇と答えた場合 {base:.1%}）")
        edge = bucket_edge(wf, ret_next)
        if len(edge):
            print("\n予測確率5分位ごとの実測:")
            print(edge.to_string(index=False))
    else:
        print("（学習データ不足）")

    rng_stats = walk_forward_range(feat, ret_next.abs())
    if rng_stats:
        print(f"\n変動幅（ボラ）予測の学習外検証: 順位相関 "
              f"{rng_stats['順位相関']:+.2f}／素朴予測より誤差 "
              f"{rng_stats['誤差改善率']:+.1%}")
    rng_model = fit_range_model(feat, ret_next.abs())

    print("\n--- ③ 前向き記録（実運用での予測採点）---")
    if fwd["件数"]:
        hi = (f"／高信頼{fwd['高信頼件数']}件 {fwd['高信頼的中率']:.1%}"
              if fwd.get("高信頼的中率") is not None else "")
        print(f"採点済み {fwd['件数']}件・的中率 {fwd['的中率']:.1%}{hi}")
    else:
        print("採点済みの予測はまだなし（明日から蓄積）")

    # 当日の予測
    latest = feat.dropna()
    forecast = {}
    if len(latest) and len(wf) and data is not None:
        cols = data.drop(columns="y").columns
        hist = data.drop(columns="y")
        Xtr = hist.to_numpy(dtype="float64")
        mu, sd = Xtr.mean(axis=0), Xtr.std(axis=0)
        sd = np.where(sd < 1e-12, 1.0, sd)
        w = fit_logistic((Xtr - mu) / sd, data["y"].to_numpy(dtype="float64"))
        x = latest[cols].iloc[-1].to_numpy(dtype="float64")
        p_up = float(predict_logistic(w, ((x - mu) / sd)[None, :])[0])
        base_date, base_close = px.index[-1], float(px.iloc[-1])
        rng_pct = predict_range(rng_model, x) if rng_model else \
            float(px.pct_change().abs().iloc[-20:].mean())
        band = base_close * rng_pct
        forecast = {
            "基準日": str(base_date), "基準終値": round(base_close, 3),
            "上昇確率": round(p_up, 3),
            "方向": "上昇（円安）" if p_up >= 0.5 else "下落（円高）",
            "想定変動幅_円": round(band, 3),
            "想定レンジ": [round(base_close - band, 2),
                           round(base_close + band, 2)],
        }
        print(f"\n翌営業日予測（基準 {base_date} 終値{base_close:.3f}円）")
        print(f"  上昇確率 {p_up:.1%} → {forecast['方向']}"
              f"／想定変動 ±{band:.2f}円"
              f"（{forecast['想定レンジ'][0]:.2f}〜"
              f"{forecast['想定レンジ'][1]:.2f}円）")
        if not args.no_log:
            if append_forecast(base_date, base_close, p_up, rng_pct):
                print("  → forecast_log.csv に記録（翌日以降に自動採点）")
            else:
                print("  → 同じ基準日の記録が既にあるためスキップ")

    news = load_news()
    print("\n--- ④ 定性ニュース（Claudeが収集）---")
    print(f"状態: {news.get('状態')}／総括: {news.get('総括') or '（なし）'}")
    for t in (news.get("テーマ") or [])[:5]:
        print(f"  ・[{t.get('含意', '中立')}] {t.get('見出し', '')}")

    out = {
        "更新": datetime.now(JST).strftime("%Y-%m-%dT%H:%M"),
        "レジーム": regime,
        "相関": corr.round(3).to_dict(orient="records") if len(corr) else [],
        "予測モデル": wf_stats,
        "ボラ予測": rng_stats,
        "前向き成績": fwd,
        "翌日予測": forecast,
        "ニュース": news,
    }
    os.makedirs(RES_DIR, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {OUT_JSON}")
    return out


if __name__ == "__main__":
    sys.exit(0 if main() else 0)
