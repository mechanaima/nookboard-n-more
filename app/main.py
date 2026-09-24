"""FastAPI application factory."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from datetime import date
from typing import Optional

from dataclasses import replace

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from .deps import (
    STAGE_ORDER, board_summary, check_blockers, index_by_id, is_blocked,
    is_closed, next_position, normalize_blocked_by, plan_move, reconcile_move,
    resolve, sort_column, stage_of, blocking as blocking_notes,
)
from .models import (
    STAGE_LABELS, Note, Signifier, Stage, Status, reconcile, stage_for_status,
)
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
    stage: Optional[str] = None
    blocked_by: list[str] = Field(default_factory=list)
    position: Optional[float] = None


class MoveIn(BaseModel):
    """A card drop. `before_id` makes the move self-describing: the client says
    where the card landed, the server decides the ordering numbers."""
    id: str
    stage: str
    before_id: Optional[str] = None


class DepIn(BaseModel):
    blocker_id: str


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

    def _require_note(note_id):
        if not note_id:
            raise HTTPException(400, "id required")
        try:
            return vault.read(str(note_id))
        except KeyError:
            raise HTTPException(404, "note not found")

    def _index() -> tuple[list[Note], dict[str, Note]]:
        notes = vault.list_all()
        return notes, index_by_id(notes)

    def _coerce_enum(enum_cls, value, field):
        try:
            return enum_cls(value)
        except ValueError:
            raise HTTPException(400, f"unknown {field}: {value!r}")

    def _coerce_stage(value) -> Optional[str]:
        if value is None or value == "":
            return None
        return _coerce_enum(Stage, value, "stage").value

    def _card(note: Note, by_id: dict[str, Note]) -> dict:
        """One board card: the note plus everything the board needs to draw it
        without asking again — whether it is stuck, on what, and how much it
        in turn is holding up."""
        blockers = resolve(note.blocked_by, by_id)
        open_blockers = [b for b in blockers if not b["closed"]]
        card = note.to_dict()
        card["blocked"] = bool(open_blockers)
        card["blockers"] = blockers
        card["open_blockers"] = open_blockers
        card["blocking"] = len(blocking_notes(note.id, by_id.values()))
        return card

    def _deps_payload(note: Note, by_id: dict[str, Note]) -> dict:
        blockers = resolve(note.blocked_by, by_id)
        return {
            "id": note.id,
            "blocked": any(not b["closed"] for b in blockers),
            "blocked_by": blockers,
            "blocking": [c.to_dict() for c in blocking_notes(note.id, by_id.values())],
        }

    @app.post("/api/notes", status_code=201)
    def create_note(payload: NoteIn):
        stage, status = reconcile(_coerce_stage(payload.stage), payload.status)
        position = payload.position
        if position is None and payload.signifier is Signifier.TASK:
            # Give a new task its slot at the bottom of its column now: ordering
            # by `created` alone cannot separate tasks captured the same day.
            _, by_id = _index()
            position = next_position(by_id.values(), stage or stage_for_status(status).value)
        note = Note(
            id=payload.id,
            collection=payload.collection,
            title=payload.title,
            body=payload.body,
            signifier=payload.signifier,
            status=status,
            dates=payload.dates,
            parent_id=payload.parent_id,
            mood=payload.mood,
            tags=payload.tags,
            recurrence=payload.recurrence,
            stage=stage,
            blocked_by=[d for d in payload.blocked_by if d != payload.id],
            position=position,
            created=date.today(),
        )
        vault.write(note)
        return note.to_dict()

    @app.patch("/api/notes/{note_id}")
    def update_note(note_id: str, payload: dict):
        existing = _require_note(note_id)
        new_dates = existing.dates
        if "dates" in payload:
            new_dates = [date.fromisoformat(d) for d in payload["dates"]]

        # Dependencies are validated here rather than at the board level so any
        # caller — editor, import, script — gets the same cycle protection.
        blocked_by = existing.blocked_by
        if "blocked_by" in payload:
            _, by_id = _index()
            proposed = normalize_blocked_by(
                payload["blocked_by"], note_id=note_id, by_id=by_id
            )
            cycle = check_blockers(note_id, proposed, by_id)
            if cycle:
                raise HTTPException(409, "dependency cycle: " + " \u2192 ".join(cycle))
            blocked_by = proposed

        status = _coerce_enum(Status, payload.get("status", existing.status.value), "status")
        stage = _coerce_stage(payload.get("stage", existing.stage))
        stage, status = reconcile(stage, status)

        updated = replace(
            existing,
            collection=payload.get("collection", existing.collection),
            title=payload.get("title", existing.title),
            body=payload.get("body", existing.body),
            signifier=_coerce_enum(
                Signifier, payload.get("signifier", existing.signifier.value), "signifier"
            ),
            status=status,
            dates=new_dates,
            parent_id=payload.get("parent_id", existing.parent_id),
            mood=payload.get("mood", existing.mood),
            tags=payload.get("tags", existing.tags),
            recurrence=payload.get("recurrence", existing.recurrence),
            stage=stage,
            blocked_by=blocked_by,
            position=payload.get("position", existing.position),
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

    # -- board (kanban) + dependencies --------------------------------------
    #
    # The board is derived, never stored: columns come from each note's stage,
    # and blocked-ness is recomputed from the graph on every read. So completing
    # a blocker unblocks its dependents immediately, with nothing to sync.

    @app.get("/api/board")
    def board(collection: Optional[str] = None, tag: Optional[str] = None):
        notes, by_id = _index()
        if collection:
            notes = [n for n in notes if n.collection == collection]
        if tag:
            notes = [n for n in notes if tag in n.tags]

        columns = []
        for stage in STAGE_ORDER:
            cards = sort_column([n for n in notes if stage_of(n) == stage.value])
            columns.append({
                "id": stage.value,
                "label": STAGE_LABELS[stage],
                "count": len(cards),
                "cards": [_card(n, by_id) for n in cards],
            })

        stuck = sort_column([n for n in notes if is_blocked(n, by_id)])
        return {
            "columns": columns,
            "summary": board_summary(notes, by_id),
            "blocked": [_card(n, by_id) for n in stuck],
        }

    @app.post("/api/board/move")
    def move_card(payload: MoveIn):
        """Drop a card into a column, optionally before a given card.

        Positions are recomputed server-side and rewritten as clean integers, so
        the ordering in the Markdown stays readable and drags cannot drift into
        ever-smaller fractional gaps.
        """
        existing = _require_note(payload.id)
        stage = _coerce_enum(Stage, payload.stage, "stage")
        notes, by_id = _index()

        # A drop names its intent as a column, so the column wins over the
        # task's previous status (see reconcile_move).
        new_stage, new_status = reconcile_move(stage.value, existing.status)
        moved = replace(existing, stage=new_stage, status=new_status)

        target = sort_column([
            n for n in notes
            if n.id != moved.id and stage_of(n) == stage.value
        ])
        positions = plan_move(target, moved, payload.before_id)

        for note_id, pos in positions.items():
            note = moved if note_id == moved.id else by_id[note_id]
            vault.write(replace(note, position=pos))

        return {
            "id": moved.id,
            "stage": stage_of(moved),
            "status": moved.status.value,
            "positions": positions,
        }

    @app.get("/api/notes/{note_id}/deps")
    def get_deps(note_id: str):
        note = _require_note(note_id)
        _, by_id = _index()
        return _deps_payload(note, by_id)

    @app.post("/api/notes/{note_id}/deps")
    def add_dep(note_id: str, payload: DepIn):
        note = _require_note(note_id)
        blocker_id = str(payload.blocker_id or "").strip()
        if not blocker_id:
            raise HTTPException(400, "blocker_id required")
        if blocker_id == note_id:
            raise HTTPException(409, "a task cannot block itself")
        _, by_id = _index()
        if blocker_id not in note.blocked_by:
            cycle = check_blockers(note_id, [blocker_id], by_id)
            if cycle:
                raise HTTPException(409, "dependency cycle: " + " \u2192 ".join(cycle))
            note = replace(note, blocked_by=note.blocked_by + [blocker_id])
            vault.write(note)
        return _deps_payload(note, by_id)

    @app.delete("/api/notes/{note_id}/deps/{blocker_id}")
    def remove_dep(note_id: str, blocker_id: str):
        note = _require_note(note_id)
        _, by_id = _index()
        if blocker_id in note.blocked_by:
            note = replace(note, blocked_by=[d for d in note.blocked_by if d != blocker_id])
            vault.write(note)
        return _deps_payload(note, by_id)

    @app.get("/api/tasks")
    def list_tasks(include_done: bool = False, collection: Optional[str] = None):
        """Flat task list — the raw material for the dependency picker."""
        notes, by_id = _index()
        tasks = [n for n in notes if n.signifier is Signifier.TASK or n.blocked_by]
        if collection:
            tasks = [n for n in tasks if n.collection == collection]
        if not include_done:
            tasks = [n for n in tasks if not is_closed(n)]
        return [_card(n, by_id) for n in sort_column(tasks)]

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