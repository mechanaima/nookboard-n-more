"""TDD: API exposes GET /api/notes and POST /api/notes."""
from fastapi.testclient import TestClient
from pathlib import Path
from app.main import create_app


def test_create_and_list_note(tmp_path: Path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)

    payload = {
        "id": "hello-1",
        "collection": "inbox",
        "title": "Hello",
        "body": "world",
        "signifier": "task",
        "status": "open",
        "dates": ["2026-09-23"],
    }
    r = c.post("/api/notes", json=payload)
    assert r.status_code == 201, r.text

    r = c.get("/api/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) == 1
    assert notes[0]["title"] == "Hello"


def test_filter_notes_by_collection(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    for nid, col in [("a", "home"), ("b", "home"), ("c", "work")]:
        c.post("/api/notes", json={"id": nid, "collection": col, "title": nid, "signifier": "note", "status": "open"})
    r = c.get("/api/notes", params={"collection": "home"})
    assert {n["id"] for n in r.json()} == {"a", "b"}


def test_migrate_task(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    c.post("/api/notes", json={"id": "m1", "collection": "inbox", "title": "Old task", "signifier": "task", "status": "open"})
    r = c.patch("/api/notes/m1", json={"status": "migrated", "dates": ["2026-10-01"]})
    assert r.status_code == 200
    assert r.json()["status"] == "migrated"
    assert r.json()["dates"] == ["2026-10-01"]


def test_first_boot_rebuild(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "pre-existing.md").write_text(
        "---\nid: pre\ncollection: inbox\ntitle: Pre\nsignifier: note\nstatus: open\n"
        "dates: []\ncreated: '2026-09-23'\nmood: null\n---\n\nbody\n"
    )
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    r = c.get("/api/notes")
    assert {n["id"] for n in r.json()} == {"pre"}


def test_search_endpoint(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    c.post("/api/notes", json={
        "id": "x", "collection": "inbox", "title": "Buy milk",
        "body": "oat", "signifier": "task", "status": "open", "dates": ["2026-09-23"],
    })
    r = c.get("/api/search", params={"q": "milk"})
    assert {n["id"] for n in r.json()} == {"x"}
    r = c.get("/api/search", params={"q": ""})
    assert r.json() == []


def test_mood_round_trip(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    c.post("/api/notes", json={
        "id": "mo", "collection": "inbox", "title": "rainy day",
        "body": "", "signifier": "note", "status": "open",
        "dates": ["2026-09-23"], "mood": "low",
    })
    r = c.get("/api/notes/mo")
    assert r.json()["mood"] == "low"
    r = c.patch("/api/notes/mo", json={"mood": "good"})
    assert r.json()["mood"] == "good"


def test_calendar_endpoint(tmp_path):
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    c.post("/api/notes", json={
        "id": "c1", "collection": "inbox", "title": "x", "body": "",
        "signifier": "task", "status": "open", "dates": ["2026-09-01", "2026-09-23"],
    })
    c.post("/api/notes", json={
        "id": "c2", "collection": "inbox", "title": "y", "body": "",
        "signifier": "task", "status": "open", "dates": ["2026-09-23"],
    })
    counts = c.get("/api/calendar/2026/9").json()
    assert counts["2026-09-01"] == 1
    assert counts["2026-09-23"] == 2
    # Wrong month
    counts = c.get("/api/calendar/2026/10").json()
    assert counts == {}