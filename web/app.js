/* Financial Analyzer — pantalla de ticker.
 *
 * Sin framework y sin build: este slice existe para fijar la forma de la API y
 * demostrar que unas miles de velas se mueven bien en un teléfono. Las dos
 * respuestas sobreviven a cualquier cliente que venga después.
 */
'use strict';

const $ = (id) => document.getElementById(id);

// The credential lives in localStorage rather than a cookie: there is no
// cookie to forge from another origin, so no CSRF surface on the writes.
const session = {
  key: 'fa.token',
  get token() { try { return localStorage.getItem(this.key) || ''; } catch { return ''; } },
  set token(value) {
    try { value ? localStorage.setItem(this.key, value) : localStorage.removeItem(this.key); }
    catch { /* private mode: the session simply does not survive a reload */ }
  },
  mode: 'open',
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && !path.includes('/auth-mode')) {
    // The credential was rejected, so it is worthless; keeping it would loop.
    // The server's own wording says whether it was wrong or merely expired —
    // reporting "session expired" for a token that was never valid is a lie.
    const body = await response.json().catch(() => ({}));
    const reason = body.detail || 'No se pudo autenticar.';
    session.token = '';
    if (!signingIn) showGate(reason);
    throw new Error(reason);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // FastAPI reports its own schema failures as a list of field errors; the
    // domain reports a sentence written for a person. Show whichever came.
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => `${(d.loc || []).slice(-1)}: ${d.msg}`).join(' · ')
      : body.detail;
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
}

const send = (path, method, body) => api(path, {
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
});

function showOk(message) {
  const box = $('error');
  box.textContent = message;
  box.className = 'toast ok';
  box.hidden = false;
  clearTimeout(showError.timer);
  showError.timer = setTimeout(() => { box.hidden = true; box.className = 'toast'; }, 3000);
}

// True while the login form is being submitted: a 401 during sign-in belongs
// next to the field the user just filled, not as a fresh gate.
let signingIn = false;

const state = {
  view: 'portfolio', ticker: null, days: 252, series: 'rsi',
  period: 'annual', curveDays: 0, charts: {}, kinds: null,
  // Cartera: which list is on screen and where each one is standing.
  portfolioTab: 'holdings', holdingPage: 0, txPage: 0, curveView: 'result',
};

const PAGE_SIZE = 10;

// The equity curve is a calendar series, not a series of sessions, so its
// windows are in days rather than in trading days. 0 means everything there is.
const CURVE_RANGES = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 182 },
  { label: '1A', days: 365 },
  { label: 'Todo', days: 0 },
];

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
// signed() is for percentages, where four figures never come up. A result in
// pesos does reach thousands, and reading it wants the separators.
const signedMoney = (v) => v == null ? '—' : `${v > 0 ? '+' : ''}${money(v)}`;

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
    li.setAttribute('aria-label', `${row.ticker}, ver detalle`);  // setAttribute escapes
    const alerts = row.active_alerts
      ? `<span>${row.active_alerts} alerta${row.active_alerts > 1 ? 's' : ''}</span>` : '';
    li.innerHTML = `
      <div class="row-top">
        <span class="sym">${escapeHtml(row.ticker)}</span>
        <span class="row-price tnum">${money(row.price)}</span>
      </div>
      <div class="row-meta">
        <span class="facts">
          <span>${row.sessions} ruedas</span>
          ${row.rsi != null ? `<span>RSI ${num(row.rsi, 0)}</span>` : ''}
          ${alerts}
          ${row.positions ? '<span>en cartera</span>' : ''}
        </span>
        ${row.trend ? `<span class="chip ${safeClass(row.trend, TREND_CLASSES, 'neutral')}">${
          escapeHtml(row.trend)}</span>` : ''}
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


// --- buscar un ticker que no seguís -----------------------------------------

function showSearching(on, stage = 'Buscando…') {
  $('search-stage').textContent = stage;
  $('search-working').hidden = !on;
}

/** Bring a ticker in, then open it. Looking it up does not follow it. */
async function lookupTicker(symbol) {
  const ticker = symbol.trim().toUpperCase();
  if (!ticker) return;
  if (!/^[A-Z0-9.\-]{1,12}$/.test(ticker)) {
    showError(`"${ticker}" no parece un símbolo.`);
    return;
  }

  showSearching(true, `Buscando ${ticker}…`);
  try {
    const started = await send(`/api/tickers/${ticker}/lookup`, 'POST');
    if (!started.fetching) {
      showSearching(false);
      showDetail(ticker);
      return;
    }
    // The fetch is five years of bars plus the statements, and the endpoint
    // starts answering as soon as the first rows land — opening then shows a
    // ticker with no price yet. Wait for the indicator reading, which is
    // written last and is what the screen is actually made of.
    for (let i = 0; i < 20; i++) {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      const response = await fetch(`/api/tickers/${ticker}`, {
        headers: session.token ? { Authorization: `Bearer ${session.token}` } : {},
      });
      if (!response.ok) continue;
      const detail = await response.json();
      if (detail.price != null) {
        showSearching(false);
        showDetail(ticker);
        return;
      }
    }
    showSearching(false);
    showError(`No se pudo traer ${ticker}. ¿Está bien el símbolo?`);
  } catch (e) {
    showSearching(false);
    showError(e.message);
  }
}

// --- detalle ----------------------------------------------------------------

async function showDetail(ticker) {
  state.ticker = ticker;
  $('view-portfolio').hidden = true;
  $('view-list').hidden = true;
  $('view-detail').hidden = false;
  $('back').hidden = false;
  $('title').textContent = ticker;
  if (location.hash !== `#/t/${ticker}`) location.hash = `#/t/${ticker}`;
  pickTab('chart');

  try {
    await paintDetail(ticker);
  } catch (e) {
    showError(e.message);
  }
}

