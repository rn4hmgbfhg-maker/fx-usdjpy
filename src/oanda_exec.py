# -*- coding: utf-8 -*-
"""
OANDA証券（NYサーバー・プロコース）REST API v20 による自動発注（注文係）。

2026-10-28 落合さん指示「Claude が判定から発注まで一貫して自動化」により新設。
JFX・外為どっとコムは自動売買禁止のため、自動発注は OANDA 証券の口座だけで行う。

仕組み（照合方式）:
  各エンジンのモデル状態（state.json / state_intraday.json / state_intraday15.json）を
  「あるべき建玉」とし、口座の建玉（システムごとのタグ付き）と突き合わせて差を埋める。
    ・エンジンが建てた → 損切り（と利確）付きの成行で新規
    ・エンジンが決済した／向きを変えた → その建玉を決済
    ・逆指値・利確が動いた → 口座側の決済注文を変更
  同じ建玉（システム＋建玉日時＋向き）は一度しか建てない。口座側の損切り・利確で
  先に決済された場合は、エンジンが次の判定を出すまで建て直さない。

モード（auto_order.json）:
  off  何もしない
  dry  注文を出さない試運転。仮想の口座（results/auto_order/paper_book.json）で記録だけ
  live 本番発注。環境変数 OANDA_API_TOKEN・OANDA_ACCOUNT_ID が必要（GitHub Secrets）

安全装置: 損切り無しの新規はしない／1回・合計の数量上限／1日の損失上限／
  資金が最高値から20%減で新規停止／API残高25万円の手前で警告／二重注文の防止／
  稼働開始時に既に持っている建玉には途中参加しない／古いシグナルでは建てない。

取引量（ゴールド会員の条件＝前月の取引量50万米ドル以上）を自前の台帳
（results/auto_order/volume_{モード}.csv）に積み上げ、月末の見込みを status に出す。
OANDA の履歴は直近2週間しか取れないため、台帳が正。

使い方:
  python3 src/oanda_exec.py            # auto_order.json のモードで1回照合
  python3 src/oanda_exec.py --mode dry # モードを一時的に上書き
"""
import argparse
import calendar
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "auto_order.json")
OUT_DIR = os.path.join(BASE_DIR, "results", "auto_order")
JST = timezone(timedelta(hours=9))

INSTRUMENT = "USD_JPY"
PIP = 0.01
PRICE_TOL = 0.0005          # 逆指値・利確をこれ未満のずれなら変更しない
GOLD_USD = 500000

# システム名 → (状態ファイル, 状態の取り出し方, 口座側タグ, 足の種類)
SYSTEMS = {
    "長期トレンド": ("state.json", lambda d: d.get("長期トレンド"), "fxauto_long", "daily"),
    "短期スイング": ("state.json", lambda d: d.get("短期スイング"), "fxauto_swing", "daily"),
    "デイトレ2H": ("state_intraday.json", lambda d: d.get("state"), "fxauto_dt2h", "hourly"),
    "デイトレ15分": ("state_intraday15.json", lambda d: d.get("state"), "fxauto_dt15", "hourly"),
}
TAG_TO_SYSTEM = {v[2]: k for k, v in SYSTEMS.items()}

LOG_FIELDS = ["時刻", "モード", "システム", "操作", "数量", "価格", "逆指値", "利確", "結果", "詳細"]
VOL_FIELDS = ["時刻", "システム", "種別", "数量", "価格", "取引量USD"]


# ---------------------------------------------------------------- 共通

