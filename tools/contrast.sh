#!/usr/bin/env bash
# Measure the contrast of rendered text, at 4x, against the pixel it actually
# sits on.
#
#   ./tools/contrast.sh <url> <selector> [selector...]
#   ./tools/contrast.sh 'http://127.0.0.1:8765/#/view/board' .card__chip .card__meta
#
# Why 4x: at 1x a 10px monospace glyph is about one pixel of anti-aliasing, so
# the brightest pixel in a chip is a blend of glyph and fill and every number
# comes out low -- a chip that measures 6.5:1 reports 2.2:1. Scale it up and the
# glyph cores are real pixels.
#
# Two things to know when reading the output:
#   * AA needs 4.5:1 for normal text and 3:1 for large text.
#   * A selector that matches a *container* (a row of chips, a button) measures
#     the boundaries inside it, not the text. That is the non-text requirement,
#     which is 3:1. Pick the element that holds the text when you mean text.
set -euo pipefail

URL="${1:?usage: contrast.sh <url> <selector> [selector...]}"
shift
[ "$#" -gt 0 ] || { echo "contrast.sh: give at least one selector" >&2; exit 2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCALE=4
SIZE="1600,980"
SHOT="$(mktemp /tmp/nookboard-contrast-XXXXXX.png)"
PROFILE="$(mktemp -d /tmp/nookboard-contrast-XXXXXX)"
trap 'rm -f "$SHOT"; rm -rf "$PROFILE"' EXIT

CHROME="${CHROME:-$(command -v chromium || command -v chromium-browser || command -v google-chrome)}"
[ -n "$CHROME" ] || { echo "contrast.sh: no chromium found; set CHROME=" >&2; exit 2; }

"$CHROME" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
  --hide-scrollbars --force-device-scale-factor="$SCALE" \
  --user-data-dir="$PROFILE" --window-size="$SIZE" \
  --screenshot="$SHOT" --virtual-time-budget=4000 "$URL" >/dev/null 2>&1 || true
[ -s "$SHOT" ] || { echo "contrast.sh: screenshot failed" >&2; exit 1; }

SHOT="$SHOT" URL="$URL" SCALE="$SCALE" RECTS="$HERE/rects.mjs" python3 - "$@" <<'PY'
import json, os, subprocess, sys
from PIL import Image

shot, url, scale, rects_js = os.environ['SHOT'], os.environ['URL'], float(os.environ['SCALE']), os.environ['RECTS']
selectors = sys.argv[1:]

def lum(p):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = p
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

im = Image.open(shot).convert('RGB')
port = 9330
worst_overall = None
for sel in selectors:
    raw = subprocess.run(['node', rects_js, url, str(port), sel],
                         capture_output=True, text=True).stdout or '[]'
    port += 1
    try:
        found = json.loads(raw)
    except ValueError:
        found = []
    if not found:
        print(f'{sel:20s} (nothing matched on this view)')
        continue
    ratios = []
    for r in found[:10]:
        # inset 2px so the border is not what we average in, then 4x the rect
        box = (int((r['x'] + 2) * scale), int((r['y'] + 2) * scale),
               int((r['x'] + r['w'] - 2) * scale), int((r['y'] + r['h'] - 2) * scale))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        ls = sorted(lum(p) for p in im.crop(box).getdata())
        bg = ls[len(ls) // 2]            # the fill: most of the area
        fg = ls[int(len(ls) * 0.985)]    # the glyph core, not its fringe
        if fg < bg:
            fg, bg = bg, fg
        ratios.append((fg + 0.05) / (bg + 0.05))
    if not ratios:
        print(f'{sel:20s} (all matches too small to measure)')
        continue
    worst, avg = min(ratios), sum(ratios) / len(ratios)
    verdict = 'pass AA' if worst >= 4.5 else ('large text only' if worst >= 3.0 else 'FAIL')
    worst_overall = worst if worst_overall is None else min(worst_overall, worst)
    print(f'{sel:20s} n={len(ratios):2d}  worst {worst:5.2f}:1  avg {avg:5.2f}:1  -> {verdict}')

if worst_overall is not None and worst_overall < 4.5:
    print(f'\nworst on the page: {worst_overall:.2f}:1 (AA needs 4.5:1 for normal text)')
PY
