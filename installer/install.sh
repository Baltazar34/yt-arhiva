#!/bin/bash
# YT Arhiva — instalacija za macOS (Apple Silicon).
#
#   installer/install.sh [opcije]
#
#   --prefix DIR        gde ide aplikacija (podrazumevano: ~/Applications/YT Arhiva)
#   --app-path PATH     gde ide "YT Arhiva.app" (podrazumevano: ~/Applications/YT Arhiva.app,
#                       a uz --prefix: <prefix>/YT Arhiva.app)
#   --autostart         LaunchAgent: panel se pokreće pri prijavi (bez otvaranja browsera)
#   --no-autostart      ukloni LaunchAgent ako postoji
#   --full              odmah instaliraj i Ollama + whisper-cpp (inace se preuzimaju pri prvom koriscenju)
#   --models            preuzmi Ollama modele (gemma4:12b-it-qat 7,2 GB + translategemma:12b 8,1 GB)
#   --no-models         ne pitaj za modele
#   --models-dir DIR    OLLAMA_MODELS (upisuje se u yta.env aplikacije; podesavanja shell-a se ne menjaju)
#   --whisper [IME]     preuzmi whisper.cpp model (podrazumevano large-v3-turbo-q5_0, ~0,55 GB) u YTA_WHISPER_DIR
#   --port N            port panela (podrazumevano 8790; upisuje YTA_PORT u yta.env)
#   --no-brew           preskoči Homebrew i instalaciju paketa (samo provera)
#   --rebuild-app       ponovo napravi .app (macOS dozvole tada treba ponovo dati)
#   --open              posle instalacije otvori aplikaciju
#   --yes, -y           "da" na sva pitanja (ne uključuje --models)
#   --dry-run           samo ispiši šta bi se uradilo, ništa ne menjaj
#   --force             preskoči proveru macOS/arm64
#   -h, --help          ova pomoć
set -euo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd -P)
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$SELF_DIR/common.sh"
SRC_ROOT=$(cd "$SELF_DIR/.." && pwd -P)

PREFIX=""
APP_BUNDLE=""
AUTOSTART=""          # ""=ne diraj, 1=ukljuci, 0=ukloni
MODELS=""             # ""=pitaj, 1=da, 0=ne
MODELS_DIR=""
WHISPER=""
PORT=""
NO_BREW=0
FULL=0
REBUILD_APP=0
OPEN_AFTER=""
FORCE=0

usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0; }
need_arg() { [ $# -ge 2 ] && [ -n "$2" ] || die "opcija $1 traži vrednost"; }

while [ $# -gt 0 ]; do
  case $1 in
    --prefix) need_arg "$@"; PREFIX=$2; shift ;;
    --prefix=*) PREFIX=${1#*=} ;;
    --app-path) need_arg "$@"; APP_BUNDLE=$2; shift ;;
    --app-path=*) APP_BUNDLE=${1#*=} ;;
    --autostart) AUTOSTART=1 ;;
    --no-autostart) AUTOSTART=0 ;;
    --full) FULL=1 ;;
    --models) MODELS=1 ;;
    --no-models) MODELS=0 ;;
    --models-dir) need_arg "$@"; MODELS_DIR=$2; shift ;;
    --models-dir=*) MODELS_DIR=${1#*=} ;;
    --whisper)
      WHISPER=large-v3-turbo-q5_0
      if [ $# -ge 2 ] && [ "${2#-}" = "$2" ]; then WHISPER=$2; shift; fi ;;
    --whisper=*) WHISPER=${1#*=} ;;
    --port) need_arg "$@"; PORT=$2; shift ;;
    --port=*) PORT=${1#*=} ;;
    --no-brew) NO_BREW=1 ;;
    --rebuild-app) REBUILD_APP=1 ;;
    --open) OPEN_AFTER=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    -h|--help) usage ;;
    *) die "nepoznata opcija: $1 (vidi --help)" ;;
  esac
  shift
done

