"""nookboard — MCP server exposing the nookboard REST API as agent tools.

Proxies the local nookboard FastAPI app (http://127.0.0.1:8765 by default) so any
MCP client (Claude, Cursor, Hermes, ...) can read and write notes in Da's vault.

Run (stdio transport, what most MCP clients expect):

    uv run python -m nookboard_mcp          # or however you invoke it


Environment:
    NOOKBOARD_MCP_BASE   base URL of the nookboard API (default http://127.0.0.1:8765)

Tool surface (notes CRUD — the core):
    nook_list_notes   query, read-only: list notes, optionally filtered
    nook_get_note     query, read-only: get one note by id
    nook_create_note  command, side effect: create a note
    nook_update_note  command, side effect: partial update of a note's fields
    nook_delete_note  command, side effect, IRREVERSIBLE (recoverable via UI history only)

This follows the agentic-tool-patterns conventions: LLM-optimized descriptions,
constrained inputs (enums), smart defaults, response shaping, recovery guides.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

#: Enums the nookboard model accepts. Constrained input (patterns: constrained-input).
SIGNIFIER = Literal["note", "event", "task"]
STATUS = Literal["open", "complete", "migrated", "scheduled", "irrelevant"]
STAGE = Literal["backlog", "todo", "doing", "review", "done"]

DEFAULT_BASE = "http://127.0.0.1:8765"
STARTUP_TIMEOUT = 15.0


def _base_url() -> str:
    return os.environ.get("NOOKBOARD_MCP_BASE", DEFAULT_BASE).rstrip("/")


def _api_is_ready(base: str) -> bool:
    try:
        response = httpx.get(f"{base}/api/health", timeout=0.5)
    except httpx.HTTPError:
        return False
    return response.status_code == 200 and response.json().get("ok") is True


def _start_api(base: str) -> subprocess.Popen | None:
    """Return an owned API process, or None when the endpoint is already ready."""
    if _api_is_ready(base):
        return None

    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"Cannot start Nookboard API for invalid base URL {base!r}.")
    if parsed.scheme == "https":
        raise RuntimeError("Cannot start an HTTPS Nookboard API; use an HTTP local endpoint.")

    env = os.environ.copy()
    env.setdefault("NOOKBOARD_VAULT", str(Path.cwd() / "vault"))
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        parsed.hostname,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Nookboard API exited during startup with code {process.returncode}."
            )
        if _api_is_ready(base):
            return process
        time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise RuntimeError(f"Nookboard API did not become ready at {base} within {STARTUP_TIMEOUT:g}s.")


def _stop_api(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_stdio() -> None:
    """Own the optional local API process for the duration of MCP stdio."""
    process = _start_api(_base_url())
    try:
        server.run()
    finally:
        _stop_api(process)


# ---------------------------------------------------------------------------
# Shared note schema — reused by create and update so both tools agree about
# what a note is (patterns: canonical-tool-model).
# ---------------------------------------------------------------------------
class NoteFields(BaseModel):
    """The mutable fields of a nookboard note (all optional; update touches only those present)."""

    title: Optional[str] = Field(
        None, description="Human title of the note. For a task/event this is usually the subject line."
    )
    body: Optional[str] = Field(None, description="Markdown body of the note. Empty for a bare task/event.")
    collection: Optional[str] = Field(
        None, description="Folder/collection the note lives in. Default is 'inbox'. e.g. 'school', 'projects'."
    )
    signifier: Optional[SIGNIFIER] = Field(
        None, description="BuJo bullet kind: 'task' (a to-do), 'event' (date-related), or 'note' (an observation)."
    )
    status: Optional[STATUS] = Field(
        None, description="Task state: 'open', 'complete', 'migrated', 'scheduled', 'irrelevant'."
    )
    dates: Optional[list[str]] = Field(
        None, description="ISO date(s) the note belongs to, e.g. ['2026-09-25']. Empty for undated."
    )
    at: Optional[str] = Field(
        None, description="Time of day 'HH:MM' (24h) when the note names an instant (appointment/class/call)."
    )
    until: Optional[str] = Field(
        None, description="End time 'HH:MM' when the note is a timed event."
    )
    path: Optional[str] = Field(
        None, description="A folder this note is about (makes it a workspace note). Kept exactly as written."
    )
    url: Optional[str] = Field(
        None, description="An address this note is about (makes it a bookmark). Kept exactly as written."
    )
    icon: Optional[str] = Field(
        None, description="A Lucide icon name (e.g. 'server', 'book-open', 'heart-pulse') drawn beside the note."
    )
    parent_id: Optional[str] = Field(None, description="Id of a parent note, if any.")
    mood: Optional[str] = Field(None, description="Mood level: one of great/good/meh/low/bad.")
    pain: Optional[int] = Field(
        None, description="Self-reported pain 0-10. Clamped to 0-10 on write."
    )
    tags: Optional[list[str]] = Field(None, description="Tags on the note, e.g. ['school', 'week-3'].")
    recurrence: Optional[str] = Field(
        None, description="Recurrence rule: 'daily', 'weekly', or 'monthly'."
    )
    stage: Optional[STAGE] = Field(
        None, description="Board column: 'backlog', 'todo', 'doing', 'review', 'done'. Auto-reconciled with status."
    )
    pinned: Optional[bool] = Field(None, description="True to float the note to the top of its board column.")
    blocked_by: Optional[list[str]] = Field(
        None, description="Ids of notes this note waits on (dependencies). Cycle-protected by the server."
    )
    position: Optional[float] = Field(None, description="Explicit ordering within its board column.")


class NoteCreate(NoteFields):
    id: str = Field(..., description="Unique id for the note (the vault filename stem). Must be unique.")
    title: str = Field(..., description="Human title of the note.")


server = MCPServer(
    "nookboard-mcp",
    title="nookboard",
    version="0.1.0",
    description=(
        "Read and write notes in Da's nookboard vault. Tools map to the nookboard "
        "REST API: list/get notes, create/update/delete notes. Tasks are notes with "
        "signifier 'task'; the vault is the universal record for notes, tasks, events, "
        "bookmarks, and workspace links."
    ),
)


# -- HTTP plumbing ---------------------------------------------------------
async def _request(method: str, path: str, *, params: dict | None = None, json_body=None):
    """Call the nookboard API and return parsed JSON, raising a clear agent-facing error."""
    try:
        async with httpx.AsyncClient(base_url=_base_url(), timeout=15.0) as client:
            resp = await client.request(method, path, params=params, json=json_body)
    except httpx.ConnectError as exc:
        raise ToolError(
            f"Could not reach the nookboard server at {_base_url()}. "
            f"Is it running? Start it with `make dev` (uv run uvicorn app.main:app --port 8765). "
            f"ConnectError: {exc}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError(f"The nookboard server at {_base_url()} timed out. Try again.") from exc

    if resp.status_code == 204:  # DELETE returns empty
        return None
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail") or resp.text
        except Exception:
            detail = resp.text
        raise ToolError(
            f"nookboard API {method} {path} failed ({resp.status_code}). "
            f"{detail} (If this is a 404, the note id is wrong or it was deleted.)"
        )
    return resp.json()


def _shape(note: dict) -> dict:
    """Response shaper: drop noise fields the agent doesn't need for most reads."""
    return {
        "id": note.get("id"),
        "title": note.get("title"),
        "collection": note.get("collection"),
        "signifier": note.get("signifier"),
        "status": note.get("status"),
        "stage": note.get("stage"),
        "dates": note.get("dates"),
        "at": note.get("at"),
        "tags": note.get("tags"),
        "mood": note.get("mood"),
        "pain": note.get("pain"),
        "url": note.get("url"),
        "path": note.get("path"),
        "body_preview": (note.get("body") or "")[:200] or None,
    }


