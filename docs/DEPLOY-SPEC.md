# Spec: mover el backend a un servidor y llegar al APK

Documento de trabajo para continuar en otra sesión. Escrito el 2026-08-26 contra
el commit `728418b` de `main`.

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
| Suite | 757 en SQLite, 762 en Postgres (`FA_TEST_DATABASE_URL=postgresql://…`) |
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

### 2.4 Yahoo prohíbe el uso comercial — bloqueante para monetizar

`yfinance` es la fuente primaria. Sus términos no permiten uso comercial. En el
momento en que se cobra una suscripción, el proyecto queda afuera.

**Lo bueno**: `AlphaVantageProvider` y `FinnhubProvider` ya existen en la cadena
y ambos tienen planes comerciales. Es cambiar contrato y key, no reescribir.

**Hacerlo antes del primer cobro, no después.**

### 2.5 Cobrar por recomendaciones puede ser asesoramiento regulado

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

- Dominio propio y Caddy delante de uvicorn (certificado automático).
- **Antes de exponer nada**: poner `FA_API_TOKEN`. Hoy `compose.yaml` publica en
  `127.0.0.1:8000` justamente para que no quede abierto por accidente; al cambiar
  el mapeo, ese token deja de ser opcional.
- El arranque ya avisa por log cuando corre sin autenticación. No ignorarlo.

*Listo cuando*: `https://dominio/api/health` responde con certificado válido y
sin token devuelve 401.

### Fase 3 — Instalable en el teléfono

Con HTTPS, la PWA ya se instala: el manifest y el service worker están hechos.

- Verificar "Agregar a pantalla de inicio" en Chrome Android.
- Íconos PNG además del SVG actual (Play Store los exige en varios tamaños).
- `assetlinks.json` servido en `/.well-known/` para que el TWA saque la barra
  del navegador.
- Bubblewrap para generar el APK/AAB.

*Listo cuando*: el APK abre a pantalla completa, sin barra de URL, contra el
dominio real.

### Fase 4 — Trabajos del servidor

Ver 2.3. Separar refresco por ticker de evaluación por cuenta.

*Listo cuando*: agregar una segunda cuenta que siga los mismos tickers no
duplica los fetch.

### Fase 5 — Multi-usuario y cobro

- Alta de usuarios por Supabase (el modo ya existe, `mode_for()` lo detecta).
- Web Push con tabla `devices` (hoy las notificaciones son Telegram).
- Planes y facturación.
- **No arrancar sin resolver 2.4 y 2.5.**

---

## 5. Decisiones abiertas del dueño

1. **Dónde**: qué VPS y qué Postgres gestionado.
2. **Dominio**: cuál.
3. **Datos**: qué proveedor comercial reemplaza a Yahoo, y con qué presupuesto.
   Define el techo de usuarios que el negocio soporta.
4. **Legal**: si se cobra por sugerencias de compra/venta, y bajo qué figura.
5. **Alcance del MVP pago**: qué queda gratis y qué no.

---

## 6. Referencia rápida

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
