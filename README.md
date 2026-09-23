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

## API

- `GET    /api/health`
- `GET    /api/collections`
- `GET    /api/notes?collection=&date=`
- `GET    /api/notes/{id}`
- `POST   /api/notes`
- `PATCH  /api/notes/{id}`
- `DELETE /api/notes/{id}`

## Stack

- Python 3.13, FastAPI, uvicorn
- python-frontmatter for note serialization
- Vanilla JS, no build step, no framework
- Catppuccin Mocha theme