async function paintDetail(ticker) {
  const detail = await api(`/api/tickers/${ticker}`);
  renderHead(detail);
  renderStats(detail.indicators);
  renderSeriesPicker(detail.series);
  await Promise.all([loadChart(), loadAlerts()]);
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
  $('d-trend').className = `chip solid ${safeClass(trend, TREND_CLASSES, 'neutral')}`;
  $('d-trend').hidden = !trend;

  // Looking a ticker up does not add it to the list, so the screen says so
  // rather than leaving the person wondering why it is not there afterwards.
  $('d-unfollowed').hidden = detail.followed !== false;

  const c = detail.coverage;
  // textContent, so no escaping needed and none pretended.
  $('d-coverage').textContent =
    `${c.sessions} ruedas · ${c.first_day} → ${c.last_day} · lectura ${ago(detail.taken_at)}`;
  // Remembered so the poll below can tell a real refresh from a re-render.
  state.lastReading = detail.taken_at;
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
    box.innerHTML = `<dt>${label}</dt><dd class="${cls}">${escapeHtml(fmt(value))}</dd>${gauge}`;
    dl.appendChild(box);
  }

  const rs = ind.relative_strength || {};
  for (const window of ['3m', '12m']) {
    if (rs[window] == null) continue;
    const box = document.createElement('div');
    box.className = 'stat';
    box.innerHTML = `<dt>vs ${escapeHtml(ind.benchmark)} ${window}</dt>`
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
      .map(([k, v]) => `<b>${escapeHtml(k)} ${escapeHtml(v)}</b>`).join('');
    const [tone, text] = OUTCOME[a.last_outcome] || ['neutral', 'sin evaluar'];
    const li = document.createElement('li');
    li.className = `alert ${a.active ? 'is-on' : 'is-off'}`;
    li.innerHTML = `
      <div class="row-top">
        <span class="alert-name">${escapeHtml(a.kind)}</span>
        <span class="chip ${a.active ? 'on' : 'off'}">${a.active ? 'activa' : 'inactiva'}</span>
      </div>
      <div class="params">${params}</div>
      <div class="row-meta">
        <span>evaluada ${ago(a.last_evaluated_at)}</span>
        <span class="chip ${tone}">${text}</span>
      </div>
      <div class="alert-actions">
        <button class="mini" data-act="toggle">${a.active ? 'Silenciar' : 'Activar'}</button>
        <button class="mini danger" data-act="delete" aria-label="Borrar alerta">
          <svg aria-hidden="true"><use href="#i-trash"/></svg>
        </button>
      </div>`;
    li.querySelector('[data-act="toggle"]').addEventListener('click', async () => {
      try {
        await send(`/api/alerts/${a.id}`, 'PATCH', { active: !a.active });
        await loadAlerts();
      } catch (e) { showError(e.message); }
    });
    li.querySelector('[data-act="delete"]').addEventListener('click', async () => {
      // Soft delete, so the confirmation says what actually happens to history.
      if (!confirm(`¿Borrar la alerta ${a.kind}? Los disparos que ya tuvo se conservan.`)) return;
      try {
        await send(`/api/alerts/${a.id}`, 'DELETE');
        showOk('Alerta borrada.');
        await loadAlerts();
      } catch (e) { showError(e.message); }
    });
    list.appendChild(li);
  }

  const feed = $('event-list');
  feed.innerHTML = '';
  if (!events.length) {
    feed.innerHTML = '<li class="notice">Nunca disparó nada acá.</li>';
  }
  for (const e of events) {
    const li = document.createElement('li');
    li.className = `alert event ${safeClass(e.severity, SEVERITY_CLASSES, 'info')}`;
    li.innerHTML = `
      <div class="row-top">
        <span class="alert-name">${escapeHtml(e.title)}</span>
        <span class="chip ${safeClass(e.severity, SEVERITY_CLASSES, 'info')}">${
          escapeHtml(e.severity)}</span>
      </div>
      <div class="row-meta">
        <span>${ago(e.fired_at)}</span>
        <span>${e.delivered.length
          ? `enviado por ${escapeHtml(e.delivered.join(', '))}` : 'no entregado'}</span>
      </div>
      ${e.acknowledged_at ? '' : '<div class="alert-actions">'
        + '<button class="mini" data-act="ack">Marcar visto</button></div>'}`;
    const ack = li.querySelector('[data-act="ack"]');
    if (ack) {
      ack.addEventListener('click', async () => {
        try {
          await send(`/api/events/${e.id}/ack`, 'POST');
          await loadAlerts();
        } catch (err) { showError(err.message); }
      });
    }
    feed.appendChild(li);
  }
}




// --- puerta de acceso -------------------------------------------------------

async function detectMode() {
  const body = await api('/api/auth-mode');
  session.mode = body.mode;
  session.supabaseUrl = body.supabase_url;
  session.supabaseKey = body.supabase_anon_key;
  return body;
}

function gateFail(message) {
  const box = $('gate-error');
  box.textContent = message;
  box.hidden = false;
}

function showGate(message = '') {
  $('gate').hidden = false;
  $('gate-error').hidden = !message;
  if (message) $('gate-error').textContent = message;
  const fields = $('gate-fields');
  fields.innerHTML = '';

  if (session.mode === 'supabase') {
    $('gate-lead').textContent = 'Entrá con tu cuenta.';
    fields.appendChild(field('email', 'Email', { type: 'email', required: true }));
    fields.appendChild(field('password', 'Contraseña', { type: 'password', required: true }));
  } else {
    $('gate-lead').textContent =
      'Este servidor pide un token. Está en el .env, en FA_API_TOKEN.';
    fields.appendChild(field('token', 'Token', { type: 'password', required: true }));
  }
  const first = fields.querySelector('input');
  if (first) first.focus();
}

function hideGate() {
  $('gate').hidden = true;
  $('logout').hidden = session.mode === 'open';
}

async function signIn() {
  signingIn = true;
  try {
    await attemptSignIn();
  } finally {
    signingIn = false;
  }
}

async function attemptSignIn() {
  const values = {};
  for (const el of $('gate-fields').querySelectorAll('input')) values[el.name] = el.value.trim();

  if (session.mode === 'supabase') {
    // Straight to Supabase's token endpoint. Pulling in their SDK to POST one
    // form would be the only thing it did.
    const response = await fetch(
      `${session.supabaseUrl}/auth/v1/token?grant_type=password`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', apikey: session.supabaseKey },
        body: JSON.stringify({ email: values.email, password: values.password }),
      },
    );
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error_description || body.msg || 'No se pudo entrar.');
    session.token = body.access_token;
  } else {
    session.token = values.token;
  }

  // Prove the credential before dismissing the gate, so a bad one fails here
  // instead of on the first screen the user sees.
  await api('/api/session');
}

async function start() {
  await detectMode();
  if (session.mode !== 'open' && !session.token) { showGate(); return; }
  try {
    await api('/api/session');
  } catch {
    return;  // api() already opened the gate
  }
  hideGate();
  renderRanges();
  loadStatus();
  route();
}

function signOut() {
  session.token = '';
  showGate();
}

// --- hoja inferior ----------------------------------------------------------

const sheet = {
  el: null,
  open(title, fields, onSubmit) {
    this.el = $('sheet');
    $('sheet-title').textContent = title;
    $('sheet-error').hidden = true;
    $('sheet-body').innerHTML = '';
    for (const field of fields) $('sheet-body').appendChild(field);
    this.onSubmit = onSubmit;
    this.el.showModal();
    const first = $('sheet-body').querySelector('input, select, textarea');
    if (first) first.focus();
  },
  close() { if (this.el) this.el.close(); },
  fail(message) {
    const box = $('sheet-error');
    box.textContent = message;
    box.hidden = false;
    box.scrollIntoView({ block: 'nearest' });
  },
};

/** One labelled control. The label is always visible, never a placeholder. */
function field(name, label, { type = 'text', value = '', options = null, hint = '', step, min, required = false } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'f';
  const id = `f-${name}`;
  // The value can be a ticker or a note the user typed, and it lands inside a
  // quoted attribute, so it is escaped like everything else. Option labels come
  // from the alert catalogue, which is ours, but escaping them costs nothing
  // and means nobody has to check where a label came from.
  const v = escapeHtml(value);
  const control = options
    ? `<select id="${id}" name="${name}">${options.map((o) => {
        const [raw, text] = Array.isArray(o) ? o : [o, o];
        const selected = String(raw) === String(value) ? ' selected' : '';
        return `<option value="${escapeHtml(raw)}"${selected}>${escapeHtml(text)}</option>`;
      }).join('')}</select>`
    : type === 'textarea'
      ? `<textarea id="${id}" name="${name}">${v}</textarea>`
      : `<input id="${id}" name="${name}" type="${escapeHtml(type)}" value="${v}"
           ${step ? `step="${escapeHtml(step)}"` : ''} ${min != null ? `min="${escapeHtml(min)}"` : ''}
           ${required ? 'required' : ''} ${type === 'text' ? 'autocapitalize="characters"' : ''}>`;
  wrap.innerHTML = `<label for="${id}">${escapeHtml(label)}</label>${control}`
    + (hint ? `<span class="hint">${escapeHtml(hint)}</span>` : '');
  return wrap;
}


function formValues() {
  const out = {};
  for (const el of $('sheet-body').querySelectorAll('input, select, textarea')) {
    out[el.name] = el.value.trim();
  }
  return out;
}

// --- alta de alerta ---------------------------------------------------------

// Los parámetros viajan con su nombre técnico, que es correcto en la base y
// áspero en un formulario. La traducción es presentación y vive acá.
const PARAM_LABEL = {
  pct: 'Porcentaje', price: 'Precio', days: 'Días', months: 'Meses',
  fast: 'Media rápida', slow: 'Media lenta', signal: 'Señal', period: 'Período',
  overbought: 'Sobrecompra', oversold: 'Sobreventa', reference: 'Referencia',
  lookback_days: 'Ventana (días)', multiple: 'Múltiplo de ATR', ratio: 'Ratio',
  tolerance_pct: 'Tolerancia %', window: 'Ventana', direction: 'Dirección',
};

