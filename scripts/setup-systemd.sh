#!/usr/bin/env bash
# Instala el timer de systemd (modo --user) que evalua las alertas periodicamente.
#
# No hay daemon: el timer duerme y a la hora indicada levanta un container
# efimero que corre `check-alerts`, notifica y muere.
set -euo pipefail

SERVICE="fa-alerts.service"
TIMER="fa-alerts.timer"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Horario por defecto: cada hora, dias habiles, 10 a 18 hora local.
# Pensado para America/Argentina/Buenos_Aires (-03), donde ese rango cubre el
# mercado US tanto en EDT (10:30-17:00 local) como en EST (11:30-18:00 local).
# En otra zona horaria ajustalo con --schedule o FA_ALERTS_SCHEDULE.
SCHEDULE="${FA_ALERTS_SCHEDULE:-Mon-Fri 10..18:00:00}"
RUN_TEST=1

red()  { printf '\033[31m%s\033[0m\n' "$*" >&2; }
yell() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
die()  { red "error: $*"; exit 1; }

usage() {
  cat <<'USAGE'
Uso: scripts/setup-systemd.sh [opciones]

  --schedule EXPR   Expresion OnCalendar de systemd.
                    Default: "Mon-Fri 10..18:00:00" (hora local)
                    Ejemplos: "hourly"  |  "Mon-Fri *:0/30"  |  "Mon-Fri 9..17:00:00"
  --no-test         No corre el chequeo una vez despues de instalar.
  --uninstall       Desactiva y borra las units.
  -h, --help        Esto.

Variables de entorno equivalentes: FA_ALERTS_SCHEDULE
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --schedule)  SCHEDULE="${2:-}"; [ -n "$SCHEDULE" ] || die "--schedule necesita un valor"; shift 2 ;;
    --no-test)   RUN_TEST=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           die "opcion desconocida: $1 (probá --help)" ;;
  esac
done

if [ "${UNINSTALL:-0}" = "1" ]; then
  systemctl --user disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE"
  systemctl --user daemon-reload
  grn "Timer desinstalado. La base y las alertas quedan intactas."
  exit 0
fi

echo "==> Verificando requisitos"

command -v systemctl >/dev/null 2>&1 || die "systemd no esta disponible en este sistema."
systemctl --user show-environment >/dev/null 2>&1 \
  || die "no hay una instancia de systemd --user. Logueate en una sesion normal e intenta de nuevo."
grn "  systemd --user ok"

command -v docker >/dev/null 2>&1 || die "docker no esta instalado."
docker compose version >/dev/null 2>&1 \
  || die "hace falta Docker Compose v2 (el subcomando 'docker compose'). Instalalo o actualiza Docker."
grn "  docker + compose v2 ok"

DOCKER_BIN="$(command -v docker)"

[ -f "$REPO_DIR/compose.yaml" ] || die "no encuentro compose.yaml en $REPO_DIR"
grn "  repo en $REPO_DIR"

if ! docker info >/dev/null 2>&1; then
  die "no puedo hablar con el daemon de docker. Verifica que este corriendo (systemctl start docker) y que tu usuario este en el grupo 'docker' (sudo usermod -aG docker \$USER, despues reloguearte)."
fi
grn "  daemon de docker accesible"

if [ ! -f "$REPO_DIR/.env" ]; then
  yell "aviso: no existe $REPO_DIR/.env"
  yell "       Sin el no hay API keys ni credenciales de Telegram: las alertas se van"
  yell "       a evaluar pero solo van a notificar por consola/log."
  yell "       Copia .env.example a .env y completalo."
else
  grn "  .env presente"
fi

# El compose mapea el usuario del host para que el SQLite en ./data no quede
# root-owned; UID/GID no siempre estan exportados, asi que los fijamos aca.
HOST_UID="$(id -u)"; HOST_GID="$(id -g)"

echo "==> Escribiendo units en $UNIT_DIR"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/$SERVICE" <<UNIT
[Unit]
Description=Financial Analyzer - chequeo de alertas
Documentation=file://$REPO_DIR/HOWTO.md
After=docker.service
Wants=network-online.target

[Service]
Type=oneshot
# WorkingDirectory es lo que permite que compose encuentre compose.yaml y .env:
# systemd no interpreta el .env por su cuenta.
WorkingDirectory=$REPO_DIR
Environment=UID=$HOST_UID
Environment=GID=$HOST_GID
# -T evita que compose pida un TTY que bajo systemd no existe.
ExecStart=$DOCKER_BIN compose run --rm -T alerts
# La primera corrida puede tener que construir la imagen.
TimeoutStartSec=900
UNIT

cat > "$UNIT_DIR/$TIMER" <<UNIT
[Unit]
Description=Financial Analyzer - chequeo periodico de alertas

[Timer]
# Hora local de la maquina. Ver --schedule en scripts/setup-systemd.sh
OnCalendar=$SCHEDULE
# Si la maquina estaba apagada o suspendida a la hora prevista, corre al volver.
Persistent=true
# Desfasa hasta 3 min para no pegarle a las APIs siempre en punto.
RandomizedDelaySec=180

[Install]
WantedBy=timers.target
UNIT

grn "  $SERVICE"
grn "  $TIMER  (OnCalendar=$SCHEDULE)"

systemctl --user daemon-reload
systemctl --user enable --now "$TIMER" >/dev/null
grn "  timer habilitado"

# Sin linger el timer se apaga al cerrar sesion.
if [ "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)" != "yes" ]; then
  yell ""
  yell "aviso: 'linger' esta apagado para tu usuario."
  yell "       El timer NO va a correr cuando no tengas una sesion abierta."
  yell "       Activalo con:  sudo loginctl enable-linger $USER"
fi

if [ "$RUN_TEST" = "1" ]; then
  echo "==> Corrida de prueba (la primera puede tardar: construye la imagen)"
  if systemctl --user start "$SERVICE"; then
    journalctl --user -u "$SERVICE" -n 12 --no-pager -o cat || true
    RESULT="$(systemctl --user show "$SERVICE" -p Result --value)"
    [ "$RESULT" = "success" ] || { red "la corrida de prueba fallo (Result=$RESULT)"; exit 1; }
    grn "  corrida de prueba ok"
  else
    red "no se pudo ejecutar $SERVICE; revisa: journalctl --user -u $SERVICE -n 40"
    exit 1
  fi
fi

echo
grn "Listo."
systemctl --user list-timers "$TIMER" --no-pager | head -3
cat <<'TIPS'

Comandos utiles:
  journalctl --user -u fa-alerts.service -n 50 --no-pager   # ultimas corridas
  journalctl --user -u fa-alerts.service -f                 # seguir en vivo
  systemctl --user list-timers fa-alerts.timer              # proxima corrida
  systemctl --user start fa-alerts.service                  # forzar una corrida
  scripts/setup-systemd.sh --uninstall                      # sacar el timer
TIPS
