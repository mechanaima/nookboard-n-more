"""The Lucide icon set, and the two rules about a note's icon.

The important test in here is `test_the_two_generated_halves_agree`: the browser and
the server each hold a copy of the icon names, both are generated from one download,
and this is what makes "there is no second copy of which icons exist" a fact rather
than a hope. The rest is the same shape as the url rules, deliberately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import icons, note_icons
from app.main import create_app
from app.models import Note

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor" / "lucide.js"


# -- the two generated halves -------------------------------------------------

def test_the_two_generated_halves_agree():
    """`static/vendor/lucide.js` and `app/icons.py`, from one tarball, must match.

    They are generated together, but nothing stops someone editing one by hand or
    updating only half. A name the picker offers that the API cannot recognise -- or
    the reverse -- is a note whose icon silently does not draw, so this is the guard
    that keeps the promise above it honest.
    """
    text = VENDOR.read_text(encoding="utf-8")
    version = re.search(r'LUCIDE_VERSION = "([^"]+)"', text).group(1)
    keys = re.findall(r'^\t"(.*?)":', text, re.M)
    assert version == icons.LUCIDE_VERSION
    assert keys == sorted(keys), "the vendored set must stay in Lucide's own order"
    assert keys == list(icons.NAMES), "the browser's names and the server's names differ"
    assert len(keys) > 1000, f"only {len(keys)} icons: the vendor file looks truncated"


def test_every_icon_has_drawable_markup():
    """A name with no drawing is worse than no name: the picker would offer a blank."""
    text = VENDOR.read_text(encoding="utf-8")
    values = re.findall(r'^\t"[^"]+": ("(?:[^"\\]|\\.)*"),$', text, re.M)
    assert len(values) == len(icons.NAMES)
    for raw in values:
        markup = json.loads(raw)
        assert markup.startswith("<") and markup.endswith(">"), markup[:40]


def test_every_shape_element_is_self_closing():
    """The bug this exists to prevent, and it is a nasty one.

    Stripping Lucide's `/>` down to `>` saves one byte per element and nests every
    shape inside the one before it -- `<path/><path/>` becomes `<path><path>`, and a
    browser paints only the outermost. `brain` drew as a bare vertical line, `book-open`
    the same, `notebook-pen` as a corner bracket; it looked like a rendering bug in the
    view, and every name was still present, so only the markup can catch it.
    """
    text = VENDOR.read_text(encoding="utf-8")
    shape = re.compile(r"<(path|circle|rect|line|polyline|polygon|ellipse)\b([^>]*)>")
    checked = 0
    for raw in re.findall(r'^\t"[^"]+": ("(?:[^"\\]|\\.)*"),$', text, re.M):
        markup = json.loads(raw)
        for hit in shape.finditer(markup):
            checked += 1
            assert hit.group(2).rstrip().endswith("/"), (
                f"unclosed <{hit.group(1)}> -- shapes would nest: {markup[:70]}"
            )
    assert checked > 2000, f"only {checked} shapes parsed: the markup test found nothing"


# -- what a name is ----------------------------------------------------------

def test_whitespace_is_not_a_name():
    assert note_icons.clean("  ") is None
    assert note_icons.clean(None) is None
    assert note_icons.clean(" server ") == "server"


@pytest.mark.parametrize("name", ["server", "book-open", "heart-pulse", "hard-drive"])
def test_a_real_name_is_known(name):
    assert note_icons.is_known(name)
    assert note_icons.icon_problem(name) is None


def test_an_unknown_name_is_reported_in_a_sentence_not_a_code():
    problem = note_icons.icon_problem("serverr")
    assert problem is not None
    assert "serverr" in problem and "Lucide" in problem
    assert "_" not in problem and " " in problem


def test_no_icon_is_not_a_problem():
    """An icon-less note is most notes, and is not an error."""
    assert note_icons.icon_problem(None) is None
    assert note_icons.icon_problem("") is None
    assert note_icons.icon_problem("   ") is None


# -- the note's own field ----------------------------------------------------

def test_an_icon_survives_the_file_in_both_directions(tmp_path):
    note = Note(id="a", collection="inbox", title="t", body="b", icon="server")
    text = note.to_markdown()
    assert "icon: server" in text
    assert Note.from_markdown(text).icon == "server"


def test_a_numeric_looking_name_stays_text(tmp_path):
    """`icon: 7` arrives from YAML as an int, and an int is not a name to look up."""
    note = Note(id="a", collection="inbox", title="t", body="b", icon="7")
    assert Note.from_markdown(note.to_markdown()).icon == "7"
    assert note.to_dict()["icon"] == "7"


# -- the API -----------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    with TestClient(create_app(vault_root=root)) as c:
        yield c


def test_an_icon_can_be_set_and_cleared_through_the_api(client):
    client.post("/api/notes", json={"id": "n1", "collection": "inbox", "title": "T", "icon": "server"})
    assert client.get("/api/notes/n1").json()["icon"] == "server"
    assert client.patch("/api/notes/n1", json={"icon": ""}).json()["icon"] is None
    assert client.patch("/api/notes/n1", json={"icon": "hard-drive"}).json()["icon"] == "hard-drive"


def test_an_icon_that_is_not_a_string_is_refused(client):
    client.post("/api/notes", json={"id": "n1", "collection": "inbox", "title": "T"})
    assert client.patch("/api/notes/n1", json={"icon": ["server"]}).status_code == 400


def test_an_unknown_name_is_kept_and_reported_not_refused(client):
    """A note is the person's file: a typo costs them the icon, not the note."""
    saved = client.post("/api/notes", json={"id": "n1", "collection": "bookmarks",
                                            "title": "Shrine", "icon": "serverr",
                                            "url": "http://127.0.0.1:8888"})
    assert saved.status_code == 201
    assert saved.json()["icon"] == "serverr"
    item = client.get("/api/bookmarks").json()["groups"][0]["items"][0]
    assert item["icon"] == "serverr"
    assert "serverr" in item["icon_problem"]


def test_a_bookmark_with_a_real_icon_has_nothing_to_report(client):
    client.post("/api/notes", json={"id": "n1", "collection": "bookmarks", "title": "Shrine",
                                    "icon": "flame", "url": "http://127.0.0.1:8888"})
    item = client.get("/api/bookmarks").json()["groups"][0]["items"][0]
    assert item["icon"] == "flame" and item["icon_problem"] is None


def test_writing_an_icon_leaves_the_folder_and_the_address_alone(client, tmp_path):
    """Three neighbouring fields, three different writes: the pair that broke once."""
    folder = tmp_path / "code"
    folder.mkdir()
    client.post("/api/notes", json={"id": "n1", "collection": "bookmarks", "title": "T",
                                    "path": str(folder), "url": "https://example.com"})
    note = client.patch("/api/notes/n1", json={"icon": "server"}).json()
    assert note["icon"] == "server"
    assert note["path"] == str(folder)
    assert note["url"] == "https://example.com"
