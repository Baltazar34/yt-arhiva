#!/bin/bash
# YT Arhiva — uklanjanje aplikacije.
#
#   installer/uninstall.sh [--prefix DIR] [--app-path PATH] [--dry-run] [--purge]
#
# Uklanja: aplikaciju (<prefix>/app), "YT Arhiva.app" i LaunchAgent; zaustavlja panel.
# Čuva: arhivu videa, Ollama/whisper modele, podešavanja/poslove/ispravke (data) i yta.env.
#   --purge   posle uklanjanja PITA za svaku stavku: podaci, arhiva, modeli (samo u terminalu)
#   --dry-run samo ispiši šta bi se uradilo
set -euo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd -P)
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$SELF_DIR/common.sh"

PREFIX=""
APP_BUNDLE=""
PURGE=0
usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
while [ $# -gt 0 ]; do
  case $1 in
    --prefix) [ $# -ge 2 ] || die "--prefix traži vrednost"; PREFIX=$2; shift ;;
    --prefix=*) PREFIX=${1#*=} ;;
    --app-path) [ $# -ge 2 ] || die "--app-path traži vrednost"; APP_BUNDLE=$2; shift ;;
    --app-path=*) APP_BUNDLE=${1#*=} ;;
    --purge) PURGE=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) usage ;;
    *) die "nepoznata opcija: $1" ;;
  esac
  shift
done

