"""TDD: llama.cpp streaming client.

These run against a real HTTP server emitting real SSE framing, not a mocked
transport, so the SSE parsing is exercised against the wire format. The
`sse_server` fixture lives in conftest.py so the endpoint tests share it.
"""
from __future__ import annotations

import asyncio

import pytest

from app.llm import LLMError, LlamaCpp


def _chunk(*, content=None, reasoning=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def collect(url, **kw):
    client = LlamaCpp(url, "test-model", **kw)
    return asyncio.run(_collect(client))


async def _collect(client):
    out = []
    async for ev in client.stream([{"role": "user", "content": "hi"}]):
        out.append(ev)
    return out


def test_streams_content(sse_server):
    base, set_script = sse_server
    set_script([_chunk(content="Hello"), _chunk(content=" world", finish="stop")])
    events = collect(base)
    assert [e.kind for e in events] == ["content", "content", "done"]
    assert "".join(e.text for e in events if e.kind == "content") == "Hello world"


def test_reasoning_is_separated_from_content(sse_server):
    """The real model emits reasoning_content before any content."""
    base, set_script = sse_server
    set_script([
        _chunk(reasoning="let me think"),
        _chunk(reasoning=" about it"),
        _chunk(content="The answer."),
        _chunk(finish="stop"),
    ])
    events = collect(base)
    assert [e.kind for e in events] == ["reasoning", "reasoning", "content", "done"]
    assert "".join(e.text for e in events if e.kind == "reasoning") == "let me think about it"
    assert "".join(e.text for e in events if e.kind == "content") == "The answer."


def test_budget_exhausted_while_thinking_reports_truncation(sse_server):
    """Observed live: finish_reason=length with empty content and no error.

    This is the case that would otherwise look like a blank success.
    """
    base, set_script = sse_server
    set_script([
        _chunk(reasoning="thinking and thinking"),
        _chunk(reasoning="more thinking", finish="length"),
    ])
    events = collect(base)
    kinds = [e.kind for e in events]
    assert "truncated" in kinds
    assert "content" not in kinds


def test_complete_raises_when_model_never_answers(sse_server):
    base, set_script = sse_server
    set_script([_chunk(reasoning="thinking", finish="length")])
    with pytest.raises(LLMError, match="budget"):
        asyncio.run(LlamaCpp(base, "m").complete([{"role": "user", "content": "hi"}]))


def test_complete_returns_joined_content(sse_server):
    base, set_script = sse_server
    set_script([_chunk(content="a"), _chunk(content="b", finish="stop")])
    assert asyncio.run(LlamaCpp(base, "m").complete([{"role": "user", "content": "x"}])) == "ab"


def test_http_error_raises_llm_error(sse_server):
    base, set_script = sse_server
    set_script([], status=500)
    with pytest.raises(LLMError, match="500"):
        collect(base)


def test_unreachable_server_raises_llm_error():
    with pytest.raises(LLMError, match="could not reach"):
        asyncio.run(LlamaCpp("http://127.0.0.1:1/v1", "m").complete(
            [{"role": "user", "content": "hi"}]))
