#!/bin/bash
# Test installera u privremenom HOME (ne dira pravi ~/Applications ni ~/Library/LaunchAgents, bez launchctl).
# "YT Arhiva.app" se nikad ne otvara (YTA_NO_DIALOG=1): bez dijaloga na ekranu, launcher se proverava direktno.
#
#   bash tests/test_installer.sh [RADNI_FOLDER]
#
#   YTA_TEST_PORT=8818   port test panela (drugi: +1 za "port zauzet")
#   YTA_TEST_KEEP=1      ne brisi radni folder
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
W=${1:-$(mktemp -d "${TMPDIR:-/tmp}/yta-inst-test.XXXXXX")}
mkdir -p "$W"; W=$(cd "$W" && pwd -P)
PORT=${YTA_TEST_PORT:-8818}
PORT2=$((PORT + 1))
H="$W/home dir"            # razmak u putanji namerno
PASS=0; FAIL=0; FAILED=""
export YTA_NO_DIALOG=1

ck() {  # ck "opis" komanda...
  local n=$1; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS + 1)); echo "ok    $n"
  else FAIL=$((FAIL + 1)); FAILED="$FAILED|$n"; echo "FAIL  $n"; fi
}
not() { ! "$@"; }
has() { grep -qF -- "$2" "$1"; }            # has FAJL TEKST
up() { curl -s -m 2 "http://127.0.0.1:$1/api/settings" 2>/dev/null | grep -q '"out_dir"'; }
snap() { (cd "$1" && find . -path ./log -prune -o -print0 | xargs -0 stat -f '%N %p %z %m %i' | LC_ALL=C sort); }
wait_up() { local _; for _ in $(seq 1 40); do up "$1" && return 0; sleep 0.5; done; return 1; }
cleanup() {
  [ -f "$H/Applications/YT Arhiva/app/installer/launcher.sh" ] &&
    HOME="$H" /bin/bash "$H/Applications/YT Arhiva/app/installer/launcher.sh" --stop >/dev/null 2>&1
  [ -n "${HTTP_PID:-}" ] && kill "$HTTP_PID" 2>/dev/null
  [ "${YTA_TEST_KEEP:-}" = 1 ] || rm -rf "$W"
}
trap cleanup EXIT

for p in "$PORT" "$PORT2"; do
  if nc -z -G 1 127.0.0.1 "$p" 2>/dev/null; then echo "port $p je zauzet — postavi YTA_TEST_PORT"; exit 2; fi
done
mkdir -p "$H" "$W/log"
echo "radni folder: $W"

