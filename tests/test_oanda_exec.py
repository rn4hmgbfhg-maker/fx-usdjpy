# -*- coding: utf-8 -*-
"""src/oanda_exec.py の照合ロジックの試験（仮想口座で通しで動かす）。
python3 -m unittest tests/test_oanda_exec.py
"""
import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import oanda_exec as ox  # noqa: E402

CFG = {
    "モード": "dry",
    "システム": {"長期トレンド": {"有効": True}, "短期スイング": {"有効": True},
               "デイトレ2H": {"有効": True}, "デイトレ15分": {"有効": False}},
    "数量倍率": 1.0, "1回の上限通貨": 20000, "合計建玉の上限通貨": 60000,
    "1日の損失上限_円": 20000, "停止する資金減少率": 0.2, "ストップの最小距離pips": 3,
    "1回の新規上限": 4, "途中参加しない": True, "鮮度": {"日足_日": 4, "時間足_時間": 6},
}
FLAT = {"position": 0, "entry": None, "stop": None, "units": 0, "best": None, "entry_date": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "out")
        self.now = datetime(2026, 11, 10, 8, 10, tzinfo=ox.JST)
        self.daily = {"長期トレンド": dict(FLAT), "短期スイング": dict(FLAT)}
        self.dt2h = dict(FLAT, tp=None)
        self.write()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self):
        with open(os.path.join(self.dir, "state.json"), "w") as f:
            json.dump(self.daily, f)
        with open(os.path.join(self.dir, "state_intraday.json"), "w") as f:
            json.dump({"state": self.dt2h}, f)

    def run_once(self, px, cfg=None):
        b = ox.PaperBroker(os.path.join(self.out, "paper_book.json"), px)
        ex = ox.Executor(cfg or CFG, "dry", b, out_dir=self.out, base_dir=self.dir, now=self.now)
        acts, alerts = ex.run()
        return b, acts, alerts

    def volume(self):
        p = os.path.join(self.out, "volume_dry.csv")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return list(csv.DictReader(f))


