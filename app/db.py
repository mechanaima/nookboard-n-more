"""SQLite index for nookboard notes.

Markdown remains the source of truth. This module maintains a denormalized
index that powers search, calendar aggregation, mood breakdown, tags,
backlinks, and the recurring-note scheduler.

The schema lives in SCHEMA below; rebuilding from .md files is done by
app.main on first boot if the DB file is missing or empty.
"""
from __future__ import annotations

import re
from pathlib import Path
from datetime import date, timedelta
from typing import Iterable

import sqlite3

from .models import Note, Signifier, Status
from .obsidian import wikilink_targets


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    collection  TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    signifier   TEXT NOT NULL,
    status      TEXT NOT NULL,
    dates_csv   TEXT NOT NULL DEFAULT '',
    parent_id   TEXT,
    created     TEXT NOT NULL,
    mood        TEXT,
    tags_csv    TEXT NOT NULL DEFAULT '',
    recurrence  TEXT,
    search_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_collection ON notes(collection);
CREATE TABLE IF NOT EXISTS note_links (
    source_id    TEXT NOT NULL,
    target_title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_target ON note_links(target_title);
CREATE TABLE IF NOT EXISTS recurrence_state (
    note_id  TEXT PRIMARY KEY,
    last_run TEXT NOT NULL
);
"""


def _extract_wikilink_titles(body: str) -> list[str]:
    return wikilink_targets(body)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert(self, n: Note, mood: str | None = None) -> None:
        search_text = f"{n.title}\n{n.body}".lower()
        tags_csv = ",".join(n.tags)
        self.conn.execute(
            """
            INSERT INTO notes (id, collection, title, body, signifier, status,
                               dates_csv, parent_id, created, mood, tags_csv,
                               recurrence, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                collection=excluded.collection,
                title=excluded.title,
                body=excluded.body,
                signifier=excluded.signifier,
                status=excluded.status,
                dates_csv=excluded.dates_csv,
                parent_id=excluded.parent_id,
                created=excluded.created,
                mood=COALESCE(excluded.mood, notes.mood),
                tags_csv=excluded.tags_csv,
                recurrence=excluded.recurrence,
                search_text=excluded.search_text
            """,
            (
                n.id, n.collection, n.title, n.body,
                n.signifier.value, n.status.value,
                ",".join(d.isoformat() for d in n.dates),
                n.parent_id, n.created.isoformat(),
                mood, tags_csv, n.recurrence, search_text,
            ),
        )
        # Update backlink index: drop old, re-insert from current body.
        self.conn.execute("DELETE FROM note_links WHERE source_id = ?", (n.id,))
        targets = _extract_wikilink_titles(n.body)
        if targets:
            self.conn.executemany(
                "INSERT INTO note_links (source_id, target_title) VALUES (?, ?)",
                [(n.id, t) for t in targets],
            )
        # Ensure a recurrence_state row exists.
        self.conn.execute(
            "INSERT OR IGNORE INTO recurrence_state (note_id, last_run) VALUES (?, ?)",
            (n.id, n.created.isoformat()),
        )
        self.conn.commit()

    def set_mood(self, note_id: str, mood: str | None) -> None:
        self.conn.execute(
            "UPDATE notes SET mood = ? WHERE id = ?",
            (mood, note_id),
        )
        self.conn.commit()

    def delete(self, note_id: str) -> None:
        self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.execute("DELETE FROM note_links WHERE source_id = ?", (note_id,))
        self.conn.execute("DELETE FROM recurrence_state WHERE note_id = ?", (note_id,))
        self.conn.commit()

    def get(self, note_id: str) -> Note | None:
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return None
        return self._row_to_note(row)

    def search(self, q: str) -> list[Note]:
        if not q.strip():
            return []
        like = f"%{q.strip().lower()}%"
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE search_text LIKE ? ORDER BY created DESC",
            (like,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def all_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM notes").fetchall()]

    def month_counts(self, year: int, month: int) -> dict[str, int]:
        prefix = f"{year:04d}-{month:02d}-"
        rows = self.conn.execute("SELECT dates_csv FROM notes").fetchall()
        out: dict[str, int] = {}
        for row in rows:
            for d in (row["dates_csv"] or "").split(","):
                if d.startswith(prefix):
                    out[d] = out.get(d, 0) + 1
        return out

    def filter_by_tag(self, tag: str) -> list[Note]:
        like = f"%{tag}%"
        rows = self.conn.execute(
            "SELECT * FROM notes WHERE ',' || tags_csv || ',' LIKE ? ORDER BY created DESC",
            (like,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def backlinks_for_title(self, title: str) -> list[Note]:
        rows = self.conn.execute(
            "SELECT n.* FROM notes n JOIN note_links l ON l.source_id = n.id "
            "WHERE l.target_title = ? ORDER BY n.created DESC",
            (title,),
        ).fetchall()
        return [self._row_to_note(r) for r in rows]

    def run_recurring(self, today: date) -> list[Note]:
        """For each note with recurrence, instantiate due dates up to `today`.

        Returns the list of newly-created Note instances. The caller
        is responsible for writing them to the vault (file system).
        """
        created: list[Note] = []
        rows = self.conn.execute(
            "SELECT n.*, COALESCE(s.last_run, n.created) AS last_run "
            "FROM notes n LEFT JOIN recurrence_state s ON s.note_id = n.id "
            "WHERE n.recurrence IS NOT NULL AND n.recurrence != ''"
        ).fetchall()
        for row in rows:
            real = self._row_to_note(row)
            last_run = date.fromisoformat(row["last_run"])
            if not real.recurrence:
                continue
            while True:
                nxt = _next_instance(last_run, real.recurrence)
                if not nxt or nxt > today:
                    break
                new_id = f"{real.id}-{nxt.isoformat()}"
                if self.get(new_id) is not None:
                    last_run = nxt
                    continue
                instance = Note(
                    id=new_id,
                    collection=real.collection,
                    title=f"{real.title} ({nxt.isoformat()})",
                    body=real.body,
                    signifier=real.signifier,
                    status=Status.OPEN,
                    dates=[nxt],
                    parent_id=real.id,
                    created=nxt,
                    mood=None,
                    tags=[],
                )
                # Caller writes the file; we just bump state and return the note.
                self.conn.execute(
                    "UPDATE recurrence_state SET last_run = ? WHERE note_id = ?",
                    (nxt.isoformat(), real.id),
                )
                self.conn.commit()
                created.append(instance)
                last_run = nxt
        return created

    def rebuild_from(self, notes: Iterable[Note]) -> None:
        self.conn.executescript("DELETE FROM notes; DELETE FROM note_links; DELETE FROM recurrence_state;")
        for n in notes:
            self.upsert(n)

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        dates_csv = row["dates_csv"] or ""
        dates = [date.fromisoformat(d) for d in dates_csv.split(",") if d]
        tags_csv = row["tags_csv"] or ""
        tags = [t for t in tags_csv.split(",") if t]
        return Note(
            id=row["id"],
            collection=row["collection"],
            title=row["title"],
            body=row["body"],
            signifier=Signifier(row["signifier"]),
            status=Status(row["status"]),
            dates=dates,
            parent_id=row["parent_id"],
            created=date.fromisoformat(row["created"]),
            mood=row["mood"],
            tags=tags,
            recurrence=row["recurrence"],
        )


def _next_instance(last: date, kind: str) -> date | None:
    if kind == "daily":
        return last + timedelta(days=1)
    if kind == "weekly":
        return last + timedelta(days=7)
    if kind == "monthly":
        from calendar import monthrange
        year = last.year + (1 if last.month == 12 else 0)
        month = 1 if last.month == 12 else last.month + 1
        day = min(last.day, monthrange(year, month)[1])
        return date(year, month, day)
    return None