# Spec: mover el backend a un servidor y llegar al APK

Documento de trabajo para continuar en otra sesión. Escrito el 2026-08-26,
actualizado el 2026-08-28 contra el commit `598c277` de `main`.

**Objetivo final**: backend hosteado, clientes móviles, suscripciones.
**Objetivo inmediato**: correr esto en un servidor propio, con un solo usuario
(el dueño). Todo lo demás cuelga de eso.

---

## 1. Estado actual, para no rehacer lo que está hecho

Lo que sigue ya funciona y tiene tests. No hace falta rediseñarlo.

| Pieza | Estado |
|---|---|
| Esquema | v13, migraciones versionadas con backup `VACUUM INTO` y `PRAGMA foreign_key_check` |
| Motores | SQLite **y** Postgres, sin ORM. `fa/store/database.py` traduce paramstyle (`?`→`%s`), `RETURNING id` y DDL por dialecto |
| Suite | 802 en SQLite, 807 en Postgres (`FA_TEST_DATABASE_URL=postgresql://…`) |
| Multi-tenant | `TENANT_TABLES` por `account_id`, `SHARED_TABLES` por ticker. Aislamiento con tests que lo garantizan |
| Auth | `fa/api/auth.py::mode_for()` → `supabase` \| `token` \| `open`, según qué variables estén puestas |
| Cliente | PWA sin build: manifest, service worker, uPlot vendorizado. 200 KB en total |
| Datos | `ProviderChain`: Yahoo → Alpha Vantage → Finnhub. **Nunca simula**: si fallan todos, corta con error |

El split `SHARED`/`TENANT` es la decisión cara y ya está tomada correctamente para
un servicio hosteado: los precios de AAPL se traen una vez y sirven a todos los
usuarios; las posiciones, alertas y movimientos son por cuenta.

---

## 2. Bloqueos concretos, verificados

### 2.1 `psycopg` no está en la imagen — bloqueante

```
docker compose exec dashboard python -c "import psycopg"
→ ModuleNotFoundError
```

Está comentado en `requirements.txt`:

```
# Optional: only needed when DATABASE_URL points at Postgres or Supabase.
# psycopg[binary]>=3.2
```

En modo local con SQLite no hace falta, y por eso quedó así. En el servidor, con
`DATABASE_URL` apuntando a Postgres, el contenedor arranca y muere.

**Acción**: descomentarlo, o mejor, dos targets en el Dockerfile (`local` y
`server`) para no cargar el driver donde no se usa.

### 2.2 La imagen es Python 3.11, el desarrollo es 3.14

`Dockerfile` dice `FROM python:3.11-slim`; el `.venv` local corre 3.14.0. Los
tests que corren en local no están probando la versión que corre en producción.
Alinear las dos, o al menos correr la suite dentro de la imagen antes de
desplegar.

### 2.3 El timer de alertas es por máquina, no por servicio

`scripts/setup-systemd.sh` instala un timer de systemd **en modo `--user`** que
levanta un contenedor efímero con `check-alerts`. Sirve para una laptop, no para
un servidor con varias cuentas:

- Es `--user`: muere si el usuario no tiene lingering habilitado.
- `check-alerts` recorre *todos* los tickers seguidos y evalúa *todas* las
  alertas en un solo proceso secuencial. Con N usuarios eso escala mal y, peor,
  vuelve a traer el mismo ticker una vez por cuenta.

**Acción** (fase 4): separar en dos trabajos, que es como debería haber estado
desde el principio ahora que hay más de una cuenta:

1. **Refresco por ticker** — un solo fetch por símbolo, escribe en `SHARED_TABLES`.
2. **Evaluación por cuenta** — lee lo guardado, evalúa alertas, notifica. No toca
   la red.

Esa separación ya existe conceptualmente en el código (`fa/warm.py` documenta
"reads never fetch, and the one write that introduces a new ticker does").

### 2.4 Cada tarea de fondo abre su propia conexión

Desde `598c277`, las tareas de fondo ya no le prestan la conexión al request:
`deps.in_background()` les abre una y la cierra al terminar. Fue por un bug real
—un `/portfolio/history` devolvió 500 con `sqlite3.InterfaceError` mientras
corría un refresh masivo— y en un servidor con varias cuentas importa más, no
menos.