def now_jst():
    return datetime.now(JST)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_csv(path, fields, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def fmt_price(p):
    return f"{float(p):.3f}"


def usd_notional(units, price):
    """取引量の米ドル換算。USD_JPY は数量そのものが米ドル。"""
    return abs(float(units)) if INSTRUMENT.startswith("USD_") else abs(float(units)) * float(price)


def entry_key(system, st):
    """エンジンの建玉を一意に表すキー（システム|建玉日時|向き）。ノーポジなら None。"""
    if not st or int(st.get("position") or 0) == 0:
        return None
    return f"{system}|{st.get('entry_date')}|{int(st['position'])}"


def key_comment(st):
    """口座側の建玉に付けるコメント（英数字のみ）。キーの復元に使う。"""
    return f"{st.get('entry_date')}|{int(st['position'])}"


def is_fresh(kind, entry_date, cfg, now):
    """シグナルが新しいか。古いシグナルでは新規に建てない。"""
    fresh = cfg.get("鮮度", {})
    try:
        if kind == "daily":
            d = datetime.strptime(str(entry_date)[:10], "%Y-%m-%d").date()
            return (now.date() - d).days <= int(fresh.get("日足_日", 4))
        t = datetime.strptime(str(entry_date)[:16], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
        return now - t <= timedelta(hours=float(fresh.get("時間足_時間", 6)))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 口座（本番）

class BrokerError(Exception):
    pass


class OandaBroker:
    """OANDA v20 REST の薄い包み。注文系（POST/PUT）は再送しない（二重注文防止）。"""
    URLS = {"live": "https://api-fxtrade.oanda.com",
            "practice": "https://api-fxpractice.oanda.com"}

    def __init__(self, token, account_id, env="live", cfg=None, session=None):
        if session is None:
            import requests
            session = requests.Session()
        self.s = session
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json",
                               "Accept-Datetime-Format": "RFC3339"})
        self.base = f"{self.URLS[env]}/v3/accounts/{account_id}"
        self.cfg = cfg or {}

    def _req(self, method, path, body=None, retries=0):
        last = None
        for _ in range(retries + 1):
            try:
                r = self.s.request(method, self.base + path, json=body, timeout=20)
            except Exception as e:  # 通信エラー
                last = f"{type(e).__name__}: {e}"
                continue
            if r.status_code >= 400:
                raise BrokerError(f"HTTP {r.status_code} {method} {path}: {r.text[:400]}")
            return r.json()
        raise BrokerError(f"通信失敗 {method} {path}: {last}")

    def summary(self):
        a = self._req("GET", "/summary", retries=2)["account"]
        return {"balance": float(a["balance"]), "nav": float(a["NAV"]),
                "unrealized": float(a.get("unrealizedPL", 0)),
                "hedging": bool(a.get("hedgingEnabled", False))}

    def price(self):
        p = self._req("GET", f"/pricing?instruments={INSTRUMENT}", retries=2)["prices"][0]
        bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
        return (bid + ask) / 2

    @staticmethod
    def _dep(o):
        if not o:
            return None
        return {"id": o.get("id"), "price": float(o["price"]), "gtd": o.get("gtdTime")}

    def open_trades(self):
        out = []
        for t in self._req("GET", "/openTrades", retries=2).get("trades", []):
            if t.get("instrument") != INSTRUMENT:
                continue
            ce = t.get("clientExtensions") or {}
            out.append({"id": t["id"], "tag": ce.get("tag", ""), "comment": ce.get("comment", ""),
                        "units": int(float(t["currentUnits"])), "price": float(t["price"]),
                        "sl": self._dep(t.get("stopLossOrder")),
                        "tp": self._dep(t.get("takeProfitOrder"))})
        return out

    def _dep_body(self, price):
        body = {"price": fmt_price(price)}
        if self.cfg.get("決済注文の有効期限") == "GTD":
            days = int(self.cfg.get("GTDの日数", 99))
            gtd = datetime.now(timezone.utc) + timedelta(days=days)
            body.update({"timeInForce": "GTD", "gtdTime": gtd.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")})
        return body

    def market_open(self, units, sl, tp, tag, comment, hedging):
        order = {"type": "MARKET", "instrument": INSTRUMENT, "units": str(int(units)),
                 "positionFill": "OPEN_ONLY" if hedging else "DEFAULT",
                 "stopLossOnFill": self._dep_body(sl),
                 "tradeClientExtensions": {"tag": tag, "comment": comment}}
        tif = self.cfg.get("成行のtimeInForce")
        if tif:
            order["timeInForce"] = tif
        if tp:
            order["takeProfitOnFill"] = self._dep_body(tp)
        r = self._req("POST", "/orders", {"order": order})
        fill = r.get("orderFillTransaction")
        if not fill or not fill.get("tradeOpened"):
            reason = (r.get("orderCancelTransaction") or {}).get("reason", "約定なし")
            raise BrokerError(f"新規が約定しなかった: {reason}")
        return {"trade_id": fill["tradeOpened"]["tradeID"], "price": float(fill["price"]),
                "units": int(float(fill["tradeOpened"]["units"]))}

    def close_trade(self, trade_id):
        r = self._req("PUT", f"/trades/{trade_id}/close", {"units": "ALL"})
        fill = r.get("orderFillTransaction")
        if not fill:
            reason = (r.get("orderCancelTransaction") or {}).get("reason", "約定なし")
            raise BrokerError(f"決済が約定しなかった: {reason}")
        return {"price": float(fill["price"]), "pl": float(fill.get("pl", 0)),
                "units": abs(int(float(fill.get("units", 0))))}

    def set_dependents(self, trade_id, sl=None, tp=None):
        body = {}
        if sl is not None:
            body["stopLoss"] = self._dep_body(sl)
        if tp is not None:
            body["takeProfit"] = self._dep_body(tp)
        self._req("PUT", f"/trades/{trade_id}/orders", body)

    def trade_detail(self, trade_id):
        t = self._req("GET", f"/trades/{trade_id}", retries=2)["trade"]
        return {"state": t.get("state"), "units": abs(int(float(t.get("initialUnits", 0)))),
                "close_price": float(t["averageClosePrice"]) if t.get("averageClosePrice") else None,
                "pl": float(t.get("realizedPL", 0)), "close_time": t.get("closeTime")}


# ---------------------------------------------------------------- 口座（試運転用の仮想口座）

class PaperBroker:
    """注文を出さない試運転用。現在値で約定したことにして帳簿に付ける。"""

    def __init__(self, path, price, start_balance=1000000.0):
        self.path = path
        self.px = float(price)
        self.book = load_json(path, None) or {"balance": start_balance, "next_id": 1, "trades": {}, "closed": {}}
        self._settle_dependents()

    def _save(self):
        save_json(self.path, self.book)

    def _pl(self, t, px):
        return (px - t["price"]) * t["units"]

    def _settle_dependents(self):
        """前回から今回までに現在値が逆指値・利確を越えていれば、その値で決済したことにする。"""
        for tid, t in list(self.book["trades"].items()):
            long_ = t["units"] > 0
            hit = None
            if t.get("sl") and ((long_ and self.px <= t["sl"]) or (not long_ and self.px >= t["sl"])):
                hit = t["sl"]
            elif t.get("tp") and ((long_ and self.px >= t["tp"]) or (not long_ and self.px <= t["tp"])):
                hit = t["tp"]
            if hit is not None:
                self._close(tid, hit)
        self._save()

    def _close(self, tid, px):
        t = self.book["trades"].pop(tid)
        pl = round(self._pl(t, px))
        self.book["balance"] += pl
        self.book["closed"][tid] = {"units": abs(t["units"]), "close_price": px, "pl": pl,
                                    "close_time": now_jst().isoformat(timespec="seconds")}
        return {"price": px, "pl": pl, "units": abs(t["units"])}

    def summary(self):
        unreal = sum(self._pl(t, self.px) for t in self.book["trades"].values())
        return {"balance": self.book["balance"], "nav": self.book["balance"] + unreal,
                "unrealized": unreal, "hedging": True}

    def price(self):
        return self.px

    def open_trades(self):
        return [{"id": tid, "tag": t["tag"], "comment": t["comment"], "units": t["units"],
                 "price": t["price"],
                 "sl": {"id": None, "price": t["sl"], "gtd": None} if t.get("sl") else None,
                 "tp": {"id": None, "price": t["tp"], "gtd": None} if t.get("tp") else None}
                for tid, t in self.book["trades"].items()]

    def market_open(self, units, sl, tp, tag, comment, hedging):
        tid = f"P{self.book['next_id']}"
        self.book["next_id"] += 1
        self.book["trades"][tid] = {"tag": tag, "comment": comment, "units": int(units),
                                    "price": self.px, "sl": sl, "tp": tp}
        self._save()
        return {"trade_id": tid, "price": self.px, "units": int(units)}

    def close_trade(self, trade_id):
        r = self._close(trade_id, self.px)
        self._save()
        return r

    def set_dependents(self, trade_id, sl=None, tp=None):
        t = self.book["trades"][trade_id]
        if sl is not None:
            t["sl"] = sl
        if tp is not None:
            t["tp"] = tp
        self._save()

    def trade_detail(self, trade_id):
        c = self.book["closed"].get(trade_id)
        if not c:
            return {"state": "OPEN" if trade_id in self.book["trades"] else "UNKNOWN", "units": 0,
                    "close_price": None, "pl": 0, "close_time": None}
        return {"state": "CLOSED", **c}


# ---------------------------------------------------------------- 照合

class Executor:
    def __init__(self, cfg, mode, broker, out_dir=OUT_DIR, base_dir=BASE_DIR, now=None, notify=None):
        self.cfg, self.mode, self.b = cfg, mode, broker
        self.out_dir, self.base_dir = out_dir, base_dir
        self.now = now or now_jst()
        self.notify = notify
        self.ledger_path = os.path.join(out_dir, f"ledger_{mode}.json")
        self.led = load_json(self.ledger_path, None) or {
            "initialized": False, "keys": {}, "open_ids": {}, "peak_nav": None, "start_nav": None}
        self.actions, self.alerts = [], []

    # --- 記録
    def log(self, system, op, units="", price="", sl="", tp="", result="成功", detail=""):
        row = {"時刻": self.now.isoformat(timespec="seconds"), "モード": self.mode, "システム": system,
               "操作": op, "数量": units, "価格": price, "逆指値": sl, "利確": tp, "結果": result, "詳細": detail}
        append_csv(os.path.join(self.out_dir, f"log_{self.mode}.csv"), LOG_FIELDS, row)
        self.actions.append(row)

    def alert(self, msg):
        self.alerts.append(msg)

    def add_volume(self, system, kind, units, price):
        append_csv(os.path.join(self.out_dir, f"volume_{self.mode}.csv"), VOL_FIELDS, {
            "時刻": self.now.isoformat(timespec="seconds"), "システム": system, "種別": kind,
            "数量": abs(int(units)), "価格": fmt_price(price), "取引量USD": round(usd_notional(units, price))})

    def month_volume(self):
        path = os.path.join(self.out_dir, f"volume_{self.mode}.csv")
        ym = self.now.strftime("%Y-%m")
        total = 0.0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["時刻"][:7] == ym:
                        total += float(r["取引量USD"] or 0)
        days = calendar.monthrange(self.now.year, self.now.month)[1]
        elapsed = self.now.day - 1 + self.now.hour / 24 + 1e-9
        proj = total / elapsed * days if elapsed >= 1 else None
        return total, proj

    def today_realized(self):
        path = os.path.join(self.out_dir, f"log_{self.mode}.csv")
        d = self.now.strftime("%Y-%m-%d")
        pl = 0.0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r["時刻"][:10] == d and r["結果"] == "成功" and r["操作"] in ("決済", "口座側で決済"):
                        try:
                            pl += float(r["詳細"].split("損益")[1].split("円")[0])
                        except (IndexError, ValueError):
                            pass
        return pl

    # --- エンジン状態
    def desired(self):
        out = {}
        for name, (fname, pick, tag, kind) in SYSTEMS.items():
            if not self.cfg.get("システム", {}).get(name, {}).get("有効"):
                continue
            d = load_json(os.path.join(self.base_dir, fname), {}) or {}
            st = pick(d) or {}
            out[name] = {"st": st, "key": entry_key(name, st), "tag": tag, "kind": kind}
        return out

    def live_units(self, st, nav):
        u = float(st.get("units") or 0) * float(self.cfg.get("数量倍率", 1.0))
        if self.cfg.get("口座資金に比例"):
            model = float(load_json(os.path.join(self.base_dir, "config.json"), {}).get("口座資金_円", 1000000))
            u *= nav / model
        return min(int(round(u)), int(self.cfg.get("1回の上限通貨", 20000)))

    # --- 本体
    def run(self):
        summ = self.b.summary()
        px = self.b.price()
        trades = self.b.open_trades()
        want = self.desired()
        nav = summ["nav"]
        if self.led["start_nav"] is None:
            self.led["start_nav"] = nav
        self.led["peak_nav"] = max(self.led["peak_nav"] or nav, nav)

        # 1) 口座側の損切り・利確で消えた建玉を記録（取引量・損益）
        open_ids = {t["id"] for t in trades}
        for tid, info in list(self.led["open_ids"].items()):
            if tid in open_ids:
                continue
            det = self.b.trade_detail(tid)
            if det.get("state") == "CLOSED":
                cp = det.get("close_price") or px
                self.add_volume(info["system"], "決済", info["units"], cp)
                self.log(info["system"], "口座側で決済", info["units"], fmt_price(cp),
                         detail=f"損益{det.get('pl', 0):.0f}円")
            self.led["keys"].get(info["key"], {})["状態"] = "決済済"
            del self.led["open_ids"][tid]

        by_tag = {}
        for t in trades:
            by_tag.setdefault(t["tag"], []).append(t)

        first_run = not self.led["initialized"]
        opens = []
        for name, w in want.items():
            mine = by_tag.get(w["tag"], [])
            if len(mine) > 1:
                self.alert(f"{name}: 口座に同じシステムの建玉が{len(mine)}本あります。自動処理を止めました（手で確認してください）")
                continue
            t = mine[0] if mine else None
            tkey = f"{name}|{t['comment']}" if t else None

            # 2) エンジンが決済した／向きを変えた → 決済
            if t and tkey != w["key"]:
                try:
                    r = self.b.close_trade(t["id"])
                    self.add_volume(name, "決済", r["units"], r["price"])
                    self.log(name, "決済", r["units"], fmt_price(r["price"]), detail=f"損益{r['pl']:.0f}円")
                    self.led["keys"].get(tkey, {})["状態"] = "決済済"
                    self.led["open_ids"].pop(t["id"], None)
                except BrokerError as e:
                    self.log(name, "決済", abs(t["units"]), result="失敗", detail=str(e))
                    self.alert(f"{name}: 決済に失敗 {e}")
                    continue
                t = None

            if not w["key"]:
                continue
            st = w["st"]

            # 3) 同じ建玉を持っている → 逆指値・利確を合わせる
            if t:
                self.led["keys"].setdefault(w["key"], {"状態": "建玉中", "trade_id": t["id"]})
                self.led["open_ids"].setdefault(t["id"], {"key": w["key"], "system": name, "units": abs(t["units"])})
                self.sync_dependents(name, t, st)
                continue

            # 4) 持っていない → 新規の候補
            if w["key"] in self.led["keys"]:
                continue  # 建てたことがある（口座側で決済済み）か見送り済み
            if first_run and self.cfg.get("途中参加しない", True):
                self.led["keys"][w["key"]] = {"状態": "見送り", "理由": "稼働開始時に既に建玉中（途中参加しない）"}
                self.log(name, "見送り", st.get("units"), detail="稼働開始時に既に建玉中（途中参加しない）")
                continue
            if not is_fresh(w["kind"], st.get("entry_date"), self.cfg, self.now):
                self.led["keys"][w["key"]] = {"状態": "見送り", "理由": "シグナルが古い"}
                self.log(name, "見送り", st.get("units"), detail=f"シグナルが古い（建玉日時 {st.get('entry_date')}）")
                continue
            opens.append((name, w))

        if opens:
            self.open_new(opens, summ, px)
        self.check_account(summ)
        self.led["initialized"] = True
        save_json(self.ledger_path, self.led)
        self.write_status(summ, px)
        if self.notify and self.mode == "live" and (self.actions or self.alerts):
            self.notify(self.mail_subject(), self.mail_text())
        return self.actions, self.alerts

    def sync_dependents(self, name, t, st):
        sl, tp = st.get("stop"), st.get("tp")
        new_sl = new_tp = None
        if sl and (not t["sl"] or abs(t["sl"]["price"] - float(sl)) >= PRICE_TOL):
            new_sl = round(float(sl), 3)
        if tp and (not t["tp"] or abs(t["tp"]["price"] - float(tp)) >= PRICE_TOL):
            new_tp = round(float(tp), 3)
        if self.cfg.get("決済注文の有効期限") == "GTD":
            soon = datetime.now(timezone.utc) + timedelta(days=10)
            for kind, dep in (("sl", t["sl"]), ("tp", t["tp"])):
                g = dep and dep.get("gtd")
                if g and datetime.fromisoformat(g[:19]).replace(tzinfo=timezone.utc) < soon:
                    if kind == "sl" and new_sl is None:
                        new_sl = dep["price"]
                    if kind == "tp" and new_tp is None and tp:
                        new_tp = dep["price"]
        if new_sl is None and new_tp is None:
            return
        try:
            self.b.set_dependents(t["id"], sl=new_sl, tp=new_tp)
            self.log(name, "決済注文の変更", abs(t["units"]),
                     sl=fmt_price(new_sl) if new_sl else "", tp=fmt_price(new_tp) if new_tp else "",
                     detail=f"変更前 逆指値{t['sl']['price'] if t['sl'] else '—'}／利確{t['tp']['price'] if t['tp'] else '—'}")
        except BrokerError as e:
            self.log(name, "決済注文の変更", abs(t["units"]), result="失敗", detail=str(e))
            self.alert(f"{name}: 逆指値・利確の変更に失敗 {e}")

    def open_new(self, opens, summ, px):
        nav = summ["nav"]
        # 新規を止める条件（資金20%減・1日の損失上限）
        stop_rate = float(self.cfg.get("停止する資金減少率", 0.2))
        if self.led["peak_nav"] and nav <= self.led["peak_nav"] * (1 - stop_rate):
            for name, w in opens:
                self.led["keys"][w["key"]] = {"状態": "見送り", "理由": "資金減少で新規停止"}
                self.log(name, "見送り", w["st"].get("units"), detail=f"資金が最高値から{stop_rate:.0%}以上減ったため新規停止")
            self.alert(f"資金が最高値 {self.led['peak_nav']:,.0f}円 から{stop_rate:.0%}以上減りました（現在 {nav:,.0f}円）。新規を止めています")
            return
        day_loss = self.today_realized() + summ["unrealized"]
        lim = float(self.cfg.get("1日の損失上限_円", 20000))
        if day_loss <= -lim:
            for name, w in opens:
                self.led["keys"][w["key"]] = {"状態": "見送り", "理由": "1日の損失上限"}
                self.log(name, "見送り", w["st"].get("units"), detail=f"本日の損益 {day_loss:,.0f}円 が上限 -{lim:,.0f}円 に達した")
            self.alert(f"本日の損益が {day_loss:,.0f}円 で上限に達したため新規を止めました")
            return

        exposure = sum(abs(t["units"]) for t in self.b.open_trades())
        net = sum(t["units"] for t in self.b.open_trades())
        cap_total = int(self.cfg.get("合計建玉の上限通貨", 60000))
        min_dist = float(self.cfg.get("ストップの最小距離pips", 3)) * PIP
        for i, (name, w) in enumerate(opens):
            st = w["st"]
            side = int(st["position"])
            if i >= int(self.cfg.get("1回の新規上限", 4)):
                self.alert(f"{name}: 1回の新規上限に達したため次の回へ回しました")
                continue
            units = self.live_units(st, nav)
            sl = st.get("stop")
            tp = st.get("tp")
            reason = None
            if units < 1:
                reason = "数量が1通貨未満"
            elif not sl:
                reason = "逆指値が無い（損切り無しの建玉は禁止）"
            elif (side > 0 and float(sl) > px - min_dist) or (side < 0 and float(sl) < px + min_dist):
                reason = f"逆指値 {fmt_price(sl)} が現在値 {fmt_price(px)} に近すぎるか反対側"
            elif tp and ((side > 0 and float(tp) <= px) or (side < 0 and float(tp) >= px)):
                reason = f"利確 {fmt_price(tp)} が既に現在値 {fmt_price(px)} を越えている"
            elif exposure + units > cap_total:
                reason = f"合計建玉の上限 {cap_total:,}通貨 を超える"
            elif not summ["hedging"] and net * side < 0:
                reason = "口座が両建て不可で、反対向きの建玉がある"
            if reason:
                self.led["keys"][w["key"]] = {"状態": "見送り", "理由": reason}
                self.log(name, "見送り", units, fmt_price(px), fmt_price(sl) if sl else "", detail=reason)
                self.alert(f"{name}: 新規を見送り（{reason}）")
                continue
            try:
                r = self.b.market_open(side * units, round(float(sl), 3), round(float(tp), 3) if tp else None,
                                       w["tag"], key_comment(st), summ["hedging"])
            except BrokerError as e:
                self.led["keys"][w["key"]] = {"状態": "失敗", "理由": str(e)[:200]}
                self.log(name, "新規", side * units, fmt_price(px), fmt_price(sl), fmt_price(tp) if tp else "",
                         result="失敗", detail=str(e))
                self.alert(f"{name}: 新規に失敗 {e}")
                continue
            self.led["keys"][w["key"]] = {"状態": "建玉中", "trade_id": r["trade_id"]}
            self.led["open_ids"][r["trade_id"]] = {"key": w["key"], "system": name, "units": abs(r["units"])}
            self.add_volume(name, "新規", r["units"], r["price"])
            self.log(name, "新規", r["units"], fmt_price(r["price"]), fmt_price(sl), fmt_price(tp) if tp else "",
                     detail=f"{'買' if side > 0 else '売'} エンジン数量{st.get('units')}")
            exposure += abs(r["units"])
            net += r["units"]

    def check_account(self, summ):
        floor = float(self.cfg.get("API残高の下限_円", 250000))
        margin = float(self.cfg.get("残高警告の余裕_円", 30000))
        if self.mode == "live" and summ["balance"] <= floor + margin:
            self.alert(f"口座残高 {summ['balance']:,.0f}円。API の条件（25万円超）まであと {summ['balance'] - floor:,.0f}円 です。入金を検討してください")
        vol, proj = self.month_volume()
        if self.mode == "live" and proj is not None and self.now.day >= 15 and proj < GOLD_USD:
            self.alert(f"今月の取引量 {vol:,.0f}米ドル（月末見込み {proj:,.0f}）。ゴールド会員の条件 50万米ドル に届かない見込みです")

    def write_status(self, summ, px):
        vol, proj = self.month_volume()
        trades = self.b.open_trades()
        status = {
            "更新": self.now.isoformat(timespec="seconds"), "モード": self.mode,
            "現在値": round(px, 3), "残高_円": round(summ["balance"]), "有効証拠金_円": round(summ["nav"]),
            "評価損益_円": round(summ["unrealized"]), "両建て": summ["hedging"],
            "最高値_円": round(self.led["peak_nav"] or 0),
            "建玉": [{"システム": TAG_TO_SYSTEM.get(t["tag"], t["tag"] or "手動"), "数量": t["units"],
                     "建値": t["price"], "逆指値": t["sl"]["price"] if t["sl"] else None,
                     "利確": t["tp"]["price"] if t["tp"] else None} for t in trades],
            "今月の取引量_USD": round(vol), "月末見込み_USD": round(proj) if proj is not None else None,
            "ゴールド条件_USD": GOLD_USD,
            "今回の操作": self.actions, "警告": self.alerts,
        }
        save_json(os.path.join(self.out_dir, f"status_{self.mode}.json"), status)

    def mail_subject(self):
        ops = [a for a in self.actions if a["操作"] in ("新規", "決済", "口座側で決済")]
        head = "【自動発注】" + ("・".join(f"{a['システム']}{a['操作']}" for a in ops[:3]) if ops else "お知らせ")
        return head + ("　⚠要確認" if self.alerts else "")

    def mail_text(self):
        lines = [f"OANDA 自動発注（{self.mode}）{self.now.strftime('%Y-%m-%d %H:%M')} JST", ""]
        for a in self.actions:
            lines.append(f"・{a['システム']} {a['操作']} {a['数量']}通貨 価格{a['価格'] or '—'} "
                         f"逆指値{a['逆指値'] or '—'} 利確{a['利確'] or '—'} {a['結果']} {a['詳細']}")
        if self.alerts:
            lines += ["", "【要確認】"] + [f"・{m}" for m in self.alerts]
        vol, proj = self.month_volume()
        lines += ["", f"今月の取引量 {vol:,.0f}米ドル（月末見込み {proj:,.0f}／ゴールド条件 50万）" if proj
                  else f"今月の取引量 {vol:,.0f}米ドル"]
        return "\n".join(lines)


# ---------------------------------------------------------------- 入口

def reference_price(base_dir=BASE_DIR):
    """試運転で使う現在値。15分系の最新終値（15分ごとに更新）を使う。"""
    for fname in ("state_intraday15.json", "state_intraday.json"):
        d = load_json(os.path.join(base_dir, fname), {}) or {}
        if d.get("last_close"):
            return float(d["last_close"])
    raise SystemExit("現在値が取れません（state_intraday15.json に last_close が無い）")


def main(argv=None):
    ap = argparse.ArgumentParser(description="OANDA 自動発注（照合方式）")
    ap.add_argument("--mode", choices=["off", "dry", "live"], help="auto_order.json のモードを一時的に上書き")
    args = ap.parse_args(argv)
    cfg = load_json(CONFIG_PATH, {}) or {}
    mode = args.mode or cfg.get("モード", "off")
    if mode == "off":
        print("自動発注: モード off のため何もしません")
        return 0
    if mode == "live":
        token, acct = os.environ.get("OANDA_API_TOKEN"), os.environ.get("OANDA_ACCOUNT_ID")
        if not token or not acct:
            print("⚠自動発注: OANDA_API_TOKEN／OANDA_ACCOUNT_ID が無いため本番を止めました")
            return 1
        broker = OandaBroker(token, acct, cfg.get("環境", "live"), cfg)
        notify = None
        if cfg.get("通知", True):
            sys.path.insert(0, os.path.join(BASE_DIR, "src"))
            try:
                import mailer
                notify = lambda s, t: mailer.send_signal(s, t)  # noqa: E731
            except Exception as e:  # メールが使えなくても発注は続ける
                print(f"⚠メール送信の準備に失敗: {e}")
    else:
        broker = PaperBroker(os.path.join(OUT_DIR, "paper_book.json"), reference_price())
        notify = None
    try:
        actions, alerts = Executor(cfg, mode, broker, notify=notify).run()
    except BrokerError as e:
        print(f"⚠自動発注: 口座との通信に失敗 {e}")
        return 1
    for a in actions:
        print(f"[{mode}] {a['システム']} {a['操作']} {a['数量']} {a['価格']} {a['結果']} {a['詳細']}")
    for m in alerts:
        print(f"⚠ {m}")
    if not actions and not alerts:
        print(f"[{mode}] 変更なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
