"""The sieve endpoints, with the network replaced by a fake client.

What is being checked is the app's half of the contract: that a start persists
its id before it answers, that a follow-up is recorded before it is waited on,
that an unconfigured instance refuses rather than pretends, and that a delivered
file is proxied through this server because the key never reaches a browser.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeClient:
    """A sieve client that answers from memory. Records what it was asked."""

    instances: list["FakeClient"] = []

    def __init__(self, api_key: str, base_url: str, **kwargs):
        self.api_key = api_key
        self.base_url = base_url
        self.started: list[dict] = []
        self.messages: list[tuple[str, dict]] = []
        FakeClient.instances.append(self)

    async def start(self, body: dict) -> dict:
        self.started.append(body)
        return {"status": "queued", "session_id": "sv-1", "poll": "/api/scrapes/sv-1"}

    async def poll(self, session_id: str) -> dict:
        return {"status": "done", "turns": 1, "result": {"quotes": []}}

    async def send_message(self, session_id: str, body: dict) -> dict:
        self.messages.append((session_id, body))
        return {"status": "running"}

    async def fetch_file(self, url: str) -> tuple[bytes, str]:
        return b"text,author\n", "text/csv"

    async def credits(self) -> dict:
        return {"plan": "free", "limit": 20, "used": 1, "remaining": 19}


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        vault=tmp_path,
        llm_url="http://127.0.0.1:1/v1",
        llm_model="fake",
        llm_max_tokens=8,
        llm_timeout=1.0,
        **overrides,
    )


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """An app with sieve configured and the client swapped for the fake.

    No context manager, so the lifespan (and its poll loop) never starts: these
    tests are about the routes and the recorded state, not the timer.
    """
    FakeClient.instances.clear()
    monkeypatch.setattr("app.main.SieveClient", FakeClient)
    app = create_app(settings=_settings(tmp_path, sieve_api_key="dc_sk_test"))
    return TestClient(app)


def test_an_unconfigured_instance_says_so_and_refuses_to_start(tmp_path):
    client = TestClient(create_app(settings=_settings(tmp_path)))
    assert client.get("/api/sieve").json()["configured"] is False
    assert client.post("/api/sieve/scrapes", json={"instruction": "x"}).status_code == 503
    assert client.get("/api/sieve/credits").status_code == 503


def test_starting_a_scrape_persists_it_before_it_returns(configured):
    response = configured.post(
        "/api/sieve/scrapes",
        json={"instruction": "Extract quotes", "target_urls": ["https://quotes.toscrape.com"]},
    )
    assert response.status_code == 202, response.text
    assert response.json() == {
        "status": "queued",
        "session_id": "sv-1",
        "poll": "/api/scrapes/sv-1",
    }
    assert FakeClient.instances[-1].started[0]["compliance_mode"] == "regular"
    stored = configured.get("/api/sieve/scrapes/sv-1").json()
    assert stored["status"] == "queued"
    assert stored["request"]["instruction"] == "Extract quotes"


def test_a_request_without_an_instruction_is_refused_before_any_call(configured):
    response = configured.post("/api/sieve/scrapes", json={})
    assert response.status_code == 400
    assert "instruction" in response.json()["detail"]
    assert FakeClient.instances[-1].started == []


def test_an_unknown_run_is_a_404(configured):
    assert configured.get("/api/sieve/scrapes/nope").status_code == 404


def test_a_follow_up_needs_a_finished_run(configured):
    configured.post("/api/sieve/scrapes", json={"instruction": "x"})
    response = configured.post(
        "/api/sieve/scrapes/sv-1/messages", json={"instruction": "and the tags"}
    )
    assert response.status_code == 409


def test_a_follow_up_records_the_next_turn_before_waiting_on_it(configured):
    configured.post("/api/sieve/scrapes", json={"instruction": "x"})
    db = configured.app.state.db
    db.set_sieve_session(
        "sv-1", status="done", turns=2, payload={"status": "done", "turns": 2}, next_poll_at=None
    )

    response = configured.post(
        "/api/sieve/scrapes/sv-1/messages", json={"instruction": "and the tags"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["awaiting_turn"] == 3
    row = db.get_sieve_session("sv-1")
    assert row["status"] == "running"
    assert row["awaiting_turn"] == 3
    assert FakeClient.instances[-1].messages[-1] == (
        "sv-1",
        {"instruction": "and the tags", "compliance_mode": "regular"},
    )


def test_a_delivered_file_is_proxied_through_this_server(configured):
    response = configured.get("/api/sieve/files", params={"url": "/files/quotes.csv"})
    assert response.status_code == 200
    assert response.content == b"text,author\n"
    assert response.headers["content-type"].startswith("text/csv")


def test_a_file_from_another_origin_is_refused(configured):
    response = configured.get("/api/sieve/files", params={"url": "https://evil.test/x.csv"})
    assert response.status_code == 400


def test_credits_round_trip(configured):
    assert configured.get("/api/sieve/credits").json()["remaining"] == 19
