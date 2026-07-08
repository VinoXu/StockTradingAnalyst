const LS_SELECTED = 'pp_selected_symbols';
const LS_SELECTED_EXPLICIT = 'pp_selected_explicit';

const views = {
  chat: document.getElementById('view-chat'),
  history: document.getElementById('view-history'),
  stockList: document.getElementById('view-stockList'),
};
const chatFooter = document.getElementById('chatFooter');
const chatMessages = document.getElementById('chatMessages');
const chatEmpty = document.getElementById('chatEmpty');
const inputBottom = document.getElementById('inputBottom');
const sendBtn = document.getElementById('sendBtn');
const loadingBar = document.getElementById('loadingBar');
const analyzingBanner = document.getElementById('analyzingBanner');
const analyzingStatusText = document.getElementById('analyzingStatusText');
const cancelChatBtn = document.getElementById('cancelChatBtn');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const selectedSummary = document.getElementById('selectedSummary');
const settingsModal = document.getElementById('settingsModal');
const stockPickerModal = document.getElementById('stockPickerModal');
const toastEl = document.getElementById('toast');
const contextLimitBanner = document.getElementById('contextLimitBanner');
const contextLimitText = document.getElementById('contextLimitText');
const contextLimitNewChat = document.getElementById('contextLimitNewChat');

let holdings = [];
let selected = loadSelected();
let pickerDraft = [];
let chatTurns = [];
let sending = false;
let contextFull = false;
let llmReady = false;
let chatAbortController = null;
let analyzingTimer = null;
let analyzingStartedAt = 0;
let tradingHours = false;
let livePollTimer = null;
let typingEl = null;
let currentSessionId = null;
let loadedSessionId = null;
const chartInstances = new Map();

function loadSelected() {
  try {
    return JSON.parse(localStorage.getItem(LS_SELECTED) || '[]');
  } catch {
    return [];
  }
}

function isSelectionExplicit() {
  return localStorage.getItem(LS_SELECTED_EXPLICIT) === '1';
}

function markSelectionExplicit() {
  localStorage.setItem(LS_SELECTED_EXPLICIT, '1');
}

/** 与持仓对齐：仅剔除已删除标的；未手动选过且为空时才默认全选 */
function syncSelectedWithHoldings() {
  const codes = new Set(holdings.map((h) => h.code));
  selected = selected.filter((c) => codes.has(c));
  if (!selected.length && holdings.length && !isSelectionExplicit()) {
    selected = holdings.map((h) => h.code);
    saveSelected();
  }
}

function saveSelected() {
  localStorage.setItem(LS_SELECTED, JSON.stringify(selected));
  updateSelectedSummary();
}

function toast(msg, type = 'info') {
  toastEl.textContent = msg;
  toastEl.className = `toast ${type === 'error' ? 'bg-fall' : type === 'ok' ? 'bg-rise' : 'bg-textMain'}`;
  toastEl.classList.remove('hidden', 'opacity-0');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    toastEl.classList.add('opacity-0');
    setTimeout(() => toastEl.classList.add('hidden'), 300);
  }, 3200);
}

function openModal(el) {
  if (el) el.classList.add('is-open');
}

function closeModal(el) {
  if (el) el.classList.remove('is-open');
}

function closeAllModals() {
  closeModal(settingsModal);
  closeModal(stockPickerModal);
}

async function api(path, opts = {}) {
  const { signal, ...rest } = opts;
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...rest.headers },
    signal,
    ...rest,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : data.error || res.statusText);
  }
  return data;
}

