"""The current schema, in one place, for a database that starts from nothing.

A fresh install gets this script and is stamped at :data:`TARGET_VERSION`
directly. It never replays the incremental migrations, which exist only to
carry databases created before a change forward — and which, being SQLite
archaeology, would be meaningless on Postgres.

Type placeholders (``{ID_PK}``, ``{JSON}``, ``{REAL}``, ``{FK_ID}``) are filled
in by :func:`fa.store.database.render_ddl` for the engine in use.

Two kinds of table live here and the difference decides how the hosted mode
scales:

* **Shared** — one row per ticker and moment, written only by the market
  refresh, read by everybody. No ``account_id``.
* **Per account** — everything that is somebody's money, decision or history.
  Always carries ``account_id`` and, on Postgres, sits behind a row level
  security policy.
"""
from __future__ import annotations

TENANT_TABLES = (
    "positions",
    "transactions",
    "alerts",
    "alert_events",
    "alert_evaluations",
    "delivery_attempts",
    "portfolio_valuations",
    "analyses",
    "ai_suggestions",
    "check_runs",
)

SHARED_TABLES = ("daily_bars", "indicator_snapshots", "data_fetches", "price_snapshots")

# The single account every local install runs under. Hosted deployments create
# one per sign-up; the id is only special in that migrations adopt orphan rows
# into it.
LOCAL_ACCOUNT_ID = 1

