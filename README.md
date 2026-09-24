# nookboard

A local-first, file-backed personal notes / tasks / journal server.
Combines the workflow primitives of three iOS apps into one web UI:

- **NeatNook** — curated collections of long-form notes
- **Agenda** — date-tagged entries that surface on a timeline
- **BuJo** — rapid-log bullets (task •, event ○, note –) with migration

Single user, no auth, localhost-only. Plain Markdown + YAML frontmatter
on disk; the server is a thin index over the files.

## Run

```bash
make dev    # http://127.0.0.1:8765
make test   # pytest
make test-js
```

## Keyboard & deep links

| key | action |
|---|---|
| `/` | focus search |
| `n` | new note |
| `Esc` | close the open note (or blur search) |
| `Enter` / `,` | commit a tag in the tag field |
| `Backspace` | in an empty tag field, remove the last tag |

Deep links (shareable, and they survive reload):

```
#/                          rapid log
#/view/board                kanban board
#/view/mood                 mood & pain heatmap
#/view/calendar             calendar tab
#/note/<id>                 a specific note
#/view/timeline/note/<id>   a note with the timeline tab selected
#/view/board/note/<id>      the board with a card open in the editor
```

## Design

Dark-first, built on **Catppuccin Mocha** with a restrained vaporwave accent
(a pink → mauve → cyan gradient used sparingly: wordmark, primary button,
active-tab underline).

The whole visual layer is one hand-written stylesheet —
`static/css/app.css`, ~13 numbered sections (tokens → reset → typography →
topbar → sidebar → entries → editor → controls → markdown → calendar →
motion → responsive). There is **no framework, no build step, and no CDN**;
`marked` is vendored into `static/vendor/`.

Design decisions worth knowing before you edit it:

- **Every colour comes from a token** in section 1. Components use
  `color-mix()` against those tokens rather than literal hex, so re-theming
  means editing the token block and nothing else.
- **Motion is deliberate and respects `prefers-reduced-motion`.** List rows
  rise in with a stagger driven by a `--i` custom property; completing a task
  fires a pop + burst; the tab indicator is a measured sliding pill
  (`moveInk()` in `app.js` recomputes it on render, resize and font load).
- **Specificity gotcha:** form controls are styled via
  `input:not([type="checkbox"])` (specificity 0,1,1). To override it for a
  single field you need at least a two-class selector — see
  `.sheet-head input.doc-title`.
- **A wide view re-grids the page.** `.layout.is-wide` swaps the sidebar for the
  view and keeps the editor as a second column; `.is-wide-empty` collapses the
  editor when no note is open. Both classes are derived from `activeView` by
  `syncLayoutMode()` — which `render()` and `openEditor()` both call, because a
  click that only sets `activeId` would leave the layout in its empty mode and
  CSS would silently hide the editor. Adding a wide view means adding it to
  `WIDE_VIEWS`; nothing else has to remember.

### Visual verification

The UI is checked headlessly, not by eyeball alone:

```bash
./tools/check_render.sh 'http://127.0.0.1:8765/#/note/<id>'   # 53 DOM assertions
./tools/shot.sh /tmp/shot.png 'http://127.0.0.1:8765/'        # screenshot
```

`check_render.sh` loads the page in headless Chromium, dumps the post-JS DOM
and asserts that entries, counts, the tab ink, both editor panes, the rendered
markdown, tag chips, backlinks, the board's five columns and cards, the mood
heatmap and its logging row, and the calendar legend all actually rendered — so
a JS exception fails the check instead of silently producing a blank pane.

It makes **three** passes, and the latter two matter most: the board loads
`#/view/board/note/<id>` and asserts the layout is in wide mode *and not* in its
empty state (the regression guard for "clicking a card opened nothing"), and the
mood view loads `#/view/mood` and asserts the opposite — wide mode *and* empty,
because no note is open there.

Fixtures use the extremes of the scale (`mood: bad`, `pain: 10`) on purpose: a
day collapses to its worst mood and highest pain, so nothing already in the vault
can override them and make the assertions depend on the user's own data.

`shot.sh` uses a throwaway `--user-data-dir`, which sidesteps the profile lock
that blocks screenshotting while a normal browser session is open.

## Rapid-log syntax

Type into the rapid-log box:

| prefix | meaning | BuJo symbol |
|---|---|---|
| `•` or `*` | task | • |
| `○` or `o ` | event | ○ |
| `–` or `-` | note  | – |
| (none) | note | – |

