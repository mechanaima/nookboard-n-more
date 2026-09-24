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
MOOD_ID="render-check-mood"
WS_ID="render-check-workspace"
TODAY="$(date +%F)"

if ! curl -sf --max-time 3 "$BASE/api/health" >/dev/null; then
  echo "!! no server at $BASE — start it with 'make dev' first" >&2
  exit 1
fi

PROFILE="$(mktemp -d /tmp/nookboard-check-XXXXXX)"
DOM="$(mktemp /tmp/nookboard-dom-XXXXXX.html)"
BOARD_DOM="$(mktemp /tmp/nookboard-board-XXXXXX.html)"
MOOD_DOM="$(mktemp /tmp/nookboard-mood-XXXXXX.html)"
HOME_DOM="$(mktemp /tmp/nookboard-home-XXXXXX.html)"
TRANSCRIBE_DOM="$(mktemp /tmp/nookboard-transcribe-XXXXXX.html)"
WS_DOM="$(mktemp /tmp/nookboard-ws-XXXXXX.html)"
WS_NOTE_DOM="$(mktemp /tmp/nookboard-ws-note-XXXXXX.html)"
# A repo made for this check, because the assertions need a state that exists
# on no machine in particular: a real marker in a real comment, and one
# uncommitted file. Pointing at the checkout instead would make these depend on
# whether the person running the check happens to be mid-edit.
WS_DIR="$(mktemp -d /tmp/nookboard-wsrepo-XXXXXX)"
git -C "$WS_DIR" init -q -b main
git -C "$WS_DIR" config user.email "check@localhost"
git -C "$WS_DIR" config user.name "Render Check"
printf '# TODO: wire the tray icon\nprint(1)\n' > "$WS_DIR/main.py"
git -C "$WS_DIR" add -A
git -C "$WS_DIR" commit -qm "first"
printf 'still going\n' > "$WS_DIR/wip.py"
cleanup() {
  for id in "$NOTE_ID" "$TARGET_ID" "$BOARD_ID" "$BOARD_BLOCKER" "$MOOD_ID" "$WS_ID"; do
    curl -s -o /dev/null -X DELETE "$BASE/api/notes/$id" || true
  done
  rm -rf "$PROFILE" "$DOM" "$BOARD_DOM" "$MOOD_DOM" "$HOME_DOM" "$TRANSCRIBE_DOM" \
    "$WS_DOM" "$WS_NOTE_DOM" "$WS_DIR"
}
trap cleanup EXIT

# --- seed ------------------------------------------------------------------
# The fixture is dated 2026-09-25, so `this week` is the fixed ISO week
# 2026-09-21..27 whatever day this check happens to run: the query assertions
# depend on the note's own date, not on the clock, and not on the vault.
BODY='Soil mix:\n\n- **60 percent** potting mix\n- 30 percent perlite\n\n> dry out between waterings\n\nsee [[Render Check Target]] and `soil.md`\n\n## Days\n\n```nookboard\ndays this week\n```\n\n## Finished\n\n```nookboard\ncompleted this week\n```' 

curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$TARGET_ID\", \"collection\": \"inbox\", \"title\": \"Render Check Target\",
  \"body\": \"the link target\", \"signifier\": \"note\", \"status\": \"open\", \"dates\": []
}" || { echo "!! could not seed fixture" >&2; exit 1; }

curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$NOTE_ID\", \"collection\": \"inbox\", \"title\": \"Render Check\",
  \"body\": \"$BODY\", \"signifier\": \"task\", \"status\": \"open\",
  \"dates\": [\"2026-09-25\"], \"tags\": [\"fixture\"], \"mood\": \"good\",
  \"at\": \"09:15\", \"until\": \"10:30\"
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

# Mood fixture: dated today and tagged #mood, so it is the check-in note the
# view writes to — which is what makes the "today" row show an active pick and
# a pain reading.
#
# mood "bad" and pain 10 are the extremes on purpose: a day collapses to its
# worst mood and its highest pain, so nothing already in the vault can override
# these. A mid-range fixture would make the assertions depend on whatever else
# happens to be dated today.
curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$MOOD_ID\", \"collection\": \"inbox\", \"title\": \"Render Check Mood\",
  \"signifier\": \"note\", \"status\": \"open\", \"dates\": [\"$TODAY\"],
  \"tags\": [\"mood\"], \"mood\": \"bad\", \"pain\": 10
}" || { echo "!! could not seed mood fixture" >&2; exit 1; }

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

