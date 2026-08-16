# 08｜GitHub Actions 移行手順（モードA＝完全クラウド・Mac不要）

作成: 2026-08-16

## なぜActionsなのか
クラウドのClaude既定環境は `raw.githubusercontent.com` と PyPI 以外への通信が遮断されている
（2026-08-14実測）。一方 **GitHub Actions のランナーは外向き通信が自由**なので、
Yahoo Finance も Forex Factory もそのまま叩ける。つまり:

```
[GitHub Actions] --毎時--> Yahoo取得・判定・状態更新 --commit--> [repo]
       |                                                          |
       +--> Gmail SMTPで指示書メール（スマホに届く）      raw.githubusercontent.com
                                                                  |
                                          [クラウドClaude routine]（任意）--> プッシュ通知
```

Macの電源・スリープ・故障から完全に独立する。

---

## ★先に知っておくべき2つの制約（隠さず書く）

### 1. Actionsのcronは「定刻」を保証しない
GitHubのスケジュール実行は**混雑時に数分〜十数分遅延する**ことがあり、負荷が高い時間帯は
**実行がスキップされる**こともある（GitHubの公式仕様）。毎正時に確定する1時間足を
毎時5分に判定する運用では、遅延しても**バー単位で冪等**にしておけば実害は出ない。

対策（本手順のワークフローに組み込み済み）:
- cronは**15分ごと**に回し、「未処理の確定バーがあれば処理、無ければ即終了」にする
- 処理済みバーは `state.json` の `last_bar` で判定する（＝二重通知しない）
- これで「定刻±15分」の精度が保証される。Mac版の毎時判定と実質同等。

### 2. リポジトリを公開すると建玉が公開される
クラウドのClaudeが `raw.githubusercontent.com` から結果を読むには、**公開リポジトリ**である
必要がある（プライベートのraw取得にはトークンが要り、それをプロンプトに書くのは不可）。
しかし結果ファイルには保有方向・数量・建値・逆指値が入る＝**取引ポジションの公開**になる。

**推奨: リポジトリはプライベートにし、通知はActions自身がGmail SMTPで送る。**
プッシュ通知も欲しい場合は、クラウド側は `03` の「モードC」（WebSearch近似）で
二重化する。リポジトリの中身は読ませない。この構成なら秘密情報は一切外に出ない。

---

## 手順1｜リポジトリを作る

```bash
cd "$HOME/FXドル円自動売買スキーム"
git init
git add src config.json docs
git commit -m "初期コミット: FXドル円スキーム"
gh repo create fx-usdjpy --private --source=. --push
```

★ `state*.json` と `results/` は**コミットする**（Actionsが状態を持ち回るため）。
★ 資格情報は絶対にコミットしない（メールのアプリパスワードは Secrets に置く）。

## 手順2｜Secrets を登録する

```bash
gh secret set GMAIL_APP_PASSWORD    # キーチェーン fx-gmail-app-password と同じ値
gh secret set MAIL_TO               # （本人アドレス・local_settings.json で設定）
```

★ アプリパスワードは**貼り付けで空登録になる落とし穴**がある（過去に発生）。
登録後は必ず `gh secret list` で存在を確認し、初回実行のログで送信成功を確かめる。

## 手順3｜ワークフローを置く

`.github/workflows/intraday15.yml`（第4システム＝移行第1号の推奨）:

```yaml
name: fx-intraday15
on:
  schedule:
    - cron: '*/15 * * * 1-5'    # UTC。JST月9:00〜土8:45を15分間隔でカバー
  workflow_dispatch:             # 手動実行も可能にする

permissions:
  contents: write                # 状態のコミットに必要

concurrency:
  group: fx-intraday15           # 遅延で実行が重なった時の二重判定を防ぐ
  cancel-in-progress: false

jobs:
  signal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install pandas numpy requests

      - name: 判定を実行（未処理バーが無ければ即終了）
        id: run
        run: python3 src/run_intraday15.py
        env:
          TZ: Asia/Tokyo

      - name: 指示書をメール送信（新規シグナル時のみ）
        if: steps.run.outputs.signal == '1'
        run: python3 src/mailer.py --file results/intraday15_latest.txt
        env:
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          MAIL_TO: ${{ secrets.MAIL_TO }}

      - name: 状態と結果をコミット
        run: |
          git config user.name  "fx-actions"
          git config user.email "actions@users.noreply.github.com"
          git add state_intraday15.json results/
          git diff --staged --quiet || git commit -m "intraday15 $(date -u +%FT%TZ)"
          git push
```

他システムも同じ形で置く。cronだけ変える:

| ファイル | システム | cron（UTC） | 意味 |
|---|---|---|---|
| `daily.yml` | 第1・第2（日足2システム） | `'0,15,30 23 * * 0-4'` | JST 8:00/8:15/8:30 の朝配信 |
| `intraday2h.yml` | 第3（2時間足） | `'*/15 * * * 1-5'` | 未処理バーがある時だけ処理 |
| `intraday15.yml` | 第4（1時間足） | `'*/15 * * * 1-5'` | 同上 |
| `fundamental.yml` | ファンダ・予測研究 | `'0 21,0,3,6,9,12 * * 1-5'` | JST 6/9/12/15/18/21時 |
| `research.yml` | 日次研究（進化） | `'0 6 * * 1-5'` | JST 15時 |
| `pair.yml` | 週次ペア研究 | `'0 0 * * 6'` | JST 土曜9時 |

★ cronはすべて**UTC**。JSTとの9時間差を必ず確認する（曜日指定もUTC基準でずれる）。

## 手順4｜Mac版と1週間並走させて突合する

**いきなり切り替えない。** Actionsを動かしつつMac版も動かし、毎日次を確認する:

```bash
# Mac側
python3 src/run_intraday15.py --status
# Actions側（コミットされた state をpullして比較）
git pull && cat state_intraday15.json
```

`position` / `stop` / `tp` / `units` / `entry` がすべて一致することを**5営業日連続**で確認する。
食い違ったら原因を突き止めるまで移行しない（`06_日次研究_パラメータ進化.md` の「3) 整合点検」参照）。

## 手順5｜切り替え

一致確認後、Mac側のlaunchdを止める:

```bash
launchctl unload ~/Library/LaunchAgents/com.ochiai.fx-intraday15.plist
```

★ **Mac版のコードは消さない。** 「セカンドオピニオン用の照合系」として残し、
月1回は手動実行してActions版と突き合わせる。

---

## 移行の推奨順序（再掲）

1. **第4システム（15分＝1時間足）** … リプレイ精度が実証済みで判定も軽い。第1号に最適
2. **第3システム（2時間足）** … 4Hフィルタ＋MACD確認があるぶん検証項目が多い
3. **第1・第2システム（日足）** … 未確定バーの暫定終値問題があるため最後。
   状態をリポジトリに持たせれば解決する（＝Actions版はむしろMac版より安定する）
4. **研究系（日次・週次・ファンダ）** … 判定に直結しないので最後でよい

## 移行後に残る「Macでしかできないこと」

- Excel運用ダッシュボード（xlsx）の更新 … Drive連携がMacローカル前提
- macOSデスクトップ通知
- Outlookデスクトップ経由のメール送信（フォールバック経路）

これらが不要なら、Macは完全に停止してよい。
Excelダッシュボードを残したい場合は「Actionsが判定・記録 → Macが週1でダッシュボードだけ生成」
という分担にする。