Status is updated in the editor pane: `open`, `complete`, `migrated`,
`scheduled`, `irrelevant`.

## Features

- **Rapid Log** — type `• task`, `○ event`, `– note` and hit Enter
- **Board** — a kanban view with real dependencies: columns, drag or tap to
  move, blocked cards, cycle-safe blockers (see [Task management](#task-management))
- **Mood & pain** — a year-at-a-glance heatmap with one-tap logging, streaks and
  a distribution (see [Mood & pain](#mood--pain))
- **Daily notes** — an end-of-day recap of what you actually finished, written
  into that day's own note (see [Daily notes](#daily-notes))
- **Collections** — NeatNook-style curation, create + filter
- **Timeline** — Agenda-style date-filter view
- **Calendar** — month grid with per-day counts, click-through
- **Search** — topbar search box, live dropdown
- **Markdown preview** — split / write / preview tabs, live render
- **Mood tags** — emoji picker on every note
- **Migration** — task states: open → complete / migrated / scheduled / irrelevant
- **Tags** — multi-select, filter by tag (`?tag=foo`), chips in rapid-list
- **Wikilinks** — `[[Title]]` syntax, click-through in preview, missing/resolved styling
- **Backlinks** — every note shows a panel of notes that link to it
- **Recurring notes** — daily/weekly/monthly cadence, instances auto-created on startup
- **Vault export** — `GET /api/export.zip` — single zip of all `.md` files
- **ICS subscription** — `GET /api/calendar.ics` — external calendar apps subscribe
- **Obsidian interop** — the vault is a valid Obsidian vault; point nookboard at
  any Markdown folder with `NOOKBOARD_VAULT` (see [Obsidian](#obsidian))
- **Local AI** — summarize / suggest tags / suggest links / ask your notes,
  streamed from llama.cpp (see [Local AI](#local-ai))

## Task management

The **Board** tab is a kanban over the same notes as everything else — not a
separate database. A card *is* a note; moving it edits the Markdown.

Columns are Backlog → To do → Doing → Review → Done. Drag a card, or use the
**‹ ›** buttons on it (a drag is fine-motor work and invisible to a keyboard, so
every drag has a one-tap equivalent), or the **✓ / ↺** button to complete and
reopen.

### Dependencies and blockers

`blocked_by:` in a note's frontmatter lists the note ids it waits on:

```yaml
blocked_by: [outline, wire-api]
stage: doing
position: 2.0
```

Design decisions worth knowing before you edit any of it:

- **One stored direction.** Only `blocked_by` is written. "What does this
  block?" is the reverse edge, computed on read, so the two views cannot
  disagree. Nothing to keep in sync.
- **Blocked-ness is derived, never stored.** A task is blocked while any note it
  waits on is not closed (`complete` / `irrelevant`). Finishing a blocker
  releases its dependents immediately, with no bookkeeping.
- **A deleted blocker still blocks, and says so.** A dangling id is reported as
  `missing` rather than treated as satisfied — otherwise deleting a gate would
  silently make the work behind it look ready.
- **Cycles are refused, not tolerated.** `A waits on B waits on A` means neither
  can ever start, and every naive graph walk would recurse forever. Every write
  path (the deps endpoints *and* `PATCH`) rejects an edge that would close a
  loop, with `409` and the offending chain: `outline → tests → wire-api → outline`.
- **Ordering is explicit data.** Columns are ordered by a `position` number
  rather than by `created`, because `created` is only a *date* — every task
  captured in one sitting would tie and fall back to id order. `POST
  /api/board/move` rewrites a column's positions as clean integers (`1..N`), so
  dragging cannot drift into ever-smaller fractional gaps. Tasks get a slot on
  creation; notes that are not tasks do not, so a journal entry never grows a
  meaningless ordering field.
- **Column and status are reconciled, not independent.** `reconcile()` keeps the
  board and the BuJo states telling one story: dropping a card in Done completes
  the task, completing a task moves its card to Done, and dragging a card *out*
  of Done reopens it. A drop names its intent as a column, so the column wins
  (see `reconcile_move`) — that is what makes "leave Done" reopen rather than
  contradict itself.

## Mood & pain

The **Mood** tab is a year of weeks — a GitHub-style heatmap where each cell is
a day, coloured by mood, with a ring on days where pain reached 5 or more.

Logging is one tap. The **How's today?** row writes straight to a note dated
today and tagged `#mood`; touching a face or the pain slider saves immediately,
so there is nothing to submit. Reading is the point of the grid, so logging has
to cost nothing or the grid stays empty.

```yaml
mood: low      # great | good | meh | low | bad
pain: 7        # 0-10, or absent
```

Decisions worth knowing before you edit any of it:

- **The reading lives on a note, not in its own table.** Both fields are just
  frontmatter on any note at all, so logging a mood does not create a parallel
  store — and a hand-written note with `mood:` and `pain:` shows up in the grid
  without being imported.
- **A note's reading belongs to its dates, or to the day it was captured.** A
  note carrying `dates:` counts for each of them (that is how you backfill a week
  you did not write up); an undated note counts for `created`. This is the one
  place a note's mood is not simply "today".
- **A day collapses to its *worst* mood and its *highest* pain.** Averaging is
  the obvious thing and it is wrong here: a good morning and a bad evening
  average into a flat "meh", which hides exactly the day you would want to look
  back at. The individual readings stay on the day (the recent-days list shows
  them), so nothing is lost — only the summary is pessimistic.
- **Logging never hijacks a note you wrote.** The view writes only to a note
  already tagged `#mood`, and derives its id from the date (`mood-2026-09-23`),
  so there can only ever be one check-in per day and a journal entry dated today
  is left alone.
- **Unlogged days are drawn, not skipped.** A faint cell means "in range, not
  logged"; a transparent one means "outside the range you asked for". Filling
  the gaps client-side is what lets the grid keep every weekday in its column.
- **`pain` is clamped, not rejected.** A `12` on a bad day is a real thing to
  type; refusing to save it loses the entry, so it is clamped to 10 on the way
  in and on the way off disk.
- **An unrecognised mood is preserved, not normalised.** A foreign or
  hand-written vault may say `mood: happy`. Rewriting that to nothing on the
  next save would be data loss, so filtering to the five known levels happens
  when the series is *plotted*, never when a note is parsed.

## Daily notes

Finish a task and it is stamped with the day you finished it. After your cutoff
hour (22:00 by default) a scheduler writes that day's finished work into a note
titled with the date — a short recap from the local model, then the list it was
drawn from.

```yaml
completed: 2026-09-23   # stamped on the transition into complete
```

This is the only feature that runs on its own, so most of the decisions are about
what it does when nobody is watching:

- **A task records *that* it is done, not *when*.** Without a stamp there is no
  way to ask "what did I finish on Tuesday", so `completed` is written when a
  note becomes complete. Nothing is backfilled: a status flag cannot be turned
  back into a day, and dating old tasks from the file's mtime would file work
  under afternoons it never happened on.
- **Tasks finished before this existed simply have no date**, so they appear in
  no daily note. That is the honest outcome rather than a guessed one.
- **The date survives editing but not reopening.** Re-saving a finished task,
  renaming it or dragging its card around must not move the work to today, so an
  existing date is kept while the note stays complete. Reopening clears it, which
  is what lets a task finished *again* report the second time.
- **Only `Status.COMPLETE` stamps it.** A struck-through note sits in the Done
  column but was abandoned, not finished; a recap that counts abandoned work is
  worse than no recap.
- **A missed day is caught up for a week**, and at most three days are written
  per tick so a long absence drains over successive runs instead of firing a
  burst of model calls. One day was not enough: finish work on Friday, shut the
  laptop, open it on Monday, and Friday would be dropped for good.
- **A failed recap is retried, not given up on.** If the model is down the list
  is still written, and the day stays owed until the recap succeeds — so a model
  that starts at nine still produces that evening's prose. It stops after six
  attempts so a model that is never coming back is not asked all night, and
  `GET /api/daily` reports any day written without its recap under `retrying`.
- **Daily notes are not tasks.** They are left off the board, which counts what
  it held back in `hidden_daily`; otherwise every day would drop another date
  page into To-do to be dismissed by hand. The same rule keeps a daily note from
  counting itself as the day's work or being read as a recurrence parent.
- **No note is created for a day with nothing in it.** A page saying "nothing
  happened" is worse than the absence of a page.
- **The recap is a fenced section, not the whole note.** The day's note is also
  somewhere you write, so the generated part is delimited by HTML comments and
  swapped wholesale. Regenerating leaves your own lines, before and after it,
  byte for byte.
- **The list is always written; the recap is not.** If the model is down, or has
  been shut off, the note still records what you did and says so in the API
  result. The prose is the part that is allowed to go missing.
- **The run is a loop, not a cron entry.** Nothing else is running to wake a
  local app at 22:00, and a laptop is shut at 22:00 far more often than it is
  open — so the loop ticks, and a day missed while the machine was off is caught
  up on the next start. One day of catch-up only: waking to fifteen model calls
  about afternoons nobody will read is not a feature.
- **Once per day, recorded in the index.** `daily_summary_state` is what stops
  every tick from spending a model call and rewriting the note all evening.
  Anything finished *after* the cutoff needs `refresh` — the run has already
  happened, and the note should not silently disagree with your evening.
- **Scheduler failures are recorded, not swallowed.** `GET /api/daily` reports
  the last error, because a scheduler that fails every night in silence is
  unfalsifiable.

Configuration: `NOOKBOARD_DAILY_SUMMARY_HOUR` (default `22`, `-1` disables).

## API

- `GET    /api/health`
- `GET    /api/collections`
- `GET    /api/notes?collection=&date=&tag=`
- `GET    /api/notes/{id}`
- `GET    /api/notes/{id}/backlinks`
- `POST   /api/notes`
- `PATCH  /api/notes/{id}`
- `DELETE /api/notes/{id}`
- `GET    /api/search?q=`
- `GET    /api/mood?days=&start=&end=` → a collapsed record per logged day, plus
  `summary` (days logged, streak, averages, counts) and the level vocabulary
- `GET    /api/daily` → days owed a summary now, days already summarised, and the
  scheduler's last error
- `GET    /api/daily/{day}` → what that day's note holds and what it is owed
- `POST   /api/daily/summary` `{date?, refresh?}` → write the day's recap
- `GET    /api/calendar/{year}/{month}` → `{"YYYY-MM-DD": count, ...}`
- `POST   /api/recurring/run` → instantiate due recurring notes now
- `GET    /api/export.zip` → download the vault as a zip
- `GET    /api/calendar.ics` → RFC 5545 feed for calendar subscription
- `GET    /api/config` → which vault and model this instance is using
- `POST   /api/rebuild-index` (rebuild DB from .md files)

Board and dependencies:

- `GET    /api/board?collection=&tag=` → five columns of cards, plus
  `summary` (`ready` / `blocked` / `done`) and a flat `blocked` list. Each card
  carries `blocked`, `blockers`, `open_blockers` and `blocking`.
- `POST   /api/board/move` `{id, stage, before_id?}` → move/reorder, reconciling
  status; `409` on a cycle
- `GET    /api/notes/{id}/deps` → `{blocked, blocked_by, blocking}`
- `POST   /api/notes/{id}/deps` `{blocker_id}` → add a blocker (`409` on cycle,
  `409` on self, idempotent on repeat)
- `DELETE /api/notes/{id}/deps/{blocker_id}` → remove one
- `GET    /api/tasks?include_done=&collection=` → flat task list for the picker

Local AI (all stream NDJSON, one JSON object per line):

- `POST   /api/ai/summarize` `{id}`
- `POST   /api/ai/tags` `{id}` → final `{"kind":"result","tags":[...]}`
- `POST   /api/ai/links` `{id}` → final `{"kind":"result","links":[...]}`
- `POST   /api/ai/ask` `{question}` → final `{"kind":"result","notes":[...]}`

Stream kinds: `reasoning`, `content`, `result`, `error`, `done`. A `truncated`
error means the model spent its whole budget thinking.

## Where data lives

```
vault/
  .index.sqlite       # SQLite index (rebuilt from .md files if missing)
  inbox/<id>.md
  home/<id>.md
  work/<id>.md
```

Markdown on disk is the source of truth. The SQLite index powers
search and calendar aggregation. Delete `vault/.index.sqlite` and
restart — the app rebuilds it from the `.md` files.

The index is additive-only: columns introduced later (the board's `stage`,
`blocked_by`, `position`) are `ALTER TABLE`-ed in on open, so an index written by
an older build still works instead of raising on every read.

A task note's frontmatter carries its board state:

```yaml
stage: doing          # backlog | todo | doing | review | done
blocked_by: [outline] # ids of the notes it waits on
position: 2.0         # order within its column
mood: low             # great | good | meh | low | bad
pain: 7               # 0-10, optional
completed: 2026-09-23 # stamped server-side when the task is finished
```

## Obsidian

Point nookboard at any Markdown folder:

```bash
NOOKBOARD_VAULT=~/Documents/School make dev
```

The vault nookboard creates is itself a valid Obsidian vault — open that folder
in Obsidian and everything resolves. Two conventions needed reconciling:

- **Links.** Obsidian resolves `[[Title]]` by filename or by an entry in
  `aliases:`. nookboard stores files as `<id>.md` and resolves by the `title:`
  field, so `[[Buying plants]]` used not to resolve in Obsidian at all. We now
  emit `aliases:` on every write, so the same wikilink works in both apps.
- **Foreign files.** Obsidian notes may have no frontmatter, arbitrary shapes
  for `tags:` (list, space-separated, comma-separated, `#`-prefixed), inline
  `#tags` in the body, and `[[Target|Display]]` piped links. All are parsed.

Pointing nookboard at an existing vault is **non-destructive**: a note
remembers the vault-relative path it came from, so editing never relocates the
file or breaks incoming links. A nested folder's top directory becomes the
note's collection. `GET /api/config` reports which vault and which model the
running instance is using — the first thing to check when a vault looks empty.

## Local AI

Four features, all against a local llama.cpp server, all streamed:

| | what it does |
|---|---|
| **Summarize** | two or three sentences plus bullets for the open note |
| **Suggest tags** | tags drawn from the note, preferring ones already in the vault |
| **Suggest links** | `[[links]]` to notes that already exist, click to insert |
| **Ask my notes** | a question answered from the most relevant notes, with citations |

```bash
NOOKBOARD_LLM_URL=http://127.0.0.1:11440/v1 \
NOOKBOARD_LLM_MODEL=bonsai-27b-q1_0 \
make dev
```

Two things about local reasoning models that shape this design, both measured
against the running instance rather than assumed:

- **It is slow.** Roughly 26 tok/s, and because the model reasons before
  answering, a trivial reply takes **25–95 seconds** (measured; the spread is
  wide and the same call can take 64s or 96s on consecutive runs). The first
  *answer* token can land 60s after the request. So the endpoints stream NDJSON
  and the UI shows the reasoning stream as it arrives — there is visible
  progress from about 2 seconds, which is the difference between "working" and
  "broken". Verified it really streams: 1552 chunks spread over 59 seconds.
- **It can return nothing.** The whole budget can be spent inside the reasoning
  block, giving `finish_reason: length` with empty `content` and a normal 200
  response. That is reported as an explicit error, never as a blank success.
  The default budget is 4096: at 2048 the links prompt truncated, and at 4096
  it has succeeded on every run since (`GET /api/config` reports the budget a
  running server actually loaded).

`ask` retrieval is **term-overlap scoring, not embeddings**. It is good at
keyword-ish questions and useless at paraphrase.

## Stack

- Python 3.13, FastAPI, uvicorn
- python-frontmatter for note serialization
- SQLite (stdlib) for the search / calendar index
- Vanilla JS, no build step, no framework
- `marked` (vendored, MIT) for Markdown rendering
- Catppuccin Mocha theme, hand-written CSS

## Tests

```bash
make test        # 290 pytest — model, vault, obsidian, foreign-vault, db, api,
                 #              backlinks, tags, recurring, export, ics, llm, ai,
                 #              deps (graph/order), board (columns/blockers/moves),
                 #              mood (series/streaks/collapse/coercion),
                 #              daily (stamping/sections/scheduling/recap)
make test-js     # 97 node:test — rapid-log parsing, calendar maths, wikilinks,
                 #              display helpers, board helpers, mood grid helpers,
                 #              and that every local import exists
make test-tz     # the same JS suite under UTC, UTC+14, UTC-11 and America/New_York
./tools/check_render.sh   # 72 DOM assertions in headless Chromium
```

`make test` and `make test-js` cover logic; `check_render.sh` covers whether
the front end actually painted. `make check` runs all of it.

`check_render.sh` seeds its own fixture notes through the API and deletes them
afterwards, so it does not depend on what happens to be in your vault.

**Date logic must be tested in more than one timezone.** `test-tz` exists
because a `toISOString()`-based date helper passes on a machine in EDT and is
wrong by a day everywhere else — see `localIsoDate()` in `static/js/entry.js`.
Any new date code should use that helper, never `toISOString()`.

**LLM code is tested against a real HTTP server emitting real SSE framing**,
not a mocked transport (see `tests/conftest.py`). Mocking the transport would
not have caught the two behaviours that actually matter: that reasoning is
separate from content, and that a spent budget yields empty content with a
200 response.