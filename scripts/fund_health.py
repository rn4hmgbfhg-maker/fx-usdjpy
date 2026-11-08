# -*- coding: utf-8 -*-
"""ファンダ研究の停止の見張り（2026-11-09 新設・落合さん指示「恒久的な対策措置」）

2026-10-16 夜から 11-09 まで、ファンダ研究が3週間止まったまま誰も気付かなかった。
  ・定性ニュース … Mac の Cowork 定期タスクが担当していたが、Mac 停止で止まった
  ・定量研究     … GitHub Actions では scipy が無く例外で落ち、しかも「成功」扱いだった
このスクリプトは出力ファイルの「更新」時刻を見て、古ければ
  1) ワークフローを赤（終了コード1）にする → GitHub が本人へ失敗メールを送る
  2) Gmail の秘密情報（FX_GMAIL_APP_PASSWORD・FX_MAIL_TO）があれば本人へ1通送る
のどちらも行う。本人アドレス以外には送らない。発注には一切関わらない。

使い方:  python3 scripts/fund_health.py            # 点検（古ければ終了コード1）
         python3 scripts/fund_health.py --no-mail  # メールは送らない
"""
import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

JST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(BASE, "results")

# (ファイル, 表示名, 許容時間h, 担当)
QUANT = [
    ("fundamental_latest.json", "定量研究（レジーム・翌日予測）", 26,
     "GitHub Actions fx-fundamental（平日朝6:20）"),
    ("fund_overlay_latest.json", "本運用ゲート判定（fund_overlay）", 26,
     "GitHub Actions fx-fundamental（平日朝6:20）"),
]
NEWS = ("fundamental_news.json", "定性ニュース", 10,
        "クラウド定期タスク「FXドル円 ファンダ・予測研究（クラウド）」")


def _updated(fname):
    try:
        with open(os.path.join(RES, fname), encoding="utf-8") as f:
            s = json.load(f).get("更新")
        return datetime.fromisoformat(s).replace(tzinfo=JST) if s else None
    except Exception:  # noqa: BLE001 - 壊れていても「古い」として扱う
        return None


def market_open(now):
    """月曜6時〜土曜6時（JST）＝為替市場が開いている時間。"""
    wd, h = now.weekday(), now.hour
    if wd == 6:
        return False
    if wd == 5:
        return h < 6
    if wd == 0:
        return h >= 6
    return True


def check(now):
    bad = []
    for fname, label, hours, owner in QUANT:
        t = _updated(fname)
        # 定量は Actions が直前に実行してから点検するので、平日朝以降はいつでも新しいはず。
        # 週末は土曜朝の実行から月曜朝まで空くので 60h まで許容する。
        lim = hours if market_open(now) else 60
        if t is None or now - t > timedelta(hours=lim):
            bad.append((label, t, lim, owner))
    fname, label, hours, owner = NEWS
    # ニュースは3時間ごと。月曜10時より前（週末明け）と市場休止中は点検しない。
    news_due = market_open(now) and not (now.weekday() == 0 and now.hour < 10)
    if news_due:
        t = _updated(fname)
        if t is None or now - t > timedelta(hours=hours):
            bad.append((label, t, hours, owner))
    return bad


def send_mail(subject, body):
    pw, to = os.environ.get("FX_GMAIL_APP_PASSWORD"), os.environ.get("FX_MAIL_TO")
    if not pw or not to:
        print("（メール用の秘密情報が無いため送信なし。ワークフローの失敗メールで代替）")
        return False
    acct = os.environ.get("FX_GMAIL_ACCOUNT") or to   # mailer.py と同じ取り方
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, acct, to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                              context=ssl.create_default_context(), timeout=30) as s:
            s.login(acct, pw)
            s.send_message(msg)
        print("停止の通知メールを送りました")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠通知メールの送信に失敗: {type(e).__name__}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-mail", action="store_true")
    args = ap.parse_args()
    now = datetime.now(JST)
    bad = check(now)
    if not bad:
        print(f"ファンダ研究: すべて最新（{now:%m/%d %H:%M} 点検）")
        return 0
    lines = [f"ファンダ研究の出力が古くなっています（{now:%Y-%m-%d %H:%M} JST 点検）", ""]
    for label, t, lim, owner in bad:
        age = "記録なし" if t is None else f"{t:%m/%d %H:%M} 更新（{(now - t).total_seconds() / 3600:.0f}時間前）"
        lines.append(f"・{label}: {age}／許容 {lim}時間／担当 {owner}")
        print(f"::error::{label} が古い: {age}")
    lines += ["",
              "ファンダ本運用（fund_live）は予測が古いと自動で適用を止め、従来の売買判定に戻ります（安全側）。",
              "発注への直接の影響はありませんが、指示書のファンダ警戒欄が古いままになります。",
              "チャットで Claude に「ファンダ研究が止まっている」と伝えてください。"]
    if not args.no_mail:
        send_mail("【FX】ファンダ研究が止まっています", "\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