# ---------------------------------------------------------------- 1. skripte
for s in "$ROOT"/installer/*.sh; do ck "bash -n $(basename "$s")" /bin/bash -n "$s"; done
sc() { (cd "$ROOT/installer" && shellcheck -x ./*.sh); }
if command -v shellcheck >/dev/null 2>&1; then ck "shellcheck" sc; fi
for s in install build uninstall launcher; do
  /bin/bash "$ROOT/installer/$s.sh" --help > "$W/log/help-$s.txt" 2>&1
  ck "$s --help bez 'set -e' linije" not has "$W/log/help-$s.txt" "set -euo"
done
HOME_REAL=$HOME; HOME=$H
# shellcheck source-path=SCRIPTDIR source=../installer/common.sh
. "$ROOT/installer/common.sh"
for p in / /Users /Applications "/Users/test" /Volumes/Disk "$HOME" "$HOME/" "$HOME/Movies" /private/tmp ""; do
  ck "zaštićeno: '$p'" is_protected_path "$p"
done
for p in "$HOME/Movies/YT-Arhiva" /Volumes/Disk/arhiva "$HOME/Applications/YT Arhiva" "/Applications/YT Arhiva"; do
  ck "nije zaštićeno: $p" not is_protected_path "$p"
done
# whisper modeli: zadata vrednost (YTA_WHISPER_DIR) > Application Support, isto kao panel
WN="$H/Library/Application Support/YT Arhiva/whisper"
py_wdir() {  # py_wdir [YTA_WHISPER_DIR]: izbor panela (yt_arhiva.whisper_models_dir) u privremenom HOME
  HOME="$H" python3 -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import yt_arhiva as c
print(c.whisper_models_dir({"YTA_WHISPER_DIR": sys.argv[2]}))' "$ROOT" "${1:-}"
}
ck "whisper: podrazumevano u Application Support" test "$(yta_whisper_dir "")" = "$WN"
ck "whisper: panel bira isto (podrazumevano)" test "$(py_wdir)" = "$WN"
ck "whisper: zadata vrednost ima prednost" test "$(yta_whisper_dir "$W/w dir")" = "$W/w dir"
ck "whisper: panel bira isto (zadata vrednost)" test "$(py_wdir "$W/w dir")" = "$W/w dir"
# shellcheck disable=SC2088  # namerno: doslovni ~ kao u yta.env
ck "whisper: ~ u zadatoj vrednosti" test "$(yta_whisper_dir "~/w")" = "$H/w"
# shellcheck disable=SC2088  # namerno: doslovni ~ kao u yta.env
ck "whisper: panel bira isto (~)" test "$(py_wdir "~/w")" = "$H/w"
ck "whisper: izbor ništa ne pravi" test ! -e "$WN"
HOME=$HOME_REAL

# ---------------------------------------------------------------- 2. build
VER=$(tr -d ' \n' < "$ROOT/VERSION")
/bin/bash "$ROOT/installer/build.sh" --no-pkg --no-pdf --out "$W/dist" > "$W/log/build" 2>&1
ck "build.sh" test -f "$W/dist/yt-arhiva-$VER.tar.gz"
ck "sha256" /bin/bash -c "cd '$W/dist' && shasum -a 256 -c 'yt-arhiva-$VER.tar.gz.sha256'"
tar -tzf "$W/dist/yt-arhiva-$VER.tar.gz" > "$W/log/tar.txt"
ck "tar bez xattr zaglavlja" /bin/bash -c "! gzip -dc '$W/dist/yt-arhiva-$VER.tar.gz' | grep -aq 'xattr.com.apple'"
ck "tar bez imena lokalnog korisnika" /bin/bash -c "! tar -tvzf '$W/dist/yt-arhiva-$VER.tar.gz' | awk '{print \$3}' | grep -qvx root"
for f in panel.py yt_arhiva.py memory.py toolbox.py static/index.html installer/install.sh installer/launcher.sh \
         installer/uninstall.sh installer/common.sh installer/README.md README.md LICENSE SECURITY.md runbook.md VERSION; do
  ck "tar ima $f" grep -qx "yt-arhiva-$VER/$f" "$W/log/tar.txt"
done
for bad in '/data/' '__pycache__' '.DS_Store' '/._' '/tests/' '/dist/' 'PaxHeader'; do
  ck "tar nema $bad" not grep -qF -- "$bad" "$W/log/tar.txt"
done
mkdir -p "$W/src dir"
tar -xzf "$W/dist/yt-arhiva-$VER.tar.gz" -C "$W/src dir"
SRC="$W/src dir/yt-arhiva-$VER"
ck "raspakovan installer izvršan" test -x "$SRC/installer/install.sh"

# ---------------------------------------------------------------- 3. --dry-run bez izmena
TMPD=$(mktemp -d "${TMPDIR:-/tmp}/yta-dry.XXXXXX")
snap "$W" > "$W/log/snap-a"
HOME="$H" TMPDIR="$TMPD" /bin/bash "$SRC/installer/install.sh" --dry-run --autostart --models \
  --models-dir "$W/mod els" --whisper --port "$PORT" --open --yes > "$W/log/dry1" 2>&1
r1=$?
HOME="$H" TMPDIR="$TMPD" /bin/bash "$SRC/installer/install.sh" --dry-run --no-brew --no-autostart \
  --prefix "$W/drugi prefix/YT Arhiva" --rebuild-app > "$W/log/dry2" 2>&1
r2=$?
HOME="$H" TMPDIR="$TMPD" /bin/bash "$SRC/installer/uninstall.sh" --dry-run --purge > "$W/log/dry3" 2>&1
r3=$?
snap "$W" > "$W/log/snap-b"
ck "dry-run install rc=0" test "$r1" = 0
ck "dry-run install (prefix sa razmakom) rc=0" test "$r2" = 0
ck "dry-run uninstall --purge rc=0" test "$r3" = 0
ck "dry-run: radni folder i HOME nepromenjeni" cmp -s "$W/log/snap-a" "$W/log/snap-b"
cmp -s "$W/log/snap-a" "$W/log/snap-b" || diff "$W/log/snap-a" "$W/log/snap-b" | head -20
ck "dry-run: TMPDIR prazan" test -z "$(ls -A "$TMPD")"
ck "dry-run ispis: plist" has "$W/log/dry1" "upisao bih"
ck "dry-run ispis: osacompile" has "$W/log/dry1" "osacompile"
rm -rf "$TMPD"

# ---------------------------------------------------------------- 4. zastita
ck "--prefix = HOME odbijen" not env HOME="$H" /bin/bash "$SRC/installer/install.sh" --no-brew --prefix "$H" --yes
mkdir -p "$W/tudji/app/docs"; echo keep > "$W/tudji/app/docs/moje.txt"
ck "tuđ app/ odbijen" not env HOME="$H" /bin/bash "$SRC/installer/install.sh" --no-brew --prefix "$W/tudji" --yes
ck "tuđ fajl ostao" test -f "$W/tudji/app/docs/moje.txt"
HOME="$H" /bin/bash "$SRC/installer/uninstall.sh" --prefix "$W/tudji" > "$W/log/un-tudji.log" 2>&1
ck "uninstall ne briše tuđ app/" test -f "$W/tudji/app/docs/moje.txt"
ck "--port 80 odbijen" not env HOME="$H" /bin/bash "$SRC/installer/install.sh" --dry-run --no-brew --port 80

# ---------------------------------------------------------------- 5. instalacija
P="$H/Applications/YT Arhiva"; APP="$H/Applications/YT Arhiva.app"; LA="$H/Library/LaunchAgents/com.ytarhiva.panel.plist"
L="$P/app/installer/launcher.sh"
inst() { HOME="$H" YTA_LA_DIR="$H/Library/LaunchAgents" /bin/bash "$SRC/installer/install.sh" --no-brew --no-models --yes "$@"; }
inst --autostart --port "$PORT" > "$W/log/inst1.log" 2>&1
ck "install rc=0" test $? = 0
ck "app/panel.py" test -f "$P/app/panel.py"
ck "app bez data/" test ! -e "$P/app/data"
ck "yta.env YTA_DATA" has "$P/yta.env" "YTA_DATA=\"$P/data\""
ck "yta.env YTA_PORT" has "$P/yta.env" "YTA_PORT=\"$PORT\""
ck "yta.env YTA_APP_BUNDLE" has "$P/yta.env" "YTA_APP_BUNDLE=\"$APP\""
ck ".app postoji" test -d "$APP"
ck ".app bundle id" test "$(plutil -extract CFBundleIdentifier raw -o - "$APP/Contents/Info.plist")" = com.ytarhiva.app
ck ".app potpis (ad-hoc)" codesign --verify "$APP"
ck ".app stamp" test -s "$APP/Contents/Resources/yta-stamp"
osadecompile "$APP/Contents/Resources/Scripts/main.scpt" > "$W/log/applet.txt" 2>/dev/null
ck "applet pokreće launcher" has "$W/log/applet.txt" "$L"
ck "applet --from-app" has "$W/log/applet.txt" "--from-app"
ck "LaunchAgent plist -lint" plutil -lint "$LA"
ck "LaunchAgent → launcher" test "$(plutil -extract ProgramArguments.1 raw -o - "$LA")" = "$L"
ck "LaunchAgent --autostart" test "$(plutil -extract ProgramArguments.2 raw -o - "$LA")" = --autostart
ck "LaunchAgent RunAtLoad" test "$(plutil -extract RunAtLoad raw -o - "$LA")" = true

# ---------------------------------------------------------------- 6. drugi prolaz (idempotentno)
st_app=$(stat -f '%i %m' "$APP/Contents/Info.plist" "$APP/Contents/Resources/yta-stamp")
md_env=$(md5 -q "$P/yta.env"); md_la=$(md5 -q "$LA")
inst > "$W/log/inst2.log" 2>&1
ck "drugi prolaz rc=0" test $? = 0
ck "drugi prolaz: .app netaknuta" test "$(stat -f '%i %m' "$APP/Contents/Info.plist" "$APP/Contents/Resources/yta-stamp")" = "$st_app"
ck "drugi prolaz: yta.env isti" test "$(md5 -q "$P/yta.env")" = "$md_env"
ck "drugi prolaz: LaunchAgent isti" test "$(md5 -q "$LA")" = "$md_la"
ck "drugi prolaz: poruka .app" has "$W/log/inst2.log" "nepromenjena"
HOME="$H" YTA_LA_DIR="$H/Library/LaunchAgents" /bin/bash "$P/app/installer/install.sh" --no-brew --no-models --yes > "$W/log/inst3.log" 2>&1
ck "install iz instaliranog foldera" has "$W/log/inst3.log" "kopiranje preskočeno"

# ---------------------------------------------------------------- 7. launcher
env -i HOME="$H" YTA_NO_DIALOG=1 PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/bash "$L" --no-browser > "$W/log/l1.log" 2>&1
ck "launcher start (minimalno okruženje)" test $? = 0
ck "panel odgovara na $PORT" up "$PORT"
PID1=$(cat "$P/data/panel-$PORT.pid" 2>/dev/null)
env -i HOME="$H" YTA_NO_DIALOG=1 PATH=/usr/bin:/bin /bin/bash "$L" --no-browser > "$W/log/l2.log" 2>&1
ck "drugi start: isti pid" test "$(cat "$P/data/panel-$PORT.pid" 2>/dev/null)" = "$PID1"
HOME="$H" /bin/bash "$L" --status > "$W/log/l3.log" 2>&1
ck "--status radi" has "$W/log/l3.log" "radi: http://127.0.0.1:$PORT"
HOME="$H" /bin/bash "$L" --stop > "$W/log/l4.log" 2>&1
ck "--stop" not up "$PORT"
python3 -m http.server "$PORT2" --bind 127.0.0.1 >/dev/null 2>&1 & HTTP_PID=$!
sleep 1
HOME="$H" YTA_PORT=$PORT2 /bin/bash "$L" --no-browser > "$W/log/l5.log" 2>&1
ck "port zauzet drugim programom → rc 1" test $? = 1
HOME="$H" YTA_PORT=$PORT2 /bin/bash "$L" --from-app > "$W/log/l5b.log" 2>&1
ck "--from-app bez dijaloga: greška → rc 0" test $? = 0
ck "…poruka ide u log, ne na ekran" has "$P/data/panel.log" "launcher: Port $PORT2 je zauzet"
ck "…ništa na stderr" test ! -s "$W/log/l5b.log"
HOME="$H" YTA_PORT=$PORT2 YTA_NO_DIALOG=0 /bin/bash "$L" --from-app > "$W/log/l5c.log" 2>&1
ck "--from-app (korisnik): greška → rc 1 (applet prikazuje dijalog)" test $? = 1
kill "$HTTP_PID" 2>/dev/null; wait "$HTTP_PID" 2>/dev/null; HTTP_PID=""
ck "applet: dijalog samo bez YTA_NO_DIALOG" has "$W/log/applet.txt" 'system attribute "YTA_NO_DIALOG"'
HOME="$H" /bin/bash "$L" --no-browser > "$W/log/l6a.log" 2>&1
PID2=$(cat "$P/data/panel-$PORT.pid" 2>/dev/null)
HOME="$H" /bin/bash "$L" --restart --no-browser > "$W/log/l6.log" 2>&1
ck "--restart rc=0 (bez .app)" test $? = 0
ck "…panel radi posle --restart" up "$PORT"
ck "…novi pid" not test "$(cat "$P/data/panel-$PORT.pid" 2>/dev/null)" = "$PID2"
ck "…pokrenut direktno, ne preko .app" test "$(grep '=== .* start (' "$P/data/panel.log" | tail -n 1 | grep -c '(cli)')" = 1
HOME="$H" /bin/bash "$L" --stop > /dev/null 2>&1
ck "…--stop posle --restart" not up "$PORT"
ck ".app i dalje potpisana" codesign --verify --strict "$APP"

# ---------------------------------------------------------------- 8. uklanjanje
echo '{}' > "$P/data/settings.json"
ck "uninstall --purge bez terminala odbijen" not env HOME="$H" YTA_LA_DIR="$H/Library/LaunchAgents" /bin/bash "$P/app/installer/uninstall.sh" --purge
ck "…i ništa nije obrisao" test -f "$P/app/panel.py"
HOME="$H" YTA_LA_DIR="$H/Library/LaunchAgents" /bin/bash "$P/app/installer/uninstall.sh" > "$W/log/un.log" 2>&1
ck "uninstall rc=0" test $? = 0
ck "app/ uklonjen" test ! -e "$P/app"
ck ".app uklonjena" test ! -e "$APP"
ck "LaunchAgent uklonjen" test ! -e "$LA"
ck "podaci ostali" test -f "$P/data/settings.json"
ck "yta.env ostao" test -f "$P/yta.env"
inst > "$W/log/inst4.log" 2>&1
ck "ponovna instalacija posle uklanjanja" test -d "$APP"
ck "…zadržala port iz yta.env" has "$W/log/inst4.log" "127.0.0.1:$PORT"

echo
echo "prošlo: $PASS  palo: $FAIL"
[ "$FAIL" = 0 ] || { echo "palo:${FAILED//|/$'\n'  - }"; exit 1; }
