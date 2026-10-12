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
import events  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = lambda *p: os.path.join(BASE_DIR, *p)  # noqa: E731
OUT_HTML = R("results", "dashboard_web.html")

# 2026-10-12 ユーザー指示: ボードに「更新」「今すぐ分析」ボタンと分析結果欄を設置。
# 分析はアーティファクトの実行時機能（sample＝閲覧者のClaudeに問い合わせ／db＝結果を
# 保存して全端末で共有）で行う。claude.ai の閲覧画面以外では「更新」だけが動く。
# 結果は db の analysis/latest に保存され、ボードを再公開しても消えない。
ANALYSIS_CSS = """
.abar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:8px; }
.abtn { border:0; border-radius:999px; padding:8px 16px; font-weight:700;
  font-size:.9rem; cursor:pointer; color:#fff; background:var(--navy); }
.abtn.gold { background:var(--gold); }
.abtn.ghost { background:var(--chipbg); color:var(--ink); }
.abtn:disabled { opacity:.5; cursor:default; }
.abar select { border:1px solid var(--line); border-radius:8px; padding:6px 8px;
  background:var(--panel); color:var(--ink); font-size:.85rem; }
.astatus { color:var(--sub); font-size:.78rem; margin-top:6px; }
.aout { margin-top:10px; font-size:.9rem; }
.aout h4 { margin:14px 0 4px; font-size:.9rem; color:var(--navy);
  border-left:3px solid var(--gold); padding-left:8px; }
.aout p { margin:4px 0; }
.aout ul { margin:4px 0; padding-left:1.2em; }
.aout li { margin:3px 0; }
.aout .lead { font-size:1rem; font-weight:700; padding:10px 12px;
  background:var(--chipbg); border-radius:10px; }
.aout table td, .aout table th { white-space:normal; vertical-align:top; }
/* 2026-10-12: パターン表の倍率・金額が途中で折り返されて読みにくかった（ユーザー指摘）。
   数字の列は折り返さず、長い説明の列だけ折り返す */
.aout table td:not(:last-child), .aout table th { white-space:nowrap; }
.aout table td:last-child { white-space:normal; min-width:14em; }
.aout .tag { white-space:nowrap; }
/* スキャル発注チケット（2026-10-12）: 側（買/売/見送り）を色帯と大見出しで、価格ははしご図で */
.ticket { margin-top:12px; border:1px solid var(--line); border-radius:12px; overflow:hidden;
  background:var(--panel); }
.ticket .t-head { padding:12px 16px; border-left:6px solid var(--sub); background:var(--chipbg); }
.ticket.buy .t-head { border-left-color:var(--buy); }
.ticket.sell .t-head { border-left-color:var(--sell); }
.t-pair { font-size:.75rem; letter-spacing:.08em; color:var(--sub); font-weight:700; }
.t-do { font-size:1.5rem; font-weight:800; line-height:1.3; }
.ticket.buy .t-do { color:var(--buy); } .ticket.sell .t-do { color:var(--sell); }
.ticket.flat .t-do { color:var(--ink); }
.t-sub { font-size:.82rem; color:var(--sub); margin-top:2px; }
.t-body { padding:12px 16px; display:grid; gap:12px; }
.ladder { max-width:640px; }
.ladder .lv { stroke-width:2; } .ladder .lv.up { stroke:var(--up); } .ladder .lv.down { stroke:var(--down); }
.ladder .lv.ent { stroke:var(--ink); stroke-width:2.5; } .ladder .lv.now { stroke:var(--sub); stroke-dasharray:5 4; }
.ladder .lt { font-size:13px; font-weight:700; fill:var(--sub); }
.ladder .lt.up { fill:var(--up); } .ladder .lt.down { fill:var(--down); } .ladder .lt.ent { fill:var(--ink); }
.ladder .lp { font-size:13px; fill:var(--ink); font-family:ui-monospace,"SF Mono",Menlo,monospace; }
.ladder .zone-up { fill:var(--up); opacity:.10; } .ladder .zone-down { fill:var(--down); opacity:.10; }
.t-kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; }
.t-kpis > div { background:var(--chipbg); border-radius:10px; padding:8px 10px; display:grid; gap:2px; }
.t-kpis span { font-size:.72rem; color:var(--sub); }
.t-kpis b { font-size:1.2rem; font-variant-numeric:tabular-nums; }
.t-kpis small { font-size:.72rem; color:var(--sub); }
.t-steps { margin:0; padding-left:1.4em; display:grid; gap:6px; font-size:.9rem; }
.t-steps li::marker { font-weight:800; color:var(--gold); }
.mono { font-family:ui-monospace,"SF Mono",Menlo,monospace; }
.t-warn { margin:0; padding:8px 12px; border-radius:8px; background:var(--chipbg); color:var(--down); font-weight:700; }
.t-lead { margin:0; font-weight:600; }
.t-trig { margin:0; padding:0; list-style:none; display:grid; gap:6px; }
.t-trig li { padding:8px 12px; border:1px dashed var(--line); border-radius:8px; font-size:.88rem; }
.t-trig li::before { content:"□ "; color:var(--gold); font-weight:800; }
.t-foot { padding:8px 16px; border-top:1px solid var(--line); font-size:.85rem; color:var(--sub); }
.fc-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; margin-top:10px; }
.fc { border:1px solid var(--line); border-radius:10px; padding:8px 10px; display:grid; gap:2px; }
.fc span { font-size:.72rem; color:var(--sub); } .fc b { font-size:1rem; }
.fc.buy b { color:var(--buy); } .fc.sell b { color:var(--sell); } .fc small { font-size:.72rem; color:var(--sub); }
.t-more { margin-top:10px; } .t-more summary { font-weight:700; color:var(--ink); }
.aout .tag { display:inline-block; border-radius:999px; padding:1px 8px;
  font-size:.72rem; font-weight:700; color:#fff; background:var(--sub); }
.aout .tag.now { background:var(--navy); } .aout .tag.more { background:var(--gold); }
.aout .tag.warn { background:var(--down); }
"""

ANALYSIS_CARD = """
<section class="card span2" id="analysis">
  <div class="card-head"><h3>総合分析（Claude・ボタンを押すたびに最新の状況で分析し直す）</h3>
    <span class="params">結果はこのボードに保存され、次に開いたときも表示</span></div>
  <div class="abar">
    <label class="params" for="a-risk">リスク倍率</label>
    <select id="a-risk">
      <option value="1">現状のまま（1倍）</option>
      <option value="0.5">半分（0.5倍）</option>
      <option value="1.5">1.5倍</option>
      <option value="2">2倍</option>
      <option value="2.5">2.5倍</option>
      <option value="3">3倍</option>
      <option value="3.5">3.5倍</option>
      <option value="4">4倍</option>
      <option value="4.5">4.5倍</option>
      <option value="5">5倍</option>
      <option value="6">6倍</option>
      <option value="7">7倍</option>
      <option value="8">8倍</option>
      <option value="9">9倍</option>
      <option value="10">10倍（最大）</option>
    </select>
    <button class="abtn ghost" id="a-reload" type="button">更新（最新のボードを読み込み、発注の数字を倍率で調整）</button>
    <button class="abtn gold" id="a-run" type="button" hidden>今すぐ分析</button>
    <button class="abtn ghost" id="a-stop" type="button" hidden>停止</button>
    <select id="a-depth" hidden>
      <option value="default">標準（1分前後）</option>
      <option value="complex">詳細（数分かかる・より深く考える）</option>
    </select>
  </div>
  <div class="aout" id="a-adj"></div>
  <div class="astatus" id="a-status">読み込み中…</div>
  <div class="aout" id="a-out"></div>
  <div class="note">※分析は「このボードに表示されている数字」だけを材料にした読み物で、
    投資助言ではありません。リスクを増やすパターンは概算の比較であり、変更する・しないは
    ご本人の判断です（変更するときは config.json の調整＝別途依頼）。発注は従来どおり手動。</div>
</section>
"""

SCALP_CARD = """
<section class="card span2" id="scalp">
  <div class="card-head"><h3>スキャルピング専用（高リスク発注欄・ファンダ連動）</h3>
    <span class="params">短時間で小さな値幅を取る超短期売買の案。日足4システムとは別枠・別管理</span></div>
  <div class="abar">
    <label class="params" for="s-pair">通貨ペア</label>
    <select id="s-pair">
      <option value="ドル円">ドル円（USD/JPY）</option>
      <option value="ユーロドル">ユーロドル（EUR/USD）</option>
    </select>
    <label class="params" for="s-risk">1回のリスク</label>
    <select id="s-risk"></select>
    <button class="abtn gold" id="s-run" type="button" hidden>スキャル分析（発注案を作る）</button>
    <button class="abtn ghost" id="s-stop" type="button" hidden>停止</button>
  </div>
  <div class="astatus" id="s-status">読み込み中…</div>
  <div class="aout" id="s-out"></div>
  <div class="aout" id="s-log"></div>
  __SCALP_RESEARCH__
  <div class="note">※ボードの時点のレート・ファンダ・テクニカル合議を材料にした<b>案</b>です。
    実際の発注前に必ず現在値を確認し、案の価格帯から離れていたら見送ってください。
    高リスク設定のため、損失額は日足系の数倍になります。発注・決済はすべてご本人が手動で行い、
    結果はモデル成績には含まれません（別管理）。投資助言ではありません。</div>
</section>
"""

