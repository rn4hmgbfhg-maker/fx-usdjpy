# 08｜GitHub Actions 移行（モードA＝完全クラウド・Mac不要）— 実装記録

作成: 2026-08-16 ／ 状態: **リポジトリ構築・パブリック化まで完了。ワークフロー投入待ち**

## リポジトリ
`https://github.com/rn4hmgbfhg-maker/fx-usdjpy` — **パブリック**・163ファイル・1コミット

## なぜActionsなのか
クラウドのClaude既定環境は `raw.githubusercontent.com` と PyPI 以外への通信が遮断されている
（2026-08-14実測）。一方 **GitHub Actions のランナーは外向き通信が自由**なので、
Yahoo Finance も Forex Factory もそのまま叩ける。

```
[GitHub Actions] --15分ごと--> Yahoo取得・判定・状態更新 --commit--> [repo]
       |                                                             |
       +--> Gmail SMTPで指示書メール（スマホに届く）         raw.githubusercontent.com
                                                                     |
                                             [クラウドClaude routine]（任意）--> プッシュ通知
```

Macの電源・スリープ・故障から完全に独立する。

---

## ★実際にぶつかった落とし穴（4件・すべて対処済み）

### 1. Linuxでスクリプトが落ちる（macOS依存）
`notify_mac()` / `notify()` は `osascript` を呼ぶが、**Actionsランナーには存在せず
`subprocess.run(..., check=False)` でも FileNotFoundError で落ちる**（`check=False` は
非ゼロ終了を抑えるだけで、実行ファイル不在は防げない）。
`mailer.keychain_entry()` も `security` コマンドに依存していた。

対処:
- `intraday15_engine.py` / `intraday_engine.py` / `signal_engine.py` の通知関数に
  `if sys.platform != "darwin": return` のガードを追加
- `signal_engine.py` の `open(指示書)` も同じガード
- `mailer.py` に**環境変数フォールバック**を追加（`FX_GMAIL_APP_PASSWORD` →
  キーチェーンの順。Mac側は環境変数が未設定なら従来どおりで挙動不変）

### 2. gh の OAuth トークンに `workflow` スコープが無い
`.github/workflows/*.yml` を含むpushが
`refusing to allow an OAuth App to create or update workflow ... without workflow scope`
で拒否される。**`gh auth refresh -s workflow` が必要**（ブラウザ認証＝本人操作）。

### 3. git が GitHub に認証できない
`could not read Username for 'https://github.com'`。gh はログイン済みでも
git の資格情報ヘルパーが未設定だと出る。`gh auth setup-git` で解消。

### 4. パブリック化すると個人情報が公開される
公開前スキャンで **メールアドレス11箇所・氏名入りホームパス22箇所**を検出した。

対処: `src/local_settings.py` を新設し、個人情報を**未追跡ファイル**へ外出しした。
```
取得順: 環境変数（Actions Secrets） → local_settings.json（.gitignore済み） → 既定値
キー:   mail_to / FX_MAIL_TO      … 指示書メールの宛先
        drive_dir / FX_DRIVE_DIR  … Google Drive公開先（Mac専用）
```
- `config.json` の `Driveフォルダ` を空にして実パスを `local_settings.json` へ
- `mailer.py` の `TO_ADDRESS` を `local_settings.get("mail_to")` へ
- `results/*.log` / `results/orders/*.sent` / `scripts/set_mail_password.command` を
  `.gitignore` して追跡から外した
- `docs/` 内の実アドレス・実パスを伏せ字化
- **1コミットに amend して force push** ＝ 過去版にも個人情報は残らない
- 公開後にリモートをcloneし直して**残存ゼロを実証**

★ なお **保有ポジション・建値・逆指値・損益は公開されたまま**（`state*.json` と
`results/orders/`）。これは状態をリポジトリで持ち回る設計の必然で、
パブリック化を選んだ時点で受け入れた前提。

---

## ワークフロー構成（実装済み・投入待ち）

### `.github/workflows/signals.yml`
```
cron: '5,20,35,50 * * * 0-5'   # UTC日〜金の15分ごと = JST月6:05〜土8:50
```
- **全シグナル系を1ジョブに集約**（checkout+pipのコストを1回で済ませる）
- 第4デイトレ15分 → 第3デイトレ2H → 日足2システム（JST6〜8時台のみ）の順に実行
- 各ステップ `continue-on-error: true`＝1系統の失敗で他を巻き添えにしない
- `concurrency` グループで直列化＝cron遅延で重なっても二重判定しない
- 最後に `state*.json` / `results` / `data` をコミット

