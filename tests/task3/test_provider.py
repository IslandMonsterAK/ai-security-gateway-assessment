"""Provider-adapter tests for Task 3."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from task3_stream_guardrail.provider import (
    OpenAICompatibleProvider,
)
from task3_stream_guardrail.sse import (
    encode_done,
    encode_openai_delta,
)


@pytest.mark.asyncio
async def test_provider_adapter_decodes_openai_compatible_sse() -> None:
    async def upstream(_: Request) -> StreamingResponse:
        async def generate() -> AsyncIterator[bytes]:
            yield encode_openai_delta("first ")
            yield encode_openai_delta("second")
            yield encode_done()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
        )

    app = Starlette(
        routes=[
            Route(
                "/v1/chat/completions",
                upstream,
                methods=["POST"],
            )
        ]
    )

    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://provider",
    ) as client:
        provider = OpenAICompatibleProvider(
            "http://provider/v1/chat/completions",
            client,
        )

        chunks = [
            chunk
            async for chunk in provider.stream_text(
                {
                    "model": "synthetic",
                    "stream": False,
                }
            )
        ]

    assert chunks == ["first ", "second"]
