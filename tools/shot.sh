#!/usr/bin/env bash
# shot.sh — headless screenshot of nookboard (works around the profile lock
# by using a throwaway user-data-dir).
# usage: ./shot.sh out.png [url] [size]
set -euo pipefail
OUT="${1:-/tmp/nookboard-shot.png}"
URL="${2:-http://127.0.0.1:8765/}"
SIZE="${3:-1600,1000}"
PROFILE="$(mktemp -d /tmp/nookboard-chrome-XXXXXX)"
trap 'rm -rf "$PROFILE"' EXIT

chromium \
  --headless=new \
  --disable-gpu \
  --no-sandbox \
  --hide-scrollbars=false \
  --user-data-dir="$PROFILE" \
  --window-size="$SIZE" \
  --force-device-scale-factor=1 \
  --virtual-time-budget=4000 \
  --screenshot="$OUT" \
  "$URL" >/dev/null 2>&1 || true

if [ -f "$OUT" ]; then
  echo "wrote $OUT ($(stat -c%s "$OUT") bytes)"
else
  echo "SCREENSHOT FAILED" >&2
  exit 1
fi
