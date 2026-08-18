# -*- coding: utf-8 -*-
"""モバイル運用ボード(HTML)生成 — Mac/iPhone/iPad共通のWebダッシュボード

平日朝の速報①(6時台)・速報②(7時台)・最終確定(8時台)の全ステージで
results/dashboard_web.html を再生成する(エンジンが毎回呼ぶ)。
速報段階は「速報配信中」バナーを表示し、正式判断は最終確定である旨を明示する。
公開(アーティファクト更新)は朝のスケジュールタスク内のClaudeが行う。
HTMLは自己完結(外部リソースなし)・ライト/ダーク両テーマ・モバイルファースト。
"""
import html
import json
import os
import re
import sys
from datetime import date, datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import Backtester, atr  # noqa: E402
import risk  # noqa: E402
from strategies import (donchian, donchian_exit_levels,  # noqa: E402
                        donchian_exit_proximity)
import perf  # noqa: E402
import swap  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731
OUT_HTML = R("results", "dashboard_web.html")

ACT_STYLE = {  # アクション → (表示, クラス)
    "新規買(ロング)": ("新規 買", "buy"), "新規売(ショート)": ("新規 売", "sell"),
    "ストップ変更": ("ストップ切上げ", "gold"), "決済": ("全決済", "gold"),
    "継続": ("保有継続", "flat"), "待機": ("待機", "flat"),
    "イベント見送り": ("イベント見送り", "flat"),
    "ストップ決済確認": ("ストップ決済済", "gold"),
}


def _parse_order_actions(order_text, names):
    """指示書本文から日足システムごとの【見出し】を読み、カード表示へ写す。

    本文が唯一の正（速報段階は日次ログがまだ当日分を持たない）。解釈できない
    書式なら該当システムを返さず、従来どおり日次ログへフォールバックする。
    """
    label_map = {
        "確認": ("ストップ決済済", "gold"), "決済": ("全決済", "gold"),
        "見送り": ("イベント見送り", "flat"), "変更": ("ストップ切上げ", "gold"),
        "継続": ("保有継続", "flat"), "待機": ("待機", "flat"),
    }
    out = {}
    for name in names:
        m = re.search(r"^▼\s*" + re.escape(name) + r"[（(].*?$", order_text,
                      re.M)
        if not m:
            continue
        block = order_text[m.end():]
        nxt = re.search(r"^(?:▼|◆|-{5,}|={5,})", block, re.M)
        block = block[:nxt.start()] if nxt else block
        tag = re.search(r"【(確認|決済|見送り|変更|継続|待機|新規)】", block)
        if not tag:
            continue
        if tag.group(1) == "新規":
            side = re.search(r"【新規】.*?(買|売)", block, re.S)
            if not side:
                continue
            out[name] = (f"新規 {side.group(1)}",
                         "buy" if side.group(1) == "買" else "sell")
        else:
            out[name] = label_map[tag.group(1)]
    return out


def _read_csv(path, cols):
    if os.path.exists(path):
        try:
            df = pd.read_csv(path)
            if len(df):
                return df
        except Exception:
            pass
    return pd.DataFrame(columns=cols)


def _width_html(pos, entry, mark, stop, tp):
    """保有玉の「利確幅・損切り幅 pips」をボードにも出す（2026-08-18ユーザー指示）。

    幅は発注カード（order_card.enrich）と同じ建値基準で、計算は
    risk.order_widths() だけに任せる。ボード側で *100 を書くと、同じ数字を
    2箇所で計算して片方だけ直し残る事故になる（memory
    feedback_fix_all_output_targets）。現値からの距離も併記する
    （転記直後に効くのは建値基準の幅ではなくこちらの距離のため）。
    """
    if not pos or entry in (None, ""):
        return ""
    w = risk.order_widths(pos, entry, stop=stop, tp=tp)
    now = risk.order_widths(pos, mark, stop=stop, tp=tp)
    bits = []
    if w["利確幅pips"] is not None:
        bits.append(f'利確幅 <b>{w["利確幅pips"]:.0f}</b> pips')
    if w["損切り幅pips"] is not None:
        over = (float(stop) - float(entry)) * pos > 0   # 建値より有利側
        bits.append(f'損切り幅 <b>{w["損切り幅pips"]:.0f}</b> pips'
                    + ("（建値超え＝到達しても利益）" if over else ""))
    if not bits:
        return ""
    near = []
    if now["損切り幅pips"] is not None:
        near.append(f'逆指値まで {now["損切り幅pips"]:.0f}'
                    + ("（★超過）" if now["損切り逆行"] else ""))
    if now["利確幅pips"] is not None:
        near.append(f'利確まで {now["利確幅pips"]:.0f}')
    tail = (f'　<span class="stop">現値から '
            + "／".join(near) + " pips</span>") if near else ""
    return '　' + "／".join(bits) + tail


def _bar_html(status):
    """デイトレ系カードに「判定に使った確定バー」を出す。

    2026-08-17 追加: カードには実行時刻（最終判定）しか出ておらず、週明けの
    ように新しい足がまだ立っていない時間帯でも「今の値で判定した」と読めて
    しまった。判定の土台になったバーを必ず並記する。
    """
    bar = str(status.get("確定バー") or "")
    if not bar:
        return ""
    return f'<span class="stop">判定バー {html.escape(bar)}</span>'


def _stale_note(status, tf_name):
    """データ鮮度不足のときだけ、何が起きているかを1行で説明する。"""
    if not status.get("鮮度警告"):
        return ""
    bar = html.escape(str(status.get("確定バー") or "－"))
    return (f'<div class="note">※<b>データ鮮度不足</b>＝{tf_name}の最新確定バー'
            f'（{bar}）が古く、新しい判定を保留している状態'
            f'（週明けや配信元の遅延で起きる）。'
            f'保有中の建玉と決済注文（逆指値・利確）はそのまま維持し、'
            f'新規の建て玉は足が更新されるまで見送る。</div>')


def _spark_svg(values, w=640, h=180):
    """資産カーブのSVG(エリア+終点強調)。valuesは数値list。"""
    if len(values) < 2:
        return ""
    n = max(1, len(values) // 200)
    vs = values[::n]
    if vs[-1] != values[-1]:
        vs.append(values[-1])
    lo, hi = min(vs), max(vs)
    rng = (hi - lo) or 1.0
    pad = 12
    pts = []
    for i, v in enumerate(vs):
        x = pad + (w - 2 * pad) * i / (len(vs) - 1)
        y = pad + (h - 2 * pad) * (1 - (v - lo) / rng)
        pts.append(f"{x:.1f},{y:.1f}")
    line = " ".join(pts)
    area = f"{pad},{h-pad} " + line + f" {w-pad},{h-pad}"
    grid = "".join(
        f'<line x1="{pad}" y1="{pad + (h-2*pad)*i/3:.1f}" x2="{w-pad}" '
        f'y2="{pad + (h-2*pad)*i/3:.1f}" class="grid"/>' for i in range(4))
    ex, ey = pts[-1].split(",")
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="資産推移">'
            f'{grid}<polygon points="{area}" class="area"/>'
            f'<polyline points="{line}" class="line"/>'
            f'<circle cx="{ex}" cy="{ey}" r="4" class="dot"/></svg>')


