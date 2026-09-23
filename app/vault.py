"""File-backed vault storage.

Each note lives at <root>/<collection>/<id>.md as Markdown with
YAML frontmatter. The vault creates collection directories on demand.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import Note


class Vault:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, note_id: str, collection: str) -> Path:
        safe_id = note_id.replace("/", "_")
        safe_col = collection.replace("/", "_")
        d = self.root / safe_col
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_id}.md"

    def write(self, note: Note) -> Path:
        p = self._path(note.id, note.collection)
        p.write_text(note.to_markdown())
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