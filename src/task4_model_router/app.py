"""Runnable token-aware rate-limiting and fallback gateway for Task 4."""

from __future__ import annotations

import json
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth import (
    TenantKeyError,
    extract_tenant_api_key,
)
from .limiter import SQLiteSlidingWindowLimiter
from .provider import HTTPModelProvider
from .router import (
    GatewayRoutingError,
    ModelRouter,
)
from .token_budget import (
    RequestTokenBudgeter,
    TokenBudgetError,
)

DEFAULT_DATABASE_PATH = Path(
    "data/task4_rate_limit.sqlite3"
)

DEFAULT_PRIMARY_URL = (
    "http://127.0.0.1:8021/primary"
)

DEFAULT_SECONDARY_URL = (
    "http://127.0.0.1:8021/secondary"
)


def _gateway_error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Return one standardized caller-safe gateway error."""

    return JSONResponse(
        {
            "error": {
                "code": code,
                "message": message,
            }
        },
        status_code=status_code,
        headers=headers,
    )


async def completions(
    request: Request,
) -> Response:
    """Handle one token-budgeted model request."""

    try:
        tenant_key = extract_tenant_api_key(
            request.headers.get(
                "authorization"
            )
        )
    except TenantKeyError:
        return _gateway_error(
            401,
            "invalid_api_key",
            "Bearer tenant API key required",
        )

    try:
        payload: Any = await request.json()
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return _gateway_error(
            400,
            "invalid_request",
            "Request body must contain valid JSON",
        )

    if not isinstance(payload, dict):
        return _gateway_error(
            400,
            "invalid_request",
            "Request body must be a JSON object",
        )

    budgeter: RequestTokenBudgeter = (
        request.app.state.budgeter
    )

    try:
        budget = budgeter.budget(
            payload
        )
    except TokenBudgetError as exc:
        return _gateway_error(
            400,
            "invalid_request",
            str(exc),
        )

    limiter: SQLiteSlidingWindowLimiter = (
        request.app.state.limiter
    )

    try:
        admission = await limiter.reserve(
            tenant_key,
            budget.total_tokens,
        )
    except Exception:
        # Infrastructure details remain server-side. No database path,
        # SQLite exception, or internal stack is returned to the caller.
        return _gateway_error(
            503,
            "rate_limiter_unavailable",
            "Rate limiter temporarily unavailable",
        )

    rate_headers = {
        "X-RateLimit-Limit-Tokens": str(
            limiter.limit_tokens
        ),
        "X-RateLimit-Remaining-Tokens": str(
            admission.remaining_tokens
        ),
    }

    if not admission.allowed:
        if admission.retry_after_seconds is not None:
            rate_headers["Retry-After"] = str(
                max(
                    1,
                    math.ceil(
                        admission.retry_after_seconds
                    ),
                )
            )

        return _gateway_error(
            429,
            "tenant_rate_limit_exceeded",
            "Tenant token budget exceeded",
            headers=rate_headers,
        )

    router: ModelRouter = (
        request.app.state.router
    )

    try:
        routed = await router.route(
            payload
        )
    except GatewayRoutingError as exc:
        return _gateway_error(
            exc.status_code,
            exc.code,
            exc.public_message,
            headers=rate_headers,
        )

    response_headers = dict(
        rate_headers
    )

    # These generic labels provide assessment evidence without exposing
    # upstream hostnames or infrastructure details.
    response_headers["X-Model-Provider"] = (
        routed.provider
    )

    response_headers["Content-Type"] = (
        routed.response.content_type
    )

    return Response(
        content=routed.response.content,
        status_code=routed.response.status_code,
        headers=response_headers,
    )


@asynccontextmanager
async def runtime_lifespan(
    app: Starlette,
):
    """Initialize runtime-only gateway dependencies."""

    database_path = Path(
        os.getenv(
            "TASK4_DB_PATH",
            str(DEFAULT_DATABASE_PATH),
        )
    )

    limiter = SQLiteSlidingWindowLimiter(
        database_path
    )

    await limiter.initialize()

    budgeter = RequestTokenBudgeter()

    primary_url = os.getenv(
        "TASK4_PRIMARY_URL",
        DEFAULT_PRIMARY_URL,
    )

    secondary_url = os.getenv(
        "TASK4_SECONDARY_URL",
        DEFAULT_SECONDARY_URL,
    )

    async with httpx2.AsyncClient(
        timeout=30.0
    ) as client:
        app.state.limiter = limiter
        app.state.budgeter = budgeter

        app.state.router = ModelRouter(
            HTTPModelProvider(
                primary_url,
                client,
            ),
            HTTPModelProvider(
                secondary_url,
                client,
            ),
        )

        yield


def create_app(
    *,
    limiter: SQLiteSlidingWindowLimiter | None = None,
    budgeter: RequestTokenBudgeter | None = None,
    router: ModelRouter | None = None,
) -> Starlette:
    """Create the gateway with runtime or injected dependencies."""

    supplied = [
        limiter is not None,
        budgeter is not None,
        router is not None,
    ]

    if any(supplied) and not all(supplied):
        raise ValueError(
            "Injected gateway dependencies must be provided together"
        )

    if all(supplied):
        application = Starlette(
            routes=[
                Route(
                    "/v1/chat/completions",
                    completions,
                    methods=["POST"],
                )
            ]
        )

        application.state.limiter = limiter
        application.state.budgeter = budgeter
        application.state.router = router

        return application

    return Starlette(
        routes=[
            Route(
                "/v1/chat/completions",
                completions,
                methods=["POST"],
            )
        ],
        lifespan=runtime_lifespan,
    )


app = create_app()


def main() -> None:
    """Run the Task 4 model gateway."""

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8020,
    )


if __name__ == "__main__":
    main()