**Consecuencia para Postgres**: cada refresh, cada informe de IA y cada
`check-alerts` on-demand abre una conexión mientras dura. Con el pooler de
Supabase en modo transacción (puerto 6543) el límite de conexiones es real y
bajo. Si el fan-out de la fase 4 lanza N trabajos en paralelo, hay que
dimensionarlo contra ese límite o poner un pool del lado de la app.

Hay un detalle que casi se cuela y conviene recordar: la primera versión del
helper decidía por `_database is not None`. El arranque setea eso, así que
devolvía la conexión compartida y el arreglo **no hacía nada en producción
mientras pasaba todos los tests** — los tests son justamente el caso donde la
base viene inyectada. Decide por `_owned`.

### 2.5 CORS está clavado a un puerto de desarrollo

`fa/api/app.py` permite sólo `http://localhost:5173` y `http://127.0.0.1:5173`,
sin variable de entorno que lo cambie. Hoy no molesta porque el cliente se sirve
del mismo origen que la API. Molesta en el momento en que se separen — ver la
sección 6.

### 2.6 Yahoo prohíbe el uso comercial — bloqueante para monetizar

`yfinance` es la fuente primaria. Sus términos no permiten uso comercial. En el
momento en que se cobra una suscripción, el proyecto queda afuera.

**Lo bueno**: `AlphaVantageProvider` y `FinnhubProvider` ya existen en la cadena
y ambos tienen planes comerciales. Es cambiar contrato y key, no reescribir.

**Hacerlo antes del primer cobro, no después.**

### 2.7 Cobrar por recomendaciones puede ser asesoramiento regulado

El informe de IA sugiere comprar y vender. Cobrar por eso puede caer bajo
regulación de asesoramiento financiero, según la jurisdicción de los usuarios.
Consulta legal, no técnica. Fuera del alcance de este documento pero **en el
camino crítico de la monetización**.

---

## 3. Arquitectura destino

```
  Android (TWA)  ─┐
  PWA navegador  ─┼──►  HTTPS  ──►  Caddy  ──►  uvicorn (fa serve)
  CLI local      ─┘                              │
                                                 ├──►  Postgres (gestionado)
                                                 └──►  ProviderChain (datos)

  Timers del servidor:
    refresh-tickers  (por símbolo, escribe SHARED_TABLES)
    check-alerts     (por cuenta, lee y notifica)
```

**Por qué TWA y no Capacitor**: el TWA es un APK publicable en Play Store que
abre la PWA a pantalla completa, y **se actualiza cuando se pushea el web**. Con
Capacitor cada cambio es un APK nuevo que cada usuario tiene que aceptar, y se
terminan soportando varias versiones de cliente contra un backend solo. Con
usuarios pagos eso es un problema real. Play Billing funciona en TWA vía Digital
Goods API y Web Push anda en Android, así que no hace falta nada nativo.

---

## 4. Fases

Cada fase termina con algo verificable. No empezar la siguiente sin eso.

### Fase 0 — Preparar la imagen

- Descomentar `psycopg[binary]>=3.2` en `requirements.txt`.
- Alinear la versión de Python entre `Dockerfile` y el entorno de desarrollo.
- Correr la suite **dentro de la imagen**, no sólo en el venv.

*Listo cuando*: `docker compose run --rm analyzer python -m pytest` pasa, y
`python -c "import psycopg"` funciona dentro del contenedor.

### Fase 1 — Servidor y base

- VPS con Docker. Postgres **gestionado** (Supabase, Neon, RDS): la base es el
  único estado que importa y no conviene operarla a mano.
- `DATABASE_URL` apuntando ahí. Con Supabase, usar el pooler en modo
  transacción (puerto 6543).
- Las migraciones corren solas al arrancar. Verificar que llegue a v13.
- Migrar los datos actuales desde `data/financial_analyzer.db`.

