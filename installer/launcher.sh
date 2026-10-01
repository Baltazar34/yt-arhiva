#!/bin/bash
# YT Arhiva launcher — koriste ga "YT Arhiva.app" i LaunchAgent.
# Pokrece panel.py u pozadini (nohup) ako port nije zauzet, ceka /api/settings i otvara browser.
#
#   launcher.sh              pokreni (ako ne radi) i otvori http://127.0.0.1:8790
#   launcher.sh --no-browser pokreni bez otvaranja browsera
#   launcher.sh --status | --stop | --restart   (--restart pokrece preko "YT Arhiva.app" ako postoji)
#   launcher.sh --no-dialog  bez .app i bez dijaloga (testovi); isto: YTA_NO_DIALOG=1
#
# Okruzenje: YTA_PORT, YTA_DATA, OLLAMA_MODELS, YTA_PYTHON ... iz yta.env (vrednosti iz okruzenja imaju prednost).
set -euo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd -P)
APP_DIR=$(cd "$SELF_DIR/.." && pwd -P)
PREFIX=$(dirname "$APP_DIR")

MODE=start
BROWSER=1
FROM=cli
NO_DIALOG=0

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

for a in "$@"; do
  case $a in
    --no-browser) BROWSER=0 ;;
    --autostart) FROM=autostart; BROWSER=0 ;;
    --from-app) FROM=app ;;
    --status) MODE=status ;;
    --stop) MODE=stop ;;
    --restart) MODE=restart ;;
    --no-dialog) NO_DIALOG=1 ;;
    -h|--help) usage ;;
    *) echo "launcher: nepoznata opcija: $a" >&2; exit 2 ;;
  esac
done
[ "${YTA_NO_BROWSER:-}" = 1 ] && BROWSER=0
[ "${YTA_NO_DIALOG:-}" = 1 ] && NO_DIALOG=1
# iz .app bez dijaloga: greske idu samo u log, izlaz 0 (applet tada ne prikazuje upozorenje)
QUIET_ERR=0
[ "$FROM" = app ] && [ "$NO_DIALOG" = 1 ] && QUIET_ERR=1
LOG=""
fail() {
  if [ "$QUIET_ERR" = 1 ]; then
    if [ -n "$LOG" ]; then
      { mkdir -p "$(dirname "$LOG")" && printf '%s launcher: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; } 2>/dev/null || true
    fi
    exit 0
  fi
  echo "$*" >&2
  exit 1
}

# ---- env fajl (KLJUC="vrednost"; samo YTA_* i OLLAMA_*; ne gazi postojece okruzenje)
ENV_FILE=${YTA_ENV_FILE:-}
if [ -z "$ENV_FILE" ]; then
  for c in "$PREFIX/yta.env" "$HOME/Library/Application Support/YT Arhiva/yta.env"; do
    if [ -f "$c" ]; then ENV_FILE=$c; break; fi
  done
fi
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in ''|'#'*) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    case $key in YTA_*|OLLAMA_*) ;; *) continue ;; esac
    case $key in *[!A-Z0-9_]*) continue ;; esac
    val=${val#\"}; val=${val%\"}
    # shellcheck disable=SC2088
    case $val in "~/"*) val="$HOME/${val#\~/}" ;; esac
    if ! printenv "$key" >/dev/null 2>&1; then export "$key=$val"; fi
  done < "$ENV_FILE"
fi

PORT=${YTA_PORT:-8790}
case $PORT in ''|*[!0-9]*) fail "launcher: neispravan YTA_PORT: $PORT" ;; esac
export YTA_PORT=$PORT
URL="http://127.0.0.1:$PORT"
DATA_DIR=${YTA_DATA:-$APP_DIR/data}
PIDF="$DATA_DIR/panel-$PORT.pid"
LOG="$DATA_DIR/panel.log"

# .app / LaunchAgent imaju minimalan PATH; ffmpeg-full je keg-only (yt-dlp trazi ffmpeg na PATH-u)
P="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin"
[ -d "$HOME/.local/bin" ] && P="$P:$HOME/.local/bin"   # npr. claude CLI, pipx alati (python whisper)
P="$P:${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
for d in /opt/homebrew/opt/ffmpeg-full/bin /usr/local/opt/ffmpeg-full/bin; do [ -d "$d" ] && P="$P:$d"; done
export PATH="$P"

