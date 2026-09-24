"""The workspace endpoints.

The interesting one is `POST /api/workspaces/{id}/open`: it is the only endpoint
in this app that starts a process, and its tests are mostly about what it
*refuses*. A test that opened a real editor would pop a window on the user's
desktop, so the happy path is checked by pointing it at `/bin/echo` — the argv
comes back either way, which is the whole contract.
"""

from __future__ import annotations

import subprocess

import pytest
from starlette.testclient import TestClient

from app import workspace as W
from app import workspace_run as R
from app.main import create_app
from app.models import Note, Signifier, Status


@pytest.fixture
def repo(tmp_path):
    """A real repo, a real note pointing at it, and a client over that vault."""
    repo_dir = tmp_path / "code" / "myproject"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo_dir, check=True)
    (repo_dir / "main.py").write_text("# TODO: something real\nprint(1)\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=repo_dir, check=True)
    # Left deliberately dirty: a workspace with nothing uncommitted is the one
    # state that has nothing to say, so the fixture is the interesting state.
    (repo_dir / "wip.py").write_text("still working on it\n")

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(vault_root=vault)
    with TestClient(app) as client:
        yield client, repo_dir


def make_workspace(client, tmp_path, path, note_id="ws-one", title="My Project"):
    response = client.post(
        "/api/notes",
        json={
            "id": note_id,
            "collection": "workspaces",
            "title": title,
            "signifier": "note",
            "status": "open",
            "path": path,
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def test_the_workspaces_collection_exists_before_anything_is_in_it():
    """The editor's dropdown is this list, so the first workspace note has to be
    reachable — the same deadlock `templates` and `transcripts` already have."""
    vault = None
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(vault_root=Path(tmp))
        with TestClient(app) as client:
            assert "workspaces" in client.get("/api/collections").json()


def test_a_note_can_point_at_a_folder_and_the_path_round_trips(repo):
    client, repo_dir = repo
    made = make_workspace(client, None, str(repo_dir))
    assert made["path"] == str(repo_dir)
    # and it survives the file, which is the only store that matters
    again = client.get("/api/notes/ws-one").json()
    assert again["path"] == str(repo_dir)


def test_a_note_without_a_path_comes_back_with_none():
    """The field is absent from a note that is not a workspace, rather than
    empty-stringed into looking like one."""
    made = None
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(vault_root=Path(tmp))
        with TestClient(app) as client:
            made = client.post("/api/notes", json={
                "id": "plain", "collection": "inbox", "title": "Just a note",
            }).json()
    assert made["path"] is None


def test_the_path_can_be_cleared_but_not_typoed_into_a_list(repo):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    cleared = client.patch("/api/notes/ws-one", json={"path": ""}).json()
    assert cleared["path"] is None
    bad = client.patch("/api/notes/ws-one", json={"path": ["a", "b"]})
    assert bad.status_code == 400
    assert "path must be a string" in bad.json()["detail"]


def test_the_list_reads_the_real_folder(repo):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    body = client.get("/api/workspaces").json()
    assert body["summary"]["total"] == 1
    one = body["workspaces"][0]
    assert one["note_id"] == "ws-one"
    assert one["note_title"] == "My Project"
    assert one["is_repo"] is True
    assert one["branch"] == "main"
    assert one["words"].startswith("1 uncommitted")
    assert [m["text"] for m in one["markers"]] == ["something real"]
    assert body["summary"]["need_attention"] == 1


def test_a_note_that_is_not_a_workspace_is_not_in_the_list(repo):
    client, repo_dir = repo
    client.post("/api/notes", json={
        "id": "plain", "collection": "inbox", "title": "Just a note",
    })
    body = client.get("/api/workspaces").json()
    assert body["summary"]["total"] == 0
    assert body["workspaces"] == []


def test_one_workspace_by_id(repo):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    one = client.get("/api/workspaces/ws-one").json()
    assert one["name"] == "myproject"
    assert one["path"] == str(repo_dir)


def test_asking_for_a_workspace_that_is_not_one_is_not_found(repo):
    client, repo_dir = repo
    client.post("/api/notes", json={"id": "plain", "collection": "inbox", "title": "x"})
    assert client.get("/api/workspaces/plain").status_code == 404
    assert client.get("/api/workspaces/nope").status_code == 404


def test_a_folder_that_is_gone_reads_as_gone_rather_than_erroring(repo, tmp_path):
    client, repo_dir = repo
    make_workspace(client, None, str(tmp_path / "moved-away"))
    one = client.get("/api/workspaces/ws-one").json()
    assert one["missing"] is True
    assert one["words"] == "that folder is gone"
    # It is not *work* waiting for you -- `reasons` stays empty, so it sorts
    # below anything uncommitted -- but it is not settled either: the folder is
    # unknown, and the dashboard is told so.
    summary = client.get("/api/workspaces").json()["summary"]
    assert one["reasons"] == []
    assert summary["need_attention"] == 1
    assert summary["broken"] == 1


def test_opening_needs_the_apps_own_request(repo):
    """The only endpoint that launches a process. A web page you merely visited
    can POST to localhost; it cannot set this header without a preflight."""
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    bare = client.post("/api/workspaces/ws-one/open", json={"what": "editor"})
    assert bare.status_code == 403
    assert "X-Nookboard-Action" in bare.json()["detail"]


def test_opening_an_action_that_is_not_one_of_the_three_is_refused(repo):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    response = client.post(
        "/api/workspaces/ws-one/open",
        json={"what": "sh -c 'rm -rf /'"},
        headers={"X-Nookboard-Action": "open"},
    )
    assert response.status_code == 400
    assert "what must be one of" in response.json()["detail"]


def test_opening_a_folder_that_is_gone_says_so(repo, tmp_path):
    client, repo_dir = repo
    make_workspace(client, None, str(tmp_path / "moved-away"))
    response = client.post(
        "/api/workspaces/ws-one/open",
        json={"what": "editor"},
        headers={"X-Nookboard-Action": "open"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "that folder is gone"


def test_opening_reports_the_argv_it_ran(repo, monkeypatch):
    """Pointed at /bin/echo so no window opens on the user's desktop. The
    contract is the reported argv, and that is what is asserted."""
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    monkeypatch.setattr(R, "which_tool", lambda what: "/bin/echo")
    response = client.post(
        "/api/workspaces/ws-one/open",
        json={"what": "editor"},
        headers={"X-Nookboard-Action": "open"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["ran"] == ["/bin/echo", str(repo_dir)]
    assert body["what"] == "editor"


def test_a_missing_tool_is_named_not_implied(repo, monkeypatch):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    monkeypatch.setattr(R, "which_tool", lambda what: "")
    response = client.post(
        "/api/workspaces/ws-one/open",
        json={"what": "terminal"},
        headers={"X-Nookboard-Action": "open"},
    )
    assert response.status_code == 409
    assert "no terminal found" in response.json()["detail"]


def test_the_tools_are_reported_so_the_ui_knows_which_buttons_work(repo):
    client, repo_dir = repo
    make_workspace(client, None, str(repo_dir))
    tools = client.get("/api/workspaces").json()["tools"]
    assert set(tools) == set(W.OPEN_ACTIONS)
    # on this machine everything is installed; a blank one must still be blank
    # rather than absent, so the view can say which button cannot work
    for what, binary in tools.items():
        assert binary == "" or binary.startswith("/")
