"""Streaming integration tests for Task 3."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from task3_stream_guardrail.provider import (
    ProviderStreamError,
)
from task3_stream_guardrail.sse import (
    decode_upstream_data_line,
)
from task3_stream_guardrail.stream import (
    guarded_sse_stream,
)


def _content_from_event(event: bytes) -> str:
    text = event.decode()

    for line in text.splitlines():
        parsed = decode_upstream_data_line(line)

        if parsed is not None and not parsed.done:
            return parsed.text

    return ""


class ListProvider:
    """Yield a deterministic list of text chunks."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def stream_text(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        del payload

        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_split_pii_is_redacted_across_provider_events() -> None:
    provider = ListProvider(
        [
            "Contact alice@exa",
            "mple.com, SSN 123-45-",
            "6789, card 4111 1111 ",
            "1111 1111. Done.",
        ]
    )

    events = [
        event
        async for event in guarded_sse_stream(
            provider,
            {"model": "synthetic"},
        )
    ]

    output = "".join(
        _content_from_event(event)
        for event in events
    )

    assert output == (
        "Contact [REDACTED], "
        "SSN [REDACTED], "
        "card [REDACTED]. Done."
    )

    assert events[-1] == b"data: [DONE]\n\n"


class GatedProvider:
    """Block after the first safe chunk to prove progressive delivery."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.finished = False

    async def stream_text(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        del payload

        yield "Hello, "

        await self.release.wait()

        yield "world."
        self.finished = True


@pytest.mark.asyncio
async def test_safe_output_is_emitted_before_provider_finishes() -> None:
    provider = GatedProvider()

    stream = guarded_sse_stream(
        provider,
        {"model": "synthetic"},
    )

    first = await anext(stream)

    assert _content_from_event(first) == "Hello, "
    assert provider.finished is False

    provider.release.set()

    remainder = [
        event
        async for event in stream
    ]

    assert "".join(
        _content_from_event(event)
        for event in remainder
    ) == "world."

    assert provider.finished is True


class FailingProvider:
    """Fail after emitting an unresolved possible PII suffix."""

    async def stream_text(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        del payload

        yield "SSN 123-45-"

        raise ProviderStreamError(
            "connection failed to secret.internal:9443"
        )


@pytest.mark.asyncio
async def test_provider_failure_drops_unresolved_pii_and_sanitizes_error() -> None:
    events = [
        event
        async for event in guarded_sse_stream(
            FailingProvider(),
            {"model": "synthetic"},
        )
    ]

    wire = b"".join(events).decode()

    assert "SSN " in wire

    # The unresolved sensitive suffix must never be flushed.
    assert "123-45-" not in wire

    # Raw provider topology and exception text must not escape.
    assert "secret.internal" not in wire
    assert "connection failed" not in wire

    assert "LLM provider stream failed" in wire
    assert "event: error" in wire

    # A failed stream must not pretend to complete successfully.
    assert "data: [DONE]" not in wire


@pytest.mark.asyncio
async def test_safe_stream_preserves_order() -> None:
    provider = ListProvider(
        [
            "One, ",
            "two, ",
            "three.",
        ]
    )

    events = [
        event
        async for event in guarded_sse_stream(
            provider,
            {},
        )
    ]

    output = "".join(
        _content_from_event(event)
        for event in events
    )

    assert output == "One, two, three."


def test_error_event_contains_valid_json() -> None:
    from task3_stream_guardrail.sse import encode_gateway_error

    event = encode_gateway_error("safe message").decode()

    data_line = next(
        line
        for line in event.splitlines()
        if line.startswith("data:")
    )

    payload = json.loads(
        data_line.removeprefix("data:").strip()
    )

    assert payload["error"]["message"] == "safe message"
