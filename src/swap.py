# -*- coding: utf-8 -*-
"""スワップポイント（金利差損益）の計算 — 3システム共通モジュール

2026-08-13 ユーザー指示「スワップポイントの損益も現在のポジション損益に
反映してほしい」に対応して新設。従来はレート差のみで損益を出しており、
建玉を持ち越すほど実口座との乖離が広がっていた（設計書の留意点にあった
「スワップはモデル化しない」を撤回する）。

計算ルール:
  ・付与はNY17:00のロールオーバー時点。JSTでは米夏時間=翌06:00・冬時間=翌07:00。
  ・ロールオーバーを跨いだ回数だけ付与する（保有時間ではなく跨いだ回数）。
  ・水曜NY17:00のロールオーバー（JST木曜早朝）は3日分（週末の受渡分）。
  ・金曜NY17:00は週の引け＝付与なし。日曜NY17:00の週明けオープンも付与なし。
    → JST基準で「火・水・木・金」曜日の早朝ロールオーバーのみ有効、木曜が3倍。

レートは config.json の「スワップポイント」で管理する（1万通貨・1日あたりの円）。
JFXの公表値は日々変わるため、正確な値はユーザーがMATRIX TRADERの
「スワップポイント」画面を見て次のコマンドで更新する:

    python3 src/swap.py --set 買=125 売=-165 出典="JFX公表 2026-08-13"

「推定」フラグが立っている間は、ボード・指示書に⚠推定値である旨を必ず出す。
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 初期値は金利差からの理論推定（米2年3.73% − 日本短期0.75% ≒ 2.98%、
# 1万通貨=約159.4万円 → 約130円/日）にブローカー分の目減りを見込んだ概算。
# 実際のJFX公表値で必ず上書きすること。
DEFAULT = {
    "有効": True,
    "円_1万通貨_1日": {"買": 125, "売": -165},
    "推定": True,
    "出典": "理論推定（米2年3.73%−日本短期0.75%）※JFX公表値で要上書き",
    "更新日": "2026-08-13",
    # 2026-08-13 ユーザー指示「今後も現在の金利と、現在のJFXの金利での推定値を
    # 基準」に対応。JFX公表値を採った時点の金利・ドル円を「基準」として保存し、
    # 以後は現在の金利差とドル円水準の変化で日々自動補正する（下の adjusted()）。
    # スワップは概ね「金利差×円建て想定元本」に比例するため、
    #   補正後 = 公表値 × (現在の金利差 / 基準の金利差) × (現在のドル円 / 基準のドル円)
    # とする。金利差が基準から大きく離れたらJFX公表値の再確認を促す。
    "基準": {"米2年金利": 3.707, "日本短期金利": 0.75, "ドル円": 159.423,
             "日付": "2026-08-12"},
    "自動補正": True,
    "再確認閾値_乖離率": 0.15,
}

UNIT = 10000.0      # 公表値の基準数量（1万通貨）
MACRO_PATH = os.path.join(BASE_DIR, "data", "macro_daily.csv")
PX_PATH = os.path.join(BASE_DIR, "data", "usdjpy_daily.csv")


def load(cfg=None):
    """config.jsonの「スワップポイント」を既定値で埋めて返す。"""
    if cfg is None:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    sw = dict(DEFAULT)
    sw.update(cfg.get("スワップポイント") or {})
    rate = dict(DEFAULT["円_1万通貨_1日"])
    rate.update(sw.get("円_1万通貨_1日") or {})
    sw["円_1万通貨_1日"] = rate
    return sw


def _us_dst(d):
    """米国夏時間か（3月第2日曜〜11月第1日曜）。"""
    def nth_sunday(year, month, n):
        first = date(year, month, 1)
        first_sun = first + timedelta(days=(6 - first.weekday()) % 7)
        return first_sun + timedelta(days=7 * (n - 1))
    return nth_sunday(d.year, 3, 2) <= d < nth_sunday(d.year, 11, 1)


def rollover_hour_jst(d):
    """その日のロールオーバー時刻（JSTの時）。夏時間6時・冬時間7時。"""
    return 6 if _us_dst(d) else 7


def parse_dt(v):
    """建玉日時を datetime へ。日付のみ（日足システム）はその日の
    ロールオーバー時刻とみなす＝当日朝の付与は数えない保守側の扱い。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
                "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return datetime.combine(d, datetime.min.time()).replace(
        hour=rollover_hour_jst(d))


def swap_days(entry, now=None):
    """entry〜nowの間に跨いだロールオーバーの合計付与日数（木曜JSTは3日）。"""
    entry = parse_dt(entry)
    if entry is None:
        return 0
    now = now or datetime.now()
    if now <= entry:
        return 0
    days = 0
    d = entry.date()
    while d <= now.date():
        roll = datetime.combine(d, datetime.min.time()).replace(
            hour=rollover_hour_jst(d))
        # JST火〜金の早朝のみ有効（月=週明けオープン・土日=市場休場）
        if entry < roll <= now and d.weekday() in (1, 2, 3, 4):
            days += 3 if d.weekday() == 3 else 1   # 木曜JST=水曜NY=3日分
        d += timedelta(days=1)
    return days


def _last_num(path, col):
    """CSVの最終有効値を返す（pandas不要・失敗時 None）。"""
    try:
        with open(path, encoding="utf-8") as f:
            rows = [ln.rstrip("\n").split(",") for ln in f if ln.strip()]
        idx = rows[0].index(col)
        for r in reversed(rows[1:]):
            if len(r) > idx and r[idx] not in ("", "NaN", "nan"):
                return float(r[idx])
    except Exception:
        pass
    return None


