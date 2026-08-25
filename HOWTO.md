# HOWTO — Financial Analyzer

Guía práctica de uso, en el orden en que la vas a necesitar.

---

## 1. Setup (una sola vez)

### Opción A — local

```bash
cd /data/bin/financial_analyzer
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Cargá las variables antes de usarlo (o metelas en tu `~/.bashrc`):

```bash
set -a; source .env; set +a
```

**Nada es obligatorio para empezar**: yfinance funciona sin API key. Las keys son para:

| Variable | Sin ella qué pasa |
|---|---|
| `GEMINI_API_KEY` | el reporte de IA avisa que falta la key y no corre; el resto anda igual |
| `ALPHA_VANTAGE_API_KEY` / `FINNHUB_API_KEY` | si yfinance falla, el comando corta con error (nunca inventa datos) |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | no hay push al celular, sólo consola + log |

### Opción B — Docker

```bash
docker compose build
docker compose run --rm analyzer          # menú interactivo
```

El compose corre como tu usuario (`user: "${UID:-1000}:${GID:-1000}"`) para que la base en
`./data` no quede root-owned. Si tu UID no es 1000, exportá `UID` y `GID` antes de levantarlo.
Para que el contenedor llegue a tu `llama-server` del host, la URL por defecto adentro es
`http://host.docker.internal:5001/v1`.

Dentro de Docker poné `FA_DESKTOP_NOTIFICATIONS=false` (ya viene así en el compose):
`notify-send` no tiene DBUS ahí.

### Opción C — Postgres o Supabase

Por defecto la base es un archivo SQLite en `./data` y no hace falta configurar nada. Si
preferís Postgres — porque lo vas a hostear, o porque querés varias cuentas — alcanza con
definir `DATABASE_URL`:

```bash
# en el .env
DATABASE_URL=postgresql://usuario:clave@host:5432/basededatos
```

Con esa variable presente se usa Postgres; sin ella, SQLite. No hay nada más que cambiar:
el esquema se crea solo la primera vez, igual que con SQLite.

Necesitás el driver, que no viene por defecto para que el modo local no cargue con él:

```bash
pip install "psycopg[binary]"
```

Con **Supabase**, la URL sale de *Project Settings → Database → Connection string*. Usá la
del **pooler en modo transacción** (puerto 6543), no la conexión directa: el chequeo
periódico y la app abren conexiones cortas y seguidas, y la directa se queda sin cupo.

Un detalle que conviene saber: en Postgres los payloads se guardan como `JSONB` nativo, así
que se pueden consultar desde SQL sin parsear:

```sql
SELECT ticker, rsi, payload->>'trend' FROM indicator_snapshots ORDER BY taken_at DESC;
```

### Migraciones y respaldo

El esquema tiene versión y se actualiza solo al abrir la base. Antes del primer cambio de
una actualización, en SQLite se deja un respaldo al lado del archivo:

```
data/financial_analyzer.db.pre-v10.bak
```

Si venís de una versión anterior no tenés que hacer nada: la primera corrida migra y avisa
en el log. Los datos existentes se conservan; ninguna migración borra nada.

### Correr los tests contra los dos motores

La suite corre contra SQLite por defecto. Para probar también Postgres:

```bash
docker compose --profile dev up -d postgres
FA_TEST_DATABASE_URL=postgresql://fa:fa@localhost:55432/fa_test pytest
docker compose --profile dev down          # cuando termines
```

El Postgres de dev es efímero a propósito (sin volumen): cada arranque es una base limpia.
Cada test usa su propio esquema, así que quedan tan aislados como con un archivo temporal.

### Modelo local (opcional)

Levantá tu servidor OpenAI-compatible (LM Studio → Developer → Start Server, o
`llama-server`) y apuntá el script:

```bash
export FA_LOCAL_AI_URL=http://127.0.0.1:5001/v1        # el puerto de tu llama-server
export FA_LOCAL_AI_MODEL=gemma-4-E4B-it-Q4_K_M.gguf    # el id exacto que expone el server
.venv/bin/python financial_analyzer.py local-ai        # lista los modelos disponibles
```

`local-ai` sin `FA_LOCAL_AI_MODEL` igual te lista los ids que expone el server: copiá de ahí.
Sin `FA_LOCAL_AI_MODEL` el modelo local queda apagado y todo lo demás sigue funcionando.

Dos cosas a saber con modelos que "razonan" (como gemma-4):

- El *reasoning* consume presupuesto de tokens. Si el resumen sale cortado o vacío, subí
  `FA_LOCAL_AI_MAX_TOKENS` (default 2000).
