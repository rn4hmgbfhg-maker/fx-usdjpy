# -*- coding: utf-8 -*-
"""ファンダメンタルズの運用反映（新規建ての数量調整） — 全4システム共用

2026-09-21 ユーザー指示「ファンダを運用に反映」の実装。

何をするか（リスクを減らす方向にしか働かない）:
  ・ニュース逆風: Claudeが収集した定性ニュース（results/fundamental_news.json）
    のうち重要度5の材料の含意が新規建ての向きと逆（買い新規に「円高」／
    売り新規に「円安」）で、順風の材料より多ければ新規数量を×0.5にする。
    介入・レートチェック・要人発言・重要指標の結果など、価格が織り込み切る前の
    非対称リスクに対して裁量トレーダーが取る「半分で入る」をルール化したもの。
  ・予測逆風: 定量予測モデル（research_fundamental.py）の翌日方向が新規建ての
    向きと逆で、上昇確率が 0.5±閾値 を超えて偏っている時に×0.5。ただし
    予測モデルは学習外検証で「常に上昇」を下回りエッジが無いため、
    前向き採点（forecast_log.csv）が60件以上かつ的中率が基準（上昇日比率）を
    上回るまでは自動で待機する（config「強制稼働」でのみ先行稼働できる）。

何をしないか:
  ・新規建てをブロックしない（ブロックは★5指標イベントのみ＝events.py）
  ・数量を増やさない（順風でも×1.0まで）
  ・保有玉の決済・逆指値・利確には一切触れない
  ・情報が古い時（ニュース24時間超／予測の基準日が4日超）は何もしない

根拠（2026-09-21 `--backtest` 実測。予測方向をそのまま新規建てのフィルタにした場合）:
  デイトレ2H   頑健スコア 1.38 → 1.32〜1.37（全閾値で低下）
  デイトレ15分 頑健スコア 1.09 → 1.07〜1.08（全閾値で低下）
  日足2系統    取引数30／116で有意差なし（日付整合の取り方で符号が変わる）
  → 予測はブロック型フィルタには使えない。数量調整＋実績待機方式にした理由。

使い方:
  python3 src/fundamental_filter.py             # 現在の判定（買い/売り新規）を表示
  python3 src/fundamental_filter.py --backtest  # 予測フィルタの学習外検証を再実行
"""
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUND_JSON = os.path.join(BASE_DIR, "results", "fundamental_latest.json")
NEWS_JSON = os.path.join(BASE_DIR, "results", "fundamental_news.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    "有効": True,
    "減量倍率": 0.5,
    "ニュース": {"有効": True, "重要度": 5, "鮮度時間": 24},
    "予測": {"有効": True, "閾値": 0.10, "有効日数": 4,
             "稼働条件_前向き件数": 60, "強制稼働": False},
}
CFG_KEY = "ファンダ反映"


def settings(cfg):
    user = (cfg or {}).get(CFG_KEY) or {}
    out = {**DEFAULTS, **user}
    for k in ("ニュース", "予測"):
        out[k] = {**DEFAULTS[k], **(user.get(k) or {})}
    return out


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                            # noqa: BLE001
        return None


def load():
    """(定量JSON, ニュースdict)。ニュースは fundamental_news.json と latest 内包分の
    新しい方を採る（dashboard_web と同じ規則）。"""
    fund = _load(FUND_JSON) or {}
    news = fund.get("ニュース") or {}
    direct = _load(NEWS_JSON)
    if direct and str(direct.get("更新") or "") > str(news.get("更新") or ""):
        news = direct
    return fund, news


def _age_hours(stamp, now):
    try:
        return (now - datetime.fromisoformat(str(stamp))).total_seconds() / 3600
    except Exception:                                            # noqa: BLE001
        return None


def _news_view(news, direction, s, now):
    """重要度しきい値以上の材料を新規方向に対する逆風/順風へ振り分ける。"""
    age = _age_hours(news.get("更新"), now)
    v = {"更新": news.get("更新"), "鮮度h": age, "逆風": [], "順風": [],
         "有効": False}
    if not s["有効"] or age is None or age > float(s["鮮度時間"]):
        return v
    against = "円高" if direction > 0 else "円安"
    favor = "円安" if direction > 0 else "円高"
    for t in news.get("テーマ") or []:
        try:
            imp = int(t.get("重要度", 0))
        except (TypeError, ValueError):
            continue
        if imp < int(s["重要度"]):
            continue
        imp_s = str(t.get("含意") or "")
        head = str(t.get("見出し") or "")
        if imp_s.startswith(against):
            v["逆風"].append(head)
        elif imp_s.startswith(favor):
            v["順風"].append(head)
    v["有効"] = True
    return v