const CHOICE_LABEL = {
  buy: 'precio de compra', baseline: 'precio de hoy',
  any: 'cualquiera', above: 'hacia arriba', below: 'hacia abajo',
};

const paramLabel = (key) => PARAM_LABEL[key] || key.replace(/_/g, ' ');
const choiceLabel = (value) => [value, CHOICE_LABEL[value] || value];

async function alertKinds() {
  if (!state.kinds) state.kinds = await api('/api/alert-kinds');
  return state.kinds;
}

async function openAlertForm(ticker) {
  const catalogue = await alertKinds();
  const picker = field('kind', 'Tipo de alerta', {
    options: catalogue.map((k) => [k.key, k.label]),
    value: 'rsi',
  });
  const describe = document.createElement('p');
  describe.className = 'hint';
  const params = document.createElement('div');
  params.className = 'params-box';
  const cooldown = field('cooldown_hours', 'Cooldown (horas)', {
    type: 'number', value: '24', min: 0,
    hint: 'Tiempo mínimo entre dos avisos de esta alerta.',
  });
  const note = field('note', 'Nota', { type: 'textarea' });

  // The parameter fields are built from the catalogue, so a new alert kind
  // shows up in the app without the client knowing anything about it.
  const paint = () => {
    const kind = catalogue.find((k) => k.key === picker.querySelector('select').value);
    describe.textContent = kind.description;
    params.innerHTML = '';
    for (const [key, value] of Object.entries(kind.defaults)) {
      const choices = (kind.choices || {})[key];
      params.appendChild(field(`p_${key}`, paramLabel(key), {
        value,
        options: choices ? choices.map(choiceLabel) : null,
        type: choices ? 'text' : 'number',
        step: 'any',
      }));
    }
    if (kind.requires_position) {
      describe.textContent += ' Necesita una posición cargada.';
    }
  };
  picker.querySelector('select').addEventListener('change', paint);
  paint();

  sheet.open(`Nueva alerta · ${ticker}`, [picker, describe, params, cooldown, note], async () => {
    const values = formValues();
    const body = { kind: values.kind, params: {}, note: values.note };
    for (const [key, value] of Object.entries(values)) {
      if (!key.startsWith('p_') || value === '') continue;
      const asNumber = Number(value);
      body.params[key.slice(2)] = Number.isNaN(asNumber) ? value : asNumber;
    }
    if (values.cooldown_hours !== '') body.cooldown_hours = Number(values.cooldown_hours);
    await send(`/api/tickers/${ticker}/alerts`, 'POST', body);
    sheet.close();
    showOk('Alerta creada.');
    await loadAlerts();
  });
}

// --- alta de movimiento -----------------------------------------------------

const TX_KINDS = [
  ['buy', 'Compra'], ['sell', 'Venta'], ['dividend', 'Dividendo'],
  ['split', 'Split'], ['fee', 'Comisión'],
  ['deposit', 'Depósito'], ['withdraw', 'Retiro'],
];

// Money entering or leaving the account rather than moving between cash and
// shares. They have no ticker.
const CASH_KINDS = ['deposit', 'withdraw'];

function openTransactionForm(ticker = '', existing = null) {
  const today = new Date().toISOString().slice(0, 10);
  const v = existing || {};
  const kind = field('kind', 'Tipo', { options: TX_KINDS, value: v.kind || 'buy' });
  const symbol = field('ticker', 'Ticker', { value: v.ticker || ticker, required: true });
  const when = field('trade_date', 'Fecha', {
    type: 'date', value: v.trade_date || today, required: true,
  });
  const quantity = field('quantity', 'Cantidad', {
    type: 'number', step: 'any', min: 0, value: v.quantity ?? '',
  });
  const price = field('price', 'Precio por acción', {
    type: 'number', step: 'any', min: 0, value: v.price ?? '',
  });
  const amount = field('amount', 'Monto total', {
    type: 'number', step: 'any', value: v.amount ?? '',
  });
  const ratio = field('ratio', 'Ratio del split', {
    type: 'number', step: 'any', min: 0, value: v.ratio ?? '',
    hint: '4 significa 4 acciones nuevas por cada una vieja.',
  });
  const fees = field('fees', 'Comisiones', {
    type: 'number', step: 'any', min: 0, value: v.fees ?? '0',
  });

  // A hidden control still takes part in validation, and the browser refuses to
  // submit a form whose invalid field it cannot focus — so visibility and
  // requiredness have to move together. Hiding the ticker for a deposit while
  // leaving it required silently blocked the save with nothing on screen.
  const toggle = (wrap, visible, required = false) => {
    wrap.hidden = !visible;
    const control = wrap.querySelector('input, select, textarea');
    if (control) control.required = visible && required;
  };

  // Progressive disclosure: a split has no price and a cash dividend has no
  // share count. Showing every field for every kind invites wrong entries.
  const paint = () => {
    const value = kind.querySelector('select').value;
    const shares = value === 'buy' || value === 'sell';
    const cash = CASH_KINDS.includes(value);
    toggle(symbol, !cash, true);
    toggle(quantity, shares);
    toggle(price, shares);
    toggle(ratio, value === 'split');
    toggle(amount, value === 'dividend' || value === 'fee' || cash, cash);
    toggle(fees, value !== 'split');
  };
  kind.querySelector('select').addEventListener('change', paint);
  paint();

  const title = existing ? 'Corregir movimiento' : 'Cargar movimiento';
  const fields = [kind, symbol, when, quantity, price, ratio, amount, fees];
  if (existing) {
    // The ledger keeps the original; saying so is the difference between an
    // edit the user can trust and one they wonder about later.
    const note = document.createElement('p');
    note.className = 'hint';
    note.textContent = 'Se guarda como corrección: la entrada original queda registrada.';
    fields.push(note);
  }

  sheet.open(title, fields, async () => {
      const values = formValues();
      const body = {
        kind: values.kind,
        trade_date: values.trade_date,
        fees: values.fees === '' ? 0 : Number(values.fees),
      };
      // A deposit carries no symbol, and sending an empty one is not the same
      // as sending none.
      if (!CASH_KINDS.includes(values.kind) && values.ticker) {
        body.ticker = values.ticker.toUpperCase();
      }
      for (const key of ['quantity', 'price', 'amount', 'ratio']) {
        if (values[key] !== '' && values[key] !== undefined) body[key] = Number(values[key]);
      }
      if (existing) {
        await send(`/api/portfolio/transactions/${existing.id}`, 'PATCH', body);
        sheet.close();
        await loadPortfolio();
        showOk('Movimiento corregido.');
        return;
      }
      const created = await send('/api/portfolio/transactions', 'POST', body);
      sheet.close();
      // A new entry is the newest, so it is on the first page.
      state.txPage = 0;
      await loadPortfolio();
      if (created.fetching_prices) {
        // The server went to get them; say so and come back when they land,
        // instead of leaving a holding that reads "sin precio" with no
        // explanation of whose job it is to fix that.
        showOk(`Movimiento cargado. Trayendo el historial de ${created.ticker}…`);
        awaitPrices(created.ticker);
      } else {
        showOk('Movimiento cargado.');
      }
    });
}

// --- cartera ----------------------------------------------------------------