SCHEMA = """
-- ===== tenancy ===========================================================

CREATE TABLE IF NOT EXISTS accounts (
    id          {ID_PK},
    name        TEXT NOT NULL DEFAULT 'personal',
    owner_email TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT 'local',
    created_at  TEXT NOT NULL,
    updated_at  TEXT,
    deleted_at  TEXT
);

CREATE TABLE IF NOT EXISTS account_users (
    id         {ID_PK},
    account_id {FK_ID} NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    auth_id    TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_account_users_auth ON account_users(auth_id);
CREATE INDEX IF NOT EXISTS idx_account_users_account ON account_users(account_id);

-- ===== per account: portfolio ============================================

CREATE TABLE IF NOT EXISTS positions (
    id           {ID_PK},
    account_id   {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    ticker       TEXT NOT NULL,
    quantity     {REAL} NOT NULL,
    buy_price    {REAL} NOT NULL,
    buy_date     TEXT NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'USD',
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    closed_at    TEXT,
    updated_at   TEXT,
    deleted_at   TEXT,
    close_price  {REAL},
    close_date   TEXT,
    realized_pnl {REAL}
);
CREATE INDEX IF NOT EXISTS idx_positions_ticker ON positions(account_id, ticker);

CREATE TABLE IF NOT EXISTS transactions (
    id          {ID_PK},
    account_id  {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    position_id {FK_ID} REFERENCES positions(id) ON DELETE SET NULL,
    ticker      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    quantity    {REAL},
    price       {REAL},
    amount      {REAL},
    ratio       {REAL},
    fees        {REAL} NOT NULL DEFAULT 0,
    currency    TEXT NOT NULL DEFAULT 'USD',
    note        TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'manual',
    replaces_id {FK_ID},
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_replaces ON transactions(replaces_id);
CREATE INDEX IF NOT EXISTS idx_tx_ticker ON transactions(account_id, ticker, trade_date);
CREATE INDEX IF NOT EXISTS idx_tx_position ON transactions(position_id);
CREATE INDEX IF NOT EXISTS idx_tx_updated ON transactions(updated_at);

CREATE TABLE IF NOT EXISTS portfolio_valuations (
    id           {ID_PK},
    account_id   {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    taken_at     TEXT NOT NULL,
    day          TEXT NOT NULL,
    cost_basis   {REAL} NOT NULL,
    market_value {REAL} NOT NULL,
    pnl_abs      {REAL} NOT NULL,
    pnl_pct      {REAL} NOT NULL,
    positions    {INTEGER} NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'USD',
    holdings     {JSON} NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_valuations_day ON portfolio_valuations(account_id, day DESC);

-- ===== per account: alerts ===============================================

CREATE TABLE IF NOT EXISTS alerts (
    id             {ID_PK},
    account_id     {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    position_id    {FK_ID} REFERENCES positions(id) ON DELETE SET NULL,
    ticker         TEXT NOT NULL,
    kind           TEXT NOT NULL,
    params         {JSON} NOT NULL DEFAULT '{}',
    active         {BOOLEAN} NOT NULL DEFAULT 1,
    one_shot       {BOOLEAN} NOT NULL DEFAULT 0,
    cooldown_hours {INTEGER} NOT NULL DEFAULT 24,
    last_fired_at  TEXT,
    expires_at     TEXT,
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT,
    deleted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(account_id, active, ticker);
CREATE INDEX IF NOT EXISTS idx_alerts_updated ON alerts(updated_at);

CREATE TABLE IF NOT EXISTS alert_events (
    id              {ID_PK},
    account_id      {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    alert_id        {FK_ID} REFERENCES alerts(id) ON DELETE SET NULL,
    run_id          {FK_ID},
    ticker          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    message         TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    payload         {JSON} NOT NULL DEFAULT '{}',
    delivered       {JSON} NOT NULL DEFAULT '[]',
    price           {REAL},
    fired_at        TEXT NOT NULL,
    acknowledged_at TEXT,
    updated_at      TEXT,
    deleted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_fired ON alert_events(account_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_alert ON alert_events(alert_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_updated ON alert_events(updated_at);

CREATE TABLE IF NOT EXISTS check_runs (
    id               {ID_PK},
    account_id       {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    trigger          TEXT NOT NULL DEFAULT 'manual',
    ticker           TEXT,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_ms      {INTEGER},
    checked          {INTEGER} NOT NULL DEFAULT 0,
    fired            {INTEGER} NOT NULL DEFAULT 0,
    skipped_cooldown {INTEGER} NOT NULL DEFAULT 0,
    expired          {INTEGER} NOT NULL DEFAULT 0,
    errors           {JSON} NOT NULL DEFAULT '[]',
    ok               {BOOLEAN} NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON check_runs(account_id, started_at DESC);

CREATE TABLE IF NOT EXISTS alert_evaluations (
    id           {ID_PK},
    account_id   {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    run_id       {FK_ID} REFERENCES check_runs(id) ON DELETE SET NULL,
    alert_id     {FK_ID} REFERENCES alerts(id) ON DELETE SET NULL,
    ticker       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    price        {REAL},
    detail       {JSON} NOT NULL DEFAULT '{}',
    evaluated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evals_alert ON alert_evaluations(alert_id, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_evals_run ON alert_evaluations(run_id);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id           {ID_PK},
    account_id   {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    event_id     {FK_ID} REFERENCES alert_events(id) ON DELETE SET NULL,
    channel      TEXT NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT NOT NULL DEFAULT '',
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivery_event ON delivery_attempts(event_id);

-- ===== per account: AI ===================================================

CREATE TABLE IF NOT EXISTS analyses (
    id         {ID_PK},
    account_id {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    ticker     TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT '',
    metrics    TEXT NOT NULL DEFAULT '',
    context    TEXT NOT NULL DEFAULT '',
    report     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON analyses(account_id, ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_updated ON analyses(updated_at);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    id          {ID_PK},
    account_id  {FK_ID} NOT NULL DEFAULT 1 REFERENCES accounts(id) ON DELETE CASCADE,
    analysis_id {FK_ID} REFERENCES analyses(id) ON DELETE SET NULL,
    ticker      TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'alert',
    kind        TEXT NOT NULL DEFAULT '',
    params      {JSON} NOT NULL DEFAULT '{}',
    rationale   TEXT NOT NULL DEFAULT '',
    priority    TEXT NOT NULL DEFAULT 'medium',
    status      TEXT NOT NULL DEFAULT 'pending',
    alert_id    {FK_ID} REFERENCES alerts(id) ON DELETE SET NULL,
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    updated_at  TEXT,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON ai_suggestions(account_id, status, ticker);
CREATE INDEX IF NOT EXISTS idx_suggestions_updated ON ai_suggestions(updated_at);

-- ===== shared: market data ===============================================
-- One row per ticker and moment, whoever asked for it. The refresh writes,
-- every account reads. This is what keeps a thousand users from downloading
-- the same candles a thousand times.

CREATE TABLE IF NOT EXISTS daily_bars (
    ticker     TEXT NOT NULL,
    day        TEXT NOT NULL,
    open       {REAL},
    high       {REAL},
    low        {REAL},
    close      {REAL} NOT NULL,
    volume     {REAL},
    source     TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ticker, day)
);
CREATE INDEX IF NOT EXISTS idx_bars_day ON daily_bars(day DESC);

CREATE TABLE IF NOT EXISTS indicator_snapshots (
    id               {ID_PK},
    ticker           TEXT NOT NULL,
    taken_at         TEXT NOT NULL,
    price            {REAL} NOT NULL,
    rsi              {REAL},
    sma_fast         {REAL},
    sma_slow         {REAL},
    vs_sma_slow_pct  {REAL},
    macd_histogram   {REAL},
    percent_b        {REAL},
    atr              {REAL},
    atr_pct          {REAL},
    volatility_pct   {REAL},
    volume_ratio     {REAL},
    from_high_pct    {REAL},
    from_low_pct     {REAL},
    max_drawdown_pct {REAL},
    rel_strength_3m  {REAL},
    trend            TEXT NOT NULL DEFAULT '',
    payload          {JSON} NOT NULL DEFAULT '{}',
    run_id           {FK_ID}
);
CREATE INDEX IF NOT EXISTS idx_indicators_ticker ON indicator_snapshots(ticker, taken_at DESC);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id             {ID_PK},
    ticker         TEXT NOT NULL,
    price          {REAL} NOT NULL,
    source         TEXT NOT NULL DEFAULT '',
    taken_at       TEXT NOT NULL,
    change_pct     {REAL},
    previous_close {REAL}
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON price_snapshots(ticker, taken_at DESC);

CREATE TABLE IF NOT EXISTS data_fetches (
    id          {ID_PK},
    ticker      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT '',
    ok          {BOOLEAN} NOT NULL DEFAULT 1,
    error       TEXT NOT NULL DEFAULT '',
    duration_ms {INTEGER},
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetches_ticker ON data_fetches(ticker, fetched_at DESC);

-- ===== control ===========================================================

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
