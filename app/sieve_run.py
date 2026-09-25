"""The half of the sieve integration that opens sockets.

`sieve` decides what an answer *means*; this is the half that asks. It reuses
the project's HTTP client (`httpx`, the same one `llm.py` speaks to llama.cpp
with) rather than growing a second one, and it keeps the one rule that protects
the user's credits:

- **A start is never retried.** `POST /api/scrapes` has no idempotency key and
  an accepted call creates (and bills for) a run, so a timeout or a dropped
  connection is *ambiguous* -- it raises `SieveStartAmbiguous` and the caller
  tells the user to look at their account. Retrying after 429 or 5xx is safe
  because no run was created, and those stay ordinary classified failures.
- **The session id is durable before it is returned.** `start_run` writes it to
  the database the moment the 202 arrives, so a crash resumes polling instead of
  starting a duplicate.
- **Polling is a loop with a backoff, never a blocking request.** A scrape is
  minutes of work, so `tick` is called on a timer and only polls runs that are
  actually due.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from . import sieve
from .db import Database
from .sieve import (
    SieveError,
    SieveFailure,
    SieveHTTPError,
    SieveStartAmbiguous,
)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class SieveClient:
    """Talks to the sieve scrape API. One instance per app, keyed on the secret."""

    def __init__(
        self,
        api_key: str,
        base_url: str = sieve.DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        #: A recorded/mocked transport is how the tests answer without a network;
        #: the logic under test is still this code.
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        # The key has full account access and no scopes, so it is sent only from
        # the server and never returned to a browser or written to a log.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport)

    def _failure(self, resp: httpx.Response) -> SieveFailure:
        try:
            body: object = resp.json()
        except ValueError:
            body = resp.text
        return sieve.classify_error(
            resp.status_code, body, retry_after=_retry_after(resp)
        )

    @staticmethod
    def _json(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as exc:
            raise SieveError(
                f"sieve answered {resp.status_code} with something that is not JSON"
            ) from exc
        if not isinstance(data, dict):
            raise SieveError("sieve answered with something that is not an object")
        return data

    async def start(self, body: dict) -> dict:
        """`POST /api/scrapes`. Ambiguous on a transport error; never retried here."""
        try:
            async with await self._client() as client:
                resp = await client.post(
                    self._url("/api/scrapes"), json=body, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise SieveStartAmbiguous(
                "sieve did not answer, so whether the run started is unknown; "
                f"not retrying automatically because the first call may have "
                f"succeeded and spent credits ({exc})"
            ) from exc
        if resp.status_code >= 300:
            raise SieveHTTPError(self._failure(resp))
        return self._json(resp)

    async def poll(self, session_id: str) -> dict:
        """`GET /api/scrapes/<id>`. A read, so a transport error is retryable."""
        try:
            async with await self._client() as client:
                resp = await client.get(
                    self._url(f"/api/scrapes/{session_id}"), headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise SieveHTTPError(
                sieve.transport_failure(f"could not reach sieve to poll: {exc}")
            ) from exc
        if resp.status_code >= 300:
            raise SieveHTTPError(self._failure(resp))
        return self._json(resp)

    async def send_message(self, session_id: str, body: dict) -> dict:
        """`POST /api/scrapes/<id>/messages`. A 409 means a turn is already in flight."""
        try:
            async with await self._client() as client:
                resp = await client.post(
                    self._url(f"/api/scrapes/{session_id}/messages"),
                    json=body,
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise SieveHTTPError(
                sieve.transport_failure(
                    f"could not reach sieve to send the follow-up: {exc}"
                )
            ) from exc
        if resp.status_code >= 300:
            raise SieveHTTPError(self._failure(resp))
        return self._json(resp)

    async def fetch_file(self, url: str) -> tuple[bytes, str]:
        """Download a delivered file, with the Bearer header.

        `files[].url` is relative, so it is resolved against the base; an
        absolute url is accepted only when it is the same origin.
        """
        if not sieve.is_safe_file_url(url, self.base_url):
            raise SieveError("refusing to fetch a file from outside the sieve origin")
        target = sieve.absolute_file_url(url, self.base_url)
        try:
            async with await self._client() as client:
                resp = await client.get(target, headers=self._headers())
        except httpx.HTTPError as exc:
            raise SieveHTTPError(
                sieve.transport_failure(f"could not download the file: {exc}")
            ) from exc
        if resp.status_code >= 300:
            raise SieveHTTPError(self._failure(resp))
        return resp.content, resp.headers.get("content-type", "application/octet-stream")

    async def credits(self) -> dict:
        """`GET /api/me/credits`: plan, limit, used, remaining."""
        try:
            async with await self._client() as client:
                resp = await client.get(
                    self._url("/api/me/credits"), headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise SieveHTTPError(
                sieve.transport_failure(f"could not reach sieve for credits: {exc}")
            ) from exc
        if resp.status_code >= 300:
            raise SieveHTTPError(self._failure(resp))
        return self._json(resp)


# -- durable polling ---------------------------------------------------------


async def start_run(
    db: Database, client: SieveClient, body: dict, now: datetime | None = None
) -> dict:
    """Start one run, persist its id, and only then return.

    The order is the point: a 202 whose id was forgotten is a run polling can
    never resume and the user paid for. A transport error raises before anything
    is written, and nothing here retries it.
    """
    payload = await client.start(body)
    session_id = payload.get("session_id")
    if not session_id:
        raise SieveError("sieve accepted the run but returned no session id")
    moment = now or datetime.now()
    db.save_sieve_session(
        session_id=str(session_id),
        instruction=str(body.get("instruction") or ""),
        request=body,
        status="queued",
        next_poll_at=_iso(moment + timedelta(seconds=sieve.POLL_START)),
        at=moment,
    )
    return payload


async def poll_once(
    db: Database, client: SieveClient, session: dict, now: datetime | None = None
) -> str:
    """One GET for one run, and record what it means. Returns the new status."""
    moment = now or datetime.now()
    session_id = session["session_id"]
    payload = await client.poll(session_id)
    status = sieve.run_status(payload)  # raises on any status we do not understand
    turns = sieve.turns_of(payload)

    if status == sieve.REFUSED:
        # Terminal: the run never started. The failure code says why (credit
        # limits show up as refusal.quota); the payload carries it.
        db.set_sieve_session(
            session_id, status="refused", turns=turns, payload=payload, next_poll_at=None
        )
        return "refused"

    if status == sieve.DONE and sieve.ready_for_answer(payload, session.get("awaiting_turn")):
        db.set_sieve_session(
            session_id, status="done", turns=turns, payload=payload, next_poll_at=None
        )
        if session.get("awaiting_turn") is not None:
            db.clear_sieve_awaiting_turn(session_id, at=moment)
        return "done"

    # Still running, or `done` before the recorded follow-up turn landed -- the
    # latter is the previous answer and must not be handed over as the new one.
    delay = sieve.next_poll_delay(session.get("poll_delay"))
    db.set_sieve_session(
        session_id,
        status="running",
        turns=turns,
        poll_delay=delay,
        next_poll_at=_iso(moment + timedelta(seconds=delay)),
    )
    return "running"


def _record_failure(
    db: Database, session: dict, failure: SieveFailure, now: datetime
) -> None:
    """A failed poll: back off and retry, or stop, depending on what it was.

    A retryable failure keeps the run pending and backs the next poll off, so a
    sieve outage costs a quiet retry rather than the run; the message is kept so
    the API can say why polling is quiet. A non-retryable one (a revoked key, an
    empty account) is terminal, because asking again would change nothing.
    """
    session_id = session["session_id"]
    if failure.retryable:
        delay = sieve.next_poll_delay(session.get("poll_delay"))
        db.set_sieve_session(
            session_id,
            status=session["status"],
            error=failure.message,
            poll_delay=delay,
            next_poll_at=_iso(now + timedelta(seconds=delay)),
        )
        return
    db.set_sieve_session(
        session_id, status="error", error=failure.message, next_poll_at=None
    )


async def tick(db: Database, client: SieveClient, now: datetime | None = None) -> dict:
    """Poll every run that is due. Never raises: one bad run is not the loop."""
    moment = now or datetime.now()
    polled = 0
    for session in db.pending_sieve_sessions():
        due = session.get("next_poll_at")
        if due:
            try:
                if datetime.fromisoformat(due) > moment:
                    continue
            except ValueError:
                pass  # an unreadable time is a due time, not a skipped run
        polled += 1
        try:
            await poll_once(db, client, session, moment)
        except SieveHTTPError as exc:
            _record_failure(db, session, exc.failure, moment)
        except SieveError as exc:
            # Includes SieveStartAmbiguous, which cannot happen on a poll path
            # but is a SieveError, and a status we did not understand.
            _record_failure(db, session, sieve.transport_failure(str(exc)), moment)
    return {"polled": polled}
