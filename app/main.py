"""FastAPI application factory."""
from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .models import Note, Signifier, Status
from .vault import Vault


class NoteIn(BaseModel):
    id: str
    collection: str = "inbox"
    title: str
    body: str = ""
    signifier: Signifier = Signifier.NOTE
    status: Status = Status.OPEN
    dates: list[date] = Field(default_factory=list)
    parent_id: Optional[str] = None


def create_app(vault_root: Path | None = None) -> FastAPI:
    root = Path(vault_root) if vault_root else Path(__file__).resolve().parent.parent / "vault"
    vault = Vault(root)

    app = FastAPI(title="nookboard")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/collections")
    def list_collections():
        return vault.collections()

    @app.get("/api/notes")
    def list_notes(collection: Optional[str] = None, date: Optional[date] = None):
        notes = vault.list_all()
        if collection:
            notes = [n for n in notes if n.collection == collection]
        if date:
            notes = [n for n in notes if date in n.dates]
        return [n.to_dict() for n in notes]

    @app.get("/api/notes/{note_id}")
    def get_note(note_id: str):
        try:
            return vault.read(note_id).to_dict()
        except KeyError:
            raise HTTPException(404, "note not found")

    @app.post("/api/notes", status_code=201)
    def create_note(payload: NoteIn):
        note = Note(
            id=payload.id,
            collection=payload.collection,
            title=payload.title,
            body=payload.body,
            signifier=payload.signifier,
            status=payload.status,
            dates=payload.dates,
            parent_id=payload.parent_id,
            created=date.today(),
        )
        vault.write(note)
        return note.to_dict()

    @app.patch("/api/notes/{note_id}")
    def update_note(note_id: str, payload: dict):
        try:
            existing = vault.read(note_id)
        except KeyError:
            raise HTTPException(404, "note not found")
        # Note is frozen — build a new instance with overrides applied.
        new_dates = existing.dates
        if "dates" in payload:
            new_dates = [date.fromisoformat(d) for d in payload["dates"]]
        updated = Note(
            id=existing.id,
            collection=payload.get("collection", existing.collection),
            title=payload.get("title", existing.title),
            body=payload.get("body", existing.body),
            signifier=Signifier(payload.get("signifier", existing.signifier.value)),
            status=Status(payload.get("status", existing.status.value)),
            dates=new_dates,
            parent_id=payload.get("parent_id", existing.parent_id),
            created=existing.created,
        )
        vault.write(updated)
        return updated.to_dict()

    @app.delete("/api/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        try:
            n = vault.read(note_id)
        except KeyError:
            raise HTTPException(404, "note not found")
        (vault.root / n.collection / f"{n.id}.md").unlink(missing_ok=True)
        return None

    # Static front-end
    static_dir = Path(__file__).resolve().parent.parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        idx = static_dir / "index.html"
        return FileResponse(idx)

    return app


# Module-level app for `uvicorn app.main:app`
app = create_app()