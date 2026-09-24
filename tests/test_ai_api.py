"""TDD: the /api/ai/* endpoints.

The llama.cpp server is replaced by a fake SSE server so these run offline and
assert the NDJSON protocol the browser consumes, plus the finishing step that
turns model text into structured tags / links / citations.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _chunk(*, content=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def _parse_ndjson(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


@pytest.fixture
def client(tmp_path: Path, sse_server):
    base, set_script = sse_server
    settings = Settings(
        vault=tmp_path,
        llm_url=base,
        llm_model="fake",
        llm_max_tokens=2048,
        llm_timeout=30.0,
    )
    app = create_app(settings=settings)
    c = TestClient(app)
    c.set_script = set_script  # type: ignore[attr-defined]
    return c


def _seed(c):
    c.post("/api/notes", json={
        "id": "n1", "collection": "inbox", "title": "Garden tour",
        "body": "roses and basil in the garden", "signifier": "note",
        "status": "open", "dates": [], "tags": ["garden"],
    })
    c.post("/api/notes", json={
        "id": "n2", "collection": "inbox", "title": "Buying plants",
        "body": "rose, basil, mint", "signifier": "note",
        "status": "open", "dates": [], "tags": [],
    })


# --- summarize ------------------------------------------------------------

def test_summarize_streams_content_then_done(client):
    _seed(client)
    client.set_script([
        _chunk(reasoning="thinking"),
        _chunk(content="A short summary."),
        _chunk(finish="stop"),
    ])
    r = client.post("/api/ai/summarize", json={"id": "n1"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = _parse_ndjson(r.text)
    kinds = [e["kind"] for e in events]
    assert "reasoning" in kinds
    assert "content" in kinds
    assert kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["kind"] == "content") == "A short summary."


def test_summarize_unknown_note_is_404(client):
    assert client.post("/api/ai/summarize", json={"id": "nope"}).status_code == 404


def test_summarize_without_id_is_400(client):
    assert client.post("/api/ai/summarize", json={}).status_code == 400


# --- tags -----------------------------------------------------------------

def test_tags_returns_parsed_suggestions(client):
    _seed(client)
    client.set_script([
        _chunk(content="gardening, roses"),
        _chunk(finish="stop"),
    ])
    r = client.post("/api/ai/tags", json={"id": "n1"})
    events = _parse_ndjson(r.text)
    result = next(e for e in events if e["kind"] == "result")
    assert result["tags"] == ["gardening", "roses"]


def test_tags_excludes_tags_the_note_already_has(client):
    _seed(client)
    client.set_script([_chunk(content="garden, roses"), _chunk(finish="stop")])
    events = _parse_ndjson(client.post("/api/ai/tags", json={"id": "n1"}).text)
    result = next(e for e in events if e["kind"] == "result")
    assert "garden" not in result["tags"]
    assert "roses" in result["tags"]


# --- links ----------------------------------------------------------------

def test_links_only_suggests_existing_titles(client):
    _seed(client)
    client.set_script([
        _chunk(content="Buying plants\nNot A Real Note"),
        _chunk(finish="stop"),
    ])
    events = _parse_ndjson(client.post("/api/ai/links", json={"id": "n1"}).text)
    result = next(e for e in events if e["kind"] == "result")
    assert result["links"] == ["Buying plants"]


def test_links_never_suggest_the_note_itself(client):
    _seed(client)
    client.set_script([_chunk(content="Garden tour\nBuying plants"), _chunk(finish="stop")])
    events = _parse_ndjson(client.post("/api/ai/links", json={"id": "n1"}).text)
    result = next(e for e in events if e["kind"] == "result")
    assert "Garden tour" not in result["links"]


# --- ask ------------------------------------------------------------------

def test_ask_includes_retrieved_notes_in_result(client):
    _seed(client)
    client.set_script([_chunk(content="You planted basil."), _chunk(finish="stop")])
    events = _parse_ndjson(client.post("/api/ai/ask", json={"question": "basil"}).text)
    result = next(e for e in events if e["kind"] == "result")
    assert {n["id"] for n in result["notes"]} == {"n1", "n2"}
    assert next(e for e in events if e["kind"] == "content")["text"] == "You planted basil."


def test_ask_requires_a_question(client):
    assert client.post("/api/ai/ask", json={"question": "  "}).status_code == 400


# --- failure handling -----------------------------------------------------

def test_truncation_surfaces_as_an_error_not_silence(client):
    """The case that bit us live: budget spent thinking, empty content."""
    _seed(client)
    client.set_script([_chunk(reasoning="thinking", finish="length")])
    events = _parse_ndjson(client.post("/api/ai/summarize", json={"id": "n1"}).text)
    kinds = [e["kind"] for e in events]
    assert "error" in kinds
    assert "content" not in kinds
    assert "done" not in kinds


def test_llm_http_error_is_reported_to_the_client(client):
    _seed(client)
    client.set_script([], status=500)
    events = _parse_ndjson(client.post("/api/ai/summarize", json={"id": "n1"}).text)
    err = next(e for e in events if e["kind"] == "error")
    assert "500" in err["message"]


def test_unreachable_llm_is_reported_not_raised(tmp_path):
    settings = Settings(vault=tmp_path, llm_url="http://127.0.0.1:1/v1",
                        llm_model="x", llm_max_tokens=64, llm_timeout=5.0)
    c = TestClient(create_app(settings=settings))
    c.post("/api/notes", json={"id": "a", "collection": "inbox", "title": "A",
                               "body": "b", "signifier": "note", "status": "open"})
    r = c.post("/api/ai/summarize", json={"id": "a"})
    assert r.status_code == 200
    events = _parse_ndjson(r.text)
    assert any(e["kind"] == "error" for e in events)


# --- config ---------------------------------------------------------------

def test_config_endpoint_reports_the_vault(client):
    _seed(client)
    body = client.get("/api/config").json()
    assert body["note_count"] >= 2
    assert body["llm_model"] == "fake"
    # Exposed so you can tell from outside whether a running server actually
    # picked up a settings change -- which is exactly the confusion that
    # prompted adding it.
    assert body["llm_max_tokens"] == 2048