/** Two arrows and a position. Renders nothing when everything fits on one page. */
function renderPager(node, { page, size, total, onGo }) {
  const pages = Math.max(1, Math.ceil(total / size));
  node.hidden = pages <= 1;
  node.innerHTML = '';
  if (pages <= 1) return;

  const first = page * size + 1;
  const last = Math.min(total, (page + 1) * size);

  const arrow = (label, target, enabled, aria) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.disabled = !enabled;
    button.setAttribute('aria-label', aria);
    if (enabled) button.addEventListener('click', () => onGo(target));
    return button;
  };

  const position = document.createElement('span');
  position.className = 'position';
  position.textContent = `${first}–${last} de ${total}`;

  node.append(
    arrow('‹', page - 1, page > 0, 'Página anterior'),
    position,
    arrow('›', page + 1, page < pages - 1, 'Página siguiente'),
  );
}

function pickPortfolioTab(name) {
  state.portfolioTab = name;
  for (const tab of document.querySelectorAll('.tab[data-ptab]')) {
    tab.setAttribute('aria-selected', String(tab.dataset.ptab === name));
  }
  $('ptab-holdings').hidden = name !== 'holdings';
  $('ptab-movements').hidden = name !== 'movements';
}

/** Poll until the first fetch of a ticker lands, then refresh. */
async function awaitPrices(ticker, attempts = 12) {
  for (let i = 0; i < attempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    try {
      const rows = await api('/api/tickers');
      const row = rows.find((r) => r.ticker === ticker);
      if (row && row.sessions) {
        await loadPortfolio();
        showOk(`${ticker}: ${row.sessions} ruedas cargadas.`);
        return;
      }
    } catch {
      return;  // the gate or the toast already said what happened
    }
  }
  showError(`No se pudo traer el historial de ${ticker}. Revisá los proveedores.`);
}


const KIND_LABEL = {
  buy: 'compra', sell: 'venta', dividend: 'dividendo', split: 'split', fee: 'comisión',
  deposit: 'depósito', withdraw: 'retiro',
};

async function loadPortfolio() {
  const [p, curve, txs] = await Promise.all([
    api('/api/portfolio'),
    api(`/api/portfolio/history?days=${state.curveDays || 3650}`),
    api(`/api/portfolio/transactions?limit=${PAGE_SIZE}&offset=${state.txPage * PAGE_SIZE}`),
  ]);

  $('p-empty').hidden = p.count > 0 || txs.length > 0;
  renderPortfolioHead(p);
  renderTotals(p);
  renderHoldings(p);
  renderCurve(curve);
  renderLedger(txs);
}

function renderPortfolioHead(p) {
  $('p-value').textContent = money(p.market_value);

  const delta = $('p-delta');
  if (p.pnl_pct == null) {
    delta.textContent = '';
    delta.className = 'delta tnum';
  } else {
    const up = p.pnl_abs >= 0;
    delta.textContent = `${up ? '▲' : '▼'} ${signed(p.pnl_abs)} (${signed(p.pnl_pct)}%)`;
    delta.className = `delta tnum ${up ? 'up' : 'down'}`;
  }

  $('p-basis').textContent = p.count
    ? `${p.count} tenencia${p.count > 1 ? 's' : ''} · costo ${money(p.cost_basis)} ${p.currency}`
    : '';

  // Being told a total is short a position matters more than the total.
  const notes = [];
  if (p.unpriced.length) {
    notes.push(`Sin precio guardado para ${escapeHtml(p.unpriced.join(', '))}: no entran en el total. `
      + 'Corré check-alerts para traer sus velas.');
  }
  if ((p.foreign_currency || []).length) {
    const which = p.foreign_currency
      .map((f) => `${escapeHtml(f.ticker)} (${escapeHtml(f.currency)})`).join(', ');
    notes.push(`La cartera se valúa en ${p.base_currency}, y ${which} no cotiza en esa `
      + 'moneda: queda afuera del total.');
  }
  const warn = $('p-unpriced');
  warn.hidden = notes.length === 0;
  warn.textContent = notes.join(' ');
}

// The cash figure is the running sum of what the ledger did to the money side.
// Negative means it is sitting in shares, which is not a loss — so it is shown
// as what it is, without the red a signed tone would paint on it.
// Without deposits recorded, cash is a net rather than a balance and goes
// negative while the money is in shares — calling that "caja" would be wrong.
const cashLabel = (value) => (value < 0 ? 'Neto invertido' : 'Caja');

const TOTALS = [
  ['cost_basis', 'Costo', (v) => money(v)],
  ['total_result', 'Resultado', (v) => money(v), 'signed'],
  ['net_worth', 'Patrimonio', (v) => money(v)],
  ['contributed', 'Aportado', (v) => money(v)],
  ['cash', cashLabel, (v) => money(Math.abs(v))],
  ['realized_pnl', 'Realizado', (v) => money(v), 'signed'],
  ['dividends', 'Dividendos', (v) => money(v)],
  ['fees', 'Comisiones', (v) => money(v)],
];

function renderTotals(p) {
  const dl = $('p-totals');
  dl.innerHTML = '';
  for (const [key, label, fmt, tone] of TOTALS) {
    const value = p[key];
    // Cash is meaningful at zero and negative; the rest are noise when empty.
    if (!value && !['cost_basis', 'cash', 'total_result'].includes(key)) continue;
    const box = document.createElement('div');
    box.className = 'stat';
    const cls = tone === 'signed' ? (value >= 0 ? 'up' : 'down') : '';
    const name = typeof label === 'function' ? label(value) : label;
    box.innerHTML = `<dt>${escapeHtml(name)}</dt><dd class="${cls}">${escapeHtml(fmt(value))}</dd>`;
    dl.appendChild(box);
  }
}

function renderHoldings(p) {
  const list = $('holding-list');
  list.innerHTML = '';

  // The whole set arrives in one response — the weights are shares of it — so
  // the paging is done here rather than with another round trip.
  const pages = Math.max(1, Math.ceil(p.holdings.length / PAGE_SIZE));
  if (state.holdingPage >= pages) state.holdingPage = pages - 1;
  const start = state.holdingPage * PAGE_SIZE;

  renderPager($('holding-pager'), {
    page: state.holdingPage,
    size: PAGE_SIZE,
    total: p.holdings.length,
    onGo: (page) => { state.holdingPage = page; renderHoldings(p); },
  });

  for (const h of p.holdings.slice(start, start + PAGE_SIZE)) {
    const li = document.createElement('li');
    li.className = 'holding';
    li.tabIndex = 0;
    li.setAttribute('role', 'button');
    li.setAttribute('aria-label', `${h.ticker}, ver detalle`);

    const pnlCls = h.pnl_pct == null ? '' : (h.pnl_pct >= 0 ? 'up' : 'down');
    const day = h.day_change_pct == null ? ''
      : `<span class="pnl ${h.day_change_pct >= 0 ? 'up' : 'down'}">${signed(h.day_change_pct)}% hoy</span>`;

    li.innerHTML = `
      <div class="row-top">
        <span class="sym">${escapeHtml(h.ticker)}</span>
        <span class="value tnum">${h.value == null ? 'sin precio' : money(h.value)}</span>
      </div>
      <div class="row-meta">
        <span class="qty">${num(h.quantity, h.quantity % 1 ? 4 : 0)} × ${money(h.price)}
          · costo ${money(h.average_cost)}</span>
        <span class="pnl ${pnlCls}">${h.pnl_pct == null ? '—' : `${signed(h.pnl_pct)}%`}</span>
      </div>
      <div class="weight">
        <span class="weight-bar"><i style="width:${Math.max(2, h.weight_pct || 0)}%"></i></span>
        <span class="weight-pct">${h.weight_pct == null ? '—' : `${num(h.weight_pct, 0)}%`}</span>
      </div>
      <div class="row-meta">
        <span>${day}</span>
        <button class="mini danger" data-act="remove"
                aria-label="Quitar ${escapeHtml(h.ticker)} de la cartera">
          <svg aria-hidden="true"><use href="#i-trash"/></svg>
        </button>
      </div>`;

    li.querySelector('[data-act="remove"]').addEventListener('click', async (event) => {
      // Inside a card that opens the ticker: the click must not do both.
      event.stopPropagation();
      const count = h.entries === 1 ? 'su movimiento' : `sus ${h.entries} movimientos`;
      if (!confirm(
        `¿Quitar ${h.ticker} de la cartera?\n\n`
        + `Se retiran ${count}. Quedan guardados en el historial y las velas del `
        + 'papel no se borran.'
      )) return;
      try {
        await send(`/api/portfolio/holdings/${h.ticker}`, 'DELETE');
        showOk(`${h.ticker} salió de la cartera.`);
        state.txPage = 0;
        await loadPortfolio();
      } catch (e) { showError(e.message); }
    });

    const open = () => showDetail(h.ticker);
    li.addEventListener('click', open);
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
    list.appendChild(li);
  }
}

