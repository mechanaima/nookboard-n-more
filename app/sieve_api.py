"""HTTP routes for sieve scrapes.

The router owns the shape of a request and the mapping from a classified sieve
failure back onto an HTTP status; the durable state and the polling live in
`sieve_run`, and the decisions live in `sieve`. Nothing here talks to the
network directly, so the whole surface can be exercised against a fake client.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Response

from . import sieve
from .config import Settings
from .db import Database
from .sieve import SieveError, SieveFailure, SieveHTTPError, SieveStartAmbiguous
from .sieve_run import SieveClient, start_run

#: What a classified failure becomes on the wire. 5xx is deliberately 502:
#: sieve is an upstream, and this app is not the one that broke.
_FAILURE_STATUS: dict[str, int] = {
    "bad_request": 400,
    "unauthorized": 401,
    "out_of_credits": 402,
    "not_found": 404,
    "conflict": 409,
    "rate_limited": 429,
    "server_error": 502,
    "http_error": 502,
    "transport": 502,
}


def http_status_for(failure: SieveFailure) -> int:
    return _FAILURE_STATUS.get(failure.kind, 502)


def build_sieve_router(
    db: Database,
    client: SieveClient | None,
    settings: Settings,
    last_error: Callable[[], str | None],
) -> APIRouter:
    """Build sieve routes from the app's existing db, client and settings."""
    router = APIRouter()

    def _not_configured() -> HTTPException:
        return HTTPException(
            503,
            "sieve is not configured - put SIEVE_API_KEY in .env "
            "(tools/sieve-login.py does this) and restart",
        )

    def _body(payload: dict) -> dict:
        return sieve.scrape_body(
            payload.get("instruction", ""),
            target_urls=payload.get("target_urls"),
            fields=payload.get("fields"),
            schema=payload.get("schema"),
            output_schema=payload.get("output_schema"),
            table_shape=payload.get("table_shape"),
            compliance_mode=payload.get("compliance_mode"),
        )

    def _validated(payload: dict) -> dict:
        body = _body(payload)
        problems = sieve.validate_scrape_request(body)
        if problems:
            raise HTTPException(400, "; ".join(problems))
        return body

    @router.get("/api/sieve")
    def sieve_status():
        """Whether sieve is configured, and what has run."""
        return {
            "configured": client is not None,
            "base_url": settings.sieve_base_url,
            "last_error": last_error(),
            "sessions": db.list_sieve_sessions(),
        }

    @router.post("/api/sieve/scrapes", status_code=202)
    async def start_scrape(payload: dict):
        """Start a run. The session id is persisted before this returns."""
        if client is None:
            raise _not_configured()
        body = _validated(payload)
        try:
            accepted = await start_run(db, client, body)
        except SieveStartAmbiguous as exc:
            # 504, not 500: the upstream did not answer, and the run may exist.
            raise HTTPException(504, str(exc))
        except SieveHTTPError as exc:
            raise HTTPException(http_status_for(exc.failure), exc.failure.message)
        except SieveError as exc:
            raise HTTPException(502, str(exc))
        session_id = str(accepted.get("session_id"))
        return {
            "status": "queued",
            "session_id": session_id,
            "poll": accepted.get("poll") or f"/api/scrapes/{session_id}",
        }

    @router.get("/api/sieve/scrapes")
    def list_scrapes():
        return {
            "configured": client is not None,
            "sessions": db.list_sieve_sessions(),
        }

    @router.get("/api/sieve/scrapes/{session_id}")
    def get_scrape(session_id: str):
        session = db.get_sieve_session(session_id)
        if session is None:
            raise HTTPException(404, f"no sieve run {session_id!r}")
        return session

    @router.post("/api/sieve/scrapes/{session_id}/messages", status_code=202)
    async def add_scrape_message(session_id: str, payload: dict):
        """A follow-up turn on a finished run.

        Recorded before polling: the run reads `done` the moment the message is
        accepted, so polling has to wait for the *turn* and not just the status.
        """
        if client is None:
            raise _not_configured()
        session = db.get_sieve_session(session_id)
        if session is None:
            raise HTTPException(404, f"no sieve run {session_id!r}")
        if session["status"] != "done":
            raise HTTPException(
                409,
                f"the run is {session['status']!r}; a follow-up needs a finished run",
            )
        body = _validated(payload)
        try:
            await client.send_message(session_id, body)
        except SieveHTTPError as exc:
            raise HTTPException(http_status_for(exc.failure), exc.failure.message)
        except SieveError as exc:
            raise HTTPException(502, str(exc))
        moment = datetime.now()
        awaiting = int(session["turns"]) + 1
        db.set_sieve_awaiting_turn(session_id, awaiting, body, at=moment)
        db.set_sieve_session(
            session_id,
            status="running",
            next_poll_at=(moment + timedelta(seconds=sieve.POLL_START)).isoformat(
                timespec="seconds"
            ),
            at=moment,
        )
        return {
            "status": "running",
            "session_id": session_id,
            "awaiting_turn": awaiting,
            "poll": f"/api/scrapes/{session_id}",
        }

    @router.get("/api/sieve/files")
    async def download_sieve_file(url: str = Query(...)):
        """Fetch a delivered file, prefixing the base and sending the Bearer header.

        `files[].url` is relative and the key never goes to a browser, so the
        server is the only party that can fetch it. The origin check stops this
        being an authenticated proxy for anything the response happened to name.
        """
        if client is None:
            raise _not_configured()
        if not sieve.is_safe_file_url(url, settings.sieve_base_url):
            raise HTTPException(400, "that file url is not on the sieve origin")
        try:
            content, media_type = await client.fetch_file(url)
        except SieveHTTPError as exc:
            raise HTTPException(http_status_for(exc.failure), exc.failure.message)
        except SieveError as exc:
            raise HTTPException(502, str(exc))
        name = sieve.absolute_file_url(url, settings.sieve_base_url).rsplit("/", 1)[-1]
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.get("/api/sieve/credits")
    async def sieve_credits():
        """Plan, limit, used, remaining - so "out of credits" has a number."""
        if client is None:
            raise _not_configured()
        try:
            return await client.credits()
        except SieveHTTPError as exc:
            raise HTTPException(http_status_for(exc.failure), exc.failure.message)
        except SieveError as exc:
            raise HTTPException(502, str(exc))

    return router
