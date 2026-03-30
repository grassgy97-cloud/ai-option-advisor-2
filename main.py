from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from app.api.advisor_v2 import router as advisor_router
from app.api.scanner_v2 import router as scanner_router

app = FastAPI(title="AI Option Advisor")

app.include_router(advisor_router)
app.include_router(scanner_router)


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
    margin-bottom: 16px;
    align-items: flex-start;
  }
  select {
    background: #1e2130;
    border: 1px solid #2e3250;
    color: #e0e0e0;
    padding: 10px 12px;
    border-radius: 8px;
    font-size: 14px;
    cursor: pointer;
    height: 44px;
  }
  textarea {
    flex: 1;
    background: #1e2130;
    border: 1px solid #2e3250;
    color: #e0e0e0;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 14px;
    resize: vertical;
    min-height: 44px;
    line-height: 1.5;
    font-family: inherit;
  }
  textarea:focus, select:focus {
    outline: none;
    border-color: #4a6cf7;
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
  }
  button:hover { background: #3a5ce7; }
  button:disabled { background: #2e3250; color: #666; cursor: not-allowed; }

  /* 快捷输入 */
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
  .shortcut:hover { border-color: #4a6cf7; color: #fff; }

  /* 状态 */
  #status {
    font-size: 13px;
    color: #6b7280;
    margin-bottom: 16px;
    min-height: 18px;
  }
  #status.loading { color: #4a6cf7; }
  #status.error   { color: #ef4444; }

  /* narrative */
  #narrative {
    background: #1a1d2e;
    border: 1px solid #2e3250;
    border-radius: 10px;
    padding: 16px 20px;
    font-size: 14px;
    line-height: 1.7;
    margin-bottom: 20px;
    white-space: pre-wrap;
    display: none;
  }

  /* 策略表格 */
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
  .score-mid  { color: #fbbf24; }
  .score-low  { color: #f87171; }
  .legs-cell  { font-size: 11px; color: #8892b0; max-width: 300px; line-height: 1.6; }
  .flag       { display: inline-block; background: #2d1f1f; color: #f87171;
                font-size: 10px; padding: 1px 6px; border-radius: 4px; margin: 1px; }
  .iv-label   { font-size: 11px; color: #a0aec0; }

  /* Greeks 小表 */
  .greeks {
    display: inline-flex;
    gap: 10px;
    font-size: 11px;
    color: #6b7280;
  }
  .greeks span { white-space: nowrap; }

  /* intent badge */
  #intent-bar {
    display: none;
    background: #1a1d2e;
    border: 1px solid #2e3250;
    border-radius: 8px;
    padding: 10px 16px;
    font-size: 12px;
    color: #8892b0;
    margin-bottom: 16px;
    display: flex;
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
</style>
</head>
<body>

<h1>📊 AI Option Advisor</h1>

<div class="input-row">
  <select id="underlying">
    <option value="ALL">🔍 全部标的</option>
    <option value="510300">510300 沪深300</option>
    <option value="510050">510050 上证50</option>
    <option value="510500">510500 中证500</option>
    <option value="588000">588000 科创50(华夏)</option>
    <option value="588080">588080 科创50(易方达)</option>
    <option value="159915">159915 创业板</option>
    <option value="159901">159901 深证100</option>
    <option value="159919">159919 沪深300(深)</option>
    <option value="159922">159922 中证500(深)</option>
  </select>
  <textarea id="text" rows="1" placeholder="输入你的市场观点，例如：近月认购偏贵，想做低风险跨期组合"></textarea>
  <button id="btn" onclick="runAdvisor()">分析</button>
</div>

<div class="shortcuts">
  <span class="shortcut" onclick="setShortcut('近月认购偏贵，想做低风险跨期组合')">近月认购偏贵</span>
  <span class="shortcut" onclick="setShortcut('我觉得300近期会涨，想做方向性策略')">看多方向</span>
  <span class="shortcut" onclick="setShortcut('我持有300ETF现货想做备兑')">备兑增收</span>
  <span class="shortcut" onclick="setShortcut('整体波动率偏高，想收theta')">IV偏高卖方</span>
  <span class="shortcut" onclick="setShortcut('沪深300和上证50近月认购都偏贵')">多标的跨期</span>
</div>

<div id="status"></div>
<div id="intent-bar" style="display:none"></div>
<div id="narrative"></div>
<div class="table-wrap"><table id="result-table" style="display:none">
  <thead><tr>
    <th>#</th><th>标的</th><th>策略</th><th>评分</th>
    <th>成本/收入</th><th>Greeks</th><th>IV</th><th>腿</th><th>风险</th>
  </tr></thead>
  <tbody id="result-body"></tbody>
</table></div>

<script>
function setShortcut(text) {
  document.getElementById('text').value = text;
}

async function runAdvisor() {
  const text = document.getElementById('text').value.trim();
  const underlying = document.getElementById('underlying').value;
  if (!text) return;

  const btn = document.getElementById('btn');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.className = 'loading';
  const isAll = underlying === 'ALL';
  status.textContent = isAll ? '全量分析中，预计需要60-120秒…' : '分析中…';
  document.getElementById('narrative').style.display = 'none';
  document.getElementById('result-table').style.display = 'none';
  document.getElementById('intent-bar').style.display = 'none';
  document.getElementById('result-body').innerHTML = '';

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 150000);  // 150秒超时

  try {
    const resp = await fetch('/advisor/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, underlying_id: underlying }),
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderResult(data.data || data);
    status.className = '';
    status.textContent = '';
  } catch(e) {
    status.className = 'error';
    status.textContent = '请求失败：' + e.message;
  } finally {
    clearTimeout(timeoutId);
    btn.disabled = false;
  }
}

function renderResult(data) {
  // intent bar
  const intent = data.parsed_intent || {};
  const intentBar = document.getElementById('intent-bar');
  intentBar.style.display = 'flex';
  intentBar.innerHTML = `
    <span>解析结果：</span>
    <span class="badge">market: ${intent.market_view || '-'}</span>
    <span class="badge">vol: ${intent.vol_view || '-'}</span>
    <span class="badge">标的: ${(intent.underlying_ids || [intent.underlying_id]).join(', ')}</span>
    <span class="badge">DTE: ${intent.dte_min}-${intent.dte_max}天</span>
  `;

  // narrative
  const briefing = data.briefing || {};
  const narrativeEl = document.getElementById('narrative');
  if (briefing.narrative) {
    narrativeEl.style.display = 'block';
    narrativeEl.textContent = briefing.narrative;
  }

  // table
  const rows = briefing.table || [];
  if (!rows.length) return;

  const tbody = document.getElementById('result-body');
  tbody.innerHTML = rows.map((r, i) => {
    const scoreClass = r.score >= 0.8 ? 'score-high' : r.score >= 0.6 ? 'score-mid' : 'score-low';
    const flags = (r.risk_flags || []).map(f =>
      `<span class="flag">${f}</span>`
    ).join('');
    const greeks = `<div class="greeks">
      <span>Δ=${r.net_delta}</span>
      <span>V=${r.net_vega}</span>
      <span>θ=${r.net_theta}</span>
    </div>`;
    return `<tr class="${i===0?'rank-1':''}">
      <td>${r.rank}</td>
      <td>${r.underlying}</td>
      <td><b>${r.strategy}</b></td>
      <td class="${scoreClass}">${r.score}</td>
      <td>${r.cost}</td>
      <td>${greeks}</td>
      <td><span class="iv-label">${r.iv_label}(${(r.iv_pct*100).toFixed(0)}%)</span></td>
      <td class="legs-cell">${(r.legs||'').replace(/ \/ /g,'<br>')}</td>
      <td>${flags}</td>
    </tr>`;
  }).join('');

  document.getElementById('result-table').style.display = 'table';
}

// 回车提交
document.getElementById('text').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    runAdvisor();
  }
});
</script>
</body>
</html>
"""