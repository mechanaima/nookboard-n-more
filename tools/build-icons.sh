#!/usr/bin/env bash
# The PWA icons, rendered from the two SVGs beside them.
#
# Generated rather than exported by hand once, for the same reason
# `build-lucide.py` exists: the sizes the manifest *claims* have to be the sizes
# that exist, and a hand-made export is the thing that quietly disagrees a year
# later. `tests/test_pwa.py` reads the PNG headers back and checks the claim.
#
#   ./tools/build-icons.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=static/icons
command -v rsvg-convert >/dev/null || {
  echo "!! rsvg-convert is not installed (librsvg)" >&2
  exit 1
}

rsvg-convert -w 192 -h 192 "$SRC/icon.svg"          -o "$SRC/icon-192.png"
rsvg-convert -w 512 -h 512 "$SRC/icon.svg"          -o "$SRC/icon-512.png"
rsvg-convert -w 512 -h 512 "$SRC/icon-maskable.svg" -o "$SRC/icon-maskable-512.png"

echo "wrote 3 icons into $SRC"
file "$SRC"/*.png
