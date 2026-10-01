#!/bin/bash
# YT Arhiva — pravljenje paketa za distribuciju.
#
#   installer/build.sh [--with-tests] [--no-pkg] [--no-pdf] [--out DIR] [--pkg-location DIR]
#
#   dist/yt-arhiva-<ver>.tar.gz (+ .sha256)   raspakuj i pokreni installer/install.sh
#   dist/yt-arhiva-<ver>.pkg                  ako postoji pkgbuild; kod ide u <pkg-location>/app
#                                             (podrazumevano "/Applications/YT Arhiva"), posle toga
#                                             "/Applications/YT Arhiva/app/installer/install.sh" pravi .app
#   dist/YT-Arhiva-Uputstvo-<ver>.pdf         static/uputstvo.html kao PDF, ako postoji Google Chrome
# Verzija je u fajlu VERSION.
set -euo pipefail

SELF_DIR=$(cd "$(dirname "$0")" && pwd -P)
# shellcheck source-path=SCRIPTDIR source=common.sh
. "$SELF_DIR/common.sh"
ROOT=$(cd "$SELF_DIR/.." && pwd -P)

WITH_TESTS=0
PKG=1
PDF=1
OUT="$ROOT/dist"
PKG_LOC="/Applications/$YTA_NAME"
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }
while [ $# -gt 0 ]; do
  case $1 in
    --with-tests) WITH_TESTS=1 ;;
    --no-pkg) PKG=0 ;;
    --no-pdf) PDF=0 ;;
    --out) [ $# -ge 2 ] || die "--out traži vrednost"; OUT=$2; shift ;;
    --pkg-location) [ $# -ge 2 ] || die "--pkg-location traži vrednost"; PKG_LOC=$2; shift ;;
    -h|--help) usage ;;
    *) die "nepoznata opcija: $1" ;;
  esac
  shift
done

[ -f "$ROOT/VERSION" ] || die "nema VERSION fajla u $ROOT"
VER=$(tr -d ' \n' < "$ROOT/VERSION")
case $VER in ''|*[!0-9A-Za-z.+-]*) die "neispravna verzija u VERSION: '$VER'" ;; esac
NAME="yt-arhiva-$VER"
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd -P)

step "Build $NAME"
for f in panel.py yt_arhiva.py memory.py toolbox.py static docs installer README.md LICENSE VERSION; do
  [ -e "$ROOT/$f" ] || die "nedostaje $f"
done
for s in "$ROOT"/installer/*.sh; do bash -n "$s" || die "sintaksna greška: $s"; done
ok "sintaksa skripti"
if command -v python3 >/dev/null 2>&1; then
  python3 -c 'import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]' \
    "$ROOT/panel.py" "$ROOT/yt_arhiva.py" "$ROOT/memory.py" "$ROOT/toolbox.py" || die "Python sintaksa"
  ok "Python sintaksa"
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/yta-build.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/$NAME"
mkdir -p "$STAGE"
ITEMS="panel.py yt_arhiva.py memory.py toolbox.py static docs installer README.md LICENSE SECURITY.md runbook.md VERSION"
[ "$WITH_TESTS" = 1 ] && [ -d "$ROOT/tests" ] && ITEMS="$ITEMS tests"
# shellcheck disable=SC2086
(cd "$ROOT" && rsync -a --exclude '__pycache__/' --exclude '.DS_Store' --exclude 'data/' $ITEMS "$STAGE/")
chmod +x "$STAGE/installer/"*.sh
# karantin i ostali xattr ne idu u paket (com.apple.provenance se ne skida sa fajla, pa ga tar izostavlja: --no-xattrs)
xattr -cr "$STAGE" 2>/dev/null || true
ok "sadržaj: $ITEMS"

TGZ="$OUT/$NAME.tar.gz"
rm -f "$TGZ" "$TGZ.sha256"
# vlasnik fajlova u arhivi je root/wheel (ime lokalnog korisnika ne ide u paket)
COPYFILE_DISABLE=1 tar --no-xattrs --uid 0 --gid 0 --uname root --gname wheel -czf "$TGZ" -C "$TMP" "$NAME"
(cd "$OUT" && shasum -a 256 "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
ok "$TGZ ($(du -h "$TGZ" | awk '{print $1}'))"

if [ "$PKG" = 1 ]; then
  if command -v pkgbuild >/dev/null 2>&1; then
    PKGROOT="$TMP/pkgroot"
    mkdir -p "$PKGROOT"
    cp -R "$STAGE" "$PKGROOT/app"
    PKGF="$OUT/$NAME.pkg"
    rm -f "$PKGF"
    COPYFILE_DISABLE=1 pkgbuild --quiet --root "$PKGROOT" --install-location "$PKG_LOC" \
      --identifier "$YTA_BUNDLE_ID.pkg" --version "$VER" "$PKGF"
    pkgutil --payload-files "$PKGF" > "$TMP/payload.txt"
    grep -q '^\./app/panel\.py$' "$TMP/payload.txt" || die "pkg nema app/panel.py"
    ok "$PKGF → $PKG_LOC/app"
    info "posle instalacije: $(qcmd "$PKG_LOC/app/installer/install.sh") --prefix $(qcmd "$PKG_LOC")"
  else
    info "pkgbuild nije dostupan — .pkg preskočen"
  fi
fi

# ---------------------------------------------------------------- uputstvo kao PDF (Chrome headless)
CHROME=${YTA_CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
if [ "$PDF" = 1 ]; then
  DOC="$STAGE/static/uputstvo.html"
  PDFF="$OUT/YT-Arhiva-Uputstvo-$VER.pdf"
  if [ ! -f "$DOC" ]; then
    info "nema static/uputstvo.html — PDF uputstva preskočen"
  elif [ ! -x "$CHROME" ]; then
    info "Google Chrome nije pronađen — PDF uputstva preskočen (uputstvo je u paketu: static/uputstvo.html)"
  else
    rm -f "$PDFF"
    # file:// URL: razmak, %, # i ? u putanji moraju biti kodirani
    URL="file://$(printf '%s' "$DOC" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/?/%3F/g')"
    "$CHROME" --headless=new --disable-gpu --no-first-run --no-default-browser-check --disable-extensions \
      --disable-background-networking --user-data-dir="$TMP/chrome-pdf" --no-pdf-header-footer \
      --print-to-pdf="$PDFF" "$URL" >/dev/null 2>&1 &
    CPID=$!
    # headless Chrome ume da ne izadje sam posle stampe: ceka se ceo PDF (%%EOF), pa se gasi (najvise 120 s)
    for _ in $(seq 1 240); do
      kill -0 "$CPID" 2>/dev/null || break
      if [ -s "$PDFF" ] && tail -c 64 "$PDFF" | grep -q '%%EOF'; then sleep 0.5; break; fi
      sleep 0.5
    done
    kill "$CPID" 2>/dev/null || true
    wait "$CPID" 2>/dev/null || true
    if [ -s "$PDFF" ] && [ "$(head -c 4 "$PDFF")" = "%PDF" ]; then
      ok "$PDFF ($(du -h "$PDFF" | awk '{print $1}'))"
    else
      rm -f "$PDFF"
      warn "PDF uputstva nije napravljen (Chrome) — paket je gotov i bez njega"
    fi
  fi
fi