// Two readings of the same ledger. They live on very different scales — one is
// tens of thousands, the other the few hundred you are up or down — so they are
// separate views rather than two lines forced onto one axis.
const CURVE_VIEWS = [
  { key: 'result', label: 'Resultado' },
  { key: 'total', label: 'Patrimonio' },
  { key: 'holdings', label: 'Tenencias' },
];

const swatch = (line) =>
  `<i style="border-top-color:${line.color};border-top-style:${line.dash ? 'dashed' : 'solid'}"></i>`;

function renderCurveViews() {
  const box = $('curve-view');
  box.innerHTML = '';
  for (const view of CURVE_VIEWS) {
    const button = document.createElement('button');
    button.className = 'range';
    button.type = 'button';
    button.textContent = view.label;
    button.setAttribute('aria-pressed', String(view.key === state.curveView));
    button.addEventListener('click', () => {
      state.curveView = view.key;
      loadPortfolio().catch((e) => showError(e.message));
    });
    box.appendChild(button);
  }
}

function renderCurveRanges() {
  const box = $('curve-ranges');
  box.innerHTML = '';
  for (const range of CURVE_RANGES) {
    const button = document.createElement('button');
    button.className = 'range';
    button.type = 'button';
    button.textContent = range.label;
    button.setAttribute('aria-pressed', String(range.days === state.curveDays));
    button.addEventListener('click', () => {
      state.curveDays = range.days;
      loadPortfolio().catch((e) => showError(e.message));
    });
    box.appendChild(button);
  }
}

function renderCurve(curve) {
  const box = $('p-curve-box');
  const legend = $('p-curve-legend');
  renderCurveViews();
  renderCurveRanges();

  // One point is not a curve; saying so beats drawing a dot on an empty axis.
  if (curve.sessions < 2) {
    renderCurveViews();
    renderCurveRanges();
    if (state.charts.curve) { state.charts.curve.destroy(); state.charts.curve = null; }
    $('p-curve').innerHTML = '';
    box.hidden = curve.sessions === 0;
    legend.innerHTML = curve.sessions
      ? '<span>Una sola valuación guardada. La curva aparece con la segunda.</span>' : '';
    return;
  }
  box.hidden = false;

  const xs = curve.day.map((d) => Date.parse(d) / 1000);
  const last = (arr) => (arr && arr.length ? arr[arr.length - 1] : 0);

  // Three questions, three reference lines. Each view is only readable against
  // the right baseline: the result against zero, what the account is worth
  // against what was put into it, the shares against what they cost.
  const VIEWS = {
    // The result crosses zero, so it is drawn against a zero reference and a
    // fill would be a lie — a filled area below zero reads as a quantity.
    result: {
      series: [
        { label: 'Resultado', stroke: LINES.close.color, width: 2 },
        { label: 'Cero', stroke: LINES.slow.color, width: 1, dash: [2, 4] },
      ],
      data: [curve.result, curve.result.map(() => 0)],
      legend: `<span>${swatch(LINES.close)}resultado ${signedMoney(last(curve.result))}</span>`
        + `<span>${swatch(LINES.slow)}cero</span>`
        + '<span class="hint-inline">lo que produjo el mercado: depositar no lo mueve</span>',
    },
    total: {
      series: [
        { label: 'Patrimonio', stroke: LINES.close.color, width: 2, fill: 'rgba(34,197,94,.10)' },
        { label: 'Aportado', stroke: LINES.slow.color, width: 1.4, dash: [7, 4] },
      ],
      data: [curve.total, curve.contributed],
      legend: `<span>${swatch(LINES.close)}patrimonio ${money(last(curve.total))}</span>`
        + `<span>${swatch(LINES.slow)}aportado</span>`
        + '<span class="hint-inline">acciones más caja: la distancia al aportado es la ganancia</span>',
    },
    holdings: {
      series: [
        { label: 'Valor', stroke: LINES.close.color, width: 2, fill: 'rgba(34,197,94,.10)' },
        { label: 'Costo', stroke: LINES.slow.color, width: 1.4, dash: [7, 4] },
      ],
      data: [curve.market_value, curve.cost_basis],
      legend: `<span>${swatch(LINES.close)}valor</span>`
        + `<span>${swatch(LINES.slow)}costo</span>`
        + '<span class="hint-inline">sólo las acciones: vender lo baja</span>',
    },
  };
  const view = VIEWS[state.curveView] || VIEWS.result;
  const series = [{ label: 'Día' }, ...view.series];
  const data = [xs, ...view.data];

  if (state.charts.curve) state.charts.curve.destroy();
  state.charts.curve = new uPlot({
    width: Math.max(240, $('p-curve').clientWidth - 8),
    height: 190,
    padding: [10, 8, 0, 0],
    legend: { show: false },
    cursor: { drag: { x: true, y: false } },
    scales: { x: { time: true } },
    axes: [axis(), axis({ size: 58, values: (u, ticks) => ticks.map((v) => v.toFixed(0)) })],
    series,
  }, data, $('p-curve'));

  // Dragging across the plot zooms in; without being told, the way back out is
  // undiscoverable — so the legend says it, next to the buttons that do it too.
  legend.innerHTML = view.legend
    + `<span>${escapeHtml(curve.first_day || '')} → ${escapeHtml(curve.last_day || '')}</span>`
    + '<span class="hint-inline">arrastrá para acercar · doble clic para volver</span>';
}

function renderLedger(ledgerPage) {
  const txs = ledgerPage.entries;
  const list = $('tx-list');
  list.innerHTML = '';

  $('tx-count').textContent = ledgerPage.total === 1
    ? '1 movimiento' : `${ledgerPage.total} movimientos`;

  renderPager($('tx-pager'), {
    page: state.txPage,
    size: PAGE_SIZE,
    total: ledgerPage.total,
    onGo: (page) => {
      state.txPage = page;
      loadPortfolio().catch((e) => showError(e.message));
    },
  });

  if (!txs.length) {
    list.innerHTML = '<li class="notice">El libro mayor está vacío.</li>';
    return;
  }
  for (const t of txs) {
    const li = document.createElement('li');
    li.className = `tx ${safeClass(t.kind, TX_CLASSES)}`;
    // A cash dividend carries an amount and no share count; printing a price
    // of "—" next to it reads as missing data instead of not applicable.
    let detail = '';
    if (t.kind === 'split') detail = `ratio ${num(t.ratio, 0)}:1`;
    else if (t.price != null && t.quantity != null) {
      detail = `${num(t.quantity, t.quantity % 1 ? 4 : 0)} × ${money(t.price)}`;
    } else if (CASH_KINDS.includes(t.kind)) detail = '';
    else if (t.amount != null) detail = 'en efectivo';
    const fee = t.fees ? `comisión ${money(t.fees)}` : '';
    li.innerHTML = `
      <div class="tx-top">
        <span class="tx-kind">${t.ticker ? `${escapeHtml(t.ticker)} · ` : ''}${
          escapeHtml(KIND_LABEL[t.kind] || t.kind)}</span>
        <span class="tx-cash ${t.cash_flow > 0 ? 'in' : 'out'}">${
          t.cash_flow ? signed(t.cash_flow) : ''}</span>
      </div>
      <div class="tx-sub">
        <span>${t.trade_date}</span><span>${detail}</span>${fee ? `<span>${fee}</span>` : ''}
        ${t.corrected ? '<span class="chip neutral">corregido</span>' : ''}
      </div>
      <div class="alert-actions">
        <button class="mini" data-act="edit">Editar</button>
        <button class="mini danger" data-act="drop" aria-label="Borrar movimiento">
          <svg aria-hidden="true"><use href="#i-trash"/></svg>
        </button>
      </div>`;
    li.querySelector('[data-act="edit"]').addEventListener('click', () => {
      openTransactionForm(t.ticker, t);
    });
    li.querySelector('[data-act="drop"]').addEventListener('click', async () => {
      if (!confirm(`¿Borrar el movimiento de ${t.ticker}? Queda registrado en el historial.`)) return;
      try {
        await send(`/api/portfolio/transactions/${t.id}`, 'DELETE');
        showOk('Movimiento borrado.');
        await loadPortfolio();
      } catch (e) { showError(e.message); }
    });
    list.appendChild(li);
  }
}



