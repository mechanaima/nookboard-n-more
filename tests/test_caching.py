"""Nothing this app serves is a thing to keep.

The app has no asset versioning -- `app.js` and `static/vendor/lucide.js` have the
same URL after every change -- and it is served from one machine to one person, so a
cached copy can only ever be a copy of something that has since been fixed. Twice
that was the whole of a bug report: a stale `app.js` after the history-rendering fix,
and a stale `lucide.js` that kept drawing the icon set from before the generator was
fixed. These tests pin the header that ended both.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    with TestClient(create_app(vault_root=root)) as c:
        yield c


@pytest.mark.parametrize("path", ["/api/home", "/api/board", "/api/bookmarks", "/api/notes", "/"])
def test_no_response_is_cacheable(client, path):
    assert client.get(path).headers.get("cache-control") == "no-store"


def test_static_files_are_not_cacheable_either(client):
    """The one that bit: a vendored icon set the browser kept serving after a fix."""
    resp = client.get("/static/js/app.js")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
