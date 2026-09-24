"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
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
    coerce_pain, not_work_reason, reconcile, stage_for_status, stamp_completed,
)
from . import bookmarks
from . import health
from . import health_run
from . import history
from . import history_run
from . import home
from . import insight
from . import workspace
from . import workspace_run
from . import mood as moodlib
from . import query as querylib
from . import schedule
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
    #: "HH:MM". A note with a time names an instant -- see `app.schedule`.
    at: Optional[str] = None
    until: Optional[str] = None
    #: A folder this note is about. A note that has one is a workspace.
    path: Optional[str] = None
    #: An address this note is about. A note that has one is a bookmark.
    url: Optional[str] = None
    #: A Lucide icon name, drawn beside the note wherever it is shown as a card.
    icon: Optional[str] = None
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

        `workspaces` too: the first workspace note has to be creatable from the
        editor, and the editor's dropdown is this list.
        """
        cols = vault.collections()
        for name in (templates.TEMPLATES_COLLECTION, transcribe.TRANSCRIPT_COLLECTION,
                     workspace.WORKSPACES_COLLECTION):
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

    def _coerce_time(value: object, field: str) -> Optional[str]:
        """A time, or a refusal.

        Reads are forgiving on purpose -- a foreign file with `at: noon` opens as an
        untimed note rather than failing to open at all. A *write* is not: silently
        dropping a time somebody just typed would leave a note that looks scheduled
        and is not, which is the one outcome worse than an error message.
        """
        if value is None or value == "":
            return None
        parsed = schedule.parse_time(value)
        if parsed is None:
            raise HTTPException(400, f"{field} is not a time (write it like 14:30)")
        return parsed


    def _coerce_path(value: object) -> Optional[str]:
        """The folder a note points at, or a refusal.

        An empty string (or whitespace) is how you *clear* the field, not an
        error -- the same rule the times use. Anything that is not a string is
        refused rather than coerced: `path: [a, b]` is a typo, and picking one
        of the two would be inventing an answer about the person's own machine.

        A path that does not exist is accepted on purpose. The folder may not be
        cloned yet, and a note is allowed to record an intention; the *read*
        says "that folder is gone" rather than stopping you writing it down.
        """
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise HTTPException(400, "path must be a string (a folder path, or null)")
        text = value.strip()
        return text or None

    def _coerce_icon(value: object) -> Optional[str]:
        """The icon a note shows, or a refusal.

        Stored as typed, and an unknown name is *kept*: a note is the person's own
        file, and `icon: serverr` is a typo in their handwriting, not a reason to
        lose the line. `app.note_icons` says whether a name is one Lucide can draw,
        and the view draws the fallback for the ones that are not.
        """
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise HTTPException(400, "icon must be a string (a Lucide icon name, or null)")
        text = value.strip()
        return text or None

    def _coerce_url(value: object) -> Optional[str]:
        """The address a note points at, or a refusal.

        Stored as typed and *not* validated here, which is the one place this differs
        from a path: whether a browser can open an address is `app.bookmarks`' answer,
        and it is a thing to be told rather than a thing to be stopped for. A url that
        is a typo is still the person's note about that service, and refusing the save
        would throw the note away to protect a link.
        """
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise HTTPException(400, "url must be a string (an address, or null)")
        text = value.strip()
        return text or None


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
            at=_coerce_time(payload.at, "at"),
            until=_coerce_time(payload.until, "until"),
            path=_coerce_path(payload.path),
            url=_coerce_url(payload.url),
            icon=_coerce_icon(payload.icon),
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
        path = vault.write(note)
        # After the write, never before -- the same ordering as the index update,
        # and for the same reason: a note that is on disk cannot be lost by the
        # thing that came along afterwards to describe it.
        return {**note.to_dict(), "history": _record("new", note.title, [path])}

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
            url=_coerce_url(payload.get("url", existing.url)),
            icon=_coerce_icon(payload.get("icon", existing.icon)),
            signifier=_coerce_enum(
                Signifier, payload.get("signifier", existing.signifier.value), "signifier"
            ),
            status=status,
            dates=new_dates,
            at=_coerce_time(payload.get("at", existing.at), "at"),
            until=_coerce_time(payload.get("until", existing.until), "until"),
            path=_coerce_path(payload.get("path", existing.path)),
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
        # The file a note is *leaving*, if its collection changed: a collection
        # is a folder here, so that is a move, and a move recorded halfway shows
        # the note existing in two places at once.
        before = vault.root / existing.source_rel if existing.source_rel else None
        path = vault.write(updated)
        return {**updated.to_dict(), "history": _record("edit", updated.title, [before, path])}

    @app.delete("/api/notes/{note_id}", status_code=204)
    def delete_note(note_id: str):
        """Deleting a note is now recoverable, and the history says which note.

        The path is read *before* the unlink because only the note can name its
        own file. Deleting something that is not there stays a 204: a delete that
        already happened is not an error, and this app does not report the
        absence of a note as a failure to delete it.
        """
        _, by_id = _index()
        gone = by_id.get(note_id)
        vault.delete(note_id)
        if gone is not None and gone.source_rel:
            _record("delete", gone.title, [vault.root / gone.source_rel])
        return None

    # -- history -------------------------------------------------------------
    #
    # The vault is a folder of ordinary markdown, which is what makes a history
    # possible at all: git, in the folder, invisible. Nothing here runs on a
    # schedule and nothing here needs a server -- the vault's past is a `.git`
    # beside its notes, readable with the same git the notes are readable with.

    def _rel_of(path) -> str:
        """A path the vault just wrote, as the vault sees it."""
        if not path:
            return ""
        try:
            return str(Path(path).relative_to(vault.root))
        except ValueError:
            # A path outside the vault is not history this app keeps.
            return ""

    def _record(act: str, title: str, paths) -> dict:
        """Record one change in the vault's history.

        Always called *after* the write it describes, which is the ordering that
        makes a history safe: the note is already on disk, so this can only add
        information. `{"on": False}` when history is off -- not an error, and the
        same key every time rather than one that appears when convenient.
        """
        root = str(vault.root)
        if not history_run.is_repo(root):
            return {"on": False}
        rels = [r for r in (_rel_of(p) for p in paths) if r]
        return {"on": True, **history_run.commit_change(root, rels, act, title)}

    def _deleted() -> list[dict]:
        """Notes the vault has lost, each with the version that brings it back.

        A note that has since been recreated is not lost, so it is not offered:
        asking "restore this?" about something already there is a question whose
        answer the app already knows.
        """
        root = str(vault.root)
        now = datetime.now(timezone.utc)
        out = []
        for entry in history_run.deletions(root):
            rel = history.safe_relpath(root, entry["path"])
            if not rel or (Path(root) / rel).exists():
                continue
            out.append({**entry, "path": rel, "words": history.version_words(entry, now),
                        "clock": history.clock_label(entry["when"])})
        return out

    def _history_state() -> dict:
        root = str(vault.root)
        if not history_run.is_repo(root):
            return {
                "on": False, "changes": [], "deleted": [], "pending": [],
                "summary": "History is off.",
                "why": "Turning it on puts this vault under git, so every change "
                       "from here on can be undone.",
            }
        now = datetime.now(timezone.utc)
        # The baseline commit is where the vault started, not a change to it --
        # "2 changes recorded" the moment history is turned on would be counting
        # the act of counting.
        changes = [{**e, "words": history.version_words(e, now),
                    "clock": history.clock_label(e["when"])}
                   for e in history_run.log_for(root, limit=25)
                   if e["subject"] != history_run.FIRST_COMMIT]
        pending = history_run.pending(root)
        deleted = _deleted()
        return {
            "on": True, "why": "", "changes": changes, "deleted": deleted,
            "pending": pending,
            "summary": history.summary_line(changes, deleted, now, len(pending)),
        }

    @app.get("/api/history")
    def history_state():
        """The vault's past: what changed, what is not recorded, what is gone."""
        return _history_state()

    @app.post("/api/history/init")
    def history_init():
        """Start keeping history.

        Explicit rather than automatic, because it writes a `.git` into someone's
        vault: that is a thing to be asked for, not a thing to happen to you.
        """
        result = history_run.ensure_repo(str(vault.root))
        if not result.get("ok"):
            raise HTTPException(500, f"could not start keeping history: {result.get('why')}")
        return {**_history_state(), "started": result}

    @app.post("/api/history/checkpoint")
    def history_checkpoint():
        """Record everything that has changed, now, because someone asked."""
        root = str(vault.root)
        if not history_run.is_repo(root):
            raise HTTPException(400, "history is not on for this vault")
        result = history_run.checkpoint(root)
        if not result.get("ok"):
            raise HTTPException(500, f"could not record these changes: {result.get('why')}")
        return {**_history_state(), "recorded": result}

    @app.get("/api/history/{note_id}")
    def note_history(note_id: str):
        """One note's versions, newest first.

        A note with no file of its own has no history to show, and says so rather
        than showing an empty list that reads as "nothing ever happened here".
        """
        note = _require_note(note_id)
        root = str(vault.root)
        rel = note.source_rel or ""
        if not history_run.is_repo(root):
            return {"on": False, "note_id": note_id, "relpath": rel, "versions": [], "why": ""}
        if not rel:
            return {"on": True, "note_id": note_id, "relpath": "", "versions": [],
                    "why": "this note has no file of its own yet"}
        now = datetime.now(timezone.utc)
        versions = [{**e, "words": history.version_words(e, now),
                     "clock": history.clock_label(e["when"])}
                    for e in history_run.log_for(root, rel, limit=40)]
        # Whether the newest version is the one on disk is *measured*: a note
        # edited outside the app is not its own newest commit.
        if versions:
            versions[0]["is_now"] = history_run.matches_now(root, rel, versions[0]["sha"])
        return {"on": True, "note_id": note_id, "relpath": rel, "versions": versions,
                "why": "" if versions else "nothing recorded for this note yet"}

    @app.post("/api/history/restore")
    def restore_version(payload: dict):
        """Put one file back the way it was, and record that it was put back.

        Identified by *path*, not by note id, because that is what a version is a
        version of -- and because the case this exists for is a note that was
        deleted, which has no note record left to look itself up by.

        One file, written from one old version: not a `git reset`. Nothing else in
        the vault can move, which is what makes this safe to offer as a button.
        """
        rev = str(payload.get("rev") or "").strip()
        rel = str(payload.get("path") or "").strip()
        if not rev:
            raise HTTPException(400, "which version? pass rev")
        if not rel:
            raise HTTPException(400, "which file? pass path")
        result = history_run.restore_version(str(vault.root), rel, rev)
        if not result.get("ok"):
            raise HTTPException(400, str(result.get("why") or "could not restore that version"))
        # The file on disk changed, so the index is rebuilt from the files.
        # Deliberately the whole index rather than the one note: a restored note
        # may be one that the index had no row for at all (it was deleted), and
        # "which rows need to change" is a question the files already answer.
        db.rebuild_from(vault.list_all())
        return {"ok": True, **result, "history": _history_state()}

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
            # Read here because this is where the vault is at hand, and read
            # *every* time the dashboard is: a folder that was moved an hour ago
            # should not still be reported as it was yesterday.
            workspace_states=_workspace_states(notes),
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
        # What is kept off the board is `models.not_work_reason`'s answer -- one
        # predicate, shared with the dashboard and an unnarrowed query, so the three
        # cannot disagree about what is left to do. This endpoint used to spell the
        # rule out for itself (generated ids and templates) and therefore missed the
        # notes in `daily`, `weekly`, `bookmarks`, `workspaces` and `testing`, whose
        # ids look like any other note's.
        #
        # The reasons are counted as a partition, so they add up to the number held
        # back rather than to something larger, and they are *reported* rather than
        # hidden: a board must never quietly look smaller than the vault it describes.
        hidden = {"template": 0, "generated": 0, "collection": 0}
        kept = []
        for note in notes:
            reason = not_work_reason(note)
            if reason is None:
                kept.append(note)
            else:
                hidden[reason] += 1
        notes = kept

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
            "hidden_generated": hidden["generated"],
            "hidden_templates": hidden["template"],
            # New reasons, and the total, so the counts can be checked against each
            # other by anyone reading the JSON rather than only by the app.
            "hidden_collections": hidden["collection"],
            "hidden_total": sum(hidden.values()),
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

    # -- workspaces ---------------------------------------------------------
    #
    # A note with `path:` is the home for a folder: its branch, what is
    # uncommitted, when it was last committed, what markers the code carries.
    # The folder is read on every request rather than cached, because a cache is
    # a second answer that goes stale the moment you commit something.

    def _workspace_states(notes) -> list[dict]:
        """Every workspace note's folder, read now, in the order they are shown.

        One reader for the view, the dashboard card and (through the note) the
        editor panel, so the same folder cannot be described two ways.
        """
        now = datetime.now(timezone.utc)
        states = []
        for note in notes:
            if not workspace.is_workspace_note(note):
                continue
            state = workspace_run.state_for_note(note, now=now)
            if state:
                states.append(state)
        return workspace.sort_states(states)

    @app.get("/api/workspaces")
    def list_workspaces():
        """Every note that points at a folder, and what that folder is *now*.

        Read here rather than in the browser so the list, the editor panel and
        the dashboard card cannot disagree -- and so a folder is read once per
        request instead of once per place it appears.
        """
        states = _workspace_states(vault.list_all())
        summary = workspace.summarize(states)
        return {
            "summary": summary,
            # The sentence comes from the same place as the counts, so a card
            # cannot say "all settled" while the counts say otherwise.
            "line": workspace.attention_line(summary),
            "workspaces": states,
            "tools": workspace_run.tools(),
        }

    # A note with `url:` is a bookmark: an address you wanted to keep, sitting in the
    # vault you already back up and can grep. "Services and the like" is a list, and a
    # browser's bookmarks bar is the one place a list cannot be read from a file.

    @app.get("/api/bookmarks")
    def list_bookmarks():
        """Every note that points at an address, grouped the way its tags say.

        No I/O beyond the index, deliberately: unlike a workspace there is nothing on
        the other end to ask. A bookmark says where something is, not whether it is up,
        and this app does not probe your services.
        """
        return bookmarks.view([n for n in vault.list_all() if bookmarks.is_bookmark(n)])

    @app.post("/api/bookmarks/check")
    def check_bookmarks():
        """Ask every bookmark's address whether it is answering, right now.

        The only endpoint in this app that makes requests *outward*, and it is marked
        as such because that is a real property of it: everything else reads the vault
        or runs an allowlisted binary, and this one talks to whatever the vault's urls
        point at. Bounded per address and run in parallel, so one dead host costs
        seconds rather than the view -- and a failure comes back as a result, never as
        an exception the view has to read.

        The answer carries when it was taken. A status without a time is a claim about
        the past dressed as one about the present.
        """
        notes = [n for n in vault.list_all() if bookmarks.is_bookmark(n)]
        urls = [u for u in (bookmarks.url_of(n) for n in notes) if u]
        return health_run.check_all(urls)

    @app.get("/api/workspaces/{note_id}")
    def get_workspace(note_id: str):
        """One workspace. A note that is not one is not found, rather than
        answered with an empty state that looks like a broken folder.

        Carries `tools` too: the editor panel shows the same open buttons, and a
        button that is disabled because the *panel* did not know what is
        installed would be the app stating something false about this machine.
        """
        try:
            note = vault.read(note_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, f"no note {note_id!r}")
        state = workspace_run.state_for_note(note, now=datetime.now(timezone.utc))
        if state is None:
            raise HTTPException(404, f"note {note_id!r} does not point at a folder")
        state["tools"] = workspace_run.tools()
        return state

    @app.post("/api/workspaces/{note_id}/open")
    def open_workspace(note_id: str, payload: dict, request: Request):
        """Open a workspace's folder: in your editor, a terminal, the files app.

        **This is the one endpoint in the app that starts a process**, so it is
        the one endpoint that asks for something a cross-origin page cannot send
        without a preflight. There is no auth here and never was, which is
        tolerable while every other endpoint only edits notes -- but a web page
        you merely visited should not be able to launch a terminal on your
        machine, and without this header check it could.

        It reports the argv it ran, so the app can tell you what it did instead
        of implying it.
        """
        if request.headers.get("x-nookboard-action") != "open":
            raise HTTPException(
                403, "this action needs the app's own request (X-Nookboard-Action)"
            )
        what = str(payload.get("what") or "").strip()
        if what not in workspace.OPEN_ACTIONS:
            raise HTTPException(400, f"what must be one of {list(workspace.OPEN_ACTIONS)}")
        try:
            note = vault.read(note_id)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, f"no note {note_id!r}")
        path = workspace.path_field(note)
        if path is None:
            raise HTTPException(404, f"note {note_id!r} does not point at a folder")
        # `file` and `line` are how a card asks for a *place* in the folder --
        # a marker's `file:line`, or one of the changed file names. Both come
        # from the request, so both are checked before anything is spawned:
        # `inside_folder` refuses anything that is not a real file inside this
        # workspace, and a refusal here is a bad request (400), not a missing
        # thing (409) -- the request asked for something it may not have.
        result = workspace_run.open_workspace(
            path, what, file=payload.get("file"), line=payload.get("line")
        )
        if not result.get("ok"):
            raise HTTPException(400 if result.get("invalid") else 409,
                                result.get("error") or "could not open it")
        return result

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
    @app.middleware("http")
    async def _never_cache(request, call_next):
        """No response from this app is a thing to keep.

        There is no asset versioning here to key a cache on -- `app.js` has the same
        URL after every change -- and this app is served from one machine to one
        person, so a cached copy can only ever be a copy of something that has since
        been fixed. Twice now that has been the whole of a bug report:

        - a stale `app.js` after the history-rendering fix, where the doubling only
          went away after a hard reload;
        - a stale `static/vendor/lucide.js`, which kept drawing the icon set from
          before the generator was fixed -- the icons looked clipped in a tab whose
          neighbour, opened cold, drew them correctly.

        An earlier version of this middleware was written on a theory that turned out
        to be wrong (a caching explanation for a discrepancy the person had caused by
        editing a note themselves) and was removed for that reason. This one is not a
        theory: both incidents are reproducible by loading a cold profile and comparing.
        """
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        idx = static_dir / "index.html"
        return FileResponse(idx)

    return app


# Module-level app for `uvicorn app.main:app`
app = create_app()