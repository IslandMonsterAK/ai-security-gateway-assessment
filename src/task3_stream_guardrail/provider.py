"""OpenAI-compatible streaming provider adapter for Task 3."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx2

from .sse import SSEDecodeError, decode_upstream_data_line


class ProviderStreamError(RuntimeError):
    """Caller-safe representation of an upstream streaming failure."""


class TextStreamProvider(Protocol):
    """Minimal provider contract used by the guardrail."""

    def stream_text(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        """Yield provider-generated text deltas."""


class OpenAICompatibleProvider:
    """Consume an OpenAI-compatible SSE text-generation endpoint."""

    def __init__(
        self,
        url: str,
        client: httpx2.AsyncClient,
    ) -> None:
        self._url = url
        self._client = client

    async def stream_text(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        """Forward the request and yield decoded text deltas."""

        upstream_payload = dict(payload)
        upstream_payload["stream"] = True

        try:
            async with self._client.stream(
                "POST",
                self._url,
                json=upstream_payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    try:
                        event = decode_upstream_data_line(line)
                    except SSEDecodeError as exc:
                        raise ProviderStreamError(
                            "LLM provider returned an invalid stream"
                        ) from exc

                    if event is None:
                        continue

                    if event.done:
                        return

                    if event.text:
                        yield event.text

        except ProviderStreamError:
            raise

        except httpx2.HTTPError as exc:
            raise ProviderStreamError(
                "LLM provider unavailable"
            ) from exc
