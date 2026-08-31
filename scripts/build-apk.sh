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

step "1/8 · Requisitos"

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

step "2/8 · Bubblewrap"

if have bubblewrap; then
    ok "ya instalado"
else
    info "no está instalado."
    confirm "¿Lo instalo con 'npm i -g @bubblewrap/cli'?" || die "hace falta bubblewrap"
    npm i -g @bubblewrap/cli
fi
have bubblewrap || die "bubblewrap no quedó en el PATH; revisá el prefijo global de npm"

# --- 3. the app has to be reachable -----------------------------------------

step "3/8 · Verificando el sitio"

manifest_code=$(curl -s -o /dev/null -w '%{http_code}' "$MANIFEST_URL")
[ "$manifest_code" = "200" ] || die "$MANIFEST_URL devolvió $manifest_code; Bubblewrap necesita leerlo sin token"
ok "manifest accesible"

icon_code=$(curl -s -o /dev/null -w '%{http_code}' "https://${DOMAIN}/static/icon-512.png")
[ "$icon_code" = "200" ] || die "el ícono de 512 devolvió $icon_code; Bubblewrap no construye sin un PNG de 512"
ok "ícono de 512 accesible"

# --- 4. the keystore ---------------------------------------------------------

step "4/8 · Keystore"

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

step "5/8 · Proyecto TWA"

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
    note "Play Billing y la delegación de ubicación las apago después; esta app"
    note "no cobra por la tienda ni pide ubicación."
    info ""
    confirm "¿Arranco el init?" || die "cancelado"

    bubblewrap init --manifest "$MANIFEST_URL"
fi

[ -f twa-manifest.json ] || die "el init no dejó twa-manifest.json; algo falló"

# Bubblewrap enables Play Billing and location delegation by default. Neither is
# free: the billing library declares minSdk 23 and the build dies on the merge
# against the project's 21, and location delegation ships a location permission
# in an app that never asks for one. Off unless someone opts in.
MANIFEST_PATCHED=""
if node -e '
    const fs = require("fs");
    const twa = JSON.parse(fs.readFileSync("twa-manifest.json", "utf8"));
    const wanted = {
        playBilling: process.env.FA_TWA_PLAY_BILLING === "1",
        locationDelegation: process.env.FA_TWA_LOCATION === "1",
    };
    let changed = false;
    twa.features = twa.features || {};
    for (const [name, on] of Object.entries(wanted)) {
        const feature = twa.features[name];
        if (feature && feature.enabled !== on) { feature.enabled = on; changed = true; }
    }
    if (changed) fs.writeFileSync("twa-manifest.json", JSON.stringify(twa, null, 2) + "\n");
    process.exit(changed ? 0 : 1);
'; then
    info "apagué playBilling y locationDelegation en twa-manifest.json"
    note "se prenden con FA_TWA_PLAY_BILLING=1 o FA_TWA_LOCATION=1."
    MANIFEST_PATCHED="1"
fi

