# -*- coding: utf-8 -*-
"""最終確定指示のメール自動送信（2026-08-12 ユーザー指示・経路3）

設計方針:
  - launchd から動く run_morning.py の中で完結させる。Claudeセッションが
    落ちていても毎朝必ず届く（セッション依存の通知とは独立の経路）。
  - 宛先は本人アドレス固定。外部宛には一切送れないようにしてある
    （誤送信・第三者への発注情報流出を構造的に防ぐ）。
  - アプリパスワードはコード・設定ファイルに置かず、macOSキーチェーンから読む。
      登録（本人が1回だけ実行。パスワードは対話入力で、履歴に残らない）:
      security add-generic-password -s fx-gmail-app-password \
          -a <本人アドレス> -w -U
  - 送信は1日1通（冪等）。マーカーファイルで二重送信を防ぐ。
  - 送信失敗はシグナル配信を止めない（呼び出し側は戻り値だけ見る）。

使い方:
  python3 src/mailer.py                # 本日の最終確定を送る（未送信なら）
  python3 src/mailer.py --dry-run      # 送らずに件名・本文を表示して確認
  python3 src/mailer.py --force        # 既に送っていても再送する
"""
import argparse
import json
import os
import smtplib
import ssl
import subprocess
from datetime import date
from email.message import EmailMessage
from email.utils import formatdate

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDERS_DIR = os.path.join(BASE_DIR, "results", "orders")

import local_settings  # noqa: E402  （個人情報をリポジトリから切り離す）
# 宛先は本人固定（外部宛には送れない設計）。実値は local_settings.json /
# 環境変数 FX_MAIL_TO から取る＝リポジトリには残さない（2026-08-16 公開化対応）。
TO_ADDRESS = local_settings.get("mail_to")
SMTP_TIMEOUT = 30
BOARD_URL = "https://claude.ai/code/artifact/fc8dd94c-6df0-4c02-a8be-72787cad9504"

# 送信経路は上から順に試し、最初に成功したものを使う。
#   gmail  : キーチェーン fx-gmail-app-password（Googleのアプリパスワード）
#   icloud : キーチェーン fx-icloud-app-password（Appleのアプリ用パスワード）
#   outlook: ローカルOutlookデスクトップをAppleScriptで叩く（パスワード不要）
TRANSPORT_ORDER = ("gmail", "icloud", "outlook")
KEYCHAIN = {"gmail": "fx-gmail-app-password",
            "icloud": "fx-icloud-app-password"}
SMTP = {"gmail": ("smtp.gmail.com", 465, "ssl"),
        "icloud": ("smtp.mail.me.com", 587, "starttls")}


ENV_CRED = {"fx-gmail-app-password": ("FX_GMAIL_ACCOUNT", "FX_GMAIL_APP_PASSWORD"),
            "fx-icloud-app-password": ("FX_ICLOUD_ACCOUNT",
                                       "FX_ICLOUD_APP_PASSWORD")}


def keychain_entry(service):
    """(アカウント, パスワード) を取得する。未登録なら (None, None)。

    取得順:
      1) 環境変数（GitHub Actions等のLinux用。Secretsから注入する）
      2) macOSキーチェーン（Mac運用時の従来経路）
    ★2026-08-16 追加: Actionsランナーには `security` コマンドが無く
    キーチェーンを引けないため、環境変数フォールバックを設けた。
    Mac側の挙動は環境変数が未設定なら従来どおりで変わらない。
    """
    acct_env, pw_env = ENV_CRED.get(service, (None, None))
    if pw_env:
        pw = (os.environ.get(pw_env) or "").strip()
        if len(pw) >= 12:
            return (os.environ.get(acct_env) or TO_ADDRESS), pw
    try:
        pw = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=15)
        # 空・短すぎる値は「登録し損なった残骸」として無効扱いにする。
        # 2026-08-12: 登録コマンドの貼り付け改行が入力プロンプトを飲み込み、
        # 中身2文字の項目ができていた。これで認証を試すと毎朝失敗するだけなので弾く。
        if pw.returncode != 0 or len(pw.stdout.strip()) < 12:
            return None, None
        meta = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, text=True, timeout=15)
        acct = None
        for line in meta.stdout.splitlines():
            if '"acct"<blob>=' in line:
                acct = line.split('="', 1)[-1].rstrip('"') or None
        return acct, pw.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None