ANALYSIS_JS = r"""
<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var runBtn = $("a-run"), stopBtn = $("a-stop"), depthSel = $("a-depth");
  var status = $("a-status"), out = $("a-out");
  var esc = function (s) { return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };
  var fmtAt = function (iso) {
    try { var d = new Date(iso);
      return (d.getMonth() + 1) + "/" + d.getDate() + " " + d.getHours() + ":" +
        ("0" + d.getMinutes()).slice(-2); } catch (e) { return iso || ""; }
  };
  var list = function (arr) {
    if (!arr || !arr.length) return "";
    return "<ul>" + arr.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
  };
  var tagOf = function (name) {
    var n = String(name || "");
    if (/現状|現在|いま/.test(n)) return "now";
    if (/警戒|危険|注意/.test(n)) return "warn";
    return "more";
  };

  // ── リスク倍率（「更新」で発注の数字を調整して表示）──
  // 数量＝リスク額÷ストップ幅 なので、リスクを k 倍にすると新規建ての数量が k 倍になる
  // （逆指値の水準は ATR で決まり変わらない）。既存建玉の注文変更は数量を変えられない。
  var riskSel = $("a-risk"), adj = $("a-adj");
  var readMult = function () {
    try { var v = parseFloat(localStorage.getItem("fx_risk_mult")); return v > 0 ? v : 1; }
    catch (e) { return 1; }
  };
  var mult = readMult();
  riskSel.value = String(mult);
  if (riskSel.value !== String(mult)) { mult = 1; riskSel.value = "1"; }
  var orderData = null;
  try { var od = document.getElementById("order-data"); if (od) orderData = JSON.parse(od.textContent); }
  catch (e) { orderData = null; }
  var yen = function (n) { return (n < 0 ? "-" : "+") + Math.round(Math.abs(n)).toLocaleString() + "円"; };
  function adjustedRows() {
    var rows = [];
    if (!orderData || !orderData.操作) return rows;
    Object.keys(orderData.操作).forEach(function (sysName) {
      (orderData.操作[sysName] || []).forEach(function (op) {
        var kind = String(op.種別 || "");
        var units = Number(op.数量 || 0);
        var isNew = kind === "新規建て";
        var adjUnits = isNew && units ? Math.max(1000, Math.round(units * mult / 1000) * 1000) : units;
        var sl = op["損切り幅pips_数値"], tp = op["利確幅pips_数値"];
        var price = op.レート != null ? op.レート : op.変更後 != null ? op.変更後 : op.決済逆指値;
        rows.push({ sys: sysName, kind: kind, side: op.売買 || op.対象 || "", units: units, adjUnits: adjUnits,
          isNew: isNew, price: price, stop: op.決済逆指値 != null ? op.決済逆指値 : op.変更後,
          tp: op["決済指値(利確)"], slYen: sl != null && adjUnits ? -sl * 0.01 * adjUnits : null,
          tpYen: tp != null && adjUnits ? tp * 0.01 * adjUnits : null, slPips: sl, tpPips: tp });
      });
    });
    return rows;
  }
  function renderAdjust() {
    var rates = orderData && orderData.リスク率 ? orderData.リスク率 : null;
    var cap = orderData && orderData.口座資金 ? Number(orderData.口座資金) : null;
    var h = "";
    var rateTxt = rates ? Object.keys(rates).map(function (k) {
      return k + " " + (Number(rates[k]) * 100 * mult).toFixed(2).replace(/\.?0+$/, "") + "%"; }).join("／") : "";
    if (mult === 1) {
      h += "<p class='note'>リスク倍率: <b>現状のまま</b>（発注カードの数字をそのまま使う）"
         + (rateTxt ? "　1回のリスク: " + esc(rateTxt) : "") + "</p>";
    } else {
      var rows = adjustedRows();
      h += "<h4>調整後の発注数値（リスク " + mult + "倍" + (cap ? "・口座資金 " + cap.toLocaleString() + "円" : "") + "）</h4>";
      if (rateTxt) h += "<p class='note'>1回のリスク（倍率適用後）: " + esc(rateTxt) + "</p>";
      if (!rows.length) {
        h += "<p class='note'>本日の発注作業はありません（新規建てが出た日に、ここに倍率を掛けた数量が出ます）。</p>";
      } else {
        h += "<div class='tablewrap'><table><tr><th>系</th><th>種別</th><th>売買/対象</th><th>数量（元）</th>"
           + "<th>数量（調整後）</th><th>レート</th><th>到達時の損失（調整後）</th><th>利確時（調整後）</th></tr>";
        rows.forEach(function (r) {
          h += "<tr><td>" + esc(r.sys) + "</td><td>" + esc(r.kind) + "</td><td>" + esc(r.side) + "</td>"
             + "<td>" + (r.units ? r.units.toLocaleString() : "—") + "</td>"
             + "<td>" + (r.isNew ? "<b>" + r.adjUnits.toLocaleString() + "</b> ← この数量で" :
                         (r.units ? r.units.toLocaleString() + "（既存建玉のため変えない）" : "—")) + "</td>"
             + "<td>" + (r.price != null ? Number(r.price).toFixed(3) : "—") + "</td>"
             + "<td class='down'>" + (r.slYen != null ? yen(r.slYen) + "（" + Math.round(r.slPips) + "pips）" : "—") + "</td>"
             + "<td class='up'>" + (r.tpYen != null ? yen(r.tpYen) + "（" + Math.round(r.tpPips) + "pips）" : "—") + "</td></tr>";
        });
        h += "</table></div>";
      }
      h += "<p class='note'>※逆指値・利確の水準は変わらず、新規建ての数量だけが倍率で変わる（1,000通貨単位に丸め）。"
         + "エンジンの建玉管理は現状の数量で追跡するので、倍率で建てた分の差は手動で把握が必要。"
         + "恒久的に変えるなら config.json のリスク率を変更（Claudeに別途依頼）。</p>";
    }
    adj.innerHTML = h;
    syncOrderText();
  }
  // 2026-10-12: 倍率を変えたら「本日の注文指示書」の発注カード（新規建ての数量と到達時の金額）
  // もその場で書き換え、分析欄の調整表と同じ数字にする（発注数指示の同期）。
  // 元の文面は data-orig に残し、1倍に戻せば元に戻る。通知メールはクラウド生成のため元の数量のまま。
  function syncOrderText() {
    document.querySelectorAll("pre.order").forEach(syncOne);
  }
  function syncOne(pre) {
    if (!pre) return;
    if (pre.dataset.orig == null) pre.dataset.orig = pre.textContent;
    var txt = pre.dataset.orig;
    if (mult === 1) { pre.textContent = txt; return; }
    var changed = 0;
    var chunks = txt.split(/(?=［\d+\/\d+］)/).map(function (ch) {
      var head = ch.split("\n")[0] || "";
      if (!/新規建て/.test(head)) return ch;
      var m = ch.match(/数量\s*: ([\d,]+) 通貨/);
      if (!m) return ch;
      var u = parseInt(m[1].replace(/,/g, ""), 10);
      if (!(u > 0)) return ch;
      var a = Math.max(1000, Math.round(u * mult / 1000) * 1000), f = a / u;
      ch = ch.replace(/(数量\s*: )([\d,]+)( 通貨)/,
        "$1" + a.toLocaleString() + "$3   ← リスク" + mult + "倍（元 " + u.toLocaleString() + " 通貨）");
      ch = ch.replace(/／([+-])([\d,]+)円/g, function (_, sgn, n) {
        return "／" + sgn + Math.round(parseInt(n.replace(/,/g, ""), 10) * f).toLocaleString() + "円";
      });
      changed++;
      return ch;
    });
    // 2026-10-12: 各システム見出し（▼…リスク1.0%）のリスク率も倍率後の値に書き換える
    var body = chunks.join("").split("\n").map(function (ln) {
      if (!/^▼/.test(ln)) return ln;
      return ln.replace(/リスク(\d+(?:\.\d+)?)%/, function (_, r) {
        return "リスク" + (parseFloat(r) * mult).toFixed(2).replace(/\.?0+$/, "") + "%（元" + r + "%×" + mult + "倍）";
      });
    }).join("\n");
    pre.textContent = "※リスク倍率 " + mult + "倍 を適用して表示中（新規建ての数量" + changed
      + "件を調整・保有中建玉の数量と逆指値の水準は変わらない・通知メールは元のまま）\n\n" + body;
  }
  // 2026-10-12: 「今すぐ分析」のたびに、倍率適用後の指示書を Google Drive の「指示書」フォルダへ
  // 書き出す（ユーザー指示: 問答無用で全て更新）。フォルダはエンジンが置く *_指示_確定.txt の
  // 親フォルダを検索で特定（IDをリポジトリに書かない）。同じ日付の前回の調整版はゴミ箱へ移して1日1本に保つ。
  var mcpNs = null;
  async function pushDrive(db, headline) {
    if (!mcpNs) return "Drive未接続（この画面ではGoogle Driveを使えません）";
    var day = orderData && orderData.日付 ? orderData.日付 : new Date().toISOString().slice(0, 10);
    var stage = orderData && orderData.段階 ? orderData.段階 : "";
    var now = new Date();
    var stamp = now.getFullYear() + "-" + ("0" + (now.getMonth() + 1)).slice(-2) + "-" + ("0" + now.getDate()).slice(-2)
      + " " + ("0" + now.getHours()).slice(-2) + ":" + ("0" + now.getMinutes()).slice(-2);
    try {
      var found = await mcpNs.callTool("Google Drive", "search_files", {
        query: "title contains '_指示_確定' and title contains '.txt'", excludeContentSnippets: true, pageSize: 5
      }, { cache: false });
      var files = (found.payload && found.payload.files) || [];
      var parent = files.length ? files[0].parentId : null;
      if (!parent) return "Drive指示書フォルダが見つかりません";
      var text = "■ ドル円 注文指示書【リスク倍率調整版】 " + day + "（元: " + stage + "）\n"
        + "  作成: " + stamp + "（ボードの「今すぐ分析」で自動更新）\n"
        + "  リスク倍率: " + mult + "倍" + (mult === 1 ? "（現状のまま）" : "") + "\n"
        + (headline ? "  分析の結論: " + headline + "\n" : "")
        + "  ※エンジンの正本は " + day + "_指示_確定.txt。本書は倍率を新規建ての数量に掛けた転記用。\n\n"
        + Array.prototype.map.call(document.querySelectorAll("pre.order"), function (x) {
            return x.textContent; }).join("\n\n");  // 2026-10-13: ユーロドルの指示書も含める
      var title = day + "_指示_リスク調整版.txt";
      var made = await mcpNs.callTool("Google Drive", "create_file", {
        title: title, parentId: parent, textContent: text,
        contentMimeType: "text/plain", disableConversionToGoogleType: true
      }, { cache: false });
      var p = made.payload || {};
      var newId = p.id || (p.file && p.file.id) || null;
      var ref = db ? db.doc("drive/adjusted") : null;
      if (ref) {
        try {
          var prev = await ref.get();
          var pd = prev.exists ? prev.data() : null;
          if (pd && pd.day === day && pd.id && pd.id !== newId) {
            try { await mcpNs.callTool("Google Drive", "trash_file", { fileId: pd.id }, { cache: false }); } catch (e) {}
          }
          if (newId) await ref.set({ day: day, id: newId, at: now.toISOString(), mult: mult });
        } catch (e) {}
      }
      return "Drive指示書を更新（" + title + "・" + mult + "倍）";
    } catch (e) {
      var why = { needs_reauth: "Google Driveの再接続が必要（claude.ai 設定→コネクタ）",
        server_not_connected: "Google Driveコネクタが未接続（claude.ai 設定→コネクタで追加）",
        not_in_manifest: "Google Driveの利用がこの画面で許可されていません",
        approval_required: "Google Driveへの書き込みが未承認" }[e && e.code];
      return "Drive指示書の更新失敗（" + (why || (e && (e.message || e.code)) || "不明") + "）";
    }
  }
  renderAdjust();
  // スキャル欄の「1回のリスク」選択肢: 1・2・3・5%、以降5%刻みで40%まで（2026-10-12 指示）。
  // 金額は口座資金から計算。10%以上は1回の負けで資金が大きく減るため「危険」を明記。
  (function () {
    var sel = $("s-risk"), capS = orderData && orderData.口座資金 ? Number(orderData.口座資金) : 1000000;
    var rates = [0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40];
    var saved = 0.02;
    try { var v = parseFloat(localStorage.getItem("fx_scalp_risk")); if (rates.indexOf(v) >= 0) saved = v; } catch (e) {}
    rates.forEach(function (r) {
      var o = document.createElement("option");
      o.value = String(r);
      o.textContent = Math.round(r * 100) + "%（" + Math.round(capS * r).toLocaleString() + "円）"
        + (r >= 0.20 ? " ⚠非常に危険" : r >= 0.10 ? " ⚠危険" : "");
      if (r === saved) o.selected = true;
      sel.appendChild(o);
    });
    sel.onchange = function () { try { localStorage.setItem("fx_scalp_risk", sel.value); } catch (e) {} };
  })();
  // 「更新」＝最新のボードを読み込み → 自動で分析 → 分析のおすすめ倍率で発注数量を更新
  $("a-reload").onclick = function () {
    try { localStorage.setItem("fx_risk_mult", riskSel.value);
          localStorage.setItem("fx_auto_analyze", "1"); } catch (e) {}
    location.reload();
  };
  riskSel.onchange = function () {
    try { localStorage.setItem("fx_risk_mult", riskSel.value); } catch (e) {}
    mult = parseFloat(riskSel.value) || 1; renderAdjust();
  };

  function render(doc) {
    if (!doc || !doc.text && !doc.result) {
      out.innerHTML = "<p class='note'>まだ分析はありません。「今すぐ分析」を押してください。</p>";
      return;
    }
    var r = doc.result;
    if (!r) { out.innerHTML = "<p>" + esc(doc.text).replace(/\n/g, "<br>") + "</p>"; return; }
    var h = "";
    if (r.headline) h += "<div class='lead'>" + esc(r.headline) + "</div>";
    if (r.summary) h += "<h4>いまの状況（やさしく）</h4><p>" + esc(r.summary) + "</p>";
    if (r.performance) h += "<h4>収益状況の評価</h4><p>" + esc(r.performance.comment) + "</p>" + list(r.performance.points);
    if (r.fundamental) h += "<h4>ファンダメンタルズを踏まえた見方</h4><p>" + esc(r.fundamental.comment) + "</p>" + list(r.fundamental.points);
    if (r.scenarios && r.scenarios.length) {
      h += "<h4>リスク設定の比較（現状 と リスクを増やす場合）</h4>";
      h += "<div class='tablewrap'><table class='scn'><tr><th>パターン</th><th>1回のリスク</th>"
         + "<th>全ストップ同時到達の損失</th><th>想定の年間リターン</th><th>想定の最大下落</th><th>向き不向き</th></tr>";
      r.scenarios.forEach(function (s) {
        h += "<tr><td><span class='tag " + tagOf(s.name) + "'>" + esc(s.name) + "</span></td>"
           + "<td>" + esc(s.risk).replace(/[／\/]\s*/g, "<br>") + "</td><td>" + esc(s.loss_if_all_stops) + "</td>"
           + "<td>" + esc(s.expected_return) + "</td><td>" + esc(s.expected_drawdown) + "</td>"
           + "<td>" + esc(s.comment) + "</td></tr>";
      });
      h += "</table></div>";
      if (r.scenario_note) h += "<p class='note'>" + esc(r.scenario_note) + "</p>";
    }
    if (r.proposals && r.proposals.length) {
      h += "<h4>改善点・提案（採用するかはご本人の判断）</h4><ul>";
      r.proposals.forEach(function (p) {
        h += "<li><b>" + esc(p.title) + "</b>：" + esc(p.detail)
           + (p.effect ? "<br><span class='note'>期待できる効果: " + esc(p.effect) + "</span>" : "") + "</li>";
      });
      h += "</ul>";
    }
    if (r.direction) h += "<h4>今後の方向性</h4><p>" + esc(r.direction) + "</p>";
    if (r.risks && r.risks.length) h += "<h4>注意すべきリスク</h4>" + list(r.risks);
    if (r.changes_since_last) h += "<h4>前回の分析からの変化</h4><p>" + esc(r.changes_since_last) + "</p>";
    out.innerHTML = h;
  }

  function boardText() {
    var parts = [];
    var head = document.querySelector("header.top");
    if (head) parts.push(head.innerText);
    document.querySelectorAll(".banner").forEach(function (b) { parts.push(b.innerText); });
    document.querySelectorAll(".grid > *").forEach(function (el) {
      if (el.id === "analysis" || el.id === "scalp") return;
      parts.push("■ " + el.innerText.trim());
    });
    var t = parts.join("\n\n").replace(/\n{3,}/g, "\n\n");
    return t.length > 60000 ? t.slice(0, 60000) + "\n…（以下省略）" : t;
  }

  function buildPrompt(prev) {
    var p = [];
    p.push("あなたは個人のFX半自動売買スキーム（2通貨運用・2026-10-13〜）の運用レビュー担当です。ドル円は4システム（長期トレンド／短期スイング／デイトレ複合時間軸／デイトレ15分。ドンチャン・ブレイクアウト＋ATRストップ）、ユーロドルは2システム（押し目戻り＝200日線の向きに沿ったボリンジャー逆張り・トレンド＝移動平均クロス10/50）。発注は本人が手動。ドル円とユーロドルの連動（ドル全面主導か、円・ユーロ単独か）も評価に入れること。");
    p.push("下の【運用ボードの内容】だけを材料に、現状を総合的に分析してください。数字はボードの値をそのまま使い、無い数字は作らず『不明』と書くこと。");
    p.push("読む人はIT・金融の専門家ではありません。専門用語は避けるか一言説明を添え、結論を先に、短い文で書いてください。");
    p.push("必ず含める観点: ①いまの状況のやさしい要約 ②収益状況の評価（累計損益・勝率・PF・システム別の寄与、どこで損をしているか） ③ファンダメンタルズ研究欄（レジーム・材料バランス・翌日予測・焦点・ニュース総括）を踏まえた見方と、それが保有ポジションにどう効くか ④リスク設定の比較: 『現状』（ボードの各システムのリスク率と合計リスク額）と『リスクを増やす場合』の2〜3パターン（例: 現状の1.5倍、2倍）。各パターンの『全ストップ同時到達の損失』『想定の年間リターン』『想定の最大下落』は、現状の値とボードの15年検証値（総リターン・最大DD）を倍率で比例させた概算で示し、概算である旨を書く ⑤改善点・提案（リスクリワード・ストップ幅・リスク率・システムの停止や縮小など。具体的な数値つき。採否は本人） ⑥今後の方向性 ⑦注意すべきリスク（指標イベント・介入・窓開けなど）。");
    p.push("発注の指示（今すぐ買え・売れ）は書かない。投資助言ではなく、本人のシステムの点検レポートとして書く。");
    if (mult !== 1) p.push("閲覧者はいま『リスク倍率 " + mult + "倍』を選んでいる。scenarios には必ずこの倍率のパターンを含め、現状との違いを説明する。");
    if (prev && prev.text) {
      p.push("【前回の分析（" + fmtAt(prev.at) + "・参考）】\n" + String(prev.text).slice(0, 4000));
      p.push("前回からの変化（損益・ポジション・相場環境）があれば changes_since_last に1〜3文で書く。");
    }
    p.push("出力は次のJSONオブジェクトのみ（前後に文章を付けない）:\n" + JSON.stringify({
      headline: "1行の結論（40字以内）",
      summary: "いまの状況のやさしい要約（200字前後）",
      performance: { comment: "収益状況の総評（150字前後）", points: ["箇条書き（3〜5個）"] },
      fundamental: { comment: "ファンダを踏まえた見方（150字前後）", points: ["箇条書き（3〜5個）"] },
      scenarios: [{ name: "現状", risk: "長期1.0%／短期0.5%…のように", loss_if_all_stops: "-◯円（◯%）",
        expected_return: "年+◯%（概算）", expected_drawdown: "-◯%（概算）", comment: "向き不向き・注意（60字以内）" }],
      scenario_note: "比較の前提と概算である旨（100字以内）",
      proposals: [{ title: "提案名", detail: "具体的な内容と数値", effect: "期待できる効果" }],
      direction: "今後の方向性（150字前後）",
      risks: ["注意すべきリスク（3〜5個）"],
      changes_since_last: "前回からの変化（前回が無ければ空文字）",
      recommended_multiplier: "0.5／1／1.5／2／2.5／3／3.5／4／4.5／5／6／7／8／9／10 のいずれか（数値）。いまの成績と相場環境から、新規建ての数量に掛けるおすすめのリスク倍率（成績が悪い・環境が不安定なら小さく、安定していれば大きく）",
      multiplier_reason: "その倍率をすすめる理由（80字以内）"
    }));
    p.push("【運用ボードの内容】\n" + boardText());
    return p.join("\n\n");
  }

  function plain(r) {   // 保存・前回参照用の素テキスト
    try {
      var s = [r.headline, r.summary, r.performance && r.performance.comment,
        r.fundamental && r.fundamental.comment, r.direction].filter(Boolean).join("\n");
      if (r.proposals) s += "\n提案: " + r.proposals.map(function (p) { return p.title; }).join("／");
      return s;
    } catch (e) { return ""; }
  }

  var copyFor = {
    not_granted: "この画面ではClaudeの利用が許可されていません（許可すると使えます）。",
    rate_limited: "呼び出しが多すぎるか利用上限です。しばらくしてからもう一度押してください。",
    refused: "この内容では分析できませんでした。", invalid_json: "結果の形が崩れました。もう一度押してください。",
    upstream_error: "通信が途切れました。もう一度押してください。", session_expired: "サインインし直してください。"
  };

  // 発注一覧・Drive指示書の同期状況を出す行（分析の状況表示とは別。保存時の再描画で消えないように）
  function syncLine(t) {
    var el = $("a-sync");
    if (!el) { el = document.createElement("div"); el.className = "astatus"; el.id = "a-sync";
      status.parentNode.insertBefore(el, status.nextSibling); }
    el.textContent = t;
  }
  var dbRef = null;
  function setup(sample, db) {
    dbRef = db;
    var latestRef = db ? db.doc("analysis/latest") : null;
    var latest = null;
    if (latestRef) {
      latestRef.onSnapshot(function (snap) {
        latest = snap.exists ? snap.data() : null;
        render(latest);
        status.textContent = latest ? "最終分析 " + fmtAt(latest.at) + "（レート " + (latest.rate || "—") + "円・"
          + (latest.tier === "complex" ? "詳細" : "標準") + "）" : "まだ分析はありません。";
      }, function (e) { status.textContent = "保存先に接続できません（" + e.code + "）。分析はこの画面内だけ表示します。"; render(null); });
    } else { render(null); status.textContent = "保存機能なし（この画面内だけ表示します）。"; }
    if (!sample) {
      status.textContent += " ※「今すぐ分析」は claude.ai の閲覧画面でのみ使えます。";
      return;
    }
    runBtn.hidden = false; depthSel.hidden = false;
    var ctl = null, timer = null;
    stopBtn.onclick = function () { if (ctl) ctl.abort(); };
    runBtn.onclick = async function () {
      runBtn.disabled = true; stopBtn.hidden = false;
      // 押した時点の選択倍率を必ず反映（問答無用で発注一覧を更新）
      mult = parseFloat(riskSel.value) || 1;
      try { localStorage.setItem("fx_risk_mult", String(mult)); } catch (e) {}
      renderAdjust();
      var headline = "", cancelled = false;
      syncLine("発注一覧を " + mult + "倍 で更新済み／Drive指示書は分析のあとに更新します…");
      ctl = new AbortController();
      var t0 = Date.now();
      status.textContent = "分析中…（0秒）";
      timer = setInterval(function () {
        status.textContent = "分析中…（" + Math.round((Date.now() - t0) / 1000) + "秒）";
      }, 1000);
      var tier = depthSel.value;
      var rateEl = document.querySelector("header.top .rate");
      var rate = rateEl ? rateEl.textContent.replace(/[^0-9.]/g, "") : "";
      try {
        var result = await sample.json(buildPrompt(latest),
          { modelTier: tier, cache: false, signal: ctl.signal });
        var doc = { result: result, text: plain(result), at: new Date().toISOString(), rate: rate, tier: tier };
        render(doc);
        headline = result && result.headline ? String(result.headline) : "";
        status.textContent = "分析完了 " + fmtAt(doc.at) + "（" + Math.round((Date.now() - t0) / 1000) + "秒）";
        // 分析のおすすめ倍率: 選択が「現状のまま(1倍)」のときだけ自動で適用する。
        // 2026-10-12: 自分で2倍を選んでも分析のたびに戻されていた（ユーザー指摘）ため、
        // 自分で選んだ倍率は上書きせず、おすすめを「適用」ボタンで提示するだけにした。
        var rec = parseFloat(result && result.recommended_multiplier);
        var reason = result && result.multiplier_reason ? "（" + result.multiplier_reason + "）" : "";
        if ([0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6, 7, 8, 9, 10].indexOf(rec) >= 0) {
          var applyRec = function () {
            mult = rec; riskSel.value = String(rec);
            try { localStorage.setItem("fx_risk_mult", String(rec)); } catch (e) {}
            renderAdjust();
          };
          if (rec === mult) {
            status.textContent += "　→ 倍率は " + rec + "倍 のまま（分析も同じ判断）";
          } else if (mult === 1) {
            applyRec();
            status.textContent += "　→ 分析のおすすめにより発注数量を " + rec + "倍 に更新" + reason;
          } else {
            status.innerHTML = esc(status.textContent) + "　分析のおすすめ倍率は <b>" + rec + "倍</b>"
              + esc(reason) + "（いまの選択 " + mult + "倍 はそのまま）"
              + " <button class='abtn ghost' id='a-apply' type='button'>おすすめを適用</button>";
            $("a-apply").onclick = async function () {
              applyRec(); status.textContent = "発注数量を " + rec + "倍 に更新しました";
              syncLine("発注一覧を " + mult + "倍 で更新済み／Drive指示書を更新中…");
              syncLine("発注一覧を " + mult + "倍 で更新済み／" + await pushDrive(dbRef, headline));
            };
          }
        }
        if (latestRef) {
          try { await latestRef.set(doc); }
          catch (e) { status.textContent += " ※保存できませんでした（閲覧のみの権限）。この画面内だけ表示します。"; }
        }
      } catch (e) {
        if (e && e.code === "cancelled") { cancelled = true; status.textContent = "停止しました。"; render(latest); }
        else {
          status.textContent = (copyFor[e && e.code] || "分析に失敗しました（" + (e && e.code) + "）。")
            + (e && e.text ? "" : "");
          if (e && e.text && e.code === "invalid_json") out.innerHTML = "<p>" + esc(e.text).replace(/\n/g, "<br>") + "</p>";
          else render(latest);
        }
      } finally {
        clearInterval(timer); stopBtn.hidden = true;
        // 分析の成否にかかわらず（停止を除く）、倍率適用後の指示書をDriveへ書き出す
        if (!cancelled) {
          syncLine("発注一覧を " + mult + "倍 で更新済み／Drive指示書を更新中…");
          syncLine("発注一覧を " + mult + "倍 で更新済み／" + await pushDrive(dbRef, headline) + "（" + fmtAt(new Date().toISOString()) + "）");
        } else {
          syncLine("発注一覧を " + mult + "倍 で更新済み／停止したためDrive指示書は未更新");
        }
        runBtn.disabled = false;
      }
    };
    // 「更新」直後の自動分析（押した瞬間に最新データで分析→数量更新）
    var auto = false;
    try { auto = localStorage.getItem("fx_auto_analyze") === "1"; localStorage.removeItem("fx_auto_analyze"); }
    catch (e) {}
    if (auto) runBtn.onclick();
  }

  // ── スキャルピング専用（高リスク発注欄）──
  function setupScalp(sample, db) {
    var sRun = $("s-run"), sStop = $("s-stop"), sRisk = $("s-risk");
    var sStatus = $("s-status"), sOut = $("s-out");
    var pairSel = $("s-pair");
    try { var sp = localStorage.getItem("fx_scalp_pair"); if (sp) pairSel.value = sp; } catch (e) {}
    var cap = orderData && orderData.口座資金 ? Number(orderData.口座資金) : 1000000;
    var sdAll = null;
    try { var sde = document.getElementById("scalp-data"); if (sde) sdAll = JSON.parse(sde.textContent); }
    catch (e) { sdAll = null; }
    // 通貨ペアごとの仕様（2026-10-12 ユーロドル追加）。jpy＝1ドル(クォート通貨)あたりの円
    var ujEl = document.querySelector("header.top .rate");
    var ujRate = ujEl ? parseFloat(ujEl.textContent.replace(/[^0-9.]/g, "")) : 158;
    function spec(p) {
      return p === "ユーロドル"
        ? { name: p, code: "EUR/USD", mult: 10000, dec: 5, unit: "ドル", jpy: ujRate, zone: "±0.0015ドル", other: "ドル円" }
        : { name: "ドル円", code: "USD/JPY", mult: 100, dec: 3, unit: "円", jpy: 1, zone: "±0.15円", other: "ユーロドル" };
    }
    var refs = db ? { "ドル円": db.doc("analysis/scalp"), "ユーロドル": db.doc("analysis/scalp_eurusd") } : null;
    var lastBy = { "ドル円": null, "ユーロドル": null };
    var last = null, ref = null, sd = null;
    function selectPair() {
      var p = pairSel.value;
      ref = refs ? refs[p] : null; last = lastBy[p]; sd = sdAll ? sdAll[p] : null;
    }
    selectPair();
    // 案の結果（勝ち・負け・見送った）を記録して勝率を出し、次の案の材料にする
    var sLog = $("s-log"), logCol = db ? db.collection("scalp_log") : null, logs = [];
    function renderLog() {
      var cnt = function (k) { return logs.filter(function (x) { return x.outcome === k; }).length; };
      var w = cnt("勝ち"), l = cnt("負け"), s = cnt("見送った");
      var h = "<p class='note'>これまでの案の記録: 勝ち " + w + "・負け " + l + "・見送った " + s
        + (w + l ? "（勝率 " + Math.round(100 * w / (w + l)) + "%）" : "") + "</p>";
      if (last && last.result && /買|売/.test(String(last.result.verdict || "")) && logCol) {
        var done = logs.some(function (x) { return x.at === last.at; });
        h += done ? "<p class='note'>この案の結果は記録済みです。</p>"
          : "<div class='abar'><span class='params'>この案の結果を記録:</span>"
          + "<button class='abtn ghost' data-oc='勝ち' type='button'>勝ち</button>"
          + "<button class='abtn ghost' data-oc='負け' type='button'>負け</button>"
          + "<button class='abtn ghost' data-oc='見送った' type='button'>見送った</button></div>";
      }
      sLog.innerHTML = h;
      sLog.querySelectorAll("button[data-oc]").forEach(function (b) {
        b.onclick = async function () {
          var r = last.result, id = String(last.at).replace(/[^0-9A-Za-z]/g, "");
          try {
            await logCol.doc(id).set({ at: last.at, outcome: b.dataset.oc, verdict: r.verdict, pair: last.pair || "ドル円",
              entry: r.entry, stop: r.stop, take_profit: r.take_profit, riskRate: last.riskRate,
              recordedAt: new Date().toISOString() });
          } catch (e) { sStatus.textContent = "結果を記録できませんでした（閲覧のみの権限）。"; }
        };
      });
    }
    if (logCol) {
      logCol.orderBy("at", "desc").limit(30).onSnapshot(function (snap) {
        logs = snap.docs.map(function (d) { return d.data(); }); renderLog();
      }, function () {});
    }
    function units(entry, stop, riskRate, S) {
      var d = Math.abs(Number(entry) - Number(stop));
      if (!(d > 0)) return { pips: 0, units: 0, yenPerUnit: 0 };
      var yenPerUnit = d * S.jpy;              // 1通貨あたり、逆指値に当たった時の損失（円）
      var u = Math.floor(cap * riskRate / yenPerUnit / 1000) * 1000;
      return { pips: d * S.mult, units: Math.max(0, u), yenPerUnit: yenPerUnit };
    }
    // 2026-10-12 落合さん指摘「ポジションの持ち方が分からない」→ 発注チケット形式に作り直し。
    // 上から: ①いまやること（大きく） ②価格のはしご（利確・入る値・損切りの位置関係）
    // ③数字4つ ④MATRIX TRADERでの手順（順番どおり） ⑤見送り時は「入る条件」と予想。根拠は折りたたみ。
    function fmtP(x, S) { return x == null || isNaN(Number(x)) ? "—" : Number(x).toFixed(S.dec); }
    function ladder(r, S, ref) {
      var pts = [["利確", r.take_profit, "up"], ["入る値", r.entry, "ent"], ["損切り", r.stop, "down"]]
        .filter(function (x) { return x[1] != null && !isNaN(Number(x[1])); });
      if (ref) pts.push(["分析時の値", ref, "now"]);
      if (pts.length < 2) return "";
      var vals = pts.map(function (x) { return Number(x[1]); });
      var hi = Math.max.apply(null, vals), lo = Math.min.apply(null, vals), span = (hi - lo) || 1;
      var H = 168, top = 14, bot = 14, W = 640;
      var y = function (v) { return top + (hi - v) / span * (H - top - bot); };
      var g = "";
      var e = Number(r.entry), tp = Number(r.take_profit), sl = Number(r.stop);
      if (!isNaN(e) && !isNaN(tp)) g += "<rect x='150' width='300' y='" + Math.min(y(e), y(tp)) + "' height='" + Math.abs(y(e) - y(tp)) + "' class='zone-up'/>";
      if (!isNaN(e) && !isNaN(sl)) g += "<rect x='150' width='300' y='" + Math.min(y(e), y(sl)) + "' height='" + Math.abs(y(e) - y(sl)) + "' class='zone-down'/>";
      pts.forEach(function (x) {
        var yy = y(Number(x[1]));
        var pips = (x[2] === "up" || x[2] === "down") && !isNaN(e)
          ? Math.round(Math.abs(Number(x[1]) - e) * S.mult) + " pips" : "";
        g += "<line x1='150' x2='450' y1='" + yy + "' y2='" + yy + "' class='lv " + x[2] + "'/>"
           + "<text x='140' y='" + (yy + 4) + "' text-anchor='end' class='lt " + x[2] + "'>" + x[0] + "</text>"
           + "<text x='460' y='" + (yy + 4) + "' class='lp'>" + fmtP(x[1], S) + "</text>"
           + (pips ? "<text x='" + (W - 6) + "' y='" + (yy + 4) + "' text-anchor='end' class='lt " + x[2] + "'>" + pips + "</text>" : "");
      });
      return "<svg class='ladder' viewBox='0 0 " + W + " " + H + "' role='img' aria-label='価格の位置関係'>" + g + "</svg>";
    }
    function renderScalp(doc) {
      if (!doc || !doc.result) { sOut.innerHTML = "<p class='note'>まだ案はありません。「スキャル分析」を押してください。</p>"; return; }
      var r = doc.result, h = "";
      var S = spec(doc.pair || "ドル円");
      var v = String(r.verdict || "見送り");
      var side = /買/.test(v) ? 1 : /売/.test(v) ? -1 : 0;
      var cls = side > 0 ? "buy" : side < 0 ? "sell" : "flat";
      var ref = doc.rate ? Number(doc.rate) : null;
      var big = side > 0 ? "買いで入る（ロング）" : side < 0 ? "売りで入る（ショート）" : "今は入らない（見送り）";
      var sub = S.code + "・" + esc(r.headline || "") + (r.confidence ? "・自信 " + esc(r.confidence) : "");
      h += "<div class='ticket " + cls + "'><div class='t-head'><div class='t-pair'>" + S.code + "</div>"
         + "<div class='t-do'>" + big + "</div><div class='t-sub'>" + sub + "</div></div>";
      if (side && r.entry != null && r.stop != null) {
        var u = units(r.entry, r.stop, doc.riskRate || 0.02, S);
        var tpD = r.take_profit != null ? Math.abs(Number(r.take_profit) - Number(r.entry)) : null;
        var slD = Math.abs(Number(r.entry) - Number(r.stop));
        var lossYen = -u.yenPerUnit * u.units, profYen = tpD != null ? tpD * S.jpy * u.units : null;
        var rr = tpD != null && slD ? (tpD / slD) : null;
        // 入り方: 分析時の値より有利な側なら指値、不利な側なら逆指値、ほぼ同じなら成行
        var e = Number(r.entry), eps = 2 / S.mult, how = "成行";
        if (ref != null) {
          if ((side > 0 && e < ref - eps) || (side < 0 && e > ref + eps)) how = "指値";
          else if ((side > 0 && e > ref + eps) || (side < 0 && e < ref - eps)) how = "逆指値";
        }
        h += "<div class='t-body'>" + ladder(r, S, ref)
           + "<div class='t-kpis'>"
           + "<div><span>数量</span><b>" + u.units.toLocaleString() + "</b><small>通貨（" + (u.units / 1000) + "Lot）</small></div>"
           + "<div><span>損切りなら</span><b class='down'>" + yen(lossYen) + "</b><small>" + Math.round(slD * S.mult) + " pips</small></div>"
           + "<div><span>利確なら</span><b class='up'>" + (profYen != null ? yen(profYen) : "—") + "</b><small>" + (tpD != null ? Math.round(tpD * S.mult) + " pips" : "利確なし") + "</small></div>"
           + "<div><span>損益比</span><b>" + (rr != null ? "1 : " + rr.toFixed(2) : "—") + "</b><small>損1に対し益</small></div>"
           + "</div>";
        if (u.units === 0) h += "<p class='t-warn'>損切り幅に対してリスク額が小さく1,000通貨未満になるため、この案は発注できません。1回のリスクを上げるか見送ってください。</p>";
        h += "<ol class='t-steps'>"
           + "<li>MATRIX TRADER で通貨ペアを <b>" + S.code + "</b> にする</li>"
           + "<li>新規注文 →「<b>IFO</b>」タブ（新規と利確・損切りを1回で出す注文）</li>"
           + "<li>新規: <b>" + (side > 0 ? "買" : "売") + "</b>・数量 <b>" + u.units.toLocaleString() + "</b>・<b>" + how + "</b>"
           + (how === "成行" ? "（いまの値がおおむね " + fmtP(r.entry, S) + " 付近なら）" : " <b class='mono'>" + fmtP(r.entry, S) + "</b>") + "</li>"
           + "<li>決済（OCO）: 指値（利確） <b class='mono up'>" + fmtP(r.take_profit, S) + "</b> ／ 逆指値（損切り） <b class='mono down'>" + fmtP(r.stop, S) + "</b></li>"
           + "<li>有効期限: <b>" + esc(r.valid_until || "当日中") + "</b>。それまでに約定しなければ注文を取り消す</li>"
           + "<li>約定したら触らない（利確か損切りで自動的に終わる）。現在値が " + fmtP(ref, S) + " から "
           + esc(r.max_slippage || "10pips") + " 以上離れていたら、発注せず見送る</li>"
           + "</ol></div>";
      } else if (side) {
        h += "<p class='t-warn'>価格が出なかったため発注案になりません。もう一度「スキャル分析」を押してください。</p>";
      } else {
        h += "<div class='t-body'><p class='t-lead'>条件がそろっていないため、いまは建てません。次の条件がそろったら案に変わります。</p>";
        if (r.entry_triggers && r.entry_triggers.length) {
          h += "<ul class='t-trig'>" + r.entry_triggers.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>";
        }
        h += "</div>";
      }
      if (r.next_check) h += "<div class='t-foot'>次に見る時刻: <b>" + esc(r.next_check) + "</b></div>";
      h += "</div>";
      // 内部予想（コンパクト表示: 矢印＋確率）
      if (r.forecast && r.forecast.length) {
        h += "<div class='fc-row'>" + r.forecast.map(function (f) {
          var d = String(f.direction || "");
          var c = /上|買|円安/.test(d) ? "buy" : /下|売|円高/.test(d) ? "sell" : "flat";
          var arrow = c === "buy" ? "↑" : c === "sell" ? "↓" : "→";
          return "<div class='fc " + c + "' title='" + esc(f.basis || "") + "'><span>" + esc(f.timeframe) + "</span><b>" + arrow + " " + esc(d) + "</b>"
               + "<small>" + esc(f.probability || "") + "・" + esc(f.range || "") + "</small></div>";
        }).join("") + "</div>";
      }
      if (r.checklist && r.checklist.length) h += "<h4>発注前チェック</h4>" + list(r.checklist);
      var more = "";
      if (r.reason) more += "<h4>根拠（テクニカル）</h4>" + list(r.reason);
      if (r.fundamental) more += "<h4>ファンダの読み</h4>" + list(r.fundamental);
      if (r.forecast && r.forecast.length) more += "<h4>予想の根拠</h4>" + list(r.forecast.map(function (f) { return f.timeframe + ": " + (f.basis || ""); }));
      if (r.other_pairs && r.other_pairs.length) more += "<h4>他の通貨ペア（方向のみ）</h4>" + list(r.other_pairs.map(function (p) { return p.pair + " " + p.bias + "：" + p.why; }));
      if (r.caution) more += "<h4>注意</h4>" + list(r.caution);
      if (more) h += "<details class='t-more'><summary>根拠・ファンダ・注意を開く</summary>" + more + "</details>";
      sOut.innerHTML = h;
    }
    function showPair() {
      selectPair(); renderScalp(last); renderLog();
      sStatus.textContent = last ? pairSel.value + "の最終案 " + fmtAt(last.at) + "（レート " + (last.rate || "—")
        + "・リスク " + Math.round((last.riskRate || 0) * 100) + "%）" : pairSel.value + "の案はまだありません。";
    }
    pairSel.onchange = function () { try { localStorage.setItem("fx_scalp_pair", pairSel.value); } catch (e) {} showPair(); };
    if (refs) {
      Object.keys(refs).forEach(function (p) {
        refs[p].onSnapshot(function (snap) {
          lastBy[p] = snap.exists ? snap.data() : null;
          if (p === pairSel.value) showPair();
        }, function (e) { sStatus.textContent = "保存先に接続できません（" + e.code + "）。"; });
      });
    } else { renderScalp(null); sStatus.textContent = "保存機能なし（この画面内だけ表示します）。"; }
    if (!sample) { sStatus.textContent += " ※「スキャル分析」は claude.ai の閲覧画面でのみ使えます。"; return; }
    sRun.hidden = false;
    var ctl = null, timer = null;
    sStop.onclick = function () { if (ctl) ctl.abort(); };
    sRun.onclick = async function () {
      sRun.disabled = true; sStop.hidden = false; ctl = new AbortController();
      var t0 = Date.now(); sStatus.textContent = "スキャル分析中…（0秒）";
      timer = setInterval(function () { sStatus.textContent = "スキャル分析中…（" + Math.round((Date.now() - t0) / 1000) + "秒）"; }, 1000);
      var riskRate = parseFloat(sRisk.value) || 0.02;
      selectPair();
      var S = spec(pairSel.value), runPair = pairSel.value, runRef = ref;
      var rate = sd && sd.値動き && sd.値動き["15分"] ? String(sd.値動き["15分"].終値) : "";
      var p = [];
      p.push("対象の通貨ペアは【" + S.name + "（" + S.code + "）】。価格は小数" + S.dec + "桁・単位は" + S.unit
        + "・1pip＝" + (S.mult === 100 ? "0.01円" : "0.0001ドル") + "。相手の通貨（" + S.other
        + "）はドルの動きを確認する材料として使う（ボードに両方の値動き・研究が載っている）。");
      if (S.name === "ユーロドル") p.push("ユーロドルはドル円より短い足の優位性が小さい（15分足はPF1未満）。案を出してよいのは、スキャル研究の各時間足の上位条件のうち前半・後半ともPF1.1以上のもの（例: 4時間足『EMA20押し目/戻り・EMA200と同方向』、30分足『RSI行き過ぎの戻り・EMA200と同方向』）に合う時だけ。合わなければ見送り。ECB・ユーロ圏CPIなどユーロ側の★5級も避ける。");
      p.push("あなたは" + S.name + "のスキャルピング（数分〜数十分で5〜20pips程度を狙う超短期売買）の案を作る担当です。下の【運用ボードの内容】だけを材料にします。数字はボードの値を使い、無い数字は作らず『不明』と書くこと。");
      p.push("読む人は専門家ではありません。専門用語は避け、短い文で。これは本人のツール内の案であり投資助言ではない。");
      p.push("判断材料: テクニカル合議（15分足・1時間足）、ATR、直近の値動き・高値安値・節目（ファンダ研究欄の『次の焦点』にある節目）、ファンダメンタルズ（レジーム・材料バランス・翌日予測・ニュース総括・当局のけん制・指標イベント）。指標発表の前後30分や★5級イベント直前なら『見送り』。材料が拮抗し方向が読めないときも『見送り』を選ぶ（無理に案を出さない）。");
      p.push("価格は『直近の値動き』の最終バー終値（あれば。無ければボードのヘッダのレート）を基準レートにし、エントリーは基準レートから" + S.zone + "以内、損切りは12pips前後（8〜16）、利確は損切りの1.2〜1.5倍で具体的な小数" + S.dec + "桁の数値を出す。数量は書かない（ページが口座資金とリスク率から計算する）。");
      p.push("判断の決まり（過去の検証に寄せる）: ①『スキャル研究』の各時間足の上位条件（入り方・EMA200と同方向か・損切り/利確のATR倍率・時間帯）に、『直近の値動き』のその足の RSI14／EMA20とEMA50の配列／EMA200の向き／いまの時刻（JST）が合うときだけ案を出す。PFが1.0未満の足は案を出さない。②合わなければ必ず『見送り』。③『避ける時間帯』・指標の前後30分・★5級イベント接近時も見送り。④『これまでの案と結果』で負けが続いている型（同じ時間帯・同じ向き）は避ける。⑤案を出すときは reason に『時間足=◯・RSI=◯・EMA200の向き=◯・時間帯=◯時台（研究の勝率◯%）』と数字で根拠を書く。損切り/利確はその足の『損切りpips目安／利確pips目安』を使う。");
      p.push("内部予想（見送りでも必ず）: forecast に 15分・1時間・4時間・日足 の4行を入れ、各行で『方向（上／下／横ばい）・確率（%）・想定レンジ（円）・根拠（RSI・EMA配列・ATR・節目・ファンダを数字で）』を書く。確率は50%を中立とし、材料が無いときは50〜55%に留める。entry_triggers には『何が起きたら買い／売りの案に変わるか』を価格と時刻で2〜4個、next_check には次に見るべき時刻（JST）を書く。");
      if (sd && sd.研究) p.push("【スキャル研究（短い足から日足まで・時間帯幅1/2/3/6時間・JSON）】\n" + JSON.stringify(sd.研究).slice(0, 24000));
      if (sd && sd.値動き) p.push("【直近の値動き（15分・30分・1時間・4時間・日足の指標と直近バー。時刻はJST）】\n" + JSON.stringify(sd.値動き).slice(0, 16000));
      if (logs.length) p.push("【これまでの案と結果（新しい順・最大10件）】\n" + JSON.stringify(logs.slice(0, 10).map(function (x) {
        return { 通貨: x.pair || "ドル円", 時刻: x.at, 売買: x.verdict, 結果: x.outcome, エントリー: x.entry, 損切り: x.stop, 利確: x.take_profit }; })));
      p.push("他の通貨ペア（ユーロ円・ポンド円・豪ドル円・" + S.other + "）については、ここでは数値は書かず、ファンダ（ドル全面主導かどうか等）から『円安寄り／円高寄り／中立』の方向だけを一言で。");
      p.push("出力は次のJSONオブジェクトのみ:\n" + JSON.stringify({
        verdict: "買／売／見送り", confidence: "高／中／低", headline: "1行の結論（40字以内）",
        reference_rate: "基準にしたレートと時点", entry: 158.123, entry_zone: "158.10〜158.15 のように",
        stop: 158.0, take_profit: 158.3, valid_until: "何時まで有効か（例: 本日12:00まで／次の1時間足確定まで）",
        max_slippage: "10pips", reason: ["テクニカルの根拠（2〜4個）"], fundamental: ["ファンダの根拠（2〜4個）"],
        checklist: ["発注前に確認すること（2〜4個）"],
        forecast: [{ timeframe: "15分", direction: "上／下／横ばい", probability: "55%", range: "158.10〜158.30", basis: "数字で根拠" }],
        entry_triggers: ["例: 15分足で157.95を終値で割り、RSI14が30未満に落ちて1時間足EMA200が下向きなら売り案に変わる"],
        next_check: "次に見るべき時刻（JST）",
        other_pairs: [{ pair: "ユーロ円", bias: "円安寄り", why: "一言" }], caution: ["注意（2〜4個）"]
      }));
      p.push("【運用ボードの内容】\n" + boardText());
      try {
        var result = await sample.json(p.join("\n\n"), { modelTier: "default", cache: false, signal: ctl.signal });
        var doc = { result: result, at: new Date().toISOString(), rate: rate, riskRate: riskRate, pair: runPair };
        lastBy[runPair] = doc;
        if (pairSel.value === runPair) renderScalp(doc);
        sStatus.textContent = "案を更新 " + fmtAt(doc.at) + "（" + Math.round((Date.now() - t0) / 1000) + "秒・リスク " + Math.round(riskRate * 100) + "%）";
        if (runRef) { try { await runRef.set(doc); } catch (e) { sStatus.textContent += " ※保存できませんでした（閲覧のみの権限）。"; } }
      } catch (e) {
        if (e && e.code === "cancelled") { sStatus.textContent = "停止しました。"; renderScalp(last); }
        else { sStatus.textContent = copyFor[e && e.code] || "分析に失敗しました（" + (e && e.code) + "）。"; renderScalp(last); }
      } finally { clearInterval(timer); sRun.disabled = false; sStop.hidden = true; }
    };
  }

  if (!window.claude || typeof window.claude.use !== "function") {
    status.textContent = "この画面では「更新」のみ使えます（分析は claude.ai の閲覧画面で）。";
    $("s-status").textContent = "スキャル分析は claude.ai の閲覧画面でのみ使えます。";
    render(null);
    return;
  }
  Promise.all([claude.use("sample"), claude.use("db"), claude.use("mcp")]).then(function (r) {
    mcpNs = r[2];
    setup(r[0], r[1]);
    setupScalp(r[0], r[1]);
  });
})();
</script>
"""