# Workspace fixture: a note pointing at the small repo built above. Reading a
# folder and opening a file in it are all this feature does, and the repo is a
# temp directory, so nothing here can touch anything that matters.
curl -sf -o /dev/null -X POST "$BASE/api/notes" -H 'content-type: application/json' -d "{
  \"id\": \"$WS_ID\", \"collection\": \"workspaces\",
  \"title\": \"Render Check Workspace\", \"signifier\": \"note\",
  \"status\": \"open\", \"path\": \"$WS_DIR\"
}" || { echo "!! could not seed workspace fixture" >&2; exit 1; }

# --- render 1: a note in the rapid log -------------------------------------
URL="$BASE/#/note/$NOTE_ID"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$URL" > "$DOM" 2>/dev/null

check() { check_file "$DOM" "$1" "$2"; }
check_gone() { check_absent "$DOM" "$1" "$2"; }

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

# the time row: the input a person sets, and the line that says what it will do
check "time starts-at input present"  'id="note-at"[^>]*type="time"'
check "time ends-at input present"    'id="note-until"[^>]*type="time"'
check "time clear button present"     'id="note-time-clear"'
check "time hint says when it fires"  'id="note-time-hint"[^>]*>fires at 09:15<'

# editor panes + markdown pipeline
check "editor pane: markdown label"   'pane-tag">markdown'
check "editor pane: rendered label"   'pane-tag">rendered'
check "preview: bold rendered"        '<strong>60 percent</strong>'
check "preview: list rendered"        '<ul>'
check "preview: blockquote rendered"  '<blockquote>'
check "preview: wikilink resolved"    'class="wikilink exists"'
check "preview: query resolved to a linked day" 'class="wikilink[^"]*"[^>]*>2026-09-21</a>'
check_gone "preview: query not left as a code block" 'language-nookboard'
check "preview: inline code"          '<code>soil.md</code>'

# ai panel
check "ai panel present"              'id="ai-panel"'
check "day recap button present"      'id="ai-day-recap"'
check "week recap button present"     'id="ai-week-recap"'
check "template picker present"      'id="template-pick"'
check "template picker is labelled"  'class="template-label"'
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
# The time rides on the card's meta row beside the date, so a card you scan on
# the board says when the thing is -- not just which day.
check_board "a time chip is on the card"   'card__chip--time"[^>]*>09:15[^<]*10:30<'
check_board "blocker card rendered"        'data-id="render-check-blocker"'
check_board "card move controls rendered"  'class="card__bitem'
check_board "board filters rendered"       'id="board-blocked-only"'
check_board "layout in wide mode"           'class="layout is-wide"'
# The regression guard for "clicking a card opened nothing": when a note is
# open the layout must NOT be in its empty state, or CSS hides the editor.
check_board_absent "layout not in empty state" 'layout is-wide is-wide-empty'
check_board "blocked card badge rendered"  'class="card__blocked"'
check_board "lock glyph on blocked card"   '🔒'
check_board "dependency chip rendered"     'class="dep-chip'
check_board "blocked state on the note"    'deps-state--blocked'

# --- render 3: the mood view, with today logged ----------------------------
# Same layout invariant as the board (a wide view must re-grid on load), plus
# the pieces the mood view is actually made of.
MOOD_URL="$BASE/#/view/mood"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$MOOD_URL" > "$MOOD_DOM" 2>/dev/null

check_mood() { check_file "$MOOD_DOM" "$1" "$2"; }
check_mood_absent() { check_absent "$MOOD_DOM" "$1" "$2"; }
check_count() { # dom-file, name, extended-regex, expected-hits
  local got
  got="$(grep -oE -- "$3" "$1" | wc -l)"
  if [ "$got" -eq "$4" ]; then
    echo "  ok   $2 ($got)"; pass=$((pass+1))
  else
    echo "  FAIL $2 (expected $4, got $got)"; fail=$((fail+1))
  fi
}

