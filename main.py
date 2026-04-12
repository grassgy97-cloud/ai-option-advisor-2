from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api.advisor_v2 import router as advisor_router
from app.api.covered_call_api import router as covered_call_router
from app.api.scanner_v2 import router as scanner_router

app = FastAPI(title="AI Option Advisor")

app.include_router(advisor_router)
app.include_router(scanner_router)
app.include_router(covered_call_router)


@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_PAGE


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Option Advisor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0f1117;
    color: #e0e0e0;
    min-height: 100vh;
    padding: 24px;
  }
  h1 {
    font-size: 20px;
    font-weight: 600;
    color: #fff;
    margin-bottom: 20px;
    letter-spacing: 0.3px;
  }
  .input-row {
    display: flex;
    gap: 10px;
    margin-bottom: 8px;
    align-items: flex-start;
  }
  .underlying-panel {
    background: #1e2130;
    border: 1px solid #2e3250;
    border-radius: 8px;
    padding: 8px 10px;
    min-width: 170px;
    max-width: 190px;
    flex-shrink: 0;
  }
  .underlying-panel .panel-title {
    font-size: 11px;
    color: #6b7280;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .underlying-panel .panel-title button {
    height: auto;
    padding: 0;
    background: transparent;
    color: #4a6cf7;
    font-size: 11px;
    border: none;
    cursor: pointer;
  }
  .underlying-panel .panel-title button:hover {
    background: transparent;
    text-decoration: underline;
  }
  .underlying-panel label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: #a0a8c0;
    padding: 3px 0;
    cursor: pointer;
    white-space: nowrap;
  }
  .underlying-panel label:hover { color: #e0e0e0; }
  .underlying-panel input[type="checkbox"] {
    accent-color: #4a6cf7;
    width: 13px;
    height: 13px;
    cursor: pointer;
  }
  .underlying-panel .divider {
    border: none;
    border-top: 1px solid #2e3250;
    margin: 6px 0;
  }
  .text-area-wrap {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  textarea {
    width: 100%;
    background: #1e2130;
    border: 1px solid #2e3250;
    color: #e0e0e0;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 14px;
    resize: vertical;
    min-height: 84px;
    line-height: 1.6;
    font-family: inherit;
  }
  textarea:focus {
    outline: none;
    border-color: #4a6cf7;
  }
  .textarea-hint {
    font-size: 11px;
    color: #4b5568;
    line-height: 1.5;
    padding: 0 2px;
  }
  .button-col {
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-self: flex-start;
  }
  button {
    background: #4a6cf7;
    color: #fff;
    border: none;
    padding: 0 22px;
    height: 44px;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    white-space: nowrap;
    transition: background 0.15s;
    align-self: flex-start;
  }
  button:hover { background: #3a5ce7; }
  button:disabled { background: #2e3250; color: #666; cursor: not-allowed; }
  #btn-covered { background: #1f8f5f; }
  #btn-covered:hover { background: #18724c; }

  .cc-panel {
    background: #1a1d2e;
    border: 1px solid #2e3250;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 16px;
    display: none;
    flex-wrap: wrap;
    gap: 12px;
    align-items: flex-end;
  }
  .cc-panel.visible { display: flex; }
  .cc-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .cc-item label {
    font-size: 11px;
    color: #6b7280;
  }
  .cc-item input {
    width: 110px;
    padding: 8px;
    border-radius: 6px;
    border: 1px solid #2e3250;
    background: #0f1117;
    color: #e0e0e0;
  }
  .cc-item.wide input { width: 140px; }
  .cc-note {
    font-size: 11px;
    color: #6b7280;
    max-width: 420px;
    line-height: 1.5;
  }

  .shortcuts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 20px;
  }
  .shortcut {
    background: #1e2130;
    border: 1px solid #2e3250;
    color: #a0a8c0;
    padding: 5px 12px;
    border-radius: 20px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .shortcut:hover {
    border-color: #4a6cf7;
    color: #fff;
  }

  #status {
    font-size: 13px;
    color: #6b7280;
    margin-bottom: 16px;
    min-height: 18px;
  }
  #status.loading { color: #4a6cf7; }
  #status.error { color: #ef4444; }

  #intent-bar {
    background: #1a1d2e;
    border: 1px solid #2e3250;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12px;
    color: #8892b0;
    margin-bottom: 16px;
    display: none;
    flex-wrap: wrap;
    gap: 12px;
  }
  .badge {
    background: #0f1117;
    border: 1px solid #2e3250;
    border-radius: 5px;
    padding: 2px 8px;
    color: #a0b0d0;
  }
  .presentation-note {
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 12px;
    font-size: 13px;
    line-height: 1.6;
    display: none;
  }
  .presentation-note.reference {
    background: #1f2230;
    border: 1px solid #4a556f;
    color: #d6d9e5;
  }
  .presentation-note.weak {
    background: #2a1b1b;
    border: 1px solid #6b2c2c;
    color: #f5c2c2;
  }
  #narrative {
    background: #1a1d2e;
    border: 1px solid #2e3250;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 14px;
    line-height: 1.7;
    margin-bottom: 20px;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-wrap: anywhere;
    overflow: visible;
    max-height: none;
    display: none;
  }
  .table-wrap {
    overflow-x: auto;
    margin-bottom: 20px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    background: #1a1d2e;
    color: #8892b0;
    font-weight: 500;
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid #2e3250;
    white-space: nowrap;
  }
  td {
    padding: 10px 14px;
    border-bottom: 1px solid #1e2130;
    vertical-align: top;
  }
  tr:hover td { background: #1a1d2e; }
  .rank-1 td:first-child { color: #fbbf24; font-weight: 600; }
  .score-high { color: #34d399; }
  .score-mid { color: #fbbf24; }
  .score-low { color: #f87171; }
  .legs-cell {
    font-size: 11px;
    color: #8892b0;
    max-width: 300px;
    line-height: 1.6;
  }
  .flag {
    display: inline-block;
    background: #2d1f1f;
    color: #f87171;
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 4px;
    margin: 1px;
  }
  .score-label { font-size: 10px; letter-spacing: 1px; }
  .profit-cond { font-size: 11px; color: #6ee7b7; line-height: 1.5; }
  .greeks {
    display: inline-flex;
    gap: 10px;
    font-size: 11px;
    color: #6b7280;
  }
  .greeks span { white-space: nowrap; }

  @media (max-width: 960px) {
    body { padding: 16px; }
    .input-row {
      flex-direction: column;
    }
    .underlying-panel,
    .button-col {
      width: 100%;
      max-width: none;
    }
    .button-col {
      flex-direction: row;
      flex-wrap: wrap;
    }
  }
</style>
</head>
<body>

<h1>AI Option Advisor</h1>

<div class="input-row">
  <div class="underlying-panel">
    <div class="panel-title">
      <span>选择标的</span>
      <span>
        <button type="button" onclick="selectAll()">全选</button>
        /
        <button type="button" onclick="clearAll()">清空</button>
      </span>
    </div>
    <label><input type="checkbox" value="ALL" id="chk-ALL" onchange="toggleAll(this)"> 全部扫描</label>
    <hr class="divider">
    <label><input type="checkbox" value="510300" checked> 510300 沪深300</label>
    <label><input type="checkbox" value="510050"> 510050 上证50</label>
    <label><input type="checkbox" value="510500"> 510500 中证500</label>
    <label><input type="checkbox" value="588000"> 588000 科创50华夏</label>
    <label><input type="checkbox" value="588080"> 588080 科创50易方达</label>
    <label><input type="checkbox" value="159915"> 159915 创业板</label>
    <label><input type="checkbox" value="159901"> 159901 深证100</label>
    <label><input type="checkbox" value="159919"> 159919 沪深300深</label>
    <label><input type="checkbox" value="159922"> 159922 中证500深</label>
  </div>

  <div class="text-area-wrap">
    <textarea id="text" rows="3" placeholder="输入你的市场观点，例如：
看多但希望控制风险；
IV 偏高，想收 theta；
下方 8% 有支撑、上方 5% 有压力。"></textarea>
    <div class="textarea-hint">
      提示：价格位置尽量用相对百分比表达（如 -8%、+5%），并可同时描述方向、波动预期、支撑/压力位、Greek 偏好。
    </div>
  </div>

  <div class="button-col">
    <button id="btn" type="button" onclick="runAdvisor()">分析</button>
    <button id="btn-covered" type="button" onclick="runCoveredCall()">备兑扫描</button>
  </div>
</div>

<div id="cc-panel" class="cc-panel">
  <div class="cc-item">
    <label for="cc-hands">持仓手数</label>
    <input id="cc-hands" type="number" value="2" min="1">
  </div>
  <div class="cc-item">
    <label for="cc-dte-min">最短 DTE</label>
    <input id="cc-dte-min" type="number" value="60" min="1">
  </div>
  <div class="cc-item">
    <label for="cc-dte-max">最长 DTE</label>
    <input id="cc-dte-max" type="number" value="180" min="1">
  </div>
  <div class="cc-item">
    <label for="cc-delta-target">目标 Delta</label>
    <input id="cc-delta-target" type="number" value="0.20" step="0.01" min="0.01" max="0.99">
  </div>
  <div class="cc-item">
    <label for="cc-delta-tolerance">Delta 容差</label>
    <input id="cc-delta-tolerance" type="number" value="0.12" step="0.01" min="0.01" max="0.50">
  </div>
  <div class="cc-item">
    <label for="cc-short-dte-max">近期限截止 DTE</label>
    <input id="cc-short-dte-max" type="number" value="120" min="1">
  </div>
  <div class="cc-item wide">
    <label for="cc-short-buffer">近期限目标上行保护</label>
    <input id="cc-short-buffer" type="number" value="0.08" step="0.01" min="0" max="1">
  </div>
  <div class="cc-item wide">
    <label for="cc-long-buffer">远期限目标上行保护</label>
    <input id="cc-long-buffer" type="number" value="0.10" step="0.01" min="0" max="1">
  </div>
  <div class="cc-item">
    <label for="cc-top-n">Top N</label>
    <input id="cc-top-n" type="number" value="5" min="1" max="20">
  </div>
  <div class="cc-note">
    说明：例如“近期限截止 DTE=120，近期限上行保护=8%，远期限上行保护=10%”表示
    120 天以内按 8% 缓冲，更长期按 10% 缓冲筛选备兑 call。
  </div>
</div>

<div class="shortcuts">
  <span class="shortcut" onclick="setShortcut('近月认购偏贵，想做低风险跨期组合')">近月认购偏贵</span>
  <span class="shortcut" onclick="setShortcut('我觉得 300 近期会涨，想做方向性策略')">看多方向</span>
  <span class="shortcut" onclick="setShortcut('我持有 300ETF 现货，想做备兑增强')">备兑增收</span>
  <span class="shortcut" onclick="setShortcut('整体波动率偏高，想收 theta')">IV 偏高卖方</span>
  <span class="shortcut" onclick="setShortcut('双向波动可能变大，但下行空间更多，下方 8% 有支撑')">非对称波动</span>
  <span class="shortcut" onclick="setShortcut('偏空，下行空间大，上方 5% 有压力')">偏空压力位</span>
</div>

<div id="status"></div>
<div id="intent-bar"></div>
<div id="presentation-note" class="presentation-note"></div>
<div id="narrative"></div>

<div class="table-wrap">
  <table id="result-table" style="display:none">
    <thead>
      <tr>
        <th>#</th>
        <th>标的</th>
        <th>策略</th>
        <th>评分</th>
        <th>成本/收入</th>
        <th>Greeks</th>
        <th>IV分位(ATM/C/P)</th>
        <th>腿</th>
        <th>盈利条件</th>
        <th>风险</th>
      </tr>
    </thead>
    <tbody id="result-body"></tbody>
  </table>
</div>

<script>
let coveredCallMode = false;

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function setShortcut(text) {
  document.getElementById('text').value = text;
}

function isExplicitCoveredCallMode() {
  const params = new URLSearchParams(window.location.search);
  const mode = (params.get('mode') || '').toLowerCase();
  return mode === 'covered-call' || mode === 'covered_call';
}

function setCoveredCallMode(enabled) {
  coveredCallMode = !!enabled;
  const panel = document.getElementById('cc-panel');
  const coveredBtn = document.getElementById('btn-covered');
  if (panel) panel.classList.toggle('visible', coveredCallMode);
  if (coveredBtn) coveredBtn.textContent = coveredCallMode ? '运行备兑扫描' : '备兑扫描';
}

function getSelectedUnderlyings() {
  const allChk = document.getElementById('chk-ALL');
  if (allChk.checked) return ['ALL'];
  const checks = document.querySelectorAll('.underlying-panel input[type="checkbox"]:not(#chk-ALL):checked');
  const values = Array.from(checks).map(c => c.value);
  return values.length ? values : ['510300'];
}

function toggleAll(el) {
  const others = document.querySelectorAll('.underlying-panel input[type="checkbox"]:not(#chk-ALL)');
  others.forEach(c => {
    c.checked = false;
    c.disabled = el.checked;
  });
}

function selectAll() {
  document.getElementById('chk-ALL').checked = false;
  const others = document.querySelectorAll('.underlying-panel input[type="checkbox"]:not(#chk-ALL)');
  others.forEach(c => {
    c.checked = true;
    c.disabled = false;
  });
}

function clearAll() {
  document.getElementById('chk-ALL').checked = false;
  const others = document.querySelectorAll('.underlying-panel input[type="checkbox"]:not(#chk-ALL)');
  others.forEach(c => {
    c.checked = false;
    c.disabled = false;
  });
  const defaultBox = document.querySelector('input[value="510300"]');
  if (defaultBox) defaultBox.checked = true;
}

function clearResultArea() {
  const presentationNote = document.getElementById('presentation-note');
  presentationNote.style.display = 'none';
  presentationNote.className = 'presentation-note';
  presentationNote.textContent = '';
  document.getElementById('narrative').style.display = 'none';
  document.getElementById('narrative').textContent = '';
  document.getElementById('result-table').style.display = 'none';
  document.getElementById('intent-bar').style.display = 'none';
  document.getElementById('intent-bar').innerHTML = '';
  document.getElementById('result-body').innerHTML = '';
}

async function runAdvisor() {
  setCoveredCallMode(false);
  const text = document.getElementById('text').value.trim();
  if (!text) return;

  const selectedIds = getSelectedUnderlyings();
  const isAll = selectedIds.includes('ALL');
  const underlyingId = isAll ? 'ALL' : selectedIds[0];

  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.className = 'loading';
  status.textContent = isAll
    ? '全量分析中，预计需要 30-120 秒…'
    : selectedIds.length > 1
      ? `分析 ${selectedIds.length} 个标的中，预计需要 20-40 秒…`
      : '分析中…';

  clearResultArea();

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 200000);

  try {
    const payload = isAll
      ? { text, underlying_id: 'ALL' }
      : selectedIds.length > 1
        ? { text, underlying_ids: selectedIds }
        : { text, underlying_id: underlyingId };

    console.log('[frontend_multi_check] selectedIds=', selectedIds);
    console.log('[frontend_multi_check] payload=', payload);

    const resp = await fetch('/advisor/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const raw = await resp.json();
    const result = raw.data || raw;
    renderResult(result);
    status.className = '';
    status.textContent = '';
  } catch (e) {
    status.className = 'error';
    status.textContent = '请求失败：' + e.message;
  } finally {
    clearTimeout(timeoutId);
    btn.disabled = false;
  }
}

async function runCoveredCall() {
  if (!coveredCallMode) {
    setCoveredCallMode(true);
    const status = document.getElementById('status');
    status.className = '';
    status.textContent = '已展开备兑参数，请确认后再次点击“运行备兑扫描”。';
    const firstInput = document.getElementById('cc-hands');
    if (firstInput) firstInput.focus();
    return;
  }

  const selectedIds = getSelectedUnderlyings();
  const isAll = selectedIds.includes('ALL');
  const underlyingId = isAll ? '510300' : selectedIds[0];

  const hands = Number(document.getElementById('cc-hands').value || 2);
  const dteMin = Number(document.getElementById('cc-dte-min').value || 60);
  const dteMax = Number(document.getElementById('cc-dte-max').value || 180);
  const deltaTarget = Number(document.getElementById('cc-delta-target').value || 0.20);
  const deltaTolerance = Number(document.getElementById('cc-delta-tolerance').value || 0.12);
  const shortDteMax = Number(document.getElementById('cc-short-dte-max').value || 120);
  const shortBuffer = Number(document.getElementById('cc-short-buffer').value || 0.08);
  const longBuffer = Number(document.getElementById('cc-long-buffer').value || 0.10);
  const topN = Number(document.getElementById('cc-top-n').value || 5);

  if (!Number.isFinite(hands) || hands <= 0) {
    alert('手数输入无效');
    return;
  }
  if (dteMin <= 0 || dteMax < dteMin) {
    alert('DTE 范围输入无效');
    return;
  }
  if (shortDteMax < dteMin) {
    alert('近期限截止 DTE 不能小于最短 DTE');
    return;
  }

  const btn = document.getElementById('btn-covered');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.className = 'loading';
  status.textContent = '备兑扫描中…';

  clearResultArea();

  try {
    const resp = await fetch('/advisor/covered-call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        underlying_id: underlyingId,
        hands,
        dte_min: dteMin,
        dte_max: dteMax,
        delta_target: deltaTarget,
        delta_tolerance: deltaTolerance,
        max_rel_spread: 0.05,
        fee_per_share: 0.0004,
        top_n: topN,
        target_upside_rules: [
          { dte_max: shortDteMax, target_upside_buffer: shortBuffer },
          { dte_max: 9999, target_upside_buffer: longBuffer },
        ],
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const payload = await resp.json();
    renderCoveredCallResult(payload.data);
    status.className = '';
    status.textContent = '';
  } catch (e) {
    status.className = 'error';
    status.textContent = '请求失败：' + e.message;
  } finally {
    btn.disabled = false;
  }
}

function renderIntentBar(intent) {
  const intentBar = document.getElementById('intent-bar');
  const gp = intent.greeks_preference || {};
  const gpStr = Object.entries(gp).map(([k, v]) => {
    const sign = v.sign === 'positive' ? '↑' : v.sign === 'negative' ? '↓' : '~';
    return `${k}:${sign}(${v.strength})`;
  }).join(' ');
  const pl = intent.price_levels || {};
  const plStr = Object.entries(pl).map(([k, v]) => `${k}:${(v * 100).toFixed(1)}%`).join(' ');
  const underlyings = intent.underlying_ids || [intent.underlying_id].filter(Boolean);

  intentBar.innerHTML = `
    <span>解析：</span>
    <span class="badge">market: ${escapeHtml(intent.market_view || '-')}</span>
    <span class="badge">vol: ${escapeHtml(intent.vol_view || '-')}</span>
    <span class="badge">标的: ${escapeHtml(underlyings.join(', '))}</span>
    <span class="badge">DTE: ${escapeHtml(intent.dte_min)}-${escapeHtml(intent.dte_max)}天</span>
    ${gpStr ? `<span class="badge">Greeks: ${escapeHtml(gpStr)}</span>` : ''}
    ${plStr ? `<span class="badge">价位: ${escapeHtml(plStr)}</span>` : ''}
    ${intent.asymmetry ? `<span class="badge">偏态: ${escapeHtml(intent.asymmetry)}</span>` : ''}
  `;
  intentBar.style.display = 'flex';
}

function renderResult(data) {
  const intent = data.parsed_intent || {};
  renderIntentBar(intent);

  const briefing = data.briefing || {};
  const presentation = briefing.presentation || {};
  const presentationNoteEl = document.getElementById('presentation-note');
  if (presentation.note) {
    const tierClass = presentation.overall_tier === 'weak_match_watchlist'
      ? 'weak'
      : presentation.overall_tier === 'reference_candidate'
        ? 'reference'
        : '';
    presentationNoteEl.className = `presentation-note ${tierClass}`.trim();
    presentationNoteEl.style.display = 'block';
    presentationNoteEl.textContent = presentation.note;
  }

  const narrativeEl = document.getElementById('narrative');
  if (briefing.narrative) {
    narrativeEl.style.display = 'block';
    narrativeEl.textContent = briefing.narrative;
  }

  const rows = briefing.table || [];
  if (!rows.length) return;

  const formatIvPart = (value, fallback) => {
    if (typeof value === 'number') return (value * 100).toFixed(0) + '%';
    if (value !== undefined && value !== null && value !== '') return String(value);
    if (typeof fallback === 'number') return (fallback * 100).toFixed(0) + '%';
    if (fallback !== undefined && fallback !== null && fallback !== '') return String(fallback);
    return '-';
  };

  const tbody = document.getElementById('result-body');
  tbody.innerHTML = rows.map((r, i) => {
    const scoreClass = r.score >= 0.80 ? 'score-high' : r.score >= 0.60 ? 'score-mid' : 'score-low';
    const flags = (r.risk_flags || []).map(f => `<span class="flag">${escapeHtml(f)}</span>`).join('');
    const greeks = `<div class="greeks">
      <span>Δ=${escapeHtml(r.net_delta)}</span>
      <span>V=${escapeHtml(r.net_vega)}</span>
      <span>Θ=${escapeHtml(r.net_theta)}</span>
    </div>`;
    const ivTriplet = r.iv_triplet_display || [
      formatIvPart(r.atm_iv_pct, r.iv_pct),
      formatIvPart(r.call_iv_pct, null),
      formatIvPart(r.put_iv_pct, null),
    ].join(' / ');
    const profit = r.profit_condition
      ? `<span class="profit-cond">${escapeHtml(r.profit_condition)}</span>`
      : '';

    return `<tr class="${i === 0 ? 'rank-1' : ''}">
      <td>${escapeHtml(r.rank)}</td>
      <td>${escapeHtml(r.underlying)}</td>
      <td><b>${escapeHtml(r.strategy)}</b></td>
      <td class="${scoreClass}">${escapeHtml(r.score)}<br><span class="score-label">${escapeHtml(r.score_tier_label || '')}</span></td>
      <td>${escapeHtml(r.cost)}</td>
      <td>${greeks}</td>
      <td><span class="iv-label">${escapeHtml(ivTriplet)}</span></td>
      <td class="legs-cell">${escapeHtml(r.legs || '').replace(/ \\/ /g, '<br>')}</td>
      <td>${profit}</td>
      <td>${flags}</td>
    </tr>`;
  }).join('');

  document.getElementById('result-table').style.display = 'table';
}

function renderCoveredCallResult(data) {
  const intentBar = document.getElementById('intent-bar');
  const rulesText = (data.params.target_upside_rules || [])
    .map(r => `DTE≤${r.dte_max}: ${(r.target_upside_buffer * 100).toFixed(1)}%`)
    .join(' / ');

  intentBar.innerHTML = `
    <span>备兑扫描：</span>
    <span class="badge">标的: ${escapeHtml(data.underlying_id)}</span>
    <span class="badge">手数: ${escapeHtml(data.hands)}</span>
    <span class="badge">份额: ${escapeHtml(data.total_shares)}</span>
    <span class="badge">DTE: ${escapeHtml(data.params.dte_min)}-${escapeHtml(data.params.dte_max)}</span>
    <span class="badge">目标Delta: ${escapeHtml(data.params.delta_target)}</span>
    <span class="badge">上行保护: ${escapeHtml(rulesText || '未设置')}</span>
  `;
  intentBar.style.display = 'flex';

  const narrativeEl = document.getElementById('narrative');
  narrativeEl.style.display = 'block';
  narrativeEl.textContent =
    `备兑扫描结果：${data.underlying_id}，持仓 ${data.hands} 手（${data.total_shares} 份），按评分展示前 ${data.params.top_n} 个候选 short call。`;

  const rows = data.items || [];
  const tbody = document.getElementById('result-body');
  tbody.innerHTML = rows.map((r, i) => {
    const scoreClass = r.score >= 1.0 ? 'score-high' : r.score >= 0.75 ? 'score-mid' : 'score-low';
    const greeks = `<div class="greeks">
      <span>Δ=${escapeHtml(r.delta)}</span>
      <span>IV=${escapeHtml(r.iv)}</span>
      <span>DTE=${escapeHtml(r.dte)}</span>
    </div>`;
    const cost = `预计收入 ${r.estimated_total_income_mid}`;
    const legs = `卖CALL K=${r.strike}<br>到期=${r.expiry_date}<br>bid=${r.bid} ask=${r.ask} mid=${r.mid}<br>建议挂单=${r.limit_price}`;
    const profit = `<span class="profit-cond">
      年化=${(r.ann_yield * 100).toFixed(1)}%<br>
      上行缓冲=${r.upside_buffer !== null ? (r.upside_buffer * 100).toFixed(1) + '%' : '-'}<br>
      规则保护=${r.target_upside_buffer !== null && r.target_upside_buffer !== undefined ? (r.target_upside_buffer * 100).toFixed(1) + '%' : '-'}<br>
      保护评分=${r.buffer_score}
    </span>`;
    const flags = `
      <span class="flag">spread=${(r.rel_spread * 100).toFixed(1)}%</span>
      <span class="flag">收入(挂单)=${escapeHtml(r.estimated_total_income_limit)}</span>
    `;

    return `<tr class="${i === 0 ? 'rank-1' : ''}">
      <td>${i + 1}</td>
      <td>${escapeHtml(r.underlying_id)}</td>
      <td><b>covered_call</b></td>
      <td class="${scoreClass}">${escapeHtml(r.score)}</td>
      <td>${escapeHtml(cost)}</td>
      <td>${greeks}</td>
      <td>-</td>
      <td class="legs-cell">${legs}</td>
      <td>${profit}</td>
      <td>${flags}</td>
    </tr>`;
  }).join('');

  document.getElementById('result-table').style.display = 'table';
}

document.getElementById('text').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    runAdvisor();
  }
});

setCoveredCallMode(isExplicitCoveredCallMode());
</script>
</body>
</html>
"""