ACT_STYLE = {  # アクション → (表示, クラス)
    "新規買(ロング)": ("新規 買", "buy"), "新規売(ショート)": ("新規 売", "sell"),
    "ストップ変更": ("ストップ切上げ", "gold"), "決済": ("全決済", "gold"),
    "継続": ("保有継続", "flat"), "待機": ("待機", "flat"),
    "イベント見送り": ("イベント見送り", "flat"),
    "ファンダ見送り": ("ファンダ見送り", "flat"),
    "ストップ決済確認": ("ストップ決済済", "gold"),
}


def _load_fund():
    """ファンダ研究JSONとニュースを読む。戻り値 (fu, news)。無ければ (None, {})。

    ニュースは fundamental_news.json を直接参照する。定量研究→ニュース更新の
    順で走った回は latest 埋め込み分が1世代古くなるため、更新時刻が新しい方を
    採用する（レジーム・予測は latest のまま）。
    """
    fpath = R("results", "fundamental_latest.json")
    if not os.path.exists(fpath):
        return None, {}
    try:
        fu = json.load(open(fpath, encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return None, {}
    news = (fu or {}).get("ニュース") or {}
    npath = R("results", "fundamental_news.json")
    if os.path.exists(npath):
        try:
            n_direct = json.load(open(npath, encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            n_direct = None
        if n_direct and (str(n_direct.get("更新") or "") >
                         str(news.get("更新") or "")):
            news = n_direct
    return fu, news


def _fund_balance(news):
    """ニュース材料を重要度で重み付けし、円安／円高どちらが優勢かを数える。

    2026-09-21 ユーザー指示「ボードに研究中のファンダも取り入れる」で新設。
    ボード上の材料バランス棒と各建玉カードの追い風／向かい風チップは、必ず
    この関数1本から出す（出力先ごとに数えると片方だけ直し残る）。
    発注判定には使わない＝表示専用。
    """
    up = down = both = 0
    for t in (news or {}).get("テーマ") or []:
        try:
            w = int(t.get("重要度") or 3)
        except (TypeError, ValueError):
            w = 3
        imp = str(t.get("含意") or "")
        if "両" in imp:
            both += w
        elif "円安" in imp:
            up += w
        elif "円高" in imp:
            down += w
    total = up + down + both
    if not total:
        return None
    net = (up - down) / total
    label = ("円安材料が優勢" if net >= 0.2 else
             "円高材料が優勢" if net <= -0.2 else "材料は拮抗")
    return {"円安": up, "円高": down, "両方向": both, "net": net, "判定": label}


def _fund_chip(pos, bal):
    """建玉の向きとファンダ材料バランスの相性チップ（参考表示・発注不使用）"""
    if not pos or not bal:
        return ""
    if abs(bal["net"]) < 0.2:
        txt = "ファンダ 中立（材料拮抗）"
    elif (pos > 0) == (bal["net"] > 0):
        txt = "ファンダ 追い風"
    else:
        txt = "ファンダ 向かい風"
    return f'<span class="chip">{txt}<small>・参考</small></span>'


def _intraday_block(latest_path, state_path, name, label, stale_hours=3):
    """デイトレの最新指示を指示書欄に併記する文面。古い指示は「現況」に差し替える。"""
    if not os.path.exists(latest_path):
        return ""
    body = open(latest_path, encoding="utf-8").read().strip()
    head = "\n\n" + "=" * 46 + f"\n▼ {name} {label}\n" + "=" * 46 + "\n"
    try:
        st = json.load(open(state_path, encoding="utf-8"))
        upd = datetime.fromisoformat(str(st.get("updated", ""))[:16])
        m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", body.split("\n", 1)[0])
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M") if m else None
    except Exception:  # noqa: BLE001
        return head + body
    if ts is None or (upd - ts).total_seconds() < stale_hours * 3600:
        return head + body
    s0 = st.get("state", {}) or {}
    pos = int(s0.get("position", 0) or 0)
    lines = [f"■ ドル円 {name} 現況（最終判定 {upd:%Y-%m-%d %H:%M}・確定バー {st.get('last_bar', '—')}）"]
    if pos == 0:
        lines.append("【待機】ノーポジ。新しい指示なし。発注不要。")
    else:
        side = "買" if pos > 0 else "売"
        def f3(v):
            return f"{float(v):.3f}円" if v not in (None, "") else "—"
        lines.append(f"【保有継続】{side} {int(s0.get('units', 0) or 0):,}通貨　建値 {f3(s0.get('entry'))}"
                     f"　決済逆指値 {f3(s0.get('stop'))}　利確指値 {f3(s0.get('tp'))}")
        lines.append("  新しい指示なし。発注不要（MATRIX TRADER上の逆指値・利確が上の値と一致しているかだけ確認）。")
    lines.append(f"  ※直近の指示（{ts:%Y-%m-%d %H:%M}）は{stale_hours}時間以上前のもので処理済みの前提。"
                 "転記用の発注カードは出さない（全文は当時の通知メール）。")
    return head + "\n".join(lines)


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
        tags = re.findall(r"【(確認|決済|見送り|変更|継続|待機|新規)】", block)
        if not tags:
            continue
        if "新規" in tags:
            side = re.search(r"【新規】.*?(買|売)", block, re.S)
            if not side:
                continue
            label = f"新規 {side.group(1)}"
            if "確認" in tags:
                # 前日ストップ到達の決済確認と新規建てが同じ朝に重なるケース。
                # 確認タグだけを拾うと「ストップ決済済」のまま古い状態が
                # 表示され、下の建玉行（新規ポジション）と矛盾して見えるため
                # 両方を明示する。
                label = f"ストップ決済→{label}"
            out[name] = (label, "buy" if side.group(1) == "買" else "sell")
        elif tags[0] == "見送り" and "ファンダ本運用ルール" in block:
            out[name] = ("ファンダ見送り", "flat")
        else:
            out[name] = label_map[tags[0]]
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


def _scalp_snapshot(pair="usdjpy"):
    """スキャル分析に渡す「直近の値動き」を短い足から長期まで（15分・30分・1時間・
    4時間・日足）そろえる。各足の RSI14・EMA20/50/200・EMA200の向き・ATR・直近バー。
    日足終値しか材料が無いと超短期の案も内部予想も作れない（2026-10-12）。"""
    try:
        import numpy as np                                   # noqa: PLC0415
        mult, dec = (10000, 5) if pair == "eurusd" else (100, 3)
        m15 = pd.read_csv(R("data", f"{pair}_15m.csv"))
        m15["Time"] = pd.to_datetime(m15["Time"], utc=True).dt.tz_convert("Asia/Tokyo")
        m15 = m15.set_index("Time").sort_index()[["Open", "High", "Low", "Close"]].astype(float)
        h1 = pd.read_csv(R("data", f"{pair}_1h.csv"))
        h1["Time"] = pd.to_datetime(h1["Time"], utc=True).dt.tz_convert("Asia/Tokyo")
        h1 = h1.set_index("Time").sort_index()[["Open", "High", "Low", "Close"]].astype(float)
        dly = pd.read_csv(R("data", f"{pair}_daily.csv"))
        dly["Date"] = pd.to_datetime(dly["Date"])
        dly = dly.set_index("Date").sort_index()[["Open", "High", "Low", "Close"]].astype(float)
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
        frames = {
            "15分": (m15.tail(600), 24), "30分": (m15.resample("30min", label="left", closed="left").agg(agg).dropna().tail(600), 16),
            "1時間": (h1.tail(600), 12), "4時間": (h1.resample("4h", label="left", closed="left").agg(agg).dropna().tail(600), 12),
            "日足": (dly.tail(400), 10),
        }
        out = {}
        for name, (df, nbars) in frames.items():
            c = df["Close"]
            d = c.diff()
            up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
            e20, e50, e200 = (c.ewm(span=n, adjust=False).mean() for n in (20, 50, 200))
            tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                            (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14).mean() * mult
            fmt = "%m/%d" if name == "日足" else "%m/%d %H:%M"
            out[name] = {
                "最終バー(JST)": f"{df.index[-1]:{fmt}}", "終値": round(float(c.iloc[-1]), dec),
                "RSI14": round(float(rsi.iloc[-1]), 1),
                "EMA20": round(float(e20.iloc[-1]), dec), "EMA50": round(float(e50.iloc[-1]), dec),
                "EMA200": round(float(e200.iloc[-1]), dec),
                "EMA200の向き": "上" if e200.iloc[-1] > e200.iloc[-5] else "下",
                "EMA20とEMA50": "上昇配列" if e20.iloc[-1] > e50.iloc[-1] else "下降配列",
                "ATR14pips": round(float(atr.iloc[-1]), 1),
                "直近高値": round(float(df["High"].tail(nbars).max()), dec),
                "直近安値": round(float(df["Low"].tail(nbars).min()), dec),
                "直近バー": [{"t": f"{t:{fmt}}", "o": round(r.Open, dec), "h": round(r.High, dec),
                            "l": round(r.Low, dec), "c": round(r.Close, dec)}
                           for t, r in df.tail(nbars).iterrows()],
            }
        last = m15.index[-1]
        today = m15[m15.index.date == last.date()]
        prev_days = m15[m15.index.date < last.date()]
        prev = prev_days[prev_days.index.date == prev_days.index.date[-1]] if len(prev_days) else today
        out["共通"] = {"最終15分バー(JST)": f"{last:%Y-%m-%d %H:%M}",
                     "当日高値": round(float(today["High"].max()), dec), "当日安値": round(float(today["Low"].min()), dec),
                     "前日高値": round(float(prev["High"].max()), dec), "前日安値": round(float(prev["Low"].min()), dec)}
        return out
    except Exception as e:                                   # データ欠損時は材料なしで続行
        return {"エラー": str(e)[:120]}


def _scalp_research_html(sr, pair_name="ドル円"):
    if not sr:
        return (f'<p class="note">{pair_name}のスキャル研究はまだありません'
                '（python3 src/research_scalp.py で作成）。</p>')
    rows, cross = "", ""
    other = "ユーロドル" if pair_name == "ドル円" else "ドル円"
    for tf, rep in (sr.get("時間足別") or {}).items():
        for c in (rep.get("相手通貨で確認した場合") or [])[:1]:
            mark = "<b>改善</b>" if c.get("改善") else "変化小・悪化"
            cross += (f'<tr><td>{html.escape(tf)}</td><td>{c["元の勝率"]:.0%}→{c["確認後の勝率"]:.0%}</td>'
                      f'<td>{c["元のPF"]}→{c["確認後のPF"]}</td><td>{c["元の回数"]}→{c["確認後の回数"]}</td>'
                      f'<td>{mark}</td></tr>')
    for tf, rep in (sr.get("時間足別") or {}).items():
        top = (rep.get("上位条件") or [])
        if not top:
            rows += f'<tr><td>{html.escape(tf)}</td><td colspan="6">該当条件なし</td></tr>'
            continue
        x = top[0]
        good = "／".join(rep.get("狙い目の時間帯") or []) or "差なし"
        bad = "／".join(rep.get("避ける時間帯") or []) or "差なし"
        rows += (f'<tr><td><b>{html.escape(tf)}</b></td><td>{html.escape(str(x["条件"]))}</td>'
                 f'<td>{x["勝率"]:.0%}</td><td>{x["PF"]}（{x["PF前"]}/{x["PF後"]}）</td>'
                 f'<td>{x["取引数"]}</td><td>{x["損切りpips目安"] or "—"}／{x["利確pips目安"] or "—"}</td>'
                 f'<td>狙い目: {html.escape(good)}<br>避ける: {html.escape(bad)}</td></tr>')
    return (
        f'<h4>{pair_name}のスキャル研究（短い足から日足まで×時間帯幅1/2/3/6時間で検証・'
        f'{html.escape(str(sr.get("更新", ""))[:10])}）</h4>'
        f'<p class="note"><b>{html.escape(str(sr.get("結論", "")))}</b></p>'
        f'<div class="tablewrap"><table><tr><th>時間足</th><th>最も安定した入り方</th><th>勝率</th>'
        f'<th>PF（前半/後半）</th><th>回数</th><th>損切り/利確pips目安</th><th>時間帯</th></tr>'
        f'{rows}</table></div>'
        f'<p class="note">※{html.escape(str(sr.get("期間", "")))}・{html.escape(str(sr.get("前提", "")))}。'
        f'PFが1.0前後の足は、機械的に入っても往復コストでほぼ利益が残らない＝その足では'
        f'「見送り」が基本。各足の上位6条件と時間帯幅別の勝率表は「スキャル分析」が読んでいる。</p>'
        + (f'<p class="note"><b>{other}の向きで確認した場合</b>（ドルが両通貨で同じ向きに動いている時だけ入る・'
           f'各足の最良条件）</p><div class="tablewrap"><table><tr><th>時間足</th><th>勝率</th><th>PF</th>'
           f'<th>回数</th><th>評価</th></tr>{cross}</table></div>' if cross else ""))


def _eurusd_section(cfg):
    """ユーロドル（2通貨目・2026-10-12〜）の表示一式。
    戻り値: (ヘッダ用レート文字列, 運用カードHTML, 研究カードHTML, 指示書テキスト, 指示書ファイル名)"""
    ecfg = cfg.get("ユーロドル") or {}
    if not ecfg.get("有効"):
        return "", "", "", "", ""
    try:
        import data_fetch_eurusd as dfe                      # noqa: PLC0415
        import eurusd_engine as eng                          # noqa: PLC0415
        from strategies import make_signal                   # noqa: PLC0415
        px = dfe.load_daily()
        close = float(px["Close"].iloc[-1])
        jpy = eng.usdjpy_now()
        sw = eng.swap_cfg(cfg)
    except Exception as e:                                   # noqa: BLE001
        return "", (f'<section class="card span2"><h3>ユーロドル</h3><p class="note">'
                    f'データを読めません（{html.escape(str(e)[:120])}）</p></section>'), "", "", ""
    state = {}
    if os.path.exists(R("state_eurusd.json")):
        state = json.load(open(R("state_eurusd.json"), encoding="utf-8"))
    # 本日の指示書（確定 > 速報2 > 速報1）
    odir = R("results", "orders")
    text, fname = "", ""
    if os.path.isdir(odir):
        fs = sorted(f for f in os.listdir(odir) if "_ユーロドル指示_" in f)
        if fs:
            day = max(f[:10] for f in fs)
            rank = {"速報1": 1, "速報2": 2, "確定": 3}
            todays = sorted((f for f in fs if f.startswith(day)),
                            key=lambda f: rank.get(f.rsplit("_", 1)[-1].replace(".txt", ""), 0))
            fname = todays[-1]
            text = open(os.path.join(odir, fname), encoding="utf-8").read().strip()
    acts = {k.replace("ユーロドル ", ""): v for k, v in _parse_order_actions(
        text, [f"ユーロドル {n}" for n in (ecfg.get("システム") or {})]).items()} if text else {}
    a_pips = float(atr(px).iloc[-1]) / eng.PIP
    cards, tot_risk, tot_pnl = [], 0.0, 0.0
    for name, scfg in (ecfg.get("システム") or {}).items():
        if not scfg.get("有効", True):
            continue
        st = state.get(name, {})
        pos = int(st.get("position", 0) or 0)
        act, cls = acts.get(name, ("指示書待ち" if not text else "待機", "flat"))
        units = float(st.get("units") or 0)
        pos_html = ('<span class="pos flat">ポジションなし</span>' if not pos else
                    f'<span class="pos {"buy" if pos > 0 else "sell"}">{"買" if pos > 0 else "売"} {units:,.0f}通貨</span>')
        stop_html = "ストップ設定なし"
        if pos and st.get("stop"):
            stop, entry = float(st["stop"]), float(st.get("entry") or close)
            w = risk.order_widths(pos, close, stop=stop, units=units, entry=entry, pip=eng.PIP, jpy=jpy)
            ppl = units * (close - entry) * pos * jpy
            sy, sd = swap.accrued(pos, units, st.get("entry_date"), sw=sw)
            tot_pnl += ppl + sy
            tot_risk += (w["損切り到達額円"] or 0) + sy
            stop_html = (f'ストップ <b>{stop:.5f}</b>　現値から逆指値まで {w["損切り幅pips"]:.0f} pips'
                         f'　到達なら {w["損切り到達額円"] + sy:+,.0f}円'
                         f'　含み {ppl + sy:+,.0f}円（スワップ {sy:+,.0f}円・{sd}日）')
        exit_txt = html.escape(eng.exit_line_text(scfg, px, pos).strip()) if pos else ""
        v = (scfg.get("検証") or {})
        cards.append(
            f'<article class="card"><div class="card-head"><h2>ユーロドル {html.escape(name)}</h2>'
            f'<span class="params">{html.escape(eng.describe(scfg))}・リスク{float(scfg["リスク率"]):.1%}</span></div>'
            f'<div class="action {cls}">{html.escape(act)}</div>'
            f'<div class="posline">{pos_html}<span class="stop">{stop_html}</span></div>'
            + (f'<div class="posline"><span class="stop">{exit_txt}</span></div>' if exit_txt else "")
            + f'<div class="note">※損益はドル建てをドル円{jpy:.2f}円で円換算。発注は MATRIX TRADER で'
              f'<b>通貨ペアを EUR/USD に切り替えて</b>手動で。</div></article>')
    sr = {}
    if os.path.exists(R("results", "research_eurusd_latest.json")):
        try:
            sr = json.load(open(R("results", "research_eurusd_latest.json"), encoding="utf-8"))
        except (OSError, ValueError):
            sr = {}
    risk_line = (f'<p class="note">ユーロドル建玉の合計: 含み {tot_pnl:+,.0f}円／全ストップ到達で {tot_risk:+,.0f}円'
                 f'（ドル円の「リスクリワード管理」とは別計上）</p>') if state else ""
    op_html = (f'<section class="card span2"><div class="card-head"><h3>ユーロドル（2通貨目・2026-10-13 稼働）</h3>'
               f'<span class="params">終値 {close:.5f}ドル・ATR {a_pips:.0f}pips・ドル円 {jpy:.2f}円</span></div>'
               f'<div class="grid" style="margin-top:8px">{"".join(cards)}</div>{risk_line}</section>')
    # 研究カード
    res_html = ""
    if sr:
        rows = ""
        for nm, v in (sr.get("選定パラメータ") or {}).items():
            t = v.get("検証", {})
            rows += (f'<tr><td>{html.escape(nm)}</td><td>{t.get("勝率", 0):.0%}</td><td>{t.get("PF全")}</td>'
                     f'<td>{t.get("PF前")}/{t.get("PF後")}</td><td>{t.get("最大DD", 0):.1%}</td>'
                     f'<td>{t.get("取引数")}</td></tr>')
        cfg_rows = ""
        for nm, scfg in (ecfg.get("システム") or {}).items():
            cfg_rows += f'<li><b>{html.escape(nm)}</b>: {html.escape(eng.describe(scfg))}・リスク{float(scfg["リスク率"]):.1%}</li>'
        rel = sr.get("ドル円との関係") or {}
        corr = rel.get("日次相関") or {}
        share = rel.get("主役の割合(直近60日)") or {}
        lead = rel.get("1時間足の先行遅行(相関)") or {}
        syn = sr.get("相乗効果(ドル円日足×ユーロドル確認)") or {}
        syn_rows = ""
        for nm, v in syn.items():
            base = v["比較"].get("現行", {})
            best = max((vv for kk, vv in v["比較"].items() if kk != "現行"), key=lambda x: x["スコア"])
            syn_rows += (f'<li>{html.escape(nm)}: 現行 スコア{base.get("スコア")}（PF{base.get("PF全")}・{base.get("取引数")}回）'
                         f' → ユーロドルで確認 最良 スコア{best.get("スコア")}（PF{best.get("PF全")}・{best.get("取引数")}回）'
                         f'＝{html.escape(v.get("判定", ""))}</li>')
        dj = (sr.get("ドンチャン(ドル円と同じ型)の上位") or {}).get("長期") or [{}]
        res_html = (
            f'<section class="card span2"><div class="card-head"><h3>ユーロドル研究（日足15年・ドル円との関係）</h3>'
            f'<span class="params">{html.escape(str(sr.get("更新", ""))[:16])}・{html.escape(str(sr.get("期間", "")))}</span></div>'
            f'<h4 class="fh">採用中の売買ルール（落合さん承認 2026-10-12）</h4><ul class="focus">{cfg_rows}</ul>'
            f'<div class="tablewrap"><table><tr><th>システム</th><th>勝率</th><th>PF</th><th>PF前半/後半</th>'
            f'<th>最大下落</th><th>回数</th></tr>{rows}</table></div>'
            f'<p class="note">※ドル円型のドンチャン（トレンド追随）はユーロドルでは後半（2019年〜）PF{dj[0].get("PF後", "—")}'
            f'で負け越すため不採用。ユーロドルは「長期線の向きに沿った押し目・戻り」が効く通貨。</p>'
            f'<h4 class="fh">ドル円との関係（2大通貨の連動）</h4>'
            f'<ul class="focus"><li>日次の値動きの相関: 全期間 {corr.get("全期間")}／直近250日 {corr.get("直近250日")}／直近60日 {corr.get("直近60日")}'
            f'（マイナス＝ドルが両方の通貨で同時に動く。−0.5前後は「ドルの強弱が共通の主役」の状態）</li>'
            f'<li>直近60日の主役: ドル全面主導 {share.get("ドル全面主導", 0):.0%}／円主導 {share.get("円主導", 0):.0%}'
            f'／ユーロ主導 {share.get("ユーロ主導", 0):.0%}／小動き {share.get("小動き・その他", 0):.0%}　最新日: <b>{html.escape(str(rel.get("最近の主役", "")))}</b></li>'
            f'<li>1時間足の先行: ユーロドル1本前→ドル円 {lead.get("ユーロドル1本前→ドル円")}／ドル円1本前→ユーロドル {lead.get("ドル円1本前→ユーロドル")}'
            f'（ほぼ0＝どちらかが先に動いて他方を予告する関係は無い。同時に動く）</li></ul>'
            f'<h4 class="fh">相乗効果の検証（ドル円の日足売買をユーロドルの向きで確認したら）</h4><ul class="focus">{syn_rows}</ul>'
            f'<p class="note">※採用基準（スコア1.05倍以上）に届かないため現行のまま。毎朝の研究で監視を続ける。'
            f'スキャルでの相乗効果はスキャルピング欄の「相手通貨で確認した場合」を参照。</p></section>')
    head = f'<span class="rate" style="font-size:1.1rem">EUR/USD {close:.5f}</span>'
    return head, op_html, res_html, text, fname


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
    order_json = None
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
            # 2026-10-12: 発注カードの機械可読版を埋め込み、ボード上の「更新」で
            # リスク倍率を掛けた数量・損失額を表示できるようにする（表示専用）
            oj = os.path.join(odir, pick.replace("_指示_", "_発注_")
                              .replace(".txt", ".json"))
            if os.path.exists(oj):
                try:
                    order_json = json.load(open(oj, encoding="utf-8"))
                except (OSError, ValueError):
                    order_json = None
    if order_json is not None:
        order_json["リスク率"] = {k: float(v.get("リスク率", 0))
                                  for k, v in systems.items()}
        for key, name in (("デイトレ", "デイトレ複合時間軸"),
                          ("デイトレ15分", "デイトレ15分")):
            if cfg.get(key, {}).get("リスク率") is not None:
                order_json["リスク率"][name] = float(cfg[key]["リスク率"])
        order_json["口座資金"] = float(cfg["口座資金_円"])
        order_data_html = ('<script type="application/json" id="order-data">'
                           + json.dumps(order_json, ensure_ascii=False)
                           .replace("</", "<\\/") + "</script>")
    else:
        order_data_html = ""
    # スキャル欄の材料: 過去検証の要約＋直近の値動き（2026-10-12）
    def _rj(name):
        try:
            return json.load(open(R("results", name), encoding="utf-8"))
        except (OSError, ValueError):
            return None
    scalp_research = _rj("research_scalp_latest.json")
    scalp_research_eu = _rj("research_scalp_eurusd_latest.json")
    eu_head, eu_op_html, eu_res_html, eu_text, eu_fname = _eurusd_section(cfg)
    eu_order_html = (f'<section class="card span2"><h3>本日のユーロドル注文指示書'
                     f'（{"最終確定" if "確定" in eu_fname else "速報・参考"}）</h3>'
                     f'<pre class="order">{html.escape(eu_text)}</pre></section>') if eu_text else ""
    scalp_card = SCALP_CARD.replace("__SCALP_RESEARCH__",
                                    _scalp_research_html(scalp_research, "ドル円")
                                    + _scalp_research_html(scalp_research_eu, "ユーロドル"))
    scalp_data_html = ('<script type="application/json" id="scalp-data">'
                       + json.dumps({"ドル円": {"研究": scalp_research, "値動き": _scalp_snapshot("usdjpy")},
                                     "ユーロドル": {"研究": scalp_research_eu,
                                               "値動き": _scalp_snapshot("eurusd")}},
                                    ensure_ascii=False).replace("</", "<\\/")
                       + "</script>")
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
        # 2026-09-22: 上の自前計算は「指示書ファイルのある直近10日」を数えるため、
        # 土曜の部分生成（例: 9/12は速報①のみ）が分母に入り、ラッパ(run_morning)が
        # 30/30=100%と出す日でもボードだけ 28/30 ⚠欠落あり と表示されていた。
        # 完成率の定義は run_morning.completion_rate（平日のみ・当日は到達済み段階
        # まで）に一本化する。取得失敗時のみ上の自前計算にフォールバック。
        try:
            from run_morning import completion_rate as _completion_rate
            _rows, _got, _exp = _completion_rate(10)
            if _exp:
                got, exp = _got, _exp
                days = [r[0] for r in _rows]
        except Exception:
            pass
        # 2026-08-14: 警告印を完成率の直後にまとめて出していたため、
        # 完成率 29/29（100%）でも遅延だけで「⚠要点検」が付き、
        # 欠落があるように読めた。遅延と欠落は別々の位置に出す。
        delivery = ("<br>本日の配信 " + " ／ ".join(parts)
                    + ("　⚠配信遅延あり" if warn else "")
                    + f"　｜ 完成率(直近{len(days)}営業日) {got}/{exp}"
                    + ("　⚠欠落あり" if got < exp else ""))

    # デイトレの最新指示を同じ指示書欄に併記（4システムを1枚で確認できるように）。
    # 2026-10-13: *_latest.txt は通知があった回だけ書き換わるため、何日も前の
    # 「決済」「要確認」カードが最新指示として残り、転記の誤りを招いていた（ユーザー指摘）。
    # エンジンの最終判定より3時間以上古い指示は、状態ファイルから作った「現況」に差し替える。
    order_text += _intraday_block(R("results", "intraday_latest.txt"), R("state_intraday.json"),
                                  "デイトレ複合時間軸", "最新指示（毎時更新）")
    order_text += _intraday_block(R("results", "intraday15_latest.txt"), R("state_intraday15.json"),
                                  "デイトレ15分", "最新指示（15分ごと更新）")

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
    sys_cards, needs_action, action_count = [], False, 0
    # ファンダ研究は先に1回だけ読む（各カードの追い風／向かい風チップと
    # 下のファンダカードで同じ材料バランスを使うため）
    fu, fnews = _load_fund()
    fbal = _fund_balance(fnews)
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
            needs_action, action_count = True, action_count + 1
        _new_today = pos != 0 and st.get("entry_date") == date.today().isoformat()
        _new_tag = '<span class="chip" style="margin-left:2px">本日新規</span>' if _new_today else ''
        pos_html = (f'<span class="pos buy">買 {int(st.get("units", 0)):,}通貨</span>{_new_tag}'
                    if pos > 0 else
                    f'<span class="pos sell">売 {int(st.get("units", 0)):,}通貨</span>{_new_tag}'
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
            needs_action, action_count = True, action_count + 1
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
    {_fund_chip(pos, fbal)}
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
            needs_action, action_count = True, action_count + 1
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
  <div class="chips">{chips}{_fund_chip(dpos, fbal)}</div>
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
            needs_action, action_count = True, action_count + 1
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
  <div class="chips">{chips15}{_fund_chip(p15, fbal)}</div>
</article>""")

    # リスクリワード管理カード（2026-08-14 ユーザー指示で新設）
    # 「いま各建玉がいくら失いうるか／いくら取りにいけるか」を1枚に集約する。
    # 数字は risk.py だけが計算し、ボード側は並べるだけにする（出力先ごとに
    # 計算を書くと片方だけ直し残る＝memory feedback_fix_all_output_targets）。
    sys_short = {"長期トレンド": "長期", "短期スイング": "短期",
                 "デイトレ複合時間軸": "デイ", "デイトレ15分": "15分",
                 "ユーロドル押し目戻り": "EU押目", "ユーロドルトレンド": "EUトレ"}
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
        {pr["合計含み損益円"]:+,.0f}円</div>
      <div class="l"><small>@@UNREAL_UNIFIED@@</small></div></div>
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
    # 2026-08-19: 同じ画面にRRカード（各系の最終確定バー評価）と運用成績
    # （全建玉を日足終値で統一評価）の2つの含み損益が並び、基準の違いで数百円
    # ずれるため「どちらかが誤り」に見えていた。RRカード側に統一評価額を併記して
    # 別基準の同一計算であることを示す。値は unreal を再利用し二重計算しない。
    rr_card = rr_card.replace(
        "@@UNREAL_UNIFIED@@",
        (f"日足終値{mark:,.3f}円で統一評価すると {unreal:+,.0f}円"
         f"（下の運用成績と一致）") if open_pos else "")
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
    # 2026-08-20 表示研究: モデル資産の「開始資金からいくら増減したか」が
    # 資産推移グラフの注記まで読まないと分からなかった。KPIの直下に
    # 増減額と率を出す（開始資金は cfg 一箇所から取り、二重計算しない）。
    _eq_base = float(cfg["口座資金_円"])
    _eq_diff = equity_now - _eq_base
    _eq_cls = "up" if _eq_diff >= 0 else "down"
    equity_sub = (f'<div class="l"><small>開始 {_eq_base:,.0f}円 → '
                  f'<span class="{_eq_cls}">{_eq_diff:+,.0f}円'
                  f'（{_eq_diff / _eq_base:+.2%}）</span></small></div>')

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
    # ファンダ・フィルタ検証（research_fund_filter.py・2026-09-21 ユーザー指示）
    ff_msg = "未実行（次回の日次研究から）"
    ff_path = R("results", "fund_filter_latest.json")
    if os.path.exists(ff_path):
        try:
            ff = json.load(open(ff_path, encoding="utf-8"))
            ff_msg = "（" + str(ff.get("更新", ""))[:10] + "）" + " ／ ".join(
                f"{k}: {v.get('判定', '')}"
                for k, v in (ff.get("システム") or {}).items())
        except Exception:                                        # noqa: BLE001
            pass
    pair_msg = "初回の土曜9時に実行されます"
    if len(pair_log):
        p = pair_log.iloc[-1]
        pair_msg = f"{p['実行日']}: {p['判定']}"

    # ファンダ・予測研究（research_fundamental.py が書き出すJSON）
    fund_html = ""
    fund_stale_days = None  # 2026-09-09: 停止警告を上部バナーにも出すため保持
    if fu:      # 冒頭の _load_fund() で読込済み（fu=定量研究／fnews=ニュース）
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
        if fwd.get("高信頼件数"):
            fwd_txt += (f"（高信頼 {fwd['高信頼件数']}件・"
                        f"{fwd.get('高信頼的中率', 0):.1%}）")
        # ── ニュース研究の取り込み（2026-09-21 ユーザー指示で拡充）──
        # 従来は総括＋見出し5件だけで、研究が毎回書いている「保有ポジションへの
        # 含意」「次の焦点」「重要度」がボードに出ていなかった。読込と材料
        # バランスは冒頭の _load_fund()/_fund_balance() の結果をそのまま使う。
        news = fnews
        bal_html = ""
        if fbal:
            seg = "".join(
                f'<span style="flex:{max(v, 0.001)};background:var({c})"></span>'
                for v, c in ((fbal["円高"], "--sell"), (fbal["両方向"], "--line"),
                             (fbal["円安"], "--buy")))
            bal_html = (
                f'<div class="fbar-head"><b>{html.escape(fbal["判定"])}</b>'
                f'<span class="stop">材料の重み（重要度の合計）　'
                f'円高 {fbal["円高"]}／両方向 {fbal["両方向"]}／円安 {fbal["円安"]}'
                f'</span></div><div class="fbar">{seg}</div>'
                '<div class="fbar-legend"><span>← 円高（ドル安）</span>'
                '<span>円安（ドル高）→</span></div>')
        impl = str(news.get("保有ポジションへの含意") or "").strip()
        impl_html = (f'<div class="fbox"><div class="l">保有ポジションへの含意</div>'
                     f'{html.escape(impl)}</div>' if impl else "")
        _focus_raw = news.get("次の焦点") or []
        if isinstance(_focus_raw, str):
            # fundamental_news.json は「①...②...③...」を1本の文字列で持つ形式
            # （fundamental_latest.json はリスト形式）。文字列のまま for に
            # かけると1文字ずつ<li>化されてしまう既知バグ（2026-09-24発見）
            # ＝丸数字の直前で分割してリスト化する。
            _focus_raw = [s.strip() for s in
                          re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])", _focus_raw) if s.strip()]
        focus = [str(x) for x in _focus_raw if str(x).strip()]
        focus_html = ""
        if focus:
            lis = "".join(f"<li>{html.escape(x)}</li>" for x in focus[:6])
            more = "".join(f"<li>{html.escape(x)}</li>" for x in focus[6:])
            focus_html = (f'<h4 class="fh">次の焦点</h4><ul class="focus">{lis}</ul>'
                          + (f'<details><summary>残り{len(focus) - 6}件</summary>'
                             f'<ul class="focus">{more}</ul></details>'
                             if more else ""))

        def _theme_row(t):
            try:
                w = int(t.get("重要度") or 0)
            except (TypeError, ValueError):
                w = 0
            stars = "★" * w if w else "—"
            imp = str(t.get("含意", ""))
            cls = ("buy" if "円安" in imp and "両" not in imp else
                   "sell" if "円高" in imp and "両" not in imp else "flat")
            # スマホで表だと見出しが横に切れるため、縦並びのブロックにする
            return (f'<div class="theme"><div class="theme-meta">'
                    f'<span class="gold">{stars}</span>'
                    f'<span class="pos {cls}">{html.escape(imp)}</span></div>'
                    f'<div class="theme-h">{html.escape(str(t.get("見出し", "")))}</div>'
                    f'<details><summary>要旨</summary>'
                    f'{html.escape(str(t.get("要旨", "")))}</details></div>')

        # 2026-10-12: ファンダ研究タスクが書く「スキャル指針」（避ける時間帯・狙い目・節目・
        # 方向バイアス）を表示。スキャル分析のプロンプトはボード本文経由でこれを読む。
        guide_html = ""
        # 2026-10-12: ユーロドル用の指針（スキャル指針_ユーロドル）と、ユーロドルのファンダ要約も表示
        for key, ttl in (("スキャル指針", "スキャル指針・ドル円"), ("スキャル指針_ユーロドル", "スキャル指針・ユーロドル")):
            g = news.get(key)
            if not (isinstance(g, dict) and g):
                continue
            parts = []
            for k in ("方向バイアス", "狙い目", "避ける時間帯", "節目", "一言"):
                v = g.get(k)
                if v:
                    vs = "／".join(str(x) for x in v) if isinstance(v, list) else str(v)
                    parts.append(f"<b>{html.escape(k)}</b>: {html.escape(vs)}")
            if parts:
                guide_html += (f'<div class="fbox"><div class="l">{ttl}（ファンダ研究が3時間おきに更新）</div>'
                               + "<br>".join(parts) + "</div>")
        eu_f = news.get("ユーロドル")
        if isinstance(eu_f, dict) and eu_f:
            parts = [f"<b>{html.escape(k)}</b>: {html.escape(chr(12289).join(map(str, v)) if isinstance(v, list) else str(v))}"
                     for k, v in eu_f.items() if v]
            guide_html += ('<div class="fbox"><div class="l">ユーロドルのファンダ（ECB・ユーロ圏・ドルとの関係）</div>'
                           + "<br>".join(parts) + "</div>")
        themes = news.get("テーマ") or []
        items = "".join(_theme_row(t) for t in themes[:8])
        news_upd = str(news.get("更新") or "")
        summary = str(news.get("総括") or "")
        news_html = (
            f'{impl_html}{focus_html}{guide_html}'
            f'<h4 class="fh">材料一覧（新しい順・上位8件／全{len(themes)}件）</h4>'
            f'<div class="themes">{items}</div>'
            f'<details><summary>ニュース総括の全文'
            f'{"（更新 " + html.escape(news_upd) + "）" if news_upd else ""}'
            f'</summary><p class="note">{html.escape(summary)}</p></details>'
            if items else '<p class="note">ニュース未収集</p>')
        stale_html = ""
        fu_upd = str(fu.get("更新") or "")
        try:
            age_h = (datetime.now() - datetime.fromisoformat(fu_upd)) \
                .total_seconds() / 3600
            if age_h > 20:
                fund_stale_days = age_h / 24
                stale_html = (
                    '<div class="banner act">⚠ ファンダ研究が'
                    f'{fund_stale_days:.1f}日更新されていません'
                    '（3時間おき更新の想定・専任タスクの停止を確認してください）</div>')
        except (ValueError, TypeError):
            pass
        try:
            import fund_live                                 # noqa: PLC0415
            live_html = "<br>".join(html.escape(x.strip())
                                    for x in fund_live.status_lines())
        except Exception as e:                               # noqa: BLE001
            live_html = html.escape(f"状況の取得に失敗（{type(e).__name__}）")
        fund_html = f"""
<section class="card span2">
  {stale_html}
  <div class="card-head"><h3>ファンダメンタルズ研究（3時間おき更新・本運用は自動昇格方式）</h3>
    <span class="params">定量 {html.escape(str(fu.get("更新", "")))[:16].replace("T", " ")}\
{"／ニュース " + html.escape(news_upd)[:16].replace("T", " ") if news_upd else ""}</span></div>
  <div class="action flat" style="font-size:1.1rem">\
{html.escape(str(fu.get("レジーム", "")))}</div>
  <div class="chips">{corr_chips}</div>
  {bal_html}
  <div class="posline" style="margin-top:10px">{fc_html}</div>
  <p class="note">{html.escape(wf_txt)}／{html.escape(fwd_txt)}</p>
  <div class="fbox"><div class="l">ファンダ本運用（売買判定への自動組込み）</div>{live_html}</div>
  <p class="note">※検証ゲートを3営業日連続で通過したルールだけを自動で新規建ての判定
    （見送り・数量）に使い、3営業日連続で不通過なら自動で外す。方向予測は的中率が
    「常に上昇」を上回ることも条件。発注は従来どおり本人が手動で行う。</p>
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
        banner = (f'<div class="banner act">本日は発注作業があります'
                  f'（{action_count}件・下の指示書どおりに）</div>')
    else:
        banner = '<div class="banner ok">本日の発注作業はありません</div>'
    # 2026-09-09: ファンダ研究の停止警告が下部セクションに埋もれて気づきにくい
    # ため、上部バナー直後にも短い注意を出す（詳細は下のファンダ・予測研究欄）
    if fund_stale_days is not None:
        banner += (
            f'<div class="banner act">⚠ ファンダ研究停止 '
            f'{fund_stale_days:.0f}日（詳細は下部）</div>')
    # 2026-10-02: 本日の★5級指標イベントは下部の指示書<pre>に埋もれていて
    # 一目で気づけなかった（NFP等・新規建て自動見送りの根拠）。上部バナーにも
    # 直近1件を出す（取得失敗時は何も出さない＝フェイルオープンを維持）。
    try:
        ev = events.upcoming_events(24)
    except Exception:
        ev = None
    if ev:
        # 日本語名が付く(=知名度の高い)イベントを優先して見せる。同時刻に
        # 複数ある場合は件数も添えて「他にもある」ことが分かるようにする。
        jst, _cur, title, jp = sorted(ev, key=lambda e: e[3] == "")[0]
        label = jp or title
        extra = f"ほか{len(ev) - 1}件" if len(ev) > 1 else ""
        banner += (
            f'<div class="banner act">⚠ 今後24時間に★5級指標 '
            f'{jst:%m/%d %H:%M} {html.escape(label)}{extra}'
            f'（新規建ては全系見送り）</div>')

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
/* 2026-09-21: 指示書preの長い1行がカードを押し広げ、スマホでページ全体が
   横スクロールになっていた（幅7,000px超）。グリッド子要素の最小幅を0にする */
.grid > * {{ min-width:0; }}
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
.fbar-head {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
  margin-top:12px; font-size:.9rem; }}
.fbar {{ display:flex; height:10px; border-radius:999px; overflow:hidden;
  margin-top:6px; gap:2px; }}
.fbar-legend {{ display:flex; justify-content:space-between;
  color:var(--sub); font-size:.7rem; margin-top:3px; }}
.fbox {{ margin-top:12px; padding:10px 12px; border-left:3px solid var(--gold);
  background:var(--chipbg); border-radius:0 10px 10px 0; font-size:.82rem; }}
.fbox .l {{ color:var(--sub); font-size:.72rem; font-weight:700;
  margin-bottom:2px; }}
h4.fh {{ margin:14px 0 4px; font-size:.82rem; color:var(--sub); }}
ul.focus {{ margin:0; padding-left:1.2em; font-size:.8rem; }}
ul.focus li {{ margin:3px 0; }}
.theme {{ padding:8px 0; border-bottom:1px solid var(--line); }}
.theme-meta {{ display:flex; gap:10px; align-items:baseline; font-size:.75rem; }}
.theme-meta .gold {{ color:var(--gold); letter-spacing:.05em; }}
.theme-h {{ font-size:.82rem; font-weight:600; margin-top:2px; }}
details {{ margin-top:6px; font-size:.78rem; color:var(--sub); }}
details summary {{ cursor:pointer; }}
footer {{ color:var(--sub); font-size:.7rem; margin-top:22px;
  line-height:1.6; }}
{ANALYSIS_CSS}
</style>
<div class="wrap">
<header class="top">
  <h1>FXドル円 運用ボード</h1>
  <span class="rate">{close:.3f}<small>円</small></span>
  {eu_head}
  <span class="meta">更新 {datetime.now():%Y-%m-%d %H:%M}（朝6・7・8時台の3回＋デイトレ毎時判定）
   ｜ 生成元: {origin}
   ｜ 運用先: MATRIX TRADER（発注は手動）{delivery}</span>
</header>
{banner}
<div class="grid">
{ANALYSIS_CARD}
{scalp_card}
{''.join(sys_cards)}
{eu_op_html}
{rr_card}
{fund_html}
<section class="card span2">
  <h3>運用成績（全4システム合算・実現＋含み）</h3>
  <div class="kpis" style="margin-top:10px">
    <div class="kpi"><div class="l">モデル資産</div><div class="v">{equity_txt}</div>
      {equity_sub}</div>
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
  <h3>本日の注文指示書（{"速報・参考" if is_flash else "最終確定"}・指標イベント欄含む）</h3>
  <pre class="order">{html.escape(order_text)}</pre>
</section>
{eu_order_html}
{eu_res_html}
<section class="card">
  <h3>直近の記録</h3>
  <div class="tablewrap"><table>
    <tr><th>日付</th><th>系</th><th>アクション</th><th>ストップ</th></tr>
    {rows_html}
  </table></div>
</section>
<section class="card">
  <h3>研究・進化</h3>
  <p class="note">日次研究: {html.escape(res_msg)}</p>
  <p class="note">ファンダ・フィルタ検証（マクロで新規を絞ると成績は上がるか・発注には未使用）: {html.escape(ff_msg)}</p>
  <p class="note">週次ペア研究: {html.escape(pair_msg)}</p>
</section>
</div>
<footer>発注はMATRIX TRADERで本人が手動で行う（JFXはEA・自動売買禁止）。逆指値は建玉と必ずセット。
検証値は過去データによるもので将来の成果を保証しない。高重要度指標イベント日は新規建てを自動見送り。
本ページは情報提供であり投資助言ではない。</footer>
</div>
{order_data_html}
{scalp_data_html}
{ANALYSIS_JS}
"""
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Webボード生成: {OUT_HTML}")
    return OUT_HTML


if __name__ == "__main__":
    build()