echo "rendering $MOOD_URL  ($(wc -c < "$MOOD_DOM") bytes of DOM)"

check_mood "mood tab marked active"        'data-view="mood"[^>]*class="tab active"|class="tab active"[^>]*data-view="mood"'
check_mood "mood view rendered"            'id="mood-view" class="mood-view"'
check_mood "layout in wide mode"           'class="layout is-wide'
# No note is open here, so the editor must be collapsed rather than showing an
# empty pane — the same layout machinery the board uses.
check_mood "editor collapsed with no note" 'class="layout is-wide is-wide-empty"'
check_mood "summary chips rendered"        'class="mood-stat-value"'
check_mood "logged-today pick is active"   'class="mood-pick active" data-mood="bad"'
check_mood "pain reading shown as 10/10"   'id="mood-log-pain-out"[^>]*>10/10<'
check_count "$MOOD_DOM" "five mood picks offered" 'class="mood-pick' 5
# Legend swatches are the only <i> in the view; the distribution bars are spans.
# `title` serializes before `style`, so match on the pair rather than on `<i style=`.
check_count "$MOOD_DOM" "five legend swatches" '<i title="[a-z]+" style="background: var\(--mood-' 5
check_count "$MOOD_DOM" "distribution rows"    'class="mood-dist-level"' 5
check_mood "heatmap grid rendered"         'class="mood-grid"'
check_mood "heatmap cells rendered"        'class="mood-cell'
check_mood "logged cell painted as bad"    'class="mood-cell mood-cell--bad'
check_mood "high-pain day is ringed"       'mood-cell--pain'
check_mood "month axis rendered"           'class="mood-months"'
check_mood "weekday labels rendered"       'class="mood-daycol"'
check_mood "recent days panel filled"      'class="mood-recent-date"|class="mood-empty"'
check_mood "scroll affordance present"     'id="mood-scroll-hint"'

# The insight panel joins two features (finished work and pain), so it has two
# legitimate states and the assertions must hold in both: bands when there are
# enough paired days, the "not enough yet" sentence when there are not. The
# caveat is the invariant worth pinning -- it is what stops the sentence being
# read as a diagnosis, so it must never be the thing that quietly goes missing.
check_mood "insight reading present"        'id="insight-reading"'
check_mood "insight caveat refuses cause"   'not a cause'
check_mood "insight says something"         'class="insight-band"|class="insight-empty"|Not enough to go on'

# --- render 4: the dashboard ----------------------------------------------
# Loaded from a bare URL on purpose: Home is the view the app opens on, so this
# doubles as the assertion that the default view still renders without a hash.
# Every card is painted from /api/home after boot, so these checks are really
# asking whether that fetch landed -- a throw inside renderHome() would leave
# the placeholder dashes in place, which is what the two _absent checks pin.
HOME_URL="$BASE/"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$HOME_URL" > "$HOME_DOM" 2>/dev/null

check_home() { check_file "$HOME_DOM" "$1" "$2"; }
check_home_absent() { check_absent "$HOME_DOM" "$1" "$2"; }

echo "rendering $HOME_URL  ($(wc -c < "$HOME_DOM") bytes of DOM)"

