# -*- coding: utf-8 -*-
"""モバイル運用ボード(HTML)生成 — Mac/iPhone/iPad共通のWebダッシュボード

平日朝の速報①(6時台)・速報②(7時台)・最終確定(8時台)の全ステージで
results/dashboard_web.html を再生成する(エンジンが毎回呼ぶ)。
速報段階は「速報配信中」バナーを表示し、正式判断は最終確定である旨を明示する。
公開(アーティファクト更新)は朝のスケジュールタスク内のClaudeが行う。
HTMLは自己完結（外部は Google Fonts の数字用書体CSSだけ。読めなくても標準書体で表示）・
ライト/ダーク両テーマ・モバイルファースト。
2026-10-13 ビジュアル改修: 上から「今日の発注→指示書の原文→ポジションとリスク→分析・スキャル→
ファンダ→成績→記録・研究」の順。カードはすべて .grid の直下（boardText＝AI分析の材料の約束）。
二次的な長文は .clamp（高さ制限・innerText には残る）で畳み、<details> で新しく畳むのは #scalp 内だけ。
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

# 2026-10-13 ユーザー指示「ボード全体のビジュアルも改善」: ページ全体のCSS。
# 普通の文字列（f-string でも r 文字列でもない）なので波かっこは1重・バックスラッシュは書かない。
# 色は4系統を混ぜない＝方向（買＝朱／売＝藍）・結果（益＝緑／損＝紅）・要対応（琥珀の塗り＋濃い文字）・
# 構造（紺・灰）。白抜き文字は使わない（明暗どちらのテーマでも文字のコントラスト比4.5以上）。
BOARD_CSS = """
/* ===== トークン（明るい値は素の :root。暗い値は :not([data-theme="light"]) で守る） ===== */
:root {
  color-scheme: light;
  --ground:#F2F5F8; --panel:#FFFFFF; --raised:#F6F8FA; --chipbg:#EBF0F5;
  --line:#D5DDE6; --line-strong:#8393A6;
  --ink:#13233A; --sub:#4A596B;
  --navy:#1F4E79; --gold:#875E00;
  --act:#F2B73A; --act-ink:#2A1D00; --act-bg:#FFF6DC;
  --buy:#B53A0A; --buy-bg:#FDEEE6; --sell:#1D5FB0; --sell-bg:#E8F0FB;
  --up:#12763A; --down:#B4173F;
  --info-bg:#E6EFF8; --info-ink:#173E63;
  --warn-bg:#FFF3D1; --warn-ink:#5E4200; --warn-line:#E0B23A;
  --alert-bg:#FDEBEE; --alert-ink:#8F1630; --alert-line:#E79AAB;
  --btn:#1F4E79; --btn-ink:#FFFFFF; --focus:#1D5FB0;
  --pair-uj:#1F4E79; --pair-eu:#0E6B66; --pair-ej:#8A4B0F; --pair-gj:#6B3FA0;
  --f-ui:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Hiragino Kaku Gothic ProN","BIZ UDPGothic","Yu Gothic UI","Yu Gothic",Meiryo,"Noto Sans JP",sans-serif;
  --f-num:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,Consolas,"Hiragino Sans","BIZ UDGothic",monospace;
  --f-slip:"BIZ UDGothic","Osaka-Mono","MS Gothic","Noto Sans Mono CJK JP",ui-monospace,"SF Mono",Menlo,monospace;
  --fs-cap:.75rem; --fs-note:.8125rem; --fs-body:.9375rem; --fs-lead:1.0625rem;
  --fs-h2:1.3125rem; --fs-xl:1.375rem; --fs-key:1.5rem; --fs-hero:2rem;
  --lh-body:1.7; --lh-tight:1.3;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:32px; --s7:48px;
  --r1:4px; --r2:8px; --r3:12px;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  color-scheme: dark;
  --ground:#0B1420; --panel:#121E2E; --raised:#172536; --chipbg:#1B2A3E;
  --line:#263A52; --line-strong:#5A7090;
  --ink:#E6EDF5; --sub:#A3B3C6;
  --navy:#8FB6DE; --gold:#E8C060;
  --act:#E9B53E; --act-ink:#1E1500; --act-bg:#2D2410;
  --buy:#FF9466; --buy-bg:#3A2117; --sell:#8AB2F4; --sell-bg:#172A4A;
  --up:#5CC98A; --down:#F57D95;
  --info-bg:#16304C; --info-ink:#C3DAF2;
  --warn-bg:#33290F; --warn-ink:#F3D27B; --warn-line:#8A6A1E;
  --alert-bg:#3A1A22; --alert-ink:#FFB3C0; --alert-line:#8C3A4A;
  --btn:#8FB6DE; --btn-ink:#0B1420; --focus:#8FB6DE;
  --pair-uj:#8FB6DE; --pair-eu:#5CC9BE; --pair-ej:#E0A458; --pair-gj:#C3A2F0;
} }
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground:#0B1420; --panel:#121E2E; --raised:#172536; --chipbg:#1B2A3E;
  --line:#263A52; --line-strong:#5A7090;
  --ink:#E6EDF5; --sub:#A3B3C6;
  --navy:#8FB6DE; --gold:#E8C060;
  --act:#E9B53E; --act-ink:#1E1500; --act-bg:#2D2410;
  --buy:#FF9466; --buy-bg:#3A2117; --sell:#8AB2F4; --sell-bg:#172A4A;
  --up:#5CC98A; --down:#F57D95;
  --info-bg:#16304C; --info-ink:#C3DAF2;
  --warn-bg:#33290F; --warn-ink:#F3D27B; --warn-line:#8A6A1E;
  --alert-bg:#3A1A22; --alert-ink:#FFB3C0; --alert-line:#8C3A4A;
  --btn:#8FB6DE; --btn-ink:#0B1420; --focus:#8FB6DE;
  --pair-uj:#8FB6DE; --pair-eu:#5CC9BE; --pair-ej:#E0A458; --pair-gj:#C3A2F0;
}
/* 計算済みのコントラスト比（文字/背景）
   明: ink/panel 15.8・sub/chipbg 6.3・act-ink/act 9.1・buy/buy-bg 5.2・sell/sell-bg 5.5・up/chipbg 5.0・down/panel 6.7・gold/panel 5.8・info 9.5・warn 8.4・alert 7.9・btn 8.7
   暗: ink/panel 14.2・sub/chipbg 6.8・act 9.6・buy/buy-bg 6.9・sell/sell-bg 6.7・up/chipbg 7.0・down/chipbg 5.7・gold 9.7・info 9.4・alert 9.2・btn 8.7（すべて4.5以上） */

/* ===== 基本 ===== */
*,*::before,*::after{box-sizing:border-box}
[hidden]{display:none !important}
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--f-ui);font-size:var(--fs-body);line-height:var(--lh-body);-webkit-text-size-adjust:100%;text-size-adjust:100%}
h1,h2,h3,h4{font-feature-settings:"palt" 1;letter-spacing:.02em}
a{color:var(--navy)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
@media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
[id]{scroll-margin-top:64px}
.wrap{max-width:1040px;margin:0 auto;padding:var(--s4) var(--s4) var(--s7)}

/* ===== ヘッダ（計器帯） ===== */
header.top{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s2);padding:var(--s1) 0 var(--s3)}
.top-id{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:var(--s1) var(--s3)}
header.top h1{margin:0;font-size:var(--fs-lead);font-weight:800}
header.top h1 small{margin-left:.5em;font-size:var(--fs-note);font-weight:400;color:var(--sub)}
header.top .meta{font-size:var(--fs-note);color:var(--sub);font-variant-numeric:tabular-nums}
header.top .meta .upd{color:var(--ink);font-weight:700}
.inst-row{display:grid;gap:var(--s2);grid-template-columns:repeat(auto-fit,minmax(min(100%,9.5rem),1fr))}
.inst{display:grid;align-content:start;gap:2px;min-width:0;padding:var(--s2) var(--s3);background:var(--panel);border:1px solid var(--line);border-radius:var(--r2)}
.inst-l{font-size:var(--fs-cap);font-weight:700;letter-spacing:.06em;color:var(--sub)}
header.top .rate{font-family:var(--f-num);font-weight:600;font-size:clamp(1.625rem,7vw,var(--fs-hero));line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
header.top .rate small{margin-left:.15em;font-family:var(--f-ui);font-size:var(--fs-note);font-weight:400;color:var(--sub)}
header.top .inst.sub .rate,header.top .inst.eq .eqv{font-family:var(--f-num);font-weight:600;font-size:clamp(1.25rem,5vw,var(--fs-key));line-height:1.2;font-variant-numeric:tabular-nums}
.inst.eq .d{font-family:var(--f-num);font-size:var(--fs-note);font-weight:600}
.inst-n{font-size:var(--fs-cap);color:var(--sub)}
.live-bar{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2) var(--s3)}
.inst .live-d{font-family:var(--f-num);font-size:var(--fs-cap);font-weight:600}
.abtn.live{background:var(--down);color:var(--btn-ink)}   /* 2026-10-15 落合さん指示「赤いボタン」。文字色は明暗とも4.5以上 */   /* レートの基準（日足終値）。ラベルを短く保って折り返さない */
@media (max-width:559px){.inst.eq{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:baseline;column-gap:var(--s3)} .inst.eq .inst-l{flex-basis:100%}}
.dlv{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.dlv-l{font-size:var(--fs-note);font-weight:700;color:var(--sub)}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--sub);font-size:var(--fs-note);font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.pill.done{background:var(--info-bg);color:var(--info-ink);border-color:transparent}
.pill.wait{border-style:dashed}
.pill.late,.pill.miss{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
.meta-s{margin:0;font-size:var(--fs-note);color:var(--sub)}

/* ===== バナー（クラス banner は維持） ===== */
.banner{display:grid;grid-template-columns:auto minmax(0,1fr);align-items:start;gap:var(--s1) var(--s3);margin:var(--s2) 0;padding:var(--s3) var(--s4);border:1px solid transparent;border-radius:var(--r2);font-size:var(--fs-body);font-weight:700;line-height:1.5}
.banner::before{content:"";width:10px;height:10px;margin-top:7px;background:currentColor}
.banner.act{background:var(--act);color:var(--act-ink)}
.banner.ok{background:var(--info-bg);color:var(--info-ink)}
.banner.flash{background:var(--panel);color:var(--navy);border:1px dashed var(--line-strong)}
.banner.warn{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
.banner .go{grid-column:2;justify-self:end;display:inline-flex;align-items:center;min-height:32px;color:inherit;font-size:var(--fs-note);text-decoration:underline;text-underline-offset:3px}
@media (min-width:720px){.banner{grid-template-columns:auto minmax(0,1fr) auto} .banner .go{grid-column:3;grid-row:1;align-self:center}}
.banner .go::after{content:"今日の発注へ ↓"}

/* ===== ページ内リンクの帯（.grid の外＝AIの材料外） ===== */
nav.jump{position:sticky;top:0;z-index:5;display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;margin:var(--s2) calc(-1*var(--s4)) var(--s3);padding:var(--s2) var(--s4);background:var(--ground);border-bottom:1px solid var(--line)}
nav.jump::-webkit-scrollbar{display:none}
/* 右端のぼかし（横に動かせる合図）。帯の右端に貼り付き、最後まで送ると空白に重なって消える */
nav.jump::after{content:"";position:sticky;right:calc(-1*var(--s4));flex:none;width:32px;margin-left:-6px;background:linear-gradient(90deg,transparent,var(--ground) 70%);pointer-events:none}
nav.jump a{flex:none;display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:4px 12px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--ink);font-size:var(--fs-note);font-weight:700;text-decoration:none}
nav.jump a:hover{border-color:var(--navy)}
nav.jump .cnt{display:inline-grid;place-items:center;min-width:1.5em;height:1.5em;padding:0 4px;border-radius:999px;background:var(--act);color:var(--act-ink);font-family:var(--f-num);font-size:var(--fs-cap)}

/* ===== グリッドとカード ===== */
.grid{display:grid;gap:var(--s3);grid-template-columns:minmax(0,1fr)}
.grid > *{min-width:0}   /* 消さない（指示書の長い行でページ幅が7,000pxを超えた事故の対策） */
@media (min-width:720px){.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s4)} .grid > .span2,.grid > .grp,.grid > .subgrp{grid-column:1/-1}}
.grp{margin:var(--s5) 0 0;padding-bottom:var(--s2);border-bottom:2px solid var(--ink);display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--s1) var(--s3);font-size:var(--fs-h2);font-weight:800;line-height:var(--lh-tight)}
.grp small{font-size:var(--fs-note);font-weight:400;letter-spacing:0;color:var(--sub)}
.subgrp{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--s1) var(--s3);padding-top:var(--s2)}
.subgrp h3{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2);font-size:var(--fs-lead);font-weight:800}
.subgrp .note{flex-basis:100%}
.card{display:flex;flex-direction:column;gap:var(--s3);background:var(--panel);border:1px solid var(--line);border-radius:var(--r3);padding:var(--s4)}
.card > *{min-width:0;margin-top:0;margin-bottom:0}
.card > :empty{display:none}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:var(--s1) var(--s3)}
.card h2,.card h3{margin:0;font-size:var(--fs-lead);font-weight:700;line-height:var(--lh-tight)}
.card h3 .badge{margin-left:var(--s2);vertical-align:middle}
.params{font-size:var(--fs-note);color:var(--sub);line-height:1.5}
.card h4,h4.fh{margin:var(--s2) 0 0;padding-left:var(--s2);border-left:3px solid var(--navy);font-size:var(--fs-body);font-weight:700;line-height:1.4;color:var(--ink)}
.card h4 small,h4.fh small{margin-left:.5em;font-size:var(--fs-cap);font-weight:400;color:var(--sub)}
.note{margin:0;font-size:var(--fs-note);line-height:1.65;color:var(--sub)}
.note b{color:var(--ink)}
.hedge{margin:0;font-size:var(--fs-note);color:var(--ink)}
.callout{margin:0;padding:var(--s2) var(--s3);border:1px solid;border-radius:var(--r2);font-size:var(--fs-body);font-weight:700;line-height:1.55}
.callout.alert{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
.callout.warn{background:var(--warn-bg);color:var(--warn-ink);border-color:var(--warn-line)}
.callout.info{background:var(--info-bg);color:var(--info-ink);border-color:transparent}
.mult-flag{display:none}
.wrap:has(#a-risk option:checked:not([value="1"])) .mult-flag{display:block}

/* ===== 札・状態 ===== */
.badges{display:flex;flex-wrap:wrap;gap:4px}
.badge{display:inline-flex;align-items:center;padding:1px 8px;border:1px solid var(--line-strong);border-radius:var(--r1);color:var(--sub);font-size:var(--fs-cap);font-weight:700;letter-spacing:.04em;line-height:1.6;white-space:nowrap}
.badge.pair{font-family:var(--f-num);color:var(--pair-uj);border-color:currentColor}
.badge.pair.eu{color:var(--pair-eu)} .badge.pair.ej{color:var(--pair-ej)} .badge.pair.gj{color:var(--pair-gj)}
.badge.trial{border-style:dashed}
.badge.risk{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
.badge.new{background:var(--info-bg);color:var(--info-ink);border-color:transparent}
.action{display:flex;align-items:center;gap:var(--s2);margin:0;font-size:var(--fs-key);font-weight:800;line-height:1.25}
.action::before{content:"";flex:none;width:.45em;height:.45em;background:var(--line-strong)}
.action.buy{color:var(--buy)} .action.buy::before{background:var(--buy)}
.action.sell{color:var(--sell)} .action.sell::before{background:var(--sell)}
.action.gold{color:var(--ink)} .action.gold::before{background:var(--act)}
.action.flat{color:var(--sub);font-size:var(--fs-lead)} .action.flat::before{background:transparent;border:1.5px solid var(--line-strong)}
@media (max-width:559px){.action{font-size:1.25rem}}
.pos{display:inline-flex;align-items:center;padding:2px 10px;border-radius:var(--r1);font-size:var(--fs-body);font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.pos.buy{background:var(--buy-bg);color:var(--buy)}
.pos.sell{background:var(--sell-bg);color:var(--sell)}
.pos.flat{background:var(--chipbg);color:var(--sub)}
.up{color:var(--up)} .down{color:var(--down)}
.posline{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--s1) var(--s3);font-size:var(--fs-body)}
.stop{color:var(--sub);font-variant-numeric:tabular-nums}
.stop b{font-family:var(--f-num);font-weight:600;color:var(--ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;align-items:baseline;gap:4px;padding:2px 8px;border:1px solid var(--line);border-radius:var(--r1);background:transparent;color:var(--sub);font-size:var(--fs-note);font-variant-numeric:tabular-nums;white-space:nowrap}
.chip small{font-size:var(--fs-cap)}
.chip.fav{color:var(--up);border-color:currentColor}
.chip.adv{color:var(--down);border-color:currentColor}

/* ===== 今日の発注 ===== */
.today.has-act{border-color:var(--warn-line);box-shadow:inset 0 4px 0 var(--act)}
.todo{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:minmax(0,1fr)}
.todo-row{display:grid;grid-template-columns:auto minmax(0,1fr) auto;grid-template-areas:"pair sys st" "what what what" "px px px";align-items:center;gap:var(--s1) var(--s2);padding:var(--s3) 0;border-top:1px solid var(--line)}
.todo-row:first-child{border-top:0;padding-top:0}
.todo-row > .badge{grid-area:pair;justify-self:start}
.todo-sys{grid-area:sys;font-weight:700}
.state{grid-area:st;justify-self:end;padding:2px 10px;border-radius:999px;font-size:var(--fs-note);font-weight:700;white-space:nowrap}
.state.act{background:var(--act);color:var(--act-ink)}
.state.done{background:var(--info-bg);color:var(--info-ink)}
.state.none{background:var(--chipbg);color:var(--sub)}
.state.wait{border:1px dashed var(--line-strong);color:var(--sub)}
.todo-what{grid-area:what;font-size:var(--fs-note);line-height:1.6;color:var(--sub)}
.todo-row.act .todo-what{font-size:var(--fs-body);color:var(--ink)}
.todo-px{grid-area:px;display:flex;flex-wrap:wrap;align-items:baseline;gap:2px var(--s3);font-family:var(--f-num);font-variant-numeric:tabular-nums}
.todo-px .v{font-size:var(--fs-key);font-weight:600;color:var(--ink)}
.todo-px .old{font-size:var(--fs-lead);color:var(--sub);text-decoration:line-through}
.todo-px .arr{color:var(--sub)}
.todo-px .k,.todo-px .dir{font-family:var(--f-ui);font-size:var(--fs-note);font-weight:700;color:var(--sub)}
/* 横並びの列にするのは幅1024px以上だけ。価格は作業内容の下の段に置く（5列にすると幅720〜850pxで
   内容欄が押しつぶれた。720〜1023pxはスマホと同じ縦積みのほうが内容欄を広く取れる） */
@media (min-width:1024px){.todo-row{grid-template-columns:5.5rem 11rem 8rem minmax(0,1fr);grid-template-areas:"pair sys st what" ". . . px"} .state{justify-self:start}}

/* ===== 指示書の原文と高さ制限 ===== */
pre.order{margin:0;padding:var(--s3) var(--s4);background:var(--raised);border:1px solid var(--line);border-left:4px solid var(--line-strong);border-radius:var(--r2);color:var(--ink);font-family:var(--f-slip);font-size:.8125rem;line-height:1.7;white-space:pre;overflow-x:auto;tab-size:4}
@media (max-width:719px){pre.order{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.78rem}}
.clamp{position:relative;display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s2)}
.clamp > *{min-width:0}
.clamp-t{position:absolute;width:1px;height:1px;margin:0;opacity:0;pointer-events:none}
.clamp-b{max-height:var(--clamp-h,15rem);overflow:hidden;-webkit-mask-image:linear-gradient(#000 calc(100% - 3rem),transparent);mask-image:linear-gradient(#000 calc(100% - 3rem),transparent)}
.clamp.c-xs{--clamp-h:5.5rem} .clamp.c-s{--clamp-h:10rem} .clamp.c-m{--clamp-h:16rem} .clamp.c-l{--clamp-h:28rem}
.clamp-t:checked ~ .clamp-b{max-height:none;-webkit-mask-image:none;mask-image:none}
.clamp-btn{justify-self:start;display:inline-flex;align-items:center;min-height:36px;padding:4px 14px;border:1px solid var(--line-strong);border-radius:999px;background:var(--panel);color:var(--navy);font-size:var(--fs-note);font-weight:700;cursor:pointer}
.clamp-btn::after{content:"続きを表示 ▾"}
.clamp-t:checked ~ .clamp-btn::after{content:"たたむ ▴"}
#cl-order ~ .clamp-btn::after,#cl-order-eu ~ .clamp-btn::after{content:"発注カードまで全文を表示 ▾"}
#cl-order:checked ~ .clamp-btn::after,#cl-order-eu:checked ~ .clamp-btn::after{content:"たたむ ▴"}
.clamp-t:focus-visible ~ .clamp-btn{outline:2px solid var(--focus);outline-offset:2px}

/* ===== システムのタイル ===== */
.tile-h{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:var(--s1) var(--s2)}
.tile-h .params{flex-basis:100%}
.tile.need{border-color:var(--warn-line);box-shadow:inset 0 4px 0 var(--act)}
.tile.side-buy{border-left:4px solid var(--buy)} .tile.side-sell{border-left:4px solid var(--sell)}
.tile-s{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2) var(--s3)}
.kvs{display:grid;grid-template-columns:repeat(auto-fill,minmax(8.5rem,1fr));gap:var(--s2)}
.kv{display:grid;align-content:start;gap:1px;min-width:0;padding:6px 10px;border-radius:var(--r2);background:var(--chipbg)}
.kv .k{font-size:var(--fs-cap);font-weight:700;color:var(--sub)}
.kv b{font-family:var(--f-num);font-size:var(--fs-lead);font-weight:600;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.kv b i{margin-left:2px;font-family:var(--f-ui);font-style:normal;font-size:var(--fs-cap);font-weight:400;color:var(--sub)}
.kv small{font-size:var(--fs-cap);color:var(--sub)}
.kv.alert{background:var(--alert-bg)} .kv.alert b{color:var(--alert-ink)}
.exit{display:grid;gap:4px;padding:var(--s2) var(--s3);border-left:4px solid var(--line-strong);border-radius:0 var(--r2) var(--r2) 0;background:var(--raised);font-size:var(--fs-note);line-height:1.6}
.exit .exit-h{font-size:var(--fs-body)}
.exit .exit-h b{font-family:var(--f-num);font-size:var(--fs-lead);font-weight:600}
.exit .k{margin-right:6px;font-size:var(--fs-cap);font-weight:700;color:var(--sub)}
.exit .first{display:inline-block;margin-left:6px;padding:0 6px;border-radius:var(--r1);background:var(--act);color:var(--act-ink);font-size:var(--fs-cap);font-weight:700}
.exit .nxt{color:var(--sub)}
.exit.near{background:var(--alert-bg);border-left-color:var(--down)}
.exit .alert{color:var(--alert-ink);font-weight:700}
.tile-meta{display:flex;flex-wrap:wrap;gap:var(--s1) var(--s3);font-size:var(--fs-note);color:var(--sub)}
.tile .chips{margin-top:auto;padding-top:var(--s2);border-top:1px dashed var(--line)}

/* ===== 数字カード・表 ===== */
.kpis{display:grid;gap:var(--s2);grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr))}
.kpi{display:grid;align-content:start;gap:2px;min-width:0;padding:var(--s2) var(--s3);border-radius:var(--r2);background:var(--chipbg)}
.kpi .l{font-size:var(--fs-cap);font-weight:700;line-height:1.45;color:var(--sub)}
.kpi .l small,.kpi small{font-weight:400}
.kpi .v{font-family:var(--f-num);font-size:var(--fs-xl);font-weight:600;line-height:1.2;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.kpi .v small{display:block;margin-top:2px;font-family:var(--f-ui);font-size:var(--fs-cap);font-weight:400;color:var(--sub)}
.kpi .v .v{font-size:var(--fs-lead)}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--line);border-radius:var(--r2)}
table{width:100%;border-collapse:collapse;font-size:var(--fs-note);font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:0}
th{background:var(--raised);color:var(--sub);font-size:var(--fs-cap);font-weight:700}
td.n,th.n{text-align:right;font-family:var(--f-num)}
td.w{white-space:normal;min-width:16em}
@media (max-width:719px){.rr td.w{min-width:24em}}   /* スマホで評価の列が3〜4行に折れて行が間延びしないように */
@media (max-width:559px){th,td{padding:6px 8px}}
.tablewrap.sticky1 th:first-child,.tablewrap.sticky1 td:first-child{position:sticky;left:0;z-index:1;background:var(--panel);box-shadow:1px 0 0 var(--line)}
.tablewrap.sticky1 th:first-child{background:var(--raised)}

/* ===== 成績・資産推移 ===== */
.perf-hero{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s3)}
@media (min-width:720px){.perf-hero{grid-template-columns:minmax(0,1fr) minmax(0,1.7fr);align-items:center}}
.hero-l{display:block;font-size:var(--fs-cap);font-weight:700;color:var(--sub)}
.hero-v{display:block;font-family:var(--f-num);font-size:clamp(1.75rem,7vw,2.25rem);font-weight:600;line-height:1.1;font-variant-numeric:tabular-nums}
.hero-d{font-family:var(--f-num);font-size:var(--fs-body);font-weight:600}
svg{width:100%;height:auto;display:block}   /* 全体の指定は残す（スキャルのはしご図が依存） */
svg.spark{height:clamp(130px,28vw,200px)}
svg .gl{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}
svg .base{stroke:var(--sub);stroke-width:1;stroke-dasharray:4 4;vector-effect:non-scaling-stroke}
svg .line{fill:none;stroke:var(--navy);stroke-width:2.2;vector-effect:non-scaling-stroke}
svg .area{fill:var(--navy);opacity:.12;stroke:none}
svg .dot{stroke:var(--act);stroke-width:9;stroke-linecap:round;vector-effect:non-scaling-stroke}
.spark-lab{display:flex;flex-wrap:wrap;justify-content:space-between;gap:var(--s1) var(--s3);font-size:var(--fs-note);color:var(--sub);font-variant-numeric:tabular-nums}
.spark-lab b{font-family:var(--f-num);font-weight:600;color:var(--ink)}
.sysbar{list-style:none;margin:0;padding:0;display:grid;gap:var(--s2)}
.sysbar li{display:grid;grid-template-columns:4.5rem minmax(0,1fr) auto;align-items:center;gap:var(--s2);font-size:var(--fs-note)}
.sysbar .nm{font-weight:700}
.sysbar .val{font-family:var(--f-num);font-weight:600;text-align:right}
.sysbar .val small{display:block;font-family:var(--f-ui);font-size:var(--fs-cap);font-weight:400;color:var(--sub)}
.bar{position:relative;height:10px;border-radius:999px;background:var(--chipbg)}
.bar::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--line-strong)}
.bar i{position:absolute;top:0;bottom:0}
.bar i.up{left:50%;background:var(--up);border-radius:0 999px 999px 0}
.bar i.down{right:50%;background:var(--down);border-radius:999px 0 0 999px}

