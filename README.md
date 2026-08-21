# Financial Analyzer

Analizador financiero de consola con **datos de mercado reales**, **persistencia en SQLite**
y **alertas** que se chequean por cron y avisan por consola, Telegram y notificaciones de escritorio.

> **Regla dura del proyecto:** los datos nunca se simulan. La cadena de proveedores es
> `yfinance` → Alpha Vantage → Finnhub. Si ninguno responde, el comando falla con un error
> explícito (`DataUnavailableError`); nunca se rellenan números inventados.

---

## Instalación

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # completá las keys que quieras usar
```

O con Docker:

```bash
docker compose build
docker compose run --rm analyzer            # menú interactivo
docker compose run --rm alerts              # chequeo de alertas (perfil cron)
```

## Variables de entorno

| Variable | Obligatoria | Para qué |
|---|---|---|
| `GEMINI_API_KEY` | no | reporte estratégico con IA (opción 3 / `--ai`) |
| `ALPHA_VANTAGE_API_KEY` | no | fallback de datos si falla yfinance |
| `FINNHUB_API_KEY` | no | segundo fallback |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | no | push de alertas al celular |
| `FA_LOCAL_AI_URL` + `FA_LOCAL_AI_MODEL` | no | modelo local OpenAI-compatible para tareas baratas |
| `FA_LOCAL_AI_MAX_TOKENS` / `FA_LOCAL_AI_TIMEOUT` | no | presupuesto y paciencia con el modelo local (2000 / 240 s) |
| `FA_DESKTOP_NOTIFICATIONS` | no | `notify-send` (poner `false` dentro de Docker) |
| `FA_COOLDOWN_HOURS` | no | horas mínimas entre repeticiones de una alerta (default 24) |
| `FA_EARNINGS_WARNING_DAYS` | no | antelación del aviso de earnings (default 7) |
| `FA_DATA_DIR` / `FA_DB_PATH` / `FA_LOG_PATH` | no | ubicación de la DB y el log |

## Uso

### Menú interactivo

```bash
python financial_analyzer.py
```

Dos niveles: un **menú general** (portfolio, posiciones, alertas, chequeo, digest, sugerencias)
y un **workspace por ticker**. Al elegir "Trabajar sobre un ticker" todo queda scopeado a ese
símbolo — análisis, alertas, compras, historial — sin volver a tipearlo. El ticker activo se
guarda en la base, así que sobrevive al reinicio; se sale con `x` y se cambia con `c`.
Al entrar muestra las alertas disparadas que todavía no viste.

### CLI

```bash
python financial_analyzer.py use PODD          # fija el ticker activo (y unuse lo borra)
python financial_analyzer.py analyze --ai      # usa el ticker activo
python financial_analyzer.py analyze PODD --period both --ai
python financial_analyzer.py add-position PODD --qty 10 --price 141.5 --date 2026-03-01 --with-default-alerts
python financial_analyzer.py add-alert PODD --kind price_below --param price=120
python financial_analyzer.py add-alert AAPL --kind pct_down --param pct=8 --param reference=baseline
python financial_analyzer.py portfolio
python financial_analyzer.py positions
python financial_analyzer.py alerts --active
python financial_analyzer.py history --limit 20
python financial_analyzer.py kinds            # catálogo de alertas
python financial_analyzer.py check-alerts     # evalúa y notifica (para cron)
python financial_analyzer.py check-alerts --ticker PODD
python financial_analyzer.py suggestions --review   # crea las alertas que propuso la IA, una a una
python financial_analyzer.py digest                 # resumen del portfolio escrito por el modelo local
python financial_analyzer.py local-ai               # diagnostica la conexión con el modelo local
```

`check-alerts` devuelve exit code `0` si todo salió bien y `2` si algún ticker se quedó sin datos.
Con `--json` imprime un resumen machine-readable y no ensucia stdout con el canal de consola.

### Cron

```cron
# cada 30 minutos en horario de mercado (NY), de lunes a viernes
*/30 9-17 * * 1-5  .venv/bin/python financial_analyzer.py check-alerts --quiet
# o con Docker
*/30 9-17 * * 1-5  cd /data/bin/financial_analyzer && docker compose run --rm alerts
```

## Tipos de alerta

| Tipo | Parámetros | Qué hace |
|---|---|---|
| `pct_up` / `pct_down` | `pct`, `reference` (`buy`\|`baseline`) | sube/baja X% contra el precio de compra o contra el precio congelado al crearla |
| `price_above` / `price_below` | `price` | precio objetivo (one-shot: se desactiva al dispararse) |
| `period_elapsed` | `months` o `days` | recordatorio de revisión N meses después de la compra, con el P&L del momento |
| `earnings_near` | `days` | avisa N días antes del próximo earnings call |
| `trailing_stop` | `pct` | caída de X% desde el máximo alcanzado después de la compra |
| `sma_cross` | `fast`, `slow`, `direction` | golden/death cross; sólo dispara en la rueda del cruce |
| `rsi` | `period`, `overbought`, `oversold` | RSI de Wilder fuera de los umbrales |
| `dividend_ex_near` | `days` | avisa N días antes de la fecha ex-dividendo |
| `split_detected` | `lookback_days` | detecta splits posteriores a la compra y sugiere el costo ajustado |

Las alertas que dependen de una compra (`period_elapsed`, `trailing_stop`, `split_detected`)
requieren una posición cargada. Cada alerta tiene *cooldown* para no repetirse, puede tener
fecha de expiración y queda registrada en `alert_events` con los canales por los que se entregó.

## Reporte de IA: grounding y sugerencias

El reporte no se le pide "a ciegas". Antes de llamar al modelo se arma un **DATA PACK** con
precio (fuente + timestamp), cierre previo, rango anual, SMA50/200, RSI14, calendario de
earnings y ex-dividendo, splits, los estados contables con su fecha de corte, y la posición y
alertas del usuario. El prompt le prohíbe explícitamente usar datos de su entrenamiento y le
exige citar la fecha de cada número; lo que no pudimos traer va en una sección **MISSING DATA**
con la instrucción de decir "no disponible" en vez de estimar. La línea de procedencia
(`📊 Datos: precio yahoo @ … · fundamentals yahoo · …`) se imprime arriba del reporte y se
guarda junto a él.

El modelo devuelve además un bloque JSON con **alertas y acciones sugeridas**. Se guardan como
`pending` y se revisan de a una: `s` la crea, `e` permite editar los parámetros antes de crearla,
`n` la descarta, ENTER la deja pendiente. Las acciones que no son automatizables (por ejemplo
"tomar ganancias parciales") quedan registradas como recordatorio, nunca como alerta.

## Modelo local

Cualquier servidor OpenAI-compatible (LM Studio, llama.cpp, vLLM). Se usa sólo para lo barato:

1. **Extraer datos del texto pegado** de tu app de inversión → JSON estructurado que entra al
   DATA PACK como "claims sin verificar", en vez de texto crudo.
2. **Digest del portfolio** (`digest`): P&L, alertas cerca de dispararse y earnings próximos,
   redactado en lenguaje natural sin gastar cuota de Gemini. Cron-eable.
3. **Reparar sugerencias**: si el modelo remoto escribió las alertas en prosa, el local las
   reconvierte a JSON válido contra el catálogo.

Si no hay modelo local configurado, las tres funciones degradan solas: el texto va crudo, el
digest muestra los datos sin redactar y las sugerencias quedan vacías. Cuando el modelo está
configurado pero falla (timeout de carga, presupuesto agotado por el *reasoning*), el mensaje
dice el motivo real en vez de fingir que no hay modelo.

Probado contra `llama-server` (llama.cpp) con gemma-4-E4B-it en `127.0.0.1:5001`.

## Base de datos

SQLite en `data/financial_analyzer.db` (WAL). Tablas: `positions`, `alerts`, `alert_events`,
`price_snapshots`, `analyses` (reportes de IA guardados), `ai_suggestions` y `meta`
(guarda el ticker activo).

## Arquitectura

```
fa/
├── config.py      settings inmutables desde el entorno
├── models.py      dataclasses frozen (Quote, Position, Alert, Signal, MarketContext…)
├── providers/     yahoo (primario) → alphavantage → finnhub, unificados por chain.py
├── store/         esquema SQLite + CRUD (positions, alerts, events)
├── indicators.py  SMA, RSI, cruces, drawdown (funciones puras)
├── metrics.py     FCF, equity, market cap, EV, yields desde fundamentals reales
├── alerts/        catálogo de tipos, reglas puras y motor de chequeo
├── notify/        console+log, telegram, notify-send y el dispatcher
├── ai_context.py  DATA PACK con procedencia y huecos declarados
├── localai.py     cliente OpenAI-compatible + local_tasks.py (extracción, digest, reparación)
├── digest.py      fact sheet del portfolio
├── ui/            gráficos, prompts, vistas, menú general y workspace por ticker
├── market.py      facade que arma el MarketContext (1 fetch por ticker por corrida)
├── portfolio.py   valuación y P&L
├── actions.py     operaciones compartidas por CLI y menú
└── cli.py         argparse
```

## Tests

```bash
.venv/bin/python -m pytest --cov=fa --cov-report=term-missing
```

290 tests, 86% de cobertura, sin tocar la red: proveedores y modelo local se ejercitan con
payloads enlatados y el mercado se stubea.
