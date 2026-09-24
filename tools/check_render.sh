#!/usr/bin/env bash
# check_render.sh — load nookboard in headless Chromium, dump the post-JS DOM,
# and assert the key components actually rendered. Catches JS exceptions that a
# syntax check cannot see (a thrown error leaves the panes blank).
#
# It seeds its own fixture notes through the API and deletes them afterwards,
# so it does not depend on whatever happens to be in the vault.
#
# Greps a file rather than a pipe: `printf ... | grep -q` exits early, SIGPIPEs
# the writer, and under `set -o pipefail` reports a failure for a real match.
#
# usage: ./tools/check_render.sh [base_url]
set -uo pipefail

BASE="${1:-http://127.0.0.1:8765}"
TARGET_ID="render-check-target"
NOTE_ID="render-check"

if ! curl -sf --max-time 3 "$BASE/api/health" >/dev/null; then
  echo "!! no server at $BASE — start it with 'make dev' first" >&2
  exit 1
fi

PROFILE="$(mktemp -d /tmp/nookboard-check-XXXXXX)"
DOM="$(mktemp /tmp/nookboard-dom-XXXXXX.html)"
cleanup() {
  curl -s -o /dev/null -X DELETE "$BASE/api/notes/$NOTE_ID" || true
  curl -s -o /dev/null -X DELETE "$BASE/api/notes/$TARGET_ID" || true
  rm -rf "$PROFILE" "$DOM"
}
trap cleanup EXIT

# --- seed ------------------------------------------------------------------
BODY='Soil mix:\n\n- **60 percent** potting mix\n- 30 percent perlite\n\n> dry out between waterings\n\nsee [[Render Check Target]] and `soil.md`'

curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$TARGET_ID\", \"collection\": \"inbox\", \"title\": \"Render Check Target\",
  \"body\": \"the link target\", \"signifier\": \"note\", \"status\": \"open\", \"dates\": []
}" || { echo "!! could not seed fixture" >&2; exit 1; }

curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$NOTE_ID\", \"collection\": \"inbox\", \"title\": \"Render Check\",
  \"body\": \"$BODY\", \"signifier\": \"task\", \"status\": \"open\",
  \"dates\": [\"2026-09-25\"], \"tags\": [\"fixture\"], \"mood\": \"good\"
}" || { echo "!! could not seed fixture" >&2; exit 1; }

# --- render ----------------------------------------------------------------
URL="$BASE/#/note/$NOTE_ID"
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

# sidebar + chrome
check "sidebar entries rendered"      'class="entry sig-'
check "rapid count badge"             'id="rapid-count"[^>]*>[0-9]+'
check "collection count badge"        'id="collection-count"[^>]*>[0-9]+'
check "collection row counts"         'class="row-count"'
check "active tab ink positioned"     'class="tab-ink ready" aria-hidden="true" style="left: [0-9]'
check "search box present"            'id="search-box"'

# editor
check "note title rendered"           'value="Render Check"|>Render Check<'
check "collection select populated"   'id="note-collection"'
check "tag chip input present"        'id="tag-chips"'
check "tag chip from fixture"         'tag-chip--removable[^>]*>fixture|>fixture<'
check "mood buttons present"          'data-mood="great"'
check "mood value label filled"       'id="mood-value"[^>]*>[^<]+<'

# editor panes + markdown pipeline
check "editor pane: markdown label"   'pane-tag">markdown'
check "editor pane: rendered label"   'pane-tag">rendered'
check "preview: bold rendered"        '<strong>60 percent</strong>'
check "preview: list rendered"        '<ul>'
check "preview: blockquote rendered"  '<blockquote>'
check "preview: wikilink resolved"    'class="wikilink exists"'
check "preview: inline code"          '<code>soil.md</code>'

# ai panel
check "ai panel present"              'id="ai-panel"'
check "ai action buttons present"     'id="ai-summarize"'
check "ai ask field present"          'id="ai-ask-input"'
check "ai thinking pane present"      'id="ai-thinking"'
check "ai model label filled"         'id="ai-model"[^>]*>[^<]*\('

# structural bits that must survive any redesign
check "empty-state CTA exists"        'id="new-note-btn"'
check "note close control exists"     'id="note-close"'
check "calendar legend present"       'class="cal-legend"'
check "backlinks block present"       'Linked from'

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