def adjusted(sw=None):
    """現在の金利差・ドル円で補正したレートと補正情報を返す。

    戻り値 (rate_dict, info)。info は補正係数・現在の金利差・乖離率など。
    データが無い/自動補正オフのときは公表値をそのまま返す。
    """
    sw = sw or load()
    base_rate = dict(sw["円_1万通貨_1日"])
    info = {"補正": False, "係数": 1.0, "要再確認": False}
    b = sw.get("基準") or {}
    if not sw.get("自動補正", True) or not b:
        return base_rate, info
    us = _last_num(MACRO_PATH, "米2年金利")
    px = _last_num(PX_PATH, "Close")
    jp = float(b.get("日本短期金利", 0.75))
    try:
        d0 = float(b["米2年金利"]) - jp
        px0 = float(b["ドル円"])
    except (KeyError, TypeError, ValueError):
        return base_rate, info
    if us is None or px is None or d0 <= 0 or px0 <= 0:
        return base_rate, info
    d1 = us - jp
    if d1 <= 0:                      # 金利差消滅は補正の前提が崩れるので補正しない
        info["要再確認"] = True
        return base_rate, info
    k = (d1 / d0) * (px / px0)
    info.update({"補正": True, "係数": k, "現在金利差": d1, "基準金利差": d0,
                 "米2年金利": us, "ドル円": px,
                 "要再確認": abs(k - 1.0) >= float(
                     sw.get("再確認閾値_乖離率", 0.15))})
    return {k2: round(v * k, 1) for k2, v in base_rate.items()}, info


def daily_yen(position, units, sw=None):
    """1日あたりのスワップ（円）。買い持ちは受取・売り持ちは支払いが通常。"""
    sw = sw or load()
    if not sw.get("有効", True) or not position or not units:
        return 0.0
    rate = adjusted(sw)[0]["買" if int(position) > 0 else "売"]
    return float(rate) * float(units) / UNIT


def accrued(position, units, entry, now=None, sw=None):
    """建玉から現時点までに発生したスワップ（円）と付与日数を返す。"""
    sw = sw or load()
    if not sw.get("有効", True) or not position or not units:
        return 0.0, 0
    days = swap_days(entry, now)
    return daily_yen(position, units, sw) * days, days


def is_estimate(sw=None):
    sw = sw or load()
    return bool(sw.get("有効", True) and sw.get("推定"))


def note(sw=None):
    """表示用の一行注記。"""
    sw = sw or load()
    if not sw.get("有効", True):
        return "スワップ計上なし（config「スワップポイント」で無効）"
    r, info = adjusted(sw)
    s = (f"スワップ 買{float(r['買']):+,.0f}円／売{float(r['売']):+,.0f}円"
         f"（1万通貨・1日）")
    if sw.get("推定"):
        return s + "　⚠推定値（JFX公表値で要更新）"
    s += f"　出典: {sw.get('出典', '')}"
    if info.get("補正"):
        b = sw.get("基準", {})
        s += (f"／現在の金利差{info['現在金利差']:.2f}%"
              f"（米2年{info['米2年金利']:.2f}%−日本{b.get('日本短期金利')}%）"
              f"とドル円{info['ドル円']:.2f}円で自動補正 ×{info['係数']:.2f}")
    if info.get("要再確認"):
        s += "　⚠基準から乖離（MATRIX TRADERのスワップ一覧で再確認推奨）"
    return s


def ensure_columns(path, header):
    """既存CSVに新しい列（スワップ円など）を後付けする一度きりの移行。
    旧行は空欄のままにし、既存の集計を書き換えない。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        rows = [ln.rstrip("\n") for ln in f if ln.strip()]
    if not rows:
        return
    old = rows[0].split(",")
    if old == header:
        return
    add = [c for c in header if c not in old]
    if not add:
        return
    idx = {c: i for i, c in enumerate(old)}
    out = [",".join(header)]
    for ln in rows[1:]:
        cells = ln.split(",")
        out.append(",".join(
            cells[idx[c]] if c in idx and idx[c] < len(cells) else ""
            for c in header))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main():
    ap = argparse.ArgumentParser(description="スワップポイント設定の確認・更新")
    ap.add_argument("--set", nargs="*", metavar="キー=値",
                    help='例: --set 買=125 売=-165 出典="JFX公表 2026-08-13"')
    ap.add_argument("--off", action="store_true", help="スワップ計上を無効化")
    ap.add_argument("--on", action="store_true", help="スワップ計上を有効化")
    args = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    sw = load(cfg)
    changed = False

    for kv in args.set or []:
        k, _, v = kv.partition("=")
        k = k.strip()
        if k in ("買", "売"):
            sw["円_1万通貨_1日"][k] = float(v)
            sw["推定"] = False
            sw["更新日"] = date.today().isoformat()
            changed = True
        elif k in ("出典", "更新日"):
            sw[k] = v
            changed = True
        else:
            raise SystemExit(f"未知のキー: {k}（買 / 売 / 出典 / 更新日）")
    if args.off:
        sw["有効"] = False
        changed = True
    if args.on:
        sw["有効"] = True
        changed = True

    if changed:
        cfg["スワップポイント"] = sw
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("config.json を更新しました。")
    print(json.dumps(sw, ensure_ascii=False, indent=2))
    print(note(sw))


if __name__ == "__main__":
    main()
