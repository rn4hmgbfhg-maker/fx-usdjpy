# -*- coding: utf-8 -*-
"""ユーロ円研究の入口（中身は research_yenpair.py・2026-10-15 ポンド円追加で共通化）。
使い方: python3 src/research_eurjpy.py [--adopt]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_yenpair  # noqa: E402

if __name__ == "__main__":
    research_yenpair.run(adopt="--adopt" in sys.argv, pair="eurjpy")