★ 15分間隔にした理由: **Actionsのcronは定刻を保証せず**、混雑時に数分〜十数分遅延し
スキップされることもある（GitHub公式仕様）。各ラッパは未処理バーが無ければ即終了する
冪等設計なので、多めに回して取りこぼしを潰す。パブリックは無料枠無制限なので回数は自由。

★ `0-5`（日〜金）にしている理由: **UTC月〜金(1-5)にするとJST月曜朝6〜9時が抜け、
日足2システムの月曜配信が落ちる**。

### `.github/workflows/research.yml`
```
cron: '0 6 * * 1-5'            # UTC 6:00 = JST 15:00（平日）
```
- 日足2システム／デイトレ2H／デイトレ15分／ファンダの4研究を順に実行（timeout 90分）
- 頑健性ゲートを通った時だけ `config.json` が書き換わる＝自動進化
- 進化が起きたら `::warning::` で
  「docs/クラウド運用プロンプト/01〜03 の定数を更新すること」と警告を出す
  （**合わせないとクラウド代行版とActions版で判定が食い違う**）

★ プライベートのままなら無料枠2,000分/月に対し研究だけで約770分を食うため
載せられなかった。**パブリック化で初めて研究のクラウド移行が可能になった。**

---

## 残りの手順

### 手順A｜ワークフロー権限を付与（本人操作）
```bash
gh auth refresh -s workflow
```

### 手順B｜Gmailアプリパスワードを Secrets へ（本人操作・値は画面に出ない）
```bash
security find-generic-password -s fx-gmail-app-password -w | gh secret set FX_GMAIL_APP_PASSWORD --repo rn4hmgbfhg-maker/fx-usdjpy
```
```bash
gh secret set FX_MAIL_TO --repo rn4hmgbfhg-maker/fx-usdjpy --body "$(python3 -c "import json;print(json.load(open('$HOME/FXドル円自動売買スキーム/local_settings.json'))['mail_to'])")"
```
★ アプリパスワードは**貼り付けで空登録になる落とし穴**がある（過去に発生）。
`gh secret list` で存在を確認し、初回実行のログで送信成功を確かめること。

### 手順C｜ワークフロー投入と手動テスト
```bash
cd "$HOME/FXドル円自動売買スキーム" && git add -f .github && git commit -m "Actionsワークフロー追加" && git push && gh workflow run fx-signals
```

### 手順D｜Mac版と1週間並走して突合（★飛ばさない）
```bash
cd "$HOME/FXドル円自動売買スキーム" && python3 src/run_intraday15.py --status && git pull -q && cat state_intraday15.json
```
`position` / `stop` / `tp` / `units` / `entry` がすべて一致することを**5営業日連続**で確認する。
食い違ったら原因を突き止めるまで移行しない
（`06_日次研究_パラメータ進化.md` の「3) 整合点検」参照）。

### 手順E｜切り替え
一致確認後、Mac側のlaunchdを止める:
```bash
launchctl unload ~/Library/LaunchAgents/com.ochiai.fx-intraday15.plist
```
★ **Mac版のコードは消さない。** 「セカンドオピニオン用の照合系」として残し、
月1回は手動実行してActions版と突き合わせる。

---

## 移行の推奨順序

1. **第4システム（15分＝1時間足）** … リプレイ精度が実証済みで判定も軽い。第1号に最適
2. **第3システム（2時間足）** … 4Hフィルタ＋MACD確認があるぶん検証項目が多い
3. **第1・第2システム（日足）** … 未確定バーの暫定終値問題があるため最後。
   状態をリポジトリに持たせれば解決する（＝Actions版はむしろMac版より安定する）
4. **研究系** … 判定に直結しないので最後でよい

## 移行後に残る「Macでしかできないこと」
- Excel運用ダッシュボード（xlsx）の更新 … Drive連携がMacローカル前提
- macOSデスクトップ通知
- Outlookデスクトップ経由のメール送信（フォールバック経路）

これらが不要ならMacは完全に停止してよい。Excelダッシュボードを残したい場合は
「Actionsが判定・記録 → Macが週1でダッシュボードだけ生成」という分担にする。
