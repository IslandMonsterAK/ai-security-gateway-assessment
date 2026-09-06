"""Runnable Starlette application for Task 2."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .auth import TokenAuthenticator, token_roles_from_environment
from .proxy import Gateway, HTTPDownstreamForwarder

DEFAULT_DOWNSTREAM_URL = "http://127.0.0.1:8001/rpc"


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    """Own one pooled asynchronous HTTP client for the gateway lifetime."""

    downstream_url = os.getenv(
        "TASK2_DOWNSTREAM_URL",
        DEFAULT_DOWNSTREAM_URL,
    )

    async with httpx2.AsyncClient(timeout=5.0) as client:
        app.state.gateway = Gateway(
            TokenAuthenticator(token_roles_from_environment()),
            HTTPDownstreamForwarder(downstream_url, client),
        )
        yield


async def rpc(request: Request) -> Response:
    """Dispatch the HTTP request through the security gateway."""

    gateway: Gateway = request.app.state.gateway
    return await gateway.handle(request)


app = Starlette(
    lifespan=lifespan,
    routes=[Route("/rpc", rpc, methods=["POST"])],
)


def main() -> None:
    """Run the gateway on localhost only."""

    uvicorn.run(
        "task2_mcp_gateway.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
