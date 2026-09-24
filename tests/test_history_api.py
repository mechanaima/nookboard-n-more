"""The history endpoints, over a real vault with a real repository in it.

The two things worth guarding here are not the happy paths. They are: that a note
save never depends on git working (history exists to keep notes, not to control
them), and that history is off until it is asked for — this app does not write a
`.git` into someone's vault because it seemed like a good idea.
"""

from __future__ import annotations

import subprocess

import pytest
from starlette.testclient import TestClient

from app.main import create_app


@pytest.fixture
def app_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "inbox").mkdir(parents=True)
    app = create_app(vault_root=vault)
    with TestClient(app) as client:
        yield client, vault


@pytest.fixture
def client(app_vault):
    return app_vault[0]


def make_note(client, note_id="one", title="Soil mix", **extra):
    body = {
        "id": note_id, "collection": "inbox", "title": title,
        "body": "one part peat\n", **extra,
    }
    response = client.post("/api/notes", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True).stdout


# --------------------------------------------------------------- off, until asked

def test_history_is_off_until_it_is_asked_for(app_vault):
    client, vault = app_vault
    state = client.get("/api/history").json()
    assert state["on"] is False
    assert state["changes"] == [] and state["deleted"] == []
    assert state["summary"] == "History is off."
    assert "git" in state["why"]
    # and nothing was done to the folder behind the person's back
    assert not (vault / ".git").exists()


def test_note_saving_does_not_depend_on_history(app_vault):
    """A history that could refuse a save would be a history that loses notes,
    which is the opposite of what it is for."""
    client, vault = app_vault
    created = make_note(client)
    assert created["history"] == {"on": False}
    assert (vault / "inbox" / "one.md").exists()
    assert client.get("/api/notes/one").status_code == 200


def test_turning_it_on_puts_the_vault_under_git(app_vault):
    client, vault = app_vault
    make_note(client)  # something to have a past
    state = client.post("/api/history/init").json()
    assert state["on"] is True
    assert state["started"]["created"] == ["repository", ".gitignore"]
    assert (vault / ".git").exists()
    # the baseline commit holds the vault as it was when history began
    assert "history begins" in git(vault, "log", "--format=%s")
    assert client.get("/api/history").json()["on"] is True


# --------------------------------------------------------- what a save records

def test_a_new_note_is_recorded_as_new(client):
    client.post("/api/history/init")
    created = make_note(client)
    assert created["history"]["on"] is True and created["history"]["committed"]
    state = client.get("/api/history").json()
    assert state["changes"][0]["subject"] == "new: Soil mix"
    # the baseline commit is where the vault started, not a change to it
    assert state["summary"].startswith("1 change recorded")


def test_an_edit_is_recorded_as_an_edit(client):
    client.post("/api/history/init")
    make_note(client)
    client.patch("/api/notes/one", json={"body": "two parts peat\n"})
    subjects = [c["subject"] for c in client.get("/api/history").json()["changes"]]
    assert subjects[0] == "edit: Soil mix"
    assert subjects[1] == "new: Soil mix"       # newest first


def test_a_collection_change_records_the_move(app_vault):
    """A collection is a folder, so this moves the file. A move recorded halfway
    would show the note existing in two places at once."""
    client, vault = app_vault
    client.post("/api/history/init")
    make_note(client)
    client.patch("/api/notes/one", json={"collection": "workshop"})
    assert (vault / "workshop" / "one.md").exists()
    assert client.get("/api/history").json()["changes"][0]["subject"] == "edit: Soil mix"
    # the old path is not left alive in the repository
    assert "inbox/one.md" not in git(vault, "ls-files").split()


def test_the_note_that_changed_is_what_is_recorded(app_vault):
    """Untouched notes stay out of someone else's commit, which is the difference
    between a log you can read and a snapshot of the whole vault every time."""
    client, vault = app_vault
    client.post("/api/history/init")
    make_note(client, note_id="one", title="Soil mix")
    make_note(client, note_id="two", title="Seed trays")
    client.patch("/api/notes/two", json={"body": "sown\n"})
    changed = git(vault, "show", "--name-only", "--format=", "HEAD")
    assert "inbox/two.md" in changed
    assert "inbox/one.md" not in changed


# ------------------------------------------------------------------- undo