function splitHistoryAnswer(answer) {
  if (!answer) return { timeBanner: null, body: answer };
  const idx = answer.indexOf('\n\n');
  if (idx > 0 && answer.slice(0, idx).includes('📅')) {
    return { timeBanner: answer.slice(0, idx), body: answer.slice(idx + 2) };
  }
  return { timeBanner: null, body: answer };
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function updateSelectedSummary() {
  if (!selected.length) {
    selectedSummary.textContent = '当前分析标的：未选择（将按提问智能匹配板块与数据）';
    return;
  }
  const names = selected.map((code) => holdings.find((x) => x.code === code)?.name || code);
  const label = names.length <= 4 ? names.join('、') : `${names.slice(0, 3).join('、')} 等 ${names.length} 只`;
  selectedSummary.textContent = `当前分析标的：${label}`;
}

function updatePickerCount() {
  const el = document.getElementById('pickerCount');
  if (el) el.textContent = `已选 ${pickerDraft.length} 只`;
}

function switchView(name) {
  document.querySelectorAll('[data-view]').forEach((nav) => {
    nav.classList.remove('nav-active');
    nav.querySelector('span').className = 'text-textSub';
    nav.querySelector('i').classList.add('text-textSub');
  });
  const active = document.querySelector(`[data-view="${name}"]`);
  if (active) {
    active.classList.add('nav-active');
    active.querySelector('span').className = '';
    active.querySelector('i').classList.remove('text-textSub');
  }
  Object.values(views).forEach((v) => v.classList.add('hidden'));
  views[name].classList.remove('hidden');
  chatFooter.classList.toggle('hidden', name !== 'chat');
  if (name === 'history') loadHistory();
  if (name === 'stockList') loadHoldings();
}

function renderChat() {
  chatMessages.querySelectorAll('.chat-bubble-row').forEach((el) => el.remove());
  if (!chatTurns.length) {
    chatEmpty.classList.remove('hidden');
    return;
  }
  chatEmpty.classList.add('hidden');
  chatTurns.forEach((turn) => appendBubble(turn.role, turn.content, {
    noScroll: true,
    charts: turn.charts,
    timeBanner: turn.timeBanner,
    outlook: turn.outlook,
  }));
  scrollChatBottom();
}

function outlookBiasLabel(bias, text) {
  if (text) return text;
  if (bias === 'bullish') return '偏多观察';
  if (bias === 'bearish') return '偏空观察';
  return '观望';
}

function outlookBiasClass(bias) {
  if (bias === 'bullish') return 'outlook-bullish';
  if (bias === 'bearish') return 'outlook-bearish';
  return 'outlook-neutral';
}

function renderOutlookPanel(outlook) {
  if (!outlook?.length) return null;
  const panel = document.createElement('div');
  panel.className = 'outlook-panel';
  outlook.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'outlook-card';
    if (item.label && item.label !== '综合') {
      const title = document.createElement('div');
      title.className = 'outlook-card-title';
      title.textContent = item.label;
      card.appendChild(title);
    }
    const row = document.createElement('div');
    row.className = 'outlook-row';
    [
      { key: 'short', label: '短期 1～3日', text: item.short_text, bias: item.short_bias },
      { key: 'medium', label: '中期 1～2周', text: item.medium_text, bias: item.medium_bias },
    ].forEach(({ label, text, bias }) => {
      const chip = document.createElement('div');
      chip.className = `outlook-chip ${outlookBiasClass(bias)}`;
      chip.innerHTML = `<span class="outlook-chip-label">${escapeHtml(label)}</span><span class="outlook-chip-value">${escapeHtml(outlookBiasLabel(bias, text))}</span>`;
      row.appendChild(chip);
    });
    card.appendChild(row);
    panel.appendChild(card);
  });
  return panel;
}

function appendBubble(role, text, { noScroll = false, charts = null, timeBanner = null, outlook = null } = {}) {
  if (role !== 'typing') chatEmpty.classList.add('hidden');

  const row = document.createElement('div');
  if (role === 'user') {
    row.className = 'chat-bubble-row chat-row-user';
  } else {
    row.className = 'chat-bubble-row chat-row-ai';
  }

  const wrap = document.createElement('div');
  wrap.className = role === 'user' ? 'chat-wrap-user' : 'chat-wrap-ai';

  if (role === 'assistant' && timeBanner) {
    const tb = document.createElement('div');
    tb.className = 'time-banner';
    tb.textContent = timeBanner;
    wrap.appendChild(tb);
  }

  if (role === 'assistant' && outlook?.length) {
    wrap.appendChild(renderOutlookPanel(outlook));
  }

  const bubble = document.createElement('div');
  if (role === 'user') {
    bubble.className = 'bubble-user';
  } else if (role === 'typing') {
    bubble.className = 'bubble-typing';
    bubble.innerHTML = '<i class="fa fa-circle-o-notch fa-spin mr-2"></i>正在分析，请稍候…';
  } else {
    bubble.className = 'bubble-ai';
  }
  if (role !== 'typing') {
    bubble.style.whiteSpace = 'pre-wrap';
    bubble.textContent = text;
  }

  wrap.appendChild(bubble);

  if (role === 'assistant' && charts?.length) {
    charts.forEach((spec) => {
      const box = document.createElement('div');
      box.className = 'bg-white border border-blockBorder rounded-xl p-3 shadow-flat';
      const title = document.createElement('div');
      title.className = 'text-xs text-textSub mb-2';
      title.textContent = spec.title || '走势图';
      const canvas = document.createElement('canvas');
      canvas.height = 160;
      box.appendChild(title);
      box.appendChild(canvas);
      wrap.appendChild(box);
      renderChart(canvas, spec);
    });
  }

  row.appendChild(wrap);
  chatMessages.appendChild(row);
  if (!noScroll) scrollChatBottom();
  return row;
}

