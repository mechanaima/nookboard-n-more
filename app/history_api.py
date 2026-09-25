"""HTTP routes and write policy for the vault's Git-backed history.

The application factory owns construction and note writes; this module owns the
history state those writes record and the endpoints that inspect or restore it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import history, history_run
from .db import Database
from .models import Note
from .vault import Vault


HistoryRecorder = Callable[[str, str, Iterable[Path | None]], dict]


def build_history_router(
    vault: Vault,
    db: Database,
    require_note: Callable[[str], Note],
) -> tuple[APIRouter, HistoryRecorder]:
    """Build history routes and the recorder shared with note-writing routes."""
    router = APIRouter()

    def rel_of(path: Path | None) -> str:
        """A path the vault just wrote, as the vault sees it."""
        if not path:
            return ""
        try:
            return str(Path(path).relative_to(vault.root))
        except ValueError:
            # A path outside the vault is not history this app keeps.
            return ""

    def record(act: str, title: str, paths: Iterable[Path | None]) -> dict:
        """Record one change after the write it describes has reached disk."""
        root = str(vault.root)
        if not history_run.is_repo(root):
            return {"on": False}
        rels = [r for r in (rel_of(p) for p in paths) if r]
        return {"on": True, **history_run.commit_change(root, rels, act, title)}

    def deleted() -> list[dict]:
        """Notes the vault has lost, each with the version that brings it back."""
        root = str(vault.root)
        now = datetime.now(timezone.utc)
        out = []
        for entry in history_run.deletions(root):
            rel = history.safe_relpath(root, entry["path"])
            if not rel or (Path(root) / rel).exists():
                continue
            out.append({
                **entry,
                "path": rel,
                "words": history.version_words(entry, now),
                "clock": history.clock_label(entry["when"]),
            })
        return out

    def state() -> dict:
        root = str(vault.root)
        if not history_run.is_repo(root):
            return {
                "on": False,
                "changes": [],
                "deleted": [],
                "pending": [],
                "summary": "History is off.",
                "why": "Turning it on puts this vault under git, so every change "
                       "from here on can be undone.",
            }
        now = datetime.now(timezone.utc)
        # The baseline commit is where the vault started, not a change to it.
        changes = [
            {
                **entry,
                "words": history.version_words(entry, now),
                "clock": history.clock_label(entry["when"]),
            }
            for entry in history_run.log_for(root, limit=25)
            if entry["subject"] != history_run.FIRST_COMMIT
        ]
        pending = history_run.pending(root)
        lost = deleted()
        return {
            "on": True,
            "why": "",
            "changes": changes,
            "deleted": lost,
            "pending": pending,
            "summary": history.summary_line(changes, lost, now, len(pending)),
        }

    @router.get("/api/history")
    def history_state():
        """The vault's past: what changed, what is not recorded, what is gone."""
        return state()

    @router.post("/api/history/init")
    def history_init():
        """Start keeping history in the vault's Git repository."""
        result = history_run.ensure_repo(str(vault.root))
        if not result.get("ok"):
            raise HTTPException(
                500, f"could not start keeping history: {result.get('why')}"
            )
        return {**state(), "started": result}

    @router.post("/api/history/checkpoint")
    def history_checkpoint():
        """Record everything that has changed, now, because someone asked."""
        root = str(vault.root)
        if not history_run.is_repo(root):
            raise HTTPException(400, "history is not on for this vault")
        result = history_run.checkpoint(root)
        if not result.get("ok"):
            raise HTTPException(
                500, f"could not record these changes: {result.get('why')}"
            )
        return {**state(), "recorded": result}

    @router.get("/api/history/{note_id}")
    def note_history(note_id: str):
        """One note's versions, newest first."""
        note = require_note(note_id)
        root = str(vault.root)
        rel = note.source_rel or ""
        if not history_run.is_repo(root):
            return {
                "on": False,
                "note_id": note_id,
                "relpath": rel,
                "versions": [],
                "why": "",
            }
        if not rel:
            return {
                "on": True,
                "note_id": note_id,
                "relpath": "",
                "versions": [],
                "why": "this note has no file of its own yet",
            }
        now = datetime.now(timezone.utc)
        versions = [
            {
                **entry,
                "words": history.version_words(entry, now),
                "clock": history.clock_label(entry["when"]),
            }
            for entry in history_run.log_for(root, rel, limit=40)
        ]
        if versions:
            versions[0]["is_now"] = history_run.matches_now(
                root, rel, versions[0]["sha"]
            )
        return {
            "on": True,
            "note_id": note_id,
            "relpath": rel,
            "versions": versions,
            "why": "" if versions else "nothing recorded for this note yet",
        }

    @router.post("/api/history/restore")
    def restore_version(payload: dict):
        """Put one file back the way it was and rebuild the note index."""
        rev = str(payload.get("rev") or "").strip()
        rel = str(payload.get("path") or "").strip()
        if not rev:
            raise HTTPException(400, "which version? pass rev")
        if not rel:
            raise HTTPException(400, "which file? pass path")
        result = history_run.restore_version(str(vault.root), rel, rev)
        if not result.get("ok"):
            raise HTTPException(
                400, str(result.get("why") or "could not restore that version")
            )
        db.rebuild_from(vault.list_all())
        return {"ok": True, **result, "history": state()}

    return router, record