def _jsonify(value) -> str:
    """Every tool returns exactly ONE compact JSON text block.

    Relying on mcp 2.x's structured-output inference is fragile: a list[str]
    return becomes one TextContent per element (N blocks), which clients parse
    poorly. Shaping every tool result through this guarantees one parseable
    JSON block regardless of the underlying type (patterns: response-shaper,
    token-efficient-response).
    """
    return json.dumps(value, ensure_ascii=False)


# -- Query tools -----------------------------------------------------------
@server.tool(
    name="nook_list_notes",
    description=(
        "List notes from the nookboard vault. Read-only. Optionally filter by collection "
        "(folder), an ISO date (YYYY-MM-DD), or a tag. Every returned note shows id, title, "
        "collection, signifier, status, stage, dates, tags. Use nook_get_note for full body. "
        "To find what collections exist, call nook_list_collections."
    ),
)
async def nook_list_notes(
    collection: Optional[str] = None,
    date: Optional[str] = None,
    tag: Optional[str] = None,
) -> str:
    """List notes, optionally filtered. `date` is an ISO date (2026-09-25)."""
    params = {}
    if collection:
        params["collection"] = collection
    if date:
        params["date"] = date
    if tag:
        params["tag"] = tag
    notes = await _request("GET", "/api/notes", params=params)
    return _jsonify([_shape(n) for n in notes])


