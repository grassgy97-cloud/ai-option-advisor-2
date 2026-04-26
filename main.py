from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.api.advisor_v2 import router as advisor_router
from app.api.covered_call_api import router as covered_call_router
from app.api.debug import router as debug_router
from app.api.monitor import router as monitor_router
from app.api.positions import router as positions_router
from app.api.scanner_v2 import router as scanner_router

app = FastAPI(title="AI Option Advisor")

app.include_router(advisor_router)
app.include_router(scanner_router)
app.include_router(covered_call_router)
app.include_router(monitor_router)
app.include_router(positions_router)
app.include_router(debug_router)


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

.positions-section {
  margin-top: 28px;
  padding: 18px;
  border: 1px solid #d8e1ef;
  border-radius: 14px;
  background: #fbfdff;
}
.positions-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.positions-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.positions-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.positions-table th,
.positions-table td {
  padding: 8px;
  border-bottom: 1px solid #e5edf7;
  vertical-align: top;
}
.position-form-panel {
  display: none;
  margin: 12px 0;
  padding: 14px;
  border: 1px solid #d8e1ef;
  border-radius: 12px;
  background: #ffffff;
}
.position-form-panel.visible {
  display: block;
}
.position-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.position-grid label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #41546d;
}
.position-grid input,
.position-grid select {
  padding: 8px;
  border: 1px solid #cbd7e6;
  border-radius: 8px;
}
.monitor-box,
.position-monitor {
  margin-top: 12px;
  padding: 12px;
  border-radius: 10px;
  background: #111;
  border: 1px solid #343a46;
  color: #e6e6e6;
  line-height: 1.6;
  white-space: pre-wrap;
  font-size: 13px;
}
.alert { color: #ff4d4f; }
.watch { color: #faad14; }
.normal { color: #52c41a; }
.contract-meta-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.contract-meta-tools input {
  flex: 1 1 170px;
}
.contract-meta-status {
  min-height: 16px;
  color: #41546d;
  font-size: 12px;
}
.contract-meta-status.error {
  color: #a33a2a;
}
.contract-meta-status.ok {
  color: #236b3a;
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
        <button id="btn-select-all" type="button">全选</button>
        /
        <button id="btn-clear-all" type="button">清空</button>
      </span>
    </div>
    <label><input type="checkbox" value="ALL" id="chk-ALL"> 全部扫描</label>
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
    <button id="btn" type="button">分析</button>
    <button id="btn-covered" type="button">备兑扫描</button>
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
  <span class="shortcut" data-shortcut="近月认购偏贵，想做低风险跨期组合">近月认购偏贵</span>
  <span class="shortcut" data-shortcut="我觉得 300 近期会涨，想做方向性策略">看多方向</span>
  <span class="shortcut" data-shortcut="我持有 300ETF 现货，想做备兑增强">备兑增收</span>
  <span class="shortcut" data-shortcut="整体波动率偏高，想收 theta">IV 偏高卖方</span>
  <span class="shortcut" data-shortcut="双向波动可能变大，但下行空间更多，下方 8% 有支撑">非对称波动</span>
  <span class="shortcut" data-shortcut="偏空，下行空间大，上方 5% 有压力">偏空压力位</span>
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

<section class="positions-section">
  <div class="positions-header">
    <div>
      <h2>持仓管理</h2>
      <div class="textarea-hint">手动录入当前持仓腿；covered_call 或不计入组合 Greeks 的腿仍会显示，但不会进入标的聚合监控。</div>
    </div>
    <div class="positions-actions">
      <button id="btn-position-new" type="button">新增持仓腿</button>
      <button id="btn-position-refresh" type="button">刷新持仓</button>
    </div>
  </div>

  <div id="position-form-panel" class="position-form-panel">
    <input id="pos-leg-id" type="hidden">
    <div class="position-grid">
      <label>underlying_id<input id="pos-underlying-id" value="510300"></label>
      <label>contract_id
        <div class="contract-meta-tools">
          <input id="pos-contract-id">
          <button id="btn-contract-meta" type="button">自动读取</button>
        </div>
        <span id="contract-meta-status" class="contract-meta-status"></span>
      </label>
      <label>option_type
        <select id="pos-option-type">
          <option value="CALL">CALL</option>
          <option value="PUT">PUT</option>
        </select>
      </label>
      <label>strike<input id="pos-strike" type="number" step="0.0001"></label>
      <label>expiry_date<input id="pos-expiry-date" type="date"></label>
      <label>side
        <select id="pos-side">
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
        </select>
      </label>
      <label>quantity<input id="pos-quantity" type="number" min="0" step="1" value="1"></label>
      <label>avg_entry_price<input id="pos-avg-entry-price" type="number" step="0.0001"></label>
      <label>strategy_bucket<input id="pos-strategy-bucket" placeholder="bear_call_spread / covered_call"></label>
      <label>group_id<input id="pos-group-id"></label>
      <label>tag<input id="pos-tag"></label>
      <label>fee_rmb<input id="pos-fee-rmb" type="number" step="0.01" value="0"></label>
      <label>include_in_portfolio_greeks
        <select id="pos-include-greeks">
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <label>note<input id="pos-note"></label>
    </div>
    <div class="positions-actions" style="margin-top:12px">
      <button id="btn-position-save" type="button">保存</button>
      <button id="btn-position-cancel" type="button">取消</button>
    </div>
  </div>

  <div class="table-wrap">
    <table class="positions-table">
      <thead>
        <tr>
          <th>标的</th>
          <th>合约</th>
          <th>方向</th>
          <th>数量/均价</th>
          <th>分组</th>
          <th>Greeks计入</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody id="positions-body"></tbody>
    </table>
  </div>
  <div id="position-monitor-result" class="monitor-box" style="display:none"></div>
</section>

<script>
let coveredCallMode = false;
let positionRows = [];

function escapeHtml(value) {
  return String(value !== undefined && value !== null ? value : '')
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
  const llmCommentary = briefing.llm_commentary || data.briefing_llm_commentary || {};
  if (briefing.narrative) {
    narrativeEl.style.display = 'block';
    let llmText = '';
    if (llmCommentary.available) {
      llmText = [
          '',
          'LLM解读：' + (llmCommentary.text || llmCommentary.summary || '-'),
          '首选理由：' + (llmCommentary.why_primary || '-'),
          '关注点：' + ((llmCommentary.what_to_watch || []).join(' / ') || '-'),
        ].join('\\n');
    } else if (llmCommentary.error) {
      llmText = ['', 'LLM解读暂不可用'].join('\\n');
    }
    narrativeEl.textContent = briefing.narrative + llmText;
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

function positionPayloadFromForm() {
  const optionalNumber = function(id) {
    const el = document.getElementById(id);
    if (!el || el.value === '') return null;
    return Number(el.value);
  };
  return {
    underlying_id: document.getElementById('pos-underlying-id').value.trim(),
    contract_id: document.getElementById('pos-contract-id').value.trim(),
    option_type: document.getElementById('pos-option-type').value,
    strike: optionalNumber('pos-strike'),
    expiry_date: document.getElementById('pos-expiry-date').value,
    side: document.getElementById('pos-side').value,
    quantity: Number(document.getElementById('pos-quantity').value || 0),
    avg_entry_price: Number(document.getElementById('pos-avg-entry-price').value || 0),
    strategy_bucket: document.getElementById('pos-strategy-bucket').value.trim() || null,
    group_id: document.getElementById('pos-group-id').value.trim() || null,
    tag: document.getElementById('pos-tag').value.trim() || null,
    include_in_portfolio_greeks: document.getElementById('pos-include-greeks').value === 'true',
    note: document.getElementById('pos-note').value.trim() || null,
    fee_rmb: Number(document.getElementById('pos-fee-rmb').value || 0),
    reason: 'manual_frontend_upsert',
  };
}

function openPositionForm(row) {
  row = row || {};
  const panel = document.getElementById('position-form-panel');
  if (!panel) {
    console.error('[positions] position form panel not found');
    return;
  }
  panel.classList.add('visible');
  panel.style.display = 'block';
  document.getElementById('pos-leg-id').value = row.leg_id || '';
  document.getElementById('pos-underlying-id').value = row.underlying_id || '510300';
  document.getElementById('pos-contract-id').value = row.contract_id || '';
  document.getElementById('pos-option-type').value = row.option_type || 'CALL';
  document.getElementById('pos-strike').value = row.strike !== undefined && row.strike !== null ? row.strike : '';
  document.getElementById('pos-expiry-date').value = row.expiry_date || '';
  document.getElementById('pos-side').value = row.side || 'SELL';
  document.getElementById('pos-quantity').value = row.quantity !== undefined && row.quantity !== null ? row.quantity : 1;
  document.getElementById('pos-avg-entry-price').value = row.avg_entry_price !== undefined && row.avg_entry_price !== null ? row.avg_entry_price : '';
  document.getElementById('pos-strategy-bucket').value = row.strategy_bucket || '';
  document.getElementById('pos-group-id').value = row.group_id || '';
  document.getElementById('pos-tag').value = row.tag || '';
  document.getElementById('pos-include-greeks').value = String(row.include_in_portfolio_greeks !== undefined && row.include_in_portfolio_greeks !== null ? row.include_in_portfolio_greeks : true);
  document.getElementById('pos-note').value = row.note || '';
  document.getElementById('pos-fee-rmb').value = 0;
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closePositionForm() {
  const panel = document.getElementById('position-form-panel');
  if (!panel) return;
  panel.classList.remove('visible');
  panel.style.display = 'none';
}

function setContractMetaStatus(message, kind) {
  const status = document.getElementById('contract-meta-status');
  if (!status) return;
  status.textContent = message || '';
  status.className = 'contract-meta-status' + (kind ? ' ' + kind : '');
}

async function loadContractMetaFromForm() {
  const input = document.getElementById('pos-contract-id');
  if (!input) return;
  const contractId = input.value.trim();
  if (!contractId) {
    setContractMetaStatus('请先输入 contract_id', 'error');
    return;
  }

  setContractMetaStatus('读取合约信息中...', '');
  try {
    const resp = await fetch('/positions/enrich?contract_id=' + encodeURIComponent(contractId));
    if (!resp.ok) {
      setContractMetaStatus('未找到该 contract_id，可继续手动填写', 'error');
      return;
    }
    const raw = await resp.json();
    const data = raw.data || {};
    document.getElementById('pos-underlying-id').value = data.underlying_id || '';
    document.getElementById('pos-option-type').value = data.option_type || 'CALL';
    document.getElementById('pos-strike').value = data.strike !== undefined && data.strike !== null ? data.strike : '';
    document.getElementById('pos-expiry-date').value = data.expiry_date || '';
    setContractMetaStatus('已自动填充合约信息', 'ok');
  } catch (e) {
    console.error('[positions] contract meta load failed', e);
    setContractMetaStatus('读取失败：' + (e.message || e), 'error');
  }
}

async function savePositionLeg() {
  const payload = positionPayloadFromForm();
  if (!payload.contract_id) {
    alert('请至少填写 contract_id；underlying_id / option_type / strike / expiry_date 可自动补全。');
    return;
  }
  const resp = await fetch('/positions/upsert-leg', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const text = await resp.text();
    alert('保存失败：' + text);
    return;
  }
  closePositionForm();
  await loadPositions();
}

async function loadPositions() {
  const tbody = document.getElementById('positions-body');
  if (!tbody) return;
  try {
    const resp = await fetch('/positions/legs?status=OPEN');
    if (!resp.ok) {
      tbody.innerHTML = '<tr><td colspan="8">持仓读取失败</td></tr>';
      return;
    }
    const raw = await resp.json();
    positionRows = raw.data || [];
    renderPositions(positionRows);
  } catch (e) {
    console.error('[positions] load failed', e);
    tbody.innerHTML = '<tr><td colspan="8">持仓读取失败：' + escapeHtml(e.message || e) + '</td></tr>';
  }
}

function renderPositions(rows) {
  const tbody = document.getElementById('positions-body');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="8">暂无 OPEN 持仓腿</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(function(row) {
    const legId = Number(row.leg_id);
    return [
      '<tr>',
      '<td>' + escapeHtml(row.underlying_id) + '</td>',
      '<td>' + escapeHtml(row.contract_id) + '<br>' +
        escapeHtml(row.option_type) + ' K=' + escapeHtml(row.strike) + ' ' + escapeHtml(row.expiry_date) + '</td>',
      '<td>' + escapeHtml(row.side) + '</td>',
      '<td>' + escapeHtml(row.quantity) + ' @ ' + escapeHtml(row.avg_entry_price) + '</td>',
      '<td>' + escapeHtml(row.strategy_bucket || '-') + '<br>group=' +
        escapeHtml(row.group_id || '-') + ' tag=' + escapeHtml(row.tag || '-') + '</td>',
      '<td>' + (row.include_in_portfolio_greeks ? '是' : '否') + '</td>',
      '<td>' + escapeHtml(row.status) + '</td>',
      '<td>',
      '<button type="button" data-position-action="edit" data-leg-id="' + legId + '">编辑</button> ',
      '<button type="button" data-position-action="delete" data-leg-id="' + legId + '">删除</button> ',
      '<button type="button" data-position-action="monitor" data-underlying-id="' + escapeHtml(row.underlying_id) + '">监控标的</button>',
      '</td>',
      '</tr>',
    ].join('');
  }).join('');
}

function editPositionLeg(legId) {
  let row = null;
  for (let i = 0; i < positionRows.length; i += 1) {
    if (Number(positionRows[i].leg_id) === Number(legId)) {
      row = positionRows[i];
      break;
    }
  }
  if (row) openPositionForm(row);
}

window.openPositionForm = openPositionForm;
window.closePositionForm = closePositionForm;
window.editPositionLeg = editPositionLeg;
window.deletePositionLeg = deletePositionLeg;
window.monitorUnderlying = monitorUnderlying;
window.loadPositions = loadPositions;
window.savePositionLeg = savePositionLeg;
window.loadContractMetaFromForm = loadContractMetaFromForm;

async function deletePositionLeg(legId) {
  if (!confirm('删除仅用于误录清理，不代表正常交易平仓。确认删除？')) return;
  try {
    const resp = await fetch('/positions/legs/' + encodeURIComponent(legId), { method: 'DELETE' });
    if (!resp.ok) {
      alert('删除失败：' + await resp.text());
      return;
    }
    await loadPositions();
  } catch (e) {
    console.error('[positions] delete failed', e);
    alert('删除失败：' + (e.message || e));
  }
}

async function monitorUnderlying(underlyingId) {
  const box = document.getElementById('position-monitor-result');
  if (!box) return;
  box.style.display = 'block';
  box.className = 'monitor-box';
  box.textContent = '监控 ' + underlyingId + ' 中...';
  try {
    const resp = await fetch('/monitor/underlying/' + encodeURIComponent(underlyingId));
    if (!resp.ok) {
      box.textContent = '监控失败：' + await resp.text();
      return;
    }
    const raw = await resp.json();
    const data = raw.data || {};
    const summary = data.monitoring_summary || {};
    const statusClass = summary.status === 'alert' ? 'alert' : summary.status === 'watch' ? 'watch' : 'normal';
    box.className = 'monitor-box ' + statusClass;
    const contributors = data.risk_contributors || [];
    const hedges = data.hedge_suggestions || [];
    const llm = data.llm_commentary || data.monitoring_llm_commentary || summary.llm_commentary || {};
    const commentaryLines = llm.available ? [
      'LLM解读: ' + (llm.text || llm.summary || '-'),
      '风险解释: ' + (llm.risk_explanation || '-'),
      '行动建议: ' + ((llm.actionable_suggestions || []).join(' / ') || '-'),
    ] : [];
    const valueOrDash = function(value) {
      return value !== undefined && value !== null ? value : '-';
    };
    box.textContent = [
      '标的: ' + (data.underlying_id || '-'),
      '状态: ' + (summary.status || '-') + ' / 建议: ' + (summary.recommended_action || '-'),
      'Spot: ' + valueOrDash(summary.spot) + ' / PnL: ' + valueOrDash(summary.pnl_estimate) + ' RMB / DTE: ' + valueOrDash(summary.dte),
      'Greeks: Δ=' + valueOrDash(summary.net_delta) + ' Γ=' + valueOrDash(summary.net_gamma) + ' Θ=' + valueOrDash(summary.net_theta) + ' V=' + valueOrDash(summary.net_vega),
      'Risk flags: ' + ((summary.risk_flags || []).join(', ') || '-'),
      '主要风险腿: ' + (contributors.length ? contributors.map(function(c) {
        return c.leg_id + ':' + c.reason + ':' + c.suggested_action;
      }).join(' / ') : '-'),
      '对冲提示: ' + (hedges.length ? hedges.map(function(h) {
        return h.goal + ': ' + h.suggestion;
      }).join(' / ') : '-'),
      '备注: ' + ((summary.notes || []).join(' / ') || '-'),
    ].concat(commentaryLines).join('\\n');
  } catch (e) {
    console.error('[positions] monitor failed', e);
    box.textContent = '监控失败：' + (e.message || e);
  }
}

window.runAdvisor = runAdvisor;
window.runCoveredCall = runCoveredCall;
window.setShortcut = setShortcut;
window.toggleAll = toggleAll;
window.selectAll = selectAll;
window.clearAll = clearAll;

function safeInit(name, fn) {
  try {
    fn();
  } catch (e) {
    console.error('[init] ' + name + ' failed', e);
  }
}

function initAdvisorInteractions() {
  const analyzeButton = document.getElementById('btn');
  if (analyzeButton) {
    analyzeButton.addEventListener('click', function() {
      runAdvisor();
    });
  }

  const coveredButton = document.getElementById('btn-covered');
  if (coveredButton) {
    coveredButton.addEventListener('click', function() {
      runCoveredCall();
    });
  }

  const selectAllButton = document.getElementById('btn-select-all');
  if (selectAllButton) {
    selectAllButton.addEventListener('click', function() {
      selectAll();
    });
  }

  const clearAllButton = document.getElementById('btn-clear-all');
  if (clearAllButton) {
    clearAllButton.addEventListener('click', function() {
      clearAll();
    });
  }

  const allCheckbox = document.getElementById('chk-ALL');
  if (allCheckbox) {
    allCheckbox.addEventListener('change', function() {
      toggleAll(allCheckbox);
    });
  }

  document.querySelectorAll('[data-shortcut]').forEach(function(item) {
    item.addEventListener('click', function() {
      setShortcut(item.getAttribute('data-shortcut') || '');
    });
  });

  const textEl = document.getElementById('text');
  if (textEl) {
    textEl.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        runAdvisor();
      }
    });
  }
  setCoveredCallMode(isExplicitCoveredCallMode());
}

function initPositionInteractions() {
  const positionNewButton = document.getElementById('btn-position-new');
  if (positionNewButton) {
    positionNewButton.addEventListener('click', function() {
      openPositionForm();
    });
  }

  const contractMetaButton = document.getElementById('btn-contract-meta');
  if (contractMetaButton) {
    contractMetaButton.addEventListener('click', function() {
      loadContractMetaFromForm();
    });
  }

  const contractInput = document.getElementById('pos-contract-id');
  if (contractInput) {
    contractInput.addEventListener('blur', function() {
      if (contractInput.value.trim()) {
        loadContractMetaFromForm();
      }
    });
  }

  const positionRefreshButton = document.getElementById('btn-position-refresh');
  if (positionRefreshButton) {
    positionRefreshButton.addEventListener('click', function() {
      loadPositions();
    });
  }

  const positionSaveButton = document.getElementById('btn-position-save');
  if (positionSaveButton) {
    positionSaveButton.addEventListener('click', function() {
      savePositionLeg();
    });
  }

  const positionCancelButton = document.getElementById('btn-position-cancel');
  if (positionCancelButton) {
    positionCancelButton.addEventListener('click', function() {
      closePositionForm();
    });
  }

  const positionsBody = document.getElementById('positions-body');
  if (positionsBody) {
    positionsBody.addEventListener('click', function(e) {
      const target = e.target;
      if (!target || !target.getAttribute) return;
      const action = target.getAttribute('data-position-action');
      if (!action) return;
      const legId = target.getAttribute('data-leg-id');
      const underlyingId = target.getAttribute('data-underlying-id');
      if (action === 'edit') {
        editPositionLeg(legId);
      } else if (action === 'delete') {
        deletePositionLeg(legId);
      } else if (action === 'monitor') {
        monitorUnderlying(underlyingId);
      }
    });
  }

  loadPositions().catch(function(e) {
    console.error('[positions] initial load failed', e);
  });
}

function initAdvisor() {
  initAdvisorInteractions();
}

function initPositions() {
  initPositionInteractions();
}

function initPage() {
  safeInit('advisor', initAdvisor);
  safeInit('positions', initPositions);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
</script>
</body>
</html>
"""