check_home "bare URL lands on the dashboard" 'id="home-view" class="home-view"'
check_home "home tab marked active"       'data-view="home"[^>]*class="tab active"|class="tab active"[^>]*data-view="home"'
check_home "layout in wide mode"          'class="layout is-wide'
check_home "editor collapsed with no note" 'class="layout is-wide is-wide-empty"'
check_home "vault named in the title"     'id="home-vault"[^>]*>[^<]+<'
check_home "clock painted"                'id="home-time"[^>]*>[0-9][0-9]:[0-9][0-9]<'
check_home "greeting painted"             'id="home-greeting"[^>]*>(Good morning|Good afternoon|Good evening|Still up)<'
check_home "long date painted"            'id="home-date"[^>]*>[A-Z][a-z]+ [0-9]+ [A-Z][a-z]+<'
check_home "month label filled"           'id="home-month"[^>]*>[A-Z][a-z]+ [0-9]{4}<'
check_home "mini calendar weekdays"       'class="mini-cal-head"'
check_home "mini calendar days"           'class="mini-cal-day'
check_home "today ringed in the grid"     'class="mini-cal-day[^"]*is-today'
check_home "mood streak painted"          'id="home-streak"[^>]*>[0-9]+<'
check_home "today line painted"           'id="home-today-line"[^>]*>[^<]+<'
check_home "today action labelled"        'id="home-today-action"[^>]*>[^<]+<'
check_home "entry actions offered"        'data-entry="task"'
check_home "view jumps offered"           'data-jump="collections"'
check_home "stats card describes the vault" 'id="home-stats"'
# Four vault tiles plus four board tiles; the two containers hold the same class.
check_count "$HOME_DOM" "eight tiles across two cards" 'class="h-tile"' 8
check_home_absent "clock not left as a placeholder" 'id="home-time"[^>]*>—<'
check_home_absent "dashboard not left hidden"       'id="home-view" class="home-view hidden"'

# --- render 5: the transcribe view ----------------------------------------
# The engine line and the job list are painted from /api/transcribe after boot,
# so these are really asking whether that fetch landed and whether the form was
# built from it: a throw inside renderTranscribe() leaves the placeholder line
# and two empty selects, which is what the _absent checks pin.
TRANSCRIBE_URL="$BASE/#/view/transcribe"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$TRANSCRIBE_URL" > "$TRANSCRIBE_DOM" 2>/dev/null

check_tr() { check_file "$TRANSCRIBE_DOM" "$1" "$2"; }
check_tr_absent() { check_absent "$TRANSCRIBE_DOM" "$1" "$2"; }

echo "rendering $TRANSCRIBE_URL  ($(wc -c < "$TRANSCRIBE_DOM") bytes of DOM)"

check_tr "transcribe tab marked active"   'data-view="transcribe"[^>]*class="tab active"|class="tab active"[^>]*data-view="transcribe"'
check_tr "view shown, not hidden"         'id="transcribe-view" class="transcribe-view"'
check_tr "layout in wide mode"            'class="layout is-wide'
check_tr "engine line filled"             'id="transcribe-engine"[^>]*>[^<]+<'
check_tr "a path can be typed"            'id="transcribe-path"'
check_tr "a file can be picked"           'id="transcribe-file"'
check_tr "the microphone is offered"      'id="transcribe-record"'
check_tr "a start action is present"      'id="transcribe-start"'
check_tr "the summary can be declined"    'id="transcribe-summarize"'
check_tr "the job list exists"            'id="transcribe-list"'
check_tr "model choices offered"          '<option value="small"|<option value="medium"'
check_tr "transcripts is offered as a collection" '<option value="transcripts"'
check_tr "the empty state shows with no jobs" 'id="transcribe-empty" class="tr-empty"'
check_tr "what happens to the file is stated" 'Nothing leaves this machine'
check_tr_absent "engine line not the placeholder" 'id="transcribe-engine"[^>]*>asking what is installed'
check_tr_absent "no error shown before anything is tried" 'id="transcribe-error" class="tr-error"'
check_tr_absent "the empty state is not left under a job" 'id="transcribe-list"><li'

# --- render 6: the workspaces view -----------------------------------------
# The cards are painted from /api/workspaces after boot, so these are really
# asking whether that fetch landed and whether the cards were built from it: a
# throw inside renderWorkspaces() leaves the hero's placeholder line and an empty
# grid, which is what the two _absent checks pin.
#
# The fixture points at this checkout, so the assertions hold on any machine and
# in any vault: a real repo, a real branch, real files, real openers resolved.
WS_URL="$BASE/#/view/workspaces"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=5000 \
  --dump-dom "$WS_URL" > "$WS_DOM" 2>/dev/null

check_ws() { check_file "$WS_DOM" "$1" "$2"; }
check_ws_absent() { check_absent "$WS_DOM" "$1" "$2"; }

