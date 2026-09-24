"""The bookmark endpoints.

The interesting properties here are not the happy path: an address that a browser
cannot open has to be *reported* rather than refused (losing the note over a typo
would be the worse failure), and a note's `path` must survive an `url` write --
which is exactly the pair of fields that a duplicated keyword in the update path
once broke.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app import bookmarks as B
from app.main import create_app


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    app = create_app(vault_root=root)
    with TestClient(app) as client:
        yield client, root


def make(client, nid="bk-1", title="Nookboard", url="http://127.0.0.1:8765", tags=None, **kw):
    return client.post(
        "/api/notes",
        json={
            "id": nid,
            "collection": "bookmarks",
            "title": title,
            "signifier": "note",
            "status": "open",
            "url": url,
            "tags": tags or [],
            **kw,
        },
    )


def test_a_note_saved_with_an_address_shows_up_under_its_tag(vault):
    client, _ = vault
    assert make(client, tags=["services"]).status_code == 201
    payload = client.get("/api/bookmarks").json()
    assert payload["count"] == 1
    group = payload["groups"][0]
    assert group["name"] == "services"
    assert group["items"][0]["title"] == "Nookboard"
    assert group["items"][0]["host"] == "127.0.0.1:8765"


def test_the_address_survives_the_round_trip_through_the_file(vault):
    client, root = vault
    make(client, url="https://braxia.tel/misskey?x=1#y")
    written = next(root.glob("**/bk-1.md")).read_text()
    assert "url: https://braxia.tel/misskey?x=1#y" in written
    # and the app reads back what it wrote
    payload = client.get("/api/bookmarks").json()
    assert payload["groups"][0]["items"][0]["url"] == "https://braxia.tel/misskey?x=1#y"


def test_an_address_a_browser_cannot_open_is_kept_and_reported(vault):
    client, _ = vault
    assert make(client, url="127.0.0.1:8765").status_code == 201
    payload = client.get("/api/bookmarks").json()
    assert payload["unusable"] == 1
    shown = payload["groups"][-1]
    assert shown["name"] == B.UNUSABLE
    assert "https://" in shown["items"][0]["problem"]


def test_clearing_the_address_takes_it_out_of_the_view(vault):
    client, _ = vault
    make(client)
    assert client.patch("/api/notes/bk-1", json={"url": ""}).status_code == 200
    assert client.get("/api/bookmarks").json()["count"] == 0


def test_a_url_that_is_not_a_string_is_refused_rather_than_coerced(vault):
    client, _ = vault
    make(client)
    assert client.patch("/api/notes/bk-1", json={"url": ["a", "b"]}).status_code == 400


def test_a_note_that_is_not_a_bookmark_never_appears(vault):
    client, _ = vault
    client.post("/api/notes", json={"id": "plain", "collection": "inbox", "title": "Plain"})
    assert client.get("/api/bookmarks").json()["count"] == 0


def test_writing_an_address_does_not_disturb_the_folder_a_note_points_at(vault, tmp_path):
    """The two fields are neighbours in the model, the API and the file.

    A note can be both a workspace and a bookmark, and one write must not carry the
    other field's value -- a mistake this layer has already made once, where a
    duplicate keyword in the update path took the whole app down on import.
    """
    client, _ = vault
    folder = tmp_path / "code"
    folder.mkdir()
    make(client, path=str(folder))
    client.patch("/api/notes/bk-1", json={"url": "https://example.com/docs"})
    note = client.get("/api/notes/bk-1").json()
    assert note["url"] == "https://example.com/docs"
    assert note["path"] == str(folder)

    client.patch("/api/notes/bk-1", json={"path": ""})
    note = client.get("/api/notes/bk-1").json()
    assert note["path"] is None
    assert note["url"] == "https://example.com/docs"
