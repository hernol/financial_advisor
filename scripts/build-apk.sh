#!/usr/bin/env bash
#
# Build the Android TWA that wraps the PWA, on a workstation — not on the
# server. Two reasons it does not run there: Bubblewrap pulls a JDK and the
# Android SDK, well over a gigabyte, onto a box whose documented exception
# covers the Docker stack and nothing else; and the keystore it creates is the
# application's permanent identity, which has no business living on a machine
# that also serves mail and runs other people's agents.
#
#   ./scripts/build-apk.sh
#
# Safe to re-run: it never overwrites a keystore, and it skips any step whose
# work is already done. Override any of the defaults through the environment.
#
set -euo pipefail

DOMAIN="${FA_TWA_DOMAIN:-hernol.com.ar}"
PACKAGE="${FA_TWA_PACKAGE:-ar.com.hernol.analyzer}"
PROJECT_DIR="${FA_TWA_DIR:-$HOME/twa/financial-analyzer}"
KEYSTORE="${FA_TWA_KEYSTORE:-$HOME/keys/financial-analyzer.keystore}"
KEY_ALIAS="${FA_TWA_ALIAS:-analyzer}"

MANIFEST_URL="https://${DOMAIN}/manifest.webmanifest"
ASSETLINKS_URL="https://${DOMAIN}/.well-known/assetlinks.json"

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
    BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; RESET=''
fi

step()  { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
info()  { printf '    %s\n' "$1"; }
note()  { printf '    %s%s%s\n' "$DIM" "$1" "$RESET"; }
warn()  { printf '    %s%s%s\n' "$YELLOW" "$1" "$RESET"; }
ok()    { printf '    %s%s%s\n' "$GREEN" "$1" "$RESET"; }
die()   { printf '\n%serror:%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# Every prompt below needs a human. Failing early beats hanging in a pipeline.
[ -t 0 ] || die "este script es interactivo, corrélo en una terminal (no por pipe ni con nohup)"

confirm() {
    local answer
    while true; do
        read -r -p "    $1 [s/n] " answer
        case "${answer,,}" in
            s|si|sí|y|yes) return 0 ;;
            n|no)          return 1 ;;
            *)             info "respondé s o n." ;;
        esac
    done
}

have() { command -v "$1" >/dev/null 2>&1; }

# --- 0. what we are about to do ---------------------------------------------

step "Financial Analyzer — APK (TWA)"
info "dominio    ${DOMAIN}"
info "package    ${PACKAGE}"
info "proyecto   ${PROJECT_DIR}"
info "keystore   ${KEYSTORE}"
info "alias      ${KEY_ALIAS}"
note "cualquiera de estos se cambia por variable de entorno: FA_TWA_DOMAIN,"
note "FA_TWA_PACKAGE, FA_TWA_DIR, FA_TWA_KEYSTORE, FA_TWA_ALIAS."

# --- 1. prerequisites --------------------------------------------------------

step "1/7 · Requisitos"

have curl || die "falta curl"
have node || die "falta node. Instalalo (nvm, o 'sudo pacman -S nodejs npm') y volvé a correr"
have npm  || die "falta npm"
ok "node $(node --version)"

if ! have keytool; then
    warn "falta el JDK (keytool). Bubblewrap lo necesita para firmar."
    if have pacman && confirm "¿Instalo jdk17-openjdk con pacman? (pide sudo)"; then
        sudo pacman -S --needed jdk17-openjdk
    else
        die "instalá un JDK 17 y volvé a correr"
    fi
fi
have keytool || die "keytool sigue sin estar en el PATH; revisá la instalación del JDK"
ok "keytool presente"

# --- 2. bubblewrap -----------------------------------------------------------

step "2/7 · Bubblewrap"

if have bubblewrap; then
    ok "ya instalado"
else
    info "no está instalado."
    confirm "¿Lo instalo con 'npm i -g @bubblewrap/cli'?" || die "hace falta bubblewrap"
    npm i -g @bubblewrap/cli
fi
have bubblewrap || die "bubblewrap no quedó en el PATH; revisá el prefijo global de npm"

# --- 3. the app has to be reachable -----------------------------------------

step "3/7 · Verificando el sitio"

manifest_code=$(curl -s -o /dev/null -w '%{http_code}' "$MANIFEST_URL")
[ "$manifest_code" = "200" ] || die "$MANIFEST_URL devolvió $manifest_code; Bubblewrap necesita leerlo sin token"
ok "manifest accesible"

icon_code=$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}/static/icon-512.png")
[ "$icon_code" = "200" ] || die "el ícono de 512 devolvió $icon_code; Bubblewrap no construye sin un PNG de 512"
ok "ícono de 512 accesible"

# --- 4. the keystore ---------------------------------------------------------

step "4/7 · Keystore"

if [ -f "$KEYSTORE" ]; then
    ok "ya existe: $KEYSTORE"
    note "no se toca. Es la identidad permanente de la app."
else
    warn "no existe todavía. Se va a crear uno nuevo."
    info ""
    info "RSA 2048 y 10000 días de validez (vence cerca de 2053). Play Store exige"
    info "expiración posterior a octubre de 2033, así que este keystore te sirve"
    info "también si algún día publicás en la tienda."
    info ""
    warn "RESPALDÁ este archivo y su contraseña apenas termine."
    warn "Si lo perdés después de publicar, no podés volver a actualizar la app."
    info ""
    confirm "¿Lo genero?" || die "sin keystore no se puede firmar"

    keystore_dir="$(dirname "$KEYSTORE")"
    # Only lock down a directory we are creating: FA_TWA_KEYSTORE could point
    # straight at $HOME, and chmod 700 on someone's home is not ours to do.
    if [ ! -d "$keystore_dir" ]; then
        mkdir -p "$keystore_dir"
        chmod 700 "$keystore_dir"
    fi
    # keytool prompts for the passwords and the distinguished name itself, so
    # nothing secret passes through argv, where ps would show it.
    keytool -genkeypair -v \
        -keystore "$KEYSTORE" \
        -alias "$KEY_ALIAS" \
        -keyalg RSA -keysize 2048 \
        -validity 10000 \
        -storetype PKCS12
    chmod 600 "$KEYSTORE"
    ok "creado: $KEYSTORE"