def build():
    cfg = json.load(open(R("config.json"), encoding="utf-8"))
    systems = {k: v for k, v in cfg.get("システム", {}).items()
               if v.get("有効", True)}
    state = json.load(open(R("state.json"), encoding="utf-8")) \
        if os.path.exists(R("state.json")) else {}
    if "position" in state:
        state = {"長期トレンド": state}

    px = pd.read_csv(R("data", "usdjpy_daily.csv"))
    close = float(px["Close"].iloc[-1])
    daily = _read_csv(R("results", "daily_log.csv"), ["日付", "システム"])
    # 決済履歴は3ファイル（日足2システム/デイトレ/デイトレ15分）を合算する。
    # 2026-08-14: ここが trades.csv だけだったため、デイトレの決済が
    # 実現損益・勝率・PF・取引回数のどれにも入らず「決済しても成績が
    # 変わらない」状態になっていた（ユーザー指摘）。
    trades = perf.load_all()
    research = _read_csv(R("results", "research_log.csv"), ["日付", "判定"])
    pair_log = _read_csv(R("results", "pair_scan_log.csv"), ["実行日", "判定"])

    # 指示書(最新日の最上位ステージ: 確定 > 速報2 > 速報1)
    order_text = "本日の指示書はまだ生成されていません。"
    is_flash = False   # 速報段階の表示か
    odir = R("results", "orders")
    if os.path.isdir(odir):
        files = [f for f in os.listdir(odir)
                 if f.endswith(".txt") and "_指示_" in f]
        if files:
            latest_date = max(f.split("_")[0] for f in files)
            todays = [f for f in files if f.startswith(latest_date)]
            rank = {"速報1": 1, "速報2": 2, "確定": 3}
            todays.sort(key=lambda f: rank.get(
                f.rsplit("_", 1)[-1].replace(".txt", ""), 0))
            pick = todays[-1]
            is_flash = "確定" not in pick
            order_text = open(os.path.join(odir, pick),
                              encoding="utf-8").read().strip()
    # 本日の配信メタ（各ステージの実際の生成時刻・遅延印・完成率）
    # 2026-08-12: launchdの発火遅れ+エンジンの長時間ハングで最終確定が09:34になった
    # が、ボード上に痕跡が無く遅延に気づけなかった。以後ヘッダで常時可視化する。
    def _run_log_time(label):
        """当日そのステージが実際に生成された時刻（最初の成功行）を返す。"""
        log = R("results", "run_log.csv")
        if not os.path.exists(log):
            return None
        today_iso = date.today().isoformat()
        for line in open(log, encoding="utf-8"):
            c = line.rstrip("\n").split(",")
            if len(c) >= 4 and c[1] == today_iso and c[2] == label \
                    and c[3] == "成功":
                try:
                    return datetime.fromisoformat(c[0])
                except ValueError:
                    return None
        return None

    delivery = ""
    if os.path.isdir(odir):
        today = date.today().isoformat()
        parts, warn = [], False
        for label, tag, sched_h in (("速報①", "速報1", 6), ("速報②", "速報2", 7),
                                    ("最終確定", "確定", 8)):
            p = os.path.join(odir, f"{today}_指示_{tag}.txt")
            if os.path.exists(p):
                # 配信時刻は run_log.csv（実際の生成記録）を正とする。
                # 2026-08-18: git pull が results/orders も含む76ファイルを
                # 07:05に書き戻し、mtime基準だと定刻生成(06:05)の速報①まで
                # 「⚠遅延」と誤表示した。mtimeは記録が無い時だけの保険。
                t = _run_log_time(label) or datetime.fromtimestamp(
                    os.path.getmtime(p))
                late = (t.hour, t.minute) >= (sched_h, 20)
                warn = warn or late
                parts.append(f"{label} {t:%H:%M}" + ("⚠遅延" if late else ""))
            else:
                parts.append(f"{label} 未")
                # 未到来のステージ（6時台に見る最終確定など）は警告対象にしない
                warn = warn or datetime.now().hour >= sched_h
        days = sorted({f.split("_")[0] for f in os.listdir(odir)
                       if f.endswith(".txt") and "_指示_" in f})[-10:]
        # 分母は「配信時刻を過ぎたステージ」だけ（当日の未到来分は欠落ではない）。
        # 2026-08-13: 6時台に 27/30 ⚠要点検 と出て未来の2件を欠落扱いしていた。
        now_h = datetime.now().hour
        got = exp = 0
        for d in days:
            for tg, sched_h in (("速報1", 6), ("速報2", 7), ("確定", 8)):
                if d == today and now_h < sched_h:
                    continue
                exp += 1
                got += os.path.exists(os.path.join(odir, f"{d}_指示_{tg}.txt"))
        # 2026-08-14: 警告印を完成率の直後にまとめて出していたため、
        # 完成率 29/29（100%）でも遅延だけで「⚠要点検」が付き、
        # 欠落があるように読めた。遅延と欠落は別々の位置に出す。
        delivery = ("<br>本日の配信 " + " ／ ".join(parts)
                    + ("　⚠配信遅延あり" if warn else "")
                    + f"　｜ 完成率(直近{len(days)}営業日) {got}/{exp}"
                    + ("　⚠欠落あり" if got < exp else ""))

    # デイトレの最新指示を同じ指示書欄に併記（3システムを1枚で確認できるように）
    latest_intraday = R("results", "intraday_latest.txt")
    if os.path.exists(latest_intraday):
        order_text += ("\n\n" + "=" * 46 +
                       "\n▼ デイトレ複合時間軸 最新指示（毎時更新）\n" + "=" * 46 +
                       "\n" + open(latest_intraday,
                                   encoding="utf-8").read().strip())

    # デイトレ15分の最新指示も併記（4システムを1枚で確認できるように）
    latest_i15 = R("results", "intraday15_latest.txt")
    if os.path.exists(latest_i15):
        order_text += ("\n\n" + "=" * 46 +
                       "\n▼ デイトレ15分 最新指示（15分ごと更新）\n" + "=" * 46 +
                       "\n" + open(latest_i15,
                                   encoding="utf-8").read().strip())

    # 速報段階のみ: 当日バーでのストップ到達を暫定反映する。
    # エンジンの正式計上(trades.csv追記・state更新)は8時の最終確定でのみ行われる
    # ため、速報中はカードが「保有継続」・損益が据え置きになり指示書本文と食い違う
    # (2026-08-11ユーザー指摘)。判定ロジックはsignal_engine.process_systemと同一。
    pending = {}   # システム名 -> 暫定実現損益(円・コスト込み)
    if is_flash and len(px):
        from signal_engine import COST_PER_SIDE
        last_bar = px.iloc[-1]
        for name in systems:
            st0 = state.get(name, {})
            p0 = int(st0.get("position", 0))
            sv = st0.get("stop")
            if p0 != 0 and sv is not None:
                sv = float(sv)
                hit = (p0 > 0 and float(last_bar["Low"]) <= sv) or \
                      (p0 < 0 and float(last_bar["High"]) >= sv)
                if hit:
                    gap = (p0 > 0 and float(last_bar["Open"]) <= sv) or \
                          (p0 < 0 and float(last_bar["Open"]) >= sv)
                    exit_px = float(last_bar["Open"]) if gap else sv
                    u = float(st0.get("units") or 0)
                    pending[name] = (u * (exit_px - float(st0["entry"])) * p0
                                     - u * COST_PER_SIDE * 2)

    # カードのアクションは「本日の指示書本文」を一次情報にする。
    # 2026-08-13: 速報段階では日次ログ(最終確定でしか追記されない)を見ていたため、
    # 指示書が【継続】の朝でもカードに前日の「ストップ切上げ」が残り矛盾していた。
    flash_acts = _parse_order_actions(order_text, list(systems))

    # システム別サマリ+検証値
    sys_cards, needs_action = [], False
    # リスクリワード管理（2026-08-14 ユーザー指示）。各カードで求めた値を
    # ここに集めて下段のRRカードで一覧にする。計算は risk.py に一本化。
    rr_rows = []
    eq_ref_curves = []
    atr_series = atr(px)
    for name, s in systems.items():
        n_e, n_x = int(s["エントリー期間"]), int(s["イグジット期間"])
        atr_k = float(s["ATRストップ係数"])
        params = f"ドンチャン{n_e}/{n_x}・ATR×{atr_k}"
        st = state.get(name, {})
        pos = int(st.get("position", 0))
        drow = daily[daily["システム"] == name] if len(daily) else daily
        act_raw = str(drow.iloc[-1]["アクション"]) if len(drow) else "運用開始前"
        act, cls = ACT_STYLE.get(act_raw, (act_raw, "flat"))
        if name in flash_acts:      # 指示書本文が最優先（表示の食い違いを防ぐ）
            act, cls = flash_acts[name]
        if cls in ("buy", "sell", "gold"):
            needs_action = True
        pos_html = (f'<span class="pos buy">買 {int(st.get("units", 0)):,}通貨</span>'
                    if pos > 0 else
                    f'<span class="pos sell">売 {int(st.get("units", 0)):,}通貨</span>'
                    if pos < 0 else '<span class="pos flat">ポジションなし</span>')
        stop_html = (f'ストップ <b>{float(st["stop"]):.3f}</b>円'
                     if st.get("stop") else "ストップ設定なし")
        if pos and st.get("stop"):
            stop_html += _width_html(pos, st.get("entry"), close,
                                     st.get("stop"), None)
        _sy = 0.0
        if pos:
            _sy, _sd = swap.accrued(pos, float(st.get("units") or 0),
                                    st.get("entry_date"),
                                    sw=swap.load(cfg))
            stop_html += f'　スワップ <b>{_sy:+,.0f}</b>円（{_sd}日分）'
        # 利確（手仕舞い）ライン: ドンチャンの逆側チャネル。2026-08-14 ユーザー
        # 指示で常時表示にした。それまで日足2システムはATRトレール逆指値しか
        # 出しておらず、実際にはその手前で効くことが多いこの線が見えなかった。
        tp_html = ""
        tp_now, tp_next = donchian_exit_levels(px, n_x, pos)
        if tp_now is not None:
            side_txt = ("高値を終値で超えたら決済" if pos < 0
                        else "安値を終値で割ったら決済")
            _u = float(st.get("units") or 0)
            _ent = float(st.get("entry") or 0)

            def _at(lv):    # その水準で決済した場合の損益（スワップ込み概算）
                return (_ent - lv) * _u * (1 if pos < 0 else -1) + _sy

            def _kind(v):   # 利確なのか損切りなのかを言い切る
                return "利益" if v > 0 else "損失"
            # 逆指値とこの線の、市場に近い方が先に効く出口。
            _stop = float(st.get("stop") or 0)
            first = ""
            if _stop:
                near_tp = (tp_now < _stop) if pos < 0 else (tp_now > _stop)
                first = "　← 先に効くのはこちら" if near_tp else ""
            _dist_atr, _dist_pct = donchian_exit_proximity(px, n_e, n_x, pos,
                                                           atr_series)
            prox = ""
            if _dist_atr is not None:
                prox = f'　現値からの距離 {_dist_atr:.2f}ATR'
                if _dist_pct is not None and _dist_pct <= 0.10:
                    prox += (f'（★過去の同方向保有時の下位{_dist_pct:.0%}＝'
                             f'かなり接近中。値動き次第で近日中に決済判定が'
                             f'入りうる）')
            _p_now = _at(tp_now)
            nxt = ""
            if tp_next is not None and abs(tp_next - tp_now) >= 0.005:
                _p_nx = _at(tp_next)
                _mv = "切り下がる" if tp_next < tp_now else "切り上がる"
                nxt = (f'<br><span class="stop">　└ 次の引け以降は '
                       f'<b>{tp_next:.3f}</b>円へ{_mv}見込み'
                       f'（到達なら{_kind(_p_nx)} {_p_nx:+,.0f}円）'
                       f'　※当日バーが確定するまでは暫定値</span>')
            tp_html = (f'<span class="stop">決済ライン（今回の引けで判定）'
                       f'<b>{tp_now:.3f}</b>円'
                       f'（ドンチャン{n_x}日{side_txt}／到達なら'
                       f'{_kind(_p_now)} <b>{_p_now:+,.0f}</b>円・'
                       f'スワップ込み概算）{first}{prox}</span>{nxt}')
        else:
            tp_html = ('<span class="stop">決済ライン —'
                       '（建玉なし。建てた時点でドンチャン'
                       f'{n_x}日の逆側チャネルが決済ラインになる）</span>')
        if name in pending:   # 速報段階の暫定反映: 指示書本文と表示を一致させる
            act, cls = "ストップ到達→決済確認", "gold"
            needs_action = True
            stop_html = (f'ストップ <b>{float(st["stop"]):.3f}</b>円 到達 '
                         f'（決済見込み {pending[name]:+,.0f}円・8時最終確定で正式計上）')
        bt = Backtester(px, initial_capital=float(cfg["口座資金_円"]),
                        risk_per_trade=float(s["リスク率"]),
                        max_leverage=float(s["レバ上限"]), atr_stop_mult=atr_k)
        ref = bt.run(donchian(px, n=n_e, exit_n=n_x))
        m = ref["metrics"]
        if pos:
            # 日足2系統は利確目標を置かない設計なので tp は渡さない。
            # RRの代わりに検証実績のペイオフレシオを添える。
            _rr = risk.position_rr(pos, st.get("units"), st.get("entry"),
                                   close, st.get("stop"), tp=None,
                                   swap_yen=_sy)
            _rr["システム"] = name
            _rr["検証PF"] = m.get("プロフィットファクター")
            _rr["決済ライン"] = tp_now
            rr_rows.append(_rr)
        eq_ref_curves.append(ref["equity_curve"]["Equity"].astype(float).values)
        sys_cards.append(f"""
<article class="card">
  <div class="card-head"><h2>{html.escape(name)}</h2>
    <span class="params">{html.escape(params)}</span></div>
  <div class="action {cls}">{html.escape(act)}</div>
  <div class="posline">{pos_html}<span class="stop">{stop_html}</span></div>
  <div class="posline">{tp_html}</div>
  <div class="note">※決済ラインは<b>終値での判定</b>。指値注文としては置かない
    （日中に触れただけでは決済せず、終値で超えた翌朝の指示書が決済指示になる）。
    MATRIX TRADERに実際に置く注文は上のストップのみ。<br>
    ※この線は<b>利確ではなくトレンド転換の撤退線</b>。含み損のまま到達すれば
    損切りとして働く（利益が出る保証はない）。</div>
  <div class="chips">
    <span class="chip">15年検証 PF {m['プロフィットファクター']:.2f}</span>
    <span class="chip">勝率 {m['勝率']:.0%}</span>
    <span class="chip">最大DD {m['最大ドローダウン']:.1%}</span>
    <span class="chip">総リターン {m['総リターン']:+.1%}</span>
  </div>
</article>""")

    # デイトレ複合時間軸カード（第3システム・毎時エンジンが状態を更新）
    dcfg = cfg.get("デイトレ")
    if dcfg and dcfg.get("有効", True):
        dstate = json.load(open(R("state_intraday.json"), encoding="utf-8")) \
            if os.path.exists(R("state_intraday.json")) else {}
        dstatus = json.load(open(R("results", "intraday_status.json"),
                                 encoding="utf-8")) \
            if os.path.exists(R("results", "intraday_status.json")) else {}
        dst = dstate.get("state", {})
        dres = _read_csv(R("results", "research_intraday_log.csv"), ["日付"])
        dpos = int(dst.get("position", 0))
        dact_raw = str(dstatus.get("アクション", "運用開始前"))
        dact, dcls = ACT_STYLE.get(dact_raw, (dact_raw, "flat"))
        if dstatus.get("未通知"):
            needs_action = True
        dpos_html = (f'<span class="pos buy">買 {int(dst.get("units", 0)):,}通貨</span>'
                     if dpos > 0 else
                     f'<span class="pos sell">売 {int(dst.get("units", 0)):,}通貨</span>'
                     if dpos < 0 else '<span class="pos flat">ポジションなし</span>')
        dstop_html = (f'ストップ <b>{float(dst["stop"]):.3f}</b>円'
                      if dst.get("stop") else "ストップ設定なし")
        if dst.get("tp"):
            dstop_html += f'　利確 <b>{float(dst["tp"]):.3f}</b>円'
        if dpos:
            _sy, _sd = swap.accrued(dpos, float(dst.get("units") or 0),
                                    dst.get("entry_date"), sw=swap.load(cfg))
            dstop_html += f'　スワップ <b>{_sy:+,.0f}</b>円（{_sd}日分）'
            dstop_html += _width_html(dpos, dst.get("entry"),
                                      float(dstate.get("last_close") or close),
                                      dst.get("stop"), dst.get("tp"))
            # デイトレは利確指値を置くのでRRが確定値で出せる。
            # 現値は必ず自系統の最終確定バー終値を使う。2026-08-15: ここが日足
            # 終値(close)だったため、金曜夜建ての玉が金曜朝の日足終値で評価され
            # 「含み損-3千円・★ストップ超過」という実態と逆の誤表示が出た。
            dmark = float(dstate.get("last_close") or close)
            _rr = risk.position_rr(dpos, dst.get("units"), dst.get("entry"),
                                   dmark, dst.get("stop"), tp=dst.get("tp"),
                                   swap_yen=_sy)
            _rr["システム"] = "デイトレ複合時間軸"
            _rr["検証PF"] = (dres.iloc[-1].get("PF全")
                            if len(dres) else None)
            rr_rows.append(_rr)
        dparams = html.escape(str(dstatus.get(
            "パラメータ",
            f"{dcfg['時間足']}H {dcfg['エントリー期間']}/{dcfg['イグジット期間']}"
            f" ATR×{dcfg['ATRストップ係数']}")))
        chips = ""
        if len(dres):
            dr = dres.iloc[-1]
            chips = (f'<span class="chip">2.8年検証 PF {dr.get("PF全", "－")}</span>'
                     f'<span class="chip">勝率 {dr.get("勝率", "－")}</span>'
                     f'<span class="chip">最大DD {dr.get("最大DD", "－")}</span>'
                     f'<span class="chip">約{dr.get("頻度_回転毎営業日", "－")}回転/日</span>')
        try:
            from run_intraday import completion_rate
            _, got, exp = completion_rate()
            if exp:
                chips += (f'<span class="chip">完成率 {got}/{exp}'
                          f'（{100 * got / exp:.0f}%）</span>')
        except Exception:
            pass
        trial_tag = "（試験運転）" if dcfg.get("試験運転") else ""
        upd = html.escape(str(dstatus.get("更新", ""))[:16].replace("T", " "))
        sys_cards.append(f"""
<article class="card span2">
  <div class="card-head"><h2>デイトレ複合時間軸{trial_tag}</h2>
    <span class="params">{dparams}・毎時判定・指標発表を常時監視</span></div>
  <div class="action {dcls}">{html.escape(dact)}</div>
  <div class="posline">{dpos_html}<span class="stop">{dstop_html}</span>
    <span class="stop">最終判定 {upd}</span>{_bar_html(dstatus)}</div>
  {_stale_note(dstatus, "2時間足")}
  <div class="chips">{chips}</div>
</article>""")

    # デイトレ15分カード（第4システム・15分ごとエンジンが状態を更新）
    d15cfg = cfg.get("デイトレ15分")
    if d15cfg and d15cfg.get("有効", True):
        d15state = json.load(open(R("state_intraday15.json"), encoding="utf-8")) \
            if os.path.exists(R("state_intraday15.json")) else {}
        d15status = json.load(open(R("results", "intraday15_status.json"),
                                   encoding="utf-8")) \
            if os.path.exists(R("results", "intraday15_status.json")) else {}
        d15st = d15state.get("state", {})
        d15res = _read_csv(R("results", "research_15m_log.csv"), ["日付"])
        p15 = int(d15st.get("position", 0))
        act15_raw = str(d15status.get("アクション", "運用開始前"))
        act15, cls15 = ACT_STYLE.get(act15_raw, (act15_raw, "flat"))
        if d15status.get("未通知"):
            needs_action = True
        pos15_html = (f'<span class="pos buy">買 {int(d15st.get("units", 0)):,}通貨</span>'
                      if p15 > 0 else
                      f'<span class="pos sell">売 {int(d15st.get("units", 0)):,}通貨</span>'
                      if p15 < 0 else '<span class="pos flat">ポジションなし</span>')
        stop15_html = (f'ストップ <b>{float(d15st["stop"]):.3f}</b>円'
                       if d15st.get("stop") else "ストップ設定なし")
        if d15st.get("tp"):
            stop15_html += f'　利確 <b>{float(d15st["tp"]):.3f}</b>円'
        if p15:
            _sy, _sd = swap.accrued(p15, float(d15st.get("units") or 0),
                                    d15st.get("entry_date"), sw=swap.load(cfg))
            stop15_html += f'　スワップ <b>{_sy:+,.0f}</b>円（{_sd}日分）'
            stop15_html += _width_html(p15, d15st.get("entry"),
                                       float(d15state.get("last_close")
                                             or close),
                                       d15st.get("stop"), d15st.get("tp"))
            # 現値は自系統の1時間足最終終値（日足closeで評価しない。上のデイ
            # トレ欄のコメント参照）。
            mark15 = float(d15state.get("last_close") or close)
            _rr = risk.position_rr(p15, d15st.get("units"), d15st.get("entry"),
                                   mark15, d15st.get("stop"),
                                   tp=d15st.get("tp"), swap_yen=_sy)
            _rr["システム"] = "デイトレ15分"
            _rr["検証PF"] = (d15res.iloc[-1].get("PF全")
                            if len(d15res) else None)
            rr_rows.append(_rr)
        chips15 = ""
        if len(d15res):
            dr15 = d15res.iloc[-1]
            chips15 = (f'<span class="chip">3年検証 PF {dr15.get("PF全", "－")}</span>'
                       f'<span class="chip">勝率 {dr15.get("勝率", "－")}</span>'
                       f'<span class="chip">最大DD {dr15.get("最大DD", "－")}</span>'
                       f'<span class="chip">約{dr15.get("頻度_回転毎営業日", "－")}回転/日</span>')
        rate15 = d15status.get("テクニカル合議15分") or {}
        rate1h_d = d15status.get("テクニカル合議1時間") or {}
        if rate15:
            chips15 += (f'<span class="chip">合議15分 {rate15.get("判定", "－")}'
                        f'（{rate15.get("スコア", 0):+.2f}）</span>'
                        f'<span class="chip">合議1H {rate1h_d.get("判定", "－")}'
                        f'（{rate1h_d.get("スコア", 0):+.2f}）</span>')
        try:
            from run_intraday15 import completion_rate as cr15
            _, got15, exp15 = cr15()
            if exp15:
                chips15 += (f'<span class="chip">完成率 {got15}/{exp15}'
                            f'（{100 * got15 / exp15:.0f}%）</span>')
        except Exception:
            pass
        trial15 = "（試験運転）" if d15cfg.get("試験運転") else ""
        upd15 = html.escape(str(d15status.get("更新", ""))[:16].replace("T", " "))
        strat15 = html.escape(str(d15cfg.get("戦略", "")))
        sys_cards.append(f"""
<article class="card span2">
  <div class="card-head"><h2>デイトレ15分{trial15}</h2>
    <span class="params">{strat15}・ATR×{d15cfg.get("ATRストップ係数", "－")}
     {"・利確ATR×" + str(d15cfg.get("利確ATR係数")) if d15cfg.get("利確ATR係数") else ""}
     ・15分ごと判定・指標発表を常時監視</span></div>
  <div class="action {cls15}">{html.escape(act15)}</div>
  <div class="posline">{pos15_html}<span class="stop">{stop15_html}</span>
    <span class="stop">最終判定 {upd15}</span>{_bar_html(d15status)}</div>
  {_stale_note(d15status, "1時間足")}
  <div class="chips">{chips15}</div>
</article>""")

    # リスクリワード管理カード（2026-08-14 ユーザー指示で新設）
    # 「いま各建玉がいくら失いうるか／いくら取りにいけるか」を1枚に集約する。
    # 数字は risk.py だけが計算し、ボード側は並べるだけにする（出力先ごとに
    # 計算を書くと片方だけ直し残る＝memory feedback_fix_all_output_targets）。
    sys_short = {"長期トレンド": "長期", "短期スイング": "短期",
                 "デイトレ複合時間軸": "デイ", "デイトレ15分": "15分"}
    rr_card = ""
    if rr_rows:
        pr = risk.portfolio_risk(rr_rows, cfg["口座資金_円"])
        rr_tr = []
        for r in rr_rows:
            _rw = ("－" if r["リワード額円"] is None
                   else f'{r["リワード額円"]:+,.0f}円')
            _rr_v = ("－" if r["RR"] is None else f'{r["RR"]:.2f}')
            _pf = r.get("検証PF")
            try:
                _pf = float(_pf)
            except (TypeError, ValueError):
                _pf = None
            _cmt = risk.rr_comment(r["RR"], _pf,
                                   has_tp=r["リワード額円"] is not None,
                                   over=r["ストップ超過"])
            _cls = "down" if (r["リスク額円"] or 0) < 0 else "up"
            _warn = "　<b>★ストップ超過</b>" if r["ストップ超過"] else ""
            rr_tr.append(
                f'<tr><td>{html.escape(sys_short.get(r["システム"], r["システム"]))}</td>'
                f'<td>{r["建玉方向"]} {r["数量"]:,.0f}</td>'
                f'<td>{r["建値"]:.3f}</td>'
                f'<td>{r["現値"]:.3f}</td>'
                f'<td class="{"up" if r["含み損益円"] >= 0 else "down"}">'
                f'{r["含み損益円"]:+,.0f}円</td>'
                f'<td class="{_cls}">{r["リスク額円"]:+,.0f}円</td>'
                f'<td>{_rw}</td><td>{_rr_v}</td>'
                f'<td>{html.escape(_cmt)}{_warn}</td></tr>')
        _hedge = ""
        if pr["買建玉"] and pr["売建玉"]:
            _hedge = (f'　買{pr["買建玉"]:,.0f} / 売{pr["売建玉"]:,.0f} が'
                      f'ぶつかり、実質は<b>ネット'
                      f'{"買" if pr["ネット建玉"] > 0 else "売"} '
                      f'{abs(pr["ネット建玉"]):,.0f}通貨</b>'
                      f'（両建て度 {pr["両建て度"]:.0%}）。'
                      f'相場が一方向へ振れれば片側は利益になるため、'
                      f'合計リスクは最悪ケースの見積り。')
        rr_card = f"""
<section class="card span2">
  <h3>リスクリワード管理（保有中の全建玉）</h3>
  <div class="kpis" style="margin-top:10px">
    <div class="kpi"><div class="l">合計リスク額<br><small>全ストップ同時到達</small></div>
      <div class="v down">{pr["合計リスク額円"]:+,.0f}円</div></div>
    <div class="kpi"><div class="l">口座資金に対する比率</div>
      <div class="v">{pr["合計リスク率"]:.2%}</div></div>
    <div class="kpi"><div class="l">現在の含み損益<br><small>各系の最終確定バー評価</small></div>
      <div class="v {"up" if pr["合計含み損益円"] >= 0 else "down"}">
        {pr["合計含み損益円"]:+,.0f}円</div></div>
    <div class="kpi"><div class="l">ネット建玉</div>
      <div class="v">{"買" if pr["ネット建玉"] > 0 else "売" if pr["ネット建玉"] < 0 else "±"}
        {abs(pr["ネット建玉"]):,.0f}</div></div>
  </div>
  <div class="tablewrap"><table>
    <tr><th>系</th><th>売買/数量</th><th>建値</th><th>現値</th><th>含み損益</th>
      <th>ストップ到達</th><th>利確到達</th><th>RR</th><th>評価</th></tr>
    {"".join(rr_tr)}
  </table></div>
  <div class="note">※「ストップ到達」「利確到達」は<b>建値との差＋スワップ</b>で
    計算した決済損益の概算。含み益が乗っていればストップ到達でも利益になる
    （トレール後の正常な状態）。RR＝ここから先の利益余地÷損失余地で、
    利確目標を置かない日足2系統は「－」。
    現値は<b>各システム自身の最終確定バー終値</b>（日足系＝日足終値／デイ＝2時間足
    ／15分＝1時間足）のため、行によって時点が異なることがある。{_hedge}</div>
  <div class="note">※スリッページ・窓開けは考慮していない。指標発表時や
    週明けの窓ではストップを超えて約定することがある。</div>
</section>"""

    # 合算KPI（4システム合算。速報段階は当日ストップ到達分を暫定加算する）
    perf_sum = perf.summary(trades, pending)
    realized_all = perf_sum["累計実現損益"]
    if perf_sum["件数"]:
        wr = f"{perf_sum['勝率']:.0%}"
        pf = f"{perf_sum['PF']:.2f}" if perf_sum["PF"] is not None else "－"
        tag = ('<small style="font-weight:400">（暫定）</small>'
               if pending else "")
        cum_html = (f'<span class="{"up" if realized_all >= 0 else "down"}">'
                    f'{realized_all:+,.0f}円</span>{tag}')
        ntr = f"{perf_sum['件数']}回"
    else:
        wr = pf = "－"; cum_html = "運用開始前"; ntr = "0回"
    # システム別の実現損益内訳（どのシステムの決済が効いているかを一目で）
    # sys_short はリスクリワードカードと共用（上で定義済み）。
    perf_chips = "".join(
        f'<span class="chip">{html.escape(sys_short.get(b["システム"], b["システム"]))}'
        f' {b["損益円"]:+,.0f}円'
        f'<small>／{b["件数"]}回・勝率{b["勝率"]:.0%}</small></span>'
        for b in perf_sum["システム別"]) or ""
    kpi_note = ("<div class=\"note\">※速報段階のため本日のストップ到達分"
                f"（{sum(pending.values()):+,.0f}円）を暫定反映。正式計上は"
                "8時すぎの最終確定。</div>"
                if pending else "")
    # 含み損益（保有中3システムを最新終値で評価・概算）
    # 2026-08-13: 値動きだけでなくスワップ（未受渡の金利差損益）も加算する。
    # 建玉を持ち越すほど実口座との乖離が広がっていたため（ユーザー指示）。
    sw_cfg = swap.load(cfg)
    mark = float(px["Close"].iloc[-1]) if len(px) else 0.0
    open_pos, unreal, unreal_swap = [], 0.0, 0.0
    for name in list(systems) + ["デイトレ複合時間軸", "デイトレ15分"]:
        if name in systems:
            st0 = state.get(name, {})
            if name in pending:      # 速報の暫定決済分は含み益に二重計上しない
                continue
        elif name == "デイトレ15分":
            st0 = (json.load(open(R("state_intraday15.json"), encoding="utf-8"))
                   .get("state", {})
                   if os.path.exists(R("state_intraday15.json")) else {})
        else:
            st0 = (json.load(open(R("state_intraday.json"), encoding="utf-8"))
                   .get("state", {})
                   if os.path.exists(R("state_intraday.json")) else {})
        p0 = int(st0.get("position", 0) or 0)
        if p0 == 0 or not st0.get("entry"):
            continue
        u0 = float(st0.get("units") or 0)
        unreal += u0 * (mark - float(st0["entry"])) * p0
        unreal_swap += swap.accrued(p0, u0, st0.get("entry_date"), sw=sw_cfg)[0]
        open_pos.append(name)
    unreal += unreal_swap
    if open_pos:
        unreal_html = (f'<span class="{"up" if unreal >= 0 else "down"}">'
                       f'{unreal:+,.0f}円</span>'
                       f'<small style="font-weight:400">／{len(open_pos)}建玉'
                       f'・内スワップ {unreal_swap:+,.0f}円</small>')
    else:
        unreal_html = '<span class="v">ポジションなし</span>'
    # 2026-08-13: KPIの含み損益は全建玉を日足終値で統一評価するのに対し、
    # 指示書本文のデイトレ欄は2時間足終値で評価するため数十円ずれる。
    # 同一画面に違う合計が並んで「どちらかが誤り」に見えるため評価基準を明記する。
    mark_note = (f'※含み損益は全建玉を日足終値{mark:,.3f}円で統一評価'
                 f'（下の指示書本文のデイトレ欄は2時間足終値評価のため'
                 f'数十円の差が出ることがある）。' if open_pos else "")
    if sw_cfg.get("有効", True):
        swap_part = (("スワップ込み。" if mark_note else "※含み損益はスワップ込み。")
                     + html.escape(swap.note(sw_cfg)))
    else:
        swap_part = ""
    swap_note = (f'<div class="note">{mark_note}{swap_part}</div>'
                 if (mark_note or swap_part) else "")

    eq_daily = (daily.drop_duplicates("日付", keep="last")
                if len(daily) else daily)
    # モデル資産は4システム合算でその場で計算する。
    # 2026-08-14: 従来は daily_log.csv（日足2システム専用・8時台のみ更新）の
    # モデル資産をそのまま出していたため、デイトレの実現損益も含み損益も
    # 資産に乗らなかった。含み損益KPIは4システムを見ていたので、同じ画面で
    # 「資産」と「損益」の対象範囲が食い違っていた。
    equity_now = float(cfg["口座資金_円"]) + realized_all + unreal
    equity_txt = f"{equity_now:,.0f}円"

    # 資産カーブ(実運用5日未満は検証合成カーブ)
    if len(eq_daily) >= 5:
        # 生成は perf.equity_curve に一本化（xlsx側と同じ関数を通す）。
        # 2026-08-14: ここだけを4システム化して xlsx のパフォーマンスシートを
        # 直し忘れ、同じ資産が2つ並ぶ事故を起こしたため共通化した。
        pts = perf.equity_curve(daily, float(cfg["口座資金_円"]),
                                equity_now=equity_now, trades=trades)
        curve_dates = [d for d, _ in pts]
        curve = [v for _, v in pts]
        curve_label = "資産推移（実運用モデル・全4システム）"
    else:
        init = float(cfg["口座資金_円"])
        merged = eq_ref_curves[0] - init
        for c in eq_ref_curves[1:]:
            merged = merged + (c - init)
        curve = (merged + init).tolist()
        curve_dates = []
        curve_label = "資産推移（15年検証・両システム合成の理論値）"
    # カーブの読み方を数字で添える（グラフだけでは起点・現在値が読めない）
    if curve_dates:
        curve_note = (
            f'<div class="note">{curve_dates[0]} {curve[0]:,.0f}円 → '
            f'{curve_dates[-1]} {curve[-1]:,.0f}円'
            f'（{curve[-1] - curve[0]:+,.0f}円・{len(curve)}営業日）'
            '　※各点＝口座資金＋その日までの4システム累計実現損益（スワップ込）'
            '＋日足2システムの評価損益。デイトレ2系統の過去の含み損益は点に含まない'
            '（最終点のみ全建玉を評価）。</div>')
    else:
        curve_note = ""

    # 研究・ペア研究
    res_msg = "研究ログなし"
    if len(research):
        r = research.iloc[-1]
        adopted = len(research[research["判定"] == "採用"])
        res_msg = (f"最終研究 {r['日付']}・進化 累計{adopted}回・直近 "
                   f"{r.get('システム', '')} {r['判定']}")
    pair_msg = "初回の土曜9時に実行されます"
    if len(pair_log):
        p = pair_log.iloc[-1]
        pair_msg = f"{p['実行日']}: {p['判定']}"

    # ファンダ・予測研究（research_fundamental.py が書き出すJSON）
    fund_html = ""
    fpath = R("results", "fundamental_latest.json")
    if os.path.exists(fpath):
        try:
            fu = json.load(open(fpath, encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            fu = None
        if fu:
            corr_chips = "".join(
                f'<span class="chip">{html.escape(str(c["指標"]))} '
                f'{c.get("相関60日", 0):+.2f}</span>'
                for c in (fu.get("相関") or [])[:5])
            fc = fu.get("翌日予測") or {}
            fc_html = "（予測なし）"
            if fc:
                lo, hi = fc.get("想定レンジ", [0, 0])
                cls = "buy" if fc.get("上昇確率", 0.5) >= 0.5 else "sell"
                fc_html = (
                    f'<span class="pos {cls}">{html.escape(fc.get("方向", ""))}'
                    f' 確率{fc.get("上昇確率", 0)*100:.0f}%</span>'
                    f'<span class="stop">想定レンジ {lo:.2f}〜{hi:.2f}円'
                    f'（基準 {html.escape(str(fc.get("基準日", "")))} '
                    f'{fc.get("基準終値", 0):.3f}円）</span>')
            wf = fu.get("予測モデル") or {}
            fwd = fu.get("前向き成績") or {}
            wf_txt = (f"学習外検証 {wf.get('検証日数', 0)}日・的中率 "
                      f"{wf.get('的中率', 0):.1%}（常に上昇と答えた場合 "
                      f"{wf.get('上昇日比率', 0):.1%}）"
                      if wf else "検証データ不足")
            vol = fu.get("ボラ予測") or {}
            if vol:
                wf_txt += (f"／ボラ予測 順位相関 {vol.get('順位相関', 0):+.2f}")
            fwd_txt = (f"実運用の前向き採点 {fwd['件数']}件・的中率 "
                       f"{fwd['的中率']:.1%}" if fwd.get("件数") else
                       "実運用の前向き採点はまだ蓄積中")
            # ニュースは fundamental_news.json を直接参照する。定量研究→
            # ニュース更新の順で走った回は latest 埋め込み分が1世代古くなる
            # ため、更新時刻が新しい方を採用（レジーム・予測は latest のまま）
            news = fu.get("ニュース") or {}
            npath = R("results", "fundamental_news.json")
            if os.path.exists(npath):
                try:
                    n_direct = json.load(open(npath, encoding="utf-8"))
                except Exception:                                    # noqa: BLE001
                    n_direct = None
                if n_direct and (str(n_direct.get("更新") or "") >
                                 str(news.get("更新") or "")):
                    news = n_direct
            items = "".join(
                f'<tr><td>{html.escape(str(t.get("含意", "")))}</td>'
                f'<td>{html.escape(str(t.get("見出し", "")))}</td></tr>'
                for t in (news.get("テーマ") or [])[:5])
            news_upd = str(news.get("更新") or "")
            news_head = (f'<p class="note">ニュース総括（更新 '
                         f'{html.escape(news_upd)}）：' if news_upd
                         else '<p class="note">')
            news_html = (
                f'{news_head}{html.escape(str(news.get("総括") or ""))}</p>'
                f'<div class="tablewrap"><table>'
                f'<tr><th>含意</th><th>材料</th></tr>{items}</table></div>'
                if items else '<p class="note">ニュース未収集</p>')
            fund_html = f"""
<section class="card span2">
  <div class="card-head"><h3>ファンダ・予測研究（毎日更新・発注には未使用）</h3>
    <span class="params">更新 {html.escape(str(fu.get("更新", "")))}</span></div>
  <div class="action flat" style="font-size:1.1rem">\
{html.escape(str(fu.get("レジーム", "")))}</div>
  <div class="chips">{corr_chips}</div>
  <div class="posline" style="margin-top:10px">{fc_html}</div>
  <p class="note">{html.escape(wf_txt)}／{html.escape(fwd_txt)}</p>
  <p class="note">※方向予測は現時点で「常に上昇」に勝てておらず、
    発注判断には一切使っていない（研究・記録のみ）。</p>
  {news_html}
</section>"""

    # 直近の記録（日足2システムの日次ログ + デイトレの毎時アクションログを
    # 時系列でマージして新しい順に表示）
    def _stop_disp(v):    # 決済行などストップ空欄は "nan" ではなく "—" を出す
        s = str(v or "").strip()
        return "—" if s.lower() in ("", "nan", "none") else s

    recs = []
    for _, r in daily.tail(10).iterrows():
        recs.append((f"{r['日付']} 08:00", str(r["日付"]),
                     str(r["システム"])[:2], str(r["アクション"]),
                     _stop_disp(r["ストップ"]), f"{float(r['モデル資産']):,.0f}"))
    dlog = _read_csv(R("results", "intraday_log.csv"), ["日時"])
    for _, r in dlog.tail(10).iterrows():
        ts = str(r["日時"]).replace("T", " ")
        stop_s = _stop_disp(r.get("ストップ", ""))
        recs.append((ts, ts[5:16], "デイ", str(r["アクション"]), stop_s, "—"))
    d15log = _read_csv(R("results", "intraday15_log.csv"), ["日時"])
    for _, r in d15log.tail(10).iterrows():
        ts = str(r["日時"]).replace("T", " ")
        stop_s = _stop_disp(r.get("ストップ", ""))
        recs.append((ts, ts[5:16], "15分", str(r["アクション"]), stop_s, "—"))
    recs.sort(key=lambda x: x[0], reverse=True)
    # 2026-08-14: 「資産」列を廃止した。この列は daily_log の モデル資産（日足2
    # システムだけの旧計算）で、同じ画面上部の モデル資産（全4システム合算・
    # perf.py）と 1,400円ほど食い違い、同じページに違う「資産」が2つ並んで
    # いた。資産の推移は下の「資産推移」カード（perf.equity_curve で統一計算）
    # が正なので、こちらは行動ログに徹する。
    rows_html = ""
    for _, disp, sys_s, act_s, stop_s, _eq_s in recs[:10]:
        rows_html += (f"<tr><td>{html.escape(disp)}</td>"
                      f"<td>{html.escape(sys_s)}</td>"
                      f"<td>{html.escape(act_s)}</td>"
                      f"<td>{html.escape(stop_s)}</td></tr>")
    if not rows_html:
        rows_html = '<tr><td colspan="4">運用開始前（最初の最終確定から記録）</td></tr>'

    # 決済履歴（4システムの確定分を新しい順に。成績KPIの内訳がそのまま見える）
    # 2026-08-14: 決済が成績に反映されているかをボード上で直接確かめられる
    # ようにするため新設（ユーザー指摘への恒久対策）。
    trade_rows = ""
    for _, r in trades.iloc[::-1].head(12).iterrows():
        pnl = float(r["損益円"])
        swp = r["スワップ円"]
        swp_s = "—" if pd.isna(swp) else f"{float(swp):+,.0f}"
        trade_rows += (
            f'<tr><td>{html.escape(str(r["決済"])[:16])}</td>'
            f'<td>{html.escape(sys_short.get(r["システム"], str(r["システム"])))}</td>'
            f'<td>{html.escape(str(r["売買"]))} {int(r["数量"]):,}</td>'
            f'<td>{float(r["建値"]):.3f}→{float(r["決済値"]):.3f}</td>'
            f'<td>{swp_s}</td>'
            f'<td class="{"up" if pnl >= 0 else "down"}">{pnl:+,.0f}</td></tr>')
    if not trade_rows:
        trade_rows = '<tr><td colspan="6">まだ決済はありません</td></tr>'

    if is_flash:
        banner = ('<div class="banner ok">速報配信中——正式な発注判断は'
                  '8時すぎの【最終確定】で</div>')
    elif needs_action:
        banner = ('<div class="banner act">本日は発注作業があります'
                  '（下の指示書どおりに）</div>')
    else:
        banner = '<div class="banner ok">本日の発注作業はありません</div>'

    # 生成元（Mac／GitHub Actions）。ボードがどちらの系で作られたかを一目で判別する
    # （Mac沈黙時はクラウド補完routineがActions生成のHTMLを同一URLへ公開する設計）
    origin = ("クラウド(GitHub Actions)" if os.environ.get("GITHUB_ACTIONS") == "true"
              else "Mac")

    page = f"""<title>FXドル円 運用ボード</title>
<style>
:root {{
  --ground:#F6F8FA; --panel:#FFFFFF; --ink:#1A2B3F; --sub:#5B6B7E;
  --navy:#1F4E79; --gold:#B8901F; --buy:#C0392B; --sell:#2E5FA3;
  --up:#1E7E34; --down:#C0392B; --line:#DFE6EE; --chipbg:#EDF2F7;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --ground:#0E1826; --panel:#16233A; --ink:#E8EEF5; --sub:#93A5B8;
  --navy:#7FA8D0; --gold:#D8B24A; --buy:#E06A5A; --sell:#6E9BD8;
  --up:#5BBF75; --down:#E06A5A; --line:#263850; --chipbg:#1E2E47;
}} }}
:root[data-theme="light"] {{
  --ground:#F6F8FA; --panel:#FFFFFF; --ink:#1A2B3F; --sub:#5B6B7E;
  --navy:#1F4E79; --gold:#B8901F; --buy:#C0392B; --sell:#2E5FA3;
  --up:#1E7E34; --down:#C0392B; --line:#DFE6EE; --chipbg:#EDF2F7;
}}
:root[data-theme="dark"] {{
  --ground:#0E1826; --panel:#16233A; --ink:#E8EEF5; --sub:#93A5B8;
  --navy:#7FA8D0; --gold:#D8B24A; --buy:#E06A5A; --sell:#6E9BD8;
  --up:#5BBF75; --down:#E06A5A; --line:#263850; --chipbg:#1E2E47;
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--ground); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Yu Gothic",sans-serif;
  line-height:1.55; -webkit-text-size-adjust:100%; }}
.wrap {{ max-width:960px; margin:0 auto; padding:16px 14px 48px; }}
header.top {{ display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px;
  padding:6px 2px 2px; }}
header.top h1 {{ font-size:1.15rem; margin:0; letter-spacing:.02em; }}
header.top .rate {{ font-size:1.5rem; font-weight:700;
  font-variant-numeric:tabular-nums; }}
header.top .meta {{ color:var(--sub); font-size:.78rem; width:100%; }}
.banner {{ margin:12px 0; padding:10px 14px; border-radius:10px;
  font-weight:700; font-size:.95rem; color:#fff; }}
.banner.act {{ background:var(--gold); }}
.banner.ok {{ background:var(--navy); }}
.grid {{ display:grid; gap:12px; grid-template-columns:1fr; }}
@media (min-width:700px) {{ .grid {{ grid-template-columns:1fr 1fr; }}
  .grid .span2 {{ grid-column:1/-1; }} }}
.card {{ background:var(--panel); border:1px solid var(--line);
  border-radius:14px; padding:14px 16px; }}
.card h2, .card h3 {{ margin:0; font-size:.95rem; }}
.card-head {{ display:flex; justify-content:space-between; align-items:baseline;
  gap:8px; flex-wrap:wrap; }}
.params {{ color:var(--sub); font-size:.75rem; }}
.action {{ font-size:1.45rem; font-weight:800; margin:8px 0 4px; }}
.action.buy {{ color:var(--buy); }} .action.sell {{ color:var(--sell); }}
.action.gold {{ color:var(--gold); }} .action.flat {{ color:var(--sub); }}
.posline {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap;
  font-size:.9rem; }}
.pos {{ font-weight:700; }} .pos.buy {{ color:var(--buy); }}
.pos.sell {{ color:var(--sell); }} .pos.flat {{ color:var(--sub); }}
.stop {{ color:var(--sub); font-variant-numeric:tabular-nums; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }}
.chip {{ background:var(--chipbg); border-radius:999px; padding:3px 10px;
  font-size:.72rem; color:var(--sub); font-variant-numeric:tabular-nums; }}
.kpis {{ display:grid; gap:10px;
  grid-template-columns:repeat(auto-fit,minmax(112px,1fr)); }}
.kpi .l {{ color:var(--sub); font-size:.72rem; }}
.kpi .v {{ font-size:1.15rem; font-weight:700;
  font-variant-numeric:tabular-nums; }}
.up {{ color:var(--up); }} .down {{ color:var(--down); }}
pre.order {{ margin:8px 0 0; padding:12px; background:var(--chipbg);
  border-radius:10px; font-size:.78rem; line-height:1.5; overflow-x:auto;
  font-family:ui-monospace,"SF Mono",Menlo,monospace; }}
svg {{ width:100%; height:auto; display:block; margin-top:6px; }}
svg .grid {{ stroke:var(--line); stroke-width:1; }}
svg .line {{ fill:none; stroke:var(--navy); stroke-width:2.2; }}
svg .area {{ fill:var(--navy); opacity:.12; stroke:none; }}
svg .dot {{ fill:var(--gold); }}
.tablewrap {{ overflow-x:auto; margin-top:8px; }}
table {{ border-collapse:collapse; width:100%; font-size:.8rem;
  font-variant-numeric:tabular-nums; white-space:nowrap; }}
th, td {{ text-align:left; padding:6px 10px;
  border-bottom:1px solid var(--line); }}
th {{ color:var(--sub); font-weight:600; font-size:.72rem; }}
.note {{ color:var(--sub); font-size:.75rem; margin-top:6px; }}
footer {{ color:var(--sub); font-size:.7rem; margin-top:22px;
  line-height:1.6; }}
</style>
<div class="wrap">
<header class="top">
  <h1>FXドル円 運用ボード</h1>
  <span class="rate">{close:.3f}<small>円</small></span>
  <span class="meta">更新 {datetime.now():%Y-%m-%d %H:%M}（朝6・7・8時台の3回＋デイトレ毎時判定）
   ｜ 生成元: {origin}
   ｜ 運用先: MATRIX TRADER（発注は手動）{delivery}</span>
</header>
{banner}
<div class="grid">
{''.join(sys_cards)}
{rr_card}
<section class="card span2">
  <h3>運用成績（全4システム合算・実現＋含み）</h3>
  <div class="kpis" style="margin-top:10px">
    <div class="kpi"><div class="l">モデル資産</div><div class="v">{equity_txt}</div></div>
    <div class="kpi"><div class="l">累計実現損益</div><div class="v">{cum_html}</div></div>
    <div class="kpi"><div class="l">含み損益（{mark:.3f}円評価）</div><div class="v">{unreal_html}</div></div>
    <div class="kpi"><div class="l">勝率</div><div class="v">{wr}</div></div>
    <div class="kpi"><div class="l">PF</div><div class="v">{pf}</div></div>
    <div class="kpi"><div class="l">決済取引</div><div class="v">{ntr}</div></div>
  </div>
  <div class="chips" style="margin-top:10px">{perf_chips}</div>
  <div class="note">※モデル資産＝口座資金{float(cfg["口座資金_円"]):,.0f}円
    ＋累計実現損益＋含み損益（4システム合算・毎回その場で再計算）。</div>
  {kpi_note}
  {swap_note}
</section>
<section class="card span2">
  <h3>決済履歴（全4システム・新しい順）</h3>
  <div class="tablewrap"><table>
    <tr><th>決済</th><th>系</th><th>売買/数量</th><th>建値→決済値</th>
      <th>スワップ</th><th>損益円</th></tr>
    {trade_rows}
  </table></div>
  <div class="note">※スワップは建玉からのロールオーバー跨ぎ回数で計上
    （JST火〜金の朝{swap.rollover_hour_jst(date.today())}時・木曜は3日分）。
    2026-08-13より前に決済した行は現在のレートでの遡及概算。</div>
</section>
<section class="card span2">
  <h3>{curve_label}</h3>
  {_spark_svg(curve)}
  {curve_note}
</section>
<section class="card span2">
  <h3>本日の注文指示書（最終確定・指標イベント欄含む）</h3>
  <pre class="order">{html.escape(order_text)}</pre>
</section>
<section class="card">
  <h3>直近の記録</h3>
  <div class="tablewrap"><table>
    <tr><th>日付</th><th>系</th><th>アクション</th><th>ストップ</th></tr>
    {rows_html}
  </table></div>
</section>
{fund_html}
<section class="card">
  <h3>研究・進化</h3>
  <p class="note">日次研究: {html.escape(res_msg)}</p>
  <p class="note">週次ペア研究: {html.escape(pair_msg)}</p>
</section>
</div>
<footer>発注はMATRIX TRADERで本人が手動で行う（JFXはEA・自動売買禁止）。逆指値は建玉と必ずセット。
検証値は過去データによるもので将来の成果を保証しない。高重要度指標イベント日は新規建てを自動見送り。
本ページは情報提供であり投資助言ではない。</footer>
</div>
"""
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Webボード生成: {OUT_HTML}")
    return OUT_HTML


if __name__ == "__main__":
    build()
