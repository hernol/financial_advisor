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

```bash
.venv/bin/python -m pytest                    # SQLite
FA_TEST_DATABASE_URL=postgresql://... pytest  # Postgres
```

La variable es **`FA_TEST_DATABASE_URL`**, no `DATABASE_URL`. Con la equivocada
la suite corre en SQLite en silencio y parece que pasó en los dos motores.

`ruff` tiene una línea base de errores preexistentes: no la aumentes. Comparar
antes y después, no apuntar a cero.

**Nunca probar contra la base real del usuario.** Copiar a un descartable y
apuntar `FA_DB_PATH` ahí.

## Convenciones

- Respuestas al usuario en español, concisas. Código, commits y PRs en inglés.
- Sin `Co-Authored-By` en los commits.
- Monedas: siempre USD.
- Antes de cualquier acción destructiva en el servidor —borrar, detener,
  sobrescribir— avisar y esperar el OK.