// --- números del negocio -----------------------------------------------------

// How each metric is written. Percentages carry their sign because the reader
// cares which way it went; absolutes are in millions, as the tables build them.
const METRIC = {
  Period:            { label: 'Período', kind: 'text' },
  Revenue:           { label: 'Ingresos', kind: 'millions' },
  FCF:               { label: 'FCF', kind: 'millions', signed: true },
  Net_Debt:          { label: 'Deuda neta', kind: 'millions', inverted: true },
  FCF_Yield:         { label: 'FCF yield', kind: 'pct', signed: true },
  EV_FCF_Yield:      { label: 'EV/FCF yield', kind: 'pct', signed: true },
  EPS:               { label: 'Ganancia por acción', kind: 'ratio', signed: true },
  PE:                { label: 'P/E', kind: 'ratio' },
  // "PEG" alone would read as the textbook one, which divides by *forecast*
  // growth. This divides by the growth that already happened, and the label is
  // the only place a reader can find that out.
  PEG:               { label: 'PEG (crec. pasado)', kind: 'ratio' },
  Earnings_Growth:   { label: 'Crec. ganancias', kind: 'pct', signed: true },
  Gross_Margin:      { label: 'Margen bruto', kind: 'pct' },
  Operating_Margin:  { label: 'Margen operativo', kind: 'pct', signed: true },
  Net_Margin:        { label: 'Margen neto', kind: 'pct', signed: true },
  Revenue_Growth:    { label: 'Crecimiento', kind: 'pct', signed: true },
  Interest_Coverage: { label: 'Cobertura intereses', kind: 'ratio' },
  FCF_Conversion:    { label: 'Conversión a FCF', kind: 'pct' },
  ROE:               { label: 'ROE', kind: 'pct', signed: true },
  Net_Debt_to_FCF:   { label: 'Deuda neta / FCF', kind: 'ratio', inverted: true },
};

function metricCell(name, value) {
  const spec = METRIC[name] || { label: name, kind: 'ratio' };
  if (value == null) return { text: '—', cls: 'na' };
  if (spec.kind === 'text') return { text: String(value), cls: '' };

  let text;
  if (spec.kind === 'pct') text = `${spec.signed ? signed(value, 1) : num(value, 1)}%`;
  else if (spec.kind === 'millions') text = money(value);
  else text = num(value, 2);

  let cls = '';
  if (spec.signed) cls = value >= 0 ? 'up' : 'down';
  // Debt reads the other way round: less is better, so the colour follows the
  // meaning rather than the sign.
  if (spec.inverted && value > 0) cls = '';
  return { text, cls };
}

function renderMetricTable(table, columns, rows) {
  table.innerHTML = '';
  if (!rows.length) {
    table.innerHTML = '<tbody><tr><td class="na">Sin datos guardados.</td></tr></tbody>';
    return;
  }
  const periods = rows.map((r) => r.Period);
  const head = `<thead><tr><th>Métrica</th>${
    periods.map((p) => `<th>${escapeHtml(p)}</th>`).join('')}</tr></thead>`;
  const body = columns
    .filter((name) => name !== 'Period')
    .filter((name) => rows.some((r) => r[name] != null))
    .map((name) => {
      const spec = METRIC[name] || { label: name };
      const cells = rows.map((r) => {
        const { text, cls } = metricCell(name, r[name]);
        return `<td class="${cls}">${escapeHtml(text)}</td>`;
      }).join('');
      return `<tr><th scope="row">${escapeHtml(spec.label)}</th>${cells}</tr>`;
    }).join('');
  table.innerHTML = `${head}<tbody>${body}</tbody>`;
}

function renderPeriodPicker(body) {
  const box = $('period-pick');
  box.innerHTML = '';
  for (const [kind, label] of [['annual', 'Anual'], ['quarterly', 'Trimestral']]) {
    const button = document.createElement('button');
    button.className = 'range';
    button.type = 'button';
    button.textContent = label;
    button.setAttribute('aria-pressed', String(kind === state.period));
    button.addEventListener('click', () => {
      state.period = kind;
      paintFundamentals(body);
    });
    box.appendChild(button);
  }
}

function paintFundamentals(body) {
  renderPeriodPicker(body);
  const period = body.periods[state.period] || { rows: [], stale: true };
  renderMetricTable($('fund-summary'), body.summary_columns, period.rows);
  renderMetricTable($('fund-quality'), body.quality_columns, period.rows);

  const bits = [];
  if (period.fetched_at) {
    bits.push(`Fuente ${period.source || 'desconocida'} · traído ${ago(period.fetched_at)}`);
  }
  if (period.stale && period.rows.length) bits.push('Conviene actualizar.');
  $('fund-note').textContent = bits.join(' · ');

  // Two different caveats can apply at once, and each explains a different set
  // of blanks, so neither can silently replace the other.
  const warnings = [];
  if (body.currency_mismatch) {
    warnings.push('Los estados contables están en otra moneda que la acción: '
      + 'P/E, PEG, EPS, FCF yield y EV quedan en blanco en vez de mal. '
      + 'Los márgenes y el crecimiento siguen siendo válidos.');
  }
  if (body.net_debt_estimated) {
    warnings.push('En algún período el proveedor no reportó deuda total y caja: '
      + 'ahí la deuda neta es una estimación, y el EV y su yield la heredan.');
  }
  const warn = $('fund-warn');
  warn.hidden = warnings.length === 0;
  warn.textContent = warnings.join(' ');
}

async function loadFundamentals() {
  const body = await api(`/api/tickers/${state.ticker}/fundamentals`);
  paintFundamentals(body);
  if (body.missing) {
    $('fund-note').textContent = 'Trayendo los estados contables…';
    await send(`/api/tickers/${state.ticker}/fundamentals/refresh`, 'POST');
    await awaitFundamentals();
  }
}

/** Poll until the first fetch lands. */
async function awaitFundamentals(attempts = 12) {
  for (let i = 0; i < attempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    let body;
    try {
      body = await api(`/api/tickers/${state.ticker}/fundamentals`);
    } catch {
      return;
    }
    if (!body.missing) {
      paintFundamentals(body);
      return;
    }
  }
  $('fund-note').textContent =
    'No se pudieron traer los estados contables. Revisá los proveedores.';
}

// --- informe de IA y sugerencias --------------------------------------------

const PRIORITY_LABEL = { high: 'alta', medium: 'media', low: 'baja' };

