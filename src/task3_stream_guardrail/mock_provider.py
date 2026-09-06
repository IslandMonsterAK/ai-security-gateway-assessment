"""Synthetic OpenAI-compatible streaming provider for Task 3."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .sse import encode_done, encode_openai_delta

SCENARIOS: dict[str, list[str]] = {
    "safe": [
        "Hello, ",
        "this is a safe ",
        "streaming response.",
    ],
    "mixed": [
        "Contact alice@exa",
        "mple.com, SSN 123-45-",
        "6789, card 4111 1111 ",
        "1111 1111. Done.",
    ],
    "email_end": [
        "The final value is alice@exa",
        "mple.com",
    ],
}


async def chat_completions(request: Request) -> Response:
    """Emit deterministic provider chunks for runtime validation."""

    try:
        payload: Any = await request.json()
    except ValueError:
        return JSONResponse(
            {"error": "invalid request"},
            status_code=400,
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            {"error": "invalid request"},
            status_code=400,
        )

    scenario = payload.get("scenario", "mixed")
    delay_ms = payload.get("delay_ms", 150)

    if scenario not in SCENARIOS:
        return JSONResponse(
            {"error": "unknown scenario"},
            status_code=400,
        )

    if (
        isinstance(delay_ms, bool)
        or not isinstance(delay_ms, (int, float))
        or not 0 <= delay_ms <= 5000
    ):
        return JSONResponse(
            {"error": "invalid delay_ms"},
            status_code=400,
        )

    chunks = SCENARIOS[scenario]

    async def generate() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield encode_openai_delta(chunk)

            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)

        yield encode_done()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


app = Starlette(
    routes=[
        Route(
            "/v1/chat/completions",
            chat_completions,
            methods=["POST"],
        )
    ]
)


def main() -> None:
    """Run the synthetic provider on localhost."""

    uvicorn.run(
        "task3_stream_guardrail.mock_provider:app",
        host="127.0.0.1",
        port=8011,
        reload=False,
    )


if __name__ == "__main__":
    main()