def order_count(d):
    """発注カードの件数（機械可読版JSONから数える。無ければ本文から拾う）。"""
    jp = os.path.join(ORDERS_DIR, f"{d.isoformat()}_発注_確定.json")
    if os.path.exists(jp):
        try:
            data = json.load(open(jp, encoding="utf-8"))
            return sum(len(v) for v in data.get("操作", {}).values())
        except (OSError, ValueError):
            pass
    txt = instruction_text(d) or ""
    for line in txt.splitlines():
        if "本日の発注作業:" in line:
            digits = "".join(c for c in line if c.isdigit())
            return int(digits) if digits else 0
    return 0


def instruction_text(d):
    p = os.path.join(ORDERS_DIR, f"{d.isoformat()}_指示_確定.txt")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read().rstrip()


def marker_path(d):
    return os.path.join(ORDERS_DIR, f"{d.isoformat()}_mail_確定.sent")


def compose(d):
    """(件名, 本文) を返す。指示書が無ければ None。"""
    body = instruction_text(d)
    if body is None:
        return None
    n = order_count(d)
    head = f"{d:%-m/%-d}" if os.name != "nt" else f"{d.month}/{d.day}"
    subject = (f"【FX最終確定】{head} 発注{n}件（要発注）" if n
               else f"【FX最終確定】{head} 発注不要")
    text = (f"ドル円 最終確定指示 {d.isoformat()}\n"
            f"発注作業: {n}件\n"
            f"運用ボード: {BOARD_URL}\n"
            + "=" * 46 + "\n\n" + body + "\n\n" + "=" * 46 + "\n"
            "発注はMATRIX TRADER（Mac版アプリ）でご本人が手動転記・送信のこと。\n"
            "本メールは毎朝の最終確定時に自動送信（本人宛のみ）。\n")
    return subject, text


def _send_smtp(kind, subject, text):
    """SMTP直送（gmail/icloud）。戻り値: (成否, メッセージ)"""
    acct, pw = keychain_entry(KEYCHAIN[kind])
    if not pw:
        return False, f"{kind}: キーチェーン {KEYCHAIN[kind]} 未登録"
    sender = acct or TO_ADDRESS
    host, port, mode = SMTP[kind]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = TO_ADDRESS
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text)
    # 平文だけだとGmailが可変幅フォントで表示し発注カードの桁が崩れるため、
    # 等幅の<pre>版も同梱する（multipart/alternative）。
    msg.add_alternative(_html_body(text), subtype="html")
    try:
        ctx = ssl.create_default_context()
        if mode == "ssl":
            with smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT,
                                  context=ctx) as s:
                s.login(sender, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT) as s:
                s.starttls(context=ctx)
                s.login(sender, pw)
                s.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        return False, f"{kind}: {type(e).__name__}: {e}"
    return True, f"{kind} SMTP（差出人 {sender}）"


def _html_body(text):
    """Outlookはプレーン指定でもHTML化して送るため、<pre>で等幅・改行を保護する。
    （2026-08-12: plain text content で送ると発注カードの桁揃えが崩れて届いた）"""
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return ('<pre style="font-family:ui-monospace,Menlo,\'Courier New\',monospace;'
            'font-size:12.5px;line-height:1.5;white-space:pre-wrap;'
            'word-break:break-word;margin:0">' + esc + "</pre>")


# 本文の記号・改行をAppleScriptリテラルに埋め込むと壊れるため、一時ファイル経由で渡す
_OSA = '''set subj to read POSIX file "{sf}" as «class utf8»
set bodyText to read POSIX file "{bf}" as «class utf8»
tell application "Microsoft Outlook"
  set m to make new outgoing message with properties {{subject:subj, content:bodyText}}
  make new recipient at m with properties {{email address:{{address:"{to}"}}}}
  send m
end tell
return "sent"'''