/* ===== ファンダ ===== */
.regime{margin:0;font-size:var(--fs-lead);font-weight:800;line-height:1.4}
.fbox{padding:var(--s3);border-radius:var(--r2);background:var(--chipbg);font-size:var(--fs-note);line-height:1.65}
.fbox .l{margin-bottom:var(--s1);font-size:var(--fs-cap);font-weight:700;letter-spacing:.04em;color:var(--sub)}
.fbox.key{background:var(--info-bg);color:var(--info-ink);border-left:4px solid var(--navy);font-size:var(--fs-body)}
.fbox.key .l{color:var(--info-ink)}
.fbox.guide{background:var(--panel);border:1px dashed var(--line-strong)}
.fbox.guide.uj{border-left:4px solid var(--pair-uj)} .fbox.guide.eu{border-left:4px solid var(--pair-eu)} .fbox.guide.ej{border-left:4px solid var(--pair-ej)} .fbox.guide.gj{border-left:4px solid var(--pair-gj)}
.guide-row{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s2)}
@media (min-width:720px){.guide-row{grid-template-columns:repeat(2,minmax(0,1fr))}}
ul.focus{margin:0;padding-left:1.2em;font-size:var(--fs-body);line-height:1.65}
ul.focus li{margin:2px 0}
.fbal{display:grid;gap:4px}
.fbar-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--s1) var(--s3);font-size:var(--fs-body)}
.fbar{display:flex;height:12px;gap:2px;border-radius:999px;overflow:hidden}
.fbar-legend{display:flex;justify-content:space-between;font-size:var(--fs-cap);color:var(--sub)}
.themes{display:grid;grid-template-columns:minmax(0,1fr)}
.theme{padding:var(--s2) 0;border-bottom:1px solid var(--line)}
.theme-meta{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2);font-size:var(--fs-note)}
.theme-meta .gold{color:var(--gold);letter-spacing:.06em}
.theme-h{margin-top:2px;font-size:var(--fs-body);font-weight:600;line-height:1.55}
.fc-line{display:flex;flex-wrap:wrap;align-items:baseline;gap:var(--s1) var(--s3);font-size:var(--fs-body)}
.fc-line .k{font-size:var(--fs-cap);font-weight:700;color:var(--sub)}

/* ===== 折りたたみ・記録・フッター ===== */
details{font-size:var(--fs-note);color:var(--sub)}
details > summary{min-height:32px;padding:4px 0;font-weight:700;color:var(--navy);cursor:pointer}
details.fold{padding-top:var(--s2);border-top:1px solid var(--line);font-size:var(--fs-body);color:var(--ink)}
details.fold > summary{display:flex;align-items:center;min-height:44px;font-size:var(--fs-body);list-style:none}
details.fold > summary::-webkit-details-marker{display:none}
details.fold > summary::before{content:"▸";flex:none;margin-right:8px}
details.fold[open] > summary::before{content:"▾"}
details.fold[open] > summary{margin-bottom:var(--s2)}
details.fold > .tablewrap,details.fold > .note,details.fold > h4{margin-top:var(--s2)}
.kv-list{margin:0;display:grid;gap:var(--s2)}
.kv-list dt{font-size:var(--fs-cap);font-weight:700;color:var(--sub)}
.kv-list dd{margin:0;font-size:var(--fs-note);line-height:1.6;color:var(--ink)}
footer{max-width:72ch;margin-top:var(--s6);padding-top:var(--s4);border-top:1px solid var(--line);font-size:var(--fs-note);line-height:1.7;color:var(--sub)}
"""

# 2026-10-12 ユーザー指示: ボードに「更新」「今すぐ分析」ボタンと分析結果欄を設置。
# 分析はアーティファクトの実行時機能（sample＝閲覧者のClaudeに問い合わせ／db＝結果を
# 保存して全端末で共有）で行う。claude.ai の閲覧画面以外では「更新」だけが動く。
# 結果は db の analysis/latest に保存され、ボードを再公開しても消えない。
ANALYSIS_CSS = """
/* 2026-10-13 ビジュアル改修: クラス名はすべて維持（JS が書き出すHTMLが使う）。白抜き文字は全廃。
   .abtn と .abar select には display を書かない（hidden 属性での表示切替を守る） */
.abar{display:flex;flex-wrap:wrap;align-items:center;gap:var(--s2)}
.abar label{font-size:var(--fs-note);font-weight:700;color:var(--sub)}
.abtn{min-height:40px;padding:8px 16px;border:1px solid transparent;border-radius:999px;background:var(--btn);color:var(--btn-ink);font-family:var(--f-ui);font-size:var(--fs-body);font-weight:700;line-height:1.2;cursor:pointer;touch-action:manipulation}
.abtn.gold{background:var(--act);color:var(--act-ink)}
.abtn.ghost{background:var(--panel);color:var(--ink);border-color:var(--line-strong)}
.abtn:disabled{opacity:.5;cursor:default}
.abar select{min-height:40px;padding:6px 10px;border:1px solid var(--line-strong);border-radius:var(--r2);background:var(--panel);color:var(--ink);font-family:var(--f-ui);font-size:1rem}
.hint{flex-basis:100%;margin:0;font-size:var(--fs-note);color:var(--sub)}
.astatus{font-size:var(--fs-note);color:var(--sub)}
.aout{font-size:var(--fs-body)}
.aout h4{margin:var(--s4) 0 var(--s1);padding:0 0 4px;border:0;border-bottom:1px solid var(--line);font-size:var(--fs-body);font-weight:700;color:var(--navy)}
.aout p{margin:4px 0}
.aout ul{margin:4px 0;padding-left:1.2em}
.aout li{margin:3px 0}
.aout .lead{padding:var(--s3);border-radius:var(--r2);background:var(--info-bg);color:var(--info-ink);font-size:var(--fs-lead);font-weight:700}
.aout table td,.aout table th{white-space:normal;vertical-align:top}
/* 2026-10-12: パターン表の倍率・金額が途中で折り返されて読みにくかった（ユーザー指摘）。
   数字の列は折り返さず、長い説明の列だけ折り返す */
.aout table td:not(:last-child),.aout table th{white-space:nowrap}
.aout table td:last-child{white-space:normal;min-width:14em}
.aout .tag{display:inline-block;padding:1px 8px;border:1px solid var(--line-strong);border-radius:999px;background:var(--raised);color:var(--ink);font-size:var(--fs-cap);font-weight:700;white-space:nowrap}
.aout .tag.now{background:var(--info-bg);color:var(--info-ink);border-color:transparent}
.aout .tag.more{background:var(--act);color:var(--act-ink);border-color:transparent}
.aout .tag.warn{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
/* スキャル発注チケット（2026-10-12）: 側（買/売/見送り）を色帯と大見出しで、価格ははしご図で */
.ticket{margin-top:var(--s2);border:1px solid var(--line-strong);border-radius:var(--r3);overflow:hidden;background:var(--panel)}
.ticket .t-head{padding:var(--s3) var(--s4);border-left:6px solid var(--line-strong);background:var(--raised)}
.ticket.buy .t-head{border-left-color:var(--buy)}
.ticket.sell .t-head{border-left-color:var(--sell)}
.t-pair{font-family:var(--f-num);font-size:var(--fs-cap);font-weight:600;letter-spacing:.08em;color:var(--sub)}
.t-do{font-size:var(--fs-key);font-weight:800;line-height:1.3}
.ticket.buy .t-do{color:var(--buy)} .ticket.sell .t-do{color:var(--sell)} .ticket.flat .t-do{color:var(--ink)}
.t-sub{margin-top:2px;font-size:var(--fs-note);color:var(--sub)}
.t-body{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--s3);padding:var(--s3) var(--s4)}
.ladder{max-width:640px}
.ladder .lv{stroke-width:2} .ladder .lv.up{stroke:var(--up)} .ladder .lv.down{stroke:var(--down)}
.ladder .lv.ent{stroke:var(--ink);stroke-width:2.5} .ladder .lv.now{stroke:var(--sub);stroke-dasharray:5 4}
.ladder .lt{font-size:13px;font-weight:700;fill:var(--sub)}
.ladder .lt.up{fill:var(--up)} .ladder .lt.down{fill:var(--down)} .ladder .lt.ent{fill:var(--ink)}
.ladder .lp{font-size:13px;fill:var(--ink);font-family:var(--f-num)}
.ladder .zone-up{fill:var(--up);opacity:.10} .ladder .zone-down{fill:var(--down);opacity:.10}
/* 分析時の値（入る値とほぼ同じ高さに来る）の文字と価格は、線の中央の空いた所へずらして重なりを避ける
   （JS の ladder() は変えない。分析時の値の線は最後に描かれ、その価格は .lt.now の直後の .lp） */
.ladder .lt.now{text-anchor:start;transform:translateX(24px);font-weight:400}
.ladder .lt.now + .lp{text-anchor:end;transform:translateX(-24px);fill:var(--sub)}
.ladder .lt.now,.ladder .lt.now + .lp{paint-order:stroke;stroke:var(--panel);stroke-width:5px;stroke-linejoin:round}
@media (max-width:559px){.t-body{padding:var(--s3)} .ladder .lt,.ladder .lp{font-size:18px}}
.t-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,8rem),1fr));gap:var(--s2)}
.t-kpis > div{display:grid;gap:2px;padding:var(--s2) 10px;border-radius:var(--r2);background:var(--chipbg)}
.t-kpis span,.t-kpis small{font-size:var(--fs-cap);color:var(--sub)}
.t-kpis b{font-family:var(--f-num);font-size:1.25rem;font-weight:600;font-variant-numeric:tabular-nums}
.t-steps{margin:0;padding-left:1.4em;display:grid;gap:6px;font-size:var(--fs-body)}
.t-steps li::marker{font-weight:800;color:var(--gold)}
.mono{font-family:var(--f-num)}
/* スキャルのチャート（2026-10-13）: ローソク＋EMA＋ペーパートレード＋AIの案。色は既存トークンのみ */
.sc-sig{margin-top:var(--s3);padding:var(--s3) var(--s4);border:1px solid var(--line-strong);border-left:6px solid var(--gold);border-radius:var(--r3);background:var(--raised)}
.sc-sig.live.buy{border-left-color:var(--buy)} .sc-sig.live.sell{border-left-color:var(--sell)}
.sc-sig .k{font-size:var(--fs-cap);color:var(--sub);font-weight:700;letter-spacing:.04em}
.sc-sig .big{font-size:var(--fs-key);font-weight:800;line-height:1.35}
.sc-sig.live.buy .big{color:var(--buy)} .sc-sig.live.sell .big{color:var(--sell)}
.sc-sig .row{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:var(--fs-note);margin-top:4px}
.sc-svg .sg{fill:var(--gold);stroke:var(--panel);stroke-width:1.5}
.sc-eq .s4{fill:none;stroke:var(--ink);stroke-width:3}
.sc-wrap{margin-top:var(--s3);border:1px solid var(--line);border-radius:var(--r3);background:var(--panel);padding:var(--s3)}
.sc-bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:space-between}
.sc-tabs{display:flex;flex-wrap:wrap;gap:4px}
.sc-tabs button{border:1px solid var(--line-strong);background:var(--raised);color:var(--ink);border-radius:999px;padding:3px 12px;font-size:var(--fs-cap);font-weight:700;cursor:pointer}
.sc-tabs button[aria-pressed="true"]{background:var(--ink);color:var(--panel);border-color:var(--ink)}
.sc-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:var(--fs-cap);color:var(--sub)}
.sc-legend i{display:inline-block;width:14px;height:0;border-top:2px solid;vertical-align:middle;margin-right:4px}
.sc-svg{width:100%;height:auto;display:block;margin-top:6px;touch-action:pan-y}
.sc-svg .gl{stroke:var(--line);stroke-width:1}
.sc-svg .ax{font-size:11px;fill:var(--sub);font-family:var(--f-num)}
.sc-svg .cu{fill:var(--up);stroke:var(--up)} .sc-svg .cd{fill:var(--down);stroke:var(--down)}
.sc-svg .e20{fill:none;stroke:var(--navy);stroke-width:2} .sc-svg .e50{fill:none;stroke:var(--gold);stroke-width:2}
.sc-svg .tb{fill:var(--buy);stroke:var(--panel);stroke-width:1.5} .sc-svg .ts{fill:var(--sell);stroke:var(--panel);stroke-width:1.5}
.sc-svg .tb.hollow,.sc-svg .ts.hollow{fill:var(--panel);stroke-width:2} .sc-svg .tb.hollow{stroke:var(--buy)} .sc-svg .ts.hollow{stroke:var(--sell)}
.sc-svg .xw{fill:var(--up);stroke:var(--panel);stroke-width:1.5} .sc-svg .xl{fill:var(--down);stroke:var(--panel);stroke-width:1.5}
.sc-svg .tl{stroke:var(--sub);stroke-width:1.2;stroke-dasharray:3 3}
.sc-svg .ai{stroke:var(--ink);stroke-width:1.5} .sc-svg .ai.buy{fill:var(--buy)} .sc-svg .ai.sell{fill:var(--sell)} .sc-svg .ai.flat{fill:var(--panel)}
.sc-svg .pl{stroke-width:1.5;stroke-dasharray:6 4} .sc-svg .pl.up{stroke:var(--up)} .sc-svg .pl.down{stroke:var(--down)} .sc-svg .pl.ent{stroke:var(--ink);stroke-dasharray:none}
.sc-svg .pt{font-size:11px;font-weight:700;font-family:var(--f-num);paint-order:stroke;stroke:var(--panel);stroke-width:4px}
.sc-svg .pt.up{fill:var(--up)} .sc-svg .pt.down{fill:var(--down)} .sc-svg .pt.ent{fill:var(--ink)}
.sc-svg .ch{stroke:var(--sub);stroke-width:1;stroke-dasharray:2 3}
.sc-tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--line-strong);border-radius:var(--r2);padding:6px 8px;font-size:var(--fs-cap);line-height:1.5;box-shadow:0 4px 14px rgba(0,0,0,.12);white-space:nowrap;z-index:5}
.sc-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,13rem),1fr));gap:var(--s2);margin-top:var(--s3)}
.sc-stats > div{padding:var(--s2) 10px;border-radius:var(--r2);background:var(--chipbg);display:grid;gap:2px}
.sc-stats span{font-size:var(--fs-cap);color:var(--sub)} .sc-stats b{font-family:var(--f-num);font-size:1.15rem}
.sc-stats small{font-size:var(--fs-cap);color:var(--sub)}
.sc-eq{width:100%;height:auto;display:block;margin-top:6px}
.sc-eq .gl{stroke:var(--line)} .sc-eq .z{stroke:var(--line-strong)} .sc-eq .ax{font-size:11px;fill:var(--sub);font-family:var(--f-num)}
.sc-eq .s1{fill:none;stroke:var(--navy);stroke-width:2} .sc-eq .s2{fill:none;stroke:var(--gold);stroke-width:2} .sc-eq .s3{fill:none;stroke:var(--ink);stroke-width:2;stroke-dasharray:5 3}
.sc-eq .lb{font-size:11px;font-weight:700;paint-order:stroke;stroke:var(--panel);stroke-width:4px;fill:var(--ink)}
.t-warn{margin:0;padding:var(--s2) var(--s3);border-radius:var(--r2);background:var(--alert-bg);color:var(--alert-ink);font-weight:700}
.t-lead{margin:0;font-weight:600}
.t-trig{margin:0;padding:0;list-style:none;display:grid;gap:6px}
.t-trig li{padding:var(--s2) var(--s3);border:1px dashed var(--line-strong);border-radius:var(--r2);font-size:var(--fs-note)}
.t-trig li::before{content:"□ ";color:var(--gold);font-weight:800}
.t-foot{padding:var(--s2) var(--s4);border-top:1px solid var(--line);font-size:var(--fs-note);color:var(--sub)}
.fc-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,8rem),1fr));gap:var(--s2);margin-top:10px}
.fc{display:grid;gap:2px;padding:var(--s2) 10px;border:1px solid var(--line);border-radius:var(--r2)}
.fc span,.fc small{font-size:var(--fs-cap);color:var(--sub)}
.fc b{font-size:var(--fs-body)}
.fc.buy b{color:var(--buy)} .fc.sell b{color:var(--sell)}
.t-more{margin-top:10px} .t-more summary{font-weight:700;color:var(--navy)}
"""

ANALYSIS_CARD = """
<section class="card span2" id="analysis">
  <div class="card-head"><h3>Claude 総合分析</h3>
    <span class="params">押すたびに最新の状況で分析し直す・結果は保存され次回も表示</span></div>
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
    <button class="abtn ghost" id="a-reload" type="button">更新</button>
    <button class="abtn gold" id="a-run" type="button" hidden>今すぐ分析</button>
    <button class="abtn ghost" id="a-stop" type="button" hidden>停止</button>
    <select id="a-depth" hidden>
      <option value="default">標準（1分前後）</option>
      <option value="complex">詳細（数分かかる・より深く考える）</option>
    </select>
    <p class="hint">「更新」＝最新のボードを読み込み、発注の数字を倍率で調整します</p>
  </div>
  <div class="aout" id="a-adj"></div>
  <div class="astatus" id="a-status">読み込み中…</div>
  <div class="aout" id="a-out"></div>
  <div class="note">※分析は「このボードに表示されている数字」だけを材料にした読み物です。
    リスクを増やすパターンは概算の比較で、変更する・しないはご本人の判断です
    （設定を恒久的に変えるときは Claude に依頼）。</div>
</section>
"""

SCALP_CARD = """
<section class="card span2" id="scalp">
  <div class="card-head"><h3>スキャルピング<span class="badge risk">高リスク・別枠</span><span class="badge">ファンダ連動</span></h3>
    <span class="params">短時間で小さな値幅を取る超短期売買の案。日足4システムとは別枠・別管理</span></div>
  <div class="abar">
    <label class="params" for="s-pair">通貨ペア</label>
    <select id="s-pair">
      <option value="ドル円">ドル円（USD/JPY）</option>
      <option value="ユーロドル">ユーロドル（EUR/USD）</option>
      <option value="ユーロ円">ユーロ円（EUR/JPY）</option>
      <option value="ポンド円">ポンド円（GBP/JPY）</option>
    </select>
    <label class="params" for="s-risk">1回のリスク</label>
    <select id="s-risk"></select>
    <button class="abtn gold" id="s-run" type="button" hidden>スキャル分析（今入るとしたら・参考）</button>
    <button class="abtn ghost" id="s-stop" type="button" hidden>停止</button>
  </div>
  <div class="astatus" id="s-status">読み込み中…</div>
  <div class="sc-sig" id="sc-sig"></div>
  <div class="sc-wrap" id="sc-wrap">
    <div class="sc-bar"><b id="sc-title">チャート</b>
      <div class="sc-tabs" role="group" aria-label="表示の拡大縮小"><button type="button" id="sc-zin" aria-label="拡大（本数を減らす）">＋ 拡大</button><button type="button" id="sc-zout" aria-label="縮小（本数を増やす）">− 広く</button><button type="button" id="sc-zreset">既定</button><span class="params" id="sc-zoomlbl"></span></div>
      <div class="sc-tabs" role="group" aria-label="時間足"><button type="button" data-tf="15分" aria-pressed="true">15分</button><button type="button" data-tf="30分" aria-pressed="false">30分</button><button type="button" data-tf="1時間" aria-pressed="false">1時間</button><button type="button" data-tf="4時間" aria-pressed="false">4時間</button><button type="button" data-tf="8時間" aria-pressed="false">8時間</button><button type="button" data-tf="12時間" aria-pressed="false">12時間</button><button type="button" data-tf="24時間" aria-pressed="false">24時間</button></div></div>
    <div class="sc-legend"><span><i style="border-color:var(--navy)"></i>EMA20</span><span><i style="border-color:var(--gold)"></i>EMA50</span>
      <span>▲▼ ペーパー売買（塗り＝1時間ルール／白抜き＝15分ルール）</span><span>● 決済（緑＝勝ち・赤＝負け）</span><span>◆ AIの案（ボタン・参考）</span><span>★ シグナル配信（正式）</span>
      <span><i style="border-color:var(--ink)"></i>最新案の入る値</span><span><i style="border-color:var(--up);border-top-style:dashed"></i>利確</span><span><i style="border-color:var(--down);border-top-style:dashed"></i>損切り</span></div>
    <div style="position:relative"><svg class="sc-svg" id="sc-svg" viewBox="0 0 720 320" role="img" aria-label="スキャル用チャート"></svg><div class="sc-tip" id="sc-tip" hidden></div></div>
    <div class="sc-stats" id="sc-stats"></div>
    <svg class="sc-eq" id="sc-eq" viewBox="0 0 720 170" role="img" aria-label="累計pipsの推移"></svg>
    <details class="fold"><summary>ペーパー売買の明細（直近30日・新しい順）</summary><div class="tablewrap" id="sc-table"></div></details>
    <p class="note">※ペーパー売買＝スキャル研究で最も安定したルールを、過去の値動きに機械的に当てはめた「もし入っていたら」の結果（往復コスト0.8pips込み・実際の発注とは無関係）。AIの案は「スキャル分析」を押した時刻の案を、その後の15分足で自動判定（入る値に2時間以内に届いたら約定・その後4時間以内に利確／損切り、届かなければ時間切れで評価）。</p>
  </div>
  <div class="aout" id="s-out"></div>
  <div class="aout" id="s-log"></div>
  <details class="fold"><summary>スキャル研究の詳しい表（ドル円・ユーロドル・ユーロ円・ポンド円／時間足別の検証）</summary>
  __SCALP_RESEARCH__
  </details>
  <div class="note">※ボードの時点のレート・ファンダ・テクニカル合議を材料にした<b>案</b>です。
    実際の発注前に必ず現在値を確認し、案の価格帯から離れていたら見送ってください。
    高リスク設定のため、損失額は日足系の数倍になります。発注・決済はすべてご本人が手動で行い、
    結果はモデル成績には含まれません（別管理）。</div>
