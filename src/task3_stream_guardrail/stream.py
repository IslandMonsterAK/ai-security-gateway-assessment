"""Streaming guardrail composition for Task 3."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .provider import ProviderStreamError, TextStreamProvider
from .redactor import StreamingRedactor
from .sse import (
    encode_done,
    encode_gateway_error,
    encode_openai_delta,
)


async def guarded_sse_stream(
    provider: TextStreamProvider,
    payload: dict[str, Any],
) -> AsyncIterator[bytes]:
    """Stream provider output through the bounded PII guardrail."""

    redactor = StreamingRedactor()

    try:
        async for text in provider.stream_text(payload):
            safe = redactor.feed(text)

            if safe:
                yield encode_openai_delta(safe)

    except ProviderStreamError:
        # Fail closed on unresolved trailing text. If the upstream disappears
        # while a possible PII value is still pending, do not flush that
        # ambiguous suffix to the caller.
        yield encode_gateway_error(
            "LLM provider stream failed"
        )
        return

    final = redactor.flush()

    if final:
        yield encode_openai_delta(final)

    yield encode_done()