async function loadAI() {
  const [suggestions, reports, job] = await Promise.all([
    api(`/api/suggestions?ticker=${state.ticker}`),
    api(`/api/tickers/${state.ticker}/analyses`),
    api(`/api/tickers/${state.ticker}/analysis/status`),
  ]);
  renderSuggestions(suggestions);
  renderReports(reports);

  // Coming back to the tab while a report is still running should show it
  // running, not an idle screen that looks like nothing happened.
  if (job.status === 'running' && !workingTimer) {
    startWorking(job.stage || 'Pensando…');
    watchReport().catch(() => {});
  }
}

function renderSuggestions(rows) {
  const list = $('suggestion-list');
  list.innerHTML = '';
  if (!rows.length) {
    list.innerHTML = '<li class="notice">Sin sugerencias pendientes. '
      + 'Pedí un informe y la IA propone alertas concretas.</li>';
    return;
  }
  for (const s of rows) {
    const li = document.createElement('li');
    li.className = `suggestion${s.actionable ? '' : ' advice'}`;
    const params = Object.entries(s.params)
      .map(([k, v]) => `<b>${escapeHtml(k)} ${escapeHtml(v)}</b>`).join('');
    li.innerHTML = `
      <div class="row-top">
        <span class="alert-name">${escapeHtml(s.headline)}</span>
        <span class="priority ${safeClass(s.priority, PRIORITY_CLASSES, 'low')}">${
          escapeHtml(PRIORITY_LABEL[s.priority] || s.priority)}</span>
      </div>
      ${params ? `<div class="params">${params}</div>` : ''}
      <p class="why">${escapeHtml(s.rationale)}</p>
      <div class="alert-actions">
        ${s.actionable ? '<button class="mini" data-act="accept">Crear alerta</button>' : ''}
        <button class="mini" data-act="reject">${s.actionable ? 'Descartar' : 'Listo'}</button>
      </div>
      ${s.actionable ? '' : '<p class="note">Es una acción para vos, no una regla que el '
        + 'sistema pueda vigilar solo.</p>'}`;

    const accept = li.querySelector('[data-act="accept"]');
    if (accept) {
      accept.addEventListener('click', async () => {
        try {
          const created = await send(`/api/suggestions/${s.id}/accept`, 'POST', { params: {} });
          showOk(`Alerta ${created.kind} creada para ${created.ticker}.`);
          await Promise.all([loadAI(), loadAlerts()]);
        } catch (e) { showError(e.message); }
      });
    }
    li.querySelector('[data-act="reject"]').addEventListener('click', async () => {
      try {
        await send(`/api/suggestions/${s.id}/reject`, 'POST');
        await loadAI();
      } catch (e) { showError(e.message); }
    });
    list.appendChild(li);
  }
}

function renderReports(rows) {
  const box = $('report-list');
  box.innerHTML = '';
  if (!rows.length) {
    box.innerHTML = '<p class="notice">Todavía no pediste ningún informe.</p>';
    return;
  }
  rows.forEach((r, index) => {
    const details = document.createElement('details');
    details.className = 'report';
    if (index === 0) details.open = true;
    details.innerHTML = `
      <summary><span>${escapeHtml(r.created_at.slice(0, 16).replace('T', ' '))}</span>
        <span>${escapeHtml(r.model)}</span></summary>
      <div class="body">${renderReportBody(r.report)}</div>
      <p class="prov">${escapeHtml(r.provenance)}</p>`;
    box.appendChild(details);
  });
}

// Everything that reaches innerHTML goes through this first.
//
// Two sources of untrusted text meet here. Tickers and notes are typed by a
// person, and the AI's rationale is free text from a model that was shown
// whatever the user pasted as context — a prompt injection in that paste comes
// straight back out here. And because the session token lives in localStorage,
// script running on this page can walk off with the credential.
//
// Quotes are encoded too: the textContent trick does not, so it cannot protect
// an attribute, which is where the priority class was going.
const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/** A class name taken from data: only ever one of a known set. */
function safeClass(value, allowed, fallback = '') {
  return allowed.includes(value) ? value : fallback;
}

const TREND_CLASSES = ['alcista', 'bajista'];
const SEVERITY_CLASSES = ['info', 'warning', 'critical'];
const PRIORITY_CLASSES = ['high', 'medium', 'low'];
const TX_CLASSES = ['buy', 'sell', 'dividend', 'split', 'fee', 'deposit', 'withdraw'];

// Just enough markdown to make the report readable. Applied *after* escaping,
// so the only tags that can reach the DOM are the ones written here.
function renderReportBody(text) {
  return escapeHtml(text)
    .replace(/^#{1,6}\s*(.+)$/gm, '<b class="md-h">$1</b>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/^\s*[-*]\s+(.+)$/gm, '<span class="md-li">$1</span>')
    .replace(/^\s*---+\s*$/gm, '<span class="md-hr"></span>');
}

function openReportForm() {
  // Same offer the terminal makes: paste what your broker's app says and the
  // model gets it as claims to check against the real numbers, not as fact.
  const context = field('context', 'Texto de tu app de inversión', {
    type: 'textarea',
    hint: 'Opcional. Lo que pegues entra al informe como afirmaciones sin '
      + 'verificar, para que el modelo las contraste con los datos duros.',
  });
  sheet.open(`Informe de ${state.ticker}`, [context], async () => {
    const values = formValues();
    await send(`/api/tickers/${state.ticker}/analysis`, 'POST', { context: values.context });
    sheet.close();
    pickTab('ai');
    startWorking('Pidiendo el informe…');
    watchReport().catch(() => {});
  });
}

let workingTimer = null;
let workingReveal = null;

// With the data already cached a report can come back in about a second, and a
// spinner that appears and vanishes in that time reads as a glitch. Below this
// the work is simply instant and nothing needs to be said about it.
const REVEAL_AFTER = 400;

/** Show the busy card and start counting. */
function startWorking(stage) {
  const since = Date.now();
  $('ai-stage').textContent = stage;
  $('ai-elapsed').textContent = '0 s';
  $('ai-status').textContent = '';

  clearTimeout(workingReveal);
  workingReveal = setTimeout(() => { $('ai-working').hidden = false; }, REVEAL_AFTER);

  clearInterval(workingTimer);
  workingTimer = setInterval(() => {
    const seconds = Math.round((Date.now() - since) / 1000);
    $('ai-elapsed').textContent = seconds < 60
      ? `${seconds} s`
      : `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s`;
  }, 1000);
}

function stopWorking() {
  clearTimeout(workingReveal);
  clearInterval(workingTimer);
  workingReveal = null;
  workingTimer = null;
  $('ai-working').hidden = true;
}

async function watchReport(attempts = 90) {
  for (let i = 0; i < attempts; i++) {
    // Poll quickly at first so a cached report is noticed before the card is
    // even shown, then settle into a rhythm that does not hammer the API.
    await new Promise((resolve) => setTimeout(resolve, i < 3 ? 500 : 2500));
    let job;
    try {
      job = await api(`/api/tickers/${state.ticker}/analysis/status`);
    } catch {
      stopWorking();
      return;
    }
    // The server says which step it is on, so the wait is explained rather
    // than merely animated.
    if (job.stage) $('ai-stage').textContent = job.stage;

    if (job.status === 'done') {
      stopWorking();
      $('ai-status').textContent = job.suggestions
        ? `Listo: ${job.suggestions} sugerencia(s).` : 'Listo. Sin sugerencias nuevas.';
      await loadAI();
      return;
    }
    if (job.status === 'error') {
      stopWorking();
      showError(job.detail || 'El informe falló.');
      return;
    }
  }
  stopWorking();
  $('ai-status').textContent = 'Sigue corriendo. Volvé a esta pestaña en un rato.';
}

/** Re-fetch this ticker's price on demand.
 *
 * The screen reads stored rows, so a stale reading stays stale until somebody
 * asks. The fetch runs on the server in the background; this polls the reading
 * timestamp and stops as soon as it moves, rather than guessing at a delay.
 */
/** Re-fetch every followed ticker.
 *
 * The server reports how far along it is, so the button counts instead of
 * spinning blind: with a dozen symbols this takes the better part of a minute
 * and an unlabelled spinner for that long reads as a hang.
 */
async function refreshAll() {
  const button = $('p-refresh');
  const label = button.querySelector('span');
  button.disabled = true;
  button.classList.add('working');
  try {
    const started = await send('/api/portfolio/refresh', 'POST');
    if (!started.total) {
      showOk('No hay papeles que actualizar.');
      return;
    }
    label.textContent = `0 de ${started.total}…`;
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await new Promise((r) => setTimeout(r, 1000));
      const job = await api('/api/portfolio/refresh/status');
      if (job.total) label.textContent = `${job.done || 0} de ${job.total}…`;
      // Repaint as they land, so the prices visibly move rather than all
      // appearing at the end.
      if ((job.done || 0) % 3 === 0) await loadPortfolio().catch(() => {});
      if (job.status !== 'running') {
        await loadPortfolio();
        const failed = job.failed || [];
        if (failed.length) {
          // Named, not counted: which ones failed is the part you can act on.
          showError(`Sin datos para ${failed.join(', ')}. El resto se actualizó.`);
        } else {
          showOk(`${job.total} papeles actualizados.`);
        }
        return;
      }
    }
    showError('La actualización está tardando demasiado. Seguí en unos minutos.');
  } catch (e) {
    showError(e.message);
  } finally {
    button.classList.remove('working');
    label.textContent = 'Actualizar todo';
    button.disabled = false;
  }
}

