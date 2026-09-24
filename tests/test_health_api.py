"""The health check against real sockets.

A local http.server and a bound-but-silent socket, because the properties worth
testing here are properties of the network: an answer is up, a 404 is an answer, a
closed port is down, and a host that says nothing is *bounded* rather than waited on.
"""

from __future__ import annotations

import http.server
import socket
import threading

import pytest
from starlette.testclient import TestClient

from app import health as H
from app import health_run as R
from app.main import create_app


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802
        code = 200 if self.path == "/" else 404
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):                       # keep the test output quiet
        pass


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def silent_port():
    """A socket that accepts a connection and then says nothing, ever."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    sock.close()


@pytest.fixture
def dead_port():
    """A port nothing is listening on: bind it, learn it, close it."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    yield f"http://127.0.0.1:{port}"


def test_an_answering_service_is_up_and_timed(site):
    res = R.probe(site + "/")
    assert res["kind"] == H.UP and res["status"] == 200
    assert res["words"] == "answering" and res["ms"] is not None and res["at"]


def test_a_404_is_an_answer_not_a_silence(site):
    res = R.probe(site + "/nope")
    assert res["kind"] == H.ANSWERED and res["status"] == 404
    assert res["words"] == "answered 404"


def test_a_port_with_nothing_on_it_is_down_in_words(dead_port):
    res = R.probe(dead_port)
    assert res["kind"] == H.DOWN
    assert res["error"] == "nothing is listening on that port"
    assert res["status"] is None


def test_a_host_that_says_nothing_is_bounded_rather_than_waited_on(silent_port):
    """The property is the timeout, so the test has to be about the clock."""
    res = R.probe(silent_port, timeout=0.3)
    assert res["kind"] == H.DOWN
    assert "0.3 seconds" in res["error"]
    assert res["ms"] < 2000


def test_every_address_is_asked_at_once_not_one_after_another(site, silent_port):
    """Two silent hosts would take two timeouts in series; in parallel, one."""
    import time

    urls = [site + "/", silent_port, silent_port.replace("127.0.0.1", "localhost")]
    started = time.monotonic()
    out = R.check_all(urls, timeout=0.4)
    took = time.monotonic() - started
    assert len(out["results"]) == 3
    assert took < 1.0, f"{took:.2f}s for three checks means they were serial"
    assert out["counts"][H.UP] == 1 and out["counts"][H.DOWN] == 2


def test_an_address_that_cannot_be_opened_is_reported_not_probed():
    out = R.check_all(["127.0.0.1:8765", "javascript:alert(1)"])
    assert set(out["results"]) == {"127.0.0.1:8765", "javascript:alert(1)"}
    assert all(r["kind"] == H.UNKNOWN for r in out["results"].values())
    assert out["counts"][H.UNKNOWN] == 2


def test_the_endpoint_checks_what_the_vault_points_at(site, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    app = create_app(vault_root=root)
    with TestClient(app) as client:
        client.post("/api/notes", json={"id": "b1", "collection": "bookmarks", "title": "Here",
                                        "url": site + "/"})
        client.post("/api/notes", json={"id": "b2", "collection": "bookmarks", "title": "Gone",
                                        "url": "127.0.0.1:1"})
        body = client.post("/api/bookmarks/check").json()

    assert body["checked"]
    assert body["results"][site + "/"]["kind"] == H.UP
    assert body["results"]["127.0.0.1:1"]["kind"] == H.UNKNOWN
    assert body["counts"][H.UP] == 1