*Ojo con el sequence de Postgres*: al insertar la cuenta local con `id=1`
explícito, `nextval` queda atrás y el primer alta de usuario colisiona en la PK.
Ya hay una función `sync_identity()` que hace el `setval`; confirmar que corre.

*Listo cuando*: la suite pasa contra ese Postgres
(`FA_TEST_DATABASE_URL=…`) y `/api/portfolio` devuelve los datos migrados.

### Fase 2 — HTTPS

**Ya hecho** (2026-08-27, commit `293b01b`), la parte de exposición y token:

- `compose.yaml` sigue publicando en `127.0.0.1:8000`: privado por defecto, para
  que un clon nuevo no pueda servir la cartera de alguien a su red sin querer.
- `compose.lan.yaml` es una overlay opcional que abre el puerto a toda la red
  **y exige `FA_API_TOKEN` en el mismo movimiento** (`${FA_API_TOKEN:?...}`), así
  que `docker compose up` falla en seco si falta. Se activa por comando con
  `-f compose.yaml -f compose.lan.yaml`, o de una vez con
  `COMPOSE_FILE=compose.yaml:compose.lan.yaml` en el `.env`.
- Dos trampas ya resueltas, para no repetirlas en el servidor: compose interpola
  el archivo entero sin importar qué servicio corras (una variable obligatoria en
  `compose.yaml` rompe el CLI, que no usa token), y las listas de `ports` se
  **concatenan** entre archivos en vez de reemplazarse — de ahí `ports: !override`
  en la overlay.

El chequeo que se niega a escuchar en la red sin token vive en `fa/cli.py` y se
apaga dentro de un contenedor a propósito: ahí la dirección de bind no dice nada
sobre quién llega, y el mapeo que sí decide es invisible desde adentro. Por eso
la exigencia va en compose, que es la capa que ve la exposición.

**Falta**: el HTTPS en sí.

- Dominio propio y Caddy delante de uvicorn (certificado automático).

*Listo cuando*: `https://dominio/api/health` responde con certificado válido y
sin token devuelve 401.

### Fase 3 — Instalable en el teléfono

Con HTTPS, la PWA ya se instala: el manifest y el service worker están hechos.

Verificado el 2026-08-27 con la app servida por HTTP en la LAN: el navegador
reporta `isSecureContext: false` y el service worker no registra. O sea que la
Fase 3 **no arranca** hasta que esté la 2 — no es cuestión de configurar mejor.

- Verificar "Agregar a pantalla de inicio" en Chrome Android.
- Íconos PNG además del SVG actual (Play Store los exige en varios tamaños).
- `assetlinks.json` servido en `/.well-known/` para que el TWA saque la barra
  del navegador.
- Bubblewrap para generar el APK/AAB.

*Listo cuando*: el APK abre a pantalla completa, sin barra de URL, contra el
dominio real.

### Fase 4 — Trabajos del servidor

Ver 2.3. Separar refresco por ticker de evaluación por cuenta.

Al dimensionar el paralelismo, mirar 2.4: cada trabajo abre su propia conexión,
y el pooler de Supabase en modo transacción tiene un techo bajo.

*Listo cuando*: agregar una segunda cuenta que siga los mismos tickers no
duplica los fetch.

### Fase 5 — Multi-usuario y cobro

- Alta de usuarios por Supabase (el modo ya existe, `mode_for()` lo detecta).
- Web Push con tabla `devices` (hoy las notificaciones son Telegram).
- Planes y facturación.
- **No arrancar sin resolver 2.6 y 2.7.**

---

## 5. Decisiones abiertas del dueño

1. **Dónde**: qué VPS y qué Postgres gestionado.
2. **Dominio**: cuál.
3. **Datos**: qué proveedor comercial reemplaza a Yahoo, y con qué presupuesto.
   Define el techo de usuarios que el negocio soporta.
4. **Legal**: si se cobra por sugerencias de compra/venta, y bajo qué figura.
5. **Alcance del MVP pago**: qué queda gratis y qué no.

---

## 6. El plan inmediato: cliente local contra backend remoto

La idea del dueño es probar la app actual apuntando al backend del servidor, y
recién después hacer la app móvil. Hay dos maneras de leer eso y conviene no
confundirlas, porque una es trivial y la otra tiene trabajo.

