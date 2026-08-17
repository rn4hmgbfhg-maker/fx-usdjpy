# cowork/ — Cowork（クラウド）移行一式（統一版・2026-08-18）

Macに散在していたスキームの仕様と、Coworkで実際に動かすプラグインを**ここ1か所に集約**した。
Cowork移行後は、ここが運用の正典になる（Mac版のlaunchdとスケジュールタスクは照合用に残す）。

判定は GitHub Actions（fx-signals・15分ごと）が行い、**同じエンジンから
指示書・発注カード（利確幅・損切り幅pips入り）・メール・Cowork通知・Webボードが出る**。
Cowork側は結果を読んで整形・通知するだけで、判定もpips計算もしない。

| 層 | ファイル | 役割 |
|---|---|---|
| 仕様 | `fx_usdjpy_cowork.yaml` | **マスター仕様**。4システムの全パラメータ・ガード・ランブック・通知規約・受入基準・是正項目（855行）。`verify_spec.py` で実装と49項目突合できる |
| 仕様 | `verify_spec.py` | YAMLと現行実装（config/state/research/events/workflows）の突合。読み取り専用。移行前と自動進化日に必ず走らせる |
| 登録 | `routines.yaml` | Coworkへ**そのまま登録できる** routine 定義6本（Asia/Tokyo cron＋実行プロンプト同梱） |
| 実行 | `plugin/` | Coworkプラグイン本体。`skills/`（fx-morning／fx-now／fx-intraday／fx-watch／fx-research）＋`scripts/fx_fetch.py`（raw経由の唯一の取得口・pip不要）＋`reference/発注カード規約.md`＋`fx-cowork.yaml`（運用マニフェスト・受入基準・フォールバック） |

`routines.yaml` の各 prompt が「何をどう読むか」を書き、`plugin/skills/` がその作業を
会話からも呼べるスキルにしたもの。**両者は同じ raw ファイルを同じ規約で読む**
（数値の計算元は Actions 側 `src/risk.py` の `order_widths()` 一本）。

## 使い方

### 1. 移行前に必ず突合する

```bash
cd "$HOME/FXドル円自動売買スキーム" && python3 cowork/verify_spec.py
```

YAMLの数値が `config.json` / `state.json` / `src/research.py` / `src/events.py` /
`.github/workflows/*.yml` と一致しているかを49項目チェックする。
不一致があれば終了コード1。**YAMLか実装のどちらかを必ず直してから移行する**。

仕様書は書いた瞬間から腐り始める。「YAMLには40/10と書いてあるが実際は70/10で
動いている」という事故を防ぐのがこのスクリプトの唯一の目的。
パラメータが自動進化した日（`research.py` が config を書き換えた日）は必ず走らせる。

### 2. routine を登録する

`routines.yaml` の各 `routines[].name / schedule.cron / prompt` を
https://claude.ai/code/routines へ貼る。6本すべて登録して初めて現行と同等になる。

**タイムゾーンが `Asia/Tokyo` になっているか必ず確認する**（UTCのままだと9時間ズレる）。

| routine | 起動（JST） | 役割 |
|---|---|---|
| morning-daily | 平日 6:06 / 7:06 / 8:06 | 日足2システムの3段階配信・ボード公開 |
| intraday-2h | 平日 7〜23時の毎時6分 | デイトレ2Hの未通知シグナル通知 |
| intraday-15m | 毎時 4/19/34/49分 | デイトレ15分の未通知シグナル通知 |
| fundamental | 平日 6/9/12/15/18/21時台 | ニュース収集＋定量研究 |
| daily-research | 平日 15:20 | パラメータ進化＋3観点点検（必ず1通配信） |
| weekly-pair | 土曜 9:00 | 11通貨ペア横断研究 |

### 2b. プラグインを導入する

`plugin/` をCoworkのプラグインとして読み込み、`python3 plugin/scripts/fx_fetch.py --selftest`
で全経路の到達を確認する（8経路＋鮮度＋当日カード）。会話から「今日のFX」「あと何pips」
「デイトレ確認」「FX見張り」「FX研究」で各スキルが呼べる。

### 3. 5営業日並走してから Mac を止める

Cowork の既定環境は **Yahoo Finance も Forex Factory も EGRESS_BLOCKED**。
判定そのものは GitHub Actions が行い、Cowork は結果を読んで通知・公開・研究判断だけを担う。

停止していいのは受入基準1〜6を満たした後。手順は
`fx_usdjpy_cowork.yaml` の `移行手順` と `受入基準` を参照。

## 移行前に決めてほしいこと（YAMLの `是正項目`）

| ID | 内容 | 承認要否 |
|---|---|---|
| P1 | デイトレ2Hに鮮度ガードが無い（15分側にはある）。移植したい | 不要・着手待ち |
| P2 | Yahoo日足Closeの1日ずれが日足2システムに未対策。修正すると建玉判定が変わる | **要承認** |
| P3 | 長期トレンドを70/10・ATR3.0にすると勝率53.6%・PF2.97（現行44%/2.57） | **要承認** |
| P4 | `signals.yml` に `FX_MAIL_TO` が未配線（現状は実害なし） | 不要・保留 |
| P5 | Driveダッシュボードxlsx等のMac依存機能をどうするか（Webボード一本化するか） | **要承認** |

## 旧資料との関係

`docs/クラウド運用プロンプト/00〜08`（Markdown・2026-08-16作成）の内容は
本YAMLに統合済み。**齟齬が出たら本YAMLが正**。
旧資料は判定Pythonを丸ごと同梱した自己完結プロンプトなので、
モードB（ローカル貼付）で使いたい時だけ参照すればよい。
