/* Financial Analyzer — pantalla de ticker.
 *
 * Sin framework y sin build: este slice existe para fijar la forma de la API y
 * demostrar que unas miles de velas se mueven bien en un teléfono. Las dos
 * respuestas sobreviven a cualquier cliente que venga después.
 */
'use strict';

const $ = (id) => document.getElementById(id);

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

const state = { ticker: null, days: 252, series: 'rsi', charts: {} };

const RANGES = [
  { label: '1M', days: 22 },
  { label: '3M', days: 66 },
  { label: '6M', days: 130 },
  { label: '1A', days: 252 },
  { label: '5A', days: 1300 },
];

// Series carry a dash pattern as well as a colour: the chart has to read
// without colour vision, and three lines in one hue family would not.
const LINES = {
  close: { color: '#22C55E', dash: null },
  fast:  { color: '#F0B429', dash: [6, 3] },
  slow:  { color: '#94A3B8', dash: [2, 4] },
};

// --- formato ----------------------------------------------------------------

const money = (v) => v == null ? '—'
  : v.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v, d = 2) => v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`;
const num = (v, d = 2) => v == null ? '—' : v.toFixed(d);

function ago(iso) {
  if (!iso) return 'nunca';
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return 'recién';
  if (mins < 60) return `hace ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  return `hace ${days} d`;
}

function showError(message) {
  const box = $('error');
  box.textContent = message;
  box.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => { box.hidden = true; }, 6000);
}

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function reveal(node) {
  if (REDUCED) return;
  node.classList.add('reveal');
  requestAnimationFrame(() => node.classList.add('in'));
}

// --- estado del chequeo -----------------------------------------------------

async function loadStatus() {
  const badge = $('status');
  const text = $('status-text');
  try {
    const h = await api('/api/health');
    // Un número vale lo que vale la corrida que lo produjo, así que la edad del
    // último chequeo está siempre a la vista y no escondida en ajustes.
    if (!h.last_run_at) {
      badge.className = 'status bad';
      text.textContent = 'sin corridas';
    } else if (h.stale || !h.last_run_ok) {
      badge.className = 'status stale';
      text.textContent = ago(h.last_run_at);
    } else {
      badge.className = 'status ok';
      text.textContent = ago(h.last_run_at);
    }
    badge.title = `Motor ${h.engine}, esquema v${h.schema_version}. `
      + `${h.runs_7d} corridas y ${h.fired_7d} disparos en 7 días.`;
  } catch {
    badge.className = 'status bad';
    text.textContent = 'API caída';
  }
}

// --- lista ------------------------------------------------------------------

async function loadList() {
  const rows = await api('/api/tickers');
  const list = $('ticker-list');
  list.innerHTML = '';
  $('list-empty').hidden = rows.length > 0;

  for (const row of rows) {
    const li = document.createElement('li');
    li.className = 'row';
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.setAttribute('aria-label', `${row.ticker}, ver detalle`);
    const alerts = row.active_alerts
      ? `<span>${row.active_alerts} alerta${row.active_alerts > 1 ? 's' : ''}</span>` : '';
    li.innerHTML = `
      <div class="row-top">
        <span class="sym">${row.ticker}</span>
        <span class="row-price tnum">${money(row.price)}</span>
      </div>
      <div class="row-meta">
        <span class="facts">
          <span>${row.sessions} ruedas</span>
          ${row.rsi != null ? `<span>RSI ${num(row.rsi, 0)}</span>` : ''}
          ${alerts}
          ${row.positions ? '<span>en cartera</span>' : ''}
        </span>
        ${row.trend ? `<span class="chip ${row.trend}">${row.trend}</span>` : ''}
      </div>`;
    const open = () => showDetail(row.ticker);
    li.addEventListener('click', open);
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    list.appendChild(li);
    reveal(li);
  }
}

// --- detalle ----------------------------------------------------------------

async function showDetail(ticker) {
  state.ticker = ticker;
  $('view-list').hidden = true;
  $('view-detail').hidden = false;
  $('back').hidden = false;
  $('title').textContent = ticker;
  location.hash = ticker;
  pickTab('chart');

  try {
    const detail = await api(`/api/tickers/${ticker}`);
    renderHead(detail);
    renderStats(detail.indicators);
    renderSeriesPicker(detail.series);
    await Promise.all([loadChart(), loadAlerts()]);
  } catch (e) {
    showError(e.message);
  }
}

