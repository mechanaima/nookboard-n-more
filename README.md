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
#/view/calendar             calendar tab
#/note/<id>                 a specific note
#/view/timeline/note/<id>   a note with the timeline tab selected
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

### Visual verification

The UI is checked headlessly, not by eyeball alone:

```bash
./tools/check_render.sh 'http://127.0.0.1:8765/#/note/<id>'   # 21 DOM assertions
./tools/shot.sh /tmp/shot.png 'http://127.0.0.1:8765/'        # screenshot
```

`check_render.sh` loads the page in headless Chromium, dumps the post-JS DOM
and asserts that entries, counts, the tab ink, both editor panes, the rendered
markdown, tag chips, backlinks and the calendar legend all actually rendered —
so a JS exception fails the check instead of silently producing a blank pane.
`shot.sh` uses a throwaway `--user-data-dir`, which sidesteps the profile lock
that blocks screenshotting while a normal browser session is open.

## Where data lives

```
vault/
  inbox/
    <id>.md
  home/
    <id>.md
  work/
    <id>.md
```

Each file is plain Markdown with YAML frontmatter. You can edit by hand,
commit to git, etc. The server is the index, not the source of truth.

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

## Features (v3)

- **Rapid Log** — type `• task`, `○ event`, `– note` and hit Enter
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
- `GET    /api/calendar/{year}/{month}` → `{"YYYY-MM-DD": count, ...}`
- `POST   /api/recurring/run` → instantiate due recurring notes now
- `GET    /api/export.zip` → download the vault as a zip
- `GET    /api/calendar.ics` → RFC 5545 feed for calendar subscription
- `POST   /api/rebuild-index` (rebuild DB from .md files)

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

## Stack

- Python 3.13, FastAPI, uvicorn
- python-frontmatter for note serialization
- SQLite (stdlib) for the search / calendar index
- Vanilla JS, no build step, no framework
- `marked` (vendored, MIT) for Markdown rendering
- Catppuccin Mocha theme, hand-written CSS

## Tests

```bash
make test        # 33 pytest  — model, vault, db, api, backlinks, tags, recurring, export, ics
make test-js     # 34 node:test — rapid-log parsing, calendar maths, wikilinks, display helpers
make test-tz     # the same JS suite under UTC, UTC+14, UTC-11 and America/New_York
./tools/check_render.sh   # 21 DOM assertions in headless Chromium
```

`make test` and `make test-js` cover logic; `check_render.sh` covers whether
the front end actually painted. `make check` runs all of it.

**Date logic must be tested in more than one timezone.** `test-tz` exists
because a `toISOString()`-based date helper passes on a machine in EDT and is
wrong by a day everywhere else — see `localIsoDate()` in `static/js/entry.js`.
Any new date code should use that helper, never `toISOString()`.