class TestExecutor(Base):
    def test_first_run_does_not_join_existing_position(self):
        self.daily["長期トレンド"] = {"position": -1, "entry": 158.1, "stop": 160.278, "units": 2000,
                                 "best": 156.6, "entry_date": "2026-11-09"}
        self.write()
        b, acts, _ = self.run_once(157.9)
        self.assertEqual(b.open_trades(), [])
        self.assertEqual(acts[0]["操作"], "見送り")
        # 2回目も建てない（同じ建玉キーは見送り済み）
        b, acts, _ = self.run_once(157.9)
        self.assertEqual(b.open_trades(), [])

    def test_new_entry_opens_with_stop_and_tag(self):
        self.run_once(157.9)  # 稼働開始（ノーポジ）
        self.daily["短期スイング"] = {"position": 1, "entry": 157.9, "stop": 156.5, "units": 3000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        self.write()
        b, acts, alerts = self.run_once(157.95)
        t = b.open_trades()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["units"], 3000)
        self.assertEqual(t[0]["tag"], "fxauto_swing")
        self.assertAlmostEqual(t[0]["sl"]["price"], 156.5)
        self.assertEqual(alerts, [])
        v = self.volume()
        self.assertEqual((v[0]["種別"], v[0]["取引量USD"]), ("新規", "3000"))
        # 同じ回をもう一度回しても二重に建てない
        b, acts, _ = self.run_once(157.95)
        self.assertEqual(len(b.open_trades()), 1)
        self.assertEqual(acts, [])

    def test_stop_moves_and_exit(self):
        self.run_once(157.9)
        self.daily["長期トレンド"] = {"position": -1, "entry": 157.9, "stop": 160.0, "units": 2000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        self.write()
        self.run_once(157.9)
        self.daily["長期トレンド"]["stop"] = 159.5
        self.write()
        b, acts, _ = self.run_once(157.5)
        self.assertEqual(acts[0]["操作"], "決済注文の変更")
        self.assertAlmostEqual(b.open_trades()[0]["sl"]["price"], 159.5)
        # エンジンが決済 → 口座も決済、取引量は新規＋決済
        self.daily["長期トレンド"] = dict(FLAT)
        self.write()
        b, acts, _ = self.run_once(157.0)
        self.assertEqual(b.open_trades(), [])
        self.assertEqual(acts[0]["操作"], "決済")
        self.assertIn("損益1800円", acts[0]["詳細"])
        self.assertEqual(sum(int(r["取引量USD"]) for r in self.volume()), 4000)

    def test_broker_stop_hit_is_not_reopened(self):
        self.run_once(157.9)
        self.dt2h = {"position": 1, "entry": 157.9, "stop": 157.6, "tp": 158.5, "units": 4000,
                     "best": 157.9, "entry_date": "2026-11-10 07:00"}
        self.write()
        self.run_once(157.9)
        # 価格が逆指値を割る → 口座側で決済。エンジンはまだ建玉中のまま
        b, acts, _ = self.run_once(157.5)
        self.assertEqual(b.open_trades(), [])
        self.assertEqual(acts[0]["操作"], "口座側で決済")
        b, acts, _ = self.run_once(157.95)
        self.assertEqual(b.open_trades(), [], "口座側で決済された建玉を建て直してはいけない")

    def test_reversal_closes_then_opens(self):
        self.run_once(157.9)
        self.dt2h = {"position": 1, "entry": 157.9, "stop": 157.0, "tp": None, "units": 4000,
                     "best": 157.9, "entry_date": "2026-11-10 07:00"}
        self.write()
        self.run_once(157.9)
        self.dt2h = {"position": -1, "entry": 157.8, "stop": 158.8, "tp": 156.0, "units": 5000,
                     "best": 157.8, "entry_date": "2026-11-10 08:00"}
        self.write()
        b, acts, _ = self.run_once(157.8)
        self.assertEqual([a["操作"] for a in acts], ["決済", "新規"])
        t = b.open_trades()
        self.assertEqual(t[0]["units"], -5000)
        self.assertAlmostEqual(t[0]["tp"]["price"], 156.0)

    def test_guards(self):
        self.run_once(157.9)
        # 逆指値が現在値の反対側 → 見送り
        self.daily["長期トレンド"] = {"position": 1, "entry": 157.9, "stop": 158.2, "units": 2000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        # 古いシグナル → 見送り
        self.daily["短期スイング"] = {"position": 1, "entry": 157.9, "stop": 156.0, "units": 2000,
                                 "best": 157.9, "entry_date": "2026-11-01"}
        self.write()
        b, acts, alerts = self.run_once(157.9)
        self.assertEqual(b.open_trades(), [])
        details = " ".join(a["詳細"] for a in acts)
        self.assertIn("反対側", details)
        self.assertIn("古い", details)

    def test_unit_caps(self):
        cfg = dict(CFG, **{"1回の上限通貨": 2500, "合計建玉の上限通貨": 4000})
        self.run_once(157.9, cfg)
        self.daily["長期トレンド"] = {"position": 1, "entry": 157.9, "stop": 156.0, "units": 3000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        self.daily["短期スイング"] = {"position": 1, "entry": 157.9, "stop": 156.5, "units": 3000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        self.write()
        b, acts, alerts = self.run_once(157.9, cfg)
        t = b.open_trades()
        self.assertEqual([x["units"] for x in t], [2500])  # 1本目は上限2500に切り詰め、2本目は合計上限で見送り
        self.assertTrue(any("合計建玉の上限" in m for m in alerts))

    def test_drawdown_stop(self):
        self.run_once(157.9)
        ex_led = os.path.join(self.out, "ledger_dry.json")
        with open(ex_led) as f:
            led = json.load(f)
        led["peak_nav"] = 1300000  # 仮想口座100万円 → 最高値から23%減
        with open(ex_led, "w") as f:
            json.dump(led, f)
        self.daily["長期トレンド"] = {"position": 1, "entry": 157.9, "stop": 156.0, "units": 2000,
                                 "best": 157.9, "entry_date": "2026-11-09"}
        self.write()
        b, acts, alerts = self.run_once(157.9)
        self.assertEqual(b.open_trades(), [])
        self.assertTrue(any("新規を止めています" in m for m in alerts))


class TestOandaBroker(unittest.TestCase):
    """本番口座クラスが組み立てる注文の中身（通信はしない）。"""

    class FakeResp:
        def __init__(self, data, code=201):
            self._d, self.status_code, self.text = data, code, json.dumps(data)

        def json(self):
            return self._d

    class FakeSession:
        def __init__(self, resp):
            self.headers, self.calls, self.resp = {}, [], resp

        def request(self, method, url, json=None, timeout=None):
            self.calls.append((method, url, json))
            return self.resp

    def test_market_order_body(self):
        fill = {"orderFillTransaction": {"price": "157.912",
                                         "tradeOpened": {"tradeID": "123", "units": "-2000"}}}
        s = self.FakeSession(self.FakeResp(fill))
        b = ox.OandaBroker("tok", "001-009-1-001", "live", {"決済注文の有効期限": "GTC"}, session=s)
        r = b.market_open(-2000, 160.278, None, "fxauto_long", "2026-11-09|-1", hedging=True)
        self.assertEqual(r, {"trade_id": "123", "price": 157.912, "units": -2000})
        method, url, body = s.calls[0]
        self.assertEqual((method, url), ("POST", "https://api-fxtrade.oanda.com/v3/accounts/001-009-1-001/orders"))
        o = body["order"]
        self.assertEqual((o["type"], o["units"], o["positionFill"]), ("MARKET", "-2000", "OPEN_ONLY"))
        self.assertEqual(o["stopLossOnFill"], {"price": "160.278"})
        self.assertNotIn("takeProfitOnFill", o)
        self.assertNotIn("timeInForce", o)
        self.assertEqual(o["tradeClientExtensions"]["tag"], "fxauto_long")

    def test_gtd_and_cancel(self):
        s = self.FakeSession(self.FakeResp({"orderCancelTransaction": {"reason": "INSUFFICIENT_MARGIN"}}))
        b = ox.OandaBroker("tok", "A", "practice", {"決済注文の有効期限": "GTD", "GTDの日数": 99}, session=s)
        with self.assertRaises(ox.BrokerError) as cm:
            b.market_open(1000, 156.0, 159.0, "fxauto_swing", "c", hedging=False)
        self.assertIn("INSUFFICIENT_MARGIN", str(cm.exception))
        o = s.calls[0][2]["order"]
        self.assertEqual(o["positionFill"], "DEFAULT")
        self.assertEqual(o["stopLossOnFill"]["timeInForce"], "GTD")
        self.assertIn("gtdTime", o["takeProfitOnFill"])


if __name__ == "__main__":
    unittest.main()
