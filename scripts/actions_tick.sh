#!/usr/bin/env bash
# GitHub Actions（signals.yml）の1回分の処理。2026-10-19 にワークフロー内のステップから切り出した。
# 15分ごとのループ（signals.yml の「15分ごとループ」）が毎回これを呼ぶ。各処理は冪等で、失敗しても次へ進む。
set -u
cd "$(dirname "$0")/.."

python3 src/run_intraday15.py || echo "⚠デイトレ15分 失敗"
python3 src/run_intraday.py || echo "⚠デイトレ2H 失敗"
# ユーロドル等のデータ更新（この中でスキャルのシグナル判定も走る）
python3 src/data_fetch_eurusd.py || echo "⚠ユーロドル データ更新 失敗"

H=$(TZ=Asia/Tokyo date +%H)
if [ "$H" -ge 6 ] && [ "$H" -le 8 ]; then
  python3 src/run_morning.py || echo "⚠日足 失敗"
else
  echo "JST ${H}時のため日足2システムはスキップ"
fi

python3 src/intraday15_engine.py --ack-mailed || true
python3 src/intraday_engine.py --ack-mailed || true

# 2026-10-28: OANDA 自動発注（注文係）。各エンジンの判定が出そろった後に1回照合する。
# モードは auto_order.json（off／dry=注文を出さない試運転／live=本番）
python3 src/oanda_exec.py || echo "⚠自動発注 失敗"

git config user.name  "fx-actions"
git config user.email "actions@users.noreply.github.com"
for f in state.json state_intraday.json state_intraday15.json state_eurusd.json; do
  [ -f "$f" ] && git add -- "$f"
done
git add -A -- results data 2>/dev/null || true
if git diff --staged --quiet; then
  echo "変更なし"
else
  git commit -q -m "signals $(TZ=Asia/Tokyo date +'%F %H:%M JST')"
  for i in 1 2 3; do
    if git pull --rebase -X theirs -q origin main && git push -q; then
      break
    fi
    git rebase --abort 2>/dev/null || true
    echo "push 再試行 $i"
    sleep 5
  done
fi
