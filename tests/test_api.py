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

# -- times ------------------------------------------------------------------

def test_a_note_can_be_created_with_a_time(tmp_path):
    c = TestClient(create_app(vault_root=tmp_path))
    r = c.post("/api/notes", json={
        "id": "standup", "title": "Standup", "dates": ["2026-09-24"],
        "at": "9:05", "until": "09:45",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["at"] == "09:05", "normalized, not as typed"
    assert body["time_label"] == "09:05\u201309:45"
    # and it is on disk, so the app and the file agree
    assert "at: 09:05" in (tmp_path / "inbox" / "standup.md").read_text()


def test_a_note_without_a_time_reports_none(tmp_path):
    c = TestClient(create_app(vault_root=tmp_path))
    body = c.post("/api/notes", json={
        "id": "plain", "title": "Plain", "dates": ["2026-09-24"],
    }).json()
    assert body["at"] is None and body["time_label"] == ""


def test_a_time_the_api_cannot_read_is_refused_rather_than_dropped(tmp_path):
    """Reads are forgiving on purpose -- a foreign vault's `at: noon` must not
    stop a note from opening. A write is not: a note that looks scheduled and is
    not is worse than an error message."""
    c = TestClient(create_app(vault_root=tmp_path))
    r = c.post("/api/notes", json={"id": "x", "title": "X", "at": "noon"})
    assert r.status_code == 400
    assert "14:30" in r.json()["detail"], "it has to say how to write one"
    assert not (tmp_path / "inbox" / "x.md").exists(), "a refused create writes nothing"


def test_updating_sets_keeps_and_clears_a_time(tmp_path):
    c = TestClient(create_app(vault_root=tmp_path))
    c.post("/api/notes", json={"id": "x", "title": "X", "dates": ["2026-09-24"]})

    assert c.patch("/api/notes/x", json={"at": "14:30"}).json()["at"] == "14:30"

    # An absent key keeps the value: the editor sends whole notes, but the API
    # is used by scripts and imports that send only what they changed.
    kept = c.patch("/api/notes/x", json={"title": "Renamed"}).json()
    assert kept["at"] == "14:30" and kept["title"] == "Renamed"

    cleared = c.patch("/api/notes/x", json={"at": None}).json()
    assert cleared["at"] is None and cleared["time_label"] == ""


def test_updating_with_an_unreadable_time_is_refused_and_changes_nothing(tmp_path):
    c = TestClient(create_app(vault_root=tmp_path))
    c.post("/api/notes", json={
        "id": "x", "title": "X", "dates": ["2026-09-24"], "at": "14:30",
    })
    r = c.patch("/api/notes/x", json={"at": "quarter past two"})
    assert r.status_code == 400
    assert c.get("/api/notes/x").json()["at"] == "14:30", "the old time survives"


def test_a_template_carries_its_time_into_the_note_it_makes(tmp_path):
    """A standup template is *at* 09:00; that is most of the reason to have one.
    Its state is what must not carry over."""
    from app.models import Note, Status
    from app.vault import Vault

    vault = Vault(tmp_path)
    vault.write(Note(id="tmpl-standup", collection="templates", title="Standup",
                     body="", dates=[], at="09:00", until="09:15",
                     status=Status.COMPLETE))
    c = TestClient(create_app(vault_root=tmp_path))
    r = c.post("/api/templates/apply", json={"template": "tmpl-standup", "collection": "work"})
    assert r.status_code in (200, 201), r.text
    made = r.json()
    assert made["at"] == "09:00" and made["until"] == "09:15"
    assert made["status"] == "open", "a new note starts open, whatever the template was"
