# nookboard

nookboard is a local-first, file-backed personal notes / tasks / journal web app. It combines three iOS-app workflow primitives in one UI:

- **NeatNook**: curated long-form note collections
- **Agenda**: date-tagged timeline entries
- **BuJo**: rapid-log bullets with task `•`, event `○`, and note `–` migration

## Stack

- Python >= 3.13
- FastAPI
- Vanilla JavaScript (no frontend framework)
- Plain Markdown + YAML frontmatter as source of truth
- `.index.sqlite` is a disposable index rebuilt from `.md` files
- Pytest for tests
- Built with `uv` + `uv.lock`

## Core architecture rules

- Markdown files on disk are the source of truth.
- The browser is a view over files; the index is throwaway.
- A collection is a folder; for nested files, the folder determines the collection.
- Editing `collection:` in frontmatter by hand does nothing.
- Moving collections means moving the file (the API handles this).
- `created` and `completed` are app-owned fields:
  - `POST` mints `created`
  - `PATCH` stamps `completed` on transition into complete
  - input values for these fields are ignored (not merged)
- There is intentionally no `modified` field.
- Recency means `created`, `completed`, or a note's own dates — never filesystem timestamps.
- Single-user, localhost-only, no authentication.

## Module map (`app/`)

- `main.py` (root)
- `vault.py`, `models.py` (storage)
- `db.py` (index)
- `ai.py`, `llm.py` (AI/LLM glue)
- Feature modules: `bookmarks`, `daily`, `history`, `home`, `insight`, `mood`, `obsidian`, `query`, `schedule`, `sections`, `templates`, `transcribe`, `weekly`, `workspace`
- Helpers: health / ICS / bookmark / note-icon

## Runtime

- Runs as a systemd user service on boot at `127.0.0.1:8765`.
- Use the app's own `make dev`; do not kill that service.
- Scratch copies run on `127.0.0.1:8796`.

## Query grammar

`GET /api/query?q=...` resolves a query language, for example:

- open tasks in `#work`
- completed this week
- show notes in `#mood` done
- on `<ISO>`
- in `<YYYY-Www>`

Rules:

- `#` marks a tag
- `done` is exactly `complete`
- unknown names/refusals are surfaced with alternatives

## Current live state

- 59 notes across 8 collections:
  - school 27
  - juneanalytics 11
  - inbox 6
  - workspaces 5
  - bookmarks 3
  - daily 3
  - templates 2
  - weekly 1
  - work 1
- 32 open tasks

## Commands

```bash
make dev      # http://127.0.0.1:8765 (--reload, for working on the app)
make test     # pytest
make test-js
./tools/install-service.sh [--remove]   # boot user-service on 127.0.0.1:8765
```

Operational note: do not infer “recently edited” — no such field exists.
