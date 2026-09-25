"""TDD for the board + dependency API.

Exercises the feature the way the UI does: create tasks, wire blockers, move
cards, and read back what the board would draw.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Database, MIGRATIONS
from app.main import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(vault_root=tmp_path))


def task(c: TestClient, note_id: str, *, title=None, blocked_by=None, stage=None,
         status="open", collection="inbox", dates=None):
    payload = {
        "id": note_id,
        "collection": collection,
        "title": title or note_id,
        "body": "",
        "signifier": "task",
        "status": status,
    }
    if blocked_by is not None:
        payload["blocked_by"] = blocked_by
    if stage is not None:
        payload["stage"] = stage
    if dates is not None:
        payload["dates"] = dates
    r = c.post("/api/notes", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def column(board: dict, stage: str) -> dict:
    return next(col for col in board["columns"] if col["id"] == stage)


def card_for(board: dict, stage: str, note_id: str) -> dict:
    """Look a card up by id — column order is its own concern, tested below."""
    return next(card for card in column(board, stage)["cards"] if card["id"] == note_id)


# -- board shape -------------------------------------------------------------


def test_board_has_the_five_columns_in_order(tmp_path):
    c = client(tmp_path)
    r = c.get("/api/board")
    assert r.status_code == 200
    assert [col["id"] for col in r.json()["columns"]] == [
        "backlog", "todo", "doing", "review", "done",
    ]
    assert [col["label"] for col in r.json()["columns"]][1] == "To do"


def test_unplaced_tasks_land_in_todo(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    board = c.get("/api/board").json()
    assert [card["id"] for card in column(board, "todo")["cards"]] == ["a"]
    assert column(board, "backlog")["count"] == 0


def test_completed_task_lands_in_done(tmp_path):
    c = client(tmp_path)
    task(c, "a", status="complete")
    assert [x["id"] for x in column(c.get("/api/board").json(), "done")["cards"]] == ["a"]


def test_completed_due_task_is_not_in_the_due_soon_count(tmp_path):
    c = client(tmp_path)
    task(c, "finished", status="complete", dates=[date.today().isoformat()])

    summary = c.get("/api/board").json()["summary"]

    assert summary["due_soon"] == 0


def test_due_soon_includes_two_days_out_but_not_three(tmp_path):
    c = client(tmp_path)
    today = date.today()
    task(c, "within", dates=[(today + timedelta(days=2)).isoformat()])
    task(c, "outside", dates=[(today + timedelta(days=3)).isoformat()])

    summary = c.get("/api/board").json()["summary"]

    assert summary["due_soon"] == 1


def test_due_soon_checks_every_date_on_an_open_task(tmp_path):
    c = client(tmp_path)
    today = date.today()
    task(
        c,
        "multi-day",
        dates=[(today + timedelta(days=7)).isoformat(), today.isoformat()],
    )

    summary = c.get("/api/board").json()["summary"]

    assert summary["due_soon"] == 1


def test_backlog_is_reachable_by_placing_a_card(tmp_path):
    c = client(tmp_path)
    task(c, "a", stage="backlog")
    board = c.get("/api/board").json()
    assert [x["id"] for x in column(board, "backlog")["cards"]] == ["a"]


# -- blockers ----------------------------------------------------------------


def test_open_blocker_marks_the_dependent_blocked(tmp_path):
    c = client(tmp_path)
    task(c, "blocker")
    task(c, "dependent", blocked_by=["blocker"])

    card = card_for(c.get("/api/board").json(), "todo", "dependent")
    assert card["blocked"] is True
    assert card["open_blockers"][0]["title"] == "blocker"


def test_summary_counts_ready_and_blocked(tmp_path):
    c = client(tmp_path)
    task(c, "blocker")
    task(c, "dependent", blocked_by=["blocker"])
    task(c, "free")
    summary = c.get("/api/board").json()["summary"]
    assert summary == {
        "total": 3, "open": 3, "done": 0, "blocked": 1, "ready": 2, "due_soon": 0,
    }


def test_completing_a_blocker_unblocks_the_dependent(tmp_path):
    """The whole point of a blocker: clearing it releases what waited on it."""
    c = client(tmp_path)
    task(c, "blocker")
    task(c, "dependent", blocked_by=["blocker"])
    c.patch("/api/notes/blocker", json={"status": "complete"})

    card = card_for(c.get("/api/board").json(), "todo", "dependent")
    assert card["blocked"] is False


def test_a_blocker_itself_reports_what_it_blocks(tmp_path):
    c = client(tmp_path)
    task(c, "blocker")
    task(c, "dependent", blocked_by=["blocker"])
    card = card_for(c.get("/api/board").json(), "todo", "blocker")
    assert card["blocking"] == 1


def test_deleting_a_blocker_leaves_the_dependent_blocked_and_flagged(tmp_path):
    c = client(tmp_path)
    task(c, "blocker")
    task(c, "dependent", blocked_by=["blocker"])
    c.delete("/api/notes/blocker")

    card = card_for(c.get("/api/board").json(), "todo", "dependent")
    assert card["blocked"] is True
    assert card["open_blockers"][0]["missing"] is True


# -- column order ------------------------------------------------------------


def test_new_tasks_keep_the_order_they_were_captured_in(tmp_path):
    """`created` is only a date, so without an explicit slot three tasks
    captured in one sitting would come back in alphabetical id order."""
    c = client(tmp_path)
    for nid in ("zebra", "apple", "mango"):
        task(c, nid)
    ids = [x["id"] for x in column(c.get("/api/board").json(), "todo")["cards"]]
    assert ids == ["zebra", "apple", "mango"]


def test_a_new_task_lands_at_the_bottom_of_its_column(tmp_path):
    c = client(tmp_path)
    task(c, "first")
    task(c, "second")
    c.post("/api/board/move", json={"id": "first", "stage": "doing"})
    task(c, "third")
    ids = [x["id"] for x in column(c.get("/api/board").json(), "todo")["cards"]]
    assert ids == ["second", "third"]


def test_notes_that_are_not_tasks_get_no_invented_slot(tmp_path):
    """A journal entry is not a card; it must not gain meaningless ordering
    data in its frontmatter."""
    c = client(tmp_path)
    c.post("/api/notes", json={
        "id": "entry", "collection": "inbox", "title": "Entry",
        "signifier": "note", "status": "open",
    })
    assert c.get("/api/notes/entry").json()["position"] is None


# -- dependency endpoints ----------------------------------------------------


def test_add_and_remove_a_dependency(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b")

    r = c.post("/api/notes/a/deps", json={"blocker_id": "b"})
    assert r.status_code == 200, r.text
    assert [d["id"] for d in r.json()["blocked_by"]] == ["b"]
    assert r.json()["blocked"] is True

    r = c.delete("/api/notes/a/deps/b")
    assert r.status_code == 200
    assert r.json()["blocked_by"] == []


def test_adding_the_same_blocker_twice_is_idempotent(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b")
    c.post("/api/notes/a/deps", json={"blocker_id": "b"})
    r = c.post("/api/notes/a/deps", json={"blocker_id": "b"})
    assert [d["id"] for d in r.json()["blocked_by"]] == ["b"]


def test_a_task_cannot_block_itself(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    r = c.post("/api/notes/a/deps", json={"blocker_id": "a"})
    assert r.status_code == 409
    assert "itself" in r.json()["detail"]


def test_a_dependency_cycle_is_refused(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    r = c.post("/api/notes/a/deps", json={"blocker_id": "b"})
    assert r.status_code == 409
    assert "cycle" in r.json()["detail"]
    # ...and nothing was written.
    assert c.get("/api/notes/a/deps").json()["blocked_by"] == []


def test_patch_rejects_a_cycle_too(tmp_path):
    """Any write path must be guarded, not just the deps endpoint."""
    c = client(tmp_path)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    r = c.patch("/api/notes/a", json={"blocked_by": ["b"]})
    assert r.status_code == 409
    assert c.get("/api/notes/a/deps").json()["blocked_by"] == []


def test_deps_endpoint_reports_both_directions(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    body = c.get("/api/notes/b/deps").json()
    assert [d["id"] for d in body["blocked_by"]] == ["a"]
    assert body["blocking"] == []
    body = c.get("/api/notes/a/deps").json()
    assert [n["id"] for n in body["blocking"]] == ["b"]


def test_deps_for_a_missing_note_is_404(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/notes/nope/deps").status_code == 404
    assert c.post("/api/notes/nope/deps", json={"blocker_id": "x"}).status_code == 404


# -- moving cards ------------------------------------------------------------


def test_move_reports_the_new_column(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    r = c.post("/api/board/move", json={"id": "a", "stage": "doing"})
    assert r.status_code == 200, r.text
    assert r.json()["stage"] == "doing"
    assert [x["id"] for x in column(c.get("/api/board").json(), "doing")["cards"]] == ["a"]


def test_moving_into_done_completes_the_task(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    r = c.post("/api/board/move", json={"id": "a", "stage": "done"})
    assert r.json()["status"] == "complete"
    assert c.get("/api/notes/a").json()["status"] == "complete"


def test_moving_back_out_of_done_reopens_the_task(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    c.post("/api/board/move", json={"id": "a", "stage": "done"})
    r = c.post("/api/board/move", json={"id": "a", "stage": "doing"})
    assert r.json()["status"] == "open"


def test_move_respects_before_id_ordering(tmp_path):
    c = client(tmp_path)
    for nid in ("a", "b", "c"):
        task(c, nid)
    c.post("/api/board/move", json={"id": "c", "stage": "todo", "before_id": "a"})
    ids = [x["id"] for x in column(c.get("/api/board").json(), "todo")["cards"]]
    assert ids == ["c", "a", "b"]


def test_move_rewrites_positions_as_clean_integers(tmp_path):
    c = client(tmp_path)
    for nid in ("a", "b", "c"):
        task(c, nid)
    c.post("/api/board/move", json={"id": "c", "stage": "todo", "before_id": "a"})
    positions = {x["id"]: x["position"] for x in column(c.get("/api/board").json(), "todo")["cards"]}
    assert positions == {"c": 1.0, "a": 2.0, "b": 3.0}


def test_move_rejects_an_unknown_column(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    r = c.post("/api/board/move", json={"id": "a", "stage": "someday"})
    assert r.status_code == 400


def test_move_of_a_missing_card_is_404(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/board/move", json={"id": "ghost", "stage": "doing"}).status_code == 404


# -- status <-> stage both directions ---------------------------------------


def test_patch_status_complete_moves_the_card_to_done(tmp_path):
    c = client(tmp_path)
    task(c, "a", stage="doing")
    body = c.patch("/api/notes/a", json={"status": "complete"}).json()
    assert body["stage"] == "done"


def test_patch_stage_done_completes_the_task(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    body = c.patch("/api/notes/a", json={"stage": "done"}).json()
    assert body["status"] == "complete"


def test_patch_rejects_an_unknown_status_with_400_not_500(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    assert c.patch("/api/notes/a", json={"status": "nonsense"}).status_code == 400


# -- persistence -------------------------------------------------------------


def test_dependencies_round_trip_through_markdown(tmp_path):
    """Markdown is the source of truth, so the graph has to live in the file."""
    c = client(tmp_path)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    task(c, "c", blocked_by=["a"], stage="backlog")

    raw = (tmp_path / "inbox" / "b.md").read_text()
    assert "blocked_by:" in raw
    assert "a" in raw

    # A fresh app over the same files (no index) must see the same graph.
    fresh = TestClient(create_app(vault_root=tmp_path))
    card = c.get("/api/notes/b").json()
    assert card["blocked_by"] == ["a"]
    assert fresh.get("/api/notes/b").json()["blocked_by"] == ["a"]


def test_positions_round_trip_through_markdown(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b")
    c.post("/api/board/move", json={"id": "b", "stage": "todo", "before_id": "a"})
    assert (tmp_path / "inbox" / "b.md").read_text().count("position: 1.0") == 1


def test_rebuild_index_keeps_dependencies(tmp_path):
    c = client(tmp_path)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    assert c.post("/api/rebuild-index").status_code == 200
    # db.get() reads the index, not the file — deps must survive the rebuild.
    assert c.get("/api/search", params={"q": "b"}).json()[0]["blocked_by"] == ["a"]


def test_index_migrates_a_database_created_before_task_management(tmp_path):
    """An existing .index.sqlite must gain the new columns, not blow up."""
    path = tmp_path / ".index.sqlite"
    old = sqlite3.connect(str(path))
    old.executescript(
        """
        CREATE TABLE notes (
            id TEXT PRIMARY KEY, collection TEXT NOT NULL, title TEXT NOT NULL,
            body TEXT NOT NULL, signifier TEXT NOT NULL, status TEXT NOT NULL,
            dates_csv TEXT NOT NULL DEFAULT '', parent_id TEXT,
            created TEXT NOT NULL, mood TEXT, tags_csv TEXT NOT NULL DEFAULT '',
            recurrence TEXT, search_text TEXT NOT NULL
        );
        """
    )
    old.commit()
    old.close()

    db = Database(path)
    columns = {row["name"] for row in db.conn.execute("PRAGMA table_info(notes)")}
    for name, _decl in MIGRATIONS:
        assert name in columns

    # And it is usable, not merely migrated.
    app = create_app(vault_root=tmp_path)
    c = TestClient(app)
    task(c, "a")
    task(c, "b", blocked_by=["a"])
    assert c.get("/api/search", params={"q": "b"}).json()[0]["blocked_by"] == ["a"]


def test_foreign_vault_string_dependency_is_parsed(tmp_path):
    """Another vault may write blocked_by as a bare id or a comma list."""
    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.md").write_text("---\ntitle: A\n---\n")
    (d / "b.md").write_text("---\ntitle: B\nblocked_by: a\n---\n")
    (d / "c.md").write_text("---\ntitle: C\nblocked_by: a, b\n---\n")

    c = TestClient(create_app(vault_root=tmp_path))
    assert c.get("/api/notes/b").json()["blocked_by"] == ["a"]
    assert c.get("/api/notes/c").json()["blocked_by"] == ["a", "b"]


# -- task list ---------------------------------------------------------------


def test_task_list_excludes_done_by_default(tmp_path):
    c = client(tmp_path)
    task(c, "open-one")
    task(c, "done-one", status="complete")
    ids = [n["id"] for n in c.get("/api/tasks").json()]
    assert ids == ["open-one"]
    assert {n["id"] for n in c.get("/api/tasks", params={"include_done": True}).json()} == {
        "open-one", "done-one",
    }


def test_board_can_be_filtered_by_collection(tmp_path):
    c = client(tmp_path)
    task(c, "home-one", collection="home")
    task(c, "work-one", collection="work")
    board = c.get("/api/board", params={"collection": "home"}).json()
    assert [x["id"] for x in column(board, "todo")["cards"]] == ["home-one"]
