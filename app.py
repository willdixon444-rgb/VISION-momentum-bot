import os
import logging
from flask import Flask, render_template_string, jsonify
from datetime import datetime
from vision_engine import VisionEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VISION")

try:
    from database import init_db
    init_db()
except Exception as e:
    logger.warning(f"Database init failed (memory only): {e}")

app    = Flask(__name__)
engine = VisionEngine()
engine.start()

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="VISION">
<title>VISION | Warrior Trading</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#060a10;--surface:#0a101a;--card:#0e1520;--border:#162030;
  --accent:#00d4ff;--gold:#f5c518;--green:#00ff87;--red:#ff3355;
  --orange:#ff8c00;--purple:#9b70ff;
  --muted:#1e3050;--muted2:#3a5878;--text:#c0d8f0;--text2:#6a90b8;
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;
  font-size:12px;min-height:100vh;padding-bottom:20px;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(ellipse 70% 35% at 15% 0%,rgba(0,212,255,.05) 0%,transparent 65%),
             radial-gradient(ellipse 50% 40% at 85% 95%,rgba(0,255,135,.04) 0%,transparent 65%)}

/* ── HEADER ─────────────────────────────── */
.hdr{position:sticky;top:0;z-index:200;
  background:rgba(6,10,16,.95);backdrop-filter:blur(14px);
  border-bottom:1px solid var(--border)}
.hdr-top{display:flex;align-items:center;justify-content:space-between;
  padding:10px 14px 8px;gap:8px}
.logo{font-family:'Syne',sans-serif;font-size:20px;font-weight:800;
  color:var(--accent);letter-spacing:1px;line-height:1}
.logo span{color:var(--gold)}
.hdr-meta{font-size:9px;color:var(--muted2);margin-top:2px;letter-spacing:.3px}
.hdr-right{display:flex;align-items:center;gap:8px;flex-shrink:0}
.pill{font-size:9px;font-weight:700;letter-spacing:1.5px;padding:3px 10px;
  border-radius:20px;border:1px solid;text-transform:uppercase}
.pill-paper{background:rgba(245,197,24,.08);color:var(--gold);border-color:rgba(245,197,24,.3)}
.pill-live{background:rgba(0,255,135,.08);color:var(--green);border-color:rgba(0,255,135,.3)}
.pill-closed{background:rgba(58,88,120,.2);color:var(--muted2);border-color:var(--border)}

/* Stat bar */
.stat-bar{display:flex;overflow-x:auto;scrollbar-width:none;
  border-top:1px solid var(--border)}
.stat-bar::-webkit-scrollbar{display:none}
.s-item{flex:0 0 auto;padding:7px 14px;border-right:1px solid var(--border)}
.s-lbl{font-size:8px;color:var(--muted2);letter-spacing:.8px;text-transform:uppercase}
.s-val{font-size:14px;font-weight:600;margin-top:1px;color:var(--text)}
.s-val.g{color:var(--green)}.s-val.r{color:var(--red)}
.s-val.a{color:var(--accent)}.s-val.o{color:var(--gold)}
.s-val.p{color:var(--purple)}

/* Sweep bar */
.sweep{height:2px;background:var(--border);overflow:hidden}
.sweep-fill{height:100%;width:25%;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);
  animation:sweep 15s linear infinite}
@keyframes sweep{0%{margin-left:-25%}100%{margin-left:100%}}

/* ── TABS ────────────────────────────────── */
.tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border);
  position:sticky;top:94px;z-index:199;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:1;min-width:72px;padding:10px 4px 8px;
  font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;
  letter-spacing:.3px;color:var(--muted2);background:none;
  border:none;border-bottom:2px solid transparent;cursor:pointer;
  white-space:nowrap;transition:color .15s,border-color .15s}
.tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.tab:active{opacity:.6}

/* ── PANES ───────────────────────────────── */
.pane{display:none;padding:12px 12px 4px;animation:fadeIn .18s ease}
.pane.on{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}

/* ── CARDS ───────────────────────────────── */
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  margin-bottom:10px;overflow:hidden}
.card-head{padding:9px 14px;font-size:9px;font-weight:700;letter-spacing:1px;
  color:var(--muted2);text-transform:uppercase;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between}
.card-body{padding:12px 14px}

/* ── EMPTY ───────────────────────────────── */
.empty{text-align:center;padding:36px 20px;color:var(--muted2)}
.empty-icon{font-size:36px;margin-bottom:10px}
.empty-txt{font-size:10px;line-height:1.7}

