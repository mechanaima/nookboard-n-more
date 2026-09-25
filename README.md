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
make dev    # http://127.0.0.1:8765 -- with --reload, for working on the app
make test   # pytest
make test-js
```

On boot, as a service, with no terminal open:

```bash
./tools/install-service.sh            # user service on 127.0.0.1:8765
./tools/install-service.sh --dry-run  # show the unit, install nothing
./tools/install-service.sh --remove
```

It turns on `enable-linger` so it starts at **boot** rather than at login, and it
runs *without* `--reload` -- this is the app, not a development server, and a
file watcher left over the vault for the machine's whole uptime is a strange
thing to own. After editing code: `systemctl --user restart nookboard`. To work
on it live, run it somewhere else and leave this one alone:

```bash
uv run uvicorn app.main:app --reload --port 8796
```

## Working on this repo

Read [`AGENTS.md`](AGENTS.md) before editing. It is the working agreement for
anyone (agent or human) changing this code: edit discipline, the commands to
verify with, git etiquette, and what to do if something breaks.

The front end has no build step, so a syntax error in `static/js/app.js` ships
straight to the browser and blanks *every* view. Run `make check` before calling
a change done — it ends with `tools/check_render.sh`, which loads each view in
headless Chromium and asserts it actually painted.

## Install it as an app

It is a PWA, so Chromium will offer to install it (the icon in the address bar,
or ⋮ → *Install*). The installed window has no browser chrome and remembers its
own size.

Two files are served from the **root**, which is not a detail: a service
worker's scope is the directory it is served from, so one under `/static/` could
only ever control `/static/` -- and the only thing a worker here is for is a
navigation.

Offline it opens and shows the last thing it loaded, and says which one it is.
The worker is network-first everywhere -- there is no asset versioning in this
app to key a cache on, and twice a stale `app.js` was the whole of a bug report
-- and it never caches `/api/`, because a note app that answers "what is in my
vault" out of a memory is lying. With no server reachable the app draws the
notice line at the top instead of an empty vault: *"could not reach nookboard's
server"* is a different fact from "nothing finished", and the app will not
render the second when it means the first.

The icons are generated: `./tools/build-icons.sh` renders them from the two
SVGs beside them, so the sizes the manifest advertises cannot drift from the
files that exist.

## Keyboard & deep links

| key | action |
|---|---|
| `/` | focus search |
| `n` | new note |
| `Esc` | close the reading dialog, the open note, or blur search — in that order |
| `Enter` / `,` | commit a tag in the tag field |
| `Backspace` | in an empty tag field, remove the last tag |

Deep links (shareable, and they survive reload):

```
#/                          home (the dashboard)
#/view/home                 home, explicitly
#/view/rapid                rapid log
#/view/board                kanban board
#/view/mood                 mood & pain heatmap
#/view/calendar             calendar tab
#/note/<id>                 a specific note
#/view/timeline/note/<id>   a note with the timeline tab selected
#/view/board/note/<id>      the board with a card open in the editor
#/preview/<id>              a note open in the reading dialog
```

## Reading a note

**Preview** in the editor's tab row opens the note in a dialog rather than swapping
half the editor for it: reading a finished note is a different act from editing one,
and it wants the whole window. The dialog holds the properties the note carries, the
note rendered, and what links to it — one scroller, so there is one place to lose your
place. The page behind it is hidden while it is open: a reading that leaves the editor
legible beside it shows you the same note twice. `Esc` closes it, `Tab` stays inside it, clicking the backdrop closes it, and
focus goes back to the button that opened it. It is a deep link as well
(`#/preview/<id>`), so a reload lands back in the reading rather than the editor
behind it.

Properties are drawn only when the note actually carries them — an empty `folder:`
line is not information — under the app's own names. Then comes whatever else the
file's frontmatter says, under the file's own key names, because **this app carries
frontmatter it does not understand rather than dropping it**: a key nobody here knows
is read, kept verbatim, and written back on the next save. A vault is hand-edited
text, and losing a note's own words is the one thing a note app must never do.

## Design

Dark-first, built on **Catppuccin Mocha** with a restrained vaporwave accent.
The pink → mauve → cyan gradient survives only as text or a rule — the wordmark,
the active-tab underline, the dashboard title — because the primary action is
glass now (see below).

The app reads as **glass over a lit wall**: one `--wall` on `body`, and every
panel, column, card and button samples it through the glass tokens in section 1.

Three things about that are not obvious, and are worth not breaking:

- **A blur over a smooth gradient is invisible** — it renders the identical
  smooth gradient. The wall's fine lattice (24px pitch) is what the blur has to
  destroy: crisp outside a panel, hazed *inside* it. That contrast between
  smeared and sharp is the whole frosted read. Without the lattice the panels
  look like flat dark cutouts, which is exactly how the first attempt at this
  turned out.
- **Panels sit about eight luminance steps above the wall**, and it is
  arithmetic rather than taste: on a dark theme a panel *below* the wall reads
  as a hole rather than a raised surface. The glass tokens are built from
  `--surface1` for that reason. Move the wall and you have to re-measure them.
- **A breakpoint on the window is not a breakpoint on a column.** The editor is a
  *column* of the layout, so at a 1200px window it is a 232px pane. The meta grid used
  to be four fixed fractions with a window media query dropping it to two, which gave
  every field 56px — narrower than the word COLLECTION — so labels painted over each
  other and selects showed two letters of their values. It now asks how much room
  *it* has (`repeat(auto-fit, minmax(min(100px, 100%), 1fr))`), and a label that still
  does not fit is ellipsised rather than drawn over its neighbour. `check-scroll.mjs`
  measures it, at the width where it broke.
