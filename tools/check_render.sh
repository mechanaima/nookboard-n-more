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
BOARD_ID="render-check-blocked"
BOARD_BLOCKER="render-check-blocker"

if ! curl -sf --max-time 3 "$BASE/api/health" >/dev/null; then
  echo "!! no server at $BASE — start it with 'make dev' first" >&2
  exit 1
fi

PROFILE="$(mktemp -d /tmp/nookboard-check-XXXXXX)"
DOM="$(mktemp /tmp/nookboard-dom-XXXXXX.html)"
BOARD_DOM="$(mktemp /tmp/nookboard-board-XXXXXX.html)"
cleanup() {
  for id in "$NOTE_ID" "$TARGET_ID" "$BOARD_ID" "$BOARD_BLOCKER"; do
    curl -s -o /dev/null -X DELETE "$BASE/api/notes/$id" || true
  done
  rm -rf "$PROFILE" "$DOM" "$BOARD_DOM"
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

# Board fixtures: one open blocker, one task waiting on it, one in Doing.
curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$BOARD_BLOCKER\", \"collection\": \"inbox\", \"title\": \"Render Check Blocker\",
  \"signifier\": \"task\", \"status\": \"open\"
}" || { echo "!! could not seed board fixture" >&2; exit 1; }

curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$BOARD_ID\", \"collection\": \"inbox\", \"title\": \"Render Check Blocked\",
  \"signifier\": \"task\", \"status\": \"open\", \"stage\": \"doing\",
  \"blocked_by\": [\"$BOARD_BLOCKER\"]
}" || { echo "!! could not seed board fixture" >&2; exit 1; }

pass=0; fail=0
check_file() { # dom-file, name, extended-regex
  if grep -qE -- "$3" "$1"; then
    echo "  ok   $2"; pass=$((pass+1))
  else
    echo "  FAIL $2"; fail=$((fail+1))
  fi
}
# Some assertions are about something NOT happening (a pane we expect to be
# visible, a class that must be absent). Absence needs its own check — an
# inverted regex would silently pass while claiming the opposite.
check_absent() { # dom-file, name, extended-regex
  if grep -qE -- "$3" "$1"; then
    echo "  FAIL $2 (present, should be absent)"; fail=$((fail+1))
  else
    echo "  ok   $2"; pass=$((pass+1))
  fi
}

# --- render 1: a note in the rapid log -------------------------------------
URL="$BASE/#/note/$NOTE_ID"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$URL" > "$DOM" 2>/dev/null

check() { check_file "$DOM" "$1" "$2"; }

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

# dependency panel (editor side)
check "deps panel present"            'id="deps-field"'
check "deps blocked-by list present"  'id="blocked-by-list"'
check "deps picker present"           'id="dep-input"'
check "deps blocking section present" 'id="deps-blocking"'
check "deps state badge filled"       'id="deps-state"[^>]*>[^<]+<'
check "board stage select present"    'id="note-stage"'

# --- render 2: the board with a card open in the editor --------------------
# Loading the board with a note open is the case that used to leave the layout
# in its "no note" mode, so the editor stayed display:none. Assert the layout
# class is board mode AND not the empty variant.
BURL="$BASE/#/view/board/note/$BOARD_ID"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$BURL" > "$BOARD_DOM" 2>/dev/null

check_board() { check_file "$BOARD_DOM" "$1" "$2"; }
check_board_absent() { check_absent "$BOARD_DOM" "$1" "$2"; }

echo "rendering $BURL  ($(wc -c < "$BOARD_DOM") bytes of DOM)"

check_board "board tab marked active"      'data-view="board"[^>]*class="tab active"|class="tab active"[^>]*data-view="board"'
check_board "board view rendered"          'id="board-view" class="board-view"'
check_board "board summary filled"         'id="board-summary"[^>]*>[^<]+<'
check_board "all five columns rendered"    'data-stage="backlog"'
check_board "todo column rendered"         'data-stage="todo"'
check_board "doing column rendered"        'data-stage="doing"'
check_board "review column rendered"       'data-stage="review"'
check_board "done column rendered"         'data-stage="done"'
check_board "column headers rendered"      'class="board-col__head"'
check_board "fixture card rendered"        'data-id="render-check-blocked"'
check_board "blocker card rendered"        'data-id="render-check-blocker"'
check_board "card move controls rendered"  'class="card__bitem'
check_board "board filters rendered"       'id="board-blocked-only"'
check_board "layout in board mode"         'class="layout is-board"'
# The regression guard for "clicking a card opened nothing": when a note is
# open the layout must NOT be in its empty-board state, or CSS hides the editor.
check_board_absent "layout not in empty-board mode" 'layout is-board is-board-empty'
check_board "blocked card badge rendered"  'class="card__blocked"'
check_board "lock glyph on blocked card"   '🔒'
check_board "dependency chip rendered"     'class="dep-chip'
check_board "blocked state on the note"    'deps-state--blocked'

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