- La **primera** llamada también carga los pesos en memoria y puede tardar minutos.
  `FA_LOCAL_AI_TIMEOUT` está en 240 s por eso. Si se pasa, el mensaje te dice el motivo real
  (timeout) en vez de "no hay modelo local".

### Telegram en 2 minutos

1. Hablale a `@BotFather` → `/newbot` → te da el `TELEGRAM_BOT_TOKEN`.
2. Mandale un mensaje cualquiera a tu bot nuevo.
3. Abrí `https://api.telegram.org/bot<TOKEN>/getUpdates` y copiá el `chat.id` → `TELEGRAM_CHAT_ID`.

Para probar que llega: creá una alerta imposible de no disparar y corré `check-alerts`
(ej. `--kind price_above --param price=1`).

---

## 2. Flujo típico: cargar una compra y quedar monitoreado

```bash
.venv/bin/python financial_analyzer.py add-position PODD \
    --qty 10 --price 141.5 --date 2026-03-01 --with-default-alerts
```

`--with-default-alerts` te deja armado el kit básico:

- `pct_up` +10% y `pct_down` −10% contra tu precio de compra
- `trailing_stop` 15% desde el máximo posterior a la compra
- `earnings_near` 7 días antes del próximo balance
- `period_elapsed` a los 6 meses (recordatorio de revisar la tesis, con el P&L del momento)
- `split_detected` (avisa si hay split y te dice el costo ajustado)

Verificá:

```bash
.venv/bin/python financial_analyzer.py positions
.venv/bin/python financial_analyzer.py alerts
.venv/bin/python financial_analyzer.py portfolio     # P&L con precios en vivo
```

### Vender, y qué queda registrado

Se cierra desde el menú (opción *Ver / cerrar posiciones* → `c`). Pide **precio, fecha y
comisiones**, y con eso calcula el P&L realizado.

Si dejás el precio vacío, la posición se archiva **sin inventar la venta**: el resultado
queda desconocido en vez de equivocado. Es preferible para una posición vieja de la que ya no
te acordás a cuánto saliste.

### El libro mayor

Cada compra, venta, split y dividendo se escribe en `transactions`, que es **append-only**: no
se edita ni se borra nada. Tus tenencias —cantidad, costo promedio, P&L realizado, dividendos
cobrados— salen de replayar esas entradas, no de un campo guardado.

Eso tiene tres consecuencias prácticas:

- **Varias compras del mismo papel dan un costo promedio real.** Comprás 40 a 168 y después 20
  a 132: el costo es 156, no el de la primera compra.
- **Un split no destruye lo que pagaste.** El resumen se reescribe (40 acciones a 400 pasan a
  160 a 100), pero la compra original sigue entera en el libro. Antes se pisaba y no había
  vuelta atrás.
- **Borrar es lógico.** Sacar un movimiento lo quita del cálculo y lo deja registrado.

Desde el dashboard también se cargan movimientos, incluidos dividendos y comisiones, que por
CLI todavía no tienen comando propio. **Da igual por dónde cargues**: la terminal y la app
escriben en el mismo libro y leen el mismo resumen.

Si cargás un ticker que el sistema no tenía, **va a buscar su historial solo**. Tarda un par
de segundos y la app avisa mientras lo trae; no hace falta correr nada a mano.

### Corregir un movimiento

Cada fila del libro tiene **Editar**. Te olvidaste la comisión, pusiste mal el precio, erraste
la fecha: se arregla ahí, con el formulario precargado.

Por dentro **no se sobrescribe nada**. La entrada vieja se retira y la corregida ocupa su
lugar apuntando a la que reemplaza. Eso es lo que mantiene el libro append-only: un typo
corregido y un número cambiado tres meses después siguen siendo distinguibles, que es toda la
razón de tener un libro mayor y no una tabla de valores actuales.

En la lista aparece la versión vigente, marcada como `corregido`. El original queda guardado
y se puede recuperar; la cuenta usa sólo la corrección, nunca las dos.

---

## 3. Trabajar sobre un ticker

Todo lo del punto anterior se hace más cómodo fijando un ticker activo: queda seteado y no lo
tipeás más.

```bash
.venv/bin/python financial_analyzer.py use PODD    # fija el ticker activo
.venv/bin/python financial_analyzer.py analyze --ai
.venv/bin/python financial_analyzer.py add-alert --kind price_above --param price=200
.venv/bin/python financial_analyzer.py check-alerts --ticker PODD
.venv/bin/python financial_analyzer.py unuse       # vuelve al modo general
```