</section>
"""

LIVE_JS = r"""
<script>
// 2026-10-15 落合さん指示「現在価格を表示したいので更新ボタンを。全通貨・チャートも」。
// claude.ai のボードは外部サイトを直接読めないため、Mac が Google Drive の
// 「FXドル円自動売買/ライブ価格/fx_live_latest.json」へ置く最新価格・足を Google Drive コネクタで読む
// （書き手: src/live_board.py。Claude の毎時タスクと launchd の15分ごとの処理）。発注とは無関係の表示。
(function () {
  var PAIRS = { usdjpy: "ドル円", eurusd: "ユーロドル", eurjpy: "ユーロ円", gbpjpy: "ポンド円" };
  var DEC = { usdjpy: 3, eurusd: 5, eurjpy: 3, gbpjpy: 3 };
  var MULT = { usdjpy: 100, eurusd: 10000, eurjpy: 100, gbpjpy: 100 };
  var btn = document.getElementById("live-btn"), st = document.getElementById("live-st");
  if (!btn) return;
  var mcp = null, busy = false;
  function hhmm(sec) {
    try { return new Date(sec * 1000).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ""; }
  }
  function setRate(pair, price, label, close) {
    var el = document.querySelector('.inst[data-pair="' + pair + '"]');
    if (!el || !(price > 0)) return;
    var r = el.querySelector(".rate"), n = el.querySelector(".inst-n"), dec = DEC[pair];
    if (r.firstChild && r.firstChild.nodeType === 3) r.firstChild.nodeValue = price.toFixed(dec);
    var d = close > 0 ? (price - close) * MULT[pair] : null;
    n.textContent = label + " ";
    if (d !== null) {
      var s = document.createElement("span");
      s.className = "live-d " + (d >= 0 ? "up" : "down");
      s.textContent = "日足終値比 " + (d >= 0 ? "+" : "") + d.toFixed(1) + "pips";
      n.appendChild(s);
    }
  }
  function b64utf8(b64) {
    var bin = atob(b64), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return new TextDecoder("utf-8").decode(u);
  }
  var WHY = { needs_reauth: "Google Driveの再接続が必要（claude.ai 設定→コネクタ）",
    server_not_connected: "Google Driveコネクタが未接続（claude.ai 設定→コネクタ）",
    not_in_manifest: "この画面でGoogle Driveが許可されていません",
    approval_required: "Google Driveの利用を許可してください（ボタンを押すと確認が出ます）",
    not_granted: "この画面ではGoogle Driveを使えません" };
  var chartStamp = null, lastChartCheck = 0;
  // 2026-10-15 落合さん指摘「チャートが最新の時間に更新されない」: 足のファイルは15分〜1時間ごとにしか
  // 書かれず、元データ（Yahoo）も15分ほど遅れる。毎分の現在値で各時間足の最後の足を伸ばし（同じ足なら
  // 高値・安値・終値を更新、新しい時間帯なら形成中の足を1本足す。EMAも同じ式で進める）、いまの時刻まで描く。
  var TFMIN = { "15分": 15, "30分": 30, "1時間": 60, "4時間": 240, "8時間": 480, "12時間": 720, "24時間": 1440 };
  function extendBars(C, price, sec) {
    if (!C || !C.tf || !(price > 0) || !sec) return false;
    var tmin = Math.floor(sec / 60), moved = false;
    Object.keys(C.tf).forEach(function (k) {
      var bars = C.tf[k], f = TFMIN[k];
      if (!bars || !bars.length || !f) return;
      var start = Math.floor((tmin + 540) / f) * f - 540;   // JSTの0時を起点に区切る（ボードの足と同じ）
      var last = bars[bars.length - 1];
      if (start === last[0]) {
        last[2] = Math.max(last[2], price); last[3] = Math.min(last[3], price); last[4] = price; moved = true;
      } else if (start > last[0]) {
        var a20 = 2 / 21, a50 = 2 / 51;
        bars.push([start, last[4], Math.max(last[4], price), Math.min(last[4], price), price,
          last[5] + a20 * (price - last[5]), last[6] + a50 * (price - last[6])]);
        if (bars.length > 400) bars.shift();
        moved = true;
      }
    });
    if (moved) C.最終 = hhmm(sec) + "（現在値まで）";
    return moved;
  }
  async function newest(title) {
    var found = await mcp.callTool("Google Drive", "search_files", {
      query: "title = '" + title + "'", excludeContentSnippets: true, pageSize: 5 }, { cache: false });
    var files = ((found.payload && found.payload.files) || []).slice().sort(function (a, b) {
      return String(b.modifiedTime).localeCompare(String(a.modifiedTime)); });
    return files[0] || null;
  }
  async function load(file) {
    var dl = await mcp.callTool("Google Drive", "download_file_content", { fileId: file.id }, { cache: false });
    return JSON.parse(b64utf8((dl.payload && dl.payload.content) || ""));
  }
  var running = null, latest = {};   // latest: ペア → {price, time}（スキャル分析が基準レートに使う）
  function run(manual) {
    if (running) return running;
    running = runInner(manual).finally(function () { running = null; });
    return running;
  }
  // 2026-10-19: Mac が止まると Drive の価格ファイルも止まり、ボードの現在値・チャートが古いままになった
  // （落合さん指摘「更新スキームが壊れてる」）。Drive の値が5分より古い・読めないときは、
  // Twelve Data コネクタ（get_quote）から直接現在値を取る。呼び出し回数の上限（無料枠 8回/分・800回/日）を
  // 守るため、自動更新ではスキャル欄で選んでいるペアだけを3分おき、ボタンを押したときは4通貨すべてを取る。
  var TD_SYM = { usdjpy: "USD/JPY", eurusd: "EUR/USD", eurjpy: "EUR/JPY", gbpjpy: "GBP/JPY" };
  var tdLast = {}, tdNote = "";
  function parseQuote(payload) {
    var txt = payload && (payload.result || payload);
    if (typeof txt !== "string") return null;
    var lines = txt.trim().split("\n"); if (lines.length < 2) return null;
    var h = lines[0].split(";"), v = lines[1].split(";"), o = {};
    h.forEach(function (k, i) { o[k] = v[i]; });
    var price = parseFloat(o.close), t = parseInt(o.last_quote_at || o.timestamp, 10);
    return price > 0 && t > 0 ? { price: price, time: t, src: "Twelve Data" } : null;
  }
  async function tdQuote(pair) {
    var r = await mcp.callTool("Twelve Data", "get_quote", { symbol: TD_SYM[pair] }, { cache: false });
    return parseQuote(r.payload);
  }
  function selectedPair() {
    var sp = document.getElementById("s-pair"), v = sp ? sp.value : "ドル円";
    return { "ドル円": "usdjpy", "ユーロドル": "eurusd", "ユーロ円": "eurjpy", "ポンド円": "gbpjpy" }[v] || "usdjpy";
  }
  async function runInner(manual) {
    if (busy) return;
    if (!mcp) { st.textContent = "この画面では現在値を読めないため、ボード作成時点の値です（claude.ai で開いてください）。"; return; }
    busy = true; btn.disabled = true;
    var pressed = new Date().toLocaleTimeString("ja-JP", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    st.textContent = pressed + " 読み込み中…";
    var data = null, prices = null, driveErr = null;
    try {
      // ①チャートの足（Mac が書く大きいファイル）は、手動時と5分ごとに、新しくなっていれば読む
      if (manual || Date.now() - lastChartCheck > 5 * 60000) {
        lastChartCheck = Date.now();
        var cf = await newest("fx_live_latest.json");
        if (cf && cf.modifiedTime !== chartStamp) { data = await load(cf); chartStamp = cf.modifiedTime; }
      }
      // ②価格（Mac が毎分書く小さいファイル）
      var pf = await newest("fx_live_prices.json"); prices = pf ? await load(pf) : null;
    } catch (e) { driveErr = e; }
    var sc = window.__fxSc ? window.__fxSc.data() : null, got = 0, newestT = 0, redraw = false, srcs = {}, tdErr = null;
    var nowS = Date.now() / 1000, sel = selectedPair();
    for (var pair in PAIRS) {
      var el = document.querySelector('.inst[data-pair="' + pair + '"]');
      var close = el ? parseFloat(el.getAttribute("data-close")) : NaN;
      var row = data && data.pairs && data.pairs[pair];
      var pp = prices && prices.pairs && prices.pairs[pair];
      var best = null;
      if (pp && pp.price) best = { price: pp.price, time: pp.time, src: "Mac" };
      if (row && row.price && (!best || row.time > best.time)) best = { price: row.price, time: row.time, src: "Mac" };
      // Drive の値が無い・5分より古いときは Twelve Data から取る
      if ((!best || nowS - best.time > 300) && (manual || (pair === sel && Date.now() - (tdLast[pair] || 0) > 180000))) {
        try {
          tdLast[pair] = Date.now();
          var q = await tdQuote(pair);
          if (q && (!best || q.time > best.time)) best = q;
        } catch (e) { tdErr = e; }
      } else if (latest[pair] && latest[pair].src === "Twelve Data" && (!best || latest[pair].time > best.time)) {
        best = latest[pair];   // 前回 Twelve Data で取った値のほうが新しければそれを使う
      }
      if (best) {
        setRate(pair, best.price, "現在 " + hhmm(best.time), close);
        newestT = Math.max(newestT, best.time); got++; latest[pair] = best; srcs[best.src] = 1;
      }
      var C = sc && sc[PAIRS[pair]];
      if (C && row && row.tf) { C.tf = row.tf; C.最終 = row.最終 || ""; C.src = "更新ボタンで取得"; C.error = null; }
      if (C && best && extendBars(C, best.price, best.time)) redraw = true;
    }
    if ((data || redraw) && window.__fxSc) window.__fxSc.draw();
    var age = newestT ? Math.round((nowS - newestT) / 60) : null;
    var why = function (e) { return WHY[e && e.code] || (e && (e.message || e.code)) || "不明"; };
    st.textContent = pressed + " に確認（" + got + "通貨・" + (Object.keys(srcs).join("＋") || "取得なし") + "）"
      + (age !== null ? "・価格は約" + age + "分前の値" : "")
      + (prices && prices.更新 && nowS - Date.parse(prices.更新) / 1000 > 600 ? "／★Mac の価格の書き込みが止まっています（Twelve Data で補っています）" : "")
      + (driveErr && !got ? "／Drive: " + why(driveErr) : "")
      + (tdErr ? "／Twelve Data: " + why(tdErr) : "");
    busy = false; btn.disabled = false;
  }
  var ticking = false;
  function startTick() {
    if (ticking) return; ticking = true;
    setInterval(function () { if (document.visibilityState === "visible") run(false); }, 60000);
  }
  btn.onclick = function () { run(true); startTick(); };
  // 2026-10-15 落合さん指示「スキャル分析のボタンを押したら、そこでも最新のチャートに更新」:
  // スキャル分析はまずここを呼び、足のファイル・現在値を読み直してチャートを描き直してから分析する。
  var mcpReady = null;
  window.__fxLive = {
    refresh: async function () {
      if (!mcp && mcpReady) { try { await mcpReady; } catch (e) {} }
      if (!mcp) return { ok: false, why: "現在値を読めないため、ボード作成時点の足で分析します" };
      await run(true); startTick();
      return { ok: true };
    },
    latest: function (pair) { return latest[pair] || null; },
    label: hhmm
  };
  // 開いたときにも1回だけ自動で読む（Driveの利用を許可済みの時だけ。未許可ならボタンで）
  if (window.claude && claude.use) {
    mcpReady = Promise.all([claude.use("mcp"), claude.use("permissions")]).then(async function (r) {
      mcp = r[0];
      if (!mcp) return;
      var ok = true;
      try {
        if (r[1]) {
          var g1 = await r[1].state("mcp:Google Drive"), g2 = await r[1].state("mcp:Twelve Data");
          ok = g1 === "granted" || g2 === "granted";
        }
      } catch (e) { ok = false; }
      if (ok) {
        run(false);
        // 開いている間は1分ごとに現在値を読み、チャートの最後の足を伸ばす（画面が隠れている間は止める）
        startTick();
      }
      else st.textContent = "「現在価格に更新」を押すと、Google Drive から最新価格を読み込みます（初回は許可の確認が出ます）。";
    });
  }
})();
</script>
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
  // ── スキャル用チャート（2026-10-13 落合さん指示「実績が見えない。チャートでビジュアルに」）──
  // ローソク＋EMA20/50、研究の最良ルールのペーパー売買（▲▼→●）、AIの案（◆）と最新案の線、
  // 下に成績タイルと累計pipsの推移。AIの案はその後の15分足で自動判定する。
  var SC = { pair: "ドル円", tf: "15分", hist: [], lastBy: {}, histCol: null, zoom: {} };
  var scAll = null;
  try { var sce = document.getElementById("scalp-chart"); if (sce) scAll = JSON.parse(sce.textContent); } catch (e) { scAll = null; }
  function scSpec(p) { return p === "ユーロドル" ? { dec: 5, mult: 10000 } : { dec: 3, mult: 100 }; }
  function scT(min) {
    try { return new Date(min * 60000).toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return String(min); }
  }
  function scIdx(bars, tm) {   // tm（分）を含む足の番号。範囲外は -1
    if (!bars.length || tm < bars[0][0]) return -1;
    var lo = 0, hi = bars.length - 1;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (bars[mid][0] <= tm) lo = mid; else hi = mid - 1; }
    return lo;
  }
  // AIの案を15分足で自動判定（約定: 2時間以内に入る値へ到達／決着: その後4時間以内に利確・損切り）
  function scEvalAI(h) {
    if (h.auto && h.auto.done) return h.auto;
    var side = /買/.test(h.verdict || "") ? 1 : /売/.test(h.verdict || "") ? -1 : 0;
    if (!side || h.entry == null || h.stop == null) return { done: true, res: "見送り" };
    var d = scAll && scAll[h.pair || "ドル円"] && scAll[h.pair || "ドル円"].tf["15分"];
    if (!d || !d.length) return { done: false, res: "判定中" };
    var S = scSpec(h.pair), t0 = Math.floor(Date.parse(h.at) / 60000);
    var e = Number(h.entry), sl = Number(h.stop), tp = h.take_profit != null ? Number(h.take_profit) : null;
    var i0 = scIdx(d, t0); if (i0 < 0) return { done: false, res: "判定不能（足が古い）" };
    var fill = -1;
    for (var i = i0; i < d.length && d[i][0] <= t0 + 120; i++) { if (d[i][3] <= e && e <= d[i][2]) { fill = i; break; } }
    if (fill < 0) return d[d.length - 1][0] > t0 + 120 ? { done: true, res: "未約定" } : { done: false, res: "判定中" };
    for (var j = fill; j < d.length && j <= fill + 16; j++) {
      var hitSL = side > 0 ? d[j][3] <= sl : d[j][2] >= sl;
      var hitTP = tp != null && (side > 0 ? d[j][2] >= tp : d[j][3] <= tp);
      if (hitSL) return { done: true, res: "負け", pips: Math.round(((sl - e) * side * S.mult - 0.8) * 10) / 10, out: d[j][0], x: sl, in: d[fill][0] };
      if (hitTP) return { done: true, res: "勝ち", pips: Math.round(((tp - e) * side * S.mult - 0.8) * 10) / 10, out: d[j][0], x: tp, in: d[fill][0] };
    }
    if (fill + 16 < d.length) {
      var c = d[fill + 16][4], pp = Math.round(((c - e) * side * S.mult - 0.8) * 10) / 10;
      return { done: true, res: pp > 0 ? "勝ち" : "負け", why: "時間切れ", pips: pp, out: d[fill + 16][0], x: c, in: d[fill][0] };
    }
    return { done: false, res: "保有中", in: d[fill][0] };
  }
  function scDraw() {
    var svg = $("sc-svg"), wrap = $("sc-wrap");
    if (!svg) return;
    var C = scAll && scAll[SC.pair];
    if (!C || C.error || !C.tf || !C.tf[SC.tf]) { wrap.querySelector("#sc-title").textContent = "チャート（データなし" + (C && C.error ? "：" + C.error : "") + "）"; return; }
    // 画面幅に合わせて描く（スマホで文字が潰れないように）。狭い画面は直近の足だけ
    var VW = Math.max(340, Math.min(1000, Math.round(wrap.clientWidth - 24) || 720));
    var S = scSpec(SC.pair), bars = C.tf[SC.tf];
    // 2026-10-13: 時間足ごとに既定の表示本数を決め（長い足ほど本数を絞って売買点を広げる）、
    // ＋／−で拡大縮小。1本あたり最低4pxを保つ（スマホは自動で本数を減らす）
    var DEF = { "15分": 96, "30分": 96, "1時間": 120, "4時間": 90, "8時間": 60, "12時間": 60, "24時間": 45 };
    var maxByW = Math.floor((VW - 76) / 4);
    var want = SC.zoom[SC.tf] || DEF[SC.tf] || 100;
    var keep = Math.max(20, Math.min(want, bars.length, maxByW));
    bars = bars.slice(-keep);
    var n = bars.length;
    var zl = $("sc-zoomlbl");
    if (zl) zl.textContent = n + "本（" + scT(bars[0][0]).slice(0, 5) + "〜）";
    svg.setAttribute("viewBox", "0 0 " + VW + " 320");
    $("sc-title").textContent = SC.pair + " " + SC.tf + "足（最終 " + (C.最終 || "") + "・" + (C.src || "ボード更新時点") + "）";
    var X0 = 8, X1 = VW - 68, Y0 = 10, Y1 = 290, W = (X1 - X0) / n;
    var hi = -Infinity, lo = Infinity;
    bars.forEach(function (b) { hi = Math.max(hi, b[2]); lo = Math.min(lo, b[3]); });
    var last = SC.lastBy[SC.pair], lr = last && last.result, lside = lr ? (/買/.test(lr.verdict || "") ? 1 : /売/.test(lr.verdict || "") ? -1 : 0) : 0;
    var lines = [];
    if (lside && lr.entry != null) {
      [["入る値", lr.entry, "ent"], ["利確", lr.take_profit, "up"], ["損切り", lr.stop, "down"]].forEach(function (x) {
        if (x[1] != null && !isNaN(Number(x[1]))) { lines.push(x); hi = Math.max(hi, Number(x[1])); lo = Math.min(lo, Number(x[1])); }
      });
    }
    var pad = (hi - lo) * 0.14 || 1 / S.mult; hi += pad; lo -= pad;   // 足の上下に売買の印を置く余白
    var y = function (v) { return Y0 + (hi - v) / (hi - lo) * (Y1 - Y0); };
    var x = function (i) { return X0 + (i + 0.5) * W; };
    var g = "";
    for (var k = 0; k <= 4; k++) {
      var v = lo + (hi - lo) * k / 4, yy = y(v);
      g += "<line class='gl' x1='" + X0 + "' x2='" + X1 + "' y1='" + yy + "' y2='" + yy + "'/><text class='ax' x='" + (X1 + 6) + "' y='" + (yy + 4) + "'>" + v.toFixed(S.dec) + "</text>";
    }
    var step = Math.ceil(n / Math.max(2, Math.floor(VW / 120)));
    for (var i = 0; i < n; i += step) g += "<text class='ax' x='" + (i ? x(i) : X0) + "' y='308' text-anchor='" + (i ? "middle" : "start") + "'>" + scT(bars[i][0]) + "</text>";
    var bw = Math.max(1, W * 0.62);
    bars.forEach(function (b, i) {
      var up = b[4] >= b[1], cls = up ? "cu" : "cd", xx = x(i);
      g += "<line class='" + cls + "' x1='" + xx + "' x2='" + xx + "' y1='" + y(b[2]) + "' y2='" + y(b[3]) + "' stroke-width='1'/>"
         + "<rect class='" + cls + "' x='" + (xx - bw / 2) + "' width='" + bw + "' y='" + Math.min(y(b[1]), y(b[4])) + "' height='" + Math.max(1, Math.abs(y(b[1]) - y(b[4]))) + "'/>";
    });
    ["e20", "e50"].forEach(function (c, ci) {
      g += "<polyline class='" + c + "' points='" + bars.map(function (b, i) { return x(i).toFixed(1) + "," + y(b[5 + ci]).toFixed(1); }).join(" ") + "'/>";
    });
    // ペーパー売買（2026-10-13 再設計）: 長い足でつぶれないよう、①▲は足の安値の下・▼は高値の上に置く
    // ②同じ足・同じルール・同じ向きはまとめて1つの印＋件数 ③決済の●は、入った足と別の足なら決済値に、
    // 同じ足なら▲▼の隣に小さく置く ④印の大きさは足の幅に合わせる（5〜9px）
    var marks = {}, ms = Math.max(5, Math.min(9, W * 0.8)), groups = {};
    (C.paper || []).forEach(function (P, pi) {
      (P.trades || []).forEach(function (t) {
        var ii = scIdx(bars, t.in); if (ii < 0) return;
        var key = ii + "|" + pi + "|" + t.d;
        (groups[key] = groups[key] || { ii: ii, pi: pi, d: t.d, list: [] }).list.push(t);
        (marks[ii] = marks[ii] || []).push(P.tf + "ルール " + (t.d > 0 ? "買" : "売") + " " + t.e.toFixed(S.dec) + (t.open ? "（保有中）" : " → " + t.x.toFixed(S.dec) + "（" + t.why + " " + (t.pips > 0 ? "+" : "") + t.pips + "pips）"));
      });
    });
    var stackB = {}, stackS = {};   // 同じ足に複数ルールの印が来たら上下にずらす
    Object.keys(groups).forEach(function (k) {
      var G = groups[k], b = bars[G.ii], xe = x(G.ii), hol = G.pi === 1 ? " hollow" : "", dir = G.d > 0 ? "tb" : "ts";
      var lvl = G.d > 0 ? (stackB[G.ii] = (stackB[G.ii] || 0) + 1) : (stackS[G.ii] = (stackS[G.ii] || 0) + 1);
      var off = 4 + (lvl - 1) * (ms * 2 + 4);
      var yb = G.d > 0 ? y(b[3]) + off : y(b[2]) - off;   // 三角の先端
      var tri = G.d > 0 ? [[xe, yb], [xe - ms, yb + ms * 1.6], [xe + ms, yb + ms * 1.6]] : [[xe, yb], [xe - ms, yb - ms * 1.6], [xe + ms, yb - ms * 1.6]];
      G.list.forEach(function (t) {
        if (t.open) return;
        var io = scIdx(bars, t.out);
        if (io > G.ii + 1) {   // 別の足で決着: 決済値に●と、入りからの点線
          g += "<line class='tl' x1='" + xe + "' y1='" + y(t.e) + "' x2='" + x(io) + "' y2='" + y(t.x) + "'/>"
             + "<circle class='" + (t.pips > 0 ? "xw" : "xl") + "' cx='" + x(io) + "' cy='" + y(t.x) + "' r='" + (ms * 0.6) + "'/>";
        }
      });
      g += "<polygon class='" + dir + hol + "' points='" + tri.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") + "'/>";
      // 同じ足（または次の足）で決着したものは、三角の外側に勝ち負けの●を並べる
      var near = G.list.filter(function (t) { return !t.open && scIdx(bars, t.out) <= G.ii + 1; });
      var ybase = G.d > 0 ? yb + ms * 1.6 + ms * 0.7 + 2 : yb - ms * 1.6 - ms * 0.7 - 2;
      near.slice(0, 3).forEach(function (t, q) {
        g += "<circle class='" + (t.pips > 0 ? "xw" : "xl") + "' cx='" + (xe + (q - (Math.min(3, near.length) - 1) / 2) * ms * 1.3) + "' cy='" + ybase + "' r='" + (ms * 0.55) + "'/>";
      });
      if (G.list.length > 1) g += "<text class='pt ent' x='" + (xe + ms + 3) + "' y='" + (G.d > 0 ? yb + ms * 1.6 : yb - ms * 0.4) + "'>×" + G.list.length + "</text>";
    });
    // AIの案（◆）
    SC.hist.filter(function (h) { return (h.pair || "ドル円") === SC.pair; }).forEach(function (h) {
      var ii = scIdx(bars, Math.floor(Date.parse(h.at) / 60000)); if (ii < 0) return;
      var side = /買/.test(h.verdict || "") ? "buy" : /売/.test(h.verdict || "") ? "sell" : "flat";
      var pv = h.entry != null ? Number(h.entry) : Number(h.rate); if (isNaN(pv)) return;
      var xx = x(ii), yy = y(pv);
      g += "<polygon class='ai " + side + "' points='" + [[xx, yy - ms * 1.1], [xx + ms, yy], [xx, yy + ms * 1.1], [xx - ms, yy]].map(function (p) { return p.join(","); }).join(" ") + "'/>";
      var ev = scEvalAI(h);
      (marks[ii] = marks[ii] || []).push("AIの案 " + (h.verdict || "見送り") + (h.entry != null ? " " + Number(h.entry).toFixed(S.dec) : "") + "（" + ev.res + (ev.pips != null ? " " + (ev.pips > 0 ? "+" : "") + ev.pips + "pips" : "") + "）");
    });
    // シグナル配信（★・正式）
    (C.signals || []).forEach(function (sg) {
      var ii = scIdx(bars, Math.floor(Date.parse(sg.sent_at) / 60000)); if (ii < 0) return;
      var b = bars[ii], xx = x(ii), r = ms * 1.7;
      var yy = sg.d > 0 ? y(b[3]) + r + 6 : y(b[2]) - r - 6;
      var pts = [];
      for (var q = 0; q < 10; q++) { var a = -Math.PI / 2 + q * Math.PI / 5, rr = q % 2 ? r * 0.45 : r; pts.push((xx + rr * Math.cos(a)).toFixed(1) + "," + (yy + rr * Math.sin(a)).toFixed(1)); }
      g += "<polygon class='sg' points='" + pts.join(" ") + "'/>";
      if (sg.result) g += "<circle class='" + (sg.pips > 0 ? "xw" : "xl") + "' cx='" + (xx + r + 4) + "' cy='" + yy + "' r='" + (ms * 0.55) + "'/>";
      (marks[ii] = marks[ii] || []).push("★シグナル " + sg.side + " " + Number(sg.entry).toFixed(S.dec) + "（" + sg.tf + "足・想定勝率" + Math.round(sg.win * 100) + "%）"
        + (sg.result ? " → " + sg.result + " " + (sg.pips > 0 ? "+" : "") + sg.pips + "pips（" + sg.why + "）" : " → 判定中"));
    });
    // 最新案の線
    if (lines.length) {
      var li = scIdx(bars, Math.floor(Date.parse(last.at) / 60000)), lx = li < 0 ? X0 : x(li);
      lines.forEach(function (L) {
        var yy = y(Number(L[1]));
        g += "<line class='pl " + L[2] + "' x1='" + lx + "' x2='" + X1 + "' y1='" + yy + "' y2='" + yy + "'/>"
           + "<text class='pt " + L[2] + "' x='" + (X1 - 4) + "' y='" + (yy - 4) + "' text-anchor='end'>" + L[0] + " " + Number(L[1]).toFixed(S.dec) + "</text>";
      });
    }
    g += "<line class='ch' id='sc-ch' x1='0' x2='0' y1='" + Y0 + "' y2='" + Y1 + "' visibility='hidden'/>"
       + "<rect id='sc-hit' x='" + X0 + "' y='" + Y0 + "' width='" + (X1 - X0) + "' height='" + (Y1 - Y0) + "' fill='transparent'/>";
    svg.innerHTML = g;
    var tip = $("sc-tip"), ch = $("sc-ch");
    var move = function (ev) {
      var r = svg.getBoundingClientRect(), sx = (ev.clientX - r.left) / r.width * VW;
      var i = Math.max(0, Math.min(n - 1, Math.floor((sx - X0) / W))), b = bars[i];
      ch.setAttribute("x1", x(i)); ch.setAttribute("x2", x(i)); ch.setAttribute("visibility", "visible");
      tip.innerHTML = "<b>" + esc(scT(b[0])) + "</b><br>始 " + b[1].toFixed(S.dec) + "　高 " + b[2].toFixed(S.dec) + "<br>安 " + b[3].toFixed(S.dec) + "　終 " + b[4].toFixed(S.dec)
        + (marks[i] ? "<br>" + marks[i].map(esc).join("<br>") : "");
      tip.hidden = false;
      var px = x(i) / VW * r.width;
      tip.style.left = Math.min(px + 12, r.width - tip.offsetWidth - 4) + "px"; tip.style.top = "8px";
    };
    var hit = $("sc-hit");
    hit.addEventListener("pointermove", move); hit.addEventListener("pointerdown", move);
    hit.addEventListener("pointerleave", function () { tip.hidden = true; ch.setAttribute("visibility", "hidden"); });
    scStats(C, S, VW);
  }
  // いま有効なシグナル（配信から15分以内）を大きく、無ければ直近の配信を小さく出す
  function scSigBox(C, S) {
    var el = $("sc-sig"); if (!el) return;
    var sgs = C.signals || [], last = sgs[sgs.length - 1], now = Date.now();
    var upd = C.sig_updated ? "（最終判定 " + esc(fmtAt(C.sig_updated)) + "・15分ごと・ボードは最大1時間遅れ。正式な通知はメール）" : "";
    if ((SC.pair === "ユーロ円" || SC.pair === "ポンド円") && !sgs.length) {   // 2026-10-15 クロス円は研究中＝シグナル配信の対象外
      el.innerHTML = "<div class='k'>★ シグナル配信（正式）</div><div>" + SC.pair + "のスキャルはシグナル配信の対象外です（日足の売買ルールのみ運用中・2026-10-15〜）。下のチャートとペーパー売買は研究の確認用です。</div>";
      return;
    }
    if (last && now - Date.parse(last.sent_at) < 15 * 60000) {
      var side = last.d > 0 ? "buy" : "sell";
      el.className = "sc-sig live " + side;
      el.innerHTML = "<div class='k'>★ シグナル配信（正式）— 配信から15分以内</div><div class='big'>" + esc(SC.pair) + "　" + esc(last.side) + (last.d > 0 ? "い" : "り") + "　" + Number(last.entry).toFixed(S.dec) + "</div>"
        + "<div class='row'><span>利確 <b class='mono up'>" + Number(last.tp).toFixed(S.dec) + "</b></span><span>損切り <b class='mono down'>" + Number(last.stop).toFixed(S.dec) + "</b></span>"
        + "<span>想定勝率 <b>" + Math.round(last.win * 100) + "%</b>（" + esc(last.tf) + "足）</span><span>" + esc(fmtAt(last.sent_at)) + " 配信</span></div>";
    } else {
      el.className = "sc-sig";
      el.innerHTML = "<div class='k'>★ シグナル配信（正式）</div><div>いま有効なシグナルはありません" + upd + "</div>"
        + (last ? "<div class='row'><span>直近: " + esc(fmtAt(last.sent_at)) + " " + esc(last.side) + " " + Number(last.entry).toFixed(S.dec) + "（" + esc(last.tf) + "足）</span><span>" + (last.result ? esc(last.result) + " " + (last.pips > 0 ? "+" : "") + last.pips + "pips" : "判定中") + "</span></div>" : "<div class='row'><span>まだ配信はありません（想定勝率50%超の条件がそろうとメールが届きます）</span></div>");
    }
  }
  function scStats(C, S, VW) {
    var now = (C.tf["15分"] && C.tf["15分"].length) ? C.tf["15分"][C.tf["15分"].length - 1][0] : 0;
    var series = [], tiles = "", rows = [];
    (C.paper || []).forEach(function (P, pi) {
      var ts = (P.trades || []).filter(function (t) { return !t.open; });
      var w = ts.filter(function (t) { return t.pips > 0; }).length, sum = ts.reduce(function (a, t) { return a + t.pips; }, 0);
      var op = (P.trades || []).filter(function (t) { return t.open; }).length;
      tiles += "<div><span>ペーパー " + esc(P.tf) + "ルール（" + (pi === 0 ? "塗り▲▼" : "白抜き△▽") + "・直近30日）</span>"
        + "<b class='" + (sum >= 0 ? "up" : "down") + "'>" + (sum >= 0 ? "+" : "") + sum.toFixed(1) + " pips</b>"
        + "<small>" + ts.length + "回・勝ち" + w + "（勝率" + (ts.length ? Math.round(100 * w / ts.length) : 0) + "%）" + (op ? "・保有中" + op : "") + "<br>検証: 勝率" + Math.round(P.勝率 * 100) + "%・PF" + P.PF + "（" + P.回数 + "回）</small></div>";
      var cum = 0; series.push({ name: P.tf + "ルール", cls: "s" + (pi + 1), pts: ts.map(function (t) { cum += t.pips; return [t.out, cum]; }) });
      ts.forEach(function (t) { rows.push([t.in, P.tf + "ルール", t.d > 0 ? "買" : "売", t.e.toFixed(S.dec) + " → " + t.x.toFixed(S.dec), t.why, t.pips]); });
    });
    var mine = SC.hist.filter(function (h) { return (h.pair || "ドル円") === SC.pair; });
    var ev = mine.map(function (h) { return [h, scEvalAI(h)]; });
    var done = ev.filter(function (x) { return x[1].pips != null && x[1].done; });
    var aw = done.filter(function (x) { return x[1].pips > 0; }).length, asum = done.reduce(function (a, x) { return a + x[1].pips; }, 0);
    var skip = ev.filter(function (x) { return x[1].res === "見送り"; }).length, nofill = ev.filter(function (x) { return x[1].res === "未約定"; }).length;
    tiles += "<div><span>AIの案（◆・ボタン・参考）</span><b class='" + (!done.length ? "" : asum >= 0 ? "up" : "down") + "'>" + (done.length ? (asum >= 0 ? "+" : "") + asum.toFixed(1) + " pips" : "記録待ち") + "</b>"
      + "<small>" + mine.length + "回分析・決着" + done.length + "（勝ち" + aw + "）・未約定" + nofill + "・見送り" + skip
      + (function () { var wr = done.filter(function (x) { return x[0].win_rate != null; });
          return wr.length ? "<br>想定勝率の平均 " + Math.round(wr.reduce(function (a, x) { return a + Number(x[0].win_rate); }, 0) / wr.length) + "% ／ 実際 " + Math.round(100 * wr.filter(function (x) { return x[1].pips > 0; }).length / wr.length) + "%" : ""; })()
      + "</small></div>";
    var sgs = C.signals || [], sgd = sgs.filter(function (x) { return x.result; });
    var sgw = sgd.filter(function (x) { return x.pips > 0; }).length, sgsum = sgd.reduce(function (a, x) { return a + x.pips; }, 0);
    tiles = "<div><span>★ シグナル配信（正式・自動採点）</span><b class='" + (!sgd.length ? "" : sgsum >= 0 ? "up" : "down") + "'>" + (sgd.length ? (sgsum >= 0 ? "+" : "") + sgsum.toFixed(1) + " pips" : "配信待ち") + "</b>"
      + "<small>" + sgs.length + "回配信・決着" + sgd.length + "（勝ち" + sgw + (sgd.length ? "・勝率" + Math.round(100 * sgw / sgd.length) + "%" : "") + "）"
      + (sgd.length ? "<br>想定勝率の平均 " + Math.round(100 * sgd.reduce(function (a, x) { return a + x.win; }, 0) / sgd.length) + "%" : "") + "</small></div>" + tiles;
    var cum4 = 0; series.push({ name: "シグナル配信", cls: "s4", pts: sgd.map(function (x) { cum4 += x.pips; return [Math.floor(Date.parse(x.out_at ? x.out_at.replace(" ", "T") + ":00+09:00" : x.sent_at) / 60000), cum4]; }) });
    sgd.forEach(function (x) { rows.push([Math.floor(Date.parse(x.sent_at) / 60000), "★シグナル（" + x.tf + "）", x.side, Number(x.fill || x.entry).toFixed(S.dec) + " → " + Number(x.exit).toFixed(S.dec), x.why, x.pips]); });
    $("sc-stats").innerHTML = tiles;
    scSigBox(C, S);
    var cum2 = 0; series.push({ name: "AIの案", cls: "s3", pts: done.slice().sort(function (a, b) { return (a[1].out || 0) - (b[1].out || 0); }).map(function (x) { cum2 += x[1].pips; return [x[1].out, cum2]; }) });
    done.forEach(function (x) { rows.push([x[1].in || Math.floor(Date.parse(x[0].at) / 60000), "AIの案", /買/.test(x[0].verdict) ? "買" : "売", Number(x[0].entry).toFixed(S.dec) + " → " + Number(x[1].x).toFixed(S.dec), x[1].why || (x[1].res === "勝ち" ? "利確" : "損切り"), x[1].pips]); });
    // 累計pipsの推移
    var t0 = now - 30 * 1440, all = [];
    series.forEach(function (s) { s.pts.forEach(function (p) { all.push(p[1]); }); });
    var eq = $("sc-eq"), X0 = 8, X1 = VW - 60, Y0 = 18, Y1 = 150;
    eq.setAttribute("viewBox", "0 0 " + VW + " 172");
    if (!all.length) { eq.innerHTML = "<text class='ax' x='10' y='40'>決着した売買がまだありません</text>"; }
    else {
      var mx = Math.max(0, Math.max.apply(null, all)), mn = Math.min(0, Math.min.apply(null, all)), sp = (mx - mn) || 1;
      var xe = function (t) { return X0 + Math.max(0, (t - t0)) / (now - t0) * (X1 - X0); }, ye = function (v) { return Y0 + (mx - v) / sp * (Y1 - Y0); };
      var h = "<text class='ax' x='" + X0 + "' y='12'>累計pips（直近30日・決着順）</text><line class='z' x1='" + X0 + "' x2='" + X1 + "' y1='" + ye(0) + "' y2='" + ye(0) + "'/>"
        + "<text class='ax' x='" + (X1 + 6) + "' y='" + (ye(0) + 4) + "'>0</text><text class='ax' x='" + (X1 + 6) + "' y='" + (ye(mx) + 4) + "'>" + mx.toFixed(0) + "</text>" + (ye(mn) - ye(0) > 14 ? "<text class='ax' x='" + (X1 + 6) + "' y='" + (ye(mn) + 4) + "'>" + mn.toFixed(0) + "</text>" : "")
        + "<text class='ax' x='" + X0 + "' y='166'>" + scT(t0) + "</text><text class='ax' x='" + X1 + "' y='166' text-anchor='end'>" + scT(now) + "</text>";
      series.forEach(function (s) {
        if (!s.pts.length) return;
        var pts = [[t0, 0]].concat(s.pts.filter(function (p) { return p[0] >= t0; })), d = "";
        pts.push([now, pts[pts.length - 1][1]]);   // 最後の決着から現在まで横に延ばす
        pts.forEach(function (p, i) { d += (i ? " L" : "M") + xe(p[0]).toFixed(1) + "," + ye(i ? pts[i - 1][1] : 0).toFixed(1) + " L" + xe(p[0]).toFixed(1) + "," + ye(p[1]).toFixed(1); });
        var lp = pts[pts.length - 1];
        h += "<path class='" + s.cls + "' d='" + d + "'/><text class='lb' x='" + Math.min(xe(lp[0]) + 4, X1 - 4) + "' y='" + (ye(lp[1]) - 5) + "' text-anchor='end'>" + esc(s.name) + " " + (lp[1] >= 0 ? "+" : "") + lp[1].toFixed(1) + "</text>";
      });
      eq.innerHTML = h;
    }
    rows.sort(function (a, b) { return b[0] - a[0]; });
    $("sc-table").innerHTML = rows.length ? "<table><tr><th>入った時刻</th><th>種類</th><th>売買</th><th>入→出</th><th>決着</th><th>pips</th></tr>"
      + rows.slice(0, 60).map(function (r) { return "<tr><td>" + esc(scT(r[0])) + "</td><td>" + esc(r[1]) + "</td><td>" + r[2] + "</td><td class='mono'>" + r[3] + "</td><td>" + esc(r[4]) + "</td><td class='" + (r[5] > 0 ? "up" : "down") + "'>" + (r[5] > 0 ? "+" : "") + r[5] + "</td></tr>"; }).join("") + "</table>"
      : "<p class='note'>まだありません。</p>";
    // 決着したAIの案は結果を保存（足が流れても残す）
    if (SC.histCol) ev.forEach(function (x) {
      if (x[1].done && x[1].res !== "見送り" && !(x[0].auto && x[0].auto.done) && x[0]._id) {
        x[0].auto = x[1];
        try { SC.histCol.doc(x[0]._id).update({ auto: JSON.parse(JSON.stringify(x[1])) }).catch(function () {}); } catch (e) {}
      }
    });
  }
  (function () {
    var cur = function () { var C = scAll && scAll[SC.pair]; var n = C && C.tf && C.tf[SC.tf] ? C.tf[SC.tf].length : 100;
      var v = document.querySelector("#sc-zoomlbl"); var m = v && /^(\d+)/.exec(v.textContent); return { now: m ? +m[1] : 100, max: n }; };
    var zin = $("sc-zin"), zout = $("sc-zout"), zr = $("sc-zreset");
    if (zin) zin.onclick = function () { var c = cur(); SC.zoom[SC.tf] = Math.max(20, Math.round(c.now / 1.5)); scDraw(); };
    if (zout) zout.onclick = function () { var c = cur(); SC.zoom[SC.tf] = Math.min(c.max, Math.round(c.now * 1.5)); scDraw(); };
    if (zr) zr.onclick = function () { delete SC.zoom[SC.tf]; scDraw(); };
  })();
  document.querySelectorAll("#sc-wrap .sc-tabs button[data-tf]").forEach(function (b) {
    b.onclick = function () {
      SC.tf = b.dataset.tf;
      try { localStorage.setItem("fx_scalp_tf", SC.tf); } catch (e) {}
      document.querySelectorAll("#sc-wrap .sc-tabs button[data-tf]").forEach(function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      scDraw();
    };
  });
  (function () {
    try { var st = localStorage.getItem("fx_scalp_tf"); if (st && scAll && scAll["ドル円"] && scAll["ドル円"].tf && scAll["ドル円"].tf[st]) {
      SC.tf = st; document.querySelectorAll("#sc-wrap .sc-tabs button[data-tf]").forEach(function (o) { o.setAttribute("aria-pressed", o.dataset.tf === st ? "true" : "false"); }); } } catch (e) {}
    var ps = $("s-pair"); if (ps) SC.pair = ps.value; try { var sp = localStorage.getItem("fx_scalp_pair"); if (sp) SC.pair = sp; } catch (e) {} })();
  scDraw();
  // 2026-10-15: 「現在価格に更新」がチャートの足を最新に差し替えて描き直すための口
  window.__fxSc = { data: function () { return scAll; }, draw: function () { scDraw(); },
                    pair: function () { return SC.pair; }, tf: function () { return SC.tf; } };
  var scRz = null;
  window.addEventListener("resize", function () { clearTimeout(scRz); scRz = setTimeout(scDraw, 200); });

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
        : p === "ユーロ円"
        ? { name: p, code: "EUR/JPY", mult: 100, dec: 3, unit: "円", jpy: 1, zone: "±0.15円", other: "ドル円" }
        : p === "ポンド円"
        ? { name: p, code: "GBP/JPY", mult: 100, dec: 3, unit: "円", jpy: 1, zone: "±0.20円", other: "ドル円" }
        : { name: "ドル円", code: "USD/JPY", mult: 100, dec: 3, unit: "円", jpy: 1, zone: "±0.15円", other: "ユーロドル" };
    }
    var refs = db ? { "ドル円": db.doc("analysis/scalp"), "ユーロドル": db.doc("analysis/scalp_eurusd"),
                      "ユーロ円": db.doc("analysis/scalp_eurjpy"),
                      "ポンド円": db.doc("analysis/scalp_gbpjpy") } : null;   // 2026-10-15 ユーロ円・ポンド円追加
    var lastBy = { "ドル円": null, "ユーロドル": null, "ユーロ円": null, "ポンド円": null };
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
      var big = side > 0 ? "参考：今入るなら 買い（ロング）" : side < 0 ? "参考：今入るなら 売り（ショート）" : "参考：今は入らない";
      var sub = S.code + "・" + esc(r.headline || "") + (r.confidence ? "・自信 " + esc(r.confidence) : "")
        + (r.win_rate != null ? "・想定勝率 " + esc(r.win_rate) + "%" : "");
      h += "<div class='ticket " + cls + "'><div class='t-head'><div class='t-pair'>" + S.code + "</div>"
         + "<div class='t-do'>" + big + "</div><div class='t-sub'>" + sub + "</div></div>";
      // 2026-10-13: 判断を緩めた代わりに、自信『低』の案はリスクを半分にして数量を出す
      var lowConf = /低/.test(String(r.confidence || ""));
      var effRisk = (doc.riskRate || 0.02) * (lowConf ? 0.5 : 1);
      if (side && r.entry != null && r.stop != null) {
        var u = units(r.entry, r.stop, effRisk, S);
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
           + "<div><span>数量</span><b>" + u.units.toLocaleString() + "</b><small>通貨（" + (u.units / 1000) + "Lot）" + (lowConf ? "・自信『低』のためリスク半分（" + Math.round(effRisk * 1000) / 10 + "%）" : "") + "</small></div>"
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
      h += "<div class='t-foot'>これはボタンを押した時点の<b>参考</b>です。入る・入らないの正式な判断は、15分ごとの <b>★シグナル配信（メール）</b> で行います。"
         + (r.next_check ? "　次に見る時刻: <b>" + esc(r.next_check) + "</b>" : "") + "</div>";
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
      if (r.win_rate_basis) more += "<h4>想定勝率の根拠</h4><p>" + esc(r.win_rate_basis) + "</p>";
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
      SC.pair = pairSel.value; SC.lastBy = lastBy; scDraw();
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
    // AIの案の履歴（押すたびに1件。チャートの◆と自動判定に使う）
    if (db) {
      SC.histCol = db.collection("scalp_hist");
      SC.histCol.orderBy("at", "desc").limit(80).onSnapshot(function (snap) {
        SC.hist = snap.docs.map(function (d) { var o = d.data(); o._id = d.id; return o; });
        scDraw();
      }, function () {});
    }
    if (!sample) { sStatus.textContent += " ※「スキャル分析」は claude.ai の閲覧画面でのみ使えます。"; return; }
    sRun.hidden = false;
    var ctl = null, timer = null;
    sStop.onclick = function () { if (ctl) ctl.abort(); };
    sRun.onclick = async function () {
      sRun.disabled = true; sStop.hidden = false; ctl = new AbortController();
      var t0 = Date.now(); sStatus.textContent = "スキャル分析中…（0秒）";
      var sPhase = "最新のチャートに更新中…";
      timer = setInterval(function () { sStatus.textContent = sPhase + "（" + Math.round((Date.now() - t0) / 1000) + "秒）"; }, 1000);
      var riskRate = parseFloat(sRisk.value) || 0.02;
      selectPair();
      var S = spec(pairSel.value), runPair = pairSel.value, runRef = ref;
      var rate = sd && sd.値動き && sd.値動き["15分"] ? String(sd.値動き["15分"].終値) : "";
      // ①まず最新のチャート（足のファイル＋毎分の現在値）に更新してから分析する（2026-10-15 指示）
      var liveNote = "", LC = { "ドル円": "usdjpy", "ユーロドル": "eurusd", "ユーロ円": "eurjpy", "ポンド円": "gbpjpy" }[runPair];
      if (window.__fxLive) {
        try {
          var lr = await window.__fxLive.refresh();
          var lv = window.__fxLive.latest(LC);
          if (lv && lv.price) {
            rate = String(lv.price);
            liveNote = "【現在値（" + window.__fxLive.label(lv.time) + "・ボードの直近の値動きより新しい）】" + runPair + " " + lv.price
              + "。基準レート・エントリーはこの現在値を使う。";
          } else if (lr && !lr.ok) { liveNote = ""; }
        } catch (e) {}
      }
      sPhase = "スキャル分析中…" + (rate ? "（基準レート " + rate + "）" : "");
      // 現在値まで伸ばした15分足の直近8本も材料に添える
      try {
        var C0 = window.__fxSc && window.__fxSc.data()[runPair];
        var b15 = C0 && C0.tf && C0.tf["15分"] ? C0.tf["15分"].slice(-8) : [];
        if (b15.length) liveNote += "【最新の15分足（現在値まで・JST）】" + JSON.stringify(b15.map(function (b) {
          return { t: window.__fxLive ? window.__fxLive.label(b[0] * 60) : b[0], o: b[1], h: b[2], l: b[3], c: b[4] }; }));
      } catch (e) {}
      var p = [];
      if (liveNote) p.push(liveNote);
      p.push("対象の通貨ペアは【" + S.name + "（" + S.code + "）】。価格は小数" + S.dec + "桁・単位は" + S.unit
        + "・1pip＝" + (S.mult === 100 ? "0.01円" : "0.0001ドル") + "。相手の通貨（" + S.other
        + "）は" + (S.name === "ユーロ円" || S.name === "ポンド円" ? "円" : "ドル") + "の動きを確認する材料として使う（ボードに両方の値動き・研究が載っている）。");
      if (S.name === "ポンド円") p.push("ポンド円はドル円×ポンドドルで決まる通貨（2026-10-15 運用開始・日足は押し目戻り1本。スキャルはシグナル配信の対象外で、この案は参考）。値動きが大きく（ドル円の約1.3倍）、スプレッドもドル円の約4倍＝往復コスト1.5pipsで検証している。損切りは12pipsではなくATRに合わせて15〜25pips、利確はその1.2〜1.5倍にする。値動きの約7割はドル円の脚（円の強弱）、約3割はポンドの材料（英国の指標・BOE）。BOE・英CPI・日銀・介入警戒の★5級は避ける。スキャル研究のポンド円欄の上位条件（前半・後半ともPF1.1以上）に合うときだけ自信『中』以上。");
      if (S.name === "ユーロ円") p.push("ユーロ円はドル円×ユーロドルで決まる通貨（2026-10-15 運用開始・日足は押し目戻り1本。スキャルはシグナル配信の対象外で、この案は参考）。値動きの大半はドル円の脚（円の強弱）で、ドル円と同じ向きに動いている時は『円全体が動いている』。ドル円とユーロドルが逆に動いてユーロ円が動かない日（打ち消し）は様子見寄り。ECB・ユーロ圏CPI・日銀・介入警戒の★5級は両方とも避ける。スキャル研究のユーロ円欄の上位条件（前半・後半ともPF1.1以上のもの）に合うときだけ自信『中』以上。");
      if (S.name === "ユーロドル") p.push("ユーロドルはドル円より短い足の優位性が小さい（15分足はPF1未満）。前半・後半ともPF1.1以上の条件（例: 4時間足『EMA20押し目/戻り・EMA200と同方向』、30分足『RSI行き過ぎの戻り・EMA200と同方向』）に合えば自信『中』以上、PF1.0以上の条件に下の決まりで2つ合えば自信『低』で案を出してよい。ECB・ユーロ圏CPIなどユーロ側の★5級も避ける。");
      p.push("あなたは" + S.name + "のスキャルピング（数分〜数十分で5〜20pips程度を狙う超短期売買）の案を作る担当です。下の【運用ボードの内容】だけを材料にします。数字はボードの値を使い、無い数字は作らず『不明』と書くこと。");
      p.push("読む人は専門家ではありません。専門用語は避け、短い文で。これは本人のツール内の案であり投資助言ではない。");
      p.push("判断材料: テクニカル合議（15分足・1時間足）、ATR、直近の値動き・高値安値・節目（ファンダ研究欄の『次の焦点』にある節目）、ファンダメンタルズ（レジーム・材料バランス・翌日予測・ニュース総括・当局のけん制・指標イベント）。★5級イベントの前後30分は caution に必ず書く（案は出してよい。発注するかは本人が判断する）。");
      p.push("価格は『直近の値動き』の最終バー終値（あれば。無ければボードのヘッダのレート）を基準レートにし、エントリーは基準レートから" + S.zone + "以内、損切りは12pips前後（8〜16）、利確は損切りの1.2〜1.5倍で具体的な小数" + S.dec + "桁の数値を出す。数量は書かない（ページが口座資金とリスク率から計算する）。");
      p.push("判断の決まり（2026-10-13 落合さん指示: スキャルは『想定勝率が50%を超えれば必ず案を出す。発注は本人が判断』。研究結論の『合わなければ見送り』よりこちらを優先）: ①いまの状況に最も近い研究の条件を選び、想定勝率を出す。想定勝率＝その時間足の上位条件の勝率を基準に、『同じ入り方の時間帯幅別』でいまの時刻（JST）を含む時間帯の勝率があればそれを優先し、RSI14／EMA20とEMA50の配列／EMA200の向きが条件と逆なら1つにつき5ポイント下げる。②買い・売りの両方向で想定勝率を出し、高い方を candidate に必ず入れる（見送りでも価格つきで）。③このボタンの分析は『いま入るとしたら』の参考（2026-10-13 落合さん指示。入る・入らないの正式な判断は15分ごとの★シグナル配信が行う）。verdict は見送りにせず、必ず candidate と同じ向き（買／売）にする。想定勝率が50%以下なら headline に『勝率50%以下・様子見が無難』と書き、自信は『低』。④自信は、RSI・EMA配列・EMA200の向き・時間帯の4つのうち条件と合う数で決める（4つ＝高、3つ＝中、2つ以下＝低）。⑤★5級イベントの前後30分や『避ける時間帯』は勝率を下げる材料にしつつ、caution に書く。④『これまでの案と結果』で負けが続いている型（同じ時間帯・同じ向き）は避ける。⑤案を出すときは reason に『時間足=◯・RSI=◯・EMA200の向き=◯・時間帯=◯時台（研究の勝率◯%）』と数字で根拠を書く。損切り/利確はその足の『損切りpips目安／利確pips目安』を使う。");
      p.push("内部予想（見送りでも必ず）: forecast に 15分・1時間・4時間・日足 の4行を入れ、各行で『方向（上／下／横ばい）・確率（%）・想定レンジ（円）・根拠（RSI・EMA配列・ATR・節目・ファンダを数字で）』を書く。確率は50%を中立とし、材料が無いときは50〜55%に留める。entry_triggers には『何が起きたら買い／売りの案に変わるか』を価格と時刻で2〜4個、next_check には次に見るべき時刻（JST）を書く。");
      if (sd && sd.研究) p.push("【スキャル研究（短い足から日足まで・時間帯幅1/2/3/6時間・JSON）】\n" + JSON.stringify(sd.研究).slice(0, 24000));
      if (sd && sd.値動き) p.push("【直近の値動き（15分・30分・1時間・4時間・日足の指標と直近バー。時刻はJST）】\n" + JSON.stringify(sd.値動き).slice(0, 16000));
      if (logs.length) p.push("【これまでの案と結果（新しい順・最大10件）】\n" + JSON.stringify(logs.slice(0, 10).map(function (x) {
        return { 通貨: x.pair || "ドル円", 時刻: x.at, 売買: x.verdict, 結果: x.outcome, エントリー: x.entry, 損切り: x.stop, 利確: x.take_profit }; })));
      p.push("他の通貨ペア（" + ["ドル円", "ユーロドル", "ユーロ円", "ポンド円", "豪ドル円"].filter(function (x) { return x !== S.name; }).join("・") + "）については、ここでは数値は書かず、ファンダ（ドル全面主導かどうか等）から『円安寄り／円高寄り／中立』の方向だけを一言で。");
      p.push("出力は次のJSONオブジェクトのみ:\n" + JSON.stringify({
        verdict: "買／売／見送り", confidence: "高／中／低", headline: "1行の結論（40字以内）",
        win_rate: 54, win_rate_basis: "例: 1時間足『RSI行き過ぎの戻り・EMA200と同方向』勝率54%（118回）、06-07時台の時間帯勝率53%、RSIが条件と逆で-5",
        candidate: { side: "買／売（高い方）", win_rate: 54, entry: 158.123, stop: 158.0, take_profit: 158.3 },
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
        // 2026-10-13: 想定勝率が50%を超えたら、AIが見送りと書いても候補の向きで案にする（発注は本人判断）
        try {
          var cd = result && result.candidate, wr = Number(cd && cd.win_rate);
          // 2026-10-13: ボタンは『今入るとしたら』の参考。見送りと返っても候補の向きで必ず表示する
          if (cd && /見送/.test(String(result.verdict || "見送り")) && cd.entry != null && cd.stop != null) {
            result.verdict = /売/.test(String(cd.side)) ? "売" : "買";
            result.entry = cd.entry; result.stop = cd.stop; result.take_profit = cd.take_profit;
            result.win_rate = wr; result.confidence = "低";
            result.headline = (wr > 50 ? "想定勝率" + wr + "%" : "想定勝率" + wr + "%・様子見が無難") + " — " + (result.headline || "");
          }
          if (result && result.win_rate == null && cd) result.win_rate = cd.win_rate;
        } catch (e) {}
        var doc = { result: result, at: new Date().toISOString(), rate: rate, riskRate: riskRate, pair: runPair };
        lastBy[runPair] = doc;
        if (pairSel.value === runPair) renderScalp(doc);
        sStatus.textContent = "案を更新 " + fmtAt(doc.at) + "（" + Math.round((Date.now() - t0) / 1000) + "秒・リスク " + Math.round(riskRate * 100) + "%）";
        if (runRef) { try { await runRef.set(doc); } catch (e) { sStatus.textContent += " ※保存できませんでした（閲覧のみの権限）。"; } }
        if (SC.histCol) {
          try {
            var r0 = result || {};
            await SC.histCol.doc(String(doc.at).replace(/[^0-9A-Za-z]/g, "") + (runPair === "ユーロドル" ? "eu" : runPair === "ユーロ円" ? "ej" : runPair === "ポンド円" ? "gj" : "uj")).set(JSON.parse(JSON.stringify({
              at: doc.at, pair: runPair, verdict: r0.verdict || "見送り", confidence: r0.confidence || "",
              entry: r0.entry, stop: r0.stop, take_profit: r0.take_profit, rate: rate, riskRate: riskRate,
              headline: r0.headline || "", win_rate: r0.win_rate != null ? Number(r0.win_rate) : null })));
          } catch (e) {}
        }
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

# 2026-10-13 落合さん指示「ボードにコメント欄と送信ボタン。タスクに伝えたいことを送りやすく」
# 連絡箱: 画面のどこからでも開ける「💬 タスクに伝える」＋各カードの見出しの💬（その欄を対象にして開く）。
# 送った内容は artifact の db「board_inbox」に入り、連絡箱の処理タスク（毎時）と朝の配信タスクが読み、
# 返事（reply）と状況（status）を書き戻す。画面の下に履歴と返事が並ぶ。
INBOX_HTML = """
<section class="card inbox" id="inbox">
  <div class="card-head"><h3>タスクへの連絡<span class="badge">コメント欄</span></h3>
    <span class="params">ここに書いて送ると、連絡箱の処理タスク（平日7〜23時の毎時）と朝の配信が読んで対応し、返事をここに書きます</span></div>
  <div class="ib-form" id="ib-form">
    <div class="ib-row"><span class="ib-l">宛先</span><div class="ib-chips" data-k="to">
      <button type="button" data-v="おまかせ" aria-pressed="true">おまかせ</button><button type="button" data-v="朝の配信・指示書">朝の配信・指示書</button><button type="button" data-v="デイトレ">デイトレ</button><button type="button" data-v="スキャル・シグナル">スキャル・シグナル</button><button type="button" data-v="ボードの見た目・機能">ボードの見た目・機能</button><button type="button" data-v="研究・ファンダ">研究・ファンダ</button></div></div>
    <div class="ib-row"><span class="ib-l">種類</span><div class="ib-chips" data-k="kind">
      <button type="button" data-v="要望" aria-pressed="true">要望</button><button type="button" data-v="不具合">不具合</button><button type="button" data-v="質問">質問</button><button type="button" data-v="発注の報告">発注の報告</button><button type="button" data-v="メモ">メモ（記録だけ）</button></div></div>
    <div class="ib-row"><span class="ib-l">定型文</span><div class="ib-tpl" id="ib-tpl">
      <button type="button" data-kind="発注の報告" data-to="朝の配信・指示書" data-t="本日の指示書どおりに発注しました。">指示どおり発注した</button>
      <button type="button" data-kind="発注の報告" data-to="朝の配信・指示書" data-t="本日の指示書は見送りました。理由：">見送った（理由）</button>
      <button type="button" data-kind="発注の報告" data-to="スキャル・シグナル" data-t="スキャルのシグナルで入りました。結果：">シグナルで入った</button>
      <button type="button" data-kind="不具合" data-to="ボードの見た目・機能" data-t="表示がおかしいです。場所：　どうおかしい：">表示がおかしい</button>
      <button type="button" data-kind="要望" data-to="ボードの見た目・機能" data-t="こう変えてほしい：">こう変えてほしい</button>
      <button type="button" data-kind="質問" data-to="おまかせ" data-t="教えてください：">質問する</button></div></div>
    <textarea id="ib-text" rows="4" placeholder="伝えたいことを書いてください（⌘／Ctrl＋Enter で送信）。書きかけは自動で残ります"></textarea>
    <div class="ib-row ib-foot">
      <label class="ib-chk"><input type="checkbox" id="ib-ctx" checked> いまの画面の状況を添える（更新時刻・レート・見ている欄・選択中の倍率など）</label>
      <label class="ib-chk"><input type="checkbox" id="ib-urgent"> 急ぎ</label>
      <span class="ib-target" id="ib-target"></span>
      <button class="abtn gold" id="ib-send" type="button">送信</button>
    </div>
    <div class="astatus" id="ib-status"></div>
  </div>
  <h4 class="fh">これまでの連絡と返事</h4>
  <div id="ib-list" class="ib-list"><p class="note">読み込み中…</p></div>
</section>
<button class="ib-fab" id="ib-fab" type="button" aria-label="タスクに伝える">💬 タスクに伝える</button>
"""

INBOX_CSS = """
.inbox{margin-top:var(--s4)}
.ib-form{display:grid;gap:8px}
.ib-row{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center}
.ib-l{font-size:var(--fs-cap);color:var(--sub);font-weight:700;min-width:3.5em}
.ib-chips,.ib-tpl{display:flex;flex-wrap:wrap;gap:4px}
.ib-chips button,.ib-tpl button{border:1px solid var(--line-strong);background:var(--raised);color:var(--ink);border-radius:999px;padding:4px 12px;font-size:var(--fs-cap);font-weight:700;cursor:pointer}
.ib-chips button[aria-pressed="true"]{background:var(--ink);color:var(--panel);border-color:var(--ink)}
.ib-tpl button{border-style:dashed;font-weight:600}
#ib-text{width:100%;box-sizing:border-box;border:1px solid var(--line-strong);border-radius:var(--r2);padding:10px 12px;font:inherit;font-size:16px;background:var(--panel);color:var(--ink);resize:vertical;min-height:6em}
#ib-text:focus{outline:2px solid var(--navy);outline-offset:1px}
.ib-foot{justify-content:space-between}
.ib-chk{font-size:var(--fs-cap);color:var(--sub);display:flex;gap:4px;align-items:center}
.ib-target{font-size:var(--fs-cap);color:var(--ink);font-weight:700}
.ib-target button{margin-left:4px;border:0;background:none;color:var(--sub);cursor:pointer}
.ib-list{display:grid;gap:8px}
.ib-item{border:1px solid var(--line);border-radius:var(--r2);padding:8px 12px;background:var(--panel)}
.ib-item .ib-meta{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:var(--fs-cap);color:var(--sub)}
.ib-item .ib-body{white-space:pre-wrap;margin-top:4px}
.ib-item .ib-reply{margin-top:6px;padding:6px 10px;border-left:3px solid var(--navy);background:var(--raised);white-space:pre-wrap;font-size:var(--fs-note)}
.ib-st{display:inline-block;border-radius:999px;padding:0 8px;font-weight:700;border:1px solid var(--line-strong)}
.ib-st.s-未読{background:var(--act);color:var(--act-ink);border-color:transparent}
.ib-st.s-対応中{background:var(--info-bg);color:var(--info-ink);border-color:transparent}
.ib-st.s-確認待ち{background:var(--alert-bg);color:var(--alert-ink);border-color:var(--alert-line)}
.ib-item .ib-act{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
.ib-item .ib-act input{flex:1;min-width:10em;border:1px solid var(--line-strong);border-radius:var(--r2);padding:4px 8px;font:inherit;background:var(--panel);color:var(--ink)}
.ib-fab{position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom,0px));z-index:30;border:0;border-radius:999px;padding:12px 18px;font-weight:800;font-size:.95rem;background:var(--ink);color:var(--panel);box-shadow:0 6px 18px rgba(0,0,0,.22);cursor:pointer}
.ib-pin{margin-left:6px;border:1px solid var(--line);background:var(--raised);border-radius:999px;font-size:.72rem;padding:0 7px;cursor:pointer;vertical-align:middle;color:var(--sub)}
@media print{.ib-fab,.ib-pin{display:none}}
"""

INBOX_JS = r"""
<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };
  var fmt = function (iso) { try { var d = new Date(iso); return (d.getMonth() + 1) + "/" + d.getDate() + " " + d.getHours() + ":" + ("0" + d.getMinutes()).slice(-2); } catch (e) { return ""; } };
  var text = $("ib-text"), st = $("ib-status"), list = $("ib-list"), target = null;
  var pick = function (k) { var b = document.querySelector(".ib-chips[data-k='" + k + "'] button[aria-pressed='true']"); return b ? b.dataset.v : ""; };
  var setChip = function (k, v) { document.querySelectorAll(".ib-chips[data-k='" + k + "'] button").forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.v === v ? "true" : "false"); }); };
  document.querySelectorAll(".ib-chips button").forEach(function (b) {
    b.onclick = function () { setChip(b.parentNode.dataset.k, b.dataset.v); };
  });
  // 書きかけの自動保存（この端末だけ）
  try { var dr = localStorage.getItem("fx_inbox_draft"); if (dr) text.value = dr; } catch (e) {}
  text.addEventListener("input", function () { try { localStorage.setItem("fx_inbox_draft", text.value); } catch (e) {} });
  document.querySelectorAll("#ib-tpl button").forEach(function (b) {
    b.onclick = function () {
      setChip("kind", b.dataset.kind); setChip("to", b.dataset.to);
      text.value = (text.value ? text.value.replace(/\s+$/, "") + "\n" : "") + b.dataset.t;
      text.focus(); text.setSelectionRange(text.value.length, text.value.length);
      try { localStorage.setItem("fx_inbox_draft", text.value); } catch (e) {}
    };
  });
  function showTarget() {
    var t = $("ib-target");
    t.innerHTML = target ? "対象: " + esc(target) + " <button type='button' aria-label='対象を外す'>×</button>" : "";
    var x = t.querySelector("button"); if (x) x.onclick = function () { target = null; showTarget(); };
  }
  function openWith(sectionTitle) {
    target = sectionTitle || null; showTarget();
    $("inbox").scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(function () { text.focus(); }, 350);
  }
  $("ib-fab").onclick = function () { openWith(null); };
  // 各カードの見出しに 💬 を付け、その欄を対象にして開けるようにする
  document.querySelectorAll(".card .card-head h2, .card .card-head h3, .card > h3, .card .tile-h h3").forEach(function (h) {
    if (h.closest("#inbox")) return;
    var b = document.createElement("button");
    b.type = "button"; b.className = "ib-pin"; b.textContent = "💬"; b.title = "この欄についてタスクに伝える";
    b.onclick = function (ev) { ev.preventDefault(); openWith(h.textContent.replace("💬", "").trim().slice(0, 40)); };
    h.appendChild(b);
  });
  function context() {
    var c = {};
    try {
      var u = document.querySelector(".upd"); if (u) c.board_updated = u.textContent.trim();
      c.rates = Array.prototype.map.call(document.querySelectorAll(".inst"), function (e) { return e.innerText.replace(/\s+/g, " ").trim(); }).join(" ／ ");
      var y = window.scrollY + 80, seen = "";
      document.querySelectorAll("h2.grp, .card h3, .card h2").forEach(function (h) { if (h.getBoundingClientRect().top + window.scrollY <= y) seen = h.textContent.replace("💬", "").trim(); });
      c.viewing = seen;
      var rs = $("a-risk"); if (rs) c.risk_mult = rs.value;
      var sp = $("s-pair"); if (sp) c.scalp_pair = sp.value;
      var sr = $("s-risk"); if (sr) c.scalp_risk = sr.value;
      var sg = $("sc-sig"); if (sg) c.scalp_signal = sg.innerText.replace(/\s+/g, " ").trim().slice(0, 160);
      c.screen = window.innerWidth + "px";
    } catch (e) {}
    return c;
  }
  function render(items) {
    if (!items.length) { list.innerHTML = "<p class='note'>まだ連絡はありません。</p>"; return; }
    list.innerHTML = items.map(function (m) {
      var s = m.status || "未読";
      return "<div class='ib-item' data-id='" + esc(m._id) + "'><div class='ib-meta'><span class='ib-st s-" + esc(s) + "'>" + esc(s) + "</span>"
        + "<span>" + esc(fmt(m.at)) + "</span><span>宛先: " + esc(m.to) + "</span><span>" + esc(m.kind) + "</span>"
        + (m.urgent ? "<span><b>急ぎ</b></span>" : "") + (m.target ? "<span>対象: " + esc(m.target) + "</span>" : "") + "</div>"
        + "<div class='ib-body'>" + esc(m.text) + "</div>"
        + (m.reply ? "<div class='ib-reply'><b>" + esc(m.replied_by || "タスク") + "</b>（" + esc(fmt(m.replied_at)) + "）\n" + esc(m.reply) + "</div>" : "")
        + (m.thread && m.thread.length ? m.thread.map(function (t) { return "<div class='ib-reply'><b>" + esc(t.by) + "</b>（" + esc(fmt(t.at)) + "）\n" + esc(t.text) + "</div>"; }).join("") : "")
        + (s !== "完了" ? "<div class='ib-act'><input type='text' placeholder='返事・追記（承認なら「承認」）'><button type='button' class='abtn ghost' data-a='add'>追記して送る</button><button type='button' class='abtn ghost' data-a='done'>完了にする</button></div>" : "")
        + "</div>";
    }).join("");
  }
  if (!window.claude || typeof window.claude.use !== "function") {
    list.innerHTML = "<p class='note'>この画面では送信できません（claude.ai の閲覧画面で使えます）。</p>";
    $("ib-send").disabled = true; return;
  }
  claude.use("db").then(function (db) {
    if (!db) { list.innerHTML = "<p class='note'>保存先に接続できないため送信できません。</p>"; $("ib-send").disabled = true; return; }
    var col = db.collection("board_inbox"), items = [];
    col.orderBy("at", "desc").limit(40).onSnapshot(function (snap) {
      items = snap.docs.map(function (d) { var o = d.data(); o._id = d.id; return o; });
      render(items);
    }, function (e) { list.innerHTML = "<p class='note'>読み込めませんでした（" + esc(e.code) + "）。</p>"; });
    async function send() {
      var t = text.value.trim();
      if (!t) { st.textContent = "内容を書いてください。"; text.focus(); return; }
      $("ib-send").disabled = true; st.textContent = "送信中…";
      var now = new Date().toISOString(), id = now.replace(/[^0-9]/g, "").slice(0, 17);
      var doc = { at: now, to: pick("to"), kind: pick("kind"), text: t, urgent: $("ib-urgent").checked,
        target: target || "", status: pick("kind") === "メモ" ? "完了" : "未読",
        context: $("ib-ctx").checked ? context() : {}, thread: [] };
      try {
        await col.doc(id).set(JSON.parse(JSON.stringify(doc)));
        text.value = ""; try { localStorage.removeItem("fx_inbox_draft"); } catch (e) {}
        target = null; showTarget(); $("ib-urgent").checked = false;
        st.textContent = "送信しました（" + fmt(now) + "）。" + (doc.status === "未読" ? "処理タスクが次の回（毎時）に読んで返事をここに書きます。" : "記録だけ残しました。");
      } catch (e) {
        st.textContent = "送信できませんでした（" + (e && e.code || "不明") + "）。書いた内容は残してあります。";
      } finally { $("ib-send").disabled = false; }
    }
    $("ib-send").onclick = send;
    text.addEventListener("keydown", function (ev) { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); send(); } });
    list.addEventListener("click", async function (ev) {
      var b = ev.target.closest("button[data-a]"); if (!b) return;
      var box = b.closest(".ib-item"), id = box.dataset.id, m = items.filter(function (x) { return x._id === id; })[0];
      if (!m) return;
      try {
        if (b.dataset.a === "done") { await col.doc(id).update({ status: "完了" }); return; }
        var inp = box.querySelector("input"), v = inp.value.trim(); if (!v) { inp.focus(); return; }
        var th = (m.thread || []).concat([{ by: "落合さん", at: new Date().toISOString(), text: v }]);
        await col.doc(id).update({ thread: th, status: "未読" });
      } catch (e) { st.textContent = "更新できませんでした（" + (e && e.code || "不明") + "）。"; }
    });
  });
})();
</script>
"""

ACT_STYLE = {  # アクション → (表示, クラス)
    "新規買(ロング)": ("新規 買", "buy"), "新規売(ショート)": ("新規 売", "sell"),
    # 2026-10-13: 売り玉では逆指値を「下げる」のに「切上げ」と出ていたため中立の表示にした。
    # 向き（引き下げ／引き上げ）は各カードで建玉の向きから足す（表示だけ・クラス gold は不変）。
    "ストップ変更": ("ストップ変更", "gold"), "決済": ("全決済", "gold"),
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
        txt, cls = "ファンダ 中立（材料拮抗）", "chip"
    elif (pos > 0) == (bal["net"] > 0):
        txt, cls = "ファンダ 追い風", "chip fav"
    else:
        txt, cls = "ファンダ 向かい風", "chip adv"
    return f'<span class="{cls}">{txt}<small>・参考</small></span>'


def _act_disp(act, pos):
    """「ストップ変更」に向きを足す（表示だけ）。売り玉＝引き下げ／買い玉＝引き上げ。
    トレール逆指値は有利側にしか動かないため、建玉の向きで決まる。"""
    if act == "ストップ変更" and pos:
        return "ストップ変更（引き下げ）" if pos < 0 else "ストップ変更（引き上げ）"
    return act


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
        "見送り": ("イベント見送り", "flat"), "変更": ("ストップ変更", "gold"),
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


def _width_html(pos, entry, mark, stop, tp, basis="現値"):
    """保有玉の「利確幅・損切り幅 pips」をボードにも出す（2026-08-18ユーザー指示）。

    幅は発注カード（order_card.enrich）と同じ建値基準で、計算は
    risk.order_widths() だけに任せる。ボード側で *100 を書くと、同じ数字を
    2箇所で計算して片方だけ直し残る事故になる（memory
    feedback_fix_all_output_targets）。現値からの距離も併記する
    （転記直後に効くのは建値基準の幅ではなくこちらの距離のため）。
    2026-10-13: 文中に埋もれていたため、ラベルと値の小さな箱（_kv）の並びで返す。
    basis は mark が何の値か（日足終値／2時間足終値／1時間足終値）の表示名。系統ごとに
    基準の足が違うのに「現値」とだけ出ていて、ヘッダのレートと同じ値に読めたため（表示のみ）。
    """
    if not pos or entry in (None, ""):
        return ""
    w = risk.order_widths(pos, entry, stop=stop, tp=tp)
    now = risk.order_widths(pos, mark, stop=stop, tp=tp)
    bits = []
    if w["利確幅pips"] is not None:
        bits.append(_kv("利確幅（建値から）", f'{w["利確幅pips"]:.0f}', "pips"))
    if w["損切り幅pips"] is not None:
        over = (float(stop) - float(entry)) * pos > 0   # 建値より有利側
        bits.append(_kv("損切り幅（建値から）", f'{w["損切り幅pips"]:.0f}', "pips",
                        small="建値超え＝到達しても利益" if over else ""))
    if not bits:
        return ""
    if now["損切り幅pips"] is not None:
        bits.append(_kv(f"逆指値まで（{basis}から）", f'{now["損切り幅pips"]:.0f}', "pips",
                        small="★超過" if now["損切り逆行"] else "",
                        alert=bool(now["損切り逆行"])))
    if now["利確幅pips"] is not None:
        bits.append(_kv(f"利確まで（{basis}から）", f'{now["利確幅pips"]:.0f}', "pips"))
    return "".join(bits)


def _kv(label, value, unit="", small="", cls="", alert=False):
    """ラベルと値の小さな箱（システムのタイル用）。label/small は呼び出し側で安全な文字列。"""
    unit_html = f"<i>{unit}</i>" if unit else ""
    small_html = f"<small>{small}</small>" if small else ""
    box = "kv alert" if alert else "kv"
    return (f'<span class="{box}"><span class="k">{label}</span>'
            f'<b class="{cls}">{value}{unit_html}</b>{small_html}</span>')


def _bar_html(status):
    """デイトレ系カードに「判定に使った確定バー」を出す。

    2026-08-17 追加: カードには実行時刻（最終判定）しか出ておらず、週明けの
    ように新しい足がまだ立っていない時間帯でも「今の値で判定した」と読めて
    しまった。判定の土台になったバーを必ず並記する。
    """
    bar = str(status.get("確定バー") or "")
    if not bar:
        return ""
    return f'<span class="stop">判定に使った足 {html.escape(bar)}</span>'


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


def _spark_svg(values, w=640, h=180, base=None):
    """資産カーブのSVG(エリア+終点強調)。valuesは数値list。

    2026-10-13: base（開始資金）を渡すと破線の基準線を描き、増えたのか減ったのかを
    図だけで読めるようにする（表示の縮尺を変えるだけでデータは不変）。罫線のクラスは
    カード配置の .grid と衝突しないよう gl に改名。終点は長さ0の線に丸い端を付けた点
    （preserveAspectRatio="none" で伸ばしても楕円にならない）。
    """
    if len(values) < 2:
        return ""
    n = max(1, len(values) // 200)
    vs = values[::n]
    if vs[-1] != values[-1]:
        vs.append(values[-1])
    lo, hi = min(vs), max(vs)
    if base is not None:
        lo, hi = min(lo, base), max(hi, base)
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
        f'y2="{pad + (h-2*pad)*i/3:.1f}" class="gl"/>' for i in range(4))
    base_ln = ""
    if base is not None:
        yb = pad + (h - 2 * pad) * (1 - (base - lo) / rng)
        base_ln = (f'<line x1="{pad}" y1="{yb:.1f}" x2="{w-pad}" y2="{yb:.1f}" '
                   f'class="base"/>')
    ex, ey = pts[-1].split(",")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
            f'role="img" aria-label="資産推移（破線＝開始資金）">'
            f'{grid}{base_ln}<polygon points="{area}" class="area"/>'
            f'<polyline points="{line}" class="line"/>'
            f'<line x1="{ex}" y1="{ey}" x2="{ex}" y2="{ey}" class="dot"/></svg>')


def _clamp(cid, size, inner, label="全文を表示"):
    """二次的な長文を「高さ制限＋続きを表示」で畳む（2026-10-13）。

    <details> と違い overflow で隠すだけなので innerText（AI分析の材料＝boardText）には
    残る。開閉は CSS だけ（チェックボックス＋label）で、JS は使わない。
    """
    return (f'<div class="clamp {size}"><input type="checkbox" id="{cid}" class="clamp-t" '
            f'aria-label="{label}"><div class="clamp-b">{inner}</div>'
            f'<label for="{cid}" class="clamp-btn"></label></div>')


def _op_todo(op):
    """order_json["操作"] の1件を「今日の発注」欄の表示用 (what_html, px_html) に変える。

    値はファイルのまま使う（計算しない）。下げる／上げるは変更前後の大小を比べるだけ。
    """
    def num(v):
        try:
            return f"{float(v):.3f}"
        except (TypeError, ValueError):
            return None

    kind = str(op.get("種別") or "")
    try:
        units = f'{int(float(op.get("数量") or 0)):,}'
    except (TypeError, ValueError):
        units = str(op.get("数量") or "")
    px = ""
    if kind == "決済逆指値の変更":
        what = f'{kind}・{op.get("対象", "")} {units}通貨・{op.get("手順", "")}'
        old, new = num(op.get("変更前")), num(op.get("変更後"))
        if new is not None:
            if old is not None:
                d = ("下げる" if float(new) < float(old) else
                     "上げる" if float(new) > float(old) else "")
                px = f'<span class="old">{old}</span><span class="arr">→</span>'
            else:
                d = ""
            px += f'<span class="v">{new}</span>'
            if d:
                px += f'<span class="dir">{d}</span>'
    elif kind == "新規建て":
        what = (f'新規 {op.get("売買", "")} {units}通貨（リスク倍率1倍の数量）・'
                f'{op.get("注文方法", "")}')
        for k, key in (("入る値", "レート"), ("逆指値", "決済逆指値"),
                       ("利確", "決済指値(利確)")):
            v = num(op.get(key))
            if v:
                px += f'<span class="k">{k}</span><span class="v">{v}</span>'
    elif kind == "決済":
        what = f'全決済 {op.get("売買", "")} {units}通貨・{op.get("手順", "")}'
    else:
        what = str(op.get("内容") or kind)
    return html.escape(what), px


def _intraday_todo(name, status, st, pos, act_disp, cls):
    """デイトレ系の「今日の発注」欄の1行（表示専用）。

    状態は件数の数え方（未通知＝要作業）と同じ条件で決め、数量・水準は状態ファイルの値を
    そのまま並べる。"""
    upd = str(status.get("更新", ""))[:16].replace("T", " ")
    upd_s = upd[5:].replace("-", "/")   # 「10/12 20:34」（直近の記録と同じ短い形・表示のみ）
    stop, tp = st.get("stop"), st.get("tp")
    label = ""
    if status.get("未通知"):
        state = "act"
        what = f'{act_disp}・通知の発注カードどおりに（下の原文の末尾）'
    elif cls in ("buy", "sell", "gold"):
        state = "done"
        what = f'{act_disp}（通知済み・最終判定 {upd_s}）— MATRIX TRADERで実施済みか確認'
    else:
        state = "none"
        if pos:
            label = "確認のみ"   # 札と文（置かれているか確認）を食い違わせない。クラスは none のまま
            s_txt = f"{float(stop):.3f}" if stop else "—"
            t_txt = f"{float(tp):.3f}" if tp else "—"
            side = "買" if pos > 0 else "売"
            what = (f'{act_disp}・{side} {int(st.get("units", 0) or 0):,}通貨・'
                    f'逆指値 {s_txt}／利確 {t_txt} が置かれているか確認（最終判定 {upd_s}）')
        else:
            what = f'{act_disp}（ポジションなし・最終判定 {upd_s}）'
    px = ""
    if state in ("act", "done"):
        if stop:
            px += f'<span class="k">逆指値</span><span class="v">{float(stop):.3f}</span>'
        if tp:
            px += f'<span class="k">利確</span><span class="v">{float(tp):.3f}</span>'
    return (state, "uj", name, html.escape(what), px, label)


_TODO_ORDER = {"act": 0, "done": 1, "none": 2, "wait": 3}
_TODO_LABEL = {"act": "要作業", "done": "通知済み", "none": "作業なし", "wait": "指示書待ち"}


def _today_html(rows, order_json, is_flash, action_count, near_exit, stale_day="",
                basis_close=None):
    """最上段の「今日の発注」欄（2026-10-13 新設）。

    rows: [(状態 act/done/none/wait, "uj"|"eu", システム名, what_html, px_html[, 札の文字])]
    件数（action_count）はバナーと同じ数え方の値を受け取るだけで、ここでは数え直さない。
    stale_day: 指示書が今日より前の日のものなら、その日付（例 "10/12"）。見出しを
    「直近の発注」に替える（表示のみ。件数・要作業の判定は変えない）。
    near_exit: [(システム名, 決済ライン, 日足終値からのpips)]。同じ水準は1回にまとめる。
    """
    lis = ""
    for r in sorted(rows, key=lambda r: _TODO_ORDER.get(r[0], 9)):
        st, pair, name, what, px = r[:5]
        label = (r[5] if len(r) > 5 else "") or _TODO_LABEL.get(st, st)
        eu = pair == "eu"
        badge = ('<span class="badge pair eu">EUR/USD</span>' if eu
                 else '<span class="badge pair ej">EUR/JPY</span>' if pair == "ej"
                 else '<span class="badge pair gj">GBP/JPY</span>' if pair == "gj"
                 else '<span class="badge pair">USD/JPY</span>')
        px_html = f'<span class="todo-px">{px}</span>' if px else ""
        lis += (f'<li class="todo-row {st}">{badge}'
                f'<span class="todo-sys">{html.escape(name)}</span>'
                f'<span class="state {st}">{html.escape(label)}</span>'
                f'<span class="todo-what">{what}</span>{px_html}</li>')
    nsys = len({r[2] for r in rows})
    stage = html.escape(str((order_json or {}).get("段階", "")))
    if order_json:
        d = str(order_json.get("日付", ""))[5:].replace("-", "/")
        params = (f'{html.escape(d)} {stage}の指示書'
                  f'・作業 {action_count}件／{nsys}システム')
    else:
        params = "発注データなし（原文を参照）"
    if stale_day:
        title = f'直近の発注（{html.escape(stale_day)} {stage}の指示書）'
        params += '・日足の欄は実施済みなら対応不要（次の指示書は朝6時台から）'
    else:
        title = "今日の発注"
    flash = ('<p class="callout info">速報（参考）— 正式な発注判断は8時すぎの'
             '【最終確定】で</p>' if is_flash else "")
    ne_html = ""
    if near_exit:
        groups = []   # 同じ水準・同じ距離のシステムは1回にまとめる（表示のみ）
        for n, lv, p in near_exit:
            key = (f"{lv:.3f}", None if p is None else f"{p:.0f}")
            for g in groups:
                if g[0] == key:
                    g[1].append(n)
                    break
            else:
                groups.append((key, [n]))
        parts = []
        for (lv_s, p_s), names in groups:
            s = f'{html.escape("・".join(names))} {lv_s}円'
            if p_s is not None:
                base = f'日足終値{basis_close:.3f}円' if basis_close else "日足終値"
                s += f'（{base}からあと約{p_s}pips）'
            parts.append(s)
        ne_html = (f'<p class="callout alert">⚠ 決済ライン接近：{"／".join(parts)}'
                   ' — 次の引けの終値で決済判定が入りうる（注文は置かない・翌朝の指示書を待つ）</p>')
    cls = "card span2 today has-act" if action_count else "card span2 today"
    # 決済ライン接近の注意は行の一覧より前（スマホで2画面下に埋もれていた）
    return (f'<section class="{cls}" id="today">'
            f'<div class="card-head"><h3>{title}</h3><span class="params">{params}</span></div>'
            f'{flash}{ne_html}'
            '<p class="callout warn mult-flag">リスク倍率が1倍以外に設定されています。'
            '新規建ての数量は下の「注文指示書（原文）」の値（倍率を反映済み）を使ってください。</p>'
            f'<ul class="todo">{lis}</ul></section>')


_SC_METHODS = {"RSI行き過ぎの戻り": "rsi_rev", "ボリンジャー外→内の戻り": "bb_rev",
               "直近4本レンジのブレイク": "range_brk", "EMA20押し目/戻り": "ema_pull"}


def _parse_scalp_label(lab):
    """研究の条件ラベル → (入り方, 順行のみ, 損切りATR, 利確ATR, 保有本数)。読めなければ None。"""
    m = re.match(r"(.+?)(・EMA200と同方向のみ)?（損切りATR×([\d.]+)／利確ATR×([\d.]+)／最長(\d+)本）", lab or "")
    if not m or m.group(1) not in _SC_METHODS:
        return None
    return _SC_METHODS[m.group(1)], bool(m.group(2)), float(m.group(3)), float(m.group(4)), int(m.group(5))


def _scalp_chart(pair="usdjpy", days=10):
    """2026-10-13 落合さん指示「スキャルの実績が見えない。チャートでビジュアルに」。
    ①15分足・1時間足のローソク（直近）＋EMA20/50 ②研究の最良ルールを機械的に当てはめた
    ペーパートレード（直近の売買点・勝ち負け・累計pips）をボードに渡す。発注とは無関係の検証表示。"""
    try:
        import numpy as np                                   # noqa: PLC0415
        import research_scalp as rs                          # noqa: PLC0415
        cfgp = rs.PAIRS[pair]
        rs.MULT = cfgp["MULT"]
        rs.COST_PIPS = cfgp.get("cost", 0.8)     # 2026-10-15 ポンド円はコストが大きい
        dec = 5 if pair == "eurusd" else 3
        m15, h1 = rs._load_pair(pair)
        res = json.load(open(R("results", cfgp["out"]), encoding="utf-8"))
        end = m15.index[-1]
        out = {"tf": {}, "paper": [], "最終": f"{end:%m/%d %H:%M}"}
        # 2026-10-13: 正式なシグナル配信（scalp_engine.py）の履歴
        try:
            book = json.load(open(R("results", "scalp_signals.json"), encoding="utf-8"))
            code = {"eurusd": "EUR/USD", "eurjpy": "EUR/JPY", "gbpjpy": "GBP/JPY"}.get(pair, "USD/JPY")
            out["signals"] = [{k: r.get(k) for k in ("sent_at", "tf", "side", "d", "entry", "stop", "tp", "win", "rule",
                                                       "hold_min", "result", "pips", "fill", "exit", "why", "out_at",
                                                       "sl_pips", "tp_pips", "band_win")}
                              for r in book.get("signals", []) if r.get("code") == code][-60:]
            out["sig_updated"] = book.get("updated")
        except (OSError, ValueError):
            out["signals"], out["sig_updated"] = [], None
        # 2026-10-13: 30分・4時間・8時間・12時間・24時間のスイッチを追加（落合さん指示）
        hsrc = rs._base_for("1時間", m15, h1)
        frames = {"15分": (m15, "15min", 320), "30分": (m15, "30min", 240), "1時間": (hsrc, "1h", 220),
                  "4時間": (hsrc, "4h", 200), "8時間": (hsrc, "8h", 180), "12時間": (hsrc, "12h", 160),
                  "24時間": (hsrc, "1D", 160)}
        for name, (src, rule, keep) in frames.items():
            df = rs.add_indicators(rs.resample(src, rule)).tail(keep)
            out["tf"][name] = [[int(t.timestamp() // 60), round(r.Open, dec), round(r.High, dec),
                                round(r.Low, dec), round(r.Close, dec),
                                round(r.ema20, dec), round(r.ema50, dec)] for t, r in df.iterrows()]
        for tf_name in ("1時間", "15分"):
            top = ((res.get("時間足別") or {}).get(tf_name) or {}).get("上位条件") or []
            prm = _parse_scalp_label(top[0]["条件"]) if top else None
            if not prm:
                continue
            meth, trend_only, sl_k, tp_k, hold = prm
            df = rs.add_indicators(rs.resample(rs._base_for(tf_name, m15, h1), "15min" if tf_name == "15分" else "1h"))
            sig = rs.signals(df, meth).values
            o, hi, lo, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
            atr, tr, idx = df["atr"].values, df["trend"].values, df.index
            trades, last_exit, n = [], -1, len(df)
            for i in np.where(sig != 0)[0]:
                if i + 1 >= n or i <= last_exit or not np.isfinite(atr[i]) or atr[i] <= 0:
                    continue
                d = int(sig[i])
                if trend_only and tr[i] != d:
                    continue
                if idx[i + 1] < end - pd.Timedelta(days=30):
                    continue
                e = o[i + 1]
                stop, take = e - d * sl_k * atr[i] / rs.MULT, e + d * tp_k * atr[i] / rs.MULT
                pnl, jx, px_out, why = None, None, None, "時間切れ"
                for j in range(i + 1, min(i + 1 + hold, n)):
                    if (lo[j] <= stop) if d > 0 else (hi[j] >= stop):
                        pnl, jx, px_out, why = -sl_k * atr[i], j, stop, "損切り"
                        break
                    if (hi[j] >= take) if d > 0 else (lo[j] <= take):
                        pnl, jx, px_out, why = tp_k * atr[i], j, take, "利確"
                        break
                if pnl is None:
                    if i + hold >= n:     # まだ決着していない（保有中）
                        trades.append({"in": int(idx[i + 1].timestamp() // 60), "d": d, "e": round(e, dec),
                                       "sl": round(stop, dec), "tp": round(take, dec), "open": True})
                        break
                    jx = i + hold
                    px_out = c[jx]
                    pnl = (c[jx] - e) * d * rs.MULT
                trades.append({"in": int(idx[i + 1].timestamp() // 60), "out": int(idx[jx].timestamp() // 60),
                               "d": d, "e": round(e, dec), "x": round(float(px_out), dec),
                               "sl": round(stop, dec), "tp": round(take, dec),
                               "pips": round(float(pnl) - rs.COST_PIPS, 1), "why": why})
                last_exit = jx
            out["paper"].append({"tf": tf_name, "rule": top[0]["条件"], "PF": top[0]["PF"],
                                 "勝率": top[0]["勝率"], "回数": top[0]["取引数"], "trades": trades})
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


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
    other = {"ドル円": "ユーロドル"}.get(pair_name, "ドル円")
    who = "円" if pair_name in ("ユーロ円", "ポンド円") else "ドル"
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
        # 長い文の2列（入り方・時間帯）だけ折り返す（td.w）。2026-10-13: nowrap のままだと
        # 表の幅がスマホで約2,700pxになり、横に何画面も動かさないと読めなかった。
        rows += (f'<tr><td><b>{html.escape(tf)}</b></td><td class="w">{html.escape(str(x["条件"]))}</td>'
                 f'<td>{x["勝率"]:.0%}</td><td>{x["PF"]}（{x["PF前"]}/{x["PF後"]}）</td>'
                 f'<td>{x["取引数"]}</td><td>{x["損切りpips目安"] or "—"}／{x["利確pips目安"] or "—"}</td>'
                 f'<td class="w">狙い目: {html.escape(good)}<br>避ける: {html.escape(bad)}</td></tr>')
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
        + (f'<p class="note"><b>{other}の向きで確認した場合</b>（{who}が両通貨で同じ向きに動いている時だけ入る・'
           f'各足の最良条件）</p><div class="tablewrap"><table><tr><th>時間足</th><th>勝率</th><th>PF</th>'
           f'<th>回数</th><th>評価</th></tr>{cross}</table></div>' if cross else ""))


def _eurusd_section(cfg):
    """ユーロドル（2通貨目・2026-10-12〜）の表示一式。
    戻り値: (ヘッダ用レート文字列, 運用カードHTML, 研究カードHTML, 指示書テキスト, 指示書ファイル名,
             今日の発注欄用の項目リスト)
    2026-10-13: 運用カードは「見出し(div.subgrp)＋タイル2枚」を .grid の直下へ並べる形にした
    （カードの中の .grid＝入れ子をやめ、AI分析の材料に2回入る問題を解消）。"""
    ecfg = cfg.get("ユーロドル") or {}
    if not ecfg.get("有効"):
        return "", "", "", "", "", []
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
                    f'データを読めません（{html.escape(str(e)[:120])}）</p></section>'), "", "", "", []
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
    eu_items = []   # 今日の発注欄の行の材料（表示専用・件数には足さない）
    for name, scfg in (ecfg.get("システム") or {}).items():
        if not scfg.get("有効", True):
            continue
        st = state.get(name, {})
        pos = int(st.get("position", 0) or 0)
        act, cls = acts.get(name, ("指示書待ち" if not text else "待機", "flat"))
        act_disp = _act_disp(act, pos)
        units = float(st.get("units") or 0)
        pos_html = ('<span class="pos flat">ポジションなし</span>' if not pos else
                    f'<span class="pos {"buy" if pos > 0 else "sell"}">{"買" if pos > 0 else "売"} {units:,.0f}通貨</span>')
        kvs = _kv("ストップ", "設定なし") if pos else ""
        stop_v = None
        if pos and st.get("stop"):
            stop, entry = float(st["stop"]), float(st.get("entry") or close)
            stop_v = stop
            w = risk.order_widths(pos, close, stop=stop, units=units, entry=entry, pip=eng.PIP, jpy=jpy)
            ppl = units * (close - entry) * pos * jpy
            sy, sd = swap.accrued(pos, units, st.get("entry_date"), sw=sw)
            tot_pnl += ppl + sy
            tot_risk += (w["損切り到達額円"] or 0) + sy
            _hit = w["損切り到達額円"] + sy
            kvs = (_kv("ストップ", f'{stop:.5f}')
                   + _kv("逆指値まで（日足終値から）", f'{w["損切り幅pips"]:.0f}', "pips")
                   + _kv("到達なら", f'{_hit:+,.0f}', "円", cls="up" if _hit >= 0 else "down")
                   + _kv("含み", f'{ppl + sy:+,.0f}', "円", small=f'スワップ {sy:+,.0f}円・{sd}日',
                         cls="up" if ppl + sy >= 0 else "down"))
        exit_txt = html.escape(eng.exit_line_text(scfg, px, pos).strip()) if pos else ""
        v = (scfg.get("検証") or {})
        _need = " need" if cls in ("buy", "sell", "gold") else ""
        _side = " side-buy" if pos > 0 else " side-sell" if pos < 0 else ""
        kvs_html = f'<div class="kvs">{kvs}</div>' if kvs else ""
        exit_html = (f'<div class="exit"><span class="k">決済ライン</span>{exit_txt}</div>'
                     if exit_txt else "")
        cards.append(
            f'<article class="card tile eu{_need}{_side}"><div class="tile-h">'
            f'<h3>ユーロドル {html.escape(name)}</h3>'
            '<span class="badges"><span class="badge pair eu">EUR/USD</span><span class="badge">日足</span></span>'
            f'<span class="params">{html.escape(eng.describe(scfg))}・リスク{float(scfg["リスク率"]):.1%}</span></div>'
            f'<div class="tile-s"><div class="action {cls}">{html.escape(act_disp)}</div>{pos_html}</div>'
            f'{kvs_html}{exit_html}</article>')
        eu_items.append({"name": f"ユーロドル {name}", "act": act_disp, "cls": cls,
                         "pos": pos, "units": units, "stop": stop_v})
    sr = {}
    if os.path.exists(R("results", "research_eurusd_latest.json")):
        try:
            sr = json.load(open(R("results", "research_eurusd_latest.json"), encoding="utf-8"))
        except (OSError, ValueError):
            sr = {}
    risk_line = (f'<p class="note">ユーロドル建玉の合計: 含み {tot_pnl:+,.0f}円／全ストップ到達で {tot_risk:+,.0f}円'
                 f'（ドル円の「リスクと損益」とは別計上）</p>') if state else ""
    # 2枚のタイルに同じ文で入っていた円換算・手動発注の注記は、見出しに1回だけ置く
    op_html = ('<div class="subgrp span2"><h3>ユーロドル <span class="badge pair eu">EUR/USD</span>'
               '<span class="badge new">2通貨目・2026-10-13 稼働</span></h3>'
               f'<span class="params">終値 {close:.5f}ドル・ATR {a_pips:.0f}pips・ドル円 {jpy:.2f}円</span>'
               f'{risk_line}'
               f'<p class="note">※損益はドル建てをドル円{jpy:.2f}円で円換算。発注は MATRIX TRADER で'
               '<b>通貨ペアを EUR/USD に切り替えて</b>手動で。</p></div>'
               + "".join(cards))
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
        _res_upd = str(sr.get("更新", ""))[:16].replace("T", " ")
        res_body = (
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
            f'スキャルでの相乗効果はスキャルピング欄の「相手通貨で確認した場合」を参照。</p>')
        res_html = (
            f'<section class="card span2"><div class="card-head"><h3>ユーロドル研究（日足15年・ドル円との関係）</h3>'
            f'<span class="params">{html.escape(_res_upd)}・{html.escape(str(sr.get("期間", "")))}</span></div>'
            + _clamp("cl-eures", "c-m", res_body, "ユーロドル研究の全文を表示") + '</section>')
    # ヘッダ用。.rate の中は数字だけ（JS は最初の .rate＝ドル円を読む。ラベルは外側の span）
    head = (f'<div class="inst sub" data-pair="eurusd" data-dec="5" data-close="{close:.5f}"><span class="inst-l">ユーロドル EUR/USD</span>'
            f'<span class="rate">{close:.5f}</span><span class="inst-n">日足終値</span></div>')
    return head, op_html, res_html, text, fname, eu_items


def _yenpair_ops(cfg, pair):
    """クロス円（ユーロ円・ポンド円＝2026-10-15 落合さん承認で運用開始）の運用タイル一式。
    ユーロドルの _eurusd_section の運用部分と同じ作り。戻り値: (運用HTML, 指示書テキスト, ファイル名, 今日の発注の項目)"""
    NM = {"eurjpy": "ユーロ円", "gbpjpy": "ポンド円"}[pair]
    CODE = {"eurjpy": "EUR/JPY", "gbpjpy": "GBP/JPY"}[pair]
    BCLS = {"eurjpy": "ej", "gbpjpy": "gj"}[pair]
    ecfg = cfg.get(NM) or {}
    if not (ecfg.get("有効") and ecfg.get("システム")):
        return "", "", "", []
    try:
        import data_fetch_yenpair as dyp                    # noqa: PLC0415
        import yenpair_engine as eng                         # noqa: PLC0415
        eng.set_pair(pair)
        px = dyp.Pair(pair).load_daily()
        close = float(px["Close"].iloc[-1])
        sw = eng.swap_cfg(cfg)
    except Exception as e:                                   # noqa: BLE001
        return (f'<section class="card span2"><h3>{NM}</h3><p class="note">'
                f'データを読めません（{html.escape(str(e)[:120])}）</p></section>'), "", "", []
    state = {}
    sp = R("results", f"state_{pair}.json")
    if os.path.exists(sp):
        state = json.load(open(sp, encoding="utf-8"))
    odir = R("results", "orders")
    text, fname = "", ""
    if os.path.isdir(odir):
        fs = sorted(f for f in os.listdir(odir) if f"_{NM}指示_" in f)
        if fs:
            day = max(f[:10] for f in fs)
            rank = {"速報1": 1, "速報2": 2, "確定": 3}
            todays = sorted((f for f in fs if f.startswith(day)),
                            key=lambda f: rank.get(f.rsplit("_", 1)[-1].replace(".txt", ""), 0))
            fname = todays[-1]
            text = open(os.path.join(odir, fname), encoding="utf-8").read().strip()
    acts = {k.replace(f"{NM} ", ""): v for k, v in _parse_order_actions(
        text, [f"{NM} {n}" for n in (ecfg.get("システム") or {})]).items()} if text else {}
    a_pips = float(atr(px).iloc[-1]) / eng.PIP
    cards, items, tot_risk, tot_pnl = [], [], 0.0, 0.0
    for name, scfg in (ecfg.get("システム") or {}).items():
        if not scfg.get("有効", True):
            continue
        st = state.get(name, {})
        pos = int(st.get("position", 0) or 0)
        act, cls = acts.get(name, ("指示書待ち" if not text else "待機", "flat"))
        act_disp = _act_disp(act, pos)
        units = float(st.get("units") or 0)
        pos_html = ('<span class="pos flat">ポジションなし</span>' if not pos else
                    f'<span class="pos {"buy" if pos > 0 else "sell"}">{"買" if pos > 0 else "売"} {units:,.0f}通貨</span>')
        kvs, stop_v = "", None
        if pos and st.get("stop"):
            stop, entry = float(st["stop"]), float(st.get("entry") or close)
            stop_v = stop
            w = risk.order_widths(pos, close, stop=stop, units=units, entry=entry, pip=eng.PIP, jpy=1.0)
            ppl = units * (close - entry) * pos
            sy, sd = swap.accrued(pos, units, st.get("entry_date"), sw=sw)
            tot_pnl += ppl + sy
            tot_risk += (w["損切り到達額円"] or 0) + sy
            _hit = w["損切り到達額円"] + sy
            kvs = (_kv("ストップ", f'{stop:.3f}')
                   + _kv("逆指値まで（日足終値から）", f'{w["損切り幅pips"]:.0f}', "pips")
                   + _kv("到達なら", f'{_hit:+,.0f}', "円", cls="up" if _hit >= 0 else "down")
                   + _kv("含み", f'{ppl + sy:+,.0f}', "円", small=f'スワップ {sy:+,.0f}円・{sd}日',
                         cls="up" if ppl + sy >= 0 else "down"))
        exit_txt = html.escape(eng.exit_line_text(scfg, px, pos).strip()) if pos else ""
        _need = " need" if cls in ("buy", "sell", "gold") else ""
        _side = " side-buy" if pos > 0 else " side-sell" if pos < 0 else ""
        kvs_html = f'<div class="kvs">{kvs}</div>' if kvs else ""
        exit_html = (f'<div class="exit"><span class="k">決済ライン</span>{exit_txt}</div>' if exit_txt else "")
        cards.append(
            f'<article class="card tile {BCLS}{_need}{_side}"><div class="tile-h">'
            f'<h3>{NM} {html.escape(name)}</h3>'
            f'<span class="badges"><span class="badge pair {BCLS}">{CODE}</span><span class="badge">日足</span></span>'
            f'<span class="params">{html.escape(eng.describe(scfg))}・リスク{float(scfg["リスク率"]):.1%}</span></div>'
            f'<div class="tile-s"><div class="action {cls}">{html.escape(act_disp)}</div>{pos_html}</div>'
            f'{kvs_html}{exit_html}</article>')
        items.append({"name": f"{NM} {name}", "act": act_disp, "cls": cls, "pos": pos,
                      "units": units, "stop": stop_v, "pair": BCLS, "nm": NM, "dec": 3})
    risk_line = (f'<p class="note">{NM}建玉の合計: 含み {tot_pnl:+,.0f}円／全ストップ到達で {tot_risk:+,.0f}円'
                 f'（ドル円の「リスクと損益」とは別計上）</p>') if state else ""
    _swc = ecfg.get("スワップポイント") or {}
    _swy = _swc.get("円_1万通貨_1日") or {}
    sw_src = (f'1万通貨・1日 買{_swy.get("買", 0):+.0f}円／売{_swy.get("売", 0):+.0f}円'
              + ("（推定値・JFX公表値で要上書き）" if _swc.get("推定") else ""))
    op_html = (f'<div class="subgrp span2"><h3>{NM} <span class="badge pair {BCLS}">{CODE}</span>'
               f'<span class="badge new">{ecfg.get("採用日", "")} 採用</span></h3>'
               f'<span class="params">終値 {close:.3f}円・ATR {a_pips:.0f}pips</span>'
               f'{risk_line}'
               f'<p class="note">※円建てのため円換算なし。発注は MATRIX TRADER で<b>通貨ペアを {CODE} に切り替えて</b>手動で。'
               f'スワップは{html.escape(sw_src)}</p></div>'
               + "".join(cards))
    return op_html, text, fname, items


def _yenpair_section(cfg, pair="eurjpy"):
    """クロス円（ユーロ円・ポンド円＝2026-10-15 研究開始）のヘッダのレートと研究カード。
    売買ルールは落合さんの採用判断まで未採用＝発注カード・指示書は出さない（研究と表示のみ）。"""
    try:
        sr = json.load(open(R("results", f"research_{pair}_latest.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    close = sr.get("終値")
    NM = sr.get("通貨ペア") or {"eurjpy": "ユーロ円", "gbpjpy": "ポンド円"}.get(pair, pair)
    CODE = {"eurjpy": "EUR/JPY", "gbpjpy": "GBP/JPY"}.get(pair, pair.upper())
    LEG, CUR = sr.get("脚", "ユーロドル"), sr.get("通貨", "ユーロ")
    BCLS = {"eurjpy": "ej", "gbpjpy": "gj"}.get(pair, "ej")
    NTH = {"eurjpy": "3通貨目", "gbpjpy": "4通貨目"}.get(pair, "")
    head = (f'<div class="inst sub" data-pair="{pair}" data-dec="3" data-close="{float(close):.3f}"><span class="inst-l">{NM} {CODE}</span>'
            f'<span class="rate">{float(close):.3f}</span><span class="inst-n">日足終値{"" if (cfg.get(NM) or {}).get("システム") else "・研究中"}</span></div>'
            if close else "")
    adopted = bool((cfg.get(NM) or {}).get("システム"))
    fam_rows = ""
    for fam, r in (sr.get("型ごとの探索") or {}).items():
        t = (r.get("上位") or [None])[0]
        if not t:
            continue
        nb = t.get("周辺の平均スコア")
        fam_rows += (f'<tr><td>{html.escape(fam)}</td><td class="w">{html.escape(_eu_label(t["設定"]))}</td>'
                     f'<td>{t["勝率"]:.0%}</td><td>{t["PF全"]}（{t["PF前"]}/{t["PF後"]}）</td>'
                     f'<td>{t["最大DD"]:.1%}</td><td>{t["取引数"]}</td><td>{t["年率"]:.1%}</td>'
                     f'<td>{nb if nb is not None else "—"}</td></tr>')
    cand = ""
    for nm, v in (sr.get("選定パラメータ") or {}).items():
        t = v.get("検証", {})
        cand += (f'<li><b>{html.escape(nm)}</b>: {html.escape(_eu_label(v))}・リスク{float(v.get("リスク率", 0)):.1%}'
                 f'（勝率{t.get("勝率", 0):.0%}・PF{t.get("PF全")}・前半{t.get("PF前")}/後半{t.get("PF後")}'
                 f'・最大下落{t.get("最大DD", 0):.1%}・{t.get("取引数")}回）</li>')
    missing = [n for n in ("押し目戻り", "トレンド") if n not in (sr.get("選定パラメータ") or {})]
    if missing:
        cand += (f'<li>{"・".join(missing)}: 採用基準（前半・後半ともPF1.10以上、かつ近い設定でも崩れない）を'
                 f'満たす型がなく<b>候補なし</b></li>')
    usd_rows = ""
    for nm, v in (sr.get("ドル円の現行パラメータを当てた場合") or sr.get("ドル円の現行パラメータをユーロ円に当てた場合") or {}).items():
        usd_rows += (f'<li>ドル円の{html.escape(nm)}（{html.escape(_eu_label(v.get("設定", {})))}）をそのまま当てると'
                     f' PF{v.get("PF全")}（前半{v.get("PF前")}/後半{v.get("PF後")}）・{v.get("取引数")}回</li>')
    rel = sr.get("関係") or sr.get("ドル円・ユーロドルとの関係") or {}
    corr = rel.get("日次相関") or {}
    share = rel.get("主役の割合(直近60日)") or {}
    contrib = rel.get("値動きの寄与(直近250日)") or {}
    lead = rel.get("1時間足の先行遅行(相関)") or {}
    corr_txt = "／".join(f'{k} ドル円{v.get("ドル円")}・{LEG}{v.get(LEG)}' for k, v in corr.items())
    syn_rows = ""
    for nm, v in (sr.get("相乗効果") or sr.get("相乗効果(ドル円日足×ユーロ円確認)") or {}).items():
        base = v["比較"].get("現行", {})
        best_k, best = max(((kk, vv) for kk, vv in v["比較"].items() if kk != "現行"), key=lambda x: x[1]["スコア"])
        syn_rows += (f'<li>{html.escape(nm)}: 現行 スコア{base.get("スコア")}（PF{base.get("PF全")}・{base.get("取引数")}回）'
                     f' → 最良「{html.escape(best_k)}」スコア{best.get("スコア")}（PF{best.get("PF全")}・{best.get("取引数")}回）'
                     f'＝{html.escape(v.get("判定", ""))}</li>')
    upd = str(sr.get("更新", ""))[:16].replace("T", " ")
    status = (f"採用済み（{(cfg.get(NM) or {}).get('採用日', '')}〜・朝6〜8時の指示書で配信。上の『ポジションとリスク』に運用タイル）" if adopted
              else "研究のみ。売買ルールは<b>未採用</b>（採用は落合さんの判断。発注カード・指示書はまだ出ない）")
    body = (
        f'<p class="note">状態: {status}。毎朝8時の日次研究で更新。</p>'
        f'<h4 class="fh">採用候補（日足・15年）</h4><ul class="focus">{cand}</ul>'
        f'<h4 class="fh">型ごとの最良（前半〜2018／後半2019〜のPFの小さい方で選ぶ）</h4>'
        f'<div class="tablewrap"><table><tr><th>型</th><th>最良の設定</th><th>勝率</th><th>PF（前半/後半）</th>'
        f'<th>最大下落</th><th>回数</th><th>年率</th><th>周辺の平均</th></tr>{fam_rows}</table></div>'
        f'<p class="note">※「周辺の平均」＝設定を1段ずらした近い設定の平均スコア。1.0未満は当たり外れの設定（たまたま）の'
        f'疑いが強く採用しない。{html.escape(str(sr.get("前提", "")))}。</p>'
        f'<ul class="focus">{usd_rows}</ul>'
        f'<h4 class="fh">ドル円・{LEG}との関係（{NM}＝ドル円×{LEG}）</h4><ul class="focus">'
        f'<li>日次の相関: {html.escape(corr_txt)}</li>'
        f'<li>値動きの寄与（直近250日）: ドル円の脚 {contrib.get("ドル円の脚")}／{LEG}の脚 {contrib.get(LEG + "の脚")}'
        f'（1に近いほどその脚で{NM}の動きが説明できる＝いまは<b>円の強弱が主役</b>かどうか）</li>'
        f'<li>直近60日の主役: 円主導 {share.get("円主導", 0):.0%}／{CUR}主導 {share.get(CUR + "主導", 0):.0%}'
        f'／円・{CUR}同時 {share.get("円・" + CUR + "同時", 0):.0%}／打ち消し（ドルだけ動いた）{share.get("打ち消し(ドル主導)", 0):.0%}'
        f'／小動き {share.get("小動き・その他", 0):.0%}　最新日: <b>{html.escape(str(rel.get("最近の主役", "")))}</b></li>'
        f'<li>1時間足の先行: {NM}1本前→ドル円 {lead.get(NM + "1本前→ドル円")}／ドル円1本前→{NM} {lead.get("ドル円1本前→" + NM)}'
        f'（ほぼ0＝先に動いて予告する関係は無い。同時 {lead.get("同時")}）</li></ul>'
        f'<h4 class="fh">相乗効果の検証（ドル円の日足売買を「{NM}も同じ向き＝円全体が動いている」時だけにしたら）</h4>'
        f'<ul class="focus">{syn_rows}</ul>'
        f'<p class="note">※採用基準（スコア1.05倍以上・取引数6割以上）に届かなければ現行のまま。'
        f'スキャルの研究はスキャルピング欄（通貨ペアで「{NM}」を選ぶ）を参照。</p>')
    res_html = (f'<section class="card span2" id="{pair}-research"><div class="card-head"><h3>{NM}研究'
                f' <span class="badge pair {BCLS}">{CODE}</span><span class="badge new">{NTH}{"" if (cfg.get(NM) or {}).get("システム") else "・研究中"}</span></h3>'
                f'<span class="params">{html.escape(upd)}・{html.escape(str(sr.get("期間", "")))}</span></div>'
                + _clamp(f"cl-{pair}res", "c-m", body, f"{NM}研究の全文を表示") + '</section>')
    return head, res_html


def _eu_label(scfg):
    try:
        import research_eurusd as reu                          # noqa: PLC0415
        return reu.param_label(scfg)
    except Exception:                                        # noqa: BLE001
        return str(scfg)


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
    order_day = ""     # 表示している指示書の日付（YYYY-MM-DD）
    odir = R("results", "orders")
    if os.path.isdir(odir):
        files = [f for f in os.listdir(odir)
                 if f.endswith(".txt") and "_指示_" in f]
        if files:
            latest_date = max(f.split("_")[0] for f in files)
            order_day = latest_date
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
    # 2026-10-13: 指示書が今日（配信の札と同じ暦の日付）より前の日のものなら、その日付。
    # 夜中や土日に前日分の作業が「本日の作業」に見えないよう、バナーと「今日の発注」の
    # 見出し・札の文言だけを替える（件数・needs_action・要作業の判定は変えない）。
    _stale_day = (order_day[5:].replace("-", "/")
                  if order_day and order_day < date.today().isoformat() else "")
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
    scalp_research_ej = _rj("research_scalp_eurjpy_latest.json")      # 2026-10-15 ユーロ円（研究中）
    scalp_research_gj = _rj("research_scalp_gbpjpy_latest.json")      # 2026-10-15 ポンド円（研究中）
    ej_head, ej_res_html = _yenpair_section(cfg, "eurjpy")
    gj_head, gj_res_html = _yenpair_section(cfg, "gbpjpy")
    # 2026-10-15 クロス円の運用（落合さん承認）。ユーロドルと同じく別計上・指示書は別枠
    yp_op_html, yp_order_html, yp_items = "", "", []
    for _pr in ("eurjpy", "gbpjpy"):
        _op, _tx, _fn, _its = _yenpair_ops(cfg, _pr)
        yp_op_html += _op
        yp_items += _its
        if _tx:
            _nm = _its[0]["nm"] if _its else _pr
            yp_order_html += (f'<section class="card span2 orderbox" id="order-{_pr}"><div class="card-head">'
                              f'<h3>本日の{_nm}注文指示書（{"最終確定" if "確定" in _fn else "速報・参考"}）</h3></div>'
                              + _clamp(f"cl-order-{_pr}", "c-l", f'<pre class="order">{html.escape(_tx)}</pre>',
                                       f"{_nm}指示書の全文を表示") + '</section>')
    eu_head, eu_op_html, eu_res_html, eu_text, eu_fname, eu_items = _eurusd_section(cfg)
    eu_items = eu_items + yp_items          # 2026-10-15 クロス円も「今日の発注」に並べる（件数は別枠）
    # pre.order の要素・クラス・本文はそのまま（JS が倍率で書き換え、Drive へ書き出す）。
    # 並びはドル円の指示書の直後（Drive 調整版の連結順＝ドル円→ユーロドルを保つ）。
    eu_order_html = ('<section class="card span2 orderbox" id="order-eu"><div class="card-head">'
                     '<h3>本日のユーロドル注文指示書'
                     f'（{"最終確定" if "確定" in eu_fname else "速報・参考"}）</h3></div>'
                     + _clamp("cl-order-eu", "c-l",
                              f'<pre class="order">{html.escape(eu_text)}</pre>',
                              "ユーロドル指示書の全文を表示")
                     + '</section>') if eu_text else ""
    scalp_card = SCALP_CARD.replace("__SCALP_RESEARCH__",
                                    _scalp_research_html(scalp_research, "ドル円")
                                    + _scalp_research_html(scalp_research_eu, "ユーロドル")
                                    + _scalp_research_html(scalp_research_ej, "ユーロ円")
                                    + _scalp_research_html(scalp_research_gj, "ポンド円"))
    scalp_data_html = ('<script type="application/json" id="scalp-data">'
                       + json.dumps({"ドル円": {"研究": scalp_research, "値動き": _scalp_snapshot("usdjpy")},
                                     "ユーロドル": {"研究": scalp_research_eu,
                                               "値動き": _scalp_snapshot("eurusd")},
                                     "ユーロ円": {"研究": scalp_research_ej,
                                              "値動き": _scalp_snapshot("eurjpy")},
                                     "ポンド円": {"研究": scalp_research_gj,
                                              "値動き": _scalp_snapshot("gbpjpy")}},
                                    ensure_ascii=False).replace("</", "<\\/")
                       + "</script>"
                       # 2026-10-13: スキャルのチャート用（ローソク・EMA・ペーパートレード）。分析の材料とは別枠
                       + '<script type="application/json" id="scalp-chart">'
                       + json.dumps({"ドル円": _scalp_chart("usdjpy"), "ユーロドル": _scalp_chart("eurusd"),
                                   "ユーロ円": _scalp_chart("eurjpy"), "ポンド円": _scalp_chart("gbpjpy")},
                                    ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
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
                # 2026-10-13: 段階ごとに小さな札（ピル）で出す。判定と文言は従来どおり
                p_cls = "late" if late else "done"
                p_late = "⚠遅延" if late else ""
                parts.append(f'<span class="pill {p_cls}">{label} {t:%H:%M}{p_late}</span>')
            else:
                # 未到来のステージ（6時台に見る最終確定など）は警告対象にしない
                due = datetime.now().hour >= sched_h
                warn = warn or due
                p_cls = "miss" if due else "wait"
                parts.append(f'<span class="pill {p_cls}">{label} 未</span>')
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
        c_cls = "miss" if got < exp else "done"
        delivery = ('<div class="dlv"><span class="dlv-l">本日の配信</span>' + "".join(parts)
                    + ('<span class="pill miss">⚠配信遅延あり</span>' if warn else "")
                    + f'<span class="pill {c_cls}">完成率(直近{len(days)}営業日) {got}/{exp}'
                    + ("　⚠欠落あり" if got < exp else "") + '</span></div>')

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
    # 2026-10-13: 最上段「今日の発注」欄の行と、決済ライン接近の警告（表示専用）。
    # 件数は action_count（バナーと同じ数え方）をそのまま使い、ここでは数えない。
    today_rows, near_exit = [], []
    # 日足2枚のタイルに同じ文で入っていた決済ラインの説明は、RR欄の「見方」に1回だけ置く
    exit_help = ('<p class="note">※決済ラインは<b>終値での判定</b>。指値注文としては置かない'
                 '（日中に触れただけでは決済せず、終値で超えた翌朝の指示書が決済指示になる）。'
                 'MATRIX TRADERに実際に置く注文は各カードの「ストップ」のみ。</p>'
                 '<p class="note">※この線は<b>利確ではなくトレンド転換の撤退線</b>。含み損のまま到達すれば'
                 '損切りとして働く（利益が出る保証はない）。</p>')
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
        # 「本日新規」は建玉の札から外し、タイル見出しの札へ移した（2026-10-13）
        pos_html = (f'<span class="pos buy">買 {int(st.get("units", 0)):,}通貨</span>'
                    if pos > 0 else
                    f'<span class="pos sell">売 {int(st.get("units", 0)):,}通貨</span>'
                    if pos < 0 else '<span class="pos flat">ポジションなし</span>')
        _sy, _sd = 0.0, 0
        if pos:
            _sy, _sd = swap.accrued(pos, float(st.get("units") or 0),
                                    st.get("entry_date"),
                                    sw=swap.load(cfg))
        # 利確（手仕舞い）ライン: ドンチャンの逆側チャネル。2026-08-14 ユーザー
        # 指示で常時表示にした。それまで日足2システムはATRトレール逆指値しか
        # 出しておらず、実際にはその手前で効くことが多いこの線が見えなかった。
        exit_html = ""
        tp_now, tp_next = donchian_exit_levels(px, n_x, pos)
        if tp_now is not None:
            # 2026-10-13: 「ドンチャン10日高値」だけでは何の値か読めなかったため言い換え（表示のみ）
            side_txt = (f"前日までの{n_x}日の最高値（ドンチャン{n_x}日）を終値で超えたら決済"
                        if pos < 0 else
                        f"前日までの{n_x}日の最安値（ドンチャン{n_x}日）を終値で割ったら決済")
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
                first = ('<span class="first">逆指値より先に届く線</span>'
                         if near_tp else "")
            _dist_atr, _dist_pct = donchian_exit_proximity(px, n_e, n_x, pos,
                                                           atr_series)
            _near = _dist_pct is not None and _dist_pct <= 0.10
            # 決済ラインまでの距離を pips でも出す（換算は risk.order_widths に一本化。
            # ボード側で *100 を書かない＝同じ数字を2か所で計算しない）
            _exit_pips = (risk.order_widths(pos, close, stop=tp_now)["損切り幅pips"]
                          if pos else None)
            # 距離の基準は日足終値（＝ヘッダのレート）。デイトレ欄の値より古いことがあるため明記する
            prox = ""
            if _dist_atr is not None:
                prox = (f'・日足終値{close:.3f}円からあと約{_exit_pips:.0f}pips'
                        f'（1日の平均値幅の{_dist_atr:.2f}倍）'
                        if _exit_pips is not None else
                        f'・日足終値からの距離 1日の平均値幅の{_dist_atr:.2f}倍')
            if _near:
                prox += (f' <span class="alert">★過去の同方向保有中で最も近い{_dist_pct:.0%}の水準＝'
                         f'かなり接近中。値動き次第で近日中に決済判定が入りうる</span>')
                if pos:
                    near_exit.append((name, tp_now, _exit_pips))
            _p_now = _at(tp_now)
            nxt = ""
            if tp_next is not None and abs(tp_next - tp_now) >= 0.005:
                _p_nx = _at(tp_next)
                _mv = "切り下がる" if tp_next < tp_now else "切り上がる"
                nxt = (f'<div class="nxt">└ 次の引け以降は '
                       f'<b>{tp_next:.3f}</b>円へ{_mv}見込み'
                       f'（到達なら{_kind(_p_nx)} {_p_nx:+,.0f}円）'
                       f'※当日バーが確定するまでは暫定値</div>')
            _near_c = " near" if _near else ""
            exit_html = (f'<div class="exit{_near_c}"><div class="exit-h">'
                         f'<span class="k">決済ライン</span><b>{tp_now:.3f}</b>円'
                         f'（今回の引けの終値で判定・注文は置かない）{first}</div>'
                         f'<div>{side_txt}／到達なら'
                         f'{_kind(_p_now)} <b>{_p_now:+,.0f}</b>円'
                         f'（スワップ込み概算）{prox}</div>{nxt}'
                         '<div class="nxt">※利確ではなく撤退線（含み損でも到達すれば決済）。'
                         '詳しくは上の「表と決済ラインの見方」</div></div>')
        else:
            exit_html = ('<div class="exit"><span class="k">決済ライン</span>—'
                         '（建玉なし。建てた時点でドンチャン'
                         f'{n_x}日の逆側チャネルが決済ラインになる）</div>')
        if name in pending:   # 速報段階の暫定反映: 指示書本文と表示を一致させる
            act, cls = "ストップ到達→決済確認", "gold"
            needs_action, action_count = True, action_count + 1
        bt = Backtester(px, initial_capital=float(cfg["口座資金_円"]),
                        risk_per_trade=float(s["リスク率"]),
                        max_leverage=float(s["レバ上限"]), atr_stop_mult=atr_k)
        ref = bt.run(donchian(px, n=n_e, exit_n=n_x))
        m = ref["metrics"]
        _rr = None
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
        # 2026-10-13: 建玉の数字は文に埋めず、ラベルと値の箱（_kv）で並べる。値は上で求めた
        # ものを並べるだけ（含み損益は RR 欄と同じ _rr の値・新たに計算しない）。
        kvs = ""
        if name in pending:
            # 速報段階の暫定決済: 従来どおり到達の事実と決済見込みだけを出す
            kvs = _kv("ストップ到達", f'{float(st["stop"]):.3f}', "円",
                      small=f'決済見込み {pending[name]:+,.0f}円・8時最終確定で正式計上',
                      alert=True)
        else:
            if _rr:
                kvs += _kv("含み損益", f'{_rr["含み損益円"]:+,.0f}', "円",
                           cls="up" if _rr["含み損益円"] >= 0 else "down")
            kvs += (_kv("ストップ", f'{float(st["stop"]):.3f}', "円")
                    if st.get("stop") else _kv("ストップ", "設定なし"))
            if pos and st.get("stop"):
                kvs += _width_html(pos, st.get("entry"), close,
                                   st.get("stop"), None, basis="日足終値")
            if pos:
                kvs += _kv("スワップ", f'{_sy:+,.0f}', "円", small=f'{_sd}日分')
        act_disp = _act_disp(act, pos)
        # 今日の発注欄の行（needs_action と同じ条件で「要作業」）
        # 指示書が前の日のものなら札に対象日を添える（表示のみ・判定と件数は不変）
        _act_lbl = f"要作業・{_stale_day}分" if _stale_day else ""
        _done_ok = html.escape("（実施済みならそのままでOK）")
        if cls in ("buy", "sell", "gold"):
            _ops = ((order_json or {}).get("操作") or {}).get(name) or []
            if name in pending:
                today_rows.append(("act", "uj", name, html.escape(
                    f'ストップ {float(st["stop"]):.3f}円 到達→決済確認（決済見込み '
                    f'{pending[name]:+,.0f}円・8時最終確定で正式計上）'), "", _act_lbl))
            elif _ops:
                for _op in _ops:
                    _w, _p = _op_todo(_op)
                    today_rows.append(("act", "uj", name, _w + _done_ok, _p, _act_lbl))
            else:
                today_rows.append(("act", "uj", name,
                                   html.escape(f'{act_disp}（指示書の原文を参照）') + _done_ok,
                                   "", _act_lbl))
        else:
            if pos:
                _stp = f'{float(st["stop"]):.3f}円' if st.get("stop") else "（未設定）"
                _what = (f'{act_disp}・{"買" if pos > 0 else "売"} '
                         f'{int(st.get("units", 0)):,}通貨・逆指値 {_stp} が置かれているか確認')
            else:
                _what = f'{act_disp}（ポジションなし）'
            today_rows.append(("none", "uj", name, html.escape(_what), "",
                               "確認のみ" if pos else ""))
        _tile_cls = "card tile"
        if cls in ("buy", "sell", "gold"):
            _tile_cls += " need"
        if pos:
            _tile_cls += " side-buy" if pos > 0 else " side-sell"
        _badges = ('<span class="badges"><span class="badge pair">USD/JPY</span>'
                   '<span class="badge">日足</span>'
                   + ('<span class="badge new">本日新規</span>' if _new_today else "")
                   + '</span>')
        sys_cards.append(f"""
<article class="{_tile_cls}">
  <div class="tile-h"><h3>{html.escape(name)}</h3>{_badges}
    <span class="params">{html.escape(params)}</span></div>
  <div class="tile-s"><div class="action {cls}">{html.escape(act_disp)}</div>{pos_html}</div>
  <div class="kvs">{kvs}</div>
  {exit_html}
  <div class="chips">
    <span class="chip">15年検証 PF {m['プロフィットファクター']:.2f}<small>（利益÷損失）</small></span>
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
        _rr = None
        if dpos:
            _sy, _sd = swap.accrued(dpos, float(dst.get("units") or 0),
                                    dst.get("entry_date"), sw=swap.load(cfg))
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
            chips = (f'<span class="chip">2.8年検証 PF {dr.get("PF全", "－")}<small>（利益÷損失）</small></span>'
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
        # 2026-10-13: 見出しの括弧書き「（試験運転）」は札へ。数字は _kv の箱で並べる
        # （値の式は従来と同じ。含み損益は RR 欄と同じ _rr の値）。
        trial_tag = ('<span class="badge trial">試験運転</span>'
                     if dcfg.get("試験運転") else "")
        upd = html.escape(str(dstatus.get("更新", ""))[:16].replace("T", " "))
        dkvs = ""
        if _rr:
            dkvs += _kv("含み損益", f'{_rr["含み損益円"]:+,.0f}', "円",
                        cls="up" if _rr["含み損益円"] >= 0 else "down")
        dkvs += (_kv("ストップ", f'{float(dst["stop"]):.3f}', "円")
                 if dst.get("stop") else _kv("ストップ", "設定なし"))
        if dst.get("tp"):
            dkvs += _kv("利確", f'{float(dst["tp"]):.3f}', "円")
        if dpos:
            dkvs += _width_html(dpos, dst.get("entry"),
                                float(dstate.get("last_close") or close),
                                dst.get("stop"), dst.get("tp"),
                                basis="2時間足終値" if dstate.get("last_close") else "日足終値")
            dkvs += _kv("スワップ", f'{_sy:+,.0f}', "円", small=f'{_sd}日分')
        dact_disp = _act_disp(dact, dpos)
        today_rows.append(_intraday_todo("デイトレ複合時間軸", dstatus, dst, dpos,
                                         dact_disp, dcls))
        _dtile = "card tile"
        if dstatus.get("未通知"):
            _dtile += " need"
        if dpos:
            _dtile += " side-buy" if dpos > 0 else " side-sell"
        sys_cards.append(f"""
<article class="{_dtile}">
  <div class="tile-h"><h3>デイトレ複合時間軸</h3><span class="badges"><span class="badge pair">USD/JPY</span><span class="badge">毎時判定</span>{trial_tag}</span>
    <span class="params">{dparams}・指標発表を常時監視</span></div>
  <div class="tile-s"><div class="action {dcls}">{html.escape(dact_disp)}</div>{dpos_html}</div>
  <div class="kvs">{dkvs}</div>
  <div class="tile-meta"><span>最終判定 {upd}</span>{_bar_html(dstatus)}</div>
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
        _rr = None
        if p15:
            _sy, _sd = swap.accrued(p15, float(d15st.get("units") or 0),
                                    d15st.get("entry_date"), sw=swap.load(cfg))
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
            chips15 = (f'<span class="chip">3年検証 PF {dr15.get("PF全", "－")}<small>（利益÷損失）</small></span>'
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
        trial15 = ('<span class="badge trial">試験運転</span>'
                   if d15cfg.get("試験運転") else "")
        upd15 = html.escape(str(d15status.get("更新", ""))[:16].replace("T", " "))
        strat15 = html.escape(str(d15cfg.get("戦略", "")))
        params15 = f'{strat15}・ATR×{d15cfg.get("ATRストップ係数", "－")}'
        if d15cfg.get("利確ATR係数"):
            params15 += "・利確ATR×" + str(d15cfg.get("利確ATR係数"))
        params15 += "・指標発表を常時監視"
        kvs15 = ""
        if _rr:
            kvs15 += _kv("含み損益", f'{_rr["含み損益円"]:+,.0f}', "円",
                         cls="up" if _rr["含み損益円"] >= 0 else "down")
        kvs15 += (_kv("ストップ", f'{float(d15st["stop"]):.3f}', "円")
                  if d15st.get("stop") else _kv("ストップ", "設定なし"))
        if d15st.get("tp"):
            kvs15 += _kv("利確", f'{float(d15st["tp"]):.3f}', "円")
        if p15:
            kvs15 += _width_html(p15, d15st.get("entry"),
                                 float(d15state.get("last_close") or close),
                                 d15st.get("stop"), d15st.get("tp"),
                                 basis="1時間足終値" if d15state.get("last_close") else "日足終値")
            kvs15 += _kv("スワップ", f'{_sy:+,.0f}', "円", small=f'{_sd}日分')
        act15_disp = _act_disp(act15, p15)
        today_rows.append(_intraday_todo("デイトレ15分", d15status, d15st, p15,
                                         act15_disp, cls15))
        _tile15 = "card tile"
        if d15status.get("未通知"):
            _tile15 += " need"
        if p15:
            _tile15 += " side-buy" if p15 > 0 else " side-sell"
        sys_cards.append(f"""
<article class="{_tile15}">
  <div class="tile-h"><h3>デイトレ15分</h3><span class="badges"><span class="badge pair">USD/JPY</span><span class="badge">15分ごと判定</span>{trial15}</span>
    <span class="params">{params15}</span></div>
  <div class="tile-s"><div class="action {cls15}">{html.escape(act15_disp)}</div>{pos15_html}</div>
  <div class="kvs">{kvs15}</div>
  <div class="tile-meta"><span>最終判定 {upd15}</span>{_bar_html(d15status)}</div>
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
    # 2026-10-13: 表と決済ラインの説明は高さ制限（.clamp）で畳む。innerText には残るので
    # AI分析の材料は減らない。建玉が無い日も決済ラインの説明は残す。
    _rr_unreal = None   # リスク欄の「いまの含み損益」（成績欄の注記で参照・表示のみ）
    rr_card = ('<section class="card span2 rr"><div class="card-head"><h3>リスクと損益</h3>'
               '<span class="params">保有中の建玉なし</span></div>'
               + _clamp("cl-rrhow", "c-xs", '<h4 class="fh">決済ラインの見方</h4>' + exit_help,
                        "決済ラインの見方を表示")
               + '</section>')
    if rr_rows:
        pr = risk.portfolio_risk(rr_rows, cfg["口座資金_円"])
        _rr_unreal = pr["合計含み損益円"]
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
            _ucls = "up" if r["含み損益円"] >= 0 else "down"
            rr_tr.append(
                f'<tr><td>{html.escape(sys_short.get(r["システム"], r["システム"]))}</td>'
                f'<td>{r["建玉方向"]} {r["数量"]:,.0f}</td>'
                f'<td class="n">{r["建値"]:.3f}</td>'
                f'<td class="n">{r["現値"]:.3f}</td>'
                f'<td class="n {_ucls}">{r["含み損益円"]:+,.0f}円</td>'
                f'<td class="n {_cls}">{r["リスク額円"]:+,.0f}円</td>'
                f'<td class="n">{_rw}</td><td class="n">{_rr_v}</td>'
                f'<td class="w">{html.escape(_cmt)}{_warn}</td></tr>')
        _hedge = ""
        if pr["買建玉"] and pr["売建玉"]:
            _hedge = (f'　買{pr["買建玉"]:,.0f} / 売{pr["売建玉"]:,.0f} が'
                      f'ぶつかり、実質は<b>ネット'
                      f'{"買" if pr["ネット建玉"] > 0 else "売"} '
                      f'{abs(pr["ネット建玉"]):,.0f}通貨</b>'
                      f'（両建て度 {pr["両建て度"]:.0%}）。'
                      f'相場が一方向へ振れれば片側は利益になるため、'
                      f'合計リスクは最悪ケースの見積り。')
        _hedge_html = f'<p class="hedge">{_hedge.strip()}</p>' if _hedge else ""
        _ucls_all = "up" if pr["合計含み損益円"] >= 0 else "down"
        _net = "買" if pr["ネット建玉"] > 0 else "売" if pr["ネット建玉"] < 0 else "±"
        rr_how = _clamp(
            "cl-rrhow", "c-xs",
            '<h4 class="fh">表と決済ラインの見方</h4>'
            '<p class="note">※「ストップ到達」「利確到達」は<b>建値との差＋スワップ</b>で'
            '計算した決済損益の概算。含み益が乗っていればストップ到達でも利益になる'
            '（トレール後の正常な状態）。RR＝ここから先の利益余地÷損失余地で、'
            '利確目標を置かない日足2系統は「－」。'
            '「評価に使った値」は<b>各システム自身の最終確定バー終値</b>（日足系＝日足終値／デイ＝2時間足'
            '／15分＝1時間足）のため、行によって時点が異なることがある。</p>'
            '<p class="note">※スリッページ・窓開けは考慮していない。指標発表時や'
            '週明けの窓ではストップを超えて約定することがある。</p>' + exit_help,
            "表と決済ラインの見方を表示")
        rr_card = f"""
<section class="card span2 rr">
  <div class="card-head"><h3>リスクと損益</h3>
    <span class="params">ドル円の保有中の全建玉・全ストップ同時到達の最悪ケース（ユーロドルは下の欄で別計上）</span></div>
  <div class="kpis">
    <div class="kpi"><div class="l">合計リスク額<br><small>全ストップ同時到達</small></div>
      <div class="v down">{pr["合計リスク額円"]:+,.0f}円</div></div>
    <div class="kpi"><div class="l">合計リスク額の口座資金比</div>
      <div class="v">{pr["合計リスク率"]:.2%}</div></div>
    <div class="kpi"><div class="l">いまの含み損益<br><small>各システムの最新の足で評価（表の合計）</small></div>
      <div class="v {_ucls_all}">{pr["合計含み損益円"]:+,.0f}円</div>
      <div class="l"><small>@@UNREAL_UNIFIED@@</small></div></div>
    <div class="kpi"><div class="l">ネット建玉</div>
      <div class="v">{_net} {abs(pr["ネット建玉"]):,.0f}通貨</div></div>
  </div>
  {_hedge_html}
  <div class="tablewrap sticky1"><table>
    <tr><th>システム</th><th>売買/数量</th><th class="n">建値</th><th class="n">評価に使った値</th><th class="n">含み損益</th>
      <th class="n">ストップ到達</th><th class="n">利確到達</th><th class="n">RR</th><th>評価</th></tr>
    {"".join(rr_tr)}
  </table></div>
  {rr_how}
</section>"""

    # 合算KPI（4システム合算。速報段階は当日ストップ到達分を暫定加算する）
    perf_sum = perf.summary(trades, pending)
    realized_all = perf_sum["累計実現損益"]
    if perf_sum["件数"]:
        wr = f"{perf_sum['勝率']:.0%}"
        pf = f"{perf_sum['PF']:.2f}" if perf_sum["PF"] is not None else "－"
        tag = ('<small>（暫定）</small>'
               if pending else "")
        cum_html = (f'<span class="{"up" if realized_all >= 0 else "down"}">'
                    f'{realized_all:+,.0f}円</span>{tag}')
        ntr = f"{perf_sum['件数']}回"
    else:
        wr = pf = "－"; cum_html = "運用開始前"; ntr = "0回"
    # システム別の実現損益内訳（どのシステムの決済が効いているかを一目で）
    # sys_short はリスクリワードカードと共用（上で定義済み）。
    # 2026-10-13: チップの小さい字から、中央が0の横棒（右＝益・左＝損）に変更。
    # 棒の幅は見た目の縮尺だけ（最大の絶対値を50%とする）で、数字は従来どおり。
    _bmax = max((abs(float(b["損益円"])) for b in perf_sum["システム別"]),
                default=0.0) or 1.0
    perf_bars = ""
    for b in perf_sum["システム別"]:
        _bv = float(b["損益円"])
        _bc = "up" if _bv >= 0 else "down"
        perf_bars += (
            f'<li><span class="nm">{html.escape(sys_short.get(b["システム"], b["システム"]))}</span>'
            f'<span class="bar"><i class="{_bc}" style="width:{abs(_bv) / _bmax * 50:.1f}%"></i></span>'
            f'<span class="val {_bc}">{_bv:+,.0f}円'
            f'<small>{b["件数"]}回・勝率{b["勝率"]:.0%}</small></span></li>')
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
                       f'<small>／{len(open_pos)}建玉'
                       f'・内スワップ {unreal_swap:+,.0f}円</small>')
    else:
        unreal_html = '<span class="v">ポジションなし</span>'
    # 2026-08-19: 同じ画面にRRカード（各系の最終確定バー評価）と運用成績
    # （全建玉を日足終値で統一評価）の2つの含み損益が並び、基準の違いで数百円
    # ずれるため「どちらかが誤り」に見えていた。RRカード側に統一評価額を併記して
    # 別基準の同一計算であることを示す。値は unreal を再利用し二重計算しない。
    rr_card = rr_card.replace(
        "@@UNREAL_UNIFIED@@",
        f"成績欄と同じ日足終値{mark:,.3f}円で評価すると {unreal:+,.0f}円"
        if open_pos else "")
    # 2026-08-13: KPIの含み損益は全建玉を日足終値で統一評価するのに対し、
    # 指示書本文のデイトレ欄は2時間足終値で評価するため数十円ずれる。
    # 同一画面に違う合計が並んで「どちらかが誤り」に見えるため評価基準を明記する。
    mark_note = (f'※含み損益は全建玉を日足終値{mark:,.3f}円で統一評価'
                 f'（指示書本文のデイトレ欄は2時間足終値評価のため'
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
    # 2026-10-13: 増減はヘッダの計器帯と成績欄の大きな数字の下に出す（値は同じ変数）

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
    # 2026-10-13: 始点・終点の値はグラフの直下（spark_lab）、点の定義は「数字の出どころ」へ
    spark_lab, curve_def = "", ""
    if curve_dates:
        spark_lab = (
            f'<div class="spark-lab"><span>{html.escape(str(curve_dates[0]))} '
            f'<b>{curve[0]:,.0f}</b>円</span>'
            f'<span>{html.escape(str(curve_dates[-1]))} <b>{curve[-1]:,.0f}</b>円'
            f'（{curve[-1] - curve[0]:+,.0f}円・{len(curve)}営業日）</span></div>')
        curve_def = (
            '<p class="note">※資産推移の各点＝口座資金＋その日までの4システム累計実現損益（スワップ込）'
            '＋日足2システムの評価損益。デイトレ2系統の過去の含み損益は点に含まない'
            '（最終点のみ全建玉を評価）。</p>')
    elif curve:
        spark_lab = (f'<div class="spark-lab"><span>開始 <b>{curve[0]:,.0f}</b>円</span>'
                     f'<span>終点 <b>{curve[-1]:,.0f}</b>円</span></div>')

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
            # 2026-10-13: 検証で「常に上昇」を下回る＝エッジなし・表示のみなので、
            # 買い/売りの方向色をやめて灰色の参考表示にした（値と文言は不変）
            fc_html = (
                f'<span class="pos flat">{html.escape(fc.get("方向", ""))}'
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
                for v, c in ((fbal["円高"], "--sell"), (fbal["両方向"], "--line-strong"),
                             (fbal["円安"], "--buy")))
            bal_html = (
                f'<div class="fbal"><div class="fbar-head"><b>{html.escape(fbal["判定"])}</b>'
                f'<span class="stop">材料の重み（重要度の合計）　'
                f'円高 {fbal["円高"]}／両方向 {fbal["両方向"]}／円安 {fbal["円安"]}'
                f'</span></div><div class="fbar">{seg}</div>'
                '<div class="fbar-legend"><span>← 円高（ドル安）</span>'
                '<span>円安（ドル高）→</span></div></div>')
        impl = str(news.get("保有ポジションへの含意") or "").strip()
        impl_html = (f'<div class="fbox key"><div class="l">保有ポジションへの含意</div>'
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
        # 2026-10-13: スキャル指針はドル円・ユーロドルを横並びの箱（guide-row）に。畳まない
        # （スキャル分析の AI はここを画面の文字＝boardText から読む）。
        for key, ttl, gcls in (("スキャル指針", "スキャル指針・ドル円", "uj"),
                               ("スキャル指針_ユーロドル", "スキャル指針・ユーロドル", "eu"),
                               ("スキャル指針_ユーロ円", "スキャル指針・ユーロ円", "ej"),
                               ("スキャル指針_ポンド円", "スキャル指針・ポンド円", "gj")):
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
                guide_html += (f'<div class="fbox guide {gcls}"><div class="l">{ttl}（ファンダ研究が3時間おきに更新）</div>'
                               + "<br>".join(parts) + "</div>")
        if guide_html:
            guide_html = f'<div class="guide-row">{guide_html}</div>'
        eu_fund_html = ""
        eu_f = news.get("ユーロドル")
        if isinstance(eu_f, dict) and eu_f:
            parts = [f"<b>{html.escape(k)}</b>: {html.escape(chr(12289).join(map(str, v)) if isinstance(v, list) else str(v))}"
                     for k, v in eu_f.items() if v]
            eu_fund_html = ('<div class="fbox"><div class="l">ユーロドルのファンダ（ECB・ユーロ圏・ドルとの関係）</div>'
                            + "<br>".join(parts) + "</div>")
        for _k, _ttl in (("ユーロ円", "ユーロ円のファンダ（円とユーロ・ECBと日銀）"),        # 2026-10-15
                         ("ポンド円", "ポンド円のファンダ（円とポンド・BOEと日銀）")):
            ej_f = news.get(_k)
            if isinstance(ej_f, dict) and ej_f:
                parts = [f"<b>{html.escape(k)}</b>: {html.escape(chr(12289).join(map(str, v)) if isinstance(v, list) else str(v))}"
                         for k, v in ej_f.items() if v]
                eu_fund_html += (f'<div class="fbox"><div class="l">{_ttl}</div>'
                                 + "<br>".join(parts) + "</div>")
        themes = news.get("テーマ") or []
        items = "".join(_theme_row(t) for t in themes[:8])
        # 上位3件は出したまま、4〜8件目は高さ制限で畳む（innerText には残る）
        themes_top = "".join(_theme_row(t) for t in themes[:3])
        themes_rest = "".join(_theme_row(t) for t in themes[3:8])
        news_upd = str(news.get("更新") or "")
        summary = str(news.get("総括") or "")
        _sum_upd = "（更新 " + html.escape(news_upd) + "）" if news_upd else ""
        if items:
            news_top = f'{impl_html}{focus_html}{guide_html}'
            news_list = (f'<h4 class="fh">材料一覧（新しい順・上位8件／全{len(themes)}件）</h4>'
                         f'<div class="themes">{themes_top}</div>'
                         + (_clamp("cl-themes", "c-xs", f'<div class="themes">{themes_rest}</div>',
                                   "残りの材料を表示") if themes_rest else ""))
            summary_html = (f'<details><summary>ニュース総括の全文{_sum_upd}</summary>'
                            f'<p class="note">{html.escape(summary)}</p></details>')
        else:
            news_top = '<p class="note">ニュース未収集</p>'
            news_list = eu_fund_html = summary_html = ""
        stale_html = ""
        fu_upd = str(fu.get("更新") or "")
        try:
            age_h = (datetime.now() - datetime.fromisoformat(fu_upd)) \
                .total_seconds() / 3600
            if age_h > 20:
                fund_stale_days = age_h / 24
                # 2026-10-13: カード内は banner ではなく callout（上部バナーと二重に
                # AI分析の材料へ入っていたため。上部バナーは従来どおり出す）
                stale_html = (
                    '<p class="callout warn">⚠ ファンダ研究が'
                    f'{fund_stale_days:.1f}日更新されていません'
                    '（3時間おき更新の想定・専任タスクの停止を確認してください）</p>')
        except (ValueError, TypeError):
            pass
        try:
            import fund_live                                 # noqa: PLC0415
            live_html = "<br>".join(html.escape(x.strip())
                                    for x in fund_live.status_lines())
        except Exception as e:                               # noqa: BLE001
            live_html = html.escape(f"状況の取得に失敗（{type(e).__name__}）")
        # 2026-10-13: 中身は変えず並びと包みだけ変更。結論（地合い・含意・次の焦点・
        # スキャル指針）を先頭に、検証値・本運用の状況は後ろへ。含意・焦点・指針・地合いは
        # 畳まない（AI分析の材料）。本運用の箱は高さ制限（innerText には残る）。
        _fund_upd = html.escape(str(fu.get("更新", "")))[:16].replace("T", " ")
        _news_upd = ("／ニュース " + html.escape(news_upd)[:16].replace("T", " ")
                     if news_upd else "")
        fund_live_html = _clamp(
            "cl-fundlive", "c-xs",
            f'<div class="fbox"><div class="l">ファンダ本運用（売買判定への自動組込み）</div>{live_html}</div>'
            '<p class="note">※検証ゲートを3営業日連続で通過したルールだけを自動で新規建ての判定'
            '（見送り・数量）に使い、3営業日連続で不通過なら自動で外す。方向予測は的中率が'
            '「常に上昇」を上回ることも条件。発注は従来どおり本人が手動で行う。</p>',
            "本運用の状況を表示")
        fund_html = f"""
<section class="card span2 fund">
  <div class="card-head"><h3>ファンダメンタルズ</h3>
    <span class="params">3時間おき更新・本運用は自動昇格方式・定量 {_fund_upd}{_news_upd}</span></div>
  {stale_html}
  <p class="regime">{html.escape(str(fu.get("レジーム", "")))}</p>
  {news_top}
  {bal_html}
  <div class="chips">{corr_chips}</div>
  {news_list}
  <div class="fc-line"><span class="k">翌日予測（参考）</span>{fc_html}</div>
  <p class="note">{html.escape(wf_txt)}／{html.escape(fwd_txt)}</p>
  {eu_fund_html}
  {fund_live_html}
  {summary_html}
</section>"""

    # 直近の記録（日足2システムの日次ログ + デイトレの毎時アクションログを
    # 時系列でマージして新しい順に表示）
    def _stop_disp(v):    # 決済行などストップ空欄は "nan" ではなく "—" を出す
        s = str(v or "").strip()
        if s.lower() in ("", "nan", "none"):
            return "—"
        try:                  # 桁をそろえる（157.93 と 158.221 が混在していた・表示のみ）
            return f"{float(s):.3f}"
        except ValueError:
            return s

    # 2026-10-13: 日付の表示を「10/12」（日足）と「10/12 20:04」（デイトレ）にそろえた
    # （並べ替えのキーは従来どおり先頭の完全な日時）。
    recs = []
    for _, r in daily.tail(10).iterrows():
        recs.append((f"{r['日付']} 08:00", str(r["日付"])[5:10].replace("-", "/"),
                     str(r["システム"])[:2], str(r["アクション"]),
                     _stop_disp(r["ストップ"]), f"{float(r['モデル資産']):,.0f}"))
    dlog = _read_csv(R("results", "intraday_log.csv"), ["日時"])
    for _, r in dlog.tail(10).iterrows():
        ts = str(r["日時"]).replace("T", " ")
        stop_s = _stop_disp(r.get("ストップ", ""))
        recs.append((ts, ts[5:16].replace("-", "/"), "デイ", str(r["アクション"]), stop_s, "—"))
    d15log = _read_csv(R("results", "intraday15_log.csv"), ["日時"])
    for _, r in d15log.tail(10).iterrows():
        ts = str(r["日時"]).replace("T", " ")
        stop_s = _stop_disp(r.get("ストップ", ""))
        recs.append((ts, ts[5:16].replace("-", "/"), "15分", str(r["アクション"]), stop_s, "—"))
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
        _pcls = "up" if pnl >= 0 else "down"
        trade_rows += (
            f'<tr><td>{html.escape(str(r["決済"])[5:16].replace("-", "/"))}</td>'
            f'<td>{html.escape(sys_short.get(r["システム"], str(r["システム"])))}</td>'
            f'<td>{html.escape(str(r["売買"]))} {int(r["数量"]):,}</td>'
            f'<td class="n">{float(r["建値"]):.3f}→{float(r["決済値"]):.3f}</td>'
            f'<td class="n">{swp_s}</td>'
            f'<td class="n {_pcls}">{pnl:+,.0f}</td></tr>')
    if not trade_rows:
        trade_rows = '<tr><td colspan="6">まだ決済はありません</td></tr>'

    # 2026-10-13: バナーは種類別の色（要作業＝琥珀／なし＝青灰／速報＝点線／警告＝紅）。
    # 件数の数え方は従来どおり。対象の指示書の日付と段階を明記し、「今日の発注」へ飛べる
    # リンクを付ける（リンクの文字は CSS で出すので AI分析の材料には入らない）。
    _od = (f'・{str(order_json.get("日付", ""))[5:].replace("-", "/")} '
           f'{order_json.get("段階", "")}分' if order_json else "")
    eu_need = sum(1 for it in eu_items
                  if it["cls"] in ("buy", "sell", "gold") and it["act"] != "指示書待ち")
    _eu_sfx = f'（ユーロドル・クロス円は別に{eu_need}件）' if eu_need else ""
    if is_flash:
        banner = ('<div class="banner flash">速報配信中——正式な発注判断は'
                  '8時すぎの【最終確定】で</div>')
    elif needs_action and _stale_day:
        # 指示書が前の日のもの（夜中・土日）。件数は同じ数え方のまま、文言だけ替える
        _stage = html.escape(str((order_json or {}).get("段階", "")))
        banner = (f'<div class="banner act">直近の発注作業 {action_count}件'
                  f'（{_stale_day} {_stage}の指示書の分を含む）— 実施済みなら対応不要。'
                  f'次の指示書は朝6時台から{_eu_sfx}'
                  '<a class="go" href="#today" aria-label="直近の発注へ"></a></div>')
    elif needs_action:
        banner = (f'<div class="banner act">本日は発注作業があります'
                  f'（{action_count}件{html.escape(_od)}・下の「今日の発注」どおりに）{_eu_sfx}'
                  '<a class="go" href="#today" aria-label="今日の発注へ"></a></div>')
    else:
        banner = f'<div class="banner ok">本日の発注作業はありません{_eu_sfx}</div>'
    # 2026-09-09: ファンダ研究の停止警告が下部セクションに埋もれて気づきにくい
    # ため、上部バナー直後にも短い注意を出す（詳細は下のファンダ・予測研究欄）
    if fund_stale_days is not None:
        banner += (
            f'<div class="banner warn">⚠ ファンダ研究停止 '
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
            f'<div class="banner warn">⚠ 今後24時間に★5級指標 '
            f'{jst:%m/%d %H:%M} {html.escape(label)}{extra}'
            f'（新規建ては全系見送り）</div>')

    # 生成元（Mac／GitHub Actions）。ボードがどちらの系で作られたかを一目で判別する
    # （Mac沈黙時はクラウド補完routineがActions生成のHTMLを同一URLへ公開する設計）
    origin = ("クラウド(GitHub Actions)" if os.environ.get("GITHUB_ACTIONS") == "true"
              else "Mac")

    # ── 2026-10-13 ビジュアル改修: ページの部品（page の f-string に式を詰め込まない）──
    # 今日の発注（最上段）。ユーロドルは件数に入れず「指示書待ち／要作業」の行だけ足す。
    for it in eu_items:
        if it["act"] == "指示書待ち":
            _st = "wait"
            _what = "朝6時台の配信で最初の指示書が出ます"   # 札（指示書待ち）と同じ語を重ねない
        elif it["cls"] in ("buy", "sell", "gold"):
            _st = "act"
            _what = f'{it["act"]}・{it.get("nm", "ユーロドル")}の指示書（原文）どおりに'
        else:
            _st = "none"
            if it["pos"]:
                _stp = f'{it["stop"]:.{it.get("dec", 5)}f}' if it["stop"] else "（未設定）"
                _what = (f'{it["act"]}・{"買" if it["pos"] > 0 else "売"} {it["units"]:,.0f}通貨・'
                         f'逆指値 {_stp} が置かれているか確認')
            else:
                _what = f'{it["act"]}（ポジションなし）'
        _lbl = "確認のみ" if (_st == "none" and it["pos"]) else ""
        today_rows.append((_st, it.get("pair", "eu"), it["name"], html.escape(_what), "", _lbl))
    today_html = _today_html(today_rows, order_json, is_flash, action_count, near_exit,
                             stale_day=_stale_day, basis_close=close)
    # 指示書の原文（pre.order の要素・クラス・本文はそのまま。高さ制限で畳むだけ）
    _order_kind = "速報・参考" if is_flash else "最終確定"
    order_box = ('<section class="card span2 orderbox" id="order"><div class="card-head">'
                 '<h3>注文指示書（原文・MATRIX TRADER 転記用）</h3>'
                 f'<span class="params">{_order_kind}・指標イベント欄含む・日足の欄は朝6〜8時時点の文面'
                 '（デイトレは末尾の最新指示が正）</span></div>'
                 + _clamp("cl-order", "c-l", f'<pre class="order">{html.escape(order_text)}</pre>',
                          "指示書の全文を表示")
                 + '</section>')
    # ページ内リンクの帯（.grid の外＝AI分析の材料外）
    _cnt = f'<span class="cnt">{action_count}</span>' if action_count else ""
    _fund_link = '<a href="#g-fund">ファンダ</a>' if fund_html else ""
    nav_html = (f'<nav class="jump" aria-label="ページ内の移動"><a href="#today">今日の発注{_cnt}</a>'
                '<a href="#g-pos">ポジション</a><a href="#g-tools">分析・スキャル</a>'
                f'{_fund_link}<a href="#g-perf">成績</a><a href="#g-log">記録・研究</a><a href="#inbox">💬連絡</a></nav>')
    fund_head = ('<h2 class="grp" id="g-fund">ファンダ<small>相場の地合い・材料・スキャル指針</small></h2>'
                 if fund_html else "")
    _nsys = len(sys_cards) + len(eu_items)
    # 運用成績と資産推移を1枚に（数字はすべて上で求めた変数を並べるだけ）
    _eq_pct = _eq_diff / _eq_base
    equity_def = (f'<p class="note">※モデル資産＝口座資金{_eq_base:,.0f}円'
                  '＋累計実現損益＋含み損益（4システム合算・毎回その場で再計算）。</p>')
    _sysbar_html = (f'<h4 class="fh">システム別の実現損益</h4><ul class="sysbar">{perf_bars}</ul>'
                    if perf_bars else "")
    # リスク欄の含み損益（各システムの最新の足で評価）と符号まで違うことがあるため、こちらからも案内する
    _unreal_ref = (f'<br><small>リスク欄の{_rr_unreal:+,.0f}円は各システムの最新の足で評価'
                   '（デイトレ2つは評価時点が新しいため差が出る）</small>'
                   if (open_pos and _rr_unreal is not None) else "")
    perf_card = (
        '<section class="card span2 perf"><div class="card-head"><h3>運用成績</h3>'
        '<span class="params">ドル円4システム合算（実現＋含み）・ユーロドルは未計上</span></div>'
        f'<div class="perf-hero"><div><span class="hero-l">モデル資産</span>'
        f'<b class="hero-v {_eq_cls}">{equity_txt}</b>'
        f'<span class="hero-d {_eq_cls}">開始 {_eq_base:,.0f}円から {_eq_diff:+,.0f}円（{_eq_pct:+.2%}）</span></div>'
        f'<div><h4 class="fh">{curve_label}<small>破線＝開始資金</small></h4>'
        f'{_spark_svg(curve, base=_eq_base)}{spark_lab}</div></div>'
        '<div class="kpis">'
        f'<div class="kpi"><div class="l">累計実現損益</div><div class="v">{cum_html}</div></div>'
        f'<div class="kpi"><div class="l">含み損益（{mark:.3f}円評価）{_unreal_ref}</div><div class="v">{unreal_html}</div></div>'
        f'<div class="kpi"><div class="l">勝率</div><div class="v">{wr}</div></div>'
        f'<div class="kpi"><div class="l">PF<br><small>利益÷損失・1超で黒字</small></div><div class="v">{pf}</div></div>'
        f'<div class="kpi"><div class="l">決済取引</div><div class="v">{ntr}</div></div>'
        '</div>'
        f'{_sysbar_html}{kpi_note}'
        + _clamp("cl-perf", "c-xs", equity_def + swap_note + curve_def, "数字の出どころを表示")
        + '</section>')
    trades_card = (
        '<section class="card span2"><div class="card-head"><h3>決済履歴</h3>'
        '<span class="params">ドル円4システム・新しい順・直近12件</span></div>'
        + _clamp("cl-trades", "c-s",
                 '<div class="tablewrap sticky1"><table>'
                 '<tr><th>決済</th><th>システム</th><th>売買/数量</th><th class="n">建値→決済値</th>'
                 f'<th class="n">スワップ</th><th class="n">損益円</th></tr>{trade_rows}</table></div>'
                 '<p class="note">※スワップは建玉からのロールオーバー跨ぎ回数で計上'
                 f'（JST火〜金の朝{swap.rollover_hour_jst(date.today())}時・木曜は3日分）。'
                 '2026-08-13より前に決済した行は現在のレートでの遡及概算。</p>',
                 "決済履歴をすべて表示")
        + '</section>')
    research_card = (
        '<section class="card half"><h3>研究・進化</h3><dl class="kv-list">'
        f'<dt>日次研究</dt><dd>{html.escape(res_msg)}</dd>'
        '<dt>ファンダ・フィルタ検証（マクロで新規を絞ると成績は上がるか・発注には未使用）</dt>'
        f'<dd>{html.escape(ff_msg)}</dd>'
        f'<dt>週次ペア研究</dt><dd>{html.escape(pair_msg)}</dd></dl></section>')

    # 2026-10-13: CSS は BOARD_CSS（ページ全体）＋ ANALYSIS_CSS（分析・スキャル欄）。
    # 書体は Google Fonts（公開先の CSP で許可済み）から数字用の IBM Plex Mono と、
    # 指示書の原文用の BIZ UDGothic だけを読む（読めなくても標準書体で崩れない）。
    page = f"""<title>FXドル円 運用ボード</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDGothic&family=IBM+Plex+Mono:wght@500;600&display=swap">
<style>
{BOARD_CSS}
{ANALYSIS_CSS}
</style>
<div class="wrap">
<header class="top">
  <div class="top-id"><h1>FX運用ボード<small>ドル円・ユーロドル・ユーロ円・ポンド円</small></h1>
    <span class="meta"><span class="upd">更新 {datetime.now():%Y-%m-%d %H:%M}</span>｜生成元: {origin}</span></div>
  <div class="inst-row">
    <div class="inst" data-pair="usdjpy" data-dec="3" data-close="{close:.3f}"><span class="inst-l">ドル円 USD/JPY</span><span class="rate">{close:.3f}<small>円</small></span><span class="inst-n">日足終値</span></div>
    {eu_head}
    {ej_head}
    {gj_head}
    <div class="inst eq"><span class="inst-l">モデル資産</span><b class="eqv">{equity_txt}</b><span class="d {_eq_cls}">{_eq_diff:+,.0f}円（{_eq_pct:+.2%}）</span></div>
  </div>
  <div class="live-bar"><button class="abtn live" id="live-btn" type="button">現在価格に更新</button><span class="meta-s" id="live-st">表示中の価格は日足終値（ボード作成時点）</span></div>
  {delivery}
  <p class="meta-s">配信は朝6・7・8時台の3回＋デイトレ毎時判定｜運用先: MATRIX TRADER（発注は手動）</p>
</header>
{banner}
{nav_html}
<div class="grid">
{today_html}
{order_box}
{eu_order_html}
{yp_order_html}
<h2 class="grp" id="g-pos">ポジションとリスク<small>保有中の建玉・{_nsys}システムの状態</small></h2>
{rr_card}
{''.join(sys_cards)}
{eu_op_html}
{yp_op_html}
<h2 class="grp" id="g-tools">分析・スキャル<small>ボタンで動く道具</small></h2>
{ANALYSIS_CARD}
{scalp_card}
{fund_head}
{fund_html}
<h2 class="grp" id="g-perf">成績<small>モデル資産・勝率・決済履歴</small></h2>
{perf_card}
{trades_card}
<h2 class="grp" id="g-log">記録・研究<small>ユーロドル研究・ユーロ円研究・ポンド円研究・直近の記録・研究ログ</small></h2>
{eu_res_html}
{ej_res_html}
{gj_res_html}
<section class="card half">
  <h3>直近の記録</h3>
  <div class="tablewrap"><table>
    <tr><th>日付</th><th>システム</th><th>アクション</th><th>ストップ</th></tr>
    {rows_html}
  </table></div>
</section>
{research_card}
</div>
<style>{INBOX_CSS}</style>
{INBOX_HTML}
<footer>発注はMATRIX TRADERで本人が手動で行う（JFXはEA・自動売買禁止）。逆指値は建玉と必ずセット。
検証値は過去データによるもので将来の成果を保証しない。高重要度指標イベント日は新規建てを自動見送り。
本ページは情報提供であり投資助言ではない。</footer>
</div>
{order_data_html}
{scalp_data_html}
{ANALYSIS_JS}
{LIVE_JS}
{INBOX_JS}
"""
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Webボード生成: {OUT_HTML}")
    return OUT_HTML


if __name__ == "__main__":
    build()
