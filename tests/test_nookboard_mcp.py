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