case $PORT in ''|*[!0-9]*) [ -z "$PORT" ] || die "--port mora biti broj" ;; esac
if [ -n "$PORT" ] && { [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; }; then die "--port mora biti između 1024 i 65535"; fi
case $WHISPER in *[!A-Za-z0-9._-]*) die "neispravno ime whisper modela: $WHISPER" ;; esac

# ---------------------------------------------------------------- putanje
DEFAULT_PREFIX_USED=0
if [ -z "$PREFIX" ]; then PREFIX=$YTA_DEFAULT_PREFIX; DEFAULT_PREFIX_USED=1; fi
PREFIX=$(expand_tilde "$PREFIX")
case $PREFIX in /*) ;; *) PREFIX="$(pwd -P)/$PREFIX" ;; esac
PREFIX=${PREFIX%/}
is_protected_path "$PREFIX" && die "--prefix ne sme biti $PREFIX (izaberi poseban folder, npr. ~/Applications/YT Arhiva)"
APP_DIR="$PREFIX/app"

if is_writable_path "$PREFIX"; then
  DATA_DIR="$PREFIX/data"; ENV_FILE="$PREFIX/yta.env"
else  # npr. /Applications posle .pkg: kod je read-only, podaci idu u ~/Library/Application Support
  DATA_DIR="$YTA_SUPPORT_DIR/data"; ENV_FILE="$YTA_SUPPORT_DIR/yta.env"
fi
# whisper modeli: YTA_WHISPER_DIR iz okruzenja, pa iz yta.env (launcher ga prenosi panelu), pa common.sh
[ -n "$YTA_WHISPER_ENV" ] || YTA_WHISPER_DIR=$(yta_whisper_dir "$(get_env_kv "$ENV_FILE" YTA_WHISPER_DIR)")

if [ -n "$APP_BUNDLE" ]; then
  APP_BUNDLE=$(expand_tilde "$APP_BUNDLE")
elif [ "$DEFAULT_PREFIX_USED" = 1 ] || ! is_writable_path "$PREFIX"; then
  APP_BUNDLE="$HOME/Applications/$YTA_NAME.app"
else
  APP_BUNDLE="$PREFIX/$YTA_NAME.app"
fi
case $APP_BUNDLE in *.app) ;; *) APP_BUNDLE="$APP_BUNDLE.app" ;; esac

LA_DIR=${YTA_LA_DIR:-$HOME/Library/LaunchAgents}   # YTA_LA_DIR samo za testove (tada bez launchctl)
LA_PLIST="$LA_DIR/$YTA_LABEL.plist"
LAUNCHER="$APP_DIR/installer/launcher.sh"
VERSION=$(tr -d ' \n' < "$SRC_ROOT/VERSION" 2>/dev/null || echo "?")

printf '%s%s installer %s%s' "$C_BOLD" "$YTA_NAME" "$VERSION" "$C_0"
[ "$DRY_RUN" = 1 ] && printf '  %s(--dry-run: ništa se ne menja)%s' "$C_Y" "$C_0"
printf '\n'
info "aplikacija: $APP_DIR"
info ".app:       $APP_BUNDLE"
info "podaci:     $DATA_DIR"

# ---------------------------------------------------------------- sistem
step "Provera sistema"
OS=$(uname -s); ARCH=$(uname -m)
if [ "$OS" != Darwin ] || [ "$ARCH" != arm64 ]; then
  if [ "$FORCE" = 1 ]; then warn "sistem $OS/$ARCH nije podržan (macOS arm64) — nastavljam zbog --force"
  else die "potreban je macOS na Apple Silicon (arm64); ovo je $OS/$ARCH (--force za nastavak)"; fi
else
  ok "macOS $(sw_vers -productVersion 2>/dev/null || echo ?) · $ARCH"
fi
for f in panel.py yt_arhiva.py memory.py toolbox.py static/index.html installer/launcher.sh; do
  [ -f "$SRC_ROOT/$f" ] || die "izvor nije potpun: nema $SRC_ROOT/$f"
done
ok "izvor: $SRC_ROOT"

FREE=$(free_gb "$PREFIX")
if [ "$FREE" -lt 5 ]; then
  warn "na disku za aplikaciju ima samo ${FREE} GB slobodno — arhiva videa brzo raste; drži je na eksternom disku (Podešavanja → izlazni folder)"
else
  ok "slobodno na disku ($(nearest_existing "$PREFIX")): ${FREE} GB"
fi

# ---------------------------------------------------------------- Homebrew i paketi
[ "$FULL" = 1 ] && YTA_BREW_PKGS="$YTA_BREW_PKGS $YTA_BREW_HEAVY"
step "Homebrew paketi: $YTA_BREW_PKGS"
BREW=$(find_brew || true)
if [ "$NO_BREW" = 1 ]; then
  info "preskačem instalaciju paketa (--no-brew)"
else
  if [ -z "$BREW" ]; then
    warn "Homebrew nije instaliran"
    if ask "Instalirati Homebrew sada (zvanični install sa brew.sh)?" d; then
      if [ "$DRY_RUN" = 1 ]; then
        # shellcheck disable=SC2016
        dry_note '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
      else
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      fi
      BREW=$(find_brew || true)
      [ -n "$BREW" ] || [ "$DRY_RUN" = 1 ] || die "Homebrew instalacija nije uspela"
      [ -n "$BREW" ] || BREW=/opt/homebrew/bin/brew
    else
      die "bez Homebrew-a nema yt-dlp/ffmpeg-full/ollama; instaliraj ga (https://brew.sh) ili pokreni sa --no-brew"
    fi
  fi
  # instalirano = folder u Cellar; "brew list" se ne zove jer osvezava API keš (upis u HOME i mreza, i u --dry-run)
  BREW_PREFIX=$(dirname "$(dirname "$BREW")")
  brew_ver() {
    local n v last=""
    for n in "$@"; do
      v=$(readlink "$BREW_PREFIX/opt/$n" 2>/dev/null || true)   # ../Cellar/<ime>/<aktivna verzija>
      [ -n "$v" ] && [ -d "$BREW_PREFIX/Cellar/$n/${v##*/}" ] && { printf '%s' "${v##*/}"; return 0; }
      for v in "$BREW_PREFIX/Cellar/$n"/*; do [ -d "$v" ] && last=${v##*/}; done
      [ -n "$last" ] && { printf '%s' "$last"; return 0; }
    done
    return 1
  }
  MISSING=""
  for p in $YTA_BREW_PKGS; do
    if V=$(brew_ver "$p" "${p/whisper-cpp/whisper.cpp}"); then ok "$p ($V)"; else MISSING="$MISSING $p"; fi
  done
  if [ -n "$MISSING" ]; then
    info "nedostaje:$MISSING"
    # shellcheck disable=SC2086
    run "$BREW" install $MISSING
  fi
