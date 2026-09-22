# -*- coding: utf-8 -*-
"""旧名の互換入口。実体は fx_mac_push.py（ファンダ成果物＋研究パラメータをまとめて送る）。"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fx_mac_push import main  # noqa: E402

if __name__ == "__main__":
    main()
