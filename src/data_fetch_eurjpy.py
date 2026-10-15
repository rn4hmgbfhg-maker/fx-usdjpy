# -*- coding: utf-8 -*-
"""ユーロ円（EUR/JPY・2026-10-15 3通貨目）のデータ取得。中身は data_fetch_yenpair.py（クロス円の共通部品）。

使い方:
  python3 src/data_fetch_eurjpy.py                       # 日足・1時間足・15分足を更新
  python3 src/data_fetch_eurjpy.py --full                # 1時間足730日・15分足60日を取り直す
  python3 src/data_fetch_eurjpy.py --backfill 2023-08-01  # 15分足の長期履歴（Dukascopy・Macのみ）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_fetch_yenpair as _yp  # noqa: E402

_P = _yp.Pair("eurjpy")
SYMBOL, PIP = _P.SYMBOL, _P.PIP
CSV_DAILY, CSV_1H, CSV_15M, CSV_15M_HIST = _P.CSV_DAILY, _P.CSV_1H, _P.CSV_15M, _P.CSV_15M_HIST
update_all, load_daily, load_intraday, backfill = _P.update_all, _P.load_daily, _P.load_intraday, _P.backfill

if __name__ == "__main__":
    _yp.main("eurjpy")