fi
# provera alata bez obzira na --no-brew
for t in yt-dlp deno; do
  if PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" command -v "$t" >/dev/null 2>&1; then :; else warn "$t nije na PATH-u (brew install $t)"; fi
done
for t in ollama whisper-cli; do  # tezi alati: panel ih preuzima sam pri prvom koriscenju
  if PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" command -v "$t" >/dev/null 2>&1; then :
  else info "$t: preuzima se pri prvom korišćenju (ili odmah: --full)"; fi
done
if [ -x /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg ] || [ -x /usr/local/opt/ffmpeg-full/bin/ffmpeg ]; then :
else warn "ffmpeg-full nije instaliran (treba za burn titla): brew install ffmpeg-full"; fi
if PY=$(find_python); then ok "python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"
else warn "nema Python 3.9+ — dolazi uz 'brew install yt-dlp' (ili brew install python)"; fi

# ---------------------------------------------------------------- kopiranje aplikacije
step "Aplikacija → $APP_DIR"
SAME=0
if [ -d "$APP_DIR" ] && [ "$(abspath "$APP_DIR")" = "$SRC_ROOT" ]; then SAME=1; fi
if [ "$SAME" = 1 ]; then
  ok "izvor je već na odredištu (npr. posle .pkg) — kopiranje preskočeno"
