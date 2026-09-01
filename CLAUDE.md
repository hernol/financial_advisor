# Financial Analyzer — reglas del proyecto

## Este proyecto corre con Docker, y en el servidor corre siempre

En producción son tres contenedores permanentes: `proxy` (nginx en 80/443),
`dashboard` (la app) y `db` (Postgres con volumen). Están para quedarse:
`restart: unless-stopped`, y un timer de systemd los usa cada hora.

**Si el `CLAUDE.md` global de la máquina prohíbe levantar Docker o servicios
locales, acá no aplica.** Esa regla existe para no saturar un VPS chico, y este
stack no lo satura. Medido el 2026-08-28 en el servidor:

```
dashboard    88,9 MB    cpu 0,13%
db           47,3 MB    cpu 0,00%
proxy         3,9 MB    cpu 0,00%
            ────────
             140 MB     de 7,6 GB · load average 0,33
```

Operar estos contenedores —`ps`, `logs`, `up -d`, `restart`, `exec`— es el
trabajo normal de mantenimiento y está permitido. Lo que sigue valiendo de la
regla global: no levantar servicios nuevos sin pensarlo, no correr suites
pesadas en paralelo, y preguntar antes de agregar otra cosa que quede prendida.

## Antes de nada: leer la spec

`docs/DEPLOY-SPEC.md` es el documento maestro del despliegue. Tiene el estado de
cada fase, los bloqueos verificados y las decisiones que dependen del dueño.
`HOWTO.md` explica el producto. Los mensajes de commit son largos a propósito y
guardan el porqué de cada decisión.

## Regla dura sobre datos de mercado

**Nunca simular, estimar ni inventar un dato de mercado.** El primario es
yfinance; los respaldos son Alpha Vantage y Finnhub. Si fallan todos, el
programa corta con error — es preferible a un número inventado.

Esto ya moldeó el código: un P/E que cruza monedas distintas se deja **en
blanco**, no se convierte con un tipo de cambio de hoy; un PEG sobre ganancias
que cayeron no se muestra, porque el número negativo se leería como ganga.
Cuando no se sabe, se dice que no se sabe y se explica por qué.

## Cómo probar

**En el servidor no hay `.venv` ni Python con las dependencias.** La suite corre
dentro de la imagen de producción, y hay que montarle cuatro cosas porque el
`Dockerfile` sólo copia `financial_analyzer.py` y `fa/`:

```bash
docker compose run --rm --entrypoint python \
  -e PYTHONPATH=/app -e DATABASE_URL= -e FA_API_TOKEN= -e GEMINI_API_KEY= \
  -e ALPHA_VANTAGE_API_KEY= -e FINNHUB_API_KEY= -e TELEGRAM_BOT_TOKEN= \
  -e TELEGRAM_CHAT_ID= \
  -v "$PWD/tests:/app/tests:ro" -v "$PWD/pytest.ini:/app/pytest.ini:ro" \
  -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/fa:/app/fa:ro" \
  -w /tmp dashboard -m pytest -c /app/pytest.ini --rootdir=/app /app/tests
```

Al 2026-09-01 eso da **882 pasan, 5 skipped**.

Cada pieza rara está por un motivo, y sacarla rompe algo:

- **Blanquear las siete variables no es opcional.** El contenedor hereda el
  `.env`; con `FA_API_TOKEN` puesto la API arranca en modo token, el
  `TestClient` de FastAPI se come un 401 y **182 tests fallan** por algo que no
  tiene nada que ver con el cambio que estés probando.
- **Montar `fa/` también.** La imagen lo trae horneado, así que sin el montaje
  estás probando el código de la última build, no el del working tree. Falla de
  una forma muy confusa: el módulo nuevo "no existe".
- **`-w /tmp`** porque `/app` es del root y pytest no puede escribir su caché.
- **No pasar `-q`.** `pytest.ini` ya lo trae; el segundo lo convierte en `-qq`,
  que esconde la línea con los totales.

### Postgres

```bash
docker compose --profile dev up -d postgres     # efímero, sin volumen
# ...el mismo comando de arriba, más:
#   -e FA_TEST_DATABASE_URL=postgresql://fa:fa@postgres:5432/fa_test
docker compose --profile dev down postgres      # apagalo cuando terminás
```

Da **887 pasan** — los 5 que SQLite saltea, más los que sólo corren ahí.

El servicio `postgres` del profile `dev` es descartable y existe para esto. **No
apuntar la suite al contenedor `db`**: ese es el de producción, con otras
credenciales y con los datos reales del dueño.

La variable es **`FA_TEST_DATABASE_URL`**, no `DATABASE_URL`. Con la equivocada
la suite corre en SQLite en silencio y parece que pasó en los dos motores.

### Lint

`ruff` **no está instalado**: ni en el servidor, ni en la imagen, ni en
`requirements.txt`. La regla sigue siendo *no aumentar la línea base de errores
preexistentes* —comparar antes y después, no apuntar a cero— pero hoy no se
puede medir acá. Si lo corrés en otra máquina, esa regla vale; si no, decí que
no lo pudiste verificar en vez de dar por sentado que está bien.

**Nunca probar contra la base real del usuario.** Copiar a un descartable y
apuntar `FA_DB_PATH` ahí.

## Convenciones

- Respuestas al usuario en español, concisas. Código, commits y PRs en inglés.
- Sin `Co-Authored-By` en los commits.
- Monedas: siempre USD.
- Antes de cualquier acción destructiva en el servidor —borrar, detener,
  sobrescribir— avisar y esperar el OK.