- **One recipe per thing.** Tags are `.tag-chip, .card__chip` — one rule, two
  callers. The primary action is one glass button, shared by the board, the
  editor and the sidebar. The pressed filter chip reuses it. Anything written
  twice drifts; two buttons that meant the same thing already had.

The whole visual layer is one hand-written stylesheet —
`static/css/app.css`, 24 numbered sections (tokens → reset → typography →
topbar → sidebar → entries → editor → controls → markdown → calendar → empty
state → motion → board → mood → responsive → templates → dashboard →
transcribe → times → workspaces → history → bookmarks → note icons →
preview modal). The index at the top of the file is part of the change: new
sections go at the **end**, never patched in above another one. There is
**no framework, no build step, and no CDN**; `marked` is vendored into
`static/vendor/`.

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
./tools/check_render.sh 'http://127.0.0.1:8765/#/note/<id>'   # 180 DOM assertions
./tools/shot.sh /tmp/shot.png 'http://127.0.0.1:8765/'        # screenshot
./tools/contrast.sh 'http://127.0.0.1:8765/#/view/board' .card__chip
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

- **Home** — the dashboard the app opens on: calendar, clock, statistics, mood
  streak, today, and the board's counts (see [Home](#home))
- **Rapid Log** — type `• task`, `○ event`, `– note` and hit Enter
- **Board** — a kanban view with real dependencies: columns, drag or tap to
  move, blocked cards, cycle-safe blockers (see [Task management](#task-management))
- **Mood & pain** — a year-at-a-glance heatmap with one-tap logging, streaks and
  a distribution (see [Mood & pain](#mood--pain))
- **Daily notes** — an end-of-day recap of what you actually finished, written
  into that day's own note (see [Daily notes](#daily-notes))
- **Insight** — pain against what you actually finish, from the two datasets
  the vault already holds (see [Insight](#insight))
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
- **Times** — a note can name an instant, not just a day: `at: 14:30`,
  `until: 16:00`. It shows on the calendar, exports as a real timed event, and a
  user timer pops a desktop notification when the time arrives
  (see [Times and reminders](#times-and-reminders))
- **ICS subscription** — `GET /api/calendar.ics` — external calendar apps subscribe
- **Obsidian interop** — the vault is a valid Obsidian vault; point nookboard at
  any Markdown folder with `NOOKBOARD_VAULT` (see [Obsidian](#obsidian))
- **Transcription** — hand it a recording (a path, a file, or the microphone)
  and get a note: whisper.cpp on the GPU for the transcript, the local model for
  the summary, nothing leaving the machine (see [Transcription](#transcription))
- **Local AI** — summarize / suggest tags / suggest links / ask your notes,
  streamed from llama.cpp (see [Local AI](#local-ai))
- **Workspaces** — point a note at a folder of code (`path:`) and it shows what
  that folder is *now*: branch, what is uncommitted, when it was last committed,
  the markers the code still carries, and buttons to open it in your editor,
  terminal or file manager (see [Workspaces](#workspaces))
- **History** — the whole vault under git, in the vault, off until you ask for
  it: versions for every note, a button to write an old one back, and a list of
  notes you deleted and can still bring back (see [History](#history))
- **Icons** — give a note a Lucide icon (`icon: server`) and it is drawn beside
  the note wherever the note is shown: board cards, lists, bookmarks. A name, not an
  image — the file stays portable text and the icon still draws with no network (see
  [Icons](#icons))
- **Bookmarks** — put an address on a note (`url:`) and the vault keeps your
  services the way it keeps everything else: as markdown you can grep, group by
  tag, and back up. Opening the view asks each address whether it is answering and
  shows what it said — with the time it was asked, because a status without one is
  a claim about the past dressed as the present (see [Bookmarks](#bookmarks))

## Home

The view the app opens on. Seven cards over the same vault every other view reads.

| card | what it shows |
|---|---|
| Calendar | the month, with a tint on every day that has something written on it |
| Clock | the greeting, the time, the date |
| Statistics | what the vault holds: files, tasks, tags, collections |
| Mood | the streak and today's reading |
| Today | whether today has a note yet, and what has been finished |
| Board | ready / open / blocked / done |
| Workspaces | which folders want attention, and what they say |
| Bookmarks | how many addresses the vault keeps, and how many cannot be opened |

**Why the clock is the browser's job.** Every other card is a fact about the
vault, and the server knows those. The time is not one: a clock rendered
server-side is wrong by the time you read it. So the greeting and the time are
painted from `new Date()` in `static/js/home.js`, and the mini calendar is laid
out by the same `monthGrid()` the Calendar view uses — the same function, not a
second one that could disagree about which weekday the 1st falls on.

**Why the cards cannot contradict the views they stand for.** Every number is
either copied from the layer that owns it (`board_summary`,
`daily.completed_on`, `mood.summarize`) or counted here exactly once. The first
version of the statistics card counted "open" for itself and said 2 where the
board said 3: the board counts anything not closed — notes included — and that
count had quietly decided "open" meant open *tasks*. The tile is gone. The board
card carries that number, and `tests/test_home.py` pins the arrangement so it
does not come back.

The dashboard's search hands off to the topbar search rather than growing a
second one, and the three entry buttons on the icon row type the notebook's own
signifiers (`•`, `○`, `–`) into the rapid log — the same glyphs the legend uses,
read by the same parser.

## Task management

The **Board** tab is a kanban over the same notes as everything else — not a
separate database. A card *is* a note; moving it edits the Markdown.

**What is not on the board.** Five collections are note *material* rather than work,
and never appear as cards: `daily`, `weekly`, `bookmarks`, `workspaces` and
`testing`. Neither are templates (a shape for notes is not a thing to be doing) or
the period notes the app writes itself. Without this, a board fills with cards that
can only be dismissed by hand, forever.

The rule lives in exactly one place — `models.not_work_reason` — because the board,
the Home dashboard and an unnarrowed query all have to hide *the same* set, or the
app contradicts itself about what you have left to do. The response reports what it
held back, and *why*, as a partition that adds up:

```json
{"hidden_collections": 12, "hidden_generated": 2, "hidden_templates": 2, "hidden_total": 16}
```

so a board never quietly looks smaller than the vault it describes. Asking for one of
those collections by name (`in bookmarks`) is a deliberate act and is answered.

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

## Insight

Pain is logged on the days it is felt and tasks are finished on the days there
is capacity for them. Both live in the same Markdown files, written by the same
app, and nothing had ever looked at them together — so this is the one question
a notes app cannot answer and this one can: **does the work happen on the good
days?**

It is most of the difficulty of this feature that the honest answer is usually
"not yet", so that is what it says:

- **Below eight paired days it states no relationship at all.** With five, the
  coefficient swings on a single unusual day. The bands are still shown, because
  "on days logged at this pain, this much got finished" needs no statistics —
  only the correlation is withheld.
- **Only days from the first stamped completion onward are paired.** Completion
  dates did not exist before that field shipped, so counting earlier days as
  "nothing finished" would line old high-pain days up against zero output and
  manufacture the very relationship being looked for.
- **A logged day with nothing finished is still a pair.** That is half the
  signal; filtering to the productive days would remove it.
- **Rank correlation, not Pearson.** The question is whether worse days mean
  less output, which is about order. Pain 3→4 need not be the same step as
  7→8, and nothing assumes it is.
- **The caveat ships in the payload, not the UI.** It is association, not cause:
  this cannot say whether pain reduced the work or the work worsened the pain,
  and it says nothing about days that went unlogged. Keeping it server-side stops
  it being dropped for looking untidy.

```bash
GET /api/insight
```

```json
{"pain_vs_output": {
  "days_paired": 44, "min_days": 8, "since": "2026-07-26", "rho": -0.869,
  "bands": [{"band": "mild", "low": 0, "high": 2, "days": 13,
             "mean_completed": 4.8, "total_completed": 62}],
  "reading": {"strength": "strong", "direction": "negative",
              "text": "Across 44 days, more gets finished on lower-pain days (strong relationship)."},
  "caveat": "This is a pattern in what was written down, not a cause. ..."}}
```

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
- **A period note is a record of work, not a piece of it.** Daily and weekly
  notes are both left off the board, which counts what it held back in
  `hidden_generated`; otherwise every day would drop another date page into
  To-do to be dismissed by hand. The same rule keeps a period note from counting
  itself as the period's work, or being read as a recurrence parent.
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
  up on the next start. A week of catch-up, at most three days per tick: one
  day was not enough (finish work on Friday, shut the laptop, open it on Monday,
  and Friday was gone for good), and waking to fifteen model calls about
  afternoons nobody will read is not a feature either.
- **Once per day, recorded in the index.** `daily_summary_state` is what stops
  every tick from spending a model call and rewriting the note all evening.
  Anything finished *after* the cutoff needs `refresh` — the run has already
  happened, and the note should not silently disagree with your evening.
- **Scheduler failures are recorded, not swallowed.** `GET /api/daily` reports
  the last error, because a scheduler that fails every night in silence is
  unfalsifiable.

Configuration: `NOOKBOARD_DAILY_SUMMARY_HOUR` (default `22`, `-1` disables).

## Weekly notes

The same idea over a week — the unit you actually review in.

- **Written into the week's own note**, `weekly-YYYY-Www`, dated the Sunday the
  week ended. The ISO label is derived rather than stored, so a week cannot
  disagree with which week it is.
- **First owed on the Monday after the week ends.** A review of a week that has
  not finished is a review of the wrong week. It is still owed on Wednesday if
  the laptop was shut on Monday.
- **A week with nothing finished in it gets no note**, for the same reason a day
  does not.
- **Grouped by the day each thing was finished on**, so a rollup reads as a week
  rather than a heap, and each task is linked by title — which also gives the
  task a backlink to the week it belongs to.
- **"How the week felt"** is built from the mood readings that already exist,
  and says only what it has: `Logged 2 of 7 days` is part of the sentence because
  a week with two readings is not a week with seven.
- **The standing pain-against-output reading appears only when there is one to
  state** — a week is a convenient moment to notice it, not a reason to invent
  it.
- **Both the list and the prose obey the daily rules**: the list is written even
  when the model is unreachable, and a week whose prose failed stays owed so it
  can still arrive (bounded, like the day).
- **Manual runs** via `POST /api/weekly/summary`, or the *Recap this week*
  button, which takes the week from whichever note is open.

Configuration: `NOOKBOARD_WEEKLY_SUMMARY` (default `true`) switches the weekly
run off on its own. Both runs share one cutoff hour, `NOOKBOARD_DAILY_SUMMARY_HOUR`,
so the two cannot drift apart.

## Templates

A note that is a shape for other notes. No new file format: put a note in the
`templates/` collection and it becomes one.

- **Placeholders**: `{{date}}`, `{{time}}`, `{{week}}`, `{{week_start}}`,
  `{{week_end}}`, `{{title}}`. That is the entire vocabulary, and there is no
  format mini-language — `{{date}}` is always ISO, because a template that
  renders differently from one day to the next is a template you cannot rely
  on.
- **Anything else is left exactly as written.** `{{stuf}}` is a typo, and a
  template is a document you wrote: eating it, or guessing at what it meant,
  would quietly change what your notes say. Leaving it visible means you find
  the mistake instead of inheriting it into a hundred notes.
- **`{{title}}` resolves in the body, not in the title field.** A title that
  refers to itself has nothing yet to refer to, so there it stays literal.
- **Applying one** uses the picker above the Rapid Log, or
  `POST /api/templates/apply`. The note lands in the collection you are looking
  at, is dated today, and opens for editing.
- **The template's signifier and tags carry over**, so a task template makes a
  task. Its state does not: a template left marked done still makes open work.
- **Applying the same template twice gets you `(2)`.** This vault resolves
  `[[wikilinks]]` by title, so two notes sharing one title would resolve to each
  other and the backlinks would stop meaning anything.
- **Templates are kept off the board**, counted in `hidden_templates`, for the
  same reason period notes are: a shape for notes is not a thing to be doing.
- **Manual application can pick the day** (`date`), so a template can be used to
  fill in a day that has already passed.

## Queries

A template expands once and freezes. A query is resolved every time you *look*
at the note, so it keeps telling the truth as the vault changes. Put one in a
`nookboard` code block:

````md
## Finished

```nookboard
completed this week
```
````

| query | what it becomes |
| --- | --- |
| `days this week` | the days, linked: `- [[2026-09-21]] Monday` |
| `completed this week` | what was finished, grouped by the day it was finished |
| `completed today` | what was finished that day, as a list |
| `completed on 2026-09-24` | the same, for one named day |
| `completed in 2026-W38` | the same, for a named ISO week |
| `open tasks` | everything still open, linked |
| `open tasks in work` | the same, for one collection |
| `completed this week in work` | what was finished in one collection |
| `show notes in #mood` | the notes carrying that tag, newest first, linked |
| `show notes in journal` | the notes in one collection, newest first, linked |
| `show notes in #mood done` | only the ones that are complete |
| `show notes in #mood not done` | only the ones that are not |

Periods: `today`, `yesterday`, `this week`, `last week`, `this month`,
`last month`, `on <YYYY-MM-DD>`, `in <YYYY-Www>`. Either period-taking verb
takes any period.

- **The day comes from the note, not the clock.** `this week` in a note dated
  yesterday means *that* week, so a note about last week still reads as last
  week when you open it in March. This is what makes the same template usable
  for any week instead of only the current one.
- **The answer is never written into your file.** The note keeps the query and
  only the reading pane resolves it, so the same file opened in Obsidian shows
  the query rather than a list that was true when it was written.
- **What counts as finished** is `daily.completed_on` — the same rule the daily
  and weekly notes use, so a query and a generated note cannot disagree.
- **`#` is what makes it a tag.** `show notes in mood` and `show notes in #mood`
  are different questions — one collection, one tag — and a name the vault does
  not have is refused with the ones it does, because a misspelled tag and an
  empty one look identical in a note. `show notes` on its own is every note you
  own, which is not an answer, so it says what to write instead.
- **`done` means complete, and nothing else.** A note marked `irrelevant` or
  `migrated` was set aside rather than finished, so it counts as *not done* —
  otherwise this filter would quietly mean "closed", which is the board's word
  for a different question. `open tasks` and `completed` already mean it, so
  adding the word to those is refused rather than ignored: a filter that is
  dropped in silence answers something other than what you wrote.
- **Newest means the day the note is about**, not the day the file was made:
  `created` is a date, so a whole sitting's notes tie on it. A list longer than
  25 says how many it is not showing, rather than looking like the whole set.
- **A fence you have not closed yet is left alone.** Mid-typing a query is not
  yet a query, and this is also how you write *about* a query in a note rather
  than running it. A fence in any other language is never touched.
- **A query that cannot be read says so** where its answer would have been.
  Rendering nothing would look like one that failed.
- **Adding `in <collection>` narrows it.** Names are matched regardless of case,
  and a name the vault does not have is refused along with the list of ones it
  does — a misspelled collection and an empty one are indistinguishable in a
  note, and only one of them is worth your attention. `in` is already spoken for
  by `in 2026-W39`, so a trailing `in` followed by an ISO week stays a period.
- **`days` takes no collection** and says so rather than dropping the filter:
  the days of a span do not belong to a collection, and answering a slightly
  different question than the one written is worse than refusing.
- **With no collection named, shapes and generated notes are left out.** A
  template is a shape for other notes and a period note is something the app
  wrote, so neither is a thing to be doing — the same set the board hides.
  Naming one still answers (`open tasks in templates`): hiding something from a
  default view is not the same as forbidding it.

### A weekly template

Days of the week, linked, and what was finished on each — write it once and it
is right for whichever week you apply it to. A note in `vault/templates/`:

````md
---
title: "Week {{week}}"
tags: [weekly]
---

# {{week}} · {{week_start}} – {{week_end}}

## Days

```nookboard
days this week
```

## Finished

```nookboard
completed this week
```
````

Applied on any day it names the week it lands in, and the two queries then
answer for that week rather than for today, so a note made in September still
reads correctly in March.

## Transcription

Hand the app a recording and get a note back. Three ways in, all built the same
way underneath:

- a **path** to a file that is already on this machine — nothing is copied, the
  note records the path, the file stays where its owner put it
- a **file you pick** — copied into `vault/.audio/`, because for that file the
  copy is the one that will still exist tomorrow
- the **microphone** — recorded in the browser, uploaded on stop, kept in
  `vault/.audio/` for the same reason

ffmpeg reads it (video included — `-vn` means a screen recording costs a decode
and not two gigabytes of frames), whisper.cpp transcribes it on the GPU, and the
local model writes the summary. **Nothing leaves the machine.**

The note lands in `transcripts/` as `transcript-YYYY-MM-DD-<slug>.md`:

```markdown
_Source: `/home/irving/lectures/week-3.m4a` · mov · aac · 52:05 · whisper `small` · en_

<!-- nookboard:transcript-summary:start -->
Three sentences of prose, then at most six bullets.
<!-- nookboard:transcript-summary:end -->

<!-- nookboard:transcript-body:start -->
`[0:00]` The first thing the speaker said.
`[0:12]` And then the next, after a pause long enough to be a paragraph.
<!-- nookboard:transcript-body:end -->
```

Fenced regions, like a daily note's list: re-running replaces exactly what it
generated last time and leaves anything you typed yourself alone. The note is
written **as soon as the transcript exists**, with the summary added a minute
later — the transcript is the expensive part and is already true, while the
summary is a model call that may fail. If it does, the note says why and the
summary can be retried on its own (`re-summarise` on the job, or
`POST /api/transcribe/summarize`) without transcribing the recording again.

A transcript is a **record, not work**: it never becomes a board card and never
counts as something you finished that day. It is a note — searchable, editable,
wikilinkable, in its own collection.

Long recordings are summarised **in parts and then combined**: `small` transcribes
a one-hour lecture in a few minutes, and a single pass over the transcript would
be both slower and past the point where a local model starts losing the
beginning. Chunks follow the paragraph boundaries the transcript already has,
which are places the speaker stopped.

Jobs are **one at a time** — whisper and the summary model share one GPU, so two
at once do not finish sooner, they page — and they live in memory. After a
restart a job is gone; the note is not. The UI says so rather than guessing that
it finished.

### Pointing it at whisper.cpp

The engine is found, reported, and overridable. `GET /api/transcribe` says which
binary and which models were found, and the view shows that line: "transcription
is broken" and "it is running the wrong build" are different problems and only
one of them is answerable from outside.

```
NOOKBOARD_WHISPER_CLI     whisper-cli to run (default: look in the usual places)
NOOKBOARD_WHISPER_MODELS  directory of ggml-*.bin models
NOOKBOARD_WHISPER_MODEL   model to use by default (default: small)
```

Models looked for: `ggml-small.bin` and `ggml-medium.bin`. If nothing is found,
the endpoint says which variable fixes it instead of failing anonymously.

## Times and reminders

A date says which day. A time says *when* — and the things worth being told about
happen at a time: a lecture at 11:20, a call at 14:30.

```yaml
dates: [2026-09-25]   # which day
at: 14:30             # when, on that day: 24-hour "HH:MM". Names an instant
until: 16:00          # optional; an hour later if you leave it empty
```

On the task screen a **time** row sits right under the dates: two clock fields and
a ✕ that clears both. Under it, one line says what the app will do with what you
typed — `fires at 14:30`, or, if you set a time and no date, that **a time on its
own names no instant, so nothing will fire**. The rule is said before you save
rather than discovered afterwards.

- **The calendar** shows the time on the day's entry, not just a count.
- **The ICS feed** exports a timed note as a real timed event, so an external
  calendar puts it where it belongs on the grid instead of as an all-day banner.
- **The board** shows it as a chip beside the date, so a card you scan says when.

### The popup

`tools/notify-events.py` reads the vault **with the app's own parser** — one
implementation of what a time means, shared with the calendar and the feed — and
pops a desktop notification through `notify-send` / `dunstify` when a note's time
arrives. Each occurrence is announced **once** (a ledger at
`~/.local/state/nookboard/notified.json`), and lateness is said rather than
hidden: the popup for a 09:00 note first seen at 09:40 tells you it is late.

```bash
./tools/notify-events.py --dry-run        # say what would fire, send nothing
./tools/install-events-timer.sh           # install + start a user timer
journalctl --user -u nookboard-events.service -f   # watch it work
./tools/install-events-timer.sh --remove  # stop and delete the units
```

It is a **user** timer, not a system service: no root, and it reads the same vault
the app does. Re-run the installer after moving the checkout — it rewrites the
paths rather than making you hand-edit unit files.

## Icons

A note with an `icon:` shows it. The name is a [Lucide](https://lucide.dev) icon
name — `server`, `book-open`, `heart-pulse` — and the drawing is vendored, so the
note stays portable markdown and the icon renders with no network, no sprite sheet
and no image files in the vault.

```yaml
icon: brain
```

| where a note appears | what you see |
|---|---|
| board card | the icon leading the card, before the title |
| list row (timeline, rapid log, search) | the icon takes the glyph's place, so a row keeps **one** mark, not two |
| bookmark card | the icon inside the title's line |
| editor | the row itself, a live preview, and *Browse…* for the picker |

**The picker searches, because two thousand icons is not a list.** *Browse…* opens a
grid that matches as you type, ranked so `serv` offers `server` before
`cloud-server-2`, and draws at most 120 of them — the count beside the search box
says how many matched, so a short grid never quietly looks like the whole set.

**Two files hold the icon set, and both are generated.** `static/vendor/lucide.js`
(the drawings) and `app/icons.py` (the names) come from one npm tarball, by
`uv run tools/build-lucide.py <version>` — currently **Lucide 1.48.0, 2118 icons**,
ISC licensed, with the copyright notice in the generated file's header. Neither is
edited by hand, and `tests/test_icons.py` asserts they agree: a name the picker
offers that the server does not recognise would be a note whose icon silently does
not draw, so that is a test rather than a promise.

**An unknown name is kept and reported, not refused** — `icon: serverr` saves, the
API says it is not a Lucide icon, and the row falls back to the signifier's own
glyph with the reason in the tooltip. A note is the person's own file, and losing a
line of it over a typo is the worse failure: the same call this app makes about an
address a browser cannot open.

## Bookmarks

A note with a `url:` is a bookmark. That is the whole rule — no marker tag, no
separate store, no database table: the same markdown files as everything else, so
they can be grepped, edited by hand, and carried in the vault's own history.

The Bookmarks view groups them by **each note's first tag** (`services`, `tools`,
`school` — whatever you use) and puts the untagged ones under **Other**, last,
because Other is the absence of a decision rather than one that sorts early. Each
card shows the title you gave it, the host it goes to, and the note's own line
about why it is there. Clicking opens a new tab; the app itself never opens
anything for you outside the workspace buttons.

**Status, and the price of it.** The view asks every address whether it is
answering and shows the answer on a chip: `answering`, `answered 404`, `no answer`.
That reverses an earlier decision here — the view used to say, deliberately, that it
does not probe your machines, because a health check is a second answer about state
that goes stale between looks.

Reversed, with the honesty moved rather than dropped. **A status without a timestamp
is a claim about the past dressed as the present**, so:

- Every check is **taken when you look** — on opening the view, and again when you
  press *Check again*. Nothing polls on a schedule and nothing is stored: no
  `cron`, no history of uptime, no alerting, and the app never claims to know what
  happened while you were away.
- The line above the list says **when** the answers were taken (`checked just now`).
- A card nobody has asked about says **`not checked`**, and is never drawn green.
  Before the answers land, the chips say exactly that.
- `answering` only ever comes from a status code that answered. A service that is
  reachable and unhappy says `answered 503` — which is a different fact from silence,
  and the number is the difference.
- The reason is in words, not a code: *nothing is listening on that port*, *that name
  does not resolve*, *no answer within 2.5 seconds*.
- Every request is bounded and concurrent, so one dead host costs seconds rather than
  the view, and **the list is drawn before the check is asked for** — the page never
  waits on the network to show you what is in the vault. Nothing reads a response
  body, so a bookmark to a large page costs the same as one to a small one.

An address a browser could not open is not asked about at all — it has no host to
reach, and "no answer" for a typo would be the same mistake twice.

**An address a browser cannot open is shown, not dropped.** A missing `https://`,
a `javascript:` url, a `file:` path — the card goes a dashed border and says why,
in a sentence. It is *not* refused when you save it: a note is your file, and
losing what you wrote about a service because you mistyped its address would be
the worse failure. Saving a url the app dislikes is allowed; the view is where
you find out.

## Workspaces

A note with a `path:` is a workspace: the note is the *thing you write about*,
and the folder is what it points at.

```yaml
---
collection: workspaces
path: ~/Documents/School/Programming/Scripts
---
```

```bash
curl localhost:8765/api/workspaces            # every one, with its state
curl localhost:8765/api/workspaces/ws-scripts # one
```

| what it shows | where it comes from |
|---|---|
| branch, and how far it has drifted | `git status --porcelain=v2 --branch` |
| how much is uncommitted, and the file names | the same read |
| when the last commit was, and its subject | `git log -1` |
| how many files, and in what languages | one walk of the tree |
| `TODO` / `FIXME` / `XXX` / `HACK` in comments | the same walk |
| whether `code`, `alacritty`, `xdg-open` are installed | resolved the way every other tool is |

**The folder is read, never guessed.** Every number on a card comes from `git`
itself, in a request the server makes when you look — no cache, because a cache
is a second answer that goes stale the moment you commit. This is the one place
in the app where the tempting lie — "modified 3 hours ago" from a file's mtime —
would be most convincing, so nothing here touches a modification time: what a
folder *is* is a question only a version control system can answer.

**A folder inside a bigger repo says so.** `~/Documents/School/Linux/Scripts` is
not its own repository; it sits inside the School repo, so its card reads *"1
uncommitted here · 4 days ago · in the School repo"*. The count is about this
folder, the repo is what would take the commit, and git is asked at the **repo
root**: asked from inside the subdirectory, git reports paths *relative to that
subdirectory* (`./`, `../elsewhere.py`), which is how a prefix filter silently
matches nothing.

**A marker has to be in a comment.** Uppercase `TODO`/`FIXME`/`XXX`/`HACK`
behind a comment introducer — so `Stage.TODO` and the word "todo" in a sentence
are not markers, and neither is marker-shaped *data*: a string literal like
`("# FIXME: this loops forever", "FIXME")` in a test is not a note-to-self. It
is a heuristic (a marker after an apostrophe inside a real comment is missed),
and the app prints only the text it actually found, so a missed marker is
quieter than a quoted one that was never there.

**Opening a folder is an allowlist, and it says what it will run.** The three
buttons call `POST /api/workspaces/{id}/open` with `{"what": "editor"}`; the
server picks the binary from a fixed list, refuses anything else, and answers
with the argv it ran. The endpoint is bound to localhost and requires the app's
own header, so a page you merely visit cannot launch a terminal on your machine.
A button whose tool is missing stays visible, disabled, and says which tool.

**A marker is a place you can go.** Each marker and each changed file name on a
card is a button: it opens that spot in the editor (`--goto file:line`). It looks
like the monospace line it replaced — underlined on hover, nothing else — because
it is the same information with somewhere to go. The `file` in that request comes
from the page, so the app opens it only when it resolves to a real file strictly
inside the workspace's own folder: `../../etc/passwd` and `--wait` are refused
outright, not trimmed into something openable, and a refusal is a `400` rather
than a "not found" — the request asked for something it may not have.

**Honest scope: a workspace is a link plus a state line, not a second IDE.**
There is no file tree, no editor, no embedded terminal, no code search index and
no GitHub sync. Those exist, and they are better at being themselves than this
would be at imitating them; the point here is that the folder is one card away
from the note you were already writing.

## History

The vault is a folder of ordinary markdown, and that is what makes this possible:
**git, in the folder, beside your notes.** Turn it on and every change from then
on can be undone — a bad edit, a deleted note, an afternoon you regret.

```bash
curl -X POST localhost:8765/api/history/init    # turn it on (once)
curl localhost:8765/api/history                 # what changed, what is gone
curl localhost:8765/api/history/<note-id>       # one note's versions
curl -X POST localhost:8765/api/history/restore \
     -H 'content-type: application/json' \
     -d '{"path": "inbox/soil.md", "rev": "<sha>"}'
```

**It is off until you ask.** Turning it on writes a `.git` into your vault, which
is a thing to be asked for rather than a thing that happens to you. The History
view offers the button; nothing enables it on your behalf.

**A change is recorded after it is saved**, never before — the same ordering as
the index update, and for the same reason: the note is on disk before git is
asked anything, so a history that fails can never be the reason a save failed.
Every commit is authored by `nookboard` rather than by you, because a history that
claimed you wrote a line you did not write would be worse than no history.

**A commit names the note that changed.** `edit: Soil mix`, `new: Seed trays`,
`delete: Render Check` — one file per commit, so a commit you read in the log is a
change you recognise, and untouched notes stay out of someone else's commit.

**Restoring writes one file**, from one old version. It is not `git reset`, and
nothing else in the vault can move — which is what makes it safe to put behind a
button. The restore is itself recorded (`restore: soil`), because undo that cannot
be undone is undo you are afraid to use.

**Unrecorded work is said out loud.** Reordering a column and editing
dependencies are frontmatter-only writes, and a commit per drag would be a log
nobody reads, so they are not committed one at a time. They are not hidden
either: the view lists them under *Not recorded yet* with a button that records
them (`checkpoint: the vault as it is`). A gap you can see is not a gap.

**Deleted notes come back.** The view lists the notes the vault has lost, each
with the version that brings it back — and a note that has since been recreated is
not listed as lost, because offering to restore something already there is a
question whose answer the app already knows.

**Whether the newest version is the one you have is measured, not assumed.** A
note edited outside the app is not its own newest commit, and the panel asks git
rather than calling the top of the list "current".

**The index is excluded.** The `.gitignore` written into the vault covers
`*.sqlite`: the index is built from the markdown beside it and
`POST /api/rebuild-index` rebuilds it, so committing it would version a cache next
to its source.

Two limits worth naming. History starts when you turn it on — there is no past to
recover from before that, and inventing a baseline to pretend otherwise would be a
lie with a timestamp. And the app never resolves revision syntax: it has shas,
which is what `git log` printed and what the panel is holding.

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
- `GET    /api/home?month=YYYY-MM-DD` → every card on the dashboard in one
  response, so the cards are one consistent reading rather than six separate
  ones. `month` picks the month the calendar card shows and defaults to now.
- `GET    /api/mood?days=&start=&end=` → a collapsed record per logged day, plus
  `summary` (days logged, streak, averages, counts) and the level vocabulary
- `GET    /api/insight` → `pain_vs_output`: the paired days, mean finished per
  pain band, the rank correlation, and the caveat
- `GET    /api/daily` → days owed a summary now, days already summarised, any
  day waiting on a recap under `retrying`, and the scheduler's last error
- `GET    /api/daily/{day}` → what that day's note holds and what it is owed
- `POST   /api/daily/summary` `{date?, refresh?}` → write the day's recap
- `GET    /api/templates` → the shapes a note can be made from, with the
  placeholders each one uses
- `POST   /api/templates/apply` `{template, title?, collection?, date?}` →
  make a note from one
- `GET    /api/query?q=&on=` → resolve one query for the preview to splice in.
  `on` is the date of the note the query sits in, which is what makes
  `this week` mean the week that note is about
- `GET    /api/weekly` → weeks owed a review now, weeks already reviewed, any
  owed-but-unwritten prose and the last error
- `GET    /api/weekly/{week}` → what that week's note holds and what it is owed
- `POST   /api/weekly/summary` `{week?, refresh?}` → write the week's rollup;
  defaults to the most recent week that has ended
- `GET    /api/calendar/{year}/{month}` → `{"YYYY-MM-DD": count, ...}`
- `POST   /api/recurring/run` → instantiate due recurring notes now
- `GET    /api/export.zip` → download the vault as a zip
- `GET    /api/calendar.ics` → RFC 5545 feed for calendar subscription
- `GET    /api/config` → which vault and model this instance is using
- `POST   /api/rebuild-index` (rebuild DB from .md files)

Workspaces (a note with a `path:`):

- `GET    /api/workspaces` → every workspace note and what its folder is *now*,
  plus `summary` and the sentence the dashboard card shows
- `GET    /api/workspaces/{id}` → one, with `tools` (which opener binaries exist)
- `POST   /api/workspaces/{id}/open` `{what: "editor"|"terminal"|"files"}` →
  runs the allowlisted binary and answers with the argv; `403` without the app's
  own `X-Nookboard-Action` header, `400` for anything not in the list, `409` when
  the folder is not there

Bookmarks (a note with a `url:`):

- `GET    /api/bookmarks` → the addresses, grouped by each note's first tag and
  ordered here rather than in the browser, plus a count and how many of them a
  browser could not open. No network: this one only reads the vault
- `POST   /api/bookmarks/check` → asks every usable address whether it is answering,
  in parallel, each bounded by a 2.5s timeout, and answers with a kind
  (`up`/`answered`/`down`/`unknown`), the status, the reason in words, how long it
  took, and **when the check was taken**. The only endpoint here that makes requests
  outward

History (git, in the vault, off until you ask):

- `GET    /api/history` → `on`, the recent changes, the notes that are gone, the
  files that changed but are not recorded yet, and the sentence the view leads
  with
- `POST   /api/history/init` → turns it on: a repository, a `.gitignore`, and one
  commit of the vault as it stands
- `POST   /api/history/checkpoint` → records everything that has changed, now
- `GET    /api/history/{note-id}` → that note's versions, newest first; the newest
  carries `is_now`, measured against the file on disk
- `POST   /api/history/restore` `{path, rev}` → writes one file back from one old
  version and records that it was restored; `400` for a revision this app cannot
  read, or a path outside the vault

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

Transcription (all local: ffmpeg → whisper.cpp → the local model). A job is
minutes of work, so it is started and then polled:

- `GET    /api/transcribe` → the resolved engine (`cli`, `models`, `ffmpeg`,
  `ready`, `problems`), the model choices, the collections, the accepted
  extensions, and the recent jobs
- `POST   /api/transcribe` `{path, model?, collection?, summarize?}` → a job, or
  `400` if the path is not there, or `409` naming every reason the engine is not
  ready
- `POST   /api/transcribe/upload?name=&model=&collection=&summarize=` → the body
  *is* the file. No multipart: the browser already holds the bytes (a Blob from
  the picker, or the webm the recorder just made), so nothing takes them apart
  again. Kept in `vault/.audio/`, `400` for a type it cannot read or an empty body
- `GET    /api/transcribe/{job_id}` → one job; `404` if it is gone, which after a
  restart it is
- `POST   /api/transcribe/summarize` `{id}` → a job that rewrites one note's
  summary from the transcript already in it

A job is `{id, source, state, progress, message, note_id, error, model,
summarize, only_summary, keep, duration_s, elapsed_s}`. States: `queued`,
`probing`, `extracting`, `transcribing`, `summarising`, `done`, `failed`.
`progress` is a real fraction — whisper's own `progress = N%` off stderr, mapped
onto the job — because a bar that lies is worse than no bar.

## Where data lives

```
vault/
  .index.sqlite       # SQLite index (rebuilt from .md files if missing)
  .audio/             # recordings you uploaded or made in the app
  inbox/<id>.md
  home/<id>.md
  work/<id>.md
  transcripts/<id>.md # one per recording
```

`.audio/` is dot-prefixed on purpose: `Vault.collections()` lists every
directory, so a plain `audio/` would be offered as an empty notes collection and
look like somewhere to put notes. It is a folder of media sitting beside the
notes, and nothing that walks the vault reads it.

Markdown on disk is the source of truth. The SQLite index powers
search and calendar aggregation. Delete `vault/.index.sqlite` and
restart — the app rebuilds it from the `.md` files.

Two tables in it are *not* rebuilt from the files, because they record what has
already happened rather than what is: `daily_summary_state` and
`weekly_summary_state` (which days and weeks have been summarised, and what
failed), and `recurrence_state` (when a recurring note last ran). Deleting the
index therefore costs you those memories, not your notes — a day that was
already summarised may be summarised again.

The index is additive-only: columns introduced later (the board's `stage`,
`blocked_by`, `position`) are `ALTER TABLE`-ed in on open, so an index written by
an older build still works instead of raising on every read.

A task note's frontmatter carries its board state:

```yaml
at: 14:30             # optional; "HH:MM" 24h. Names an instant, not a day
until: 16:00          # optional; an hour later if empty
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
make test        # 811 pytest — model, vault, obsidian, foreign-vault, db, api,
                 #              backlinks, tags, recurring, export, ics, llm, ai,
                 #              deps (graph/order), board (columns/blockers/moves),
                 #              mood (series/streaks/collapse/coercion),
                 #              daily (stamping/sections/scheduling/recap),
                 #              weekly (ISO weeks/scheduling/rollup/fences),
                 #              templates (placeholders/titles/applying),
                 #              query (periods/day links/completed/refusals),
                 #              transcript (the whisper report, the ffprobe
                 #              JSON, paragraph grouping, the fence, finding the
                 #              tools), summary (chunking, prompts, the reply's
                 #              shape), transcribe API (the endpoints),
                 #              schedule (what a time means: parsing, labels,
                 #              lateness, the YAML shapes a hand-written file
                 #              can hold),
                 #              workspace (what a folder is: git reads, nesting,
                 #              ages, markers, what may be opened and where) and
                 #              the workspace API (the refusals: no header, bad
                 #              tool, gone folder, a file from outside); history
                 #              (what a commit message says, reading a log back,
                 #              the parent a deleted note is restored from, what
                 #              a path may be, and a real repository for the rest:
                 #              moves, restores, checkpoints, unrecorded work),
                 #              frontmatter extras (keys this app does not own:
                 #              carried through a round trip, nested values kept,
                 #              the app's own fields winning, JSON-safe output)
make test-js     # 192 node:test — rapid-log parsing, calendar maths, wikilinks,
                 #              ISO week labels, display helpers, board helpers,
                 #              mood grid helpers, query fences (finding them,
                 #              splicing answers, leaving other languages alone),
                 #              transcribe wording (states, progress, durations,
                 #              the recorder's types), the editor's time wording (a time
                 #              with no date fires nothing), the workspace card's
                 #              wording (branch drift, a subject with no age on it,
                 #              a disabled opener that names its missing tool),
                 #              the history wording (a clock for today against a
                 #              distance for older, a restore that says what it
                 #              will write), and that every local import exists
make test-tz     # the same JS suite under UTC, UTC+14, UTC-11 and America/New_York
./tools/check_render.sh   # 180 DOM assertions in headless Chromium
```

`make test` and `make test-js` cover logic; `check_render.sh` covers whether
the front end actually painted. `make check` runs all of it.

`check_render.sh` seeds its own fixture notes through the API and deletes them
afterwards, so it does not depend on what happens to be in your vault. Its
history section asserts whichever state the vault is in — off, on, or the endpoint
missing — rather than turning history on to make the assertions easy, because
turning it on writes a `.git` into a notes folder and that is the user's call.

**Date logic must be tested in more than one timezone.** `test-tz` exists
because a `toISOString()`-based date helper passes on a machine in EDT and is
wrong by a day everywhere else — see `localIsoDate()` in `static/js/entry.js`.
Any new date code should use that helper, never `toISOString()`.

**LLM code is tested against a real HTTP server emitting real SSE framing**,
not a mocked transport (see `tests/conftest.py`). Mocking the transport would
not have caught the two behaviours that actually matter: that reasoning is
separate from content, and that a spent budget yields empty content with a
200 response.