En el menú interactivo es la opción 1 del menú general. Entrás al **workspace del ticker**:

```
📌 TRABAJANDO SOBRE PODD — posición: 10 acciones (costo 1,415.00) | alertas activas: 6
yahoo: PODD = 148.74 USD (-0.41% vs cierre previo) @ 2026-08-20T17:22:11+00:00

 1. Análisis anual (YtoY)          6. Cargar compra
 2. Análisis trimestral (QtoQ)     7. Chequear sus alertas ahora
 3. Evaluación estratégica con IA  8. Historial disparado
 4. Ver / borrar sus alertas       9. Ajustar posición por split
 5. Crear alerta                  10. Sugerencias pendientes de la IA
 c. Cambiar de ticker              x. Salir del ticker (menú general)
 0. Salir del programa
```

- `x` des-setea el ticker y volvés al menú general.
- `c` cambia directo a otro ticker.
- El ticker activo se guarda en la base: si cerrás y volvés a entrar, seguís donde estabas.
- Un ticker sin compra cargada funciona igual (aparece como *watchlist*).

## 4. Alertas a medida

Ver el catálogo completo con sus parámetros y defaults:

```bash
.venv/bin/python financial_analyzer.py kinds
```

Ejemplos reales:

```bash
# Precio objetivo (se desactiva sola al dispararse)
financial_analyzer.py add-alert PODD --kind price_above --param price=200
financial_analyzer.py add-alert PODD --kind price_below --param price=120

# Baja 8% desde HOY, sin tener la acción comprada (congela el precio de hoy como referencia)
financial_analyzer.py add-alert NVDA --kind pct_down --param pct=8 --param reference=baseline

# Earnings con 14 días de anticipación
financial_analyzer.py add-alert AAPL --kind earnings_near --param days=14

# Golden/death cross clásico
financial_analyzer.py add-alert PODD --kind sma_cross --param fast=50 --param slow=200 --param direction=any

# RSI de Wilder
financial_analyzer.py add-alert PODD --kind rsi --param period=14 --param overbought=70 --param oversold=30

# Dividendo: aviso antes de la fecha ex
financial_analyzer.py add-alert KO --kind dividend_ex_near --param days=5

# Revisión a los 3 meses en vez de 6
financial_analyzer.py add-alert PODD --kind period_elapsed --param months=3

# Que no se repita más de una vez por semana
financial_analyzer.py add-alert PODD --kind pct_down --param pct=10 --cooldown 168

# Que caduque sola a fin de año
financial_analyzer.py add-alert PODD --kind price_above --param price=250 --expires 2026-12-31
```

### Alertas de indicadores

```bash
# Pierde 10 puntos porcentuales contra el índice en 6 meses
financial_analyzer.py add-alert RH --kind rel_strength --param pct=10 --param window=6m

# Stop dimensionado por volatilidad: 3 x ATR bajo el máximo de los últimos 90 días
financial_analyzer.py add-alert RH --kind atr_stop --param multiple=3 --param lookback_days=90

# Volumen del día 2.5 veces su promedio de 20 ruedas
financial_analyzer.py add-alert RH --kind volume_spike --param ratio=2.5

# Nuevo máximo (o mínimo) de 52 semanas
financial_analyzer.py add-alert RH --kind new_52w_high
financial_analyzer.py add-alert RH --kind new_52w_low --param tolerance_pct=1

# El precio perfora la SMA200 (filtro de tendencia)
financial_analyzer.py add-alert RH --kind sma_break --param period=200 --param direction=below

# Cruce de MACD
financial_analyzer.py add-alert RH --kind macd_cross --param direction=above
```

Estas dependen de datos que no todos los proveedores entregan, y **cuando el dato
falta la alerta no dispara en vez de asumir**:

| Alerta | Necesita | Si falta |
|---|---|---|
| `rel_strength` | histórico del benchmark (`FA_BENCHMARK`, default SPY) | no dispara |
| `atr_stop`, `volume_spike` | barras con high/low/volumen | no dispara |
| `sma_break`, `macd_cross` | historial largo (200 ruedas para la SMA200) | no dispara |

Dos detalles que importan:

- `atr_stop` con `lookback_days=0` (default) mide desde el máximo posterior a la
  compra. Si compraste hace mucho y la acción se derrumbó, ese máximo puede tener
  años y el stop deja de ser accionable: poné una ventana (`lookback_days=90`)
  para que el pico y el ATR estén en la misma escala temporal.
