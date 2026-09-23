"""File-backed vault storage with optional SQLite index.

Each note lives at <root>/<collection>/<id>.md as Markdown with
YAML frontmatter. The vault creates collection directories on demand.

If a Database is provided, writes/deletes are mirrored into the index.
The Markdown file remains the source of truth; the DB is an index.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, TYPE_CHECKING

from .models import Note

if TYPE_CHECKING:
    from .db import Database


class Vault:
    def __init__(self, root: Path, db: "Database | None" = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db

    def _path(self, note_id: str, collection: str) -> Path:
        safe_id = note_id.replace("/", "_")
        safe_col = collection.replace("/", "_")
        d = self.root / safe_col
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_id}.md"

    def write(self, note: Note) -> Path:
        p = self._path(note.id, note.collection)
        p.write_text(note.to_markdown())
        if self.db is not None:
            self.db.upsert(note, mood=note.mood)
        return p

    def read(self, note_id: str) -> Note:
        # Search all collections (slow but correct for v1).
        for md in self.root.rglob("*.md"):
            try:
                n = Note.from_markdown(md.read_text())
            except Exception:
                continue
            if n.id == note_id:
                return n
        raise KeyError(note_id)

    def delete(self, note_id: str) -> None:
        try:
            n = self.read(note_id)
        except KeyError:
            return
        (self.root / n.collection / f"{n.id}.md").unlink(missing_ok=True)
        if self.db is not None:
            self.db.delete(note_id)

    def list_all(self) -> list[Note]:
        out: list[Note] = []
        for md in self.root.rglob("*.md"):
            try:
                out.append(Note.from_markdown(md.read_text()))
            except Exception:
                continue
        return out

    def collections(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())