"""The PWA files: what makes nookboard installable, and whether they add up.

The behaviour of the worker -- network-first, never the API -- is verified in a
browser, because that is the only place it exists. What is checked here is the
part that fails *silently*: a manifest advertising an icon size the file is not,
or a worker served from a path whose scope makes it useless. Both would look
finished and install nothing.
"""

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import pwa
from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "static"


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    with TestClient(create_app(vault_root=root)) as client:
        yield client


def _png_size(path: Path) -> tuple[int, int]:
    """Width and height, read out of the PNG header rather than trusted."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", header[16:24])


def test_the_manifest_says_what_an_installed_app_needs():
    manifest = pwa.manifest()
    assert manifest["name"] and manifest["short_name"]
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    # A browser tab is not an installation; this is the one key that decides it.
    assert manifest["display"] == "standalone"
    assert manifest["theme_color"] == pwa.THEME
    assert manifest["background_color"] == pwa.BACKGROUND


def test_every_icon_advertised_is_a_real_png_of_the_size_it_claims():
    for icon in pwa.manifest()["icons"]:
        path = STATIC / icon["src"].removeprefix("/static/")
        assert path.is_file(), f"{icon['src']} is advertised in the manifest and missing"
        assert f"{_png_size(path)[0]}x{_png_size(path)[1]}" == icon["sizes"], (
            f"{icon['src']} claims {icon['sizes']}"
        )


def test_there_is_a_maskable_icon_so_android_can_crop_it():
    """Without one, Android puts a shrunken square inside a white circle."""
    maskable = [i for i in pwa.manifest()["icons"] if i["purpose"] == "maskable"]
    assert maskable, "no maskable icon: the launcher will letterbox the mark"
    assert all(i["sizes"] == "512x512" for i in maskable)


def test_the_manifest_is_served_as_a_manifest(vault):
    """A JSON content type makes Chrome ignore it, and it says nothing."""
    resp = vault.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(pwa.MANIFEST_TYPE)
    assert resp.json()["start_url"] == "/"


def test_the_worker_is_served_from_the_root(vault):
    """A worker's scope is the directory it is served from, so one under
    `/static/` could only ever control `/static/` -- and a worker sees exactly
    one thing worth seeing, which is a navigation."""
    resp = vault.get("/service-worker.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_the_worker_refuses_to_remember_answers(vault):
    """The API cache-skip and the network-first order are the two rules the
    worker exists to keep; the browser pass covers the behaviour, and this
    catches the edit that removes one of them by accident."""
    source = vault.get("/service-worker.js").text
    assert 'url.pathname.startsWith("/api/")' in source
    assert source.index("await fetch(request)") < source.index("caches.match(request)")


def test_the_page_links_the_manifest():
    html = (STATIC / "index.html").read_text()
    assert 'rel="manifest"' in html
    assert "/manifest.webmanifest" in html