- `volume_spike` ignora la rueda en curso. Intradía el proveedor devuelve una barra
  parcial cuyo volumen es una fracción del real, y compararla contra el promedio
  daría siempre un valor ridículo.

Reglas a tener en cuenta:

- `reference=buy` (default) usa tu precio de compra → necesita posición cargada.
  `reference=baseline` congela el precio del momento en que creás la alerta → sirve para watchlist.
- `period_elapsed`, `trailing_stop`, `atr_stop` y `split_detected` **exigen** una posición cargada.
- Toda alerta tiene *cooldown* (default 24 h, configurable con `--cooldown` o `FA_COOLDOWN_HOURS`)
  para no spamearte. Las de precio objetivo son *one-shot*: se apagan al dispararse.
- `sma_cross`, `sma_break` y `macd_cross` sólo disparan **en la rueda del cruce**, no todos los
  días que la condición siga vigente.

Activar/desactivar/borrar: menú interactivo, opción 8.

---

## 5. Chequear alertas

A mano:

```bash
.venv/bin/python financial_analyzer.py check-alerts
```

### Automático con systemd (recomendado)

Hay un script que deja todo armado:

```bash
./scripts/setup-systemd.sh
```

Instala dos units en `~/.config/systemd/user/`: `fa-alerts.service` (corre
`docker compose run --rm -T alerts` una vez) y `fa-alerts.timer` (lo dispara según
el horario). No queda ningún daemon residente: el timer duerme, levanta un
container efímero, notifica y muere. Por eso `ps` no muestra nada — para ver si
está vivo se usa `systemctl --user list-timers`.

Antes de escribir nada verifica que existan systemd `--user`, Docker con Compose
v2, el daemon accesible y el `compose.yaml`; avisa (sin abortar) si falta el
`.env` o si `linger` está apagado.

Horario por defecto: `Mon-Fri 10..18:00:00`, o sea cada hora en días hábiles.
Está pensado para **America/Argentina/Buenos_Aires (-03)**, donde ese rango cubre
el mercado US tanto en EDT (10:30–17:00 local) como en EST (11:30–18:00 local).
`OnCalendar` usa la hora local de la máquina, así que en otra zona horaria hay que
ajustarlo:

```bash
./scripts/setup-systemd.sh --schedule "Mon-Fri *:0/30"   # cada 30 min
./scripts/setup-systemd.sh --schedule "hourly"           # cada hora, todos los días
./scripts/setup-systemd.sh --schedule "Mon-Fri 9..17:00:00"
./scripts/setup-systemd.sh --no-test                     # sin corrida de prueba
./scripts/setup-systemd.sh --uninstall                   # sacar el timer
```

Ventajas sobre cron: `Persistent=true` recupera la corrida si la máquina estaba
suspendida a esa hora (cron simplemente la pierde), los logs van al journal en vez
de a un archivo que crece sin límite, y `WorkingDirectory` hace que Compose
encuentre `compose.yaml` y `.env` sin trucos.

Dos cosas que el script no puede resolver solo:

```bash
sudo loginctl enable-linger $USER   # si no, el timer se apaga al cerrar sesión
sudo usermod -aG docker $USER       # si el daemon no es accesible (requiere relogin)
```

Operación:

```bash
journalctl --user -u fa-alerts.service -n 50 --no-pager   # últimas corridas
journalctl --user -u fa-alerts.service -f                 # seguir en vivo
systemctl --user list-timers fa-alerts.timer              # próxima corrida
systemctl --user start fa-alerts.service                  # forzar una corrida
```

### Automático con cron

Si no tenés systemd (`crontab -e`):

```cron
# cada 30 min en horario de mercado NY, lun-vie
# OJO: cron arranca en $HOME, así que el cd y las rutas absolutas son obligatorios.
*/30 9-17 * * 1-5  cd /data/bin/financial_analyzer && set -a && . ./.env && set +a && ./.venv/bin/python financial_analyzer.py check-alerts --quiet >> data/cron.log 2>&1
```

Si preferís no depender del `.env`, exportá las variables directas en el crontab
(`TELEGRAM_BOT_TOKEN=...` en líneas propias arriba de la entrada) y sacá el `set -a`.

Con Docker:

```cron
*/30 9-17 * * 1-5  cd /data/bin/financial_analyzer && docker compose run --rm alerts >> data/cron.log 2>&1
```

Detalles:

- `--quiet` no imprime en consola pero **sí manda Telegram y notify-send** y guarda todo en la DB.
- `--json` devuelve un resumen machine-readable (para encadenar con otro script).
- Exit code: `0` todo bien, `2` algún ticker se quedó sin datos, `1` error de configuración.
- Lo que se disparó queda en `data/alerts.log` y en la tabla `alert_events`.

Ver el historial:

```bash
.venv/bin/python financial_analyzer.py history --limit 20
```

Al abrir el menú interactivo te muestra primero las alertas que todavía no marcaste como vistas.

---

## 6. Dashboard web

Un servidor con la API y la app móvil en el mismo puerto. Lee **sólo lo que ya está
guardado**: no sale a pedir precios, así que abrir la pantalla no dispara ninguna descarga
y funciona aunque yfinance esté caído.

```bash
pip install "fastapi>=0.115" "uvicorn[standard]>=0.32"
python financial_analyzer.py serve
# → http://127.0.0.1:8000
```

Con Docker hay un servicio dedicado:

```bash
docker compose up -d dashboard      # → http://localhost:8000
docker compose logs -f dashboard    # para ver qué hizo al arrancar
docker compose down dashboard       # para bajarlo
```

Queda levantado solo (`restart: unless-stopped`), así que sobrevive a un reinicio.

**El puerto se publica en `127.0.0.1:8000` a propósito**: adentro del contenedor el proceso
escucha en `0.0.0.0` porque es la única forma de ser alcanzable, pero quién llega de verdad
lo decide el mapeo del compose. Para abrirlo a la red, poné `FA_API_TOKEN` en el `.env` y
cambiá el mapeo a `"8000:8000"`.

El servicio monta `./web`, así que un cambio en el cliente se ve recargando la página, sin
rebuild de la imagen.

Por defecto escucha sólo en `127.0.0.1`, así que no sale de la máquina.

### Verla desde el celular en la red local

```bash
# 1. poné un token en el .env
FA_API_TOKEN=pegá-acá-una-cadena-larga-y-random

# 2. levantá para toda la red
python financial_analyzer.py serve --lan
# 📊 Dashboard en http://192.168.0.14:8000
```

Abrí esa URL en el celular, pegá el token una vez y queda guardado. `--lan` **se niega a
arrancar sin autenticación**: sin token, cualquiera en la red vería la cartera y podría
cargar movimientos. Si la red es de confianza y querés saltearlo igual, existe
`--lan --insecure`, pero es tu responsabilidad.

Para llegar desde afuera de tu casa, una VPN tipo Tailscale sigue siendo mejor que abrir
el puerto en el router.

### Modos de acceso

El servidor elige el modo solo, según lo que haya configurado:

| Configuración | Modo | Para qué |
|---|---|---|
| nada | `open` | Uso local, sólo loopback |
| `FA_API_TOKEN` | `token` | Tu red: una cuenta, un secreto compartido |
| `SUPABASE_URL` + `SUPABASE_JWT_SECRET` | `supabase` | Varios usuarios, cada uno con su cuenta |

Con Supabase, los valores salen de *Project Settings → API* (la URL y la clave `anon`) y de
*Project Settings → API → JWT Settings* (el secreto). Necesitás además el paquete `pyjwt`.
La app muestra un login con email y contraseña, y **cada usuario nuevo estrena su propia
cuenta la primera vez que entra**: sus posiciones, alertas e historial no se cruzan con los
de nadie.

```bash
pip install pyjwt
```

### Instalarla en el celular

Es una PWA: se instala sin tienda. Abrí la URL en el navegador del teléfono y usá
*Agregar a pantalla de inicio* (Chrome: menú ⋮ → Instalar app; Safari: Compartir →
Agregar a inicio). Queda como una app más, a pantalla completa.

### Qué muestra

Dos secciones en la barra inferior.

**Cartera** — el valor total con su P&L, la curva de equity, cada tenencia con su peso en
la cartera y el libro mayor de movimientos. Las tenencias salen del ledger, así que el
costo es el promedio real de todas tus compras y el P&L realizado incluye lo que ya
vendiste.

**Tickers** — la pantalla de un papel, con tres pestañas:

| Pestaña | Contenido |
|---|---|
| **Precio** | Cierres con SMA50 y SMA200, ventanas de 1M a 5A, y la grilla de indicadores |
| **Indicador** | Un indicador a lo largo del tiempo — el historial que antes no se guardaba |
| **Alertas** | Cada alerta con sus parámetros y cómo salió en la última corrida, más los disparos |
| **Números** | Los estados contables: resumen (ingresos, FCF, deuda neta, yields) y calidad del negocio (márgenes, crecimiento, cobertura, ROE), anual y trimestral |
| **IA** | Pedir el informe estratégico, leerlo, y convertir sus sugerencias en alertas |

Los precios son **el último cierre guardado**, no una cotización en vivo: **ninguna pantalla
sale a pedir datos**. La única excepción es cargar un ticker nuevo, que sí dispara la primera
descarga — es una escritura, la pediste vos, y pasa una sola vez por papel. Si una posición no tiene velas guardadas, la app avisa que quedó
afuera del total en vez de valuarla en cero.

**Todo se valúa en USD.** Si un papel cotiza en otra moneda —Yahoo devuelve la moneda real
del listado— queda fuera del total y la app dice cuál y por qué. Sumarlo sin tabla de
cotizaciones daría un número que parece correcto y no lo es. Cargar una operación en otra
moneda también se rechaza, con el mismo motivo.

### Cargar cosas desde el teléfono

Desde la app se pueden **crear alertas**, silenciarlas o borrarlas, **cargar movimientos**
(compra, venta, dividendo, split, comisión) y **marcar avisos como vistos**.

Desde la pestaña **IA** se pide el informe a Gemini. Tarda entre medio minuto y un minuto,
así que corre en segundo plano y la pantalla avisa cuando está. Lo que devuelve queda
guardado: los informes viejos se leen sin volver a pedirlos.

Las **sugerencias** que propone se aceptan con un botón y se vuelven alertas de verdad. Las
que no son automatizables —"tomar ganancias parciales", "mirar el balance del 10/09"— se
muestran distinto y lo dicen: son una acción tuya, no una regla que el sistema pueda vigilar.

La pestaña **Números** son las mismas tablas que muestran `analyze --period annual` y
`--period quarterly`, con los períodos en columnas: la comparación que uno hace es una línea
a lo largo del tiempo, y eso se lee mejor como fila. Se guardan al traerlas, así que abrir la
pantalla no descarga nada; se refrescan solas cuando pasan más de siete días, que es el ritmo
al que se mueve un balance.

Si la deuda neta de algún período es estimada —porque el proveedor no reportó deuda total y
caja— la pantalla lo dice arriba: esa estimación se propaga al EV y a su yield.

En la pestaña *Alertas* hay además **Chequear ahora**, que evalúa las alertas de ese papel sin
esperar al timer.

El formulario de alerta se arma solo a partir del catálogo: los 18 tipos con sus valores
por defecto y sus opciones salen de `fa/alerts/kinds.py`, así que un tipo nuevo aparece en
la app sin tocar el cliente. La validación es exactamente la misma que usa la terminal —
si un parámetro no sirve en el CLI, tampoco sirve acá, y el mensaje de error es el mismo.

Dos cosas a tener en cuenta:

- Una alerta de porcentaje con referencia *precio de hoy* se ancla al **último cierre
  guardado**, no a una cotización en vivo. Si el ticker todavía no tiene velas, la app te
  lo dice en vez de anclar a un número inventado.
- **Borrar es lógico**: la alerta deja de correr, pero todo lo que disparó se conserva.
  Lo mismo con los movimientos.

El indicador del encabezado dice hace cuánto corrió el último chequeo. Si dice `sin
corridas` o se pone amarillo, los números que estás viendo son viejos: eso es más
importante que los números mismos.

La pestaña *Indicador* y la curva de equity arrancan vacías en una instalación nueva.
Cada corrida de `check-alerts` agrega un punto a las dos; después de unos días hay serie
para mirar.

## 7. Análisis

```bash
# Métricas anuales y trimestrales reales (balance, FCF, EV, yields)
.venv/bin/python financial_analyzer.py analyze PODD

# Sólo trimestral
.venv/bin/python financial_analyzer.py analyze PODD --period quarterly

# Con veredicto estratégico de Gemini (corto/medio/largo plazo)
.venv/bin/python financial_analyzer.py analyze PODD --ai

# Cruzando contra el texto que copiaste de tu app de inversión
.venv/bin/python financial_analyzer.py analyze PODD --ai --context "$(cat /tmp/recomendacion.txt)"
```

Celdas en `n/a` = el proveedor no publicó esa línea del balance. No se rellenan con estimaciones.

El análisis imprime tres bloques: el **snapshot técnico**, la tabla de **valuación**
(revenue, FCF, deuda neta, yields) y la de **calidad del negocio** (márgenes,
crecimiento, cobertura de intereses, conversión de FCF, ROE, apalancamiento).
Con `--no-technicals` se omite el primero.