function renderChart(canvas, spec) {
  if (typeof Chart === 'undefined') return;
  const id = spec.id || Math.random().toString(36).slice(2);
  if (chartInstances.has(id)) {
    chartInstances.get(id).destroy();
  }
  const inst = new Chart(canvas, {
    type: spec.type || 'line',
    data: {
      labels: spec.labels || [],
      datasets: (spec.datasets || []).map((d) => ({
        ...d,
        tension: 0.25,
        pointRadius: 0,
        borderWidth: 2,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { maxTicksLimit: 6, font: { size: 10 } } },
        y: {
          min: spec.yMin,
          max: spec.yMax,
          ticks: { font: { size: 10 } },
        },
      },
    },
  });
  chartInstances.set(id, inst);
}

function scrollChatBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showTyping() {
  removeTyping();
  typingEl = appendBubble('typing', '');
}

function removeTyping() {
  if (typingEl) {
    typingEl.remove();
    typingEl = null;
  }
}

async function initChatSession() {
  try {
    const data = await api('/api/session/current');
    currentSessionId = data.session_id;
    chatTurns = data.turns || [];
    loadedSessionId = null;
    renderChat();
    applyContextStatus(data);
  } catch (e) {
    console.warn('initChatSession', e);
  }
}

async function startNewChat() {
  try {
    const data = await api('/api/session/new', { method: 'POST' });
    currentSessionId = data.session_id;
    chatTurns = [];
    loadedSessionId = null;
    applyContextStatus({ context_full: false });
    renderChat();
    switchView('chat');
  } catch (e) {
    toast(e.message || '新建对话失败', 'error');
  }
}

function clearChatIfDeleted(deletedId) {
  if (loadedSessionId && String(loadedSessionId) === String(deletedId)) {
    loadedSessionId = null;
    chatTurns = [];
    renderChat();
  }
  if (currentSessionId && String(currentSessionId) === String(deletedId)) {
    currentSessionId = null;
    chatTurns = [];
    renderChat();
  }
}

async function clearChatAfterHistoryWiped() {
  loadedSessionId = null;
  currentSessionId = null;
  chatTurns = [];
  renderChat();
  await initChatSession();
}

document.querySelectorAll('[data-view]').forEach((item) => {
  item.addEventListener('click', () => {
    if (item.dataset.view === 'chat') startNewChat();
    else switchView(item.dataset.view);
  });
});

document.getElementById('newChatBtn').addEventListener('click', startNewChat);
contextLimitNewChat?.addEventListener('click', startNewChat);

async function refreshStatus() {
  try {
    const s = await api('/api/status');
    llmReady = !!s.llm_ready;
    if (llmReady) {
      statusDot.className = 'w-2 h-2 rounded-full bg-rise';
      statusText.textContent = '大模型已就位';
    } else {
      statusDot.className = 'w-2 h-2 rounded-full bg-fall';
      statusText.textContent = '大模型未配置 · 点击设置';
    }
  } catch {
    llmReady = false;
    statusText.textContent = '服务未连接';
  }
}

document.getElementById('statusLine').addEventListener('click', () => {
  if (!llmReady) openSettings();
});

function openSettings() {
  openModal(settingsModal);
  loadSettingsForm();
}

async function loadSettingsForm() {
  try {
    const s = await api('/api/settings');
    document.getElementById('providerInput').value = s.provider || 'openai';
    document.getElementById('baseUrlInput').value = s.base_url || '';
    document.getElementById('modelInput').value = s.model || 'deepseek-r1';
    document.getElementById('apiKeyInput').value = '';
    document.getElementById('apiKeyHint').textContent = s.has_key
      ? `当前 Key：${s.api_key_masked}（留空则不修改）`
      : '请输入 API Key';
    document.getElementById('settingsStatus').textContent = s.llm_ready
      ? `已就绪 · ${s.model}`
      : '待配置';
  } catch (e) {
    document.getElementById('settingsStatus').textContent = e.message;
  }
}

function formatPct(pct) {
  if (pct == null || Number.isNaN(pct)) return '';
  const sign = pct > 0 ? '+' : '';
  return `${sign}${Number(pct).toFixed(2)}%`;
}

function stockSubline(h) {
  if (h.price_source === 'intraday' && h.live_price != null) {
    const pct = h.live_change_pct;
    const pctClass = pct > 0 ? 'text-rise' : pct < 0 ? 'text-fall' : 'text-textSub';
    const pctTxt = pct != null ? `<span class="${pctClass} ml-1">${formatPct(pct)}</span>` : '';
    const label = escapeHtml(h.price_as_of_label || '盘中');
    return `${label} · <span class="font-medium text-textMain">${Number(h.live_price).toFixed(2)}</span>${pctTxt}`;
  }
  const close = h.last_close != null ? h.last_close.toFixed(2) : '—';
  if (h.has_data) {
    const asOf = escapeHtml(h.data_as_of_label || (h.trade_date ? `${h.trade_date} 收盘` : '—'));
    return `截止 ${asOf} · 收盘 ${close}`;
  }
  return '暂无数据 · 请先同步';
}

function bindStockRowHandlers(container, codes, onChange, { deletable = false } = {}) {
  container.querySelectorAll('.stock-check').forEach((el) => {
    el.addEventListener('change', () => onChange(el.dataset.code, el.checked));
  });
  if (deletable) {
    container.querySelectorAll('.del-stock').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`删除自选股 ${btn.dataset.code}？`)) return;
        await api(`/api/holdings/${btn.dataset.code}`, { method: 'DELETE' });
        selected = selected.filter((c) => c !== btn.dataset.code);
        saveSelected();
        resetSessionQuiet();
        loadHoldings();
        toast('已删除', 'ok');
      });
    });
  }
}