def _forecast_view(fund, direction, s, now):
    fc = fund.get("翌日予測") or {}
    fwd = fund.get("前向き成績") or {}
    wf = fund.get("予測モデル") or {}
    n = int(fwd.get("件数") or 0)
    hit = fwd.get("的中率")
    base = wf.get("上昇日比率")
    need = int(s["稼働条件_前向き件数"])
    earned = (n >= need and hit is not None and base is not None
              and float(hit) > float(base))
    active = bool(s["有効"]) and (earned or bool(s["強制稼働"]))
    v = {"稼働": active, "強制": bool(s["強制稼働"]) and not earned,
         "上昇確率": fc.get("上昇確率"), "基準日": fc.get("基準日"),
         "方向": fc.get("方向"), "逆風": False, "鮮度日": None}
    if hit is not None and base is not None:
        v["待機理由"] = (f"前向き{n}件・的中率{float(hit):.1%}"
                     f"{'＞' if float(hit) > float(base) else '≦'}基準"
                     f"{float(base):.1%}")
    else:
        v["待機理由"] = f"前向き{n}件（採点待ち）"
    if not earned:
        v["待機理由"] += f"→{need}件以上かつ基準超で自動稼働"
    p = v["上昇確率"]
    try:
        v["鮮度日"] = (now.date() - datetime.fromisoformat(
            str(v["基準日"])).date()).days
    except Exception:                                            # noqa: BLE001
        pass
    fresh = v["鮮度日"] is not None and v["鮮度日"] <= int(s["有効日数"])
    if active and fresh and p is not None:
        th = float(s["閾値"])
        p = float(p)
        v["逆風"] = (p <= 0.5 - th) if direction > 0 else (p >= 0.5 + th)
    return v


def assess(direction, cfg=None, now=None, data=None):
    """新規建て（direction=+1買い/-1売り）に対する数量倍率と理由を返す。

    返り値: {"倍率": 1.0|減量倍率, "理由": [文], "状態": 一行, "ニュース": {..},
            "予測": {..}, "有効": bool}
    """
    cfg = cfg if cfg is not None else (_load(CONFIG_PATH) or {})
    s = settings(cfg)
    now = now or datetime.now()
    fund, news = data if data is not None else load()
    side = "買い" if direction > 0 else "売り"
    out = {"倍率": 1.0, "理由": [], "有効": bool(s["有効"]), "方向": side}
    nv = _news_view(news, direction, s["ニュース"], now)
    fv = _forecast_view(fund, direction, s["予測"], now)
    out["ニュース"], out["予測"] = nv, fv
    if not s["有効"]:
        out["状態"] = "ファンダ反映: 無効（config）"
        return out
    mult = float(s["減量倍率"])
    if nv["有効"] and nv["逆風"] and len(nv["逆風"]) > len(nv["順風"]):
        out["倍率"] = mult
        heads = "／".join(h[:28] for h in nv["逆風"][:2])
        out["理由"].append(f"★{s['ニュース']['重要度']}材料が{side}に逆風"
                           f"{len(nv['逆風'])}件（{heads}）")
    if fv["逆風"]:
        out["倍率"] = min(out["倍率"], mult)
        out["理由"].append(f"予測モデルが{side}に逆風"
                           f"（上昇確率{float(fv['上昇確率']):.0%}）")
    if out["倍率"] < 1.0:
        out["状態"] = (f"{side}新規 ×{out['倍率']:g}"
                       f"（{'・'.join(out['理由'])}）")
    elif nv["有効"] or fv["稼働"]:
        out["状態"] = f"{side}新規 ×1.0（逆風材料なし）"
    else:
        out["状態"] = f"{side}新規 ×1.0（ファンダ情報が古いため調整なし）"
    return out


