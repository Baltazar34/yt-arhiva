# shellcheck shell=bash disable=SC2034
# Zajednicke funkcije za install.sh / uninstall.sh / build.sh (ucitava se sa "source").
# Radi i sa macOS /bin/bash 3.2 (bez asocijativnih nizova, bez ${x,,}).

YTA_NAME="YT Arhiva"
YTA_LABEL="com.ytarhiva.panel"
YTA_BUNDLE_ID="com.ytarhiva.app"
YTA_DEFAULT_PORT=8790
YTA_DEFAULT_PREFIX="$HOME/Applications/$YTA_NAME"
YTA_SUPPORT_DIR="$HOME/Library/Application Support/$YTA_NAME"
YTA_MODELS="gemma4:12b-it-qat translategemma:12b"
YTA_BREW_PKGS="yt-dlp deno ffmpeg-full"            # osnovno (malo)
YTA_BREW_HEAVY="ollama whisper-cpp"                 # preuzima panel pri prvom koriscenju (ili --full)

DRY_RUN=${DRY_RUN:-0}
ASSUME_YES=${ASSUME_YES:-0}

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; then
  C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[34m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_0=$'\033[0m'
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_DIM=""; C_BOLD=""; C_0=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$C_B" "$C_0" "$C_BOLD" "$*" "$C_0"; }
ok()   { printf '  %s[ok]%s %s\n' "$C_G" "$C_0" "$*"; }
info() { printf '       %s\n' "$*"; }
warn() { printf '  %s[!]%s  %s\n' "$C_Y" "$C_0" "$*" >&2; }
err()  { printf '%s[GREŠKA]%s %s\n' "$C_R" "$C_0" "$*" >&2; }
die()  { err "$*"; exit 1; }

# komanda kao citljiv (i ponovo upotrebljiv) tekst
qcmd() {
  local s="" a
  for a in "$@"; do s="$s$(printf '%q' "$a") "; done
  printf '%s' "${s% }"
}

dry_note() { printf '  %s[dry-run]%s %s\n' "$C_DIM" "$C_0" "$*"; }

# run CMD...: izvrsi ili (u --dry-run) samo ispisi
run() {
  if [ "$DRY_RUN" = 1 ]; then dry_note "$(qcmd "$@")"; return 0; fi
  "$@"
}

is_tty() { [ -t 0 ] && [ -t 1 ]; }

# ask "pitanje" [d|n]  -> 0 = da. Bez terminala vraca podrazumevano; --yes = da.
ask() {
  local q=$1 def=${2:-n} hint a
  if [ "$def" = d ]; then hint="[D/n]"; else hint="[d/N]"; fi
  if [ "$DRY_RUN" = 1 ]; then dry_note "pitao bih: $q $hint (uzimam: $def)"; [ "$def" = d ]; return; fi
  if [ "$ASSUME_YES" = 1 ]; then info "$q $hint → da (--yes)"; return 0; fi
  if ! is_tty; then info "$q $hint → $def (nije terminal)"; [ "$def" = d ]; return; fi
  printf '  %s?%s %s %s ' "$C_Y" "$C_0" "$q" "$hint"
  read -r a || a=""
  case $a in
    [dDyY]*) return 0 ;;
    [nN]*) return 1 ;;
    *) [ "$def" = d ] ;;
  esac
}

# slobodno mesto (GB, ceo broj) na volumenu gde je putanja ili njen najblizi postojeci roditelj
nearest_existing() {
  local p=$1
  while [ ! -e "$p" ] && [ "$p" != "/" ]; do p=$(dirname "$p"); done
  printf '%s' "$p"
}
free_gb() {
  local p kb
  p=$(nearest_existing "$1")
  kb=$(df -Pk "$p" 2>/dev/null | awk 'NR==2 {print $4}')
  [ -n "$kb" ] || kb=0
  printf '%s' $((kb / 1024 / 1024))
}
is_writable_path() {
  local p
  p=$(nearest_existing "$1")
  [ -w "$p" ]
}

# realpath bez GNU coreutils
abspath() {
  local d=$1
  if [ -d "$d" ]; then (cd "$d" && pwd -P); else
    printf '%s/%s' "$(cd "$(dirname "$d")" && pwd -P)" "$(basename "$d")"
  fi
}