function renderStockRows(container, codes, onChange, { deletable = false } = {}) {
  if (!holdings.length) {
    container.innerHTML = '<div class="text-textPlaceholder text-sm text-center py-8">还没有自选股，在上方输入代码添加</div>';
    return;
  }
  container.innerHTML = holdings.map((h) => {
    const checked = codes.includes(h.code) ? 'checked' : '';
    return `
    <div class="stock-row">
      <input type="checkbox" class="stock-check rounded border-blockBorder text-primary w-4 h-4" data-code="${h.code}" ${checked}>
      <div class="flex-1 min-w-0">
        <div class="font-medium text-sm">${stockRowTitle(h)}</div>
        <div class="text-xs text-textSub">${stockSubline(h)}</div>
      </div>
      ${deletable ? `<button class="btn-3d btn-3d-white text-xs py-1.5 px-2.5 del-stock text-fall border-fall/20" data-code="${h.code}"><i class="fa fa-trash-o"></i></button>` : ''}
    </div>`;
  }).join('');
}

function parseStockCodes(raw) {
  const seen = new Set();
  const out = [];
  for (const part of String(raw || '').split(/[\s,，;；\n]+/)) {
    const code = part.trim().replace(/\.(SH|SZ)$/i, '');
    if (/^\d{6}$/.test(code) && !seen.has(code)) {
      seen.add(code);
      out.push(code);
    }
  }
  return out;
}

function stockRowTitle(h) {
  const name = (h.name || '').trim();
  if (name && name !== h.code) {
    return `${escapeHtml(name)} <span class="text-textPlaceholder font-normal">${escapeHtml(h.code)}</span>`;
  }
  return `<span>${escapeHtml(h.code)}</span>`;
}

function mountStockRows(container, codes, onChange, opts = {}) {
  renderStockRows(container, codes, onChange, opts);
  bindStockRowHandlers(container, codes, onChange, opts);
}

