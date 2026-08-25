# Financial Analyzer

Analizador de cartera con **datos de mercado reales**, **historial propio** y **alertas** que
corren solas y avisan por Telegram. Se usa desde la consola o desde un **dashboard web
instalable en el celular**.

> **Regla dura del proyecto:** los datos nunca se simulan. La cadena de proveedores es
> `yfinance` → Alpha Vantage → Finnhub. Si ninguno responde, el comando falla con un error
> explícito (`DataUnavailableError`); nunca se rellenan números inventados.

Dos consecuencias de esa regla que aparecen por todos lados: un indicador que no se puede
calcular vale `None` y se muestra como "n/a" en vez de aproximarse, y una posición que no se
puede valuar queda **fuera del total con su motivo** en vez de contarse como cero.

---

## Qué hay adentro

| | |
|---|---|
| **Análisis fundamental** | FCF, EV, yields y deuda neta real desde los estados contables |
| **Indicadores técnicos** | RSI, MACD, Bollinger, ATR, drawdown, fuerza relativa vs índice |
| **18 tipos de alerta** | precio, porcentaje, técnicos, earnings, dividendos, splits |
| **Libro mayor** | compras, ventas, splits y dividendos; las tenencias se derivan de ahí |
| **Historial** | velas, indicadores, corridas y valuaciones se guardan, no se recalculan |
| **Reporte de IA** | Gemini con los datos servidos y la procedencia declarada |
| **Dashboard web** | PWA instalable, sin tienda, con gráficos y alta de alertas |

Para la guía paso a paso está **[HOWTO.md](HOWTO.md)**. Esto es el mapa.

---

## Instalación

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # completá las keys que quieras usar
```

Con Docker:

```bash
docker compose build
docker compose run --rm analyzer      # menú interactivo
docker compose up -d dashboard        # → http://localhost:8000
docker compose run --rm alerts        # chequeo de alertas (perfil cron)
```

## Variables de entorno

Ninguna es obligatoria: sin nada configurado funciona con yfinance, SQLite y consola.

| Variable | Para qué |
|---|---|
| `GEMINI_API_KEY` | reporte estratégico con IA (`--ai`) |
| `ALPHA_VANTAGE_API_KEY` / `FINNHUB_API_KEY` | fallbacks si falla yfinance |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | aviso de alertas al celular |
| `FA_LOCAL_AI_URL` + `FA_LOCAL_AI_MODEL` | modelo local OpenAI-compatible para tareas baratas |
| `FA_LOCAL_AI_MAX_TOKENS` / `FA_LOCAL_AI_TIMEOUT` | presupuesto y paciencia con el modelo local |
| `FA_BENCHMARK` | índice para la fuerza relativa (default `SPY`) |
| `FA_COOLDOWN_HOURS` | horas mínimas entre repeticiones de una alerta (default 24) |
| `FA_EARNINGS_WARNING_DAYS` | antelación del aviso de earnings (default 7) |
| `FA_DESKTOP_NOTIFICATIONS` | `notify-send` (poner `false` dentro de Docker) |
| `FA_DATA_DIR` / `FA_DB_PATH` / `FA_LOG_PATH` | ubicación de la DB y el log |
| `DATABASE_URL` | Postgres o Supabase; vacío = SQLite local |
| `FA_API_TOKEN` | token del dashboard; hace falta para salir de localhost |
| `SUPABASE_URL` + `SUPABASE_ANON_KEY` + `SUPABASE_JWT_SECRET` | modo multi-usuario |

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
python financial_analyzer.py technicals PODD   # indicadores y fuerza relativa vs el índice
python financial_analyzer.py add-position PODD --qty 10 --price 141.5 --date 2026-03-01 --with-default-alerts
python financial_analyzer.py add-alert PODD --kind price_below --param price=120
python financial_analyzer.py add-alert AAPL --kind pct_down --param pct=8 --param reference=baseline
python financial_analyzer.py portfolio
python financial_analyzer.py positions
python financial_analyzer.py alerts --active
python financial_analyzer.py history --limit 20
python financial_analyzer.py kinds             # catálogo de alertas
python financial_analyzer.py check-alerts      # evalúa y notifica (para el timer)
python financial_analyzer.py check-alerts --ticker PODD --trigger timer
python financial_analyzer.py suggestions --review   # crea las alertas que propuso la IA, una a una
python financial_analyzer.py digest                 # resumen del portfolio escrito por el modelo local
python financial_analyzer.py local-ai               # diagnostica la conexión con el modelo local
python financial_analyzer.py serve                  # dashboard web en localhost:8000
```