def apply_units(units, lot, mult):
    """数量に倍率を掛けて取引単位へ切り下げる（最低1単位は残す）。"""
    if mult >= 1.0:
        return int(units)
    return int(max(lot, (units * mult) // lot * lot))


def describe(cfg=None):
    s = settings(cfg if cfg is not None else (_load(CONFIG_PATH) or {}))
    if not s["有効"]:
        return "ファンダ反映 無効"
    return (f"ファンダ反映 ★{s['ニュース']['重要度']}逆風×{s['減量倍率']:g}"
            f"・予測逆風×{s['減量倍率']:g}")


def stance(cfg=None, now=None):
    """現在の姿勢（状態JSON・ボード用）。買い/売り新規それぞれの倍率と理由。"""
    cfg = cfg if cfg is not None else (_load(CONFIG_PATH) or {})
    now = now or datetime.now()
    data = load()
    b = assess(1, cfg, now, data)
    s_ = assess(-1, cfg, now, data)
    fv = b["予測"]
    return {"買い新規": b["状態"], "売り新規": s_["状態"],
            "買い倍率": b["倍率"], "売り倍率": s_["倍率"],
            "ニュース鮮度h": (round(b["ニュース"]["鮮度h"], 1)
                          if b["ニュース"].get("鮮度h") is not None else None),
            "予測": ("稼働中" + ("（強制）" if fv.get("強制") else "")
                   if fv["稼働"] else f"待機（{fv.get('待機理由', '')}）")}


def status_lines(cfg=None, now=None):
    """指示書に載せる「◆ ファンダ反映」ブロック（毎回表示）。"""
    cfg = cfg if cfg is not None else (_load(CONFIG_PATH) or {})
    now = now or datetime.now()
    data = load()
    fund, news = data
    b = assess(1, cfg, now, data)
    s_ = assess(-1, cfg, now, data)
    lines = ["", "◆ ファンダ反映（新規建ての数量調整のみ・決済/逆指値には不介入）"]
    if not b["有効"]:
        lines.append("  無効（config「ファンダ反映」）")
        return lines
    lines.append(f"  {b['状態']}　{s_['状態']}")
    nv = b["ニュース"]
    n_s = "ニュース未収集"
    if nv.get("更新"):
        age = nv.get("鮮度h")
        n_s = (f"ニュース更新 {str(nv['更新'])[5:16].replace('T', ' ')}"
               + (f"（{age:.0f}時間前{'・古いため不使用' if not nv['有効'] else ''}）"
                  if age is not None else ""))
    fv = b["予測"]
    if fv["稼働"]:
        f_s = "予測: 稼働中" + ("（強制）" if fv.get("強制") else "")
        if fv.get("鮮度日") is not None and fv["鮮度日"] > int(
                settings(cfg)["予測"]["有効日数"]):
            f_s += f"・基準日{fv['基準日']}が古いため不使用"
    else:
        f_s = f"予測: 待機（{fv.get('待機理由', '')}）"
    lines.append(f"  {n_s}／{f_s}")
    if fund.get("レジーム") or fv.get("上昇確率") is not None:
        p = fv.get("上昇確率")
        lines.append(f"  レジーム: {fund.get('レジーム', '—')}"
                     + (f"　翌日予測: {fv.get('方向', '')}"
                        f" 上昇確率{float(p):.0%}（基準 {fv.get('基準日')}）"
                        if p is not None else ""))
    return lines


# ---------------------------------------------------------------- 検証
def forecast_probabilities():
    """予測モデルの学習外（ウォークフォワード）上昇確率。index=基準日(date)。"""
    import numpy as np
    import pandas as pd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import research_fundamental as rf
    macro = pd.read_csv(rf.MACRO_CSV, index_col=0)
    macro.index = pd.Index([pd.Timestamp(d).date() for d in macro.index],
                           name="Date")
    px = rf.load_usdjpy()
    feat = rf.build_features(px, macro)
    ret_next = px.pct_change().shift(-1)
    target = (ret_next > 0).astype(float)
    target[ret_next.isna()] = np.nan
    wf, _ = rf.walk_forward(feat, target)
    return wf["上昇確率"]


def entry_mask(bar_end_utc, probs, threshold):
    """バー終了時刻(UTC)ごとに (allow_long, allow_short)。基準日Dの予測は
    翌暦日06:00 JSTから有効。予測が無い期間は許可（NaN=True）。"""
    import numpy as np
    import pandas as pd
    avail = (pd.to_datetime(probs.index) + pd.Timedelta(days=1, hours=6)
             ).tz_localize("Asia/Tokyo").tz_convert("UTC")
    fc = pd.DataFrame({"t": avail, "p": probs.values}).sort_values("t")
    t = pd.to_datetime(pd.Series(bar_end_utc))
    t = t.dt.tz_convert("UTC") if t.dt.tz is not None else t.dt.tz_localize("UTC")
    left = pd.DataFrame({"t": t.reset_index(drop=True)})
    p = pd.merge_asof(left, fc, on="t", direction="backward")["p"].values
    lg = np.where(np.isnan(p), True, ~(p <= 0.5 - threshold))
    sh = np.where(np.isnan(p), True, ~(p >= 0.5 + threshold))
    return lg.astype(bool), sh.astype(bool)


def backtest(thresholds=(None, 0.05, 0.10, 0.15)):
    """現行config の4システムで「予測逆行なら新規見送り」を重ねた成績を比較する。"""
    import numpy as np
    import pandas as pd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from backtest import Backtester
    from data_fetch_intraday import load_merged, resample
    from strategies_intraday import align_trend, donchian_mtf
    from data_fetch_15m import load_15m
    from research_15m import build_df15
    import strategies_15m as s15
    import strategies
    import tech_filters

    cfg = _load(CONFIG_PATH)
    probs = forecast_probabilities()
    print(f"学習外予測 {len(probs)}日 {probs.index[0]} -> {probs.index[-1]}")

    def table(name, dfe, make_sig, bar_end, risk, lev, atr_k, tp_k):
        half = len(dfe) // 2
        rows = []
        for th in thresholds:
            conf = entry_mask(bar_end, probs, th) if th is not None else None
            sig = make_sig(conf)
            r = {"閾値": "なし" if th is None else th}
            for key, d_, s_ in (("全", dfe, sig),
                                ("前", dfe.iloc[:half], sig.iloc[:half]),
                                ("後", dfe.iloc[half:], sig.iloc[half:])):
                bt = Backtester(d_.reset_index(drop=True), risk_per_trade=risk,
                                max_leverage=lev, atr_stop_mult=atr_k,
                                tp_atr_mult=tp_k or None)
                m = bt.run(s_.reset_index(drop=True))["metrics"]
                r[f"PF{key}"] = m["プロフィットファクター"]
                if key == "全":
                    r.update({"取引数": m["取引回数"], "勝率": m["勝率"],
                              "最大DD": m["最大ドローダウン"]})
            r["スコア"] = min(r["PF前"], r["PF後"])
            rows.append(r)
        print(f"\n=== {name}（{dfe['Date'].iloc[0]} -> {dfe['Date'].iloc[-1]}）===")
        print(pd.DataFrame(rows).to_string(index=False,
                                           float_format=lambda v: f"{v:.3f}"))

    d = cfg["デイトレ"]
    df1h = load_merged(fetch=False)
    dfe = resample(df1h, int(d["時間足"]))
    df4 = resample(df1h, 4)
    trend = (align_trend(dfe, df4, int(d["4Hフィルタ期間"]))
             if d.get("4Hフィルタ期間") else None)
    tech = (tech_filters.entry_mask(dfe, d["確認フィルタ"])
            if d.get("確認フィルタ") else None)
    end2 = pd.to_datetime(dfe["Date"]).dt.tz_localize("Asia/Tokyo")

    def mk2(conf):
        c = tech
        if conf is not None:
            c = (tech[0] & conf[0], tech[1] & conf[1]) if tech else conf
        return donchian_mtf(dfe, int(d["エントリー期間"]),
                            int(d["イグジット期間"]), trend, c)
    table("デイトレ複合時間軸 現行", dfe, mk2, end2, float(d["リスク率"]),
          float(d["レバ上限"]), float(d["ATRストップ係数"]),
          float(d.get("利確ATR係数", 0)))

    d15 = cfg["デイトレ15分"]
    spec = d15["パラメータ"]
    dft = s15.to_tf(build_df15(load_15m(fetch=False)), int(spec["時間足分"]))
    end15 = (pd.to_datetime(dft["utc"], utc=True)
             + pd.Timedelta(minutes=int(spec["時間足分"])))
    table("デイトレ15分 現行", dft, lambda c: s15.build_signal(dft, spec, c),
          end15, float(d15["リスク率"]), float(d15["レバ上限"]),
          float(d15["ATRストップ係数"]), float(d15.get("利確ATR係数", 0)))

    dd = pd.read_csv(os.path.join(BASE_DIR, "data", "usdjpy_daily.csv"))
    first = pd.Timestamp(probs.index[0]) - pd.Timedelta(days=90)
    dd = dd[pd.to_datetime(dd["Date"]) >= first].reset_index(drop=True)
    # 日足の行日付D＝基準日D（その行の引けで判定し翌営業日寄付で執行）
    endd = (pd.to_datetime(dd["Date"]).dt.tz_localize("Asia/Tokyo")
            + pd.Timedelta(days=1, hours=6) - pd.Timedelta(minutes=1))
    for name, sc in cfg["システム"].items():
        table(f"{name} 現行（予測が存在する期間のみ）", dd,
              lambda c, sc=sc: strategies.donchian(
                  dd, int(sc["エントリー期間"]), int(sc["イグジット期間"]), c),
              endd, float(sc["リスク率"]), float(sc["レバ上限"]),
              float(sc["ATRストップ係数"]), 0.0)
    _ = np


if __name__ == "__main__":
    if "--backtest" in sys.argv[1:]:
        backtest()
    else:
        print("\n".join(status_lines()))
