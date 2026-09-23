"""SQLite index for nookboard notes.

Markdown remains the source of truth. This module maintains a denormalized
index that powers search, calendar aggregation, and mood breakdown.

The schema lives in SCHEMA below; rebuilding from .md files is done by
app.main on first boot if the DB file is missing or empty.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Iterable

import sqlite3

from .models import Note, Signifier, Status


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
    search_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_collection ON notes(collection);
"""


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
        self.conn.execute(
            """
            INSERT INTO notes (id, collection, title, body, signifier, status,
                               dates_csv, parent_id, created, mood, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                search_text=excluded.search_text
            """,
            (
                n.id, n.collection, n.title, n.body,
                n.signifier.value, n.status.value,
                ",".join(d.isoformat() for d in n.dates),
                n.parent_id, n.created.isoformat(),
                mood, search_text,
            ),
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

    def rebuild_from(self, notes: Iterable[Note]) -> None:
        self.conn.executescript("DELETE FROM notes;")
        for n in notes:
            self.upsert(n)

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        dates_csv = row["dates_csv"] or ""
        dates = [date.fromisoformat(d) for d in dates_csv.split(",") if d]
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
        )