def test_a_deleted_note_can_be_found_and_brought_back(client):
    client.post("/api/history/init")
    make_note(client)
    assert client.delete("/api/notes/one").status_code == 204
    assert client.get("/api/notes/one").status_code == 404

    state = client.get("/api/history").json()
    assert [c["subject"] for c in state["changes"]][0] == "delete: Soil mix"
    gone = state["deleted"]
    assert len(gone) == 1 and gone[0]["path"] == "inbox/one.md"
    assert "to bring back" in state["summary"]

    result = client.post(
        "/api/history/restore",
        json={"path": gone[0]["path"], "rev": gone[0]["restore_from"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is True
    # back on disk, back in the index, and no longer offered as lost
    assert client.get("/api/notes/one").status_code == 200
    assert client.get("/api/history").json()["deleted"] == []


def test_a_notes_versions_are_its_own(client):
    client.post("/api/history/init")
    make_note(client, note_id="one", title="Soil mix")
    make_note(client, note_id="two", title="Seed trays")
    client.patch("/api/notes/one", json={"body": "more peat\n"})
    history = client.get("/api/history/one").json()
    assert history["on"] is True and history["relpath"] == "inbox/one.md"
    assert [v["subject"] for v in history["versions"]] == ["edit: Soil mix", "new: Soil mix"]
    assert all("Seed trays" not in v["subject"] for v in history["versions"])
    # the newest version is the text on disk, and that was measured
    assert history["versions"][0]["is_now"] is True
    # Only the newest is asked about, because only the newest can be "what you
    # have": the question costs a git call, so it is asked once.
    assert "is_now" not in history["versions"][1]


def test_a_note_edited_outside_the_app_is_not_called_current(app_vault):
    """Measured, not assumed. If the file on disk is not the newest commit, the
    panel must not say you are looking at the newest commit."""
    client, vault = app_vault
    client.post("/api/history/init")
    make_note(client)
    (vault / "inbox" / "one.md").write_text("---\nid: one\ntitle: Soil mix\n---\n\nBY HAND\n")
    assert client.get("/api/history/one").json()["versions"][0]["is_now"] is False


def test_restoring_an_older_version_puts_the_text_back(client):
    client.post("/api/history/init")
    make_note(client)
    first = client.get("/api/history/one").json()["versions"][0]["sha"]
    client.patch("/api/notes/one", json={"body": "WRECKED\n"})
    assert client.get("/api/notes/one").json()["body"] == "WRECKED"

    result = client.post("/api/history/restore", json={"path": "inbox/one.md", "rev": first}).json()
    assert result["ok"] is True
    assert client.get("/api/notes/one").json()["body"] == "one part peat"
    # and the restore is itself a version, so undo is undoable
    versions = client.get("/api/history/one").json()["versions"]
    assert versions[0]["subject"] == "restore: one"
    assert versions[0]["is_now"] is True
    assert versions[1]["subject"] == "edit: Soil mix"


def test_a_restore_needs_a_real_version(client):
    client.post("/api/history/init")
    make_note(client)
    assert client.post("/api/history/restore", json={"path": "inbox/one.md"}).status_code == 400
    assert client.post("/api/history/restore", json={"rev": "abc1234"}).status_code == 400
    # revision *syntax* is not this app's business: it has shas
    bad = client.post("/api/history/restore", json={"path": "inbox/one.md", "rev": "HEAD~1"})
    assert bad.status_code == 400
    assert client.get("/api/notes/one").json()["body"] == "one part peat"


def test_the_history_of_something_that_is_not_a_note_is_a_404(client):
    client.post("/api/history/init")
    assert client.get("/api/history/not-a-note").status_code == 404


# ------------------------------------------------------ work it did not record

def test_unrecorded_work_is_said_out_loud_and_can_be_recorded(app_vault):
    """Not every write is recorded one at a time -- reordering a column is
    frontmatter-only. Those changes are not thrown away and not hidden: they are
    reported, and there is a button that records them."""
    client, vault = app_vault
    client.post("/api/history/init")
    make_note(client)
    assert client.get("/api/history").json()["pending"] == []

    (vault / "inbox" / "by-hand.md").write_text("---\nid: hand\ntitle: By hand\n---\n\nx\n")
    state = client.get("/api/history").json()
    assert state["pending"] == ["inbox/by-hand.md"]
    assert "1 file not recorded yet" in state["summary"]

    recorded = client.post("/api/history/checkpoint").json()
    assert recorded["recorded"]["committed"]
    assert recorded["pending"] == []
    assert recorded["changes"][0]["subject"].startswith("checkpoint:")
    assert recorded["summary"] == "2 changes recorded, the last just now"  # new + checkpoint


def test_a_checkpoint_with_nothing_to_record_changes_nothing(client):
    client.post("/api/history/init")
    make_note(client)
    before = client.get("/api/history").json()["changes"]
    result = client.post("/api/history/checkpoint").json()
    assert result["recorded"]["committed"] is None
    assert result["recorded"]["why"] == "nothing changed"
    assert len(result["changes"]) == len(before)


def test_a_checkpoint_needs_history_to_be_on(client):
    assert client.post("/api/history/checkpoint").status_code == 400
