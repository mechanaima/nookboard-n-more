"""File-backed vault storage with optional SQLite index.

nookboard's own layout is `<collection>/<id>.md`. A vault you point us at may
not follow that at all: Obsidian vaults are arbitrary folder trees and files
are usually named after the note. So a Note remembers the vault-relative path
it came from (`source_rel`) and writes go back to that exact path. Without
that, editing a note in a foreign vault would silently relocate it into a
collection directory.

If a Database is provided, writes/deletes are mirrored into the index.
The Markdown file remains the source of truth; the DB is an index.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .models import Note

if TYPE_CHECKING:
    from .db import Database


class Vault:
    def __init__(self, root: Path, db: "Database | None" = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db

    # -- path resolution ----------------------------------------------------

    def _managed_path(self, note_id: str, collection: str) -> Path:
        """nookboard's own layout: one directory per collection."""
        safe_id = note_id.replace("/", "_")
        safe_col = (collection or "inbox").replace("/", "_")
        d = self.root / safe_col
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{safe_id}.md"

    def _path_for(self, note: Note) -> Path:
        """Where this note lives: its original path, or the managed layout."""
        source_rel = getattr(note, "source_rel", None)
        if source_rel:
            return self.root / source_rel
        return self._managed_path(note.id, note.collection)

    def _load(self, md: Path) -> Note | None:
        """Parse one file, tagging it with its location. None if unparseable."""
        try:
            note = Note.from_markdown(md.read_text(), fallback_id=md.stem)
        except Exception:
            return None
        rel = md.relative_to(self.root)
        # A nested file's top folder acts as its collection, which is how an
        # Obsidian folder tree maps onto nookboard's idea of collections.
        collection = rel.parts[0] if len(rel.parts) > 1 else note.collection
        return replace(note, collection=collection, source_rel=str(rel))

    # -- api ----------------------------------------------------------------

    def write(self, note: Note) -> Path:
        """Write a note, moving the file when its collection has changed.

        `source_rel` normally sends a write back to the file the note came from,
        which is what makes editing a vault you did not create non-destructive.
        But a note's collection *is* its folder here, and on read the folder wins
        (`_load`). So a changed collection that did not move the file left the
        frontmatter saying one thing and the folder saying another, with the
        folder believed -- the collection dropdown appeared to save and then
        silently reverted on the next read.

        Only nested files move: a file sitting at the vault root has no folder to
        defer to, so its frontmatter collection stands and there is nothing to
        reconcile.
        """
        p = self._path_for(note)
        stale: Path | None = None
        if note.source_rel and len(Path(note.source_rel).parts) > 1:
            wanted = self._managed_path(note.id, note.collection)
            if (self.root / note.source_rel) != wanted:
                stale, p = self.root / note.source_rel, wanted

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(note.to_markdown())
        # After the write, never before: a crash between the two must not be able
        # to lose the note.
        if stale is not None and stale != p and stale.exists():
            stale.unlink()
        if self.db is not None:
            self.db.upsert(note)
        return p

    def read(self, note_id: str) -> Note:
        for md in self.root.rglob("*.md"):
            note = self._load(md)
            if note is not None and note.id == note_id:
                return note
        raise KeyError(note_id)

    def delete(self, note_id: str) -> None:
        try:
            note = self.read(note_id)
        except KeyError:
            return
        self._path_for(note).unlink(missing_ok=True)
        if self.db is not None:
            self.db.delete(note_id)

    def list_all(self) -> list[Note]:
        out: list[Note] = []
        for md in self.root.rglob("*.md"):
            note = self._load(md)
            if note is not None:
                out.append(note)
        return out

    def collections(self) -> list[str]:
        cols = {n.collection for n in self.list_all()}
        cols.update(p.name for p in self.root.iterdir() if p.is_dir())
        return sorted(c for c in cols if c and not c.startswith("."))
