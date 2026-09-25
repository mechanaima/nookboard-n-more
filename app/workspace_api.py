"""HTTP routes and shared state for notes that point at workspace folders."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from . import workspace, workspace_run
from .models import Note
from .vault import Vault


WorkspaceStateReader = Callable[[Sequence[Note]], list[dict]]


def build_workspace_router(vault: Vault) -> tuple[APIRouter, WorkspaceStateReader]:
    """Build workspace routes and the state reader shared with the dashboard."""
    router = APIRouter()

    def states(notes: Sequence[Note]) -> list[dict]:
        """Read every workspace note's folder now, in display order."""
        now = datetime.now(timezone.utc)
        found = []
        for note in notes:
            if not workspace.is_workspace_note(note):
                continue
            state = workspace_run.state_for_note(note, now=now)
            if state:
                found.append(state)
        return workspace.sort_states(found)

    @router.get("/api/workspaces")
    def list_workspaces():
        """Every note that points at a folder, and what that folder is now."""
        current = states(vault.list_all())
        summary = workspace.summarize(current)
        return {
            "summary": summary,
            "line": workspace.attention_line(summary),
            "workspaces": current,
            "tools": workspace_run.tools(),
        }

    @router.get("/api/workspaces/{note_id}")
    def get_workspace(note_id: str):
        """One workspace, or a 404 when the note is absent or not a workspace."""
        try:
            note = vault.read(note_id)
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, f"no note {note_id!r}")
        state = workspace_run.state_for_note(
            note, now=datetime.now(timezone.utc)
        )
        if state is None:
            raise HTTPException(
                404, f"note {note_id!r} does not point at a folder"
            )
        state["tools"] = workspace_run.tools()
        return state

    @router.post("/api/workspaces/{note_id}/open")
    def open_workspace(note_id: str, payload: dict, request: Request):
        """Open a workspace in an editor, terminal, or file browser."""
        if request.headers.get("x-nookboard-action") != "open":
            raise HTTPException(
                403, "this action needs the app's own request (X-Nookboard-Action)"
            )
        what = str(payload.get("what") or "").strip()
        if what not in workspace.OPEN_ACTIONS:
            raise HTTPException(
                400, f"what must be one of {list(workspace.OPEN_ACTIONS)}"
            )
        try:
            note = vault.read(note_id)
        except (KeyError, FileNotFoundError, ValueError):
            raise HTTPException(404, f"no note {note_id!r}")
        path = workspace.path_field(note)
        if path is None:
            raise HTTPException(
                404, f"note {note_id!r} does not point at a folder"
            )
        result = workspace_run.open_workspace(
            path, what, file=payload.get("file"), line=payload.get("line")
        )
        if not result.get("ok"):
            raise HTTPException(
                400 if result.get("invalid") else 409,
                result.get("error") or "could not open it",
            )
        return result

    return router, states
