#!/usr/bin/env bash
# check_render.sh — load nookboard headless, dump the post-JS DOM, assert the
# key components actually rendered. Catches JS exceptions that a syntax check
# misses. Greps a file (never a pipe) so an early-exit SIGPIPE combined with
# pipefail cannot masquerade as a failure.
set -uo pipefail
URL="${1:-http://127.0.0.1:8765/#/note/n5}"
PROFILE="$(mktemp -d /tmp/nookboard-check-XXXXXX)"
DOM="$(mktemp /tmp/nookboard-dom-XXXXXX.html)"
cleanup() { rm -rf "$PROFILE" "$DOM"; }
trap cleanup EXIT

chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$URL" > "$DOM" 2>/dev/null

pass=0; fail=0
check() { # name, extended-regex
  if grep -qE -- "$2" "$DOM"; then
    echo "  ok   $1"; pass=$((pass+1))
  else
    echo "  FAIL $1"; fail=$((fail+1))
  fi
}

echo "rendering $URL  ($(wc -c < "$DOM") bytes of DOM)"
check "sidebar entries rendered"      'class="entry sig-'
check "rapid count badge"             'id="rapid-count"[^>]*>[0-9]+'
check "collection count badge"        'id="collection-count"[^>]*>[0-9]+'
check "collection row counts"         'class="row-count"'
check "mood buttons present"          'data-mood="great"'
check "mood value label filled"       'id="mood-value"[^>]*>[^<]+<'
check "active tab ink positioned"     'class="tab-ink ready" aria-hidden="true" style="left: [0-9]'
check "editor pane: markdown label"   'pane-tag">markdown'
check "editor pane: rendered label"   'pane-tag">rendered'
check "markdown source in textarea"   '&lt;strong&gt;60%&lt;/strong&gt;|<strong>60%</strong>'
check "preview: bold rendered"        '<strong>60%</strong>'
check "preview: list rendered"        '<ul>'
check "preview: blockquote rendered"  '<blockquote>'
check "preview: wikilink resolved"    'class="wikilink exists"'
check "preview: inline code"          '<code>notes/soil.md</code>'
check "tag chip input present"        'id="tag-chips"'
check "tag chips rendered"            'tag-chip--removable'
check "backlinks block present"       'Linked from'
check "empty-state CTA exists"        'id="new-note-btn"'
check "note close control exists"     'id="note-close"'
check "calendar legend present"       'class="cal-legend"'

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