async function addStocksBatch(rawInput) {
  const codes = parseStockCodes(rawInput);
  if (!codes.length) {
    toast('请输入有效的 6 位股票/ETF 代码', 'error');
    return [];
  }
  const res = await api('/api/holdings/batch', {
    method: 'POST',
    body: JSON.stringify({ codes }),
  });
  for (const norm of res.added || []) {
    if (!selected.includes(norm)) selected.push(norm);
  }
  if (res.added?.length) {
    markSelectionExplicit();
    saveSelected();
  }
  await loadHoldings();
  if (res.added?.length) toast(`已添加 ${res.added.length} 只`, 'ok');
  if (res.errors?.length) toast(res.errors.join('；'), 'error');
  return res.added || [];
}

async function addStock(code) {
  const added = await addStocksBatch(code);
  return added[0] || null;
}

function stockListOnChange(code, checked) {
  markSelectionExplicit();
  if (checked && !selected.includes(code)) selected.push(code);
  if (!checked) selected = selected.filter((c) => c !== code);
  saveSelected();
  resetSessionQuiet();
}

async function loadHoldings({ quiet = false } = {}) {
  const body = document.getElementById('stockListBody');
  const onListView = body && !views.stockList.classList.contains('hidden');
  try {
    const data = await api('/api/holdings');
    tradingHours = !!data.trading_hours;
    holdings = data.items || [];
    syncSelectedWithHoldings();
    if (onListView) {
      mountStockRows(body, selected, stockListOnChange, { deletable: true });
    }
    if (stockPickerModal.classList.contains('is-open')) {
      renderPickerList();
    }
    updateSelectedSummary();
  } catch (e) {
    if (!quiet && body && onListView) {
      body.innerHTML = `<div class="text-fall text-sm">${escapeHtml(e.message)}</div>`;
    }
  }
}

function startLivePoll() {
  if (livePollTimer) clearInterval(livePollTimer);
  livePollTimer = setInterval(() => loadHoldings({ quiet: true }), 30000);
}

async function resetSessionQuiet() {
  try {
    const qs = currentSessionId ? `?session_id=${encodeURIComponent(currentSessionId)}` : '';
    await api(`/api/session/reset${qs}`, { method: 'POST' });
  } catch { /* ignore */ }
}

function openStockPicker() {
  pickerDraft = [...selected];
  renderPickerList();
  openModal(stockPickerModal);
}

function renderPickerList() {
  mountStockRows(document.getElementById('pickerList'), pickerDraft, (code, checked) => {
    if (checked && !pickerDraft.includes(code)) pickerDraft.push(code);
    if (!checked) pickerDraft = pickerDraft.filter((c) => c !== code);
    updatePickerCount();
  });
  updatePickerCount();
}

