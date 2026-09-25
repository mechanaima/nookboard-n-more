"""The sieve client and the durable polling, against the HTTP boundary.

Recorded responses are used only at the transport: `httpx.MockTransport` answers
the requests, and the code under test is the real `SieveClient` and the real
poll orchestration. The properties that matter are the ones that cost money or
show the wrong answer -- a start that is never retried, a turn that is not read
before it lands, and a failure that either backs off or stops.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import sieve as S
from app.db import Database
from app.sieve import SieveError, SieveHTTPError, SieveStartAmbiguous
from app.sieve_run import SieveClient, poll_once, start_run, tick


def _client(handler, *, base: str = "https://sieve.test", key: str = "dc_sk_test") -> SieveClient:
    return SieveClient(key, base, transport=httpx.MockTransport(handler))


def _answering(payload, status: int = 200) -> SieveClient:
    return _client(lambda request: httpx.Response(status, json=payload))


def _db(tmp_path) -> Database:
    return Database(tmp_path / "index.sqlite")


def _seeded(db: Database, *, status: str = "queued") -> dict:
    db.save_sieve_session(
        session_id="s1", instruction="x", request={"instruction": "x"}, status=status
    )
    return db.get_sieve_session("s1")


# -- the request on the wire -------------------------------------------------


def test_start_sends_the_bearer_header_and_the_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content.decode())
        return httpx.Response(202, json={"status": "queued", "session_id": "s1"})

    payload = asyncio.run(_client(handler).start({"instruction": "x", "compliance_mode": "regular"}))
    assert payload["session_id"] == "s1"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://sieve.test/api/scrapes"
    assert seen["auth"] == "Bearer dc_sk_test"
    assert seen["body"] == {"instruction": "x", "compliance_mode": "regular"}


def test_a_timed_out_start_is_ambiguous_and_never_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("no answer", request=request)

    with pytest.raises(SieveStartAmbiguous):
        asyncio.run(_client(handler).start({"instruction": "x"}))
    assert calls["n"] == 1, "the first call may have succeeded; retrying could start a second run"


def test_a_429_start_is_retryable_by_the_caller_but_not_retried_here():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"error": "slow down"}, headers={"retry-after": "7"})

    with pytest.raises(SieveHTTPError) as caught:
        asyncio.run(_client(handler).start({"instruction": "x"}))
    assert caught.value.failure.kind == "rate_limited"
    assert caught.value.failure.retryable
    assert caught.value.failure.retry_after == 7
    assert calls["n"] == 1


def test_a_dropped_read_is_a_retryable_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("dropped", request=request)

    with pytest.raises(SieveHTTPError) as caught:
        asyncio.run(_client(handler).poll("s1"))
    assert caught.value.failure.kind == "transport"
    assert caught.value.failure.retryable


def test_a_409_on_a_follow_up_is_a_conflict():
    client = _client(lambda request: httpx.Response(409, json={"error": "turn in flight"}))
    with pytest.raises(SieveHTTPError) as caught:
        asyncio.run(client.send_message("s1", {"instruction": "more"}))
    assert caught.value.failure.kind == "conflict"
    assert caught.value.failure.retryable


def test_a_file_is_fetched_with_the_bearer_header_from_the_sieve_origin():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200, content=b"text,author\n", headers={"content-type": "text/csv"}
        )

    content, media = asyncio.run(_client(handler).fetch_file("/files/quotes.csv"))
    assert content == b"text,author\n"
    assert media == "text/csv"
    assert seen["url"] == "https://sieve.test/files/quotes.csv"
    assert seen["auth"] == "Bearer dc_sk_test"


def test_a_foreign_file_url_is_refused_before_any_request():
    calls: list = []
    client = _client(lambda request: calls.append(request) or httpx.Response(200))
    with pytest.raises(SieveError):
        asyncio.run(client.fetch_file("https://evil.test/x.csv"))
    assert calls == []


# -- starting a run ----------------------------------------------------------


def test_start_run_persists_the_id_before_returning(tmp_path):
    db = _db(tmp_path)
    client = _answering({"status": "queued", "session_id": "sv", "poll": "/api/scrapes/sv"}, 202)
    payload = asyncio.run(start_run(db, client, {"instruction": "x"}))
    assert payload["session_id"] == "sv"
    stored = db.get_sieve_session("sv")
    assert stored is not None and stored["status"] == "queued"
    assert stored["next_poll_at"] is not None


def test_an_ambiguous_start_writes_nothing(tmp_path):
    db = _db(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("x", request=request)

    with pytest.raises(SieveStartAmbiguous):
        asyncio.run(start_run(db, _client(handler), {"instruction": "x"}))
    assert db.list_sieve_sessions() == []


# -- polling one run ---------------------------------------------------------


def test_a_running_poll_stays_pending_and_books_the_next_one(tmp_path):
    db = _db(tmp_path)
    session = _seeded(db)
    assert asyncio.run(poll_once(db, _answering({"status": "running", "turns": 1}), session)) == "running"
    row = db.get_sieve_session("s1")
    assert row["status"] == "running"
    assert row["turns"] == 1
    assert row["next_poll_at"] is not None


def test_a_done_poll_records_the_result_and_stops(tmp_path):
    db = _db(tmp_path)
    session = _seeded(db)
    payload = {
        "status": "done",
        "turns": 0,
        "result": {"quotes": []},
        "files": [{"name": "q.csv", "url": "/files/q.csv"}],
    }
    assert asyncio.run(poll_once(db, _answering(payload), session)) == "done"
    row = db.get_sieve_session("s1")
    assert row["status"] == "done"
    assert row["result"]["result"] == {"quotes": []}
    assert row["next_poll_at"] is None


def test_a_refused_run_is_terminal_and_keeps_the_code(tmp_path):
    db = _db(tmp_path)
    session = _seeded(db)
    assert asyncio.run(poll_once(db, _answering({"status": "refused", "refusal": {"code": "quota"}}), session)) == "refused"
    row = db.get_sieve_session("s1")
    assert row["status"] == "refused"
    assert S.refusal_code(row["result"]) == "quota"
    assert row["next_poll_at"] is None


def test_done_before_the_recorded_turn_keeps_polling(tmp_path):
    """A follow-up is answered only once the turn it added has landed."""
    db = _db(tmp_path)
    _seeded(db, status="done")
    db.set_sieve_awaiting_turn("s1", 2, {"instruction": "more"})

    # The previous answer: done, but only one turn on the server.
    assert asyncio.run(poll_once(db, _answering({"status": "done", "turns": 1}), db.get_sieve_session("s1"))) == "running"
    assert db.get_sieve_session("s1")["status"] == "running"

    # The new turn lands.
    assert asyncio.run(
        poll_once(db, _answering({"status": "done", "turns": 2, "result": {"x": 1}}), db.get_sieve_session("s1"))
    ) == "done"
    row = db.get_sieve_session("s1")
    assert row["awaiting_turn"] is None
    assert row["result"]["result"] == {"x": 1}


def test_an_unknown_status_is_an_error(tmp_path):
    db = _db(tmp_path)
    session = _seeded(db)
    with pytest.raises(SieveError):
        asyncio.run(poll_once(db, _answering({"status": "wat"}), session))


# -- ticking many runs -------------------------------------------------------


def test_tick_survives_an_unknown_status_and_records_it(tmp_path):
    db = _db(tmp_path)
    _seeded(db)
    result = asyncio.run(tick(db, _answering({"status": "wat"})))
    assert result["polled"] == 1
    row = db.get_sieve_session("s1")
    assert row["status"] in ("queued", "running")
    assert row["error"], "the reason polling is quiet is kept, not swallowed"


def test_tick_skips_a_run_that_is_not_due_yet(tmp_path):
    db = _db(tmp_path)
    db.save_sieve_session(
        session_id="later", instruction="x", request={}, next_poll_at="2999-01-01T00:00:00"
    )
    calls: list = []
    result = asyncio.run(tick(db, _client(lambda request: calls.append(request) or httpx.Response(200, json={"status": "running"}))))
    assert result["polled"] == 0
    assert calls == []


def test_tick_stops_polling_a_terminal_failure(tmp_path):
    db = _db(tmp_path)
    _seeded(db)
    result = asyncio.run(tick(db, _answering({"error": "nope"}, 401)))
    assert result["polled"] == 1
    row = db.get_sieve_session("s1")
    assert row["status"] == "error"
    assert "key" in (row["error"] or "")