# shellcheck disable=SC2088  # namerno: poredi doslovni ~
expand_tilde() { case $1 in "~") printf '%s' "$HOME" ;; "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;; *) printf '%s' "$1" ;; esac; }

# folder whisper.cpp modela (ggml-*.bin), isti izbor kao panel (yt_arhiva.whisper_models_dir):
# zadata vrednost (YTA_WHISPER_DIR) > podrazumevano u Application Support
yta_whisper_dir() {
  if [ -n "${1:-}" ]; then expand_tilde "$1"; else printf '%s' "$YTA_SUPPORT_DIR/whisper"; fi
}
YTA_WHISPER_ENV=${YTA_WHISPER_DIR:-}   # vrednost iz okruzenja ima prednost i nad yta.env
YTA_WHISPER_DIR=$(yta_whisper_dir "$YTA_WHISPER_ENV")

# putanje koje se nikad ne brisu ni uz --purge: /, prvi nivo (/Users, /Volumes/X ...), HOME i njegovi standardni folderi
is_protected_path() {
  local p=$1 h=${HOME%/} rest
  [ -n "$p" ] || return 0
  [ -e "$p" ] && p=$(abspath "$p")
  p=${p%/}
  [ -n "$p" ] || return 0
  [ -e "$h" ] && h=$(abspath "$h")
  case $p in
    "$h"|"$h/Movies"|"$h/Documents"|"$h/Desktop"|"$h/Downloads"|"$h/Library"|"$h/Applications"|"$h/Music"|"$h/Pictures") return 0 ;;
  esac
  rest=${p#/}
  case $rest in */*) ;; *) return 0 ;; esac                  # /X
  case $p in /Volumes/*|/Users/*|/private/*) rest=${rest#*/}; case $rest in */*) ;; *) return 0 ;; esac ;; esac
  return 1
}

# folder aplikacije koji je napravio ovaj installer (ili je izvor): sme da se prepise/obrise
is_yta_app_dir() {
  [ -f "$1/panel.py" ] && [ -f "$1/yt_arhiva.py" ] && [ -f "$1/installer/launcher.sh" ]
}

find_brew() {
  local b
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && { printf '%s' "$b"; return 0; }
  done
  command -v brew 2>/dev/null || return 1
}

find_python() {
  local c
  for c in "${YTA_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    # /usr/bin/python3 bez Xcode CLT otvara dijalog za instalaciju -> preskoci
    if [ "$c" = /usr/bin/python3 ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      printf '%s' "$c"; return 0
    fi
  done
  return 1
}

# set_env_kv FAJL KLJUC VREDNOST: upisi/zameni KLJUC="VREDNOST" (idempotentno)
set_env_kv() {
  local f=$1 k=$2 v=$3 tmp
  case $v in *'"'*|*$'\n'*) die "vrednost za $k ne sme da sadrži navodnik ili novi red: $v" ;; esac
  if [ "$DRY_RUN" = 1 ]; then dry_note "u $f: $k=\"$v\""; return 0; fi
  mkdir -p "$(dirname "$f")"
  [ -f "$f" ] || printf '# YT Arhiva — okruženje panela (čita ga launcher.sh; vrednosti iz okruženja imaju prednost)\n' > "$f"
  if grep -q "^$k=" "$f" 2>/dev/null; then
    if grep -qx "$k=\"$v\"" "$f"; then return 0; fi
    tmp="$f.tmp.$$"
    awk -v k="$k" -v line="$k=\"$v\"" 'index($0, k "=") == 1 { print line; next } { print }' "$f" > "$tmp"
    mv "$tmp" "$f"
  else
    printf '%s="%s"\n' "$k" "$v" >> "$f"
  fi
}

# get_env_kv FAJL KLJUC -> vrednost (bez navodnika) ili prazno
get_env_kv() {
  local f=$1 k=$2 line
  [ -f "$f" ] || return 0
  line=$(grep "^$k=" "$f" | tail -n 1) || true
  line=${line#*=}; line=${line#\"}; line=${line%\"}
  printf '%s' "$line"
}

# sed umesto ${s//} jer bash 5.2 (patsub_replacement) tumaci & u zameni
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
}