`check-alerts` devuelve exit code `0` si todo salió bien y `2` si algún ticker se quedó sin datos.
Con `--json` imprime un resumen machine-readable y no ensucia stdout con el canal de consola.

### Que corra solo

```bash
./scripts/setup-systemd.sh      # timer de usuario, con backoff y logs en journald
```

Cron sigue funcionando; el detalle de las dos formas está en
[HOWTO.md § Chequear alertas](HOWTO.md).

## Dashboard web

```bash
python financial_analyzer.py serve          # localhost
python financial_analyzer.py serve --lan    # toda la red, exige FA_API_TOKEN
```

Es una **PWA**: se instala desde el navegador del celular, sin tienda. Dos secciones —
Cartera y Tickers — con gráficos de precio, historial de indicadores, estados contables,
informe de IA con sus sugerencias, alta y baja de alertas y carga de operaciones. El buscador
trae cualquier símbolo que no estés siguiendo, como hace `analyze` en la terminal.

Lee **sólo lo que ya está guardado**: abrir una pantalla no dispara ninguna descarga, así que
funciona con el proveedor caído y no cuesta una llamada por visita. Cargar un ticker nuevo sí
trae su historial, en segundo plano: es una escritura y pasa una vez por papel. El encabezado dice hace
cuánto corrió el último chequeo, porque un número vale lo que vale la corrida que lo produjo.

**Autenticación**, elegida por configuración:

| Configurado | Modo | Para qué |
|---|---|---|
| nada | `open` | uso local, sólo loopback |
| `FA_API_TOKEN` | `token` | tu red: una cuenta, un secreto compartido |
| `SUPABASE_*` | `supabase` | varios usuarios, cada uno con su cuenta aislada |

`serve --lan` **se niega a arrancar sin autenticación** y te genera un token para pegar.

## Tipos de alerta

| Tipo | Parámetros | Qué hace |
|---|---|---|
| `pct_up` / `pct_down` | `pct`, `reference` (`buy`\|`baseline`) | sube/baja X% contra el precio de compra o contra el precio congelado al crearla |
| `price_above` / `price_below` | `price` | precio objetivo (one-shot: se desactiva al dispararse) |
| `period_elapsed` | `months` o `days` | recordatorio de revisión N meses después de la compra, con el P&L del momento |
| `earnings_near` | `days` | avisa N días antes del próximo earnings call |
| `dividend_ex_near` | `days` | avisa N días antes de la fecha ex-dividendo |
| `trailing_stop` | `pct` | caída de X% desde el máximo alcanzado después de la compra |
| `atr_stop` | `multiple`, `period`, `lookback_days` | stop dimensionado por volatilidad en vez de por un porcentaje fijo |
| `sma_cross` | `fast`, `slow`, `direction` | golden/death cross; sólo dispara en la rueda del cruce |
| `sma_break` | `period`, `direction` | el precio cruza su media (filtro de tendencia) |
| `macd_cross` | `fast`, `slow`, `signal`, `direction` | la línea MACD cruza su señal |
| `rsi` | `period`, `overbought`, `oversold` | RSI de Wilder fuera de los umbrales |
| `rel_strength` | `pct`, `window` | rinde X puntos menos que el índice en la ventana elegida |
| `volume_spike` | `ratio`, `period` | volumen del día N veces su promedio reciente |
| `new_52w_high` / `new_52w_low` | `tolerance_pct` | nuevo extremo de 52 semanas |
| `split_detected` | `lookback_days` | detecta splits posteriores a la compra y sugiere el costo ajustado |

Las alertas que dependen de una compra (`period_elapsed`, `trailing_stop`, `atr_stop`,
`split_detected`) requieren una posición cargada. Cada alerta tiene *cooldown* para no
repetirse, puede tener fecha de expiración, y **cada evaluación queda registrada** — también
las que no dispararon, que es lo que permite ver cuánto le faltó.

## Qué se guarda

SQLite en `data/financial_analyzer.db` (WAL), o Postgres si definís `DATABASE_URL`.
17 tablas, esquema versionado con migraciones que respaldan la base antes de tocarla.

**Por cuenta** — `positions`, `transactions`, `alerts`, `alert_events`, `alert_evaluations`,
`delivery_attempts`, `portfolio_valuations`, `analyses`, `ai_suggestions`, `check_runs`.

**Compartidas** — `daily_bars`, `indicator_snapshots`, `price_snapshots`, `data_fetches`.
El precio de un papel es el mismo para todo el mundo, así que se baja una vez y lo leen todas
las cuentas.

