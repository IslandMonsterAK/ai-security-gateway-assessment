"""Synthetic primary and secondary providers for Task 4 runtime validation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
)
from starlette.routing import Route


async def _payload(
    request: Request,
) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return None

    if not isinstance(value, dict):
        return None

    return value


async def primary(
    request: Request,
):
    """Synthetic primary model endpoint."""

    payload = await _payload(
        request
    )

    if payload is None:
        return JSONResponse(
            {"error": "invalid request"},
            status_code=400,
        )

    scenario = payload.get(
        "scenario",
        "primary_ok",
    )

    if scenario == "primary_429":
        return JSONResponse(
            {
                "error": (
                    "synthetic primary capacity limit"
                )
            },
            status_code=429,
        )

    if scenario == "primary_slow":
        # Intentionally exceeds the gateway's 3.0 second primary deadline.
        await asyncio.sleep(
            3.2
        )

    if scenario == "primary_500":
        return PlainTextResponse(
            "synthetic-internal-primary-detail",
            status_code=500,
        )

    return JSONResponse(
        {
            "provider": "primary",
            "result": "synthetic success",
        }
    )


async def secondary(
    request: Request,
):
    """Synthetic fallback model endpoint."""

    payload = await _payload(
        request
    )

    if payload is None:
        return JSONResponse(
            {"error": "invalid request"},
            status_code=400,
        )

    scenario = payload.get(
        "scenario",
        "secondary_ok",
    )

    if scenario == "secondary_500":
        return PlainTextResponse(
            "synthetic-internal-secondary-detail",
            status_code=500,
        )

    return JSONResponse(
        {
            "provider": "secondary",
            "result": "synthetic fallback success",
        }
    )


app = Starlette(
    routes=[
        Route(
            "/primary",
            primary,
            methods=["POST"],
        ),
        Route(
            "/secondary",
            secondary,
            methods=["POST"],
        ),
    ]
)


def main() -> None:
    """Run the synthetic Task 4 providers."""

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8021,
    )


if __name__ == "__main__":
    main()