function renderHead(detail) {
  $('d-price').textContent = money(detail.price);

  const delta = $('d-delta');
  if (detail.change_pct == null) {
    delta.textContent = '';
    delta.className = 'delta tnum';
  } else {
    const up = detail.change_pct >= 0;
    // Flecha además del color, porque el color no puede ser la única señal.
    delta.textContent = `${up ? '▲' : '▼'} ${signed(detail.change_abs)} (${signed(detail.change_pct)}%)`;
    delta.className = `delta tnum ${up ? 'up' : 'down'}`;
  }

  const trend = detail.indicators.trend;
  $('d-trend').textContent = trend || '';
  $('d-trend').className = `chip solid ${trend || 'neutral'}`;
  $('d-trend').hidden = !trend;

  const c = detail.coverage;
  $('d-coverage').textContent =
    `${c.sessions} ruedas · ${c.first_day} → ${c.last_day} · lectura ${ago(detail.taken_at)}`;
}

// key, label, formatter, tone: 'signed' colours by sign, 'rsi' adds a gauge.
const STATS = [
  ['rsi', 'RSI 14', (v) => num(v, 0), 'rsi'],
  ['vs_sma_slow_pct', 'vs SMA200', (v) => v == null ? '—' : `${signed(v, 1)}%`, 'signed'],
  ['atr_pct', 'ATR', (v) => v == null ? '—' : `${num(v, 1)}%`],
  ['volatility_pct', 'Volatilidad', (v) => v == null ? '—' : `${num(v, 0)}%`],
  ['percent_b', '%B Bollinger', (v) => num(v, 2)],
  ['macd_state', 'MACD', (v) => v || 'sin cruce'],
  ['from_high_pct', 'Desde máx 52s', (v) => v == null ? '—' : `${signed(v, 1)}%`, 'signed'],
  ['from_low_pct', 'Sobre mín 52s', (v) => v == null ? '—' : `${signed(v, 1)}%`, 'signed'],
  ['max_drawdown_pct', 'Drawdown máx', (v) => v == null ? '—' : `${num(v, 0)}%`],
  ['volume_ratio', 'Vol vs 20d', (v) => v == null ? '—' : `${num(v, 2)}x`],
];

function renderStats(ind) {
  const dl = $('stats');
  dl.innerHTML = '';

  for (const [key, label, fmt, tone] of STATS) {
    const value = ind[key];
    const box = document.createElement('div');
    box.className = 'stat';
    let cls = '';
    if (value == null) cls = 'na';
    else if (tone === 'signed') cls = value >= 0 ? 'up' : 'down';
    else if (tone === 'rsi') cls = value >= 70 ? 'down' : value <= 30 ? 'up' : '';

    let gauge = '';
    if (tone === 'rsi' && value != null) {
      // Dónde cae el RSI dentro de 0-100, sin gastar una fila de la grilla.
      gauge = `<div class="gauge"><i style="width:${Math.max(2, Math.min(100, value))}%"></i></div>`;
    }
    box.innerHTML = `<dt>${label}</dt><dd class="${cls}">${fmt(value)}</dd>${gauge}`;
    dl.appendChild(box);
  }

  const rs = ind.relative_strength || {};
  for (const window of ['3m', '12m']) {
    if (rs[window] == null) continue;
    const box = document.createElement('div');
    box.className = 'stat';
    box.innerHTML = `<dt>vs ${ind.benchmark} ${window}</dt>`
      + `<dd class="${rs[window] >= 0 ? 'up' : 'down'}">${signed(rs[window], 1)}p</dd>`;
    dl.appendChild(box);
  }
}

// --- gráfico de precio ------------------------------------------------------

function renderRanges() {
  const box = $('ranges');
  box.innerHTML = '';
  for (const r of RANGES) {
    const button = document.createElement('button');
    button.className = 'range';
    button.type = 'button';
    button.textContent = r.label;
    button.setAttribute('aria-pressed', String(r.days === state.days));
    button.addEventListener('click', () => {
      state.days = r.days;
      renderRanges();
      loadChart().catch((e) => showError(e.message));
    });
    box.appendChild(button);
  }
}

