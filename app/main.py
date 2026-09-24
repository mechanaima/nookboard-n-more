"""FastAPI application factory."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from .models import Note, Signifier, Status
from .vault import Vault
from .db import Database
from .ics import notes_to_ics
from .config import Settings, load_settings
from . import ai
from .llm import LLMError, LlamaCpp


class NoteIn(BaseModel):
    id: str
    collection: str = "inbox"
    title: str
    body: str = ""
    signifier: Signifier = Signifier.NOTE
    status: Status = Status.OPEN
    dates: list[date] = Field(default_factory=list)
    parent_id: Optional[str] = None
    mood: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    recurrence: Optional[str] = None


def create_app(vault_root: Path | None = None, settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    root = Path(vault_root) if vault_root else cfg.vault
    db = Database(root / ".index.sqlite")
    vault = Vault(root, db=db)

    # First-boot rebuild: if DB is empty but vault has files, rebuild index.
    if not db.all_ids() and any(root.rglob("*.md")):
        db.rebuild_from(vault.list_all())

    # Materialize any due recurring notes at startup.
    for n in db.run_recurring(date.today()):
        vault.write(n)

    app = FastAPI(title="nookboard")
    app.state.settings = cfg
    app.state.vault = vault
    app.state.db = db

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        """What this instance is pointed at. Handy when a vault looks empty,
        and the only way to tell from outside whether a running server picked
        up a settings change."""
        return {
            "vault": str(root),
            "note_count": len(vault.list_all()),
            "llm_url": cfg.llm_url,
            "llm_model": cfg.llm_model,
            "llm_max_tokens": cfg.llm_max_tokens,
            "llm_timeout": cfg.llm_timeout,
        }

    @app.get("/api/collections")
    def list_collections():
        return vault.collections()

    @app.get("/api/notes")
    def list_notes(collection: Optional[str] = None, date: Optional[date] = None,
                   tag: Optional[str] = None):
        notes = vault.list_all()
        if collection:
            notes = [n for n in notes if n.collection == collection]
        if date:
            notes = [n for n in notes if date in n.dates]
        if tag:
            notes = [n for n in notes if tag in n.tags]
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
            mood=payload.mood,
            tags=payload.tags,
            recurrence=payload.recurrence,
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
            mood=payload.get("mood", existing.mood),
            tags=payload.get("tags", existing.tags),
            recurrence=payload.get("recurrence", existing.recurrence),
            created=existing.created,
        )
        vault.write(updated)
        return updated.to_dict()

    @app.delete("/api/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        vault.delete(note_id)
        return None

    @app.get("/api/search")
    def search(q: str = ""):
        return [n.to_dict() for n in db.search(q)]

    @app.get("/api/calendar/{year}/{month}")
    def calendar(year: int, month: int):
        return db.month_counts(year, month)

    @app.get("/api/notes/{note_id}/backlinks")
    def get_backlinks(note_id: str):
        try:
            n = vault.read(note_id)
        except KeyError:
            raise HTTPException(404, "note not found")
        return [b.to_dict() for b in db.backlinks_for_title(n.title)]

    @app.post("/api/recurring/run")
    def trigger_recurring():
        today = date.today()
        created = db.run_recurring(today)
        # Write the new instances to the vault (file system).
        for n in created:
            vault.write(n)
        return {"created": [n.to_dict() for n in created]}

    @app.get("/api/export.zip")
    def export_zip():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for md in sorted(vault.root.rglob("*.md")):
                z.write(md, md.relative_to(vault.root))
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="nookboard-vault.zip"'},
        )

    @app.get("/api/calendar.ics")
    def calendar_ics():
        body = notes_to_ics(vault.list_all())
        return Response(
            content=body,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="nookboard.ics"'},
        )

    @app.post("/api/rebuild-index", status_code=200)
    def rebuild_index():
        notes = vault.list_all()
        db.rebuild_from(notes)
        return {"rebuilt": len(notes)}

    # -- local inference (llama.cpp) ----------------------------------------
    #
    # Everything here streams as NDJSON: one JSON object per line, so the
    # browser can render progress while the model is still thinking. The model
    # is slow enough (roughly 26 tok/s) that a blocking response is not usable.

    llm = LlamaCpp(
        cfg.llm_url,
        cfg.llm_model,
        max_tokens=cfg.llm_max_tokens,
        timeout=cfg.llm_timeout,
    )

    def _line(obj: dict) -> bytes:
        return (json.dumps(obj) + "\n").encode()

    def _ai_stream(messages: list[dict], finish=None):
        async def gen():
            buf: list[str] = []
            try:
                async for ev in llm.stream(messages):
                    if ev.kind == "content":
                        buf.append(ev.text)
                        yield _line({"kind": "content", "text": ev.text})
                    elif ev.kind == "reasoning":
                        yield _line({"kind": "reasoning", "text": ev.text})
                    elif ev.kind == "truncated":
                        yield _line({"kind": "error", "message": ev.text})
                        return
                if finish is not None:
                    yield _line({"kind": "result", **finish("".join(buf))})
                yield _line({"kind": "done"})
            except LLMError as exc:
                yield _line({"kind": "error", "message": str(exc)})
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    def _require_note(note_id):
        if not note_id:
            raise HTTPException(400, "id required")
        try:
            return vault.read(str(note_id))
        except KeyError:
            raise HTTPException(404, "note not found")

    def _link_candidates(note, limit: int = 40) -> list[str]:
        """Titles worth offering the model, best term-overlap first."""
        others = [n for n in vault.list_all() if n.id != note.id]
        ranked = ai.select_relevant(others, f"{note.title} {note.body or ''}", limit=limit)
        titles = [n.title for n in ranked if n.title]
        if not titles:
            titles = [n.title for n in others[:limit] if n.title]
        return titles

    @app.post("/api/ai/summarize")
    def ai_summarize(payload: dict):
        note = _require_note(payload.get("id"))
        return _ai_stream(ai.build_summary_messages(note))

    @app.post("/api/ai/tags")
    def ai_tags(payload: dict):
        note = _require_note(payload.get("id"))
        vault_tags = sorted({t for n in vault.list_all() for t in n.tags})
        return _ai_stream(
            ai.build_tags_messages(note, vault_tags),
            finish=lambda text: {"tags": ai.parse_tag_suggestions(text, note.tags)},
        )

    @app.post("/api/ai/links")
    def ai_links(payload: dict):
        note = _require_note(payload.get("id"))
        candidates = _link_candidates(note)
        return _ai_stream(
            ai.build_links_messages(note, candidates),
            finish=lambda text: {
                "links": ai.parse_link_suggestions(text, candidates, exclude=note.title)
            },
        )

    @app.post("/api/ai/ask")
    def ai_ask(payload: dict):
        question = (payload.get("question") or "").strip()
        if not question:
            raise HTTPException(400, "question required")
        context = ai.select_relevant(vault.list_all(), question)
        return _ai_stream(
            ai.build_ask_messages(question, context),
            finish=lambda text: {"notes": [n.to_dict() for n in context]},
        )

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