/* ── POSITIONS ───────────────────────────── */
.pos-card{background:var(--card);border:1px solid var(--border);
  border-radius:10px;margin-bottom:10px;overflow:hidden}
.pos-hdr{display:flex;align-items:flex-start;justify-content:space-between;
  padding:12px 14px 10px;border-bottom:1px solid var(--border)}
.pos-sym{font-family:'Syne',sans-serif;font-size:26px;font-weight:800;color:#fff}
.pos-state{font-size:9px;color:var(--muted2);margin-top:2px;letter-spacing:.5px}
.pos-pnl{font-family:'Syne',sans-serif;font-size:24px;font-weight:700;text-align:right}
.pos-pnl.g{color:var(--green)}.pos-pnl.r{color:var(--red)}.pos-pnl.n{color:var(--muted2)}
.pos-body{padding:12px 14px}
.levels{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:10px}
.lv{background:var(--surface);border-radius:7px;padding:8px;text-align:center}
.lv-lbl{font-size:8px;color:var(--muted2);letter-spacing:.5px;text-transform:uppercase}
.lv-val{font-size:14px;font-weight:600;margin-top:3px}
.lv-stop .lv-val{color:var(--red)}
.lv-t1 .lv-val{color:var(--gold)}
.lv-t2 .lv-val{color:var(--green)}
.prog-wrap{margin-bottom:8px}
.prog-lbls{display:flex;justify-content:space-between;font-size:9px;
  color:var(--muted2);margin-bottom:4px}
.prog-bg{height:5px;background:var(--muted);border-radius:5px;overflow:hidden}
.prog-fill{height:100%;border-radius:5px;
  background:linear-gradient(90deg,var(--gold),var(--green));transition:width .4s}
.pos-tags{display:flex;flex-wrap:wrap;gap:5px}
.tag{font-size:9px;font-weight:600;padding:2px 8px;border-radius:4px;
  background:var(--surface);border:1px solid var(--border);color:var(--text2)}

/* ── SCANNER ─────────────────────────────── */
.scan-btn{display:flex;align-items:center;justify-content:center;gap:6px;
  width:100%;padding:10px;margin-bottom:10px;
  background:rgba(0,212,255,.07);border:1px solid rgba(0,212,255,.2);
  border-radius:8px;color:var(--accent);font-family:'JetBrains Mono',monospace;
  font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.5px;
  transition:background .15s}
.scan-btn:active{background:rgba(0,212,255,.15)}
.spin{display:inline-block;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.sec-lbl{font-size:9px;font-weight:700;letter-spacing:2px;text-transform:uppercase;
  display:flex;align-items:center;gap:6px;padding:6px 0 8px;color:var(--muted2)}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.dot-g{background:var(--green)}.dot-m{background:var(--muted2)}

.srow{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;
  padding:11px 0;border-bottom:1px solid var(--border)}
.srow:last-child{border-bottom:none}
.srow-left{padding-left:8px}
.srow.ready{border-left:3px solid var(--green);background:rgba(0,255,135,.015)}
.srow.watch{border-left:3px solid var(--border);padding-left:10px}
.srow-sym{font-family:'Syne',sans-serif;font-size:22px;font-weight:800;color:#fff}
.srow-badges{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.bdg{font-size:9px;font-weight:600;padding:2px 7px;border-radius:4px;
  white-space:nowrap;border:1px solid}
.bdg-bf{background:rgba(0,212,255,.1);color:var(--accent);border-color:rgba(0,212,255,.25)}
.bdg-news{background:rgba(245,197,24,.08);color:var(--gold);border-color:rgba(245,197,24,.2)}
.bdg-vwap{background:rgba(0,255,135,.08);color:var(--green);border-color:rgba(0,255,135,.2)}
.bdg-ema{background:rgba(155,112,255,.08);color:var(--purple);border-color:rgba(155,112,255,.2)}
.bdg-warn{background:rgba(255,140,0,.06);color:var(--orange);border-color:rgba(255,140,0,.18)}
.bdg-fail{background:rgba(255,51,85,.05);color:var(--red);border-color:rgba(255,51,85,.15)}
.srow-right{text-align:right;flex-shrink:0}
.srow-gap{font-family:'Syne',sans-serif;font-size:22px;font-weight:700;color:var(--green)}
.srow-price{font-size:10px;color:var(--muted2);margin-top:1px}
.srow-rvol{font-size:10px;color:var(--accent);font-weight:600;margin-top:2px}

/* ── HISTORY ─────────────────────────────── */
.hrow{display:grid;grid-template-columns:32px 1fr auto auto;
  gap:8px;align-items:center;padding:9px 0;border-bottom:1px solid var(--border)}
.hrow:last-child{border-bottom:none}
.hrow-icon{width:28px;height:28px;border-radius:6px;display:flex;
  align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
.hi-win{background:rgba(0,255,135,.1)}.hi-loss{background:rgba(255,51,85,.08)}
.hi-open{background:rgba(0,212,255,.08)}
.hrow-sym{font-family:'Syne',sans-serif;font-size:18px;font-weight:700;color:#fff}
.hrow-detail{font-size:9px;color:var(--muted2);margin-top:1px}
.hrow-reason{font-size:9px;color:var(--muted2);text-align:right;
  max-width:88px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hrow-pnl{font-size:14px;font-weight:700;text-align:right}
.hrow-pnl.w{color:var(--green)}.hrow-pnl.l{color:var(--red)}.hrow-pnl.o{color:var(--accent)}

/* ── ANALYTICS ───────────────────────────── */
.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px}
.mbox{background:var(--card);border:1px solid var(--border);border-radius:9px;
  padding:14px 12px;text-align:center}
.mbox-lbl{font-size:8px;color:var(--muted2);letter-spacing:.8px;text-transform:uppercase}
.mbox-val{font-family:'Syne',sans-serif;font-size:30px;font-weight:700;margin-top:4px}
.mbox-val.g{color:var(--green)}.mbox-val.r{color:var(--red)}
.mbox-val.a{color:var(--accent)}.mbox-val.o{color:var(--gold)}

.brow{display:flex;align-items:center;justify-content:space-between;
  padding:8px 0;border-bottom:1px solid var(--border)}
.brow:last-child{border-bottom:none}
.brow-lbl{font-size:11px;color:var(--text)}
.brow-right{display:flex;align-items:center;gap:8px}
.bar-bg{width:56px;height:4px;background:var(--muted);border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;background:var(--green)}
.brow-pct{font-size:11px;font-weight:600;width:36px;text-align:right}
.brow-n{font-size:9px;color:var(--muted2);width:30px;text-align:right}

/* ── HEALTH / SETTINGS ───────────────────── */
.hrow2{display:flex;align-items:center;justify-content:space-between;
  padding:10px 0;border-bottom:1px solid var(--border)}
.hrow2:last-child{border-bottom:none}
.hrow2-lbl{font-size:11px;color:var(--text)}
.hrow2-val{font-size:10px;font-weight:600}
.ok{color:var(--green)}.warn{color:var(--orange)}.err{color:var(--red)}.info{color:var(--accent)}
.pulse{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:var(--green);margin-right:5px;
  animation:pulseAnim 1.8s infinite}
@keyframes pulseAnim{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-top">
    <div>
      <div class="logo">VISION <span>WT</span></div>
      <div class="hdr-meta" id="hdrMeta">— ET · — UTC</div>
    </div>
    <div class="hdr-right">
      <span class="pill pill-paper" id="modePill">PAPER</span>
    </div>
  </div>
  <div class="stat-bar">
    <div class="s-item"><div class="s-lbl">Capital</div><div class="s-val a" id="sCapital">$—</div></div>
    <div class="s-item"><div class="s-lbl">Today P&L</div><div class="s-val" id="sPnl">$0.00</div></div>
    <div class="s-item"><div class="s-lbl">Open</div><div class="s-val o" id="sOpen">0</div></div>
    <div class="s-item"><div class="s-lbl">Win Rate</div><div class="s-val" id="sWin">—</div></div>
    <div class="s-item"><div class="s-lbl">Alerts</div><div class="s-val p" id="sAlerts">0</div></div>
    <div class="s-item"><div class="s-lbl">Market</div><div class="s-val" id="sMkt">—</div></div>
  </div>
  <div class="sweep"><div class="sweep-fill"></div></div>
</div>

<!-- TABS -->
<div class="tabs">
  <button class="tab" onclick="goTab('positions')">💼 Positions</button>
  <button class="tab" onclick="goTab('scanner')">📡 Scanner</button>
  <button class="tab" onclick="goTab('history')">📋 History</button>
  <button class="tab" onclick="goTab('analytics')">📊 Analytics</button>
  <button class="tab" onclick="goTab('settings')">⚙️ Settings</button>
</div>

<!-- POSITIONS -->
<div class="pane" id="pane-positions">
  <div id="posContent"></div>
</div>

<!-- SCANNER -->
<div class="pane" id="pane-scanner">
  <button class="scan-btn" id="scanBtn" onclick="manualScan()">
    <span id="scanIco">⟳</span> Manual Scan
  </button>
  <div id="scanContent"></div>
</div>

<!-- HISTORY -->
<div class="pane" id="pane-history">
  <div id="histContent"></div>
</div>

<!-- ANALYTICS -->
<div class="pane" id="pane-analytics">
  <div id="anlContent"></div>
</div>

<!-- SETTINGS -->
<div class="pane" id="pane-settings">
  <div class="card">
    <div class="card-head">Strategy — Ross Cameron Warrior Trading</div>
    <div class="card-body">
      <div class="hrow2"><div class="hrow2-lbl">Strategy Type</div><div class="hrow2-val info">Momentum Scalping</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Entry Pattern</div><div class="hrow2-val info">Bull Flag (1-min + 5-min)</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Price Range</div><div class="hrow2-val info">$1.00 – $20.00</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Min Gap Up</div><div class="hrow2-val info">10%+</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Min RVOL (Watchlist)</div><div class="hrow2-val info">5x</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Min RVOL (Alert)</div><div class="hrow2-val warn">10x+</div></div>
      <div class="hrow2"><div class="hrow2-lbl">News Catalyst Required</div><div class="hrow2-val ok">Yes ✓</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Bull Flag Required</div><div class="hrow2-val ok">Yes ✓</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Must Be Above VWAP</div><div class="hrow2-val ok">Yes ✓</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Short Selling</div><div class="hrow2-val err">Never</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-head">Trade Management (Ross Cameron)</div>
    <div class="card-body">
      <div class="hrow2"><div class="hrow2-lbl">Stop Loss</div><div class="hrow2-val err">-$0.20 from entry</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Target 1</div><div class="hrow2-val o">+$0.40 → Sell Half</div></div>
      <div class="hrow2"><div class="hrow2-lbl">After T1 Hit</div><div class="hrow2-val ok">Stop → Breakeven</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Target 2</div><div class="hrow2-val ok">+$0.80 → Sell Rest</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Hold Signal</div><div class="hrow2-val info">Price above 9 EMA</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Exit Signal</div><div class="hrow2-val warn">First red candle (no T1)</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Parabolic Exit</div><div class="hrow2-val warn">Extension bar → sell into strength</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Order Type</div><div class="hrow2-val info">Limit only (no market)</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-head">System Health</div>
    <div class="card-body">
      <div class="hrow2"><div class="hrow2-lbl"><span class="pulse"></span>Bot Status</div><div class="hrow2-val ok" id="hStatus">Online</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Scan Interval</div><div class="hrow2-val info">Every 15 seconds</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Pre-Market Scan</div><div class="hrow2-val info">6:30–9:30 AM ET</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Alert Window</div><div class="hrow2-val info">9:30–11:30 AM ET</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Max Positions</div><div class="hrow2-val info">2 concurrent</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Data: Alpaca</div><div class="hrow2-val ok" id="hAlpaca">Connected</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Data: Finnhub</div><div class="hrow2-val ok" id="hFinnhub">Connected</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Database</div><div class="hrow2-val" id="hDb">—</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Webull Paper</div><div class="hrow2-val" id="hWebull">Disabled</div></div>
      <div class="hrow2"><div class="hrow2-lbl">Last Updated</div><div class="hrow2-val info" id="hUpdated">—</div></div>
    </div>
  </div>
</div>

<script>
// ── Globals ───────────────────────────────
let activeTab   = 'positions';
let scanData    = [];
let posData     = [];
let histData    = [];
let anlData     = {};
let alertCount  = 0;
let scanBusy    = false;

// ── Clock ─────────────────────────────────
function tick() {
  const now  = new Date();
  const et   = now.toLocaleString('en-US',{timeZone:'America/New_York',
    hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  const utc  = now.toUTCString().slice(17,25);
  document.getElementById('hdrMeta').textContent = et + ' ET · ' + utc + ' UTC';

  const h = parseInt(et.split(':')[0]);
  const m = parseInt(et.split(':')[1]);
  const dow = now.toLocaleString('en-US',{timeZone:'America/New_York',weekday:'short'});
  const isWeekend = dow === 'Sat' || dow === 'Sun';
  const inPrime   = !isWeekend && ((h===9&&m>=30)||h===10||(h===11&&m<=30));
  const inPre     = !isWeekend && ((h===6&&m>=30)||h===7||h===8||(h===9&&m<30));
  const el = document.getElementById('sMkt');
  if (isWeekend)    { el.textContent='CLOSED'; el.className='s-val'; }
  else if (inPrime) { el.textContent='PRIME ▲'; el.className='s-val g'; }
  else if (inPre)   { el.textContent='PRE-MKT'; el.className='s-val o'; }
  else              { el.textContent='CLOSED'; el.className='s-val'; }
}
setInterval(tick,1000); tick();

// ── Tab nav ───────────────────────────────
function goTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab').forEach((t,i)=>{
    t.classList.toggle('on',['positions','scanner','history','analytics','settings'][i]===name);
  });
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('on'));
  const p = document.getElementById('pane-'+name);
  if(p) p.classList.add('on');
  if(name==='scanner')   renderScanner();
  if(name==='history')   renderHistory();
  if(name==='analytics') renderAnalytics();
}

// ── Fetch ─────────────────────────────────
async function fx(url){
  try{const r=await fetch(url);return await r.json();}catch{return null;}
}

// ── Manual scan ───────────────────────────
async function manualScan(){
  if(scanBusy)return;
  scanBusy=true;
  const btn=document.getElementById('scanBtn');
  const ico=document.getElementById('scanIco');
  btn.style.opacity='.5';
  ico.textContent='⟳'; ico.className='spin';
  document.getElementById('scanContent').innerHTML=
    '<div class="empty"><div class="empty-icon">📡</div><div class="empty-txt">Scanning... takes ~15 seconds</div></div>';
  await fx('/api/test_scan');
  await refreshAll();
  scanBusy=false;
  btn.style.opacity='1';
  ico.className=''; ico.textContent='⟳';
}

// ── Main refresh ──────────────────────────
async function refreshAll(){
  // Status + top10
  const [status, top10, trades, analytics] = await Promise.all([
    fx('/api/status'), fx('/api/top10'), fx('/api/trades'), fx('/api/analytics')
  ]);

  if(top10 && top10.candidates) scanData = top10.candidates;
  if(trades && Array.isArray(trades)) histData = trades;
  if(analytics && analytics.overall) anlData = analytics;

  // Positions from paper engine via engine state
  posData = (top10 && top10.positions) ? top10.positions : [];

  updateHeader(analytics, trades);
  if(activeTab==='positions')   renderPositions();
  if(activeTab==='scanner')     renderScanner();
  if(activeTab==='history')     renderHistory();
  if(activeTab==='analytics')   renderAnalytics();
  document.getElementById('hUpdated').textContent = new Date().toLocaleTimeString('en-US',{timeZone:'America/New_York'});

  if(status){
    document.getElementById('hStatus').textContent = status.engine_active ? 'Online' : 'Stopped';
    document.getElementById('hStatus').className   = 'hrow2-val ' + (status.engine_active ? 'ok' : 'err');
    const dbOk = status.db_connected !== false;
    document.getElementById('hDb').textContent  = dbOk ? 'Connected' : 'SQLite (no Supabase)';
    document.getElementById('hDb').className    = 'hrow2-val ' + (dbOk ? 'ok' : 'warn');
    const wb = status.webull_enabled ? 'Enabled' : 'Disabled (ENABLE_WEBULL=false)';
    document.getElementById('hWebull').textContent = wb;
    document.getElementById('hWebull').className   = 'hrow2-val ' + (status.webull_enabled ? 'ok' : 'warn');
  }
}

function updateHeader(analytics, trades){
  // P&L
  const pnl = (analytics && analytics.overall) ? (analytics.overall.total_pnl||0) : 0;
  const pnlEl = document.getElementById('sPnl');
  pnlEl.textContent = (pnl>=0?'+':'') + '$' + Math.abs(pnl).toFixed(2);
  pnlEl.className   = 's-val ' + (pnl>0?'g':pnl<0?'r':'');

  // Win rate
  const wr = (analytics && analytics.overall && analytics.overall.win_rate);
  const wrEl = document.getElementById('sWin');
  if(wr !== null && wr !== undefined){
    wrEl.textContent = wr.toFixed(1)+'%';
    wrEl.className   = 's-val ' + (wr>=55?'g':wr>=45?'o':'r');
  }

  // Open positions
  const openCount = posData.length;
  document.getElementById('sOpen').textContent = openCount;

  // Alert count (trades that fired today)
  if(trades && Array.isArray(trades)){
    const today = new Date().toISOString().slice(0,10);
    const todayAlerts = trades.filter(t=>t.entry_time && t.entry_time.startsWith(today));
    document.getElementById('sAlerts').textContent = todayAlerts.length;
  }
}

// ── Render: Positions ─────────────────────
function renderPositions(){
  const el = document.getElementById('posContent');

  // Get open positions from paper engine (in-memory via /api/positions)
  if(!posData || posData.length===0){
    el.innerHTML=`<div class="empty">
      <div class="empty-icon">💼</div>
      <div class="empty-txt">No open paper positions right now.<br><br>
      VISION enters trades when a stock has:<br>
      🚩 Bull Flag confirmed<br>
      📰 News catalyst today<br>
      📊 RVOL 10x or higher<br>
      📈 Price above VWAP<br><br>
      Alerts fire 9:30–11:30 AM ET</div>
    </div>`;
    return;
  }
  let html='';
  posData.forEach(pos=>{
    const entry   = parseFloat(pos.entry_price||0);
    const curr    = parseFloat(pos.current_price||entry);
    const stop    = parseFloat(pos.stop_loss||0);
    const t1      = parseFloat(pos.target1||0);
    const t2      = parseFloat(pos.target2||0);
    const shares  = pos.shares||100;
    const unreal  = (curr - entry) * shares;
    const pnlCls  = unreal>0?'g':unreal<0?'r':'n';
    const pnlSign = unreal>=0?'+':'';
    const prog    = t1>entry ? Math.min(100,Math.max(0,((curr-entry)/(t1-entry))*100)) : 0;
    const state   = pos.state==='HALF_OUT' ? 'HALF OUT — Sold 50%, stop at breakeven' : 'OPEN — Watching for T1 or stop';
    html+=`<div class="pos-card">
      <div class="pos-hdr">
        <div>
          <div class="pos-sym">$${pos.symbol}</div>
          <div class="pos-state">${state}</div>
        </div>
        <div class="pos-pnl ${pnlCls}">${pnlSign}$${Math.abs(unreal).toFixed(2)}</div>
      </div>
      <div class="pos-body">
        <div class="levels">
          <div class="lv lv-stop"><div class="lv-lbl">Stop</div><div class="lv-val">$${stop.toFixed(2)}</div></div>
          <div class="lv lv-t1"><div class="lv-lbl">T1 (sell ½)</div><div class="lv-val">$${t1.toFixed(2)}</div></div>
          <div class="lv lv-t2"><div class="lv-lbl">T2 (sell ½)</div><div class="lv-val">$${t2.toFixed(2)}</div></div>
        </div>
        <div class="prog-wrap">
          <div class="prog-lbls">
            <span>Entry $${entry.toFixed(2)}</span>
            <span>${prog.toFixed(0)}% to T1</span>
          </div>
          <div class="prog-bg"><div class="prog-fill" style="width:${prog}%"></div></div>
        </div>
        <div class="pos-tags">
          <span class="tag">Now $${curr.toFixed(2)}</span>
          <span class="tag">${shares} shares</span>
          ${pos.rvol?`<span class="tag">RVOL ${pos.rvol}x</span>`:''}
          ${pos.entry_time?`<span class="tag">${String(pos.entry_time).slice(11,16)} ET</span>`:''}
        </div>
      </div>
    </div>`;
  });
  el.innerHTML=html;
}

// ── Render: Scanner ───────────────────────
function renderScanner(){
  const el = document.getElementById('scanContent');
  if(!scanData||scanData.length===0){
    el.innerHTML=`<div class="empty">
      <div class="empty-icon">📡</div>
      <div class="empty-txt">Scanner builds watchlist from 6:30 AM ET.<br>
      Use Manual Scan to trigger immediately.<br><br>
      Alert-ready stocks require:<br>
      🚩 Bull Flag + 📰 News + 📊 RVOL 10x+ + 📈 Above VWAP</div>
    </div>`;
    return;
  }
  const ready = scanData.filter(s=>s.alert_ready);
  const watch = scanData.filter(s=>!s.alert_ready);
  let html='';
  if(ready.length){
    html+=`<div class="sec-lbl"><div class="dot dot-g"></div>Alert Ready — Ross Cameron Setups (${ready.length})</div>`;
    html+=`<div class="card"><div class="card-body">`;
    ready.forEach(s=>{ html+=buildSrow(s,true); });
    html+=`</div></div>`;
  }
  if(watch.length){
    html+=`<div class="sec-lbl" style="margin-top:${ready.length?'8px':'0'}"><div class="dot dot-m"></div>Watchlist — Needs More Confirmation (${watch.length})</div>`;
    html+=`<div class="card"><div class="card-body">`;
    watch.forEach(s=>{ html+=buildSrow(s,false); });
    html+=`</div></div>`;
  }
  el.innerHTML=html;
}

function buildSrow(s, ready){
  const b=[];
  if(s.bull_flag)   b.push(`<span class="bdg bdg-bf">🚩 Bull Flag</span>`);
  else              b.push(`<span class="bdg bdg-fail">No Flag</span>`);
  if(s.has_news)    b.push(`<span class="bdg bdg-news">📰 News</span>`);
  else              b.push(`<span class="bdg bdg-fail">No News</span>`);
  if(s.above_vwap)  b.push(`<span class="bdg bdg-vwap">↑ VWAP</span>`);
  else              b.push(`<span class="bdg bdg-warn">↓ VWAP</span>`);
  if(s.above_ema9)  b.push(`<span class="bdg bdg-ema">↑ 9 EMA</span>`);
  if(s.float&&s.float>0&&s.float<10) b.push(`<span class="bdg bdg-vwap">Float ${s.float}M</span>`);
  return `<div class="srow ${ready?'ready':'watch'}">
    <div class="srow-left">
      <div class="srow-sym">$${s.symbol}</div>
      <div class="srow-badges">${b.join('')}</div>
    </div>
    <div class="srow-right">
      <div class="srow-gap">+${s.pct_change}%</div>
      <div class="srow-price">$${s.price}</div>
      <div class="srow-rvol">${s.rvol}x RVOL</div>
    </div>
  </div>`;
}

// ── Render: History ───────────────────────
function renderHistory(){
  const el = document.getElementById('histContent');
  if(!histData||histData.length===0){
    el.innerHTML=`<div class="empty"><div class="empty-icon">📋</div>
    <div class="empty-txt">Trade history builds as paper positions close.</div></div>`;
    return;
  }
  let html='<div class="card"><div class="card-body">';
  histData.slice(0,60).forEach(t=>{
    const won  = t.result==='win';
    const open = !t.result;
    const pnl  = parseFloat(t.pnl_usd||0);
    const pnlTxt = open?'Open':(pnl>=0?`+$${pnl.toFixed(2)}`:`-$${Math.abs(pnl).toFixed(2)}`);
    const ico  = open?'🔷':won?'✅':'❌';
    const iCls = open?'hi-open':won?'hi-win':'hi-loss';
    const pCls = open?'o':won?'w':'l';
    const et   = t.entry_time ? String(t.entry_time).slice(11,16) : '—';
    html+=`<div class="hrow">
      <div class="hrow-icon ${iCls}">${ico}</div>
      <div>
        <div class="hrow-sym">$${t.symbol}</div>
        <div class="hrow-detail">$${parseFloat(t.entry_price||0).toFixed(2)} entry · ${et} · ${t.shares||0} sh</div>
      </div>
      <div class="hrow-reason">${t.exit_reason||(open?'Open':'—')}</div>
      <div class="hrow-pnl ${pCls}">${pnlTxt}</div>
    </div>`;
  });
  html+='</div></div>';
  el.innerHTML=html;
}

// ── Render: Analytics ─────────────────────
function renderAnalytics(){
  const el = document.getElementById('anlContent');
  const ov = anlData.overall;
  if(!ov||!anlData.completed_trades){
    el.innerHTML=`<div class="empty"><div class="empty-icon">📊</div>
    <div class="empty-txt">Analytics appear after first trades complete.<br><br>
    You'll see:<br>
    • Win rate by bull flag vs no flag<br>
    • Win rate by RVOL band<br>
    • Best trading hours<br>
    • News catalyst impact<br>
    • P&L and drawdown</div></div>`;
    return;
  }
  const wr   = ov.win_rate||0;
  const pnl  = ov.total_pnl||0;
  const ev   = ov.avg_pnl||0;
  const sh   = anlData.sharpe;
  const dd   = anlData.drawdown||{};
  const wrCls = wr>=55?'g':wr>=45?'o':'r';
  const pnlCls = pnl>=0?'g':'r';

  let html=`<div class="metric-grid">
    <div class="mbox"><div class="mbox-lbl">Win Rate</div><div class="mbox-val ${wrCls}">${wr.toFixed(1)}%</div></div>
    <div class="mbox"><div class="mbox-lbl">Total P&L</div><div class="mbox-val ${pnlCls}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(2)}</div></div>
    <div class="mbox"><div class="mbox-lbl">EV / Trade</div><div class="mbox-val ${ev>=0?'g':'r'}">${ev>=0?'+':''}$${Math.abs(ev).toFixed(2)}</div></div>
    <div class="mbox"><div class="mbox-lbl">Trades</div><div class="mbox-val a">${anlData.completed_trades}</div></div>
  </div>`;

  if(sh!==null&&sh!==undefined)
    html+=`<div class="card"><div class="card-body">
      <div class="brow"><div class="brow-lbl">Sharpe Ratio</div><div class="brow-pct ${sh>=1?'g':sh>=0?'o':'r'}">${sh.toFixed(2)}</div></div>
      <div class="brow"><div class="brow-lbl">Max Drawdown</div><div class="brow-pct err">$${(dd.usd||0).toFixed(2)}</div></div>
    </div></div>`;

  function breakdownCard(title, data){
    if(!data||!Object.keys(data).length) return '';
    let h=`<div class="card"><div class="card-head">${title}</div><div class="card-body">`;
    Object.entries(data).forEach(([k,v])=>{
      const wr2=v.win_rate||0;
      const col=wr2>=55?'var(--green)':wr2>=45?'var(--gold)':'var(--red)';
      h+=`<div class="brow">
        <div class="brow-lbl">${k}</div>
        <div class="brow-right">
          <div class="bar-bg"><div class="bar-fill" style="width:${wr2}%;background:${col}"></div></div>
          <div class="brow-pct" style="color:${col}">${wr2.toFixed(0)}%</div>
          <div class="brow-n">${v.n} tr</div>
        </div>
      </div>`;
    });
    return h+'</div></div>';
  }

  html += breakdownCard('Bull Flag vs No Flag', anlData.by_bull_flag);
  html += breakdownCard('RVOL Band', anlData.by_rvol);
  html += breakdownCard('News Catalyst', anlData.by_news);
  html += breakdownCard('VWAP Position', anlData.by_vwap);
  html += breakdownCard('By Hour (ET)', anlData.by_hour);
  html += breakdownCard('Exit Reason', anlData.by_exit);

  el.innerHTML=html;
}

// ── Boot ──────────────────────────────────
goTab('positions');
refreshAll();
setInterval(refreshAll, 15000);
</script>
</body>
</html>"""


@app.route('/')
def home():
    return render_template_string(DASHBOARD_HTML)

@app.route('/api/status')
def status():
    webull_enabled = os.environ.get("ENABLE_WEBULL","false").lower() == "true"
    db_connected   = bool(os.environ.get("VISION_DATABASE_URL",""))
    return jsonify({
        "status":          "online",
        "engine_active":   engine.scheduler.running if engine.scheduler else False,
        "webull_enabled":  webull_enabled,
        "db_connected":    db_connected,
        "uptime":          datetime.now().isoformat(),
    })

@app.route('/api/top10')
def get_top10():
    # Build positions list from paper engine
    positions = []
    try:
        for sym, pos in engine.paper_engine.positions.items():
            if pos.state != "CLOSED":
                positions.append({
                    "symbol":        sym,
                    "entry_price":   pos.entry_price,
                    "stop_loss":     pos.stop_loss,
                    "target1":       pos.target1,
                    "target2":       pos.target2,
                    "shares":        pos.shares,
                    "state":         pos.state,
                    "entry_time":    pos.entry_time.isoformat() if pos.entry_time else None,
                    "rvol":          getattr(pos, 'rvol', 0),
                })
    except Exception:
        pass
    return jsonify({
        "candidates": engine.top_candidates if hasattr(engine,'top_candidates') else [],
        "positions":  positions,
    })

@app.route('/api/test_scan')
def test_scan():
    try:
        engine.hunt_momentum()
        count = len(engine.top_candidates)
        return jsonify({"status":"success","message":f"Scan complete. {count} candidates found."})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)})

@app.route('/api/trades')
def api_trades():
    try:
        from database import get_all_trades
        return jsonify(get_all_trades())
    except Exception as e:
        return jsonify([])

@app.route('/api/analytics')
def api_analytics():
    try:
        from database import get_all_trades_cached
        from analytics import compute_analytics, summary_text
        trades = get_all_trades_cached()
        report = compute_analytics(trades)
        report["summary"] = summary_text(report)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
