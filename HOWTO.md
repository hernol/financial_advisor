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

Reglas a tener en cuenta:

- `reference=buy` (default) usa tu precio de compra → necesita posición cargada.
  `reference=baseline` congela el precio del momento en que creás la alerta → sirve para watchlist.
- `period_elapsed`, `trailing_stop` y `split_detected` **exigen** una posición cargada.
- Toda alerta tiene *cooldown* (default 24 h, configurable con `--cooldown` o `FA_COOLDOWN_HOURS`)
  para no spamearte. Las de precio objetivo son *one-shot*: se apagan al dispararse.
- `sma_cross` sólo dispara **en la rueda del cruce**, no todos los días que la media siga arriba.

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

## 6. Análisis

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

## 7. Sugerencias de la IA

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

## 8. Digest del portfolio (modelo local)

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

## 9. Menú interactivo

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

## 10. Casos puntuales

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

## 11. Si algo falla

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