else
  # rsync --delete sme samo u nas folder: postojeci neprazan app/ bez YT Arhiva fajlova se ne dira
  if [ -d "$APP_DIR" ] && [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ] && ! is_yta_app_dir "$APP_DIR"; then
    die "$APP_DIR postoji i nije YT Arhiva — izaberi drugi --prefix"
  fi
  ITEMS=""
  for it in panel.py yt_arhiva.py memory.py toolbox.py static docs installer README.md LICENSE SECURITY.md runbook.md VERSION tests; do
    [ -e "$SRC_ROOT/$it" ] && ITEMS="$ITEMS $it"
  done
  run mkdir -p "$APP_DIR"
  if command -v rsync >/dev/null 2>&1; then
    # data/ nikad ne ide u app (podaci su u $DATA_DIR); --delete cisti stare fajlove u app/
    # shellcheck disable=SC2086
    (cd "$SRC_ROOT" && run rsync -a --delete --exclude '__pycache__/' --exclude '.DS_Store' --exclude 'data/' $ITEMS "$APP_DIR/")
  else
    for it in $ITEMS; do run rm -rf "${APP_DIR:?}/$it"; run cp -R "$SRC_ROOT/$it" "$APP_DIR/$it"; done
  fi
  run chmod +x "$APP_DIR/installer/"*.sh
  [ "$DRY_RUN" = 1 ] || find "$APP_DIR" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
  ok "kopirano:$ITEMS"
fi

