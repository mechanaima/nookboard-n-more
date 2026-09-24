"""Streaming client for a local llama.cpp server (OpenAI-compatible).

Two things about the local model shape this design, both measured against the
running Bonsai-27B instance rather than assumed:

1. It is a reasoning model. Most of the completion budget goes into a
   `reasoning_content` block before any `content` appears. With too small a
   `max_tokens` it returns finish_reason="length" and EMPTY content, which
   looks like a silent failure. So the budget is generous by default and
   running out of tokens is reported rather than passed off as an answer.

2. It runs at roughly 26 tokens/second, so a trivial answer takes 20-40s.
   Nothing here can be a blocking request; everything streams so the caller
   can show progress.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator, Iterable

import httpx


class LLMError(RuntimeError):
    """The server was unreachable, or answered with an error status."""


@dataclass(frozen=True)
class StreamEvent:
    kind: str          # "reasoning" | "content" | "truncated" | "done"
    text: str = ""


class LlamaCpp:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        max_tokens: int = 2048,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.transport = transport

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _payload(self, messages: Iterable[dict], max_tokens: int | None, temperature: float) -> dict:
        return {
            "model": self.model,
            "messages": list(messages),
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature,
            "stream": True,
        }

    async def stream(
        self,
        messages: Iterable[dict],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamEvent]:
        """Yield reasoning, then content, then a terminal event.

        The caller must handle `truncated`: it means the model burned the whole
        budget thinking and produced no answer.
        """
        timeout = httpx.Timeout(self.timeout, connect=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                async with client.stream(
                    "POST", self.chat_url, json=self._payload(messages, max_tokens, temperature)
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="replace")[:400]
                        raise LLMError(f"llm returned {resp.status_code}: {body}")

                    saw_content = False
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield StreamEvent("reasoning", reasoning)
                        content = delta.get("content")
                        if content:
                            saw_content = True
                            yield StreamEvent("content", content)

                        if choice.get("finish_reason") == "length" and not saw_content:
                            # Whole budget spent thinking; there is no answer.
                            yield StreamEvent(
                                "truncated",
                                "the model used its entire token budget reasoning "
                                "and produced no answer - raise NOOKBOARD_LLM_MAX_TOKENS",
                            )
                            return

                    yield StreamEvent("done")
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach {self.chat_url}: {exc}") from exc

    async def complete(
        self,
        messages: Iterable[dict],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Collect a full answer. Raises if the model produced no content."""
        parts: list[str] = []
        truncated = ""
        async for ev in self.stream(messages, max_tokens=max_tokens, temperature=temperature):
            if ev.kind == "content":
                parts.append(ev.text)
            elif ev.kind == "truncated":
                truncated = ev.text
        text = "".join(parts).strip()
        if not text:
            raise LLMError(truncated or "the model returned no content")
        return text