async function loadHistory() {
  const list = document.getElementById('historyList');
  list.innerHTML = '<div class="text-textPlaceholder text-sm">加载中…</div>';
  try {
    const data = await api('/api/history');
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = '<div class="text-textPlaceholder text-sm py-8 text-center">暂无历史</div>';
      return;
    }
    list.innerHTML = items.map((it) => `
      <div class="history-item" data-id="${it.id}">
        <div class="flex justify-between items-start gap-2">
          <div class="flex-1 min-w-0 cursor-pointer js-load-history">
            <div class="text-xs text-textPlaceholder mb-1">${escapeHtml(it.updated_at || it.created_at)} · ${it.turn_count || 0} 轮</div>
            <div class="text-sm font-medium truncate">${escapeHtml(it.title || it.preview)}</div>
            ${it.preview && it.preview !== it.title ? `<div class="text-xs text-textSub mt-1 line-clamp-2">${escapeHtml(it.preview)}</div>` : ''}
          </div>
          <button class="btn-3d btn-3d-white text-xs py-1 px-2 del-history text-fall border-fall/20 flex-shrink-0" data-id="${it.id}" title="删除"><i class="fa fa-trash-o"></i></button>
        </div>
      </div>`).join('');

    list.querySelectorAll('.js-load-history').forEach((el) => {
      el.addEventListener('click', async () => {
        const id = el.closest('.history-item').dataset.id;
        try {
          const session = await api(`/api/session/${id}/activate`, { method: 'POST' });
          currentSessionId = session.session_id;
          loadedSessionId = id;
          chatTurns = session.turns || [];
          renderChat();
          applyContextStatus(session);
          switchView('chat');
        } catch (err) {
          toast(err.message || '加载失败', 'error');
        }
      });
    });

    list.querySelectorAll('.del-history').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm('删除这个对话 session？其中的全部轮次将一并删除。')) return;
        const deletedId = btn.dataset.id;
        try {
          const res = await api(`/api/history/${deletedId}`, { method: 'DELETE' });
          clearChatIfDeleted(deletedId);
          if (res.current) {
            currentSessionId = res.current.session_id;
            if (!chatTurns.length) {
              chatTurns = res.current.turns || [];
              renderChat();
            }
          }
          btn.closest('.history-item')?.remove();
          if (!list.querySelector('.history-item')) {
            list.innerHTML = '<div class="text-textPlaceholder text-sm py-8 text-center">暂无历史</div>';
          }
          toast('已删除', 'ok');
        } catch (err) {
          toast(err.message || '删除失败', 'error');
          loadHistory();
        }
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="text-fall text-sm">${escapeHtml(e.message)}</div>`;
  }
}

function applyContextStatus(data) {
  contextFull = !!data?.context_full;
  const hint = data?.new_chat_hint;
  if (contextFull && hint) {
    contextLimitText.textContent = hint;
    contextLimitBanner.classList.remove('hidden');
    inputBottom.disabled = true;
    sendBtn.disabled = true;
    setSendButtonMode('send');
    document.querySelectorAll('.btn-quick-3d[data-send="1"]').forEach((btn) => {
      btn.disabled = true;
    });
    return;
  }
  contextLimitBanner.classList.add('hidden');
  inputBottom.disabled = false;
  if (!sending) {
    sendBtn.disabled = false;
    setSendButtonMode('send');
  }
  document.querySelectorAll('.btn-quick-3d[data-send="1"]').forEach((btn) => {
    btn.disabled = false;
  });
}

function analyzingHint(elapsedSec) {
  const n = selected.length;
  const head = n > 1 ? `正在分析 ${n} 只标的，` : '正在分析，';
  if (elapsedSec < 12) return `${head}正在拉行情与 K 线…（通常 1～3 分钟，可点停止）`;
  if (elapsedSec < 60) return `${head}已等待 ${elapsedSec} 秒，大模型生成中…`;
  const m = Math.floor(elapsedSec / 60);
  const s = elapsedSec % 60;
  return `${head}已等待 ${m} 分 ${s} 秒，可随时停止后重新提问`;
}

function updateAnalyzingUI() {
  if (!sending) return;
  const elapsed = Math.floor((Date.now() - analyzingStartedAt) / 1000);
  const hint = analyzingHint(elapsed);
  if (analyzingStatusText) analyzingStatusText.textContent = hint;
  if (typingEl) {
    const bubble = typingEl.querySelector('.bubble-typing');
    if (bubble) {
      bubble.innerHTML = `<i class="fa fa-circle-o-notch fa-spin mr-2"></i>${hint}`;
    }
  }
}

function startAnalyzingUI() {
  analyzingStartedAt = Date.now();
  analyzingBanner?.classList.remove('hidden');
  updateAnalyzingUI();
  if (analyzingTimer) clearInterval(analyzingTimer);
  analyzingTimer = setInterval(updateAnalyzingUI, 1000);
}

function stopAnalyzingUI() {
  analyzingBanner?.classList.add('hidden');
  if (analyzingTimer) {
    clearInterval(analyzingTimer);
    analyzingTimer = null;
  }
}

function cancelChatRequest() {
  if (!sending || !chatAbortController) return;
  chatAbortController.abort();
}

function setSendButtonMode(mode) {
  if (mode === 'stop') {
    sendBtn.title = '停止分析';
    sendBtn.setAttribute('aria-label', '停止分析');
    sendBtn.innerHTML = '<i class="fa fa-stop text-lg"></i>';
    sendBtn.classList.add('ring-2', 'ring-fall/40');
  } else {
    sendBtn.title = '发送';
    sendBtn.setAttribute('aria-label', '发送');
    sendBtn.innerHTML = '<i class="fa fa-paper-plane text-lg"></i>';
    sendBtn.classList.remove('ring-2', 'ring-fall/40');
  }
}

function setLoading(on) {
  sending = on;
  sendBtn.disabled = contextFull && !on;
  setSendButtonMode(on ? 'stop' : 'send');
  loadingBar.classList.toggle('hidden', !on);
  if (on) startAnalyzingUI();
  else stopAnalyzingUI();
}

function isMarketQuestion(msg) {
  return /大盘|板块|市场|指数|两市|涨跌家数|主线|龙头|环境/.test(msg);
}

async function sendMessage(text, { market = false } = {}) {
  const msg = (text || inputBottom.value).trim();
  if (!msg) return;
  if (sending) {
    toast('上一条还在分析中，可点「停止分析」或右下角停止按钮', 'info');
    return;
  }

  if (contextFull) {
    toast('本对话上下文已满，请点击「新建对话」后再继续', 'error');
    return;
  }

  if (!llmReady) {
    toast('请先配置 API Key', 'error');
    openSettings();
    return;
  }

  chatTurns.push({ role: 'user', content: msg });
  appendBubble('user', msg);
  inputBottom.value = '';
  inputBottom.style.height = 'auto';

  chatAbortController = new AbortController();
  setLoading(true);
  showTyping();

  try {
    const data = await api('/api/chat', {
      method: 'POST',
      signal: chatAbortController.signal,
      body: JSON.stringify({
        message: msg,
        symbols: selected,
        session_id: currentSessionId,
      }),
    });
    removeTyping();
    if (!data.ok) {
      chatTurns.pop();
      chatMessages.lastElementChild?.remove();
      inputBottom.value = msg;
      toast(data.error || '请求失败', 'error');
      if (data.need_settings) openSettings();
      if (data.need_new_chat) applyContextStatus(data);
      return;
    }
    chatTurns.push({
      role: 'assistant',
      content: data.body || data.reply,
      timeBanner: data.time_banner,
      fullReply: data.reply,
      charts: data.charts || [],
      outlook: data.outlook || [],
    });
    if (data.session_id) currentSessionId = data.session_id;
    loadedSessionId = null;
    applyContextStatus(data);
    appendBubble('assistant', data.body || data.reply, {
      charts: data.charts,
      timeBanner: data.time_banner,
      outlook: data.outlook || [],
    });
  } catch (e) {
    removeTyping();
    if (e.name === 'AbortError') {
      const note = '已停止本次分析。你可以修改问题后重新发送；若刚停不久又收到重复回复，忽略即可。';
      appendBubble('assistant', note);
      chatTurns.push({ role: 'assistant', content: note });
      toast('已停止分析', 'ok');
      return;
    }
    chatTurns.pop();
    chatMessages.lastElementChild?.remove();
    inputBottom.value = msg;
    toast(e.message, 'error');
    appendBubble('assistant', `出错了：${e.message}`);
    chatTurns.push({ role: 'assistant', content: `出错了：${e.message}` });
  } finally {
    chatAbortController = null;
    setLoading(false);
  }
}

sendBtn.addEventListener('click', () => {
  if (sending) cancelChatRequest();
  else sendMessage();
});
cancelChatBtn?.addEventListener('click', cancelChatRequest);
inputBottom.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

inputBottom.addEventListener('input', function autoResize() {
  this.style.height = 'auto';
  this.style.height = `${Math.min(this.scrollHeight, 200)}px`;
});

document.querySelectorAll('.btn-quick-3d[data-fill]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const fill = btn.dataset.fill;
    if (btn.dataset.send === '1') {
      sendMessage(fill, { market: btn.dataset.market === '1' });
      return;
    }
    inputBottom.value = fill;
    inputBottom.focus();
  });
});

document.getElementById('openStockPickerBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  openStockPicker();
});

document.getElementById('pickerSelectAll').addEventListener('click', () => {
  pickerDraft = holdings.map((h) => h.code);
  renderPickerList();
});
document.getElementById('pickerClearAll').addEventListener('click', () => {
  pickerDraft = [];
  renderPickerList();
});

document.getElementById('cancelPicker').addEventListener('click', () => closeModal(stockPickerModal));
stockPickerModal.addEventListener('click', (e) => {
  if (e.target === stockPickerModal) closeModal(stockPickerModal);
});
document.getElementById('confirmPicker').addEventListener('click', async () => {
  markSelectionExplicit();
  selected = [...pickerDraft];
  saveSelected();
  await resetSessionQuiet();
  closeModal(stockPickerModal);
  toast(`已选 ${selected.length} 只`, 'ok');
  inputBottom.focus();
});

document.getElementById('clearHistoryBtn').addEventListener('click', async () => {
  if (!confirm('清空全部对话历史？')) return;
  try {
    await api('/api/history', { method: 'DELETE' });
    clearChatAfterHistoryWiped();
    loadHistory();
    toast('历史已清空', 'ok');
  } catch (err) {
    toast(err.message || '清空失败', 'error');
  }
});

async function runSync(codes) {
  if (!holdings.length) {
    toast('请先添加自选股', 'error');
    return;
  }
  toast('同步中，请稍候…');
  const data = await api('/api/sync', {
    method: 'POST',
    body: JSON.stringify({ symbols: codes || [] }),
  });
  toast(data.message, data.ok ? 'ok' : 'error');
  await loadHoldings();
}

document.getElementById('syncSelectedBtn').addEventListener('click', async () => {
  const btn = document.getElementById('syncSelectedBtn');
  btn.disabled = true;
  try {
    await runSync(selected.length ? selected : null);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('syncAllBtn').addEventListener('click', async () => {
  const btn = document.getElementById('syncAllBtn');
  btn.disabled = true;
  try {
    await runSync(null);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('openSettings').addEventListener('click', openSettings);
document.getElementById('closeSettings').addEventListener('click', () => closeModal(settingsModal));
settingsModal.addEventListener('click', (e) => {
  if (e.target === settingsModal) closeModal(settingsModal);
});

document.getElementById('saveSettings').addEventListener('click', async () => {
  const btn = document.getElementById('saveSettings');
  btn.disabled = true;
  try {
    const data = await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({
        provider: document.getElementById('providerInput').value,
        base_url: document.getElementById('baseUrlInput').value.trim(),
        api_key: document.getElementById('apiKeyInput').value.trim() || null,
        model: document.getElementById('modelInput').value.trim() || 'deepseek-r1',
      }),
    });
    document.getElementById('settingsStatus').textContent = data.message;
    await loadSettingsForm();
    await refreshStatus();
    toast('设置已保存', 'ok');
  } catch (e) {
    document.getElementById('settingsStatus').textContent = e.message;
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('testSettings').addEventListener('click', async () => {
  const btn = document.getElementById('testSettings');
  btn.disabled = true;
  document.getElementById('settingsStatus').textContent = '测试中…';
  try {
    const key = document.getElementById('apiKeyInput').value.trim();
    if (key) {
      await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          provider: document.getElementById('providerInput').value,
          base_url: document.getElementById('baseUrlInput').value.trim(),
          api_key: key,
          model: document.getElementById('modelInput').value.trim() || 'deepseek-r1',
        }),
      });
    }
    const data = await api('/api/settings/test', { method: 'POST' });
    document.getElementById('settingsStatus').textContent = data.message;
    await refreshStatus();
    toast('连接成功', 'ok');
  } catch (e) {
    document.getElementById('settingsStatus').textContent = e.message;
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('providerInput').addEventListener('change', () => {
  const p = document.getElementById('providerInput').value;
  const base = document.getElementById('baseUrlInput');
  if (p === 'bailian') base.value = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
  else if (p === 'ollama') base.value = 'http://127.0.0.1:11434';
});

document.getElementById('inlineAddStock').addEventListener('click', async () => {
  const raw = document.getElementById('inlineStockCode').value.trim();
  if (!raw) {
    toast('请输入股票代码', 'error');
    return;
  }
  const btn = document.getElementById('inlineAddStock');
  btn.disabled = true;
  try {
    await addStocksBatch(raw);
    document.getElementById('inlineStockCode').value = '';
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('inlineStockCode').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('inlineAddStock').click();
});

document.getElementById('listSelectAll').addEventListener('click', () => {
  markSelectionExplicit();
  selected = holdings.map((h) => h.code);
  saveSelected();
  resetSessionQuiet();
  loadHoldings();
});
document.getElementById('listClearAll').addEventListener('click', () => {
  markSelectionExplicit();
  selected = [];
  saveSelected();
  resetSessionQuiet();
  loadHoldings();
});

(async () => {
  await refreshStatus();
  await loadHoldings();
  await initChatSession();
  startLivePoll();
  if (!llmReady) setTimeout(() => toast('请先配置 API Key', 'info'), 600);
})();
