import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from app import nookboard_mcp
from app.main import create_app


def test_the_server_enumerates_its_note_tools():
    tools = asyncio.run(nookboard_mcp.server.list_tools())

    assert {tool.name for tool in tools} == {
        "nook_list_notes",
        "nook_get_note",
        "nook_list_collections",
        "nook_create_note",
        "nook_update_note",
        "nook_delete_note",
    }


@pytest.fixture
def mcp_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(create_app(vault_root=tmp_path), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "temporary Nookboard API did not start"

    base = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("NOOKBOARD_MCP_BASE", base)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


def call_tool(name: str, arguments: dict) -> object:
    return asyncio.run(nookboard_mcp.server.call_tool(name, arguments))


def text_payload(result) -> object:
    assert not result.is_error
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


def test_create_note_returns_one_json_text_block(mcp_api: str):
    result = call_tool(
        "nook_create_note",
        {"data": {"id": "first", "title": "First note", "body": "Hello"}},
    )

    assert text_payload(result)["id"] == "first"


def test_list_notes_returns_one_json_text_block(mcp_api: str):
    call_tool(
        "nook_create_note",
        {"data": {"id": "first", "title": "First note", "body": "Hello"}},
    )

    result = call_tool("nook_list_notes", {})

    assert text_payload(result)[0]["id"] == "first"


def test_update_note_changes_the_stored_note(mcp_api: str):
    call_tool("nook_create_note", {"data": {"id": "first", "title": "Before"}})
    result = call_tool(
        "nook_update_note", {"note_id": "first", "data": {"title": "After"}}
    )

    assert text_payload(result)["title"] == "After"
    assert text_payload(call_tool("nook_get_note", {"note_id": "first"}))["title"] == "After"


def test_cold_stdio_session_starts_api_and_runs_note_crud(tmp_path: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    async def exercise():
        params = StdioServerParameters(
            command=shutil.which("uv") or "uv",
            args=["run", "python", "-m", "nookboard_mcp"],
            env={
                **os.environ,
                "NOOKBOARD_MCP_BASE": base,
                "NOOKBOARD_VAULT": str(tmp_path),
                "NOOKBOARD_DAILY_SUMMARY_HOUR": "-1",
            },
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                created = await session.call_tool(
                    "nook_create_note",
                    {"data": {"id": "cold", "title": "Before"}},
                )
                listed = await session.call_tool("nook_list_notes", {})
                updated = await session.call_tool(
                    "nook_update_note",
                    {"note_id": "cold", "data": {"title": "After"}},
                )
                fetched = await session.call_tool("nook_get_note", {"note_id": "cold"})
                deleted = await session.call_tool("nook_delete_note", {"note_id": "cold"})
                return created, listed, updated, fetched, deleted

    created, listed, updated, fetched, deleted = asyncio.run(exercise())

    assert json.loads(created.content[0].text)["id"] == "cold"
    assert json.loads(listed.content[0].text)[0]["id"] == "cold"
    assert json.loads(updated.content[0].text)["title"] == "After"
    assert json.loads(fetched.content[0].text)["title"] == "After"
    assert json.loads(deleted.content[0].text) == {"deleted": "cold"}
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_startup_failure_is_reported_before_stdio_session():
    result = subprocess.run(
        [shutil.which("uv") or "uv", "run", "python", "-m", "nookboard_mcp"],
        env={**os.environ, "NOOKBOARD_MCP_BASE": "http://127.0.0.1:1"},
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 1
    assert "nookboard MCP startup failed" in result.stderr
    assert "Nookboard API exited during startup" in result.stderr


def test_stdio_shutdown_does_not_stop_an_existing_api(mcp_api: str):
    async def exercise():
        params = StdioServerParameters(
            command=shutil.which("uv") or "uv",
            args=["run", "python", "-m", "nookboard_mcp"],
            env={**os.environ, "NOOKBOARD_MCP_BASE": mcp_api},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.list_tools()

    tools = asyncio.run(exercise())

    assert {tool.name for tool in tools.tools}
    assert httpx.get(f"{mcp_api}/api/health", timeout=2).json() == {"ok": True}


def test_documented_stdio_launch_runs_note_crud(mcp_api: str):
    async def exercise():
        params = StdioServerParameters(
            command=shutil.which("uv") or "uv",
            args=["run", "python", "-m", "nookboard_mcp"],
            env={**os.environ, "NOOKBOARD_MCP_BASE": mcp_api},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                created = await session.call_tool(
                    "nook_create_note",
                    {"data": {"id": "stdio", "title": "Before"}},
                )
                updated = await session.call_tool(
                    "nook_update_note",
                    {"note_id": "stdio", "data": {"title": "After"}},
                )
                fetched = await session.call_tool("nook_get_note", {"note_id": "stdio"})
                listed = await session.call_tool("nook_list_notes", {})
                deleted = await session.call_tool("nook_delete_note", {"note_id": "stdio"})
                return tools, created, updated, fetched, listed, deleted

    tools, created, updated, fetched, listed, deleted = asyncio.run(exercise())
    assert {tool.name for tool in tools.tools} == {
        "nook_list_notes",
        "nook_get_note",
        "nook_list_collections",
        "nook_create_note",
        "nook_update_note",
        "nook_delete_note",
    }
    assert json.loads(created.content[0].text)["id"] == "stdio"
    assert json.loads(updated.content[0].text)["title"] == "After"
    assert json.loads(fetched.content[0].text)["title"] == "After"
    assert json.loads(listed.content[0].text)[0]["id"] == "stdio"
    assert json.loads(deleted.content[0].text) == {"deleted": "stdio"}


def test_delete_note_removes_the_stored_note(mcp_api: str):
    call_tool("nook_create_note", {"data": {"id": "gone", "title": "Delete me"}})
    result = call_tool("nook_delete_note", {"note_id": "gone"})

    assert text_payload(result) == {"deleted": "gone"}
    with pytest.raises(ToolError, match="nook_get_note"):
        call_tool("nook_get_note", {"note_id": "gone"})


# -- the API this module starts, and where it is allowed to start it ----------
def test_only_a_loopback_host_may_be_bound():
    for host in ("127.0.0.1", "127.0.0.5", "::1", "localhost"):
        assert nookboard_mcp._is_loopback(host), host
    for host in ("0.0.0.0", "192.168.1.10", "10.0.0.1", "example.com"):
        assert not nookboard_mcp._is_loopback(host), host


def test_a_note_id_becomes_one_encoded_path_segment():
    assert nookboard_mcp._note_path("plain") == "/api/notes/plain"
    assert nookboard_mcp._note_path("a b#c?d") == "/api/notes/a%20b%23c%3Fd"


def test_the_owned_api_refuses_a_host_other_machines_can_reach(monkeypatch: pytest.MonkeyPatch):
    # Nothing is answering at this address (it is TEST-NET-3), so the readiness
    # probe is answered here rather than over the network: what is under test is
    # the decision, not the timeout.
    monkeypatch.setattr(nookboard_mcp, "_api_is_ready", lambda base: False)

    with pytest.raises(RuntimeError, match="loopback"):
        nookboard_mcp._start_api("http://203.0.113.7:8765")


def test_an_api_already_running_elsewhere_is_still_used(monkeypatch: pytest.MonkeyPatch):
    """Only *starting* one is refused. A base someone else already serves is
    their server and their boundary to keep, and this process starts nothing."""
    monkeypatch.setattr(nookboard_mcp, "_api_is_ready", lambda base: True)

    assert nookboard_mcp._start_api("http://203.0.113.7:8765") is None


def test_the_owned_api_leaves_the_vault_path_to_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The API defaults the vault to `<repo>/vault` from its own file location.

    This module used to override that with `<its cwd>/vault`, so an MCP client
    launched from anywhere but the repository pointed the API at a vault that
    does not exist — and quietly created and wrote to an empty one. The client's
    own NOOKBOARD_VAULT still has to win, which is what the second half checks.
    """
    seen: dict = {}

    class FakeProcess:
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        seen.update(kwargs)
        return FakeProcess()

    monkeypatch.delenv("NOOKBOARD_VAULT", raising=False)
    # Only Popen is faked, and only here: no process is really started, and the
    # real subprocess module is left as every other test finds it.
    monkeypatch.setattr(
        nookboard_mcp, "subprocess", SimpleNamespace(Popen=fake_popen, DEVNULL=subprocess.DEVNULL)
    )
    monkeypatch.chdir(tmp_path)

    def start_and_capture() -> dict:
        seen.clear()
        calls = {"n": 0}

        def counting_probe(base: str) -> bool:
            # Nothing answers before the spawn, and the API answers afterwards,
            # which is the first thing the waiting loop looks for.
            calls["n"] += 1
            return calls["n"] > 1

        monkeypatch.setattr(nookboard_mcp, "_api_is_ready", counting_probe)
        process = nookboard_mcp._start_api("http://127.0.0.1:8765")
        assert process is not None, "the API should have been started"
        return dict(seen)

    started = start_and_capture()
    assert "NOOKBOARD_VAULT" not in started["env"], (
        "the vault must be left to the API's own <repo>/vault default"
    )
    assert Path(started["cwd"]).resolve() == Path(nookboard_mcp.__file__).resolve().parent.parent

    monkeypatch.setenv("NOOKBOARD_VAULT", str(tmp_path / "mine"))
    named = start_and_capture()
    assert named["env"]["NOOKBOARD_VAULT"] == str(tmp_path / "mine"), (
        "a vault the caller named must be the one the API is given"
    )


def test_note_ids_with_url_delimiters_address_the_note_that_was_named(mcp_api: str):
    """`#` and `?` are legal in a filename stem, so they are legal in an id.

    Unencoded they end the path instead: get answered with a prefix-named note,
    and update or delete acted on that note while reporting this id as done.
    """
    odd = "weird#id?really"
    call_tool("nook_create_note", {"data": {"id": odd, "title": "Odd id"}})

    assert text_payload(call_tool("nook_get_note", {"note_id": odd}))["id"] == odd
    updated = call_tool("nook_update_note", {"note_id": odd, "data": {"title": "Renamed"}})
    assert text_payload(updated)["title"] == "Renamed"
    assert text_payload(call_tool("nook_delete_note", {"note_id": odd})) == {"deleted": odd}
    with pytest.raises(ToolError):
        call_tool("nook_get_note", {"note_id": odd})


def test_an_explicit_null_clears_a_field(mcp_api: str):
    """The update tool promises that a null clears a field; it has to mean it."""
    call_tool(
        "nook_create_note",
        {
            "data": {
                "id": "moody",
                "title": "Moody",
                "mood": "good",
                "tags": ["school", "week-3"],
                "dates": ["2026-09-25"],
            }
        },
    )

    cleared = text_payload(
        call_tool(
            "nook_update_note",
            {"note_id": "moody", "data": {"mood": None, "tags": None, "dates": None}},
        )
    )

    assert cleared["mood"] is None, "an explicit null should clear a scalar"
    assert cleared["tags"] == [], "a null list should clear to empty, not to None"
    assert cleared["dates"] == [], "a null list should clear to empty, not fail the write"


def test_a_missing_field_is_still_left_alone(mcp_api: str):
    call_tool(
        "nook_create_note",
        {"data": {"id": "kept", "title": "Before", "mood": "good"}},
    )

    after = text_payload(
        call_tool("nook_update_note", {"note_id": "kept", "data": {"title": "After"}})
    )

    assert after["title"] == "After"
    assert after["mood"] == "good", "a field the caller never mentioned keeps its value"


# -- the reason "to clear a field" needed fixing on the API side too ----------
def test_the_api_clears_a_list_field_sent_as_null(mcp_api: str):
    """The MCP tool is not the only client: a null has to clear over HTTP too."""
    httpx.post(
        f"{mcp_api}/api/notes",
        json={"id": "listed", "title": "Listed", "tags": ["a", "b"], "dates": ["2026-09-25"]},
        timeout=5,
    ).raise_for_status()

    cleared = httpx.patch(
        f"{mcp_api}/api/notes/listed", json={"tags": None, "dates": None}, timeout=5
    )

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tags"] == []
    assert cleared.json()["dates"] == []