const chartSize = (height) => ({
  width: Math.max(240, $('chart').clientWidth - 8),
  height,
});

const axis = (extra = {}) => ({
  stroke: '#64748B',
  font: '11px "Fira Code", monospace',
  grid: { stroke: '#1E2439', width: 1 },
  ticks: { stroke: '#1E2439', width: 1 },
  ...extra,
});

async function loadChart() {
  const data = await api(`/api/tickers/${state.ticker}/bars?days=${state.days}`);
  const xs = data.day.map((d) => Date.parse(d) / 1000);

  if (state.charts.price) state.charts.price.destroy();
  state.charts.price = new uPlot({
    ...chartSize(240),
    padding: [10, 8, 0, 0],
    legend: { show: false },
    cursor: { drag: { x: true, y: false }, points: { size: 6 } },
    scales: { x: { time: true } },
    axes: [axis(), axis({ size: 50, values: (u, ticks) => ticks.map((v) => v.toFixed(0)) })],
    series: [
      { label: 'Fecha' },
      { label: 'Cierre', stroke: LINES.close.color, width: 1.8,
        fill: 'rgba(34,197,94,.10)' },
      { label: `SMA${data.sma_fast_period}`, stroke: LINES.fast.color, width: 1.3,
        dash: LINES.fast.dash, spanGaps: false },
      { label: `SMA${data.sma_slow_period}`, stroke: LINES.slow.color, width: 1.3,
        dash: LINES.slow.dash, spanGaps: false },
    ],
  }, [xs, data.close, data.sma_fast, data.sma_slow], $('chart'));

  const last = (arr) => {
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return money(arr[i]);
    return '—';
  };
  const swatch = (line) =>
    `<i style="border-top-color:${line.color};border-top-style:${line.dash ? 'dashed' : 'solid'}"></i>`;
  $('chart-legend').innerHTML =
    `<span>${swatch(LINES.close)}cierre ${last(data.close)}</span>`
    + `<span>${swatch(LINES.fast)}SMA${data.sma_fast_period} ${last(data.sma_fast)}</span>`
    + `<span>${swatch(LINES.slow)}SMA${data.sma_slow_period} ${last(data.sma_slow)}</span>`
    + `<span>${data.sessions} ruedas</span>`;
}

// --- historial de un indicador ---------------------------------------------

function renderSeriesPicker(options) {
  const pick = $('series-pick');
  pick.innerHTML = '';
  for (const opt of options) {
    const node = document.createElement('option');
    node.value = opt.name;
    node.textContent = opt.label;
    if (opt.name === state.series) node.selected = true;
    pick.appendChild(node);
  }
  pick.onchange = () => {
    state.series = pick.value;
    loadSeries().catch((e) => showError(e.message));
  };
}

async function loadSeries() {
  const data = await api(`/api/tickers/${state.ticker}/indicators?name=${state.series}`);
  const note = $('series-note');

  if (!data.taken_at.length) {
    if (state.charts.series) { state.charts.series.destroy(); state.charts.series = null; }
    $('chart2').innerHTML = '';
    note.textContent = 'Todavía no hay lecturas guardadas. Cada chequeo agrega un punto.';
    return;
  }
  note.textContent = `${data.taken_at.length} lectura(s) desde `
    + `${data.taken_at[0].slice(0, 16).replace('T', ' ')}.`;

  const xs = data.taken_at.map((t) => Date.parse(t) / 1000);
  if (state.charts.series) state.charts.series.destroy();
  state.charts.series = new uPlot({
    width: Math.max(240, $('chart2').clientWidth - 8),
    height: 210,
    padding: [10, 8, 0, 0],
    legend: { show: false },
    scales: { x: { time: true } },
    axes: [axis(), axis({ size: 50 })],
    series: [
      { label: 'Momento' },
      { label: data.label, stroke: LINES.close.color, width: 2,
        points: { show: data.value.length < 40 } },
    ],
  }, [xs, data.value], $('chart2'));
}

