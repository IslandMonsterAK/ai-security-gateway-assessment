"""End-to-end ASGI composition tests for Task 4."""

from __future__ import annotations

from typing import Any

import httpx2
import pytest

from task4_model_router.app import (
    create_app,
)
from task4_model_router.limiter import (
    SQLiteSlidingWindowLimiter,
)
from task4_model_router.provider import (
    ProviderResponse,
)
from task4_model_router.router import (
    ModelRouter,
)
from task4_model_router.token_budget import (
    RequestTokenBudgeter,
)


class FakeProvider:
    """Provider with observable call count for gateway tests."""

    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b'{"ok":true}',
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.calls = 0

    async def generate(
        self,
        payload: dict[str, Any],
    ) -> ProviderResponse:
        self.calls += 1

        return ProviderResponse(
            status_code=self.status_code,
            content=self.content,
            content_type="application/json",
        )


async def _test_gateway(
    tmp_path,
    *,
    primary_status: int = 200,
):
    database = (
        tmp_path
        / "task4-test.sqlite3"
    )

    limiter = SQLiteSlidingWindowLimiter(
        database
    )

    await limiter.initialize()

    primary = FakeProvider(
        primary_status,
        b'{"provider":"primary"}',
    )

    secondary = FakeProvider(
        200,
        b'{"provider":"secondary"}',
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    app = create_app(
        limiter=limiter,
        budgeter=RequestTokenBudgeter(),
        router=router,
    )

    transport = httpx2.ASGITransport(
        app=app
    )

    client = httpx2.AsyncClient(
        transport=transport,
        base_url="http://test",
    )

    return (
        client,
        limiter,
        primary,
        secondary,
        database,
    )


@pytest.mark.asyncio
async def test_missing_tenant_key_is_rejected_before_provider(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "prompt": "hello",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 401

    assert response.json() == {
        "error": {
            "code": "invalid_api_key",
            "message": (
                "Bearer tenant API key required"
            ),
        }
    }

    assert primary.calls == 0
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_success_is_returned(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    "Bearer tenant-alpha"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "primary"
    }

    assert (
        response.headers[
            "x-model-provider"
        ]
        == "primary"
    )

    assert primary.calls == 1
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_429_falls_back_through_http_gateway(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path,
        primary_status=429,
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    "Bearer tenant-alpha"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 200

    assert response.json() == {
        "provider": "secondary"
    }

    assert (
        response.headers[
            "x-model-provider"
        ]
        == "secondary"
    )

    assert primary.calls == 1
    assert secondary.calls == 1


@pytest.mark.asyncio
async def test_rate_limit_denial_does_not_reach_provider(
    tmp_path,
) -> None:
    (
        client,
        limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path
    )

    key = "tenant-rate-limited"

    baseline = await limiter.reserve(
        key,
        49_900,
    )

    assert baseline.allowed is True

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    f"Bearer {key}"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": 1_000,
            },
        )

    assert response.status_code == 429

    assert response.json() == {
        "error": {
            "code": (
                "tenant_rate_limit_exceeded"
            ),
            "message": (
                "Tenant token budget exceeded"
            ),
        }
    }

    assert primary.calls == 0
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_invalid_token_budget_does_not_reach_provider(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    "Bearer tenant-alpha"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": "100",
            },
        )

    assert response.status_code == 400
    assert (
        response.json()["error"]["code"]
        == "invalid_request"
    )

    assert primary.calls == 0
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_failure_body_is_not_exposed(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path,
        primary_status=500,
    )

    primary.content = (
        b"password=synthetic-secret"
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    "Bearer tenant-alpha"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 502

    body = response.text

    assert "synthetic-secret" not in body
    assert "password" not in body

    assert (
        response.json()["error"]["code"]
        == "primary_provider_rejected"
    )

    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_raw_tenant_key_is_not_persisted(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        _primary,
        _secondary,
        database,
    ) = await _test_gateway(
        tmp_path
    )

    raw_key = (
        "synthetic-raw-tenant-secret"
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    f"Bearer {raw_key}"
                )
            },
            json={
                "prompt": "hello",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 200

    database_files = list(
        database.parent.glob(
            f"{database.name}*"
        )
    )

    assert database_files

    for database_file in database_files:
        assert (
            raw_key.encode()
            not in database_file.read_bytes()
        )


@pytest.mark.asyncio
async def test_non_object_json_is_rejected_before_provider(
    tmp_path,
) -> None:
    (
        client,
        _limiter,
        primary,
        secondary,
        _database,
    ) = await _test_gateway(
        tmp_path
    )

    async with client:
        response = await client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": (
                    "Bearer tenant-alpha"
                )
            },
            json=[
                "not",
                "an",
                "object",
            ],
        )

    assert response.status_code == 400

    assert primary.calls == 0
    assert secondary.calls == 0