panel_up() {
  curl -s -m 2 "$URL/api/settings" 2>/dev/null | grep -q '"out_dir"'
}
port_busy() {
  if command -v nc >/dev/null 2>&1; then nc -z -G 1 127.0.0.1 "$PORT" >/dev/null 2>&1; return; fi
  [ "$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$URL/" 2>/dev/null || true)" != 000 ]
}
our_pid() {  # pid iz pid fajla, samo ako je to zaista nas panel.py
  local f=$1 pid cmd
  [ -f "$f" ] || return 1
  pid=$(tr -dc '0-9' < "$f")
  [ -n "$pid" ] || return 1
  cmd=$(ps -p "$pid" -o command= 2>/dev/null) || return 1
  case $cmd in *"$APP_DIR/panel.py"*) printf '%s' "$pid" ;; *) return 1 ;; esac
}
stop_all() {
  local f pid n=0 i
  for f in "$DATA_DIR"/panel-*.pid; do
    [ -f "$f" ] || continue
    if pid=$(our_pid "$f"); then
      kill -TERM "$pid" 2>/dev/null || true
      for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
      kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
      echo "zaustavljen panel (pid $pid, $(basename "$f" .pid))"
      n=$((n + 1))
    fi
    rm -f "$f"
  done
  [ "$n" -gt 0 ] || echo "nijedan panel pokrenut ovim launcherom ne radi"
}
open_browser() {
  [ "$BROWSER" = 1 ] || return 0
  /usr/bin/open "$URL/" || true
}

# pokreni preko .app da macOS dozvole (Keychain, diskovi) vaze za "YT Arhiva", ne za bash/Terminal.
# Applet dobija YTA_NO_DIALOG=1: dijalog o gresci vidi samo korisnik koji sam otvori .app.
via_app() {
  [ "$NO_DIALOG" = 0 ] || return 1
  [ -n "${YTA_APP_BUNDLE:-}" ] && [ -d "$YTA_APP_BUNDLE" ] || return 1
  # -n: nova instanca i kada prethodni applet jos nije zavrsio (inace open samo aktivira stari i nista ne pokrene)
  /usr/bin/open -n -g -j --env YTA_NO_DIALOG=1 --env "YTA_NO_BROWSER=$((1 - BROWSER))" --env "YTA_PORT=$PORT" --env "YTA_DATA=$DATA_DIR" \
    -a "$YTA_APP_BUNDLE" 2>/dev/null
}

case $MODE in
  status)
    if panel_up; then echo "radi: $URL"; else echo "ne radi: $URL"; fi
    if pid=$(our_pid "$PIDF"); then echo "pid: $pid (pokrenut launcherom)"; fi
    echo "podaci: $DATA_DIR"
    echo "log: $LOG"
    [ -n "$ENV_FILE" ] && echo "env: $ENV_FILE"
    exit 0 ;;
  stop) stop_all; exit 0 ;;
  restart) stop_all ;;
esac

# pri prijavi (LaunchAgent) i za --restart iz Terminala panel se pokrece preko .app
if [ "$FROM" = autostart ] && via_app; then exit 0; fi
if [ "$MODE" = restart ] && [ "$FROM" = cli ] && ! panel_up && via_app; then
  echo "pokrećem preko $YTA_APP_BUNDLE …"
  for _ in $(seq 1 60); do if panel_up; then echo "radi: $URL"; exit 0; fi; sleep 0.5; done
  echo "Panel ne odgovara posle 30 s. Log: $LOG" >&2
  exit 1
fi

if panel_up; then
  open_browser
  exit 0
fi
if port_busy; then
  fail "Port $PORT je zauzet drugim programom (nije YT Arhiva panel). Promeni YTA_PORT u $ENV_FILE ili ugasi taj program."
fi

PY=""
for c in "${YTA_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$c" ] && [ -x "$c" ] || continue
  if [ "$c" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || fail "Nema Python 3.9+ (brew install python ili yt-dlp)."
[ -f "$APP_DIR/panel.py" ] || fail "Nema $APP_DIR/panel.py — ponovo pokreni install.sh."

mkdir -p "$DATA_DIR"
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG" | tr -d ' ')" -gt 5000000 ]; then mv -f "$LOG" "$LOG.1"; fi
printf '\n=== %s start (%s) port %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$FROM" "$PORT" >> "$LOG"
cd "$APP_DIR"
nohup "$PY" "$APP_DIR/panel.py" </dev/null >>"$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDF"

i=0
while [ $i -lt 60 ]; do
  if panel_up; then open_browser; exit 0; fi
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PIDF"
    [ "$QUIET_ERR" = 0 ] || fail "Panel se nije pokrenuo (log: $LOG)."
    echo "Panel se nije pokrenuo. Poslednje linije loga ($LOG):" >&2
    tail -n 15 "$LOG" >&2 || true
    exit 1
  fi
  sleep 0.5
  i=$((i + 1))
done
fail "Panel ne odgovara posle 30 s (pid $PID). Log: $LOG"