# ---------------------------------------------------------------- podaci i yta.env
step "Podaci i podešavanja okruženja"
run mkdir -p "$DATA_DIR"
set_env_kv "$ENV_FILE" YTA_DATA "$DATA_DIR"
set_env_kv "$ENV_FILE" YTA_APP_BUNDLE "$APP_BUNDLE"
[ -z "$PORT" ] || set_env_kv "$ENV_FILE" YTA_PORT "$PORT"
if [ -n "$MODELS_DIR" ]; then
  MODELS_DIR=$(expand_tilde "$MODELS_DIR")
  case $MODELS_DIR in /*) ;; *) die "--models-dir mora biti apsolutna putanja" ;; esac
  run mkdir -p "$MODELS_DIR"
  set_env_kv "$ENV_FILE" OLLAMA_MODELS "$MODELS_DIR"
fi
ok "env: $ENV_FILE"
PANEL_PORT=${PORT:-$(get_env_kv "$ENV_FILE" YTA_PORT)}
PANEL_PORT=${PANEL_PORT:-$YTA_DEFAULT_PORT}

# ---------------------------------------------------------------- .app
step "$YTA_NAME.app"
applescript_quote() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
L_Q=$(applescript_quote "$LAUNCHER")
AS_1='on run'
AS_2='  try'
AS_3="    do shell script \"/bin/bash \" & quoted form of \"$L_Q\" & \" --from-app\""
AS_4='  on error errMsg number errNum'
AS_5="    if errNum is not -128 and (system attribute \"YTA_NO_DIALOG\") is not \"1\" then display alert \"$YTA_NAME\" message errMsg as critical"
AS_6='  end try'
AS_7='end run'
STAMP=$(printf '%s\n' "$AS_1" "$AS_2" "$AS_3" "$AS_4" "$AS_5" "$AS_6" "$AS_7" "$YTA_BUNDLE_ID" | shasum -a 256 | awk '{print $1}')
STAMP_F="$APP_BUNDLE/Contents/Resources/yta-stamp"

build_app() {
  local tmp new
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/yta-app.XXXXXX")
  new="$tmp/$YTA_NAME.app"
  if command -v osacompile >/dev/null 2>&1; then
    osacompile -o "$new" -e "$AS_1" -e "$AS_2" -e "$AS_3" -e "$AS_4" -e "$AS_5" -e "$AS_6" -e "$AS_7" 2>/dev/null \
      || die "osacompile nije uspeo"
    plutil -replace CFBundleIdentifier -string "$YTA_BUNDLE_ID" "$new/Contents/Info.plist"
    plutil -replace CFBundleName -string "$YTA_NAME" "$new/Contents/Info.plist"
  else  # rezerva: minimalan bundle sa shell skriptom
    mkdir -p "$new/Contents/MacOS" "$new/Contents/Resources"
    printf '#!/bin/bash\nexec /bin/bash %q --from-app\n' "$LAUNCHER" > "$new/Contents/MacOS/yt-arhiva"
    chmod +x "$new/Contents/MacOS/yt-arhiva"
    cat > "$new/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>yt-arhiva</string>
  <key>CFBundleIdentifier</key><string>$YTA_BUNDLE_ID</string>
  <key>CFBundleName</key><string>$YTA_NAME</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict></plist>
PLIST
  fi
  printf '%s\n' "$STAMP" > "$new/Contents/Resources/yta-stamp"
  # ad-hoc potpis posle izmena Info.plist-a (bez potpisa macOS ne pamti dozvole)
  codesign --force --sign - "$new" >/dev/null 2>&1 || warn "codesign nije uspeo (aplikacija radi, ali dozvole se možda neće pamtiti)"
  mkdir -p "$(dirname "$APP_BUNDLE")"
  rm -rf "$APP_BUNDLE"
  mv "$new" "$APP_BUNDLE"
  rm -rf "$tmp"
}

if [ -d "$APP_BUNDLE" ] && [ "$REBUILD_APP" = 0 ] && [ "$(cat "$STAMP_F" 2>/dev/null)" = "$STAMP" ]; then
  ok "postoji i nepromenjena — ne pravim ponovo (macOS dozvole date aplikaciji ostaju)"
else
  if [ -d "$APP_BUNDLE" ]; then
    # tudja .app se ne brise ni uz --rebuild-app (mora imati nas stamp ili bundle id)
    if [ ! -f "$APP_BUNDLE/Contents/Resources/yta-stamp" ] && \
       [ "$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$APP_BUNDLE/Contents/Info.plist" 2>/dev/null || true)" != "$YTA_BUNDLE_ID" ]; then
      die "$APP_BUNDLE postoji, ali je nije napravio ovaj installer — ukloni je ili izaberi --app-path"
    fi
    warn "pravim .app ponovo (promenjena putanja ili --rebuild-app): dozvole iz Privatnost i bezbednost treba dati ponovo"
  fi
  if [ "$DRY_RUN" = 1 ]; then
    dry_note "osacompile -o $(qcmd "$APP_BUNDLE") (do shell script: /bin/bash $(qcmd "$LAUNCHER") --from-app)"
    dry_note "plutil CFBundleIdentifier=$YTA_BUNDLE_ID; codesign --force --sign - (ad-hoc)"
  else
    build_app
  fi
  ok "$APP_BUNDLE"
fi

# ---------------------------------------------------------------- LaunchAgent
step "Pokretanje pri prijavi (LaunchAgent)"
UID_N=$(id -u)
la_unload() {
  if [ -n "${YTA_LA_DIR:-}" ]; then dry_note "YTA_LA_DIR je postavljen (test) — launchctl preskočen"; return 0; fi
  run launchctl bootout "gui/$UID_N/$YTA_LABEL" 2>/dev/null || true
}
la_load() {
  if [ -n "${YTA_LA_DIR:-}" ]; then dry_note "YTA_LA_DIR je postavljen (test) — launchctl preskočen"; return 0; fi
  run launchctl bootstrap "gui/$UID_N" "$LA_PLIST" || warn "launchctl bootstrap nije uspeo — radi posle sledeće prijave"
}
if [ "$AUTOSTART" = 1 ]; then
  LOG_X=$(xml_escape "$DATA_DIR/launchagent.log")
  PLIST_BODY=$(cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$YTA_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml_escape "$LAUNCHER")</string>
    <string>--autostart</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>AbandonProcessGroup</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOG_X</string>
  <key>StandardErrorPath</key><string>$LOG_X</string>
</dict>
</plist>
PLIST
)
  if [ -f "$LA_PLIST" ] && [ "$(cat "$LA_PLIST")" = "$PLIST_BODY" ]; then
    ok "već uključeno ($LA_PLIST)"
  else
    if [ "$DRY_RUN" = 1 ]; then
      dry_note "upisao bih $LA_PLIST (RunAtLoad=true, KeepAlive=false → $LAUNCHER --autostart)"
    else
      mkdir -p "$LA_DIR"
      printf '%s\n' "$PLIST_BODY" > "$LA_PLIST.tmp"
      plutil -lint "$LA_PLIST.tmp" >/dev/null || die "neispravan plist: $LA_PLIST.tmp"
      [ -f "$LA_PLIST" ] && la_unload
      mv "$LA_PLIST.tmp" "$LA_PLIST"
    fi
    la_load
    ok "uključeno: $LA_PLIST"
  fi
elif [ "$AUTOSTART" = 0 ]; then
  if [ -f "$LA_PLIST" ]; then la_unload; run rm -f "$LA_PLIST"; ok "LaunchAgent uklonjen"; else ok "LaunchAgent nije bio uključen"; fi
else
  if [ -f "$LA_PLIST" ]; then ok "uključen (ostaje; --no-autostart ga uklanja)"; else info "nije uključeno (dodaj --autostart)"; fi
fi

# ---------------------------------------------------------------- Ollama modeli
step "Ollama modeli za prevod"
EFF_MODELS_DIR=${MODELS_DIR:-$(get_env_kv "$ENV_FILE" OLLAMA_MODELS)}
EFF_MODELS_DIR=${EFF_MODELS_DIR:-${OLLAMA_MODELS:-$HOME/.ollama/models}}
OLLAMA_BIN=$(PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" command -v ollama || true)
HAVE_MODELS=""
if [ -n "$OLLAMA_BIN" ] && curl -s -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  HAVE_MODELS=$("$OLLAMA_BIN" list 2>/dev/null | awk 'NR>1 {print $1}' || true)
fi
have_model() {  # u listi pokrenutog servera ili manifest na disku (radi i kad Ollama ne radi)
  case $'\n'"$HAVE_MODELS"$'\n' in *$'\n'"$1"$'\n'*) return 0 ;; esac  # bez grep -q (pipefail + SIGPIPE)
  [ -f "$EFF_MODELS_DIR/manifests/registry.ollama.ai/library/${1%%:*}/${1#*:}" ]
}
TODO=""
for m in $YTA_MODELS; do
  if have_model "$m"; then ok "$m (već preuzet)"; else TODO="$TODO $m"; fi
done
if [ -n "$TODO" ]; then
  MFREE=$(free_gb "$EFF_MODELS_DIR")
  info "potrebno: gemma4:12b-it-qat 7,2 GB + translategemma:12b 8,1 GB = 15,3 GB (oba)"
  info "lokacija: $EFF_MODELS_DIR — slobodno ${MFREE} GB"
  [ "$MFREE" -ge 18 ] || warn "malo prostora za modele (${MFREE} GB) — izaberi eksterni disk: --models-dir /Volumes/<disk>/ollama-models"
  if [ -z "$MODELS" ]; then
    if [ "$ASSUME_YES" = 1 ] || ! is_tty; then MODELS=0; else
      if ask "Preuzeti modele sada (~15 GB)?" n; then MODELS=1; else MODELS=0; fi
    fi
    if [ "$MODELS" = 1 ] && [ -z "$MODELS_DIR" ] && is_tty && [ "$DRY_RUN" = 0 ]; then
      printf '  %s?%s Folder za modele [%s]: ' "$C_Y" "$C_0" "$EFF_MODELS_DIR"
      read -r ans || ans=""
      if [ -n "$ans" ]; then
        EFF_MODELS_DIR=$(expand_tilde "$ans"); run mkdir -p "$EFF_MODELS_DIR"
        set_env_kv "$ENV_FILE" OLLAMA_MODELS "$EFF_MODELS_DIR"
      fi
    fi
  fi
  if [ "$MODELS" = 1 ]; then
    if [ "$MFREE" -lt 16 ] && [ "$DRY_RUN" = 0 ]; then
      ask "Nema dovoljno mesta (${MFREE} GB < 16 GB). Ipak nastaviti?" n || die "preuzimanje modela otkazano"
    fi
    [ -n "$OLLAMA_BIN" ] || [ "$DRY_RUN" = 1 ] || die "ollama nije instaliran (brew install ollama)"
    OLLAMA_BIN=${OLLAMA_BIN:-ollama}
    SERVE_PID=""
    if ! curl -s -m 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      if [ "$DRY_RUN" = 1 ]; then dry_note "OLLAMA_MODELS=$(qcmd "$EFF_MODELS_DIR") ollama serve (privremeno)"
      else
        mkdir -p "$DATA_DIR"
        OLLAMA_MODELS="$EFF_MODELS_DIR" nohup "$OLLAMA_BIN" serve >>"$DATA_DIR/ollama.log" 2>&1 &
        SERVE_PID=$!
        trap '[ -n "$SERVE_PID" ] && kill "$SERVE_PID" 2>/dev/null || true' EXIT
        for _ in $(seq 1 40); do curl -s -m 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 0.25; done
      fi
    elif [ -n "$MODELS_DIR" ]; then
      warn "Ollama već radi — modeli idu u NJEGOV folder; za $MODELS_DIR ugasi Ollama (i Ollama.app) pa ponovi"
    fi
    for m in $TODO; do run "$OLLAMA_BIN" pull "$m"; done
    ok "modeli spremni"
  else
    info "preskočeno — kasnije: install.sh --models ili Podešavanja → 'Preuzmi model'"
  fi
fi

# ---------------------------------------------------------------- whisper model (opciono)
if [ -n "$WHISPER" ]; then
  step "Whisper model ($WHISPER)"
  WM="$YTA_WHISPER_DIR/ggml-$WHISPER.bin"
  if [ -f "$WM" ]; then ok "već postoji: $WM"
  else
    info "odredište: $WM (slobodno $(free_gb "$YTA_WHISPER_DIR") GB)"
    run mkdir -p "$YTA_WHISPER_DIR"
    run curl -L --fail -sS -C - -o "$WM.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WHISPER.bin"
    run mv "$WM.part" "$WM"
    ok "$WM"
  fi
fi

# ---------------------------------------------------------------- kraj
step "Gotovo"
PIDS_RUNNING=""
for f in "$DATA_DIR"/panel-*.pid; do [ -f "$f" ] && PIDS_RUNNING="$PIDS_RUNNING $(basename "$f" .pid)"; done
info "Pokretanje:  otvori \"$APP_BUNDLE\" (Finder → Aplikacije) → http://127.0.0.1:$PANEL_PORT"
info "Terminal:    $(qcmd "$LAUNCHER")   (--status, --stop, --restart)"
info "Prvi put:    Keychain \"Chrome Safe Storage\" → 'Uvek dozvoli' (Always Allow); detalji u README.md"
info "API ključevi: security add-generic-password -a \"\$USER\" -s yt-arhiva-gemini -U -w"
if [ -n "$PIDS_RUNNING" ] && [ "$SAME" = 0 ]; then
  warn "panel već radi ($PIDS_RUNNING) — novi kod važi posle: $(qcmd "$LAUNCHER") --restart"
fi
if [ "$OPEN_AFTER" = 1 ] || { [ -z "$OPEN_AFTER" ] && [ "$ASSUME_YES" = 0 ] && is_tty && [ "$DRY_RUN" = 0 ] && ask "Otvoriti $YTA_NAME sada?" d; }; then
  run /usr/bin/open "$APP_BUNDLE"
fi
exit 0
