"""Shared pytest fixtures.

`sse_server` is a real HTTP server emitting real SSE framing, so streaming code
is exercised against the wire format rather than a mocked transport. It is
defined here so both test_llm.py and test_ai_api.py can use it.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _SSEHandler(BaseHTTPRequestHandler):
    script: list[dict] = []
    status = 200

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(self.status)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if self.status != 200:
            self.wfile.write(b"boom")
            self.wfile.flush()
            return
        for chunk in self.__class__.script:
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture
def seed_task(tmp_path):
    """Write a finished task straight to disk, before the app boots.

    A completion date is stamped as *today* when a task is completed through the
    API, so summarising a past period can only be reached by seeding one. The
    same Markdown shape the daily tests seed, which is why it lives here rather
    than being written out again.
    """

    def _seed(nid, title, *, completed, status="complete", collection="inbox"):
        folder = tmp_path / collection
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{nid}.md"
        path.write_text(
            "\n".join(
                [
                    "---",
                    f"id: {nid}",
                    f"collection: {collection}",
                    f"title: {title}",
                    "signifier: task",
                    f"status: {status}",
                    "dates: []",
                    f"created: {completed.isoformat()}",
                    f"completed: {completed.isoformat()}",
                    "tags: []",
                    "---",
                    "",
                ]
            )
        )
        return path

    return _seed


@pytest.fixture
def client_factory(tmp_path, sse_server):
    """Build an app over the seeded vault, wired to the fake model.

    Seed first, then build: the index is rebuilt at boot and a note written
    afterwards would not be in it.
    """
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    base, set_script = sse_server

    def _make():
        settings = Settings(
            vault=tmp_path,
            llm_url=base,
            llm_model="fake",
            llm_max_tokens=2048,
            llm_timeout=30.0,
        )
        client = TestClient(create_app(settings=settings))
        client.vault_root = tmp_path  # type: ignore[attr-defined]
        client.set_script = set_script  # type: ignore[attr-defined]
        return client

    return _make


@pytest.fixture
def sse_server():
    """Yields (base_url, set_script) where set_script(chunks, status=200)."""
    _SSEHandler.script = []
    _SSEHandler.status = 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"

    def set_script(chunks, status=200):
        _SSEHandler.script = chunks
        _SSEHandler.status = status

    yield base, set_script

    httpd.shutdown()
    httpd.server_close()
