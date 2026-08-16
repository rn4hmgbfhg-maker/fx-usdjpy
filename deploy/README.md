# ここのYAMLを .github/workflows/ へ「移動」してください

## なぜこのフォルダがあるのか
gh CLI の OAuth トークンに `workflow` スコープが無く、`.github/workflows/` 配下は
git push が GitHub 側で拒否される（OAuth App からのワークフロー作成・更新は禁止）。
一方、**GitHub の Web UI 上での操作は「ユーザー本人」の操作**として扱われるため
この制限を受けない。そこで中身だけ先に置いてある。

## 手順（スマホのブラウザでも可・1ファイルにつき4タップ）
1. GitHub でこのファイル（例 `deploy/signals.yml`）を開く
2. 鉛筆アイコン（Edit this file）をタップ
3. **画面上部のファイル名の欄**を丸ごと
   `.github/workflows/signals.yml` に書き換える
   （`deploy/signals.yml` の `deploy/` を消して `.github/workflows/` を打つ。
     `/` を入力するとフォルダとして扱われる）
4. `Commit changes` をタップ

`research.yml` も同様に `.github/workflows/research.yml` へ。
これで内容をコピペする必要は無く、ファイルの移動だけで済む。

## 移動後
- リポジトリの `Actions` タブに `fx-signals` と `fx-research` が現れる
- `fx-signals` を開いて `Run workflow` で手動テスト実行できる
- メール通知を使うには Secrets の登録が別途必要（下記）

## Secrets（メール通知を使う場合のみ・後からでよい）
`Settings` → `Secrets and variables` → `Actions` → `New repository secret`
- 名前: `FX_GMAIL_APP_PASSWORD`
  値: Mac のキーチェーンアクセス.app で `fx-gmail-app-password` を検索して表示した値
- 名前: `FX_MAIL_TO`
  値: 指示書メールの宛先アドレス

★ Secrets が未登録でもワークフロー自体は動く（メール送信だけが失敗して記録される）。
   Mac版との突合を先に進めたい場合は、Secrets は後回しでよい。