# The keystore this script created is the one the owner was told to back up. If
# the project signs with a different one — bubblewrap init proposes its own,
# inside the project directory — that backup protects nothing.
signing_path=$(node -e '
    const twa = JSON.parse(require("fs").readFileSync("twa-manifest.json", "utf8"));
    process.stdout.write(((twa.signingKey || {}).path) || "");
')
signing_alias=$(node -e '
    const twa = JSON.parse(require("fs").readFileSync("twa-manifest.json", "utf8"));
    process.stdout.write(((twa.signingKey || {}).alias) || "");
')
if [ "$signing_path" != "$KEYSTORE" ] || [ "$signing_alias" != "$KEY_ALIAS" ]; then
    warn "el proyecto firma con otra clave que la que este script administra:"
    info ""
    info "  proyecto  ${signing_path} (alias ${signing_alias})"
    info "  script    ${KEYSTORE} (alias ${KEY_ALIAS})"
    info ""
    warn "La clave que firma es la identidad permanente de la app. Si respaldaste"
    warn "la del script y firma la otra, el respaldo no sirve para nada."
    info ""
    note "Todavía no publicaste el fingerprint, así que cambiarla ahora no cuesta"
    note "nada. Después de instalar el APK en un teléfono, sí: hay que desinstalar."
    info ""
    if confirm "¿Paso el proyecto a firmar con ${KEYSTORE} (alias ${KEY_ALIAS})?"; then
        [ -f "$KEYSTORE" ] || die "no existe $KEYSTORE; volvé a correr para generarlo"
        KEYSTORE="$KEYSTORE" KEY_ALIAS="$KEY_ALIAS" node -e '
            const fs = require("fs");
            const twa = JSON.parse(fs.readFileSync("twa-manifest.json", "utf8"));
            twa.signingKey = {path: process.env.KEYSTORE, alias: process.env.KEY_ALIAS};
            fs.writeFileSync("twa-manifest.json", JSON.stringify(twa, null, 2) + "\n");
        '
        ok "listo. Te va a pedir la contraseña de ese keystore, no la del otro."
        MANIFEST_PATCHED="1"
    else
        warn "sigo con ${signing_path}. Respaldá ESE archivo, no el otro."
        KEYSTORE="$signing_path"
        KEY_ALIAS="$signing_alias"
    fi
fi

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

# --- 6. the android sdk ------------------------------------------------------
#
# Two things here that only show up at build time, both with unhelpful errors.

step "6/8 · SDK de Android"

# The SDK that matters is the one in bubblewrap's own config, not whatever the
# shell says. It only exists once init has run, which is why this step is here
# and not up with the other prerequisites.
SDK_PATH=$(node -e '
    const fs = require("fs"), os = require("os"), path = require("path");
    const file = path.join(os.homedir(), ".bubblewrap", "config.json");
    if (!fs.existsSync(file)) process.exit(0);
    process.stdout.write(JSON.parse(fs.readFileSync(file, "utf8")).androidSdkPath || "");
' 2>/dev/null || true)

if [ -z "$SDK_PATH" ]; then
    warn "no pude leer ~/.bubblewrap/config.json; sigo y que falle bubblewrap si falta algo"
else
    info "SDK        $SDK_PATH"

    # A second SDK in the environment is fatal: the Android Gradle plugin refuses
    # to build when ANDROID_HOME and ANDROID_SDK_ROOT disagree, and says so 150
    # lines into a stack trace. ANDROID_SDK_ROOT is the deprecated one, and
    # bubblewrap sets ANDROID_HOME itself, so dropping it is the safe half.
    if [ -n "${ANDROID_SDK_ROOT:-}" ] && [ "$ANDROID_SDK_ROOT" != "$SDK_PATH" ]; then
        warn "ANDROID_SDK_ROOT apunta a otro SDK: $ANDROID_SDK_ROOT"
        note "lo saco del entorno de esta corrida; el plugin de Android no construye"
        note "con dos SDK distintos a la vista. Es la variable deprecada."
        unset ANDROID_SDK_ROOT
    fi

    # Bubblewrap installs the build-tools itself when they are missing, but it
    # looks for sdkmanager only at "$SDK/tools/bin" or "$SDK/bin" — paths an
    # Android Studio SDK does not have, and which a well-meaning copy of
    # cmdline-tools/latest/bin leaves half-working: the launcher resolves its
    # classpath next to itself and dies with ClassNotFoundException. Installing
    # the version it wants beforehand, with the real sdkmanager, means it never
    # takes that path.
    bw_bin=$(readlink -f "$(command -v bubblewrap)")
    tools_js=$(find "$(dirname "$(dirname "$bw_bin")")" \
        -name AndroidSdkTools.js -not -name '*.map' 2>/dev/null | head -1)
    bt_version=""
    if [ -n "$tools_js" ]; then
        bt_version=$(sed -n "s/.*BUILD_TOOLS_VERSION = '\([^']*\)'.*/\1/p" "$tools_js" | head -1)
    fi

    if [ -z "$bt_version" ]; then
        note "no pude averiguar qué build-tools pide bubblewrap; sigo igual"
    elif [ -d "$SDK_PATH/build-tools/$bt_version" ]; then
        ok "build-tools $bt_version presentes"
    else
        warn "faltan las build-tools $bt_version."
        sdkmanager=""
        for candidate in "$SDK_PATH/cmdline-tools/latest/bin/sdkmanager" \
                         "$SDK_PATH/tools/bin/sdkmanager"; do
            [ -x "$candidate" ] && { sdkmanager="$candidate"; break; }
        done
        [ -n "$sdkmanager" ] || die "no encontré un sdkmanager usable dentro de $SDK_PATH"
        info "las instalo con $sdkmanager (son ~100 MB)"
        confirm "¿Sigo?" || die "sin las build-tools no se puede construir"
        yes | "$sdkmanager" --sdk_root="$SDK_PATH" "build-tools;$bt_version" >/dev/null \
            || die "falló la instalación de build-tools;$bt_version"
        [ -d "$SDK_PATH/build-tools/$bt_version" ] \
            || die "el sdkmanager terminó bien pero no quedó $SDK_PATH/build-tools/$bt_version"
        ok "build-tools $bt_version instaladas"
    fi
fi

# --- 7. build ----------------------------------------------------------------

step "7/8 · Construyendo"

info "Te va a pedir las contraseñas del keystore."
if [ -n "$MANIFEST_PATCHED" ]; then
    note "y antes va a avisar que twa-manifest.json cambió: respondé que SÍ,"
    note "los cambios son los que acaba de hacer este script."
fi
BUILD_LOG="$(mktemp)"
trap 'rm -f "$BUILD_LOG"' EXIT
if ! bubblewrap build 2>&1 | tee "$BUILD_LOG"; then
    printf '\n'
    # Gradle buries the one useful line under a stack trace. When the failure
    # came from somewhere else — the password prompt, a missing tool — there is
    # no such block, so fall back to the tail rather than printing nothing.
    reason=$(sed -n '/What went wrong/,/^\* Try:/p' "$BUILD_LOG")
    if [ -n "$reason" ]; then
        warn "la construcción falló. Lo que dijo Gradle:"
    else
        warn "la construcción falló. El final de la salida:"
        reason=$(tail -20 "$BUILD_LOG")
    fi
    info ""
    printf '%s\n' "$reason" | sed 's/^/    /'
    die "corregí eso y volvé a correr"
fi

APK=""
for candidate in app-release-signed.apk app-release-unsigned-aligned.apk; do
    [ -f "$candidate" ] && { APK="$PROJECT_DIR/$candidate"; break; }
done
if [ -n "$APK" ]; then
    ok "APK: $APK"
    case "$APK" in
        *unsigned*) warn "está SIN FIRMAR: ningún teléfono lo va a instalar." ;;
    esac
else
    warn "no encontré el APK en $PROJECT_DIR; mirá la salida de arriba"
fi

# --- 8. the fingerprint ------------------------------------------------------

step "8/8 · Fingerprint"

# Read the certificate out of the APK that was just signed, not out of the
# keystore. They should agree, and when they do not it is the APK that is
# telling the truth about what a phone will check. It also costs no password:
# a signature is public, and prompting again for a secret we do not need would
# be the wrong habit to teach.
#
# An earlier version looked for an assetlinks.json next to the build. Bubblewrap
# does not write one — that is `bubblewrap fingerprint generateAssetLinks`, a
# separate command — so the file was never there and this step always fell
# through to telling the owner to run keytool by hand.
fingerprint_of() {
    local apk="$1" digest
    [ -f "$apk" ] || return 1
    [ -n "${APKSIGNER:-}" ] || return 1
    digest=$("$APKSIGNER" verify --print-certs "$apk" 2>/dev/null \
        | sed -n 's/.*certificate SHA-256 digest: *//p' | head -1)
    [ -n "$digest" ] || return 1
    # apksigner prints it bare and lowercase; assetlinks.json wants the
    # colon-separated uppercase form.
    printf '%s' "$digest" | tr 'a-f' 'A-F' | sed 's/../&:/g; s/:$//'
}

# Do not tie this to the build-tools version resolved above: that lookup can
# come up empty for its own reasons, and then this would fail for a reason that
# has nothing to do with it. Any apksigner in the SDK reads any signed APK.
APKSIGNER=""
for candidate in "${SDK_PATH:-}/build-tools/${bt_version:-none}/apksigner" \
                 "${SDK_PATH:-}"/build-tools/*/apksigner; do
    [ -x "$candidate" ] && APKSIGNER="$candidate"
done
if [ -z "$APKSIGNER" ] && have apksigner; then
    APKSIGNER="$(command -v apksigner)"
fi

FINGERPRINT=""
if [ -n "$APK" ]; then
    FINGERPRINT=$(fingerprint_of "$APK" || true)
fi
if [ -z "$FINGERPRINT" ]; then
    FINGERPRINT=$(grep -oiE '([0-9A-F]{2}:){31}[0-9A-F]{2}' "$BUILD_LOG" | head -1 \
        | tr 'a-f' 'A-F' || true)
fi

if [ -z "$FINGERPRINT" ]; then
    warn "no pude extraerlo automáticamente. Sacalo con:"
    info ""
    if [ -n "$APKSIGNER" ] && [ -n "$APK" ]; then
        info "  $APKSIGNER verify --print-certs $APK"
    fi
    info "  keytool -list -v -keystore $KEYSTORE -alias $KEY_ALIAS | grep SHA256:"
    info ""
    note "el primero no pide contraseña y dice qué firmó de verdad el APK;"
    note "el segundo dice qué hay en el keystore, que es lo mismo salvo que"
    note "algo haya salido mal."
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
