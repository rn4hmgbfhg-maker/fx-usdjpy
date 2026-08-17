# -*- coding: utf-8 -*-
"""FXドル円スキーム — Cowork（クラウド）側の唯一のデータ取得口

クラウドのClaude実行環境は外向き通信が raw.githubusercontent.com と PyPI
以外すべて遮断されている（2026-08-14実測）。したがって Yahoo Finance も
Forex Factory もクラウドからは叩けない。データ取得と売買判定は GitHub
Actions（外向き自由）が15分ごとに行い、結果をリポジトリへコミットする。
このスクリプトはその結果を raw 経由で読むだけに徹する。

★重要な設計方針（守ること）
  1. ここは「読む」だけ。判定も pips 計算も一切しない。発注カードの
     利確幅・損切り幅は Actions 側の risk.order_widths() が算出済みの
     確定値であり、クラウド側で再計算すると数字が二重管理になる
     （memory feedback_fix_all_output_targets / feedback_fx_order_card_pips）。
  2. 標準ライブラリのみ。pip install 不要で動く。
  3. 取得に失敗したら黙って0件を返さず、必ずエラーとして表に出す。

使い方（スキルから呼ぶ）:
  python3 scripts/fx_fetch.py --status        # 全4システムの現況＋鮮度
  python3 scripts/fx_fetch.py --card          # 本日の指示書（最上位ステージ）
  python3 scripts/fx_fetch.py --card --stage 確定
  python3 scripts/fx_fetch.py --intraday --since-min 70   # 直近の未通知アクション
  python3 scripts/fx_fetch.py --research      # 日次研究・週次ペア研究の最新行
  python3 scripts/fx_fetch.py --selftest      # 全経路の到達確認
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
REPO = os.environ.get("FX_REPO", "rn4hmgbfhg-maker/fx-usdjpy")
BRANCH = os.environ.get("FX_BRANCH", "main")
BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

# 鮮度のしきい値（分）。Actionsは15分間隔なので、この時間を超えて更新が
# 無ければ Actions 停止・失敗を疑う。市場休場（土13:00〜月6:00 JST）は除外。
STALE_MIN = int(os.environ.get("FX_STALE_MIN", "60"))

SYSTEMS_DAILY = ["長期トレンド", "短期スイング"]


class FetchError(RuntimeError):
    pass


def _get(path, binary=False):
    """raw から1ファイル取得。404は None、その他の失敗は例外。

    raw.githubusercontent.com はCDNで最大5分キャッシュされる。判定の鮮度が
    命なので、キャッシュキーを変えるためのクエリを毎回付ける（ベストエフォート）。
    """
    url = f"{BASE}/{urllib.parse.quote(path)}?cb={datetime.now(JST):%Y%m%d%H%M%S}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "fx-cowork/1.0",
        "Cache-Control": "no-cache",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return raw if binary else raw.decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise FetchError(f"取得失敗 HTTP{e.code}: {path}") from e
    except Exception as e:                       # noqa: BLE001
        raise FetchError(
            f"取得失敗: {path} — {type(e).__name__}: {e}\n"
            f"  クラウドで EGRESS_BLOCKED が出る場合、宛先が raw.githubusercontent.com "
            f"以外になっていないか確認する（金融APIは遮断されている）"
        ) from e


def _get_json(path):
    t = _get(path)
    if t is None:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise FetchError(f"JSONとして読めない: {path} — {e}") from e


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], fmt).replace(tzinfo=JST)
        except ValueError:
            continue
    return None


def market_closed(now=None):
    """FX市場の休場（土13:00 JST 〜 月6:00 JST）か。鮮度警告の抑制に使う。"""
    now = now or datetime.now(JST)
    wd, h = now.weekday(), now.hour          # 月=0 … 土=5 日=6
    if wd == 5 and h >= 13:
        return True
    if wd == 6:
        return True
    if wd == 0 and h < 6:
        return True
    return False


def freshness(updated, now=None):
    """更新時刻から鮮度を判定して dict で返す。"""
    now = now or datetime.now(JST)
    dt = _parse_dt(updated)
    if dt is None:
        return {"更新": updated, "経過分": None, "鮮度": "不明",
                "警告": "更新時刻を読めませんでした"}
    age = int((now - dt).total_seconds() // 60)
    closed = market_closed(now)
    stale = age > STALE_MIN and not closed
    return {"更新": dt.strftime("%Y-%m-%d %H:%M"), "経過分": age,
            "鮮度": "古い" if stale else ("休場中" if closed else "最新"),
            "警告": (f"★{age}分間 更新がありません。GitHub Actions が停止/失敗している"
                     f"可能性があります（Actionsタブの fx-signals を確認）"
                     if stale else "")}


# ---------------------------------------------------------------- 現況
def status():
    """全4システムの建玉・注文水準・鮮度をまとめて返す。"""
    out = {"取得元": BASE, "取得時刻": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
           "システム": {}, "警告": []}

    st = _get_json("state.json") or {}
    for name in SYSTEMS_DAILY:
        s = st.get(name) or {}
        out["システム"][name] = {
            "区分": "日足", "ポジション": s.get("position", 0),
            "建値": s.get("entry"), "数量": s.get("units", 0),
            "決済逆指値": s.get("stop"), "決済指値(利確)": None,
            "建玉日": s.get("entry_date"),
            "利確設計": "利確目標を置かない設計（トレンド追随）",
        }

    for key, path, label in (
        ("デイトレ複合時間軸", "state_intraday.json", "2時間足"),
        ("デイトレ15分", "state_intraday15.json", "1時間足"),
    ):
        d = _get_json(path) or {}
        s = d.get("state") or {}
        out["システム"][key] = {
            "区分": label, "ポジション": s.get("position", 0),
            "建値": s.get("entry"), "数量": s.get("units", 0),
            "決済逆指値": s.get("stop"), "決済指値(利確)": s.get("tp"),
            "建玉日": s.get("entry_date"),
            "確定バー": d.get("last_bar"), "終値": d.get("last_close"),
        }
        out["システム"][key].update({"鮮度": freshness(d.get("updated"))})

    for key, path in (("デイトレ複合時間軸", "results/intraday_status.json"),
                      ("デイトレ15分", "results/intraday15_status.json")):
        s = _get_json(path) or {}
        out["システム"][key]["アクション"] = s.get("アクション")
        out["システム"][key]["試験運転"] = s.get("試験運転", False)
        f = freshness(s.get("更新"))
        out["システム"][key]["鮮度"] = f
        if f["警告"]:
            out["警告"].append(f"{key}: {f['警告']}")

    return out


# ---------------------------------------------------------------- 指示書
STAGE_RANK = {"速報1": 1, "速報2": 2, "確定": 3}


def card(stage=None, day=None):
    """本日（またはday）の日足指示書を返す。stage未指定なら最上位を選ぶ。

    Actionsは 速報1→速報2→確定 の順に上書き生成する。発注に使えるのは
    【最終確定】のみで、速報段階のカードで発注してはいけない。
    """
    day = day or datetime.now(JST).strftime("%Y-%m-%d")
    stages = [stage] if stage else ["確定", "速報2", "速報1"]
    for s in stages:
        text = _get(f"results/orders/{day}_指示_{s}.txt")
        if text:
            return {"日付": day, "段階": s, "本文": text,
                    "発注可": s == "確定",
                    "pips欄": "幅（pips）" in text,
                    "注意": ("" if s == "確定" else
                             "速報段階です。発注は【最終確定】のカードで行うこと"),
                    "エンジン版": ("pips対応版" if "幅（pips）" in text else
                                "★旧版（幅（pips）欄なし）。リポジトリのsrcが"
                                "pips対応前です。数値を自作せず、この旨を報告すること")}
    return {"日付": day, "段階": None, "本文": None,
            "注意": f"{day} の指示書がまだありません（JST6時台以降に生成されます）"}


def intraday_actions(since_min=70):
    """デイトレ2系統の「未通知」キューから直近 since_min 分の分だけ返す。

    クラウドからはキューを既読化（ack）できないため、時刻で窓を切って
    重複通知を防ぐ。ルーティンの実行間隔より少し広い窓にすること。
    """
    now = datetime.now(JST)
    res = []
    for label, path in (("デイトレ複合時間軸", "results/intraday_status.json"),
                        ("デイトレ15分", "results/intraday15_status.json")):
        s = _get_json(path) or {}
        for q in (s.get("未通知") or []):
            dt = None
            qid = str(q.get("id") or "")
            if len(qid) == 14 and qid.isdigit():
                dt = datetime.strptime(qid, "%Y%m%d%H%M%S").replace(tzinfo=JST)
            if dt and (now - dt) <= timedelta(minutes=since_min):
                res.append({"システム": label, "時刻": q.get("時刻"),
                            "アクション": q.get("アクション"),
                            "要旨": q.get("要旨"), "id": qid,
                            "pips記載": "pips" in str(q.get("要旨") or "")})
    return sorted(res, key=lambda r: r["id"])


def intraday_card(system="2H", day=None):
    """デイトレの最新指示書テキスト（発注カード込み）を返す。"""
    path = ("results/intraday_latest.txt" if system == "2H"
            else "results/intraday15_latest.txt")
    text = _get(path)
    if not text:
        return {"本文": None, "注意": f"{path} がまだありません"}
    return {"本文": text, "pips欄": "幅（pips）" in text,
            "エンジン版": ("pips対応版" if "幅（pips）" in text else
                        "★旧版（幅（pips）欄なし）")}


# ---------------------------------------------------------------- 研究
def _tail_csv(path, n=3):
    t = _get(path)
    if not t:
        return None
    rows = [r for r in t.strip().splitlines() if r.strip()]
    if len(rows) < 2:
        return None
    return {"見出し": rows[0], "最新": rows[-n:]}


def research():
    return {
        "日次研究(パラメータ進化)": _tail_csv("results/research_log.csv", 2),
        "週次ペア研究": _tail_csv("results/pair_scan_log.csv", 2),
        "ファンダ予測": _tail_csv("results/forecast_log.csv", 2),
        "実現損益(日次ログ)": _tail_csv("results/daily_log.csv", 2),
    }


# ---------------------------------------------------------------- 自己診断
def selftest():
    checks = []
    for path in ("state.json", "state_intraday.json", "state_intraday15.json",
                 "results/intraday_status.json",
                 "results/intraday15_status.json",
                 "results/intraday_latest.txt", "results/research_log.csv",
                 "results/pair_scan_log.csv"):
        try:
            ok = _get(path) is not None
            checks.append({"経路": path, "結果": "OK" if ok else "404（未生成）"})
        except FetchError as e:
            checks.append({"経路": path, "結果": f"NG {e}"})
    s = status()
    checks.append({"経路": "鮮度判定",
                   "結果": "／".join(
                       f"{k}:{v.get('鮮度', {}).get('鮮度', '—') if isinstance(v.get('鮮度'), dict) else '—'}"
                       for k, v in s["システム"].items())})
    c = card()
    checks.append({"経路": "本日の指示書",
                   "結果": f"{c.get('段階') or '未生成'}"
                           f"／pips欄 {'あり' if c.get('pips欄') else 'なし'}"})
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--card", action="store_true")
    ap.add_argument("--stage", choices=["確定", "速報2", "速報1"])
    ap.add_argument("--day")
    ap.add_argument("--intraday", action="store_true")
    ap.add_argument("--intraday-card", choices=["2H", "15m"])
    ap.add_argument("--since-min", type=int, default=70)
    ap.add_argument("--research", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    def dump(o):
        print(json.dumps(o, ensure_ascii=False, indent=2))

    try:
        if a.selftest:
            dump(selftest())
        elif a.status:
            dump(status())
        elif a.card:
            c = card(a.stage, a.day)
            if c.get("本文"):
                print(c["本文"])
                print("\n" + json.dumps(
                    {k: v for k, v in c.items() if k != "本文"},
                    ensure_ascii=False))
            else:
                dump(c)
        elif a.intraday_card:
            c = intraday_card(a.intraday_card)
            print(c["本文"] or json.dumps(c, ensure_ascii=False, indent=2))
        elif a.intraday:
            dump(intraday_actions(a.since_min))
        elif a.research:
            dump(research())
        else:
            ap.print_help()
    except FetchError as e:
        print(f"【エラー】{e}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