@server.tool(
    name="nook_get_note",
    description=(
        "Get one full note from nookboard by its id (the vault filename stem). Read-only. "
        "Returns the complete record including body, blocked_by, and history-free detail. "
        "If you don't know the id, call nook_list_notes first."
    ),
)
async def nook_get_note(note_id: str) -> str:
    """Fetch a single note by id."""
    return _jsonify(await _request("GET", f"/api/notes/{note_id}"))


@server.tool(
    name="nook_list_collections",
    description=(
        "List the collections (folders) that exist in the nookboard vault. Read-only. "
        "Use this before creating a note so you place it in a real collection; "
        "the default is 'inbox'."
    ),
)
async def nook_list_collections() -> str:
    """List all collections present in the vault."""
    return _jsonify(await _request("GET", "/api/collections"))


# -- Command tools ---------------------------------------------------------
@server.tool(
    name="nook_create_note",
    description=(
        "Create a new note in nookboard. Command (writes to the vault). Requires a unique "
        "`id` and a `title`. Common uses: add a task (signifier='task'), log an event "
        "(signifier='event', dates=[...]), or file a note (signifier='note'). "
        "Defaults: collection 'inbox', signifier 'note', status 'open'. "
        "Returns the created note with its resolved stage."
    ),
)
async def nook_create_note(data: NoteCreate) -> str:
    """Create a note. `data` carries id, title and any optional fields."""
    payload = data.model_dump(exclude_unset=True, exclude_none=True)
    return _jsonify(await _request("POST", "/api/notes", json_body=payload))


@server.tool(
    name="nook_update_note",
    description=(
        "Update one or more fields of an existing nookboard note by id. Command (writes to "
        "the vault). This is a PARTIAL update: only the fields you pass are changed; omitted "
        "fields keep their current value. To clear a field (e.g. remove mood) pass an explicit "
        "null. To complete a task set status='complete' (or move stage to 'done'); the server "
        "reconciles stage and status automatically. Returns the updated note."
    ),
)
async def nook_update_note(note_id: str, data: NoteFields) -> str:
    """Partially update a note. Only fields present in `data` are changed."""
    payload = data.model_dump(exclude_unset=True, exclude_none=True)
    return _jsonify(await _request("PATCH", f"/api/notes/{note_id}", json_body=payload))


@server.tool(
    name="nook_delete_note",
    description=(
        "Delete a note from nookboard by id. Command — this removes the note from the vault. "
        "IRREVERSIBLE via the API: the note is deleted from disk (recoverable only through the "
        "nookboard UI history, not this server). Only call this when the agent/user explicitly "
        "wants a note gone. Returns {'deleted': note_id}."
    ),
)
async def nook_delete_note(note_id: str) -> str:
    """Delete a note. Irreversible — use only on explicit instruction."""
    await _request("DELETE", f"/api/notes/{note_id}")
    return _jsonify({"deleted": note_id})


if __name__ == "__main__":
    run_stdio()
