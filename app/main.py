"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, datetime, timedelta
from typing import Optional

from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

from .deps import (
    STAGE_ORDER, board_summary, check_blockers, index_by_id, is_blocked,
    is_closed, next_position, normalize_blocked_by, plan_move, reconcile_move,
    resolve, sort_column, stage_of, blocking as blocking_notes,
)
from .models import (
    MOOD_LEVELS, PAIN_MAX, PAIN_MIN, STAGE_LABELS, Note, Signifier, Stage, Status,
    coerce_pain, is_generated_note_id, reconcile, stage_for_status, stamp_completed,
)
from . import home
from . import insight
from . import mood as moodlib
from . import query as querylib
from . import templates
from . import weekly
from .vault import Vault
from .db import Database
from .ics import notes_to_ics
from .config import Settings, load_settings
from . import ai
from . import daily
from . import transcribe
from .transcribe_run import AUDIO_DIRNAME, Transcriber, transcript_of
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
    pain: Optional[int] = None
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

    # -- end-of-day summaries ----------------------------------------------
    #
    # A loop rather than a cron entry, because this is a local app: nothing else
    # is running to wake it at 22:00, and the machine is shut at 22:00 far more
    # often than it is open. So the loop ticks, `daily.pending_days` decides what
    # is genuinely owed, and a day missed while the laptop was off is caught up
    # on the next start. `db.daily_summary_state` records what has run, which is
    # what keeps a due day from being summarised again on every tick.
    #
    # `llm` and `_index` are defined further down; these are only called once the
    # app is serving, by which point the whole factory has run.

    TICK_SECONDS = 300

    def _owed_days(now: datetime) -> list[date]:
        notes, _ = _index()
        blocked: set[date] = set()
        for raw in db.daily_generated_days():
            try:
                blocked.add(date.fromisoformat(raw))
            except ValueError:
                continue
        return daily.pending_days(
            notes,
            today=now.date(),
            hour=cfg.daily_summary_hour,
            now=now,
            blocked=blocked,
        )

    async def _generate_daily(day: date) -> dict:
        """Write a day's summary into that day's own note.

        Deliberately never raises: this normally runs unattended and a model that
        is down must not tear down the schedule. It writes the list regardless,
        and leaves the day owed while the recap is failing so the prose still
        lands if the model turns up later -- bounded by MAX_RECAP_ATTEMPTS.
        """
        notes, _ = _index()
        done = daily.completed_on(notes, day)
        if not done:
            # No note is created for an empty day. A page saying "nothing
            # happened" is worse than the absence of a page.
            return {
                "date": day.isoformat(),
                "completed": 0,
                "wrote": False,
                "reason": "nothing was completed that day",
            }

        recap = None
        error = None
        try:
            reply = await llm.complete(ai.build_daily_summary_messages(day, done))
            recap = ai.parse_summary(reply) or None
        except LLMError as exc:
            # The list of what got done is already known and is the part that
            # matters; only the prose is allowed to go missing.
            error = str(exc)

        section = daily.render(day, done, recap)
        note_id = daily.daily_note_id(day)
        # Re-read rather than reusing the list from before the model call. That
        # call takes about a minute, and writing the pre-call body back would
        # silently drop anything typed into this note while it ran -- a
        # data-loss window as wide as the generation itself.
        fresh, _ = _index()
        current = next((n for n in fresh if n.id == note_id), None)
        if current is not None:
            # Regenerate in place, leaving anything written by hand around it.
            vault.write(replace(current, body=daily.upsert_section(current.body, section)))
        else:
            vault.write(daily.note_for(day, done, recap))

        result = {
            "date": day.isoformat(),
            "completed": len(done),
            "wrote": True,
            "note_id": note_id,
            "titles": [n.title for n in done],
            "recap": recap,
        }
        if error:
            # Leave the day owed so the next tick tries again: a model that is
            # merely late should not cost the day its prose for good. Retries
            # stop after MAX_RECAP_ATTEMPTS so a model that is never coming back
            # is not asked all night.
            result["recap_error"] = error
            result["retrying"] = not db.note_daily_attempt(day.isoformat(), error)
        else:
            db.mark_daily_generated(day.isoformat())
        return result

    # -- weekly reviews ----------------------------------------------------

    def _week_felt(notes: list[Note], key: str) -> str | None:
        """How the week felt, from the mood layer's own numbers.

        `today` is the week's own Sunday, not the real today: the streak inside
        the summary is meaningless for a finished week, and pinning it to the
        week keeps the same input from producing a different line tomorrow.
        """
        monday, sunday = weekly.week_bounds(key)
        series = moodlib.daily_series(notes, start=monday, end=sunday)
        return weekly.felt_line(moodlib.summarize(series, today=sunday))

    def _insight_line(notes: list[Note]) -> str | None:
        """The standing pain-vs-output reading, but only when there is one.

        Left out rather than shown as "not enough days yet" every week forever:
        the mood view already says that, and a weekly review that repeats a
        non-finding each time is noise in a file.
        """
        reading = insight.pain_vs_output(notes)["reading"]
        return reading["text"] if reading["strength"] != "unknown" else None

    def _owed_weeks(now: datetime) -> list[str]:
        if not cfg.weekly_summary:
            return []
        notes, _ = _index()
        return weekly.due_weeks(
            notes,
            today=now.date(),
            hour=cfg.daily_summary_hour,
            now=now,
            blocked=db.weekly_generated_weeks(),
        )

    async def _generate_weekly(key: str) -> dict:
        """Write a week's rollup into that week's own note.

        Mirrors the daily run, including both of its rules: the list is written
        even when the model is not, and a failed recap leaves the week owed so
        the prose can still arrive later.
        """
        notes, _ = _index()
        done_by_day = weekly.finished_in(notes, key)
        if not done_by_day:
            return {
                "week": key,
                "completed": 0,
                "wrote": False,
                "reason": "nothing was completed that week",
            }

        felt = _week_felt(notes, key)
        insight_line = _insight_line(notes)
        recap = None
        error = None
        try:
            reply = await llm.complete(
                ai.build_weekly_summary_messages(key, done_by_day, felt)
            )
            recap = ai.parse_summary(reply) or None
        except LLMError as exc:
            # The list is the part that matters and it is already known.
            error = str(exc)

        section = weekly.render(key, done_by_day, recap, felt, insight_line)
        note_id = weekly.note_id(key)
        # Re-read rather than reusing the list from before the model call: a
        # weekly recap is a longer generation still, and writing the pre-call
        # body back would drop anything typed into the note meanwhile.
        fresh, _ = _index()
        current = next((n for n in fresh if n.id == note_id), None)
        if current is not None:
            vault.write(
                replace(current, body=weekly.upsert_section(current.body, section))
            )
        else:
            vault.write(
                weekly.note_for(
                    key, done_by_day, recap, felt, insight_reading=insight_line
                )
            )

        result = {
            "week": key,
            "completed": sum(len(v) for v in done_by_day.values()),
            "wrote": True,
            "note_id": note_id,
            "days": [d.isoformat() for d in sorted(done_by_day)],
            "recap": recap,
        }
        if error:
            result["recap_error"] = error
            result["retrying"] = not db.note_weekly_attempt(key, error)
        else:
            db.mark_weekly_generated(key)
        return result

    async def _daily_loop() -> None:
        while True:
            try:
                now = datetime.now()
                for day in _owed_days(now):
                    await _generate_daily(day)
                for key in _owed_weeks(now):
                    await _generate_weekly(key)
                app.state.daily_last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Recorded rather than swallowed, and read back by /api/daily:
                # a scheduler failing every night in silence is unfalsifiable.
                app.state.daily_last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(TICK_SECONDS)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if cfg.daily_summary_hour >= 0:
            task = asyncio.create_task(_daily_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="nookboard", lifespan=lifespan)
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
        """The collections, including the ones that exist before anything is in them.

        Templates are a first-class idea in this app, and the editor's collection
        dropdown can only offer what already exists -- so leaving `templates` out
        until something is in it made the first template impossible to create by
        the obvious route: the collection you would put it in was not on the list
        until you had already put something in it. Listing it empty costs a row
        and removes the deadlock.

        `transcripts` is here for the same reason: the transcribe form's "into"
        dropdown defaults to it, and a default that is not among the options is a
        form that cannot be submitted.
        """
        cols = vault.collections()
        for name in (templates.TEMPLATES_COLLECTION, transcribe.TRANSCRIPT_COLLECTION):
            if name not in cols:
                cols = sorted([*cols, name])
        return cols

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
            pain=coerce_pain(payload.pain),
            tags=payload.tags,
            recurrence=payload.recurrence,
            stage=stage,
            blocked_by=[d for d in payload.blocked_by if d != payload.id],
            position=position,
            created=date.today(),
            completed=stamp_completed(None, complete=status is Status.COMPLETE),
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
            # `.get(..., existing)` so an absent key keeps the value while an
            # explicit null clears it — the API could always express "no mood",
            # it was the index write that used to quietly keep the old one.
            mood=payload.get("mood", existing.mood),
            pain=coerce_pain(payload.get("pain", existing.pain)),
            tags=payload.get("tags", existing.tags),
            recurrence=payload.get("recurrence", existing.recurrence),
            stage=stage,
            blocked_by=blocked_by,
            completed=stamp_completed(
                existing.completed, complete=status is Status.COMPLETE
            ),
            position=payload.get("position", existing.position),
        )
        vault.write(updated)
        return updated.to_dict()

    @app.delete("/api/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        vault.delete(note_id)
        return None

    @app.get("/api/insight")
    def insights():
        """What the vault knows but no single feature does.

        Kept separate from /api/mood rather than folded into it: this is the
        joining of two features, and it has to be able to go quiet on its own
        when there is not enough evidence to say anything.
        """
        return {"pain_vs_output": insight.pain_vs_output(vault.list_all())}

    @app.get("/api/home")
    def home_view(month: Optional[str] = None):
        """Everything the dashboard shows, in one call.

        One endpoint rather than six the client has to join: the cards are meant
        to agree with each other, and the cheapest way to guarantee that is for
        them to be answered together. `month` picks the month the calendar card
        shows (`YYYY-MM-DD`); it defaults to today's.
        """
        today = date.today()
        shown = _coerce_date(month, "month") if month else today
        notes = vault.list_all()
        return home.summary(
            notes,
            root=root,
            today=today,
            calendar_counts=db.month_counts(shown.year, shown.month),
            month=shown,
            # The real list, so the count matches the Collections view.
            collections=list_collections(),
        )

    @app.get("/api/mood")
    def mood_series(
        days: int = 365,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ):
        """Mood and pain by day, plus headline numbers.

        Defaults to the last year: a mood tracker is read for its shape over
        time, and a range shorter than a few months has no shape to see.
        """
        today = date.today()
        end = end or today
        start = start or (end - timedelta(days=max(1, min(days, 3650)) - 1))
        if start > end:
            raise HTTPException(400, "start is after end")
        series = moodlib.daily_series(vault.list_all(), start=start, end=end)
        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "today": today.isoformat(),
            "days": series,
            "summary": moodlib.summarize(series, today=today),
            # Sent so the client labels the colour ramp without hard-coding the
            # level names — the vocabulary belongs in the model, not the CSS.
            "levels": list(MOOD_LEVELS),
            "pain_range": {"min": PAIN_MIN, "max": PAIN_MAX},
        }

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
        """The task board. Generated daily notes are not tasks and are left out.

        Without this, every day drops another date page into To-do for the user
        to dismiss by hand, forever. The count of what was held back is reported
        rather than hidden, so the board does not simply look smaller than the
        vault it is describing.
        """
        notes, by_id = _index()
        if collection:
            notes = [n for n in notes if n.collection == collection]
        if tag:
            notes = [n for n in notes if tag in n.tags]
        # Both kinds of generated container are kept off the board: a period
        # note is a record of work, not a piece of it. Templates are kept off it
        # too -- a shape for notes is not a thing to be doing, and a folder of
        # them would otherwise drop one card per template into To-do to be
        # dismissed by hand. Both counts are reported so the board never quietly
        # looks smaller than the vault.
        hidden_generated = sum(1 for n in notes if is_generated_note_id(n.id))
        hidden_templates = sum(1 for n in notes if templates.is_template(n))
        notes = [
            n for n in notes
            if not is_generated_note_id(n.id) and not templates.is_template(n)
        ]

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
            "hidden_generated": hidden_generated,
            "hidden_templates": hidden_templates,
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
        moved = replace(
            existing,
            stage=new_stage,
            status=new_status,
            completed=stamp_completed(
                existing.completed, complete=new_status is Status.COMPLETE
            ),
        )

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

    # -- transcription (local whisper.cpp) ---------------------------------
    #
    # Nothing here leaves the machine: ffmpeg reads the file, whisper.cpp
    # transcribes it on the GPU, and the same local model as everywhere else
    # writes the summary. A job is minutes of work, so this is start-and-poll
    # rather than one long request. The job table is in memory -- after a restart
    # a job is gone and the UI says so, because the honest answer is "start it
    # again" and not a guess that it finished.

    transcriber = Transcriber(
        cli=cfg.whisper_cli,
        models_dir=cfg.whisper_models,
        default_model=cfg.whisper_model,
        vault=vault,
        taken_ids=lambda: [n.id for n in vault.list_all()],
        llm=llm,
        audio_dir=vault.root / AUDIO_DIRNAME,
    )

    def _engine() -> transcribe.Engine:
        """What is installed, asked fresh each time.

        Not cached: a whisper build or a model download that lands while the
        server is running should show up, and a stale "ready" is exactly the
        kind of small lie that costs an hour.
        """
        wanted = list(dict.fromkeys([cfg.whisper_model, *transcribe.MODEL_CHOICES]))
        return transcriber.engine(wanted)

    def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
        """`name.webm`, then `name-2.webm`.

        An upload must not overwrite the previous recording: for something
        recorded in the browser, the file in this directory is the only copy
        that exists.
        """
        candidate = directory / f"{stem}{suffix or '.webm'}"
        counter = 2
        while candidate.exists():
            candidate = directory / f"{stem}-{counter}{suffix or '.webm'}"
            counter += 1
        return candidate

    @app.get("/api/transcribe")
    def transcribe_status():
        """What is installed, and what has run recently.

        The engine is reported even when it is fine. Which whisper binary and
        which models were found is the difference between "transcription is
        broken" and "it is running the wrong build with the wrong model", and
        only one of those is answerable from outside.
        """
        return {
            "engine": _engine().as_dict(),
            "model": cfg.whisper_model,
            "choices": list(transcribe.MODEL_CHOICES),
            "collection": transcribe.TRANSCRIPT_COLLECTION,
            "collections": list_collections(),
            "extensions": sorted(transcribe.MEDIA_EXTENSIONS),
            "audio_dir": str(vault.root / AUDIO_DIRNAME),
            "jobs": transcriber.recent(),
        }

    @app.post("/api/transcribe", status_code=201)
    async def transcribe_file(payload: dict):
        """Transcribe a file that is already on disk. Nothing is copied -- the
        note records the path, and the file stays where its owner put it.

        `async def`, not `def`: a sync handler is run in a worker thread, where
        there is no event loop for the job's task to be created on, and every
        submission is a 500. The work itself is spawned and returned from, so
        this handler only has to run in the loop that will run the job.
        """
        raw = (payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(400, "a path is required")
        target = Path(raw).expanduser()
        if not target.is_file():
            raise HTTPException(400, f"no such file: {target}")
        engine = _engine()
        if not engine.ready:
            raise HTTPException(409, " · ".join(engine.problems))
        job = transcriber.submit(
            path=target,
            source=str(target),
            model=(payload.get("model") or cfg.whisper_model),
            collection=(payload.get("collection") or transcribe.TRANSCRIPT_COLLECTION),
            summarize=bool(payload.get("summarize", True)),
            keep=False,
        )
        return job.as_dict()

    @app.post("/api/transcribe/upload", status_code=201)
    async def transcribe_upload(
        request: Request,
        name: str,
        model: str = "",
        collection: str = "",
        summarize: bool = True,
    ):
        """Transcribe something that only exists in the browser.

        The body is the file itself, not a multipart form. The browser already
        holds the bytes -- a Blob from the picker, or the webm the recorder just
        produced -- so posting them straight means nothing has to take them apart
        again, and the app needs no upload dependency to do it.

        What was uploaded is kept, under the vault's `.audio`. For a recording
        made in the browser this is the only copy there is; a note naming a
        temporary file nobody can open tomorrow would be a lie about where the
        audio went.
        """
        leaf = Path(name or "recording").name
        suffix = Path(leaf).suffix.lower()
        if suffix not in transcribe.MEDIA_EXTENSIONS:
            raise HTTPException(
                400,
                f"{suffix or 'that file type'} is not audio or video (this app reads "
                f"{', '.join(sorted(transcribe.MEDIA_EXTENSIONS))})",
            )
        directory = vault.root / AUDIO_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        target = _unique_path(directory, transcribe.slugify(Path(leaf).stem), suffix)
        written = 0
        with target.open("wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                fh.write(chunk)
        if not written:
            target.unlink(missing_ok=True)
            raise HTTPException(400, "the upload was empty")
        job = transcriber.submit(
            path=target,
            source=leaf,
            model=(model or cfg.whisper_model),
            collection=(collection or transcribe.TRANSCRIPT_COLLECTION),
            summarize=bool(summarize),
            keep=True,
        )
        return job.as_dict()

    @app.get("/api/transcribe/{job_id}")
    def transcribe_job(job_id: str):
        job = transcriber.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job (jobs do not survive a restart)")
        return job.as_dict()

    @app.post("/api/transcribe/summarize", status_code=201)
    async def transcribe_resummarize(payload: dict):
        """Retry the summary of an existing transcript note.

        The summary is the cheap half and the flaky half, so retrying it must not
        mean transcribing the recording again -- that would be an hour of GPU work
        to redo ten seconds of writing.
        """
        note = _require_note(payload.get("id"))
        if not transcript_of(note.body):
            raise HTTPException(400, "that note has no transcript to summarise")
        return transcriber.submit_resummarise(note.id).as_dict()

    # -- daily notes -------------------------------------------------------

    def _coerce_date(value, field: str) -> date:
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{field} must be an ISO date, got {value!r}")

    @app.get("/api/daily/{day}")
    def daily_state(day: str):
        """What a day's note currently holds, and what it is owed."""
        target = _coerce_date(day, "day")
        notes, _ = _index()
        done = daily.completed_on(notes, target)
        note_id = daily.daily_note_id(target)
        note = next((n for n in notes if n.id == note_id), None)
        return {
            "date": target.isoformat(),
            "completed": [n.to_dict() for n in done],
            "count": len(done),
            "note_id": note_id,
            "has_note": note is not None,
            "has_summary": bool(note and daily.has_summary(note.body)),
            "generated": target.isoformat() in db.daily_generated_days(),
            "scheduled_hour": cfg.daily_summary_hour,
            "last_error": getattr(app.state, "daily_last_error", None),
        }

    @app.post("/api/daily/summary")
    async def daily_summary(payload: dict):
        """Summarise a day's finished work into that day's own note.

        The scheduled run happens once, so anything finished after the cutoff
        would otherwise leave the note describing an earlier evening; `refresh`
        re-runs a day that has already been written.
        """
        target = (
            _coerce_date(payload["date"], "date")
            if payload.get("date")
            else date.today()
        )
        if payload.get("refresh"):
            db.clear_daily_generated(target.isoformat())
        return await _generate_daily(target)

    @app.get("/api/daily")
    def daily_pending():
        """Days owed a summary right now, and when the next run is due."""
        now = datetime.now()
        recorded = sorted(db.daily_generated_days())
        return {
            "today": now.date().isoformat(),
            "scheduled_hour": cfg.daily_summary_hour,
            "due_now": now.hour >= cfg.daily_summary_hour >= 0,
            "owed": [d.isoformat() for d in _owed_days(now)],
            "summarised": recorded,
            # Written with its list but never got a recap, and still being
            # retried. Surfaced because a page that quietly lacks its prose
            # reads as a bug in the feature rather than a model that was off.
            "retrying": db.daily_retrying_days(),
            "last_error": getattr(app.state, "daily_last_error", None),
        }

    # -- weekly reviews (API) ----------------------------------------------

    def _coerce_week(value) -> str:
        try:
            monday, _ = weekly.week_bounds(str(value or "").strip())
        except (TypeError, ValueError):
            raise HTTPException(400, f"week must look like 2026-W39, got {value!r}")
        return weekly.week_key(monday)

    @app.get("/api/weekly")
    def weekly_pending():
        """Weeks owed a review right now, and when the next run is due."""
        now = datetime.now()
        return {
            "today": now.date().isoformat(),
            "enabled": cfg.weekly_summary,
            "scheduled_hour": cfg.daily_summary_hour,
            "due_now": bool(cfg.weekly_summary and now.hour >= cfg.daily_summary_hour >= 0),
            "owed": _owed_weeks(now),
            "summarised": sorted(db.weekly_generated_weeks()),
            "retrying": db.weekly_retrying_weeks(),
            "last_error": getattr(app.state, "daily_last_error", None),
        }

    @app.get("/api/weekly/{key}")
    def weekly_state(key: str):
        """What a week's note currently holds, and what it is owed."""
        target = _coerce_week(key)
        notes, _ = _index()
        done_by_day = weekly.finished_in(notes, target)
        monday, sunday = weekly.week_bounds(target)
        note = next((n for n in notes if n.id == weekly.note_id(target)), None)
        return {
            "week": target,
            "from": monday.isoformat(),
            "to": sunday.isoformat(),
            "completed": sum(len(v) for v in done_by_day.values()),
            "days": {
                d.isoformat(): [n.title for n in v]
                for d, v in sorted(done_by_day.items())
            },
            "note_id": weekly.note_id(target),
            "has_note": note is not None,
            "has_summary": bool(note and weekly.has_summary(note.body)),
            "generated": target in db.weekly_generated_weeks(),
            "felt": _week_felt(notes, target),
            "last_error": getattr(app.state, "daily_last_error", None),
        }

    @app.post("/api/weekly/summary")
    async def weekly_summary(payload: dict):
        """Summarise a week's finished work into that week's own note.

        Defaults to the most recent week that has ended -- the week the
        scheduler would write next -- so a manual run needs no argument.
        """
        target = (
            _coerce_week(payload["week"])
            if payload.get("week")
            else weekly.week_key(datetime.now().date() - timedelta(days=7))
        )
        if payload.get("refresh"):
            db.clear_weekly_generated(target)
        return await _generate_weekly(target)

    # -- templates (API) ----------------------------------------------------

    def _free_note_id(by_id: dict) -> str:
        """An id for a note the app is about to create.

        Minted here rather than trusted from the client: this is a note the app
        made, and a caller that sent a duff id would have the write land on top
        of whatever already sits at that id.
        """
        stamp = int(datetime.now().timestamp() * 1000)
        candidate = f"tpl-{stamp:x}"
        n = 2
        while candidate in by_id:
            candidate = f"tpl-{stamp:x}-{n}"
            n += 1
        return candidate

    @app.get("/api/templates")
    def list_templates():
        """The shapes a note can be made from."""
        notes, _ = _index()
        return {
            "collection": templates.TEMPLATES_COLLECTION,
            "templates": [
                {
                    "id": n.id,
                    "title": n.title,
                    "signifier": n.signifier.value,
                    "tags": list(n.tags),
                    "body": n.body,
                    "placeholders": sorted(
                        {
                            m.lower()
                            for m in templates.PLACEHOLDER_RE.findall(n.body)
                            + templates.PLACEHOLDER_RE.findall(n.title)
                        }
                        & set(templates.KNOWN)
                    ),
                }
                for n in templates.list_templates(notes)
            ],
        }

    @app.post("/api/templates/apply")
    def apply_template(payload: dict):
        """Make a note from a template, by id or by title."""
        wanted = str(payload.get("template") or "").strip()
        notes, by_id = _index()
        source = by_id.get(wanted) or next(
            (n for n in templates.list_templates(notes) if n.title.lower() == wanted.lower()),
            None,
        )
        if source is None or not templates.is_template(source):
            raise HTTPException(404, f"no template {wanted!r}")

        day = _coerce_date(payload["date"], "date") if payload.get("date") else date.today()
        note = templates.build_note(
            source,
            note_id=_free_note_id(by_id),
            title=payload.get("title"),
            collection=str(payload.get("collection") or "").strip() or "inbox",
            day=day,
            # Notes only: a template's title is the shape of a name, not a name
            # already spoken for. Counting the templates would make every
            # template reserve its own title, so the first note made from
            # "Weekly shop" would be "Weekly shop (2)" and the second "(3)".
            taken={n.title for n in notes if not templates.is_template(n)},
        )
        vault.write(note)
        return note.to_dict()

    # -- queries in notes (API) ---------------------------------------------

    @app.get("/api/query")
    def run_query(q: str = "", on: Optional[str] = None):
        """Resolve one query, for the preview to splice into a note.

        `on` is the date of the note the query sits in, so `this week` means the
        week that note is about rather than the week it happens to be read in.
        Without it the query resolves against today.
        """
        notes, _ = _index()
        day = _coerce_date(on, "on") if on else date.today()
        return {
            "query": q,
            "on": day.isoformat(),
            "markdown": querylib.render(q, notes, on=day),
        }

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