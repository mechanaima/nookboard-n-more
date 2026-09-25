"""TDD: API exposes GET /api/notes and POST /api/notes."""
from fastapi.testclient import TestClient
from pathlib import Path
from app.main import create_app


def test_public_api_operation_contract(tmp_path: Path):
    """The paths, methods, and generated operation names clients may rely on."""
    expected = {
        ("/", "get", "index__get"),
        ("/api/health", "get", "health_api_health_get"),
        ("/api/config", "get", "get_config_api_config_get"),
        ("/api/collections", "get", "list_collections_api_collections_get"),
        ("/api/notes", "get", "list_notes_api_notes_get"),
        ("/api/notes", "post", "create_note_api_notes_post"),
        ("/api/notes/{note_id}", "get", "get_note_api_notes__note_id__get"),
        ("/api/notes/{note_id}", "patch", "update_note_api_notes__note_id__patch"),
        ("/api/notes/{note_id}", "delete", "delete_note_api_notes__note_id__delete"),
        ("/api/notes/{note_id}/backlinks", "get", "get_backlinks_api_notes__note_id__backlinks_get"),
        ("/api/notes/{note_id}/deps", "get", "get_deps_api_notes__note_id__deps_get"),
        ("/api/notes/{note_id}/deps", "post", "add_dep_api_notes__note_id__deps_post"),
        ("/api/notes/{note_id}/deps/{blocker_id}", "delete", "remove_dep_api_notes__note_id__deps__blocker_id__delete"),
        ("/api/search", "get", "search_api_search_get"),
        ("/api/tasks", "get", "list_tasks_api_tasks_get"),
        ("/api/recurring/run", "post", "trigger_recurring_api_recurring_run_post"),
        ("/api/board", "get", "board_api_board_get"),
        ("/api/board/move", "post", "move_card_api_board_move_post"),
        ("/api/board/pin", "post", "pin_card_api_board_pin_post"),
        ("/api/history", "get", "history_state_api_history_get"),
        ("/api/history/init", "post", "history_init_api_history_init_post"),
        ("/api/history/checkpoint", "post", "history_checkpoint_api_history_checkpoint_post"),
        ("/api/history/{note_id}", "get", "note_history_api_history__note_id__get"),
        ("/api/history/restore", "post", "restore_version_api_history_restore_post"),
        ("/api/workspaces", "get", "list_workspaces_api_workspaces_get"),
        ("/api/workspaces/{note_id}", "get", "get_workspace_api_workspaces__note_id__get"),
        ("/api/workspaces/{note_id}/open", "post", "open_workspace_api_workspaces__note_id__open_post"),
        ("/api/bookmarks", "get", "list_bookmarks_api_bookmarks_get"),
        ("/api/bookmarks/check", "post", "check_bookmarks_api_bookmarks_check_post"),
        ("/api/home", "get", "home_view_api_home_get"),
        ("/api/insight", "get", "insights_api_insight_get"),
        ("/api/obsidian", "get", "obsidian_notes_view_api_obsidian_get"),
        ("/api/mood", "get", "mood_series_api_mood_get"),
        ("/api/calendar/{year}/{month}", "get", "calendar_api_calendar__year___month__get"),
        ("/api/calendar.ics", "get", "calendar_ics_api_calendar_ics_get"),
        ("/api/export.zip", "get", "export_zip_api_export_zip_get"),
        ("/api/rebuild-index", "post", "rebuild_index_api_rebuild_index_post"),
        ("/api/ai/summarize", "post", "ai_summarize_api_ai_summarize_post"),
        ("/api/ai/tags", "post", "ai_tags_api_ai_tags_post"),
        ("/api/ai/links", "post", "ai_links_api_ai_links_post"),
        ("/api/ai/ask", "post", "ai_ask_api_ai_ask_post"),
        ("/api/transcribe", "get", "transcribe_status_api_transcribe_get"),
        ("/api/transcribe", "post", "transcribe_file_api_transcribe_post"),
        ("/api/transcribe/upload", "post", "transcribe_upload_api_transcribe_upload_post"),
        ("/api/transcribe/{job_id}", "get", "transcribe_job_api_transcribe__job_id__get"),
        ("/api/transcribe/summarize", "post", "transcribe_resummarize_api_transcribe_summarize_post"),
        ("/api/daily", "get", "daily_pending_api_daily_get"),
        ("/api/daily/{day}", "get", "daily_state_api_daily__day__get"),
        ("/api/daily/summary", "post", "daily_summary_api_daily_summary_post"),
        ("/api/weekly", "get", "weekly_pending_api_weekly_get"),
        ("/api/weekly/{key}", "get", "weekly_state_api_weekly__key__get"),
        ("/api/weekly/summary", "post", "weekly_summary_api_weekly_summary_post"),
        ("/api/templates", "get", "list_templates_api_templates_get"),
        ("/api/templates/apply", "post", "apply_template_api_templates_apply_post"),
        ("/api/query", "get", "run_query_api_query_get"),
        ("/manifest.webmanifest", "get", "web_manifest_manifest_webmanifest_get"),
        ("/service-worker.js", "get", "service_worker_service_worker_js_get"),
    }
    schema = create_app(vault_root=tmp_path).openapi()
    actual = {
        (path, method, operation["operationId"])
        for path, item in schema["paths"].items()
        for method, operation in item.items()
    }

    assert len(expected) == 57
    assert actual == expected, {
        "missing": sorted(expected - actual),
        "unexpected": sorted(actual - expected),
    }


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