### Deuda neta: real, no estimada

La deuda neta sale de **deuda total menos caja** cuando el proveedor las publica.
Sólo si faltan esas líneas se cae a una aproximación (40% del pasivo total), y en
ese caso la tabla lo dice explícitamente y el DATA PACK avisa al modelo de que todo
lo derivado del EV hereda esa aproximación. La columna `Net_Debt_Estimated` marca
qué períodos son estimados.

Importa más de lo que parece: en una empresa apalancada la diferencia entre la
deuda real y el proxy distorsiona el EV y por lo tanto el `EV_FCF_Yield`, que es la
métrica central de la comparativa.

### Indicadores técnicos

```bash
# Snapshot completo: tendencia, RSI, MACD, Bollinger, ATR, 52 semanas, fuerza relativa
.venv/bin/python financial_analyzer.py technicals RH

# Machine readable (para encadenar con otro script)
.venv/bin/python financial_analyzer.py technicals RH --json
```

Salida real:

```
📐 TÉCNICOS — RH (1255 ruedas)
  Tendencia      bajista    precio vs SMA200 -5.03% ▼
  SMA50/SMA200   169.31 / 163.17
  RSI(14)        37.38      %B Bollinger 0.06      MACD sin cruce
  Volatilidad    61.25% anual   ATR(14) 9.00 (5.81%)
  Rango 52s      106.30 – 257.00   desde máx -39.70%   sobre mín +45.78%
  Drawdown máx   84.43%    volumen vs 20d 1.28x
  Retorno              1m         3m         6m        12m
                  -11.20%    +13.59%    -18.18%    -29.17%
  vs SPY          -14.50p    +11.22p    -30.03p    -49.28p

  En 12 meses RH pierde contra SPY.
```

La fila **vs SPY** es la que responde la pregunta que ninguna otra métrica contesta:
si la caída es del papel o es que se cayó todo el mercado. Se mide en puntos
porcentuales de exceso sobre el benchmark. Cambiá el índice con `FA_BENCHMARK=QQQ`.

Todo lo que no se puede calcular sale como `n/a` y se lista abajo en "Sin datos para":
un historial corto no da SMA200, y un proveedor que sólo entrega cierres no da ni ATR
ni volumen relativo. Nunca se rellena con un cero.

### Qué recibe la IA (y por qué no inventa)

Antes de llamar al modelo se arma un **DATA PACK** con todo fechado y con su fuente:

- precio, cierre previo, fuente y timestamp exacto de la consulta
- rango del último año, últimos 5 cierres, SMA50 / SMA200 / RSI14
- próximo earnings, próxima fecha ex-dividendo, splits recientes
- estados contables con su fecha de corte y la fuente que los publicó
- tu posición (costo, fecha, P&L actual) y tus alertas activas
- si pegaste texto de tu app y tenés modelo local, sus afirmaciones extraídas como
  *claims sin verificar* (no como datos)

El prompt le prohíbe usar precios o fechas de su entrenamiento, le exige citar la fecha de cada
número, y lo que no pudimos traer va en una sección **MISSING DATA** con la orden de decir
"no disponible" en lugar de estimar. Arriba del reporte se imprime la línea de procedencia:

```
📊 Datos: precio yahoo @ 2026-08-20T17:22:11+00:00 · fundamentals yahoo · histórico 1258 cierres · faltantes: 1
```

Si ves `faltantes: N` alto, el reporte se hizo con huecos: fijate la sección "Verificación de
datos" del informe, que dice explícitamente con qué se quedó corto.

## 8. Sugerencias de la IA

El reporte devuelve, además del texto, un bloque estructurado con **alertas y acciones
sugeridas**. Quedan guardadas como pendientes y se aplican de a una:

```bash
.venv/bin/python financial_analyzer.py suggestions            # ver las pendientes
.venv/bin/python financial_analyzer.py suggestions --review   # decidir una por una
.venv/bin/python financial_analyzer.py suggestions --status accepted
```

En el review, por cada sugerencia:

| Tecla | Qué hace |
|---|---|
| `s` | crea la alerta tal cual la propuso, con el motivo guardado en la nota |
| `e` | te deja editar cada parámetro antes de crearla (ej. bajar el precio objetivo) |
| `n` | la descarta |
| ENTER | la deja pendiente para después |
| `q` | corta el review sin tocar el resto |

En el menú interactivo aparece sola al terminar el reporte, y también en la opción 10 del
workspace del ticker (o la 8 del menú general para ver todas).