### Opción A — todo en el servidor (recomendada para empezar)

El contenedor `dashboard` sirve la API **y** el cliente web en el mismo puerto.
Levantándolo en el servidor, abrir la URL en el navegador ya es "la app contra
el backend del servidor". Mismo origen, sin CORS, sin cambios de código.

Es además el camino directo a la fase 3: es esa misma URL, ya con HTTPS, la que
el TWA va a envolver.

### Opción B — cliente local, API remota

Sirve para probar el backend nuevo sin desplegar el cliente, pero **no funciona
tal cual** y hay que saberlo antes de empezar:

1. Los ~37 llamados del cliente usan rutas relativas (`/api/...`). Servido desde
   otro origen, pegan contra el origen local, no contra el servidor. Hace falta
   una base configurable — casi todos pasan por el helper `api()` en
   `web/app.js`, más dos `fetch` sueltos.
2. CORS sólo permite `localhost:5173` (ver 2.5). Hay que hacer la lista
   configurable por variable de entorno y sumar el origen desde el que se sirva
   el cliente.
3. Con `FA_API_TOKEN` puesto, el navegador manda `Authorization`, lo que
   convierte todo en pedido no-simple: el preflight tiene que pasar y
   `allow_headers` ya está en `*`, así que alcanza con arreglar los orígenes.

Es el mismo trabajo que pediría Capacitor. Si el destino es el TWA (opción A +
HTTPS), este trabajo no hace falta nunca.

### Variante útil sin tocar nada

Para probar sólo la capa de datos contra el Postgres del servidor, sin desplegar
nada: levantar el dashboard local con `DATABASE_URL` apuntando a la base
remota. El cliente sigue siendo local y del mismo origen que su API local, así
que no hay CORS de por medio, y lo que se está ejercitando es exactamente lo que
importa validar primero — migraciones, driver y datos migrados.

```bash
DATABASE_URL=postgresql://usuario:clave@host:6543/postgres \
  docker compose up -d dashboard
```

Ojo: eso requiere la fase 0 hecha, porque hoy `psycopg` no está en la imagen.

---

## 7. Referencia rápida

**Variables** (todas en `.env.example`, y `compose.yaml` las inyecta en cada
servicio con un solo anchor `x-app-env`):

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | vacío = SQLite en `./data`; con valor = Postgres |
| `FA_API_TOKEN` | token compartido; obligatorio si se expone a la red |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` | modo multi-usuario |
| `ALPHA_VANTAGE_API_KEY`, `FINNHUB_API_KEY` | respaldos de datos |
| `GEMINI_API_KEY`, `FA_GEMINI_MODEL` | informe de IA |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | notificaciones |
| `FA_DATA_DIR` | dónde viven la DB y `alerts.log` |

**Comandos**:

```bash
docker compose up -d dashboard                   # API + web en :8000
docker compose run --rm analyzer check-alerts    # evalúa y notifica
docker compose run --rm analyzer technicals NVDA # un papel, refresca de paso
docker compose --profile dev up -d postgres      # Postgres efímero para tests

FA_TEST_DATABASE_URL=postgresql://fa:fa@localhost:55432/fa_test pytest
```

**Trampa conocida**: la variable para correr la suite contra Postgres es
`FA_TEST_DATABASE_URL`, **no** `DATABASE_URL`. Con la equivocada la suite corre
en SQLite en silencio y parece que pasó en los dos motores.

**Archivos que importan**:

| Archivo | Qué es |
|---|---|
| `fa/store/database.py` | capa de portabilidad entre motores |
| `fa/store/migrations.py` | motor de migraciones, `TARGET_VERSION = 13` |
| `fa/store/schema.py` | esquema squasheado, `TENANT_TABLES` / `SHARED_TABLES` |
| `fa/api/auth.py` | los tres modos de autenticación |
| `fa/warm.py` | la regla de cuándo se permite salir a buscar datos |
| `compose.yaml` | un `x-app-env` para todos los servicios |
| `scripts/setup-systemd.sh` | el timer actual, a reemplazar en fase 4 |