DEFAULT_PREFIX_USED=0
if [ -z "$PREFIX" ]; then PREFIX=$YTA_DEFAULT_PREFIX; DEFAULT_PREFIX_USED=1; fi
PREFIX=$(expand_tilde "$PREFIX")
case $PREFIX in /*) ;; *) PREFIX="$(pwd -P)/$PREFIX" ;; esac
PREFIX=${PREFIX%/}
is_protected_path "$PREFIX" && die "--prefix ne sme biti $PREFIX"
APP_DIR="$PREFIX/app"
ENV_FILE="$PREFIX/yta.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$YTA_SUPPORT_DIR/yta.env"
# whisper modeli: YTA_WHISPER_DIR iz okruzenja, pa iz yta.env (launcher ga prenosi panelu), pa common.sh
[ -n "$YTA_WHISPER_ENV" ] || YTA_WHISPER_DIR=$(yta_whisper_dir "$(get_env_kv "$ENV_FILE" YTA_WHISPER_DIR)")
DATA_DIR=$(get_env_kv "$ENV_FILE" YTA_DATA)
DATA_DIR=${DATA_DIR:-$PREFIX/data}
if [ -n "$APP_BUNDLE" ]; then APP_BUNDLE=$(expand_tilde "$APP_BUNDLE"); fi
if [ -z "$APP_BUNDLE" ]; then
  APP_BUNDLE=$(get_env_kv "$ENV_FILE" YTA_APP_BUNDLE)
  if [ -z "$APP_BUNDLE" ]; then
    if [ "$DEFAULT_PREFIX_USED" = 1 ]; then APP_BUNDLE="$HOME/Applications/$YTA_NAME.app"; else APP_BUNDLE="$PREFIX/$YTA_NAME.app"; fi
  fi
fi
LA_DIR=${YTA_LA_DIR:-$HOME/Library/LaunchAgents}
LA_PLIST="$LA_DIR/$YTA_LABEL.plist"

# --purge pita za svaku stavku: bez terminala odmah prekid (pre bilo kakvog brisanja)
if [ "$PURGE" = 1 ] && [ "$DRY_RUN" = 0 ] && ! is_tty; then die "--purge radi samo u terminalu (pita za svaku stavku)"; fi

printf '%s%s — uklanjanje%s' "$C_BOLD" "$YTA_NAME" "$C_0"
[ "$DRY_RUN" = 1 ] && printf '  %s(--dry-run: ništa se ne menja)%s' "$C_Y" "$C_0"
printf '\n'

step "Zaustavljanje panela"
if [ -x "$APP_DIR/installer/launcher.sh" ]; then
  run /bin/bash "$APP_DIR/installer/launcher.sh" --stop
else
  info "launcher nije pronađen ($APP_DIR) — preskačem"
fi

step "LaunchAgent"
if [ -f "$LA_PLIST" ]; then
  if grep -qF "$(xml_escape "$APP_DIR/installer/launcher.sh")" "$LA_PLIST"; then
    if [ -z "${YTA_LA_DIR:-}" ]; then run launchctl bootout "gui/$(id -u)/$YTA_LABEL" 2>/dev/null || true; fi
    run rm -f "$LA_PLIST"
    ok "uklonjen $LA_PLIST"
  else
    warn "$LA_PLIST pripada drugoj instalaciji — ostaje"
  fi
else
  ok "nije bio uključen"
fi

step "$YTA_NAME.app"
if [ -d "$APP_BUNDLE" ]; then
  if [ -f "$APP_BUNDLE/Contents/Resources/yta-stamp" ]; then
    run rm -rf "$APP_BUNDLE"; ok "uklonjena $APP_BUNDLE"
  else
    warn "$APP_BUNDLE nije napravio installer — ostaje"
  fi
else
  ok "ne postoji ($APP_BUNDLE)"
fi

step "Aplikacija ($APP_DIR)"
if is_yta_app_dir "$APP_DIR"; then
  if [ -w "$APP_DIR" ] || [ "$DRY_RUN" = 1 ]; then
    run rm -rf "$APP_DIR"; ok "uklonjena"
  else
    warn "nema dozvole za brisanje (instalirano preko .pkg?) — ukloni ručno: sudo rm -rf $(qcmd "$APP_DIR") && sudo pkgutil --forget $YTA_BUNDLE_ID.pkg"
  fi
else
  if [ -e "$APP_DIR" ]; then warn "$APP_DIR nije YT Arhiva — ostaje"; else ok "ne postoji"; fi
fi

OUT_DIR=""
if [ -f "$DATA_DIR/settings.json" ]; then
  OUT_DIR=$(/usr/bin/plutil -extract out_dir raw -o - "$DATA_DIR/settings.json" 2>/dev/null || true)
fi
OUT_DIR=$(expand_tilde "${OUT_DIR:-$HOME/Movies/YT-Arhiva}")
MODELS_DIR=$(get_env_kv "$ENV_FILE" OLLAMA_MODELS)

if [ "$PURGE" = 0 ]; then
  step "Sačuvano (za brisanje: --purge)"
  info "podaci (podešavanja, poslovi, ispravke za obuku): $DATA_DIR"
  info "okruženje: $ENV_FILE"
  info "arhiva: $OUT_DIR"
  info "Ollama modeli: ${MODELS_DIR:-$HOME/.ollama/models} ($YTA_MODELS)"
  info "whisper modeli: $YTA_WHISPER_DIR"
  exit 0
fi

# ---------------------------------------------------------------- --purge: sve uz pitanje
step "Brisanje podataka (--purge)"
ASSUME_YES=0
trash_or_rm() {  # u Kantu kad je moguce (vraca se iz Findera), inace rm -rf
  if [ -x /usr/bin/trash ]; then run /usr/bin/trash "$1"; else run rm -rf "$1"; fi
}
size_of() { du -sh "$1" 2>/dev/null | awk '{print $1}'; }

if [ -d "$DATA_DIR" ] && is_protected_path "$DATA_DIR"; then
  warn "podaci su u $DATA_DIR — taj folder se ne briše automatski; ukloni ručno šta treba"
elif [ -d "$DATA_DIR" ] && ask "Obrisati podatke aplikacije ($DATA_DIR, $(size_of "$DATA_DIR"))? Uključuje ispravke za obuku." n; then
  trash_or_rm "$DATA_DIR"
fi
if [ -f "$ENV_FILE" ] && ask "Obrisati $ENV_FILE?" n; then run rm -f "$ENV_FILE"; fi
if [ -d "$OUT_DIR" ] && is_protected_path "$OUT_DIR"; then
  warn "arhiva je direktno u $OUT_DIR — taj folder se ne briše automatski; ukloni ručno šta treba"
elif [ -d "$OUT_DIR" ]; then
  if [ "$DRY_RUN" = 1 ]; then dry_note "pitao bih za arhivu $OUT_DIR (potvrda kucanjem OBRIŠI)"
  else
    printf '  %s?%s Arhiva %s (%s). Za brisanje ukucaj OBRIŠI (sve drugo = ostaje): ' "$C_Y" "$C_0" "$OUT_DIR" "$(size_of "$OUT_DIR")"
    read -r ans || ans=""
    if [ "$ans" = "OBRIŠI" ] || [ "$ans" = "OBRISI" ]; then trash_or_rm "$OUT_DIR"; else ok "arhiva ostaje"; fi
  fi
fi
OLLAMA_BIN=$(PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" command -v ollama || true)
if [ -n "$OLLAMA_BIN" ] && ask "Ukloniti Ollama modele ($YTA_MODELS)?" n; then
  for m in $YTA_MODELS; do run "$OLLAMA_BIN" rm "$m" || warn "$m: ollama rm nije uspeo (radi li Ollama?)"; done
fi
WMS=$(ls "$YTA_WHISPER_DIR"/ggml-*.bin 2>/dev/null || true)
if [ -n "$WMS" ] && ask "Obrisati whisper modele u $YTA_WHISPER_DIR ($(printf '%s' "$WMS" | wc -l | tr -d ' ') fajl/a)?" n; then
  printf '%s\n' "$WMS" | while IFS= read -r f; do run rm -f "$f"; done
fi
if [ -d "$PREFIX" ] && [ -z "$(ls -A "$PREFIX" 2>/dev/null)" ]; then run rmdir "$PREFIX"; fi
ok "gotovo"