async function refreshPrices() {
  const button = $('d-refresh');
  const label = button.querySelector('span');
  const before = state.lastReading;
  button.disabled = true;
  button.classList.add('working');
  label.textContent = 'Actualizando…';
  try {
    await send(`/api/tickers/${state.ticker}/refresh`, 'POST');
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((r) => setTimeout(r, 700));
      await paintDetail(state.ticker);
      if (state.lastReading !== before) {
        showOk('Precio actualizado.');
        return;
      }
    }
    // Not an error: the provider may simply have nothing newer, and saying so
    // beats a spinner that stops with no explanation.
    showOk('Sin datos más nuevos por ahora.');
  } catch (e) {
    showError(e.message);
  } finally {
    button.classList.remove('working');
    label.textContent = 'Actualizar';
    button.disabled = false;
  }
}

async function checkNow() {
  const button = $('check-now');
  button.disabled = true;
  try {
    await send(`/api/tickers/${state.ticker}/check`, 'POST');
    showOk('Chequeando…');
    // The run writes its rows as it goes; give it a moment and re-read.
    setTimeout(() => {
      loadAlerts().catch(() => {});
      loadStatus();
    }, 6000);
  } catch (e) {
    showError(e.message);
  } finally {
    button.disabled = false;
  }
}

// --- navegación -------------------------------------------------------------

function pickTab(name) {
  // Scoped to this strip: there are two of them on the page now, and a bare
  // .tab matched both — clicking a portfolio tab ran this with undefined,
  // which marked every tab of both strips selected.
  for (const tab of document.querySelectorAll('.tab[data-tab]')) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  $('tab-chart').hidden = name !== 'chart';
  $('tab-indicator').hidden = name !== 'indicator';
  $('tab-alerts').hidden = name !== 'alerts';
  $('tab-fundamentals').hidden = name !== 'fundamentals';
  $('tab-ai').hidden = name !== 'ai';

  if (name === 'indicator' && !state.charts.series) {
    loadSeries().catch((e) => showError(e.message));
  }
  if (name === 'ai') loadAI().catch((e) => showError(e.message));
  if (name === 'fundamentals') loadFundamentals().catch((e) => showError(e.message));
  // uPlot se dimensiona al construirse; uno construido oculto mide cero.
  if (name === 'chart' && state.charts.price) state.charts.price.setSize(chartSize(240));
}

const TITLES = { portfolio: 'Cartera', tickers: 'Tickers' };

function destroyCharts(...names) {
  for (const name of names) {
    if (state.charts[name]) state.charts[name].destroy();
    state.charts[name] = null;
  }
}

/** Show one of the two top level sections. */
function showView(view) {
  state.view = view;
  state.ticker = null;
  destroyCharts('price', 'series');

  showSearching(false);
  $('view-portfolio').hidden = view !== 'portfolio';
  $('view-list').hidden = view !== 'tickers';
  $('view-detail').hidden = true;
  $('back').hidden = true;
  $('title').textContent = TITLES[view];

  for (const item of document.querySelectorAll('.nav-item')) {
    if (item.dataset.view === view) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }

  const load = view === 'portfolio' ? loadPortfolio : loadList;
  load().catch((e) => showError(e.message));
}

function navigate(view, { push = true } = {}) {
  if (push) location.hash = view === 'portfolio' ? '' : `#/${view}`;
  showView(view);
}

/** Read the hash and render whatever it names. Deep links have to work. */
function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  if (hash.startsWith('t/')) {
    const ticker = hash.slice(2).toUpperCase();
    if (ticker) { showDetail(ticker); return; }
  }
  showView(hash === 'tickers' ? 'tickers' : 'portfolio');
}

document.addEventListener('DOMContentLoaded', () => {
  // Back returns to whichever section the detail was opened from.
  $('back').addEventListener('click', () => navigate(state.view));
  $('status').addEventListener('click', loadStatus);

  for (const item of document.querySelectorAll('.nav-item')) {
    item.addEventListener('click', () => navigate(item.dataset.view));
  }

  $('search').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('search-input');
    const value = input.value;
    input.value = '';
    input.blur();
    lookupTicker(value);
  });

  $('add-tx').addEventListener('click', () => openTransactionForm());
  for (const tab of document.querySelectorAll('.tab[data-ptab]')) {
    tab.addEventListener('click', () => pickPortfolioTab(tab.dataset.ptab));
  }
  $('ask-ai').addEventListener('click', openReportForm);
  $('check-now').addEventListener('click', checkNow);
  $('d-refresh').addEventListener('click', refreshPrices);
  $('p-refresh').addEventListener('click', refreshAll);
  $('add-alert').addEventListener('click', () => {
    openAlertForm(state.ticker).catch((e) => showError(e.message));
  });
  $('sheet-close').addEventListener('click', () => sheet.close());
  $('sheet-cancel').addEventListener('click', () => sheet.close());
  $('sheet-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const save = $('sheet-save');
    save.disabled = true;
    save.textContent = 'Guardando…';
    try {
      await sheet.onSubmit();
    } catch (e) {
      sheet.fail(e.message);
    } finally {
      save.disabled = false;
      save.textContent = 'Guardar';
    }
  });

  for (const tab of document.querySelectorAll('.tab[data-tab]')) {
    tab.addEventListener('click', () => pickTab(tab.dataset.tab));
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.charts.price && !$('tab-chart').hidden) {
        state.charts.price.setSize(chartSize(240));
      }
      if (state.charts.curve && !$('view-portfolio').hidden) {
        state.charts.curve.setSize({
          width: Math.max(240, $('p-curve').clientWidth - 8), height: 190,
        });
      }
    }, 120);
  });

  window.addEventListener('hashchange', route);

  $('gate-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('gate-submit');
    button.disabled = true;
    button.textContent = 'Entrando…';
    try {
      await signIn();
      hideGate();
      renderRanges();
      loadStatus();
      route();
    } catch (e) {
      gateFail(e.message);
    } finally {
      button.disabled = false;
      button.textContent = 'Entrar';
    }
  });
  $('logout').addEventListener('click', signOut);

  setInterval(() => { if ($('gate').hidden) loadStatus(); }, 60000);
  start().catch((e) => showError(e.message));

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* offline es un plus, no un requisito */ });
  }
});
