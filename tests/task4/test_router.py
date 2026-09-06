"""Fallback-policy and timeout tests for Task 4."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from task4_model_router.provider import (
    ProviderResponse,
    ProviderTransportError,
)
from task4_model_router.router import (
    DEFAULT_PRIMARY_TIMEOUT_SECONDS,
    GatewayRoutingError,
    ModelRouter,
)


class FakeProvider:
    """Deterministic provider for routing tests."""

    def __init__(
        self,
        *,
        response: ProviderResponse | None = None,
        delay_seconds: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.delay_seconds = delay_seconds
        self.error = error

        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    async def generate(
        self,
        payload: dict[str, Any],
    ) -> ProviderResponse:
        self.calls += 1
        self.payloads.append(dict(payload))

        if self.delay_seconds:
            await asyncio.sleep(
                self.delay_seconds
            )

        if self.error is not None:
            raise self.error

        assert self.response is not None

        return self.response


def _response(
    status_code: int,
    body: bytes = b'{"ok":true}',
) -> ProviderResponse:
    return ProviderResponse(
        status_code=status_code,
        content=body,
        content_type="application/json",
    )


def test_default_primary_deadline_is_exactly_three_seconds() -> None:
    primary = FakeProvider(
        response=_response(200)
    )

    secondary = FakeProvider(
        response=_response(200)
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    assert (
        router.primary_timeout_seconds
        == DEFAULT_PRIMARY_TIMEOUT_SECONDS
        == 3.0
    )


@pytest.mark.asyncio
async def test_primary_success_does_not_call_secondary() -> None:
    primary = FakeProvider(
        response=_response(
            200,
            b'{"provider":"primary"}',
        )
    )

    secondary = FakeProvider(
        response=_response(
            200,
            b'{"provider":"secondary"}',
        )
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    result = await router.route(
        {"prompt": "hello"}
    )

    assert result.provider == "primary"
    assert (
        result.response.content
        == b'{"provider":"primary"}'
    )

    assert primary.calls == 1
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_429_routes_to_secondary() -> None:
    primary = FakeProvider(
        response=_response(
            429,
            b'{"internal":"primary quota"}',
        )
    )

    secondary = FakeProvider(
        response=_response(
            200,
            b'{"provider":"secondary"}',
        )
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    payload = {
        "model": "synthetic",
        "prompt": "hello",
    }

    result = await router.route(payload)

    assert result.provider == "secondary"
    assert primary.calls == 1
    assert secondary.calls == 1
    assert secondary.payloads == [payload]


@pytest.mark.asyncio
async def test_primary_timeout_routes_to_secondary() -> None:
    primary = FakeProvider(
        response=_response(200),
        delay_seconds=0.05,
    )

    secondary = FakeProvider(
        response=_response(
            200,
            b'{"provider":"secondary"}',
        )
    )

    router = ModelRouter(
        primary,
        secondary,
        primary_timeout_seconds=0.01,
    )

    result = await router.route(
        {"prompt": "hello"}
    )

    assert result.provider == "secondary"
    assert primary.calls == 1
    assert secondary.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "primary_status",
    [
        400,
        401,
        403,
        500,
    ],
)
async def test_non_429_primary_failure_does_not_fallback(
    primary_status: int,
) -> None:
    primary = FakeProvider(
        response=_response(
            primary_status,
            b"secret upstream diagnostic",
        )
    )

    secondary = FakeProvider(
        response=_response(200)
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    with pytest.raises(
        GatewayRoutingError,
    ) as captured:
        await router.route(
            {"prompt": "hello"}
        )

    assert (
        captured.value.code
        == "primary_provider_rejected"
    )

    assert (
        str(captured.value)
        == "Primary model request failed"
    )

    assert "secret" not in str(
        captured.value
    )

    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_primary_transport_failure_does_not_fallback() -> None:
    primary = FakeProvider(
        error=ProviderTransportError(
            "connect failed to secret.internal:9443"
        )
    )

    secondary = FakeProvider(
        response=_response(200)
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    with pytest.raises(
        GatewayRoutingError,
    ) as captured:
        await router.route(
            {"prompt": "hello"}
        )

    assert (
        captured.value.code
        == "primary_provider_unavailable"
    )

    assert "secret.internal" not in str(
        captured.value
    )

    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_secondary_failure_is_sanitized() -> None:
    primary = FakeProvider(
        response=_response(429)
    )

    secondary = FakeProvider(
        error=ProviderTransportError(
            "secondary secret.internal:9555 failed"
        )
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    with pytest.raises(
        GatewayRoutingError,
    ) as captured:
        await router.route(
            {"prompt": "hello"}
        )

    assert (
        captured.value.code
        == "fallback_provider_unavailable"
    )

    public = str(captured.value)

    assert (
        public
        == "Fallback model provider unavailable"
    )

    assert "secret.internal" not in public


@pytest.mark.asyncio
async def test_secondary_timeout_is_sanitized() -> None:
    primary = FakeProvider(
        response=_response(429)
    )

    secondary = FakeProvider(
        response=_response(200),
        delay_seconds=0.05,
    )

    router = ModelRouter(
        primary,
        secondary,
        secondary_timeout_seconds=0.01,
    )

    with pytest.raises(
        GatewayRoutingError,
    ) as captured:
        await router.route(
            {"prompt": "hello"}
        )

    assert (
        captured.value.code
        == "fallback_provider_unavailable"
    )

    assert (
        str(captured.value)
        == "Fallback model provider unavailable"
    )


@pytest.mark.asyncio
async def test_secondary_non_success_body_is_not_exposed() -> None:
    primary = FakeProvider(
        response=_response(429)
    )

    secondary = FakeProvider(
        response=_response(
            500,
            b"database password=do-not-expose",
        )
    )

    router = ModelRouter(
        primary,
        secondary,
    )

    with pytest.raises(
        GatewayRoutingError,
    ) as captured:
        await router.route(
            {"prompt": "hello"}
        )

    public = str(captured.value)

    assert (
        captured.value.code
        == "fallback_provider_failed"
    )

    assert (
        public
        == "Fallback model request failed"
    )

    assert "password" not in public
    assert "do-not-expose" not in public
