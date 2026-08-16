# -*- coding: utf-8 -*-
"""個人情報をリポジトリから切り離すためのローカル設定ローダー。

★2026-08-16 追加。リポジトリをパブリック化するにあたり、メールアドレス・
氏名入りのローカルパスがコードに直書きされていたのを外に出すために作った。

取得順:
  1) 環境変数（GitHub Actions では Secrets から注入する）
  2) local_settings.json（.gitignore 済み。Mac運用時はこれが効く）
  3) 既定値

キー:
  mail_to   / FX_MAIL_TO   … 指示書メールの宛先（本人固定）
  drive_dir / FX_DRIVE_DIR … Google Drive の公開先フォルダ（Mac専用）
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE_DIR, "local_settings.json")
# 1キーに複数の環境変数を許す。mail_to に FX_GMAIL_ACCOUNT を併記しているのは、
# signals.yml が FX_MAIL_TO を渡していないため（ワークフローの編集には
# workflow スコープが要り、OAuth トークンからは変更できない）。既に配線済みの
# FX_GMAIL_ACCOUNT を宛先の代替として使い、ワークフローを触らずに通す。
ENV = {"mail_to": ("FX_MAIL_TO", "FX_GMAIL_ACCOUNT"),
       "drive_dir": ("FX_DRIVE_DIR",)}

_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            with open(PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def get(key, default=""):
    for env in ENV.get(key, ()):
        v = (os.environ.get(env) or "").strip()
        if v:
            return v
    v = _load().get(key)
    return v if v else default