Las **acciones** que no se pueden automatizar ("tomar ganancias parciales", "esperar al
earnings") se listan igual pero no se convierten en alerta: quedan como recordatorio. Si el
modelo propone un tipo de alerta que no existe, o con parámetros inválidos, se degrada a acción
en vez de perderse.

## 9. Digest del portfolio (modelo local)

```bash
.venv/bin/python financial_analyzer.py digest              # resumen redactado
.venv/bin/python financial_analyzer.py digest --facts-only # sólo los datos, sin IA
```

Junta P&L por posición, qué tan lejos está cada alerta de dispararse, earnings próximos y las
últimas alertas disparadas; el modelo local lo redacta. Sin modelo local devuelve los datos
crudos y sale con código 2. Cron-eable para tener el resumen cada mañana:

```cron
0 9 * * 1-5  ./.venv/bin/python financial_analyzer.py digest >> data/digest.log 2>&1
```

---

## 10. Menú interactivo

```bash
.venv/bin/python financial_analyzer.py
```

```
--- MENÚ GENERAL ---
 1. Trabajar sobre un ticker        5. Chequear todas las alertas ahora
 2. Portfolio (P&L en vivo)         6. Historial de alertas disparadas
 3. Ver / cerrar posiciones         7. Digest del portfolio (IA local)
 4. Ver / borrar alertas            8. Sugerencias pendientes de la IA
 0. Salir
```

La opción 1 abre el workspace por ticker descripto en la sección 3.

---

## 11. Casos puntuales

**Hubo un split y tu costo quedó mal** → menú opción 11, o mirá lo que sugirió la alerta
`split_detected`. Ratio `4` = 4-for-1: divide el precio de compra por 4 y multiplica la cantidad
por 4 (el costo total no cambia).

**Vendiste** → menú opción 6 → `c` (cerrar). La posición sale del portfolio pero queda en la DB
y no se borra el historial de alertas.

**Querés seguir un ticker que no tenés** → creá alertas con `reference=baseline` o de tipo
`price_above` / `earnings_near` / `rsi`; no hace falta cargar posición.

**Backup** → todo vive en `data/financial_analyzer.db`. Copiá ese archivo (y `alerts.log` si
te importa el histórico plano).

**Inspeccionar la DB a mano**:

```bash
sqlite3 data/financial_analyzer.db "SELECT ticker, kind, params, last_fired_at FROM alerts WHERE active=1;"
```

---

## 12. Si algo falla

| Síntoma | Causa / arreglo |
|---|---|
| `No market data provider answered` | yfinance no respondió y no hay keys de fallback. Poné `ALPHA_VANTAGE_API_KEY` o `FINNHUB_API_KEY`, o reintentá. |
| `check-alerts` sale con código 2 | algún ticker se quedó sin datos; el detalle sale en la sección "Errores de datos". El resto de las alertas igual se evaluó. |
| No llega el Telegram | probá `curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe"`. Si da `ok:true`, revisá el `chat.id`. Los fallos de entrega quedan en el campo `delivered` del evento. |
| No aparece la notificación de escritorio | `which notify-send`; dentro de Docker no funciona (esperado). |
| La alerta no vuelve a saltar | está en cooldown (default 24 h) o es *one-shot* y ya se desactivó. Miralo con `alerts`. |
| Ticker desconocido | usá el símbolo tal cual lo lista Yahoo (ej. `BRK-B`, no `BRK.B`). |
| Debug | agregá `-v` antes del subcomando: `financial_analyzer.py -v check-alerts`. |
| `local model server unreachable` | el server local no está levantado o `FA_LOCAL_AI_URL` apunta mal. Verificá con `financial_analyzer.py local-ai`. Ojo: el puerto 1234 suele estar tomado por otra cosa (`rdbg`, por ejemplo). |
| `the local model returned no content` | el modelo gastó el presupuesto razonando. Subí `FA_LOCAL_AI_MAX_TOKENS`. |
| `No se puede abrir la base ... readonly` | el archivo lo creó Docker corriendo como root. Corré `sudo chown -R $(id -u):$(id -g) data/`. Ya no vuelve a pasar: el compose corre como tu usuario. |
| El reporte no trajo sugerencias | el modelo no devolvió el bloque JSON. Con modelo local configurado se intenta reparar automáticamente; sin él, quedan vacías. |
| `No indicaste ticker y no hay uno activo` | pasá el ticker como argumento o fijalo con `use TICKER`. |
