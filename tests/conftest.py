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
