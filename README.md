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

## Features (v2)

- **Rapid Log** — type `• task`, `○ event`, `– note` and hit Enter
- **Collections** — NeatNook-style curation, create + filter
- **Timeline** — Agenda-style date-filter view
- **Calendar** — month grid with per-day counts, click-through
- **Search** — topbar search box, live dropdown
- **Markdown preview** — split / write / preview tabs, live render
- **Mood tags** — emoji picker on every note
- **Migration** — task states: open → complete / migrated / scheduled / irrelevant

## API

- `GET    /api/health`
- `GET    /api/collections`
- `GET    /api/notes?collection=&date=`
- `GET    /api/notes/{id}`
- `POST   /api/notes`
- `PATCH  /api/notes/{id}`
- `DELETE /api/notes/{id}`
- `GET    /api/search?q=`
- `GET    /api/calendar/{year}/{month}` → `{"YYYY-MM-DD": count, ...}`
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
- Catppuccin Mocha theme