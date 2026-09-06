"""Runnable Task 3 streaming LLM guardrail gateway."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .provider import OpenAICompatibleProvider, TextStreamProvider
from .stream import guarded_sse_stream

DEFAULT_PROVIDER_URL = (
    "http://127.0.0.1:8011/v1/chat/completions"
)


async def chat_completions(request: Request) -> Response:
    """Proxy one streaming text-generation request through the guardrail."""

    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(
            {
                "error": {
                    "message": "Invalid JSON request",
                    "type": "invalid_request",
                }
            },
            status_code=400,
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            {
                "error": {
                    "message": "Request body must be a JSON object",
                    "type": "invalid_request",
                }
            },
            status_code=400,
        )

    payload = dict(payload)
    payload["stream"] = True

    provider: TextStreamProvider = request.app.state.provider

    return StreamingResponse(
        guarded_sse_stream(provider, payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def create_app(
    provider: TextStreamProvider | None = None,
) -> Starlette:
    """Create the runtime or injected-test Task 3 application."""

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if provider is not None:
            app.state.provider = provider
            yield
        else:
            provider_url = os.getenv(
                "TASK3_PROVIDER_URL",
                DEFAULT_PROVIDER_URL,
            )

            async with httpx2.AsyncClient(
                timeout=30.0
            ) as client:
                app.state.provider = OpenAICompatibleProvider(
                    provider_url,
                    client,
                )
                yield

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route(
                "/v1/chat/completions",
                chat_completions,
                methods=["POST"],
            )
        ],
    )


app = create_app()


def main() -> None:
    """Run the guardrail gateway on localhost."""

    uvicorn.run(
        "task3_stream_guardrail.app:app",
        host="127.0.0.1",
        port=8010,
        reload=False,
    )


if __name__ == "__main__":
    main()