// --- alertas ----------------------------------------------------------------

const OUTCOME = {
  fired: ['critical', 'disparó'],
  quiet: ['neutral', 'sin novedad'],
  cooldown: ['warning', 'en cooldown'],
  error: ['critical', 'error'],
  skipped: ['warning', 'sin datos'],
};

async function loadAlerts() {
  const [alerts, events] = await Promise.all([
    api(`/api/tickers/${state.ticker}/alerts`),
    api(`/api/tickers/${state.ticker}/events`),
  ]);

  const list = $('alert-list');
  list.innerHTML = '';
  if (!alerts.length) {
    list.innerHTML = '<li class="notice">Sin alertas para este ticker.</li>';
  }
  for (const a of alerts) {
    const params = Object.entries(a.params)
      .map(([k, v]) => `<b>${k} ${v}</b>`).join('');
    const [tone, text] = OUTCOME[a.last_outcome] || ['neutral', 'sin evaluar'];
    const li = document.createElement('li');
    li.className = `alert ${a.active ? 'is-on' : 'is-off'}`;
    li.innerHTML = `
      <div class="row-top">
        <span class="alert-name">${a.kind}</span>
        <span class="chip ${a.active ? 'on' : 'off'}">${a.active ? 'activa' : 'inactiva'}</span>
      </div>
      <div class="params">${params}</div>
      <div class="row-meta">
        <span>evaluada ${ago(a.last_evaluated_at)}</span>
        <span class="chip ${tone}">${text}</span>
      </div>`;
    list.appendChild(li);
  }

  const feed = $('event-list');
  feed.innerHTML = '';
  if (!events.length) {
    feed.innerHTML = '<li class="notice">Nunca disparó nada acá.</li>';
  }
  for (const e of events) {
    const li = document.createElement('li');
    li.className = `alert event ${e.severity}`;
    li.innerHTML = `
      <div class="row-top">
        <span class="alert-name">${e.title}</span>
        <span class="chip ${e.severity}">${e.severity}</span>
      </div>
      <div class="row-meta">
        <span>${ago(e.fired_at)}</span>
        <span>${e.delivered.length ? `enviado por ${e.delivered.join(', ')}` : 'no entregado'}</span>
      </div>`;
    feed.appendChild(li);
  }
}

// --- navegación -------------------------------------------------------------

function pickTab(name) {
  for (const tab of document.querySelectorAll('.tab')) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  $('tab-chart').hidden = name !== 'chart';
  $('tab-indicator').hidden = name !== 'indicator';
  $('tab-alerts').hidden = name !== 'alerts';

  if (name === 'indicator' && !state.charts.series) {
    loadSeries().catch((e) => showError(e.message));
  }
  // uPlot se dimensiona al construirse; uno construido oculto mide cero.
  if (name === 'chart' && state.charts.price) state.charts.price.setSize(chartSize(240));
}

function showList() {
  state.ticker = null;
  $('view-detail').hidden = true;
  $('view-list').hidden = false;
  $('back').hidden = true;
  $('title').textContent = 'Tickers';
  location.hash = '';
  for (const key of Object.keys(state.charts)) {
    if (state.charts[key]) state.charts[key].destroy();
    state.charts[key] = null;
  }
  loadList().catch((e) => showError(e.message));
}

document.addEventListener('DOMContentLoaded', () => {
  $('back').addEventListener('click', showList);
  $('status').addEventListener('click', loadStatus);

  for (const tab of document.querySelectorAll('.tab')) {
    tab.addEventListener('click', () => pickTab(tab.dataset.tab));
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.charts.price && !$('tab-chart').hidden) {
        state.charts.price.setSize(chartSize(240));
      }
    }, 120);
  });

  window.addEventListener('hashchange', () => {
    const wanted = location.hash.replace('#', '');
    if (!wanted && state.ticker) showList();
  });

  renderRanges();
  loadStatus();
  setInterval(loadStatus, 60000);

  const wanted = location.hash.replace('#', '');
  loadList()
    .then(() => { if (wanted) showDetail(wanted); })
    .catch((e) => showError(e.message));

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* offline es un plus, no un requisito */ });
  }
});
