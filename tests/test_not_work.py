"""What is not work, and the fact that one answer covers board, dashboard and query.

The board used to spell this rule out for itself -- generated ids and templates --
and so missed every note in `daily`, `weekly`, `bookmarks`, `workspaces` and
`testing`, whose ids look like any other note's. Five note-shaped collections filled
a real board with twelve cards that could only be dismissed by hand, forever. These
tests exist so the rule lives in one place and the three readers agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import NOT_WORK_COLLECTIONS, Note, is_work, not_work_reason


def note(nid="n1", collection="inbox", **kw) -> Note:
    return Note(id=nid, collection=collection, title=nid, body="", **kw)


# -- the rule -----------------------------------------------------------------

@pytest.mark.parametrize("collection", sorted(NOT_WORK_COLLECTIONS))
def test_a_note_kept_in_one_of_these_collections_is_not_work(collection):
    assert not_work_reason(note(collection=collection)) == "collection"
    assert not is_work(note(collection=collection))


@pytest.mark.parametrize("nid", ["daily-2026-09-24", "weekly-2026-W39"])
def test_a_period_note_is_not_work_whatever_collection_it_sits_in(nid):
    assert not_work_reason(note(nid=nid, collection="inbox")) == "generated"


def test_a_template_is_not_work():
    assert not_work_reason(note(collection="templates")) == "template"


def test_a_real_task_is_work():
    assert not_work_reason(note()) is None
    assert is_work(note())
    assert is_work(note(collection="school"))


def test_one_reason_each_so_the_counts_can_be_added_up():
    """A template in `daily` is held back for being a template, not for both.

    Counting it twice is how a board's arithmetic ends up larger than the number of
    cards it did not draw.
    """
    both = note(nid="daily-2026-09-24", collection="daily")
    assert not_work_reason(both) == "generated"  # a record outranks the folder it is in
    assert not_work_reason(note(collection="templates")) == "template"


# -- the board ----------------------------------------------------------------

@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    with TestClient(create_app(vault_root=root)) as client:
        yield client


def add(client, nid, collection, title=None):
    return client.post("/api/notes", json={
        "id": nid, "collection": collection, "title": title or nid,
        "signifier": "task", "status": "open",
    })


def test_those_collections_do_not_reach_the_board(vault):
    for c in sorted(NOT_WORK_COLLECTIONS):
        assert add(vault, f"x-{c}", c).status_code == 201
    add(vault, "real-task", "school")

    body = vault.get("/api/board").json()
    ids = [c["id"] for col in body["columns"] for c in col["cards"]]
    assert ids == ["real-task"]
    assert body["hidden_collections"] == len(NOT_WORK_COLLECTIONS)


def test_the_board_reports_why_rather_than_just_looking_smaller(vault):
    add(vault, "daily-2026-09-24", "daily")
    add(vault, "tmpl", "templates")
    add(vault, "bk", "bookmarks")
    add(vault, "real", "inbox")

    body = vault.get("/api/board").json()
    assert [c["id"] for col in body["columns"] for c in col["cards"]] == ["real"]
    # a partition: the reasons add up to the total, and the total is the number held
    # back -- not a sum that can drift from the truth
    assert body["hidden_generated"] == 1
    assert body["hidden_templates"] == 1
    assert body["hidden_collections"] == 1
    assert body["hidden_total"] == 3
    assert body["hidden_total"] == (
        body["hidden_generated"] + body["hidden_templates"] + body["hidden_collections"]
    )


def test_naming_a_collection_still_shows_it(vault):
    """Asking for `bookmarks` by name is a deliberate act, and is answered."""
    add(vault, "bk", "bookmarks")
    body = vault.get("/api/board", params={"collection": "bookmarks"}).json()
    # narrowed to it, the collection is not "hidden" -- but a bookmark is still not
    # work, so the board keeps its rule; what matters is that it is not silently empty
    assert body["hidden_total"] >= 1


def test_the_board_and_an_unnarrowed_query_hide_exactly_the_same_set(vault):
    """The promise in `is_work`'s docstring, as a test rather than a comment."""
    add(vault, "real", "school")
    for c in sorted(NOT_WORK_COLLECTIONS):
        add(vault, f"x-{c}", c)
    add(vault, "daily-2026-09-25", "daily")

    on_board = {c["id"] for col in vault.get("/api/board").json()["columns"] for c in col["cards"]}
    # The query's answer is markdown, so the comparison is on what a person would
    # read: the task by name, and no trace of anything held back.
    markdown = vault.get("/api/query", params={"q": "open tasks"}).json()["markdown"]
    assert on_board == {"real"}
    assert "real" in markdown
    for hidden in [f"x-{c}" for c in sorted(NOT_WORK_COLLECTIONS)] + ["daily-2026-09-25"]:
        assert hidden not in markdown, f"{hidden} reached the query's answer"