def _send_outlook(subject, text):
    """ローカルOutlookデスクトップから送る（パスワード不要・要Outlook起動）。"""
    tmp = os.path.join(BASE_DIR, "results", ".mail_tmp")
    os.makedirs(tmp, exist_ok=True)
    sf, bf = os.path.join(tmp, "subject.txt"), os.path.join(tmp, "body.txt")
    try:
        with open(sf, "w", encoding="utf-8") as f:
            f.write(subject)
        with open(bf, "w", encoding="utf-8") as f:
            f.write(_html_body(text))
        if not subprocess.run(["pgrep", "-x", "Microsoft Outlook"],
                              capture_output=True).returncode == 0:
            subprocess.run(["open", "-g", "-a", "Microsoft Outlook"],
                           capture_output=True, timeout=60)
            import time
            time.sleep(20)   # 起動直後は Apple event を取りこぼすため待つ
        p = subprocess.run(
            ["osascript", "-e", _OSA.format(sf=sf, bf=bf, to=TO_ADDRESS)],
            capture_output=True, text=True, timeout=120)
        if p.returncode != 0 or "sent" not in p.stdout:
            err = (p.stderr or p.stdout).strip().splitlines()
            return False, "outlook: " + (err[-1][:160] if err else "不明なエラー")
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"outlook: {type(e).__name__}: {e}"
    return True, "Outlookデスクトップ"


def verify(kind):
    """メールを送らずにSMTP認証だけ試す（登録直後の確認用）。"""
    acct, pw = keychain_entry(KEYCHAIN[kind])
    if not pw:
        return False, f"{kind}: キーチェーン {KEYCHAIN[kind]} 未登録（または値が短すぎる）"
    sender = acct or TO_ADDRESS
    host, port, mode = SMTP[kind]
    try:
        ctx = ssl.create_default_context()
        if mode == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
            s.starttls(context=ctx)
        with s:
            s.login(sender, pw)
    except smtplib.SMTPAuthenticationError:
        return False, f"{kind}: 認証拒否（パスワードが違う／アプリパスワードでない）"
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        return False, f"{kind}: {type(e).__name__}: {e}"
    return True, f"{kind}: 認証成功（差出人 {sender}）"


def send_signal(subject, text):
    """シグナル即時メール（送信経路をTRANSPORT_ORDER順に試行）。
    デイトレ2H系・15分系のエンジンが新規建て・決済・ストップ変更・
    利確設定・決済確認など全アクションで呼ぶ共通経路
    （2026-08-15ユーザー指示「新規も決済も必ずメール配信」）。
    戻り値: (成否, メッセージ)。失敗してもエンジン側は処理を続行する。"""
    tried = []
    for kind in TRANSPORT_ORDER:
        ok, detail = (_send_outlook(subject, text) if kind == "outlook"
                      else _send_smtp(kind, subject, text))
        if ok:
            return True, detail
        tried.append(detail)
    return False, "メール送信失敗（全経路）: " + " ／ ".join(tried)


def send(d=None, dry_run=False, force=False, transport=None):
    """最終確定指示をメールする。戻り値: (成否, メッセージ)"""
    d = d or date.today()
    made = compose(d)
    if made is None:
        return False, "最終確定の指示書が無いためメールしない"
    subject, text = made
    if dry_run:
        return True, f"[dry-run] 件名: {subject}\n\n{text}"
    if os.path.exists(marker_path(d)) and not force:
        return True, "本日分は既に送信済み（スキップ）"

    tried = []
    for kind in ([transport] if transport else TRANSPORT_ORDER):
        ok, detail = (_send_outlook(subject, text) if kind == "outlook"
                      else _send_smtp(kind, subject, text))
        if ok:
            try:
                with open(marker_path(d), "w", encoding="utf-8") as f:
                    f.write(f"{subject}\n経路: {detail}\n")
            except OSError:
                pass
            return True, f"メール送信済み［{detail}］: {subject}"
        tried.append(detail)
    return False, "メール送信失敗（全経路）: " + " ／ ".join(tried)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="送信せず件名・本文を表示")
    ap.add_argument("--force", action="store_true", help="送信済みでも再送")
    ap.add_argument("--transport", choices=TRANSPORT_ORDER,
                    help="経路を明示（既定は上から順に自動フォールバック）")
    ap.add_argument("--verify", action="store_true",
                    help="送信せずSMTP認証だけ確認（キーチェーン登録直後の点検）")
    a = ap.parse_args()
    if a.verify:
        results = [verify(k) for k in (KEYCHAIN if not a.transport
                                       else [a.transport])
                   if k in KEYCHAIN]
        for ok, msg in results:
            print(("○ " if ok else "× ") + msg)
        print("→ 実働経路: " + ("Gmail/iCloud SMTP" if any(r[0] for r in results)
                             else "Outlookデスクトップ（SMTPは未使用）"))
        return 0 if any(r[0] for r in results) else 1
    ok, msg = send(dry_run=a.dry_run, force=a.force, transport=a.transport)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