Tres decisiones que valen la pena saber:

- **El libro mayor es la verdad.** `positions` es un resumen recalculable; `transactions` es
  append-only. Un split reescribe el resumen pero deja intacta la compra original.
- **Nada se borra de verdad.** Borrar una alerta la saca de circulación y conserva todo lo
  que disparó.
- **Toda corrida deja rastro**, dispare o no. Es la diferencia entre "el mercado estuvo
  tranquilo" y "el timer está muerto hace tres días".

## Reporte de IA: grounding y sugerencias

El reporte no se le pide "a ciegas". Antes de llamar al modelo se arma un **DATA PACK** con
precio (fuente + timestamp), cierre previo, rango anual, indicadores técnicos, calendario de
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
   redactado en lenguaje natural sin gastar cuota de Gemini.
3. **Reparar sugerencias**: si el modelo remoto escribió las alertas en prosa, el local las
   reconvierte a JSON válido contra el catálogo.

Si no hay modelo local configurado, las tres funciones degradan solas: el texto va crudo, el
digest muestra los datos sin redactar y las sugerencias quedan vacías. Cuando el modelo está
configurado pero falla (timeout de carga, presupuesto agotado por el *reasoning*), el mensaje
dice el motivo real en vez de fingir que no hay modelo.

Probado contra `llama-server` (llama.cpp) con gemma-4-E4B-it en `127.0.0.1:5001`.

## Arquitectura

```
fa/
├── config.py        settings inmutables desde el entorno
├── models.py        dataclasses frozen (Quote, Position, Transaction, Alert, Signal…)
├── providers/       yahoo (primario) → alphavantage → finnhub, unificados por chain.py
├── store/
│   ├── database.py  una interfaz sobre SQLite y Postgres (sin ORM)
│   ├── schema.py    el esquema actual, para una base que arranca de cero
│   ├── migrations.py  migraciones numeradas, transaccionales, con respaldo
│   └── …            CRUD por tabla, todo scopeado por cuenta
├── indicators/      SMA, EMA, RSI, MACD, Bollinger, ATR, volatilidad (funciones puras)
├── analytics.py     snapshot técnico completo de un ticker
├── metrics.py       FCF, equity, market cap, EV, yields desde fundamentals reales
├── ratios.py        deuda neta, márgenes, cobertura de intereses
├── ledger.py        tenencias derivadas del libro mayor (costo promedio)
├── alerts/          catálogo, reglas puras, alta compartida y motor de chequeo
├── notify/          console+log, telegram, notify-send y el dispatcher
├── ai_context.py    DATA PACK con procedencia y huecos declarados
├── localai.py       cliente OpenAI-compatible + local_tasks.py
├── api/             FastAPI: lectura, escritura y autenticación
├── ui/              gráficos, prompts, vistas, menú general y workspace por ticker
├── market.py        facade que arma el MarketContext (1 fetch por ticker por corrida)
├── portfolio.py     valuación y P&L
├── actions.py       operaciones compartidas por CLI y menú
└── cli.py           argparse

web/                 la PWA: sin framework, sin build, uPlot vendorizado
scripts/             instalador del timer de systemd
```

**Toda la cartera se valúa en USD.** Un papel que cotiza en otra moneda queda fuera del total
con su motivo: sumarlo sin tabla de cotizaciones daría un número que parece correcto y no lo
es. Cambiar eso no es editar una constante — hace falta una tabla de cambios y decidir qué
cotización aplica a una operación histórica.

## Tests

```bash
.venv/bin/python -m pytest --cov=fa --cov-report=term-missing

# y contra Postgres, que es el otro motor soportado
docker compose --profile dev up -d postgres
FA_TEST_DATABASE_URL=postgresql://fa:fa@localhost:55432/fa_test pytest
```

570 tests en SQLite y 575 en Postgres, 89% de cobertura, sin tocar la red: proveedores y
modelo local se ejercitan con payloads enlatados y el mercado se stubea.

Dos suites hacen de garantía y no de verificación:

- **`test_schema_equivalence`** construye la base de tres formas —SQLite nueva, SQLite migrada
  desde v2, Postgres nueva— y compara tablas, columnas e índices. Es lo que impide que el
  esquema y las migraciones se separen.
- **`test_api_auth`** le da a dos usuarios un set completo de datos y verifica endpoint por
  endpoint que ninguno vea al otro. El aislamiento está repartido en ~60 statements: confiar
  en la revisión no alcanza.