fi

# --- 5. the TWA project ------------------------------------------------------

step "5/7 · Proyecto TWA"

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

if [ -f twa-manifest.json ]; then
    ok "ya inicializado en $PROJECT_DIR"
else
    info "Bubblewrap va a hacerte varias preguntas. Las que importan:"
    info ""
    info "  Application ID / package    ${BOLD}${PACKAGE}${RESET}"
    info "  Key store location         ${BOLD}${KEYSTORE}${RESET}"
    info "  Key alias                  ${BOLD}${KEY_ALIAS}${RESET}"
    info ""
    warn "El Application ID tiene que ser EXACTO: es el que está en el"
    warn "assetlinks.json del servidor, y si no coincide el TWA abre con la"
    warn "barra de URL puesta."
    info ""
    note "El resto (nombre, colores, ícono) sale del manifest y podés aceptar"
    note "los valores que propone. Si te ofrece bajar el JDK o el Android SDK,"
    note "aceptá: son más de 1 GB y es normal que tarde."
    info ""
    confirm "¿Arranco el init?" || die "cancelado"

    bubblewrap init --manifest "$MANIFEST_URL"
fi

[ -f twa-manifest.json ] || die "el init no dejó twa-manifest.json; algo falló"

# Verify rather than assume: a mismatched package id is the single most common
# reason the URL bar refuses to go away, and it is silent.
actual_package=$(node -e '
    const twa = JSON.parse(require("fs").readFileSync("twa-manifest.json", "utf8"));
    process.stdout.write(twa.packageId || "");
')
if [ "$actual_package" != "$PACKAGE" ]; then
    warn "el proyecto quedó con packageId '${actual_package}'"
    warn "y el assetlinks.json del servidor dice '${PACKAGE}'."
    info ""
    info "Tienen que coincidir. Podés cambiar el del proyecto, o pedirme que"
    info "cambie el del servidor por '${actual_package}'."
    confirm "¿Sigo igual? (el APK va a abrir con barra de URL hasta arreglarlo)" \
        || die "corregí el packageId en twa-manifest.json y volvé a correr"
fi

# --- 6. build ----------------------------------------------------------------

step "6/7 · Construyendo"

info "Te va a pedir las contraseñas del keystore."
BUILD_LOG="$(mktemp)"
trap 'rm -f "$BUILD_LOG"' EXIT
bubblewrap build 2>&1 | tee "$BUILD_LOG"

APK=""
for candidate in app-release-signed.apk app-release-unsigned-aligned.apk; do
    [ -f "$candidate" ] && { APK="$PROJECT_DIR/$candidate"; break; }
done
if [ -n "$APK" ]; then
    ok "APK: $APK"
else
    warn "no encontré el APK en $PROJECT_DIR; mirá la salida de arriba"
fi

# --- 7. the fingerprint ------------------------------------------------------

step "7/7 · Fingerprint"

# Bubblewrap writes its own assetlinks.json next to the build. Reading it
# avoids a second keytool run, which would prompt for the password again.
FINGERPRINT=""
if [ -f assetlinks.json ]; then
    FINGERPRINT=$(node -e '
        const links = JSON.parse(require("fs").readFileSync("assetlinks.json", "utf8"));
        const target = (links[0] || {}).target || {};
        process.stdout.write((target.sha256_cert_fingerprints || [])[0] || "");
    ' 2>/dev/null || true)
fi
if [ -z "$FINGERPRINT" ]; then
    FINGERPRINT=$(grep -oE '([0-9A-F]{2}:){31}[0-9A-F]{2}' "$BUILD_LOG" | head -1 || true)
fi

if [ -z "$FINGERPRINT" ]; then
    warn "no pude extraerlo automáticamente. Sacalo con:"
    info ""
    info "  keytool -list -v -keystore $KEYSTORE -alias $KEY_ALIAS | grep SHA256:"
    info ""
    exit 0
fi

printf '\n    %sSHA-256:%s %s\n\n' "$BOLD" "$RESET" "$FINGERPRINT"

published=$(curl -s "$ASSETLINKS_URL" | grep -oE '([0-9A-F]{2}:){31}[0-9A-F]{2}' | head -1 || true)

if [ "$published" = "$FINGERPRINT" ]; then
    ok "el servidor ya publica este fingerprint."
    info "Podés instalar el APK: se va a abrir a pantalla completa."
    if have adb; then
        info ""
        confirm "¿Lo instalo por adb en el teléfono conectado?" && adb install -r "$APK"
    fi
else
    warn "el servidor TODAVÍA NO publica este fingerprint."
    if [ -n "$published" ]; then
        note "publicado: $published"
    else
        note "publicado: nada válido (placeholder)"
    fi
    info ""
    warn "NO INSTALES el APK todavía. Android cachea el resultado de la"
    warn "verificación: si lo abrís ahora, se queda con la barra de URL puesta"
    warn "aunque después arreglemos el archivo, y hay que desinstalar para que"
    warn "reintente."
    info ""
    info "Pasá el SHA-256 de arriba para que se actualice ${ASSETLINKS_URL},"
    info "y después volvé a correr este script: te va a decir que ya coincide."
fi