echo "rendering $WS_URL  ($(wc -c < "$WS_DOM") bytes of DOM)"

check_ws "workspaces tab marked active"  'data-view="workspaces"[^>]*class="tab active"|class="tab active"[^>]*data-view="workspaces"'
check_ws "view shown, not hidden"        'id="workspaces-view" class="workspaces-view"'
check_ws "layout in wide mode"           'class="layout is-wide'
check_ws "editor collapsed with no note" 'class="layout is-wide is-wide-empty"'
check_ws "a card was built"              'class="ws-card '
check_ws "the card names the note"       'class="ws-card__title"[^>]*>Render Check Workspace<'
# The folder is the checkout, so it is a repo and must say so -- and the card has
# to carry a branch line, which only a successful git read produces.
check_ws "the folder is called a repo"   'class="ws-card__kind"[^>]*>repo<'
check_ws "the branch is named"           'class="ws-card__branch"[^>]*>[^<]+<'
check_ws "a state line was written"      'class="ws-card__state"[^>]*>[^<]+<'
check_ws "the openers are offered"       'class="ws-open-row"'
# Three openers per card, whatever the vault holds: this check runs against the
# real vault too, so the count has to come from the cards themselves.
ws_cards="$(grep -oE 'class="ws-card ' "$WS_DOM" | wc -l)"
check_count "$WS_DOM" "three openers on every card" 'class="ws-open"' $((ws_cards * 3))
# The marker and the uncommitted file are the two places the fixture guarantees,
# and a place is a button -- that is the whole point of the marker list.
check_ws "the marker is listed with its place" 'main.py:1'
check_ws "the marker keeps the words after it" 'TODO: wire the tray icon|TODO wire the tray icon'
check_ws "the uncommitted file is listed" 'wip.py'
check_ws "a place is a button, not a line of text" 'class="ws-place"'
check_count "$WS_DOM" "one marker and one file are openable" 'class="ws-place"' 2
check_ws_absent "the hero line is not left as the placeholder" 'reading the folders'
check_ws_absent "no card claims no workspaces" 'no workspaces yet<'
check_ws_absent "the empty state is not left under a card" 'id="workspaces-empty" class="empty-state"'

# --- render 7: the folder row in the editor --------------------------------
# The panel is painted from /api/workspaces/{id} after the note loads, so the
# facts below are the fetch landing, not the markup existing: the field is
# `hidden` until the read answers, and the hint is the placeholder until then.
WS_NOTE_URL="$BASE/#/note/$WS_ID"
chromium --headless=new --disable-gpu --no-sandbox \
  --user-data-dir="$PROFILE" --virtual-time-budget=6000 \
  --dump-dom "$WS_NOTE_URL" > "$WS_NOTE_DOM" 2>/dev/null

check_wsnote() { check_file "$WS_NOTE_DOM" "$1" "$2"; }
check_wsnote_absent() { check_absent "$WS_NOTE_DOM" "$1" "$2"; }

echo "rendering $WS_NOTE_URL  ($(wc -c < "$WS_NOTE_DOM") bytes of DOM)"

check_wsnote "the folder row is on the form" 'id="note-path"'
check_wsnote "the row can be cleared"        'id="note-path-clear"'
# The panel is unhidden only once the read answers, so this is the fetch landing:
# `hidden` removed and nothing left in its place is exactly what `>` after the id
# proves. (The id sits after the class in the markup, hence this shape.)
check_wsnote "the panel was opened"          'class="field field--wide" id="workspace-panel-field">'
check_wsnote "the branch fact is listed"     'class="ws-fact__key">branch<'
check_wsnote "the folder fact names it"      'class="ws-fact__value">[^<]*nookboard-wsrepo-[^<]*<'
check_wsnote "the openers are offered here"  'class="ws-open-row"'
check_wsnote "a place is a button here too"  'class="ws-place"'
check_count "$WS_NOTE_DOM" "one marker and one file here as well" 'class="ws-place"' 2
check_wsnote_absent "the hint is not still asking for a path" 'point this at a folder'
check_wsnote_absent "the panel is not left hidden" 'id="workspace-panel-field" class="field field--wide" hidden'

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
