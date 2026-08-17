# fx-usdjpy-cowork — FXドル円スキーム Cowork運用パッケージ

Macのlaunchdに依存していたFXドル円半自動売買スキームを、Cowork（クラウド）から
操作・監視できる形にしたもの。**判定はGitHub Actionsが行い、Coworkは結果を読んで
発注カードを提示する**（クラウドからはYahoo等の金融APIが遮断されているため）。

発注は JFX MATRIX TRADER で**必ず本人が手動**で行う。EA・外部プログラムによる
自動発注は規約で禁止されているため、本パッケージは提示までを担う。

## 構成

```
fx-cowork.yaml              ← 仕様の正本（データ経路・システム・受入基準・移行手順）
.claude-plugin/plugin.json  ← プラグイン定義（この形式のみJSON。他は全てYAML）
scripts/fx_fetch.py         ← 唯一のデータ取得口（標準ライブラリのみ・pip不要）
skills/
  fx-morning/SKILL.md       ← 朝の日足2システム指示書と発注カード
  fx-now/SKILL.md           ← 4システムの現況・あと何pips
  fx-intraday/SKILL.md      ← デイトレ2系統の直近アクション
  fx-watch/SKILL.md         ← Actionsの生存監視（鮮度切れ検知）
  fx-research/SKILL.md      ← 日次/週次研究の要約
routines/*.yaml             ← 定時実行5本（cronはUTC・JST併記）
reference/発注カード規約.md  ← pips表示の規約（全スキルが従う）
```

## 導入

1. 本ディレクトリをCoworkのプラグインとして読み込む
2. `python3 scripts/fx_fetch.py --selftest` で全経路の到達を確認する
3. `routines/*.yaml` の5本を登録する（まず `fx-watch-hourly` から入れて監視を立てる）

環境変数（既定のままで動く）:

| 変数 | 既定 | 用途 |
|---|---|---|
| `FX_REPO` | `rn4hmgbfhg-maker/fx-usdjpy` | 判定結果リポジトリ |
| `FX_BRANCH` | `main` | ブランチ |
| `FX_STALE_MIN` | `60` | 鮮度切れとみなす分数 |

## 動作確認（2026-08-18 実測）

| 受入基準 | 結果 |
|---|---|
| A1 全データ経路の到達 | 合格（8経路すべてOK） |
| A2 4システムの現況取得 | 合格 |
| A3 発注カードのpips欄 | **未達** — 下記M1が未実施のため |
| A4 鮮度切れの検知 | 合格 |
| A5 デイトレの重複通知防止 | 合格 |

## 残っている作業（M1）— これを行うまでカードにpipsが出ない

利確幅・損切り幅pipsの実装はMac側のsrcにあり、**リポジトリへ未反映**。
次の5ファイルをコミット＆pushすると受入基準A3が合格する。

```
src/risk.py               order_widths()（幅の唯一の計算元）
src/order_card.py         enrich() / render() / pips_tail() / hold_pips()
src/signal_engine.py      日足2システム
src/intraday_engine.py    デイトレ複合時間軸2H
src/intraday15_engine.py  デイトレ15分
```

パブリックリポジトリへの公開操作のため、実行は本人の承認後に行う。
push前に個人情報（メールアドレス・氏名入りホームパス）の混入がないか確認すること。

未実施の間、各スキルは「幅（pips）欄が無い旧版カード」と検知して報告し、
**pipsを自分で計算して補うことはしない**（数字の二重管理を防ぐため）。
