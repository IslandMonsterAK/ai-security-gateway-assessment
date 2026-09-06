"""Primary/fallback model routing for Task 4."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .provider import (
    ModelProvider,
    ProviderResponse,
    ProviderTransportError,
)

DEFAULT_PRIMARY_TIMEOUT_SECONDS = 3.0
DEFAULT_SECONDARY_TIMEOUT_SECONDS = 10.0


class GatewayRoutingError(RuntimeError):
    """Caller-safe model-routing failure."""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        status_code: int = 502,
    ) -> None:
        super().__init__(public_message)

        self.code = code
        self.public_message = public_message
        self.status_code = status_code


@dataclass(frozen=True)
class RouteResult:
    """Successful provider-routing result."""

    provider: str
    response: ProviderResponse


class ModelRouter:
    """Route requests through a bounded primary/fallback policy.

    Fallback is allowed only when the primary returns HTTP 429 or exceeds the
    configured primary deadline. Other primary failures are normalized to
    caller-safe gateway errors and do not trigger the secondary provider.
    """

    def __init__(
        self,
        primary: ModelProvider,
        secondary: ModelProvider,
        *,
        primary_timeout_seconds: float = (
            DEFAULT_PRIMARY_TIMEOUT_SECONDS
        ),
        secondary_timeout_seconds: float = (
            DEFAULT_SECONDARY_TIMEOUT_SECONDS
        ),
    ) -> None:
        if primary_timeout_seconds <= 0:
            raise ValueError(
                "primary_timeout_seconds must be positive"
            )

        if secondary_timeout_seconds <= 0:
            raise ValueError(
                "secondary_timeout_seconds must be positive"
            )

        self._primary = primary
        self._secondary = secondary
        self._primary_timeout_seconds = (
            primary_timeout_seconds
        )
        self._secondary_timeout_seconds = (
            secondary_timeout_seconds
        )

    @property
    def primary_timeout_seconds(self) -> float:
        """Return the configured primary deadline."""

        return self._primary_timeout_seconds

    async def route(
        self,
        payload: dict[str, Any],
    ) -> RouteResult:
        """Route one generation request."""

        try:
            async with asyncio.timeout(
                self._primary_timeout_seconds
            ):
                primary = await self._primary.generate(
                    payload
                )

        except TimeoutError:
            return await self._fallback(
                payload,
            )

        except ProviderTransportError as exc:
            # The assessment specifically defines primary fallback for 429
            # and deadline expiry. A generic transport failure is therefore
            # normalized rather than silently widening fallback policy.
            raise GatewayRoutingError(
                "primary_provider_unavailable",
                "Primary model provider unavailable",
            ) from exc

        if primary.successful:
            return RouteResult(
                provider="primary",
                response=primary,
            )

        if primary.status_code == 429:
            return await self._fallback(
                payload,
            )

        raise GatewayRoutingError(
            "primary_provider_rejected",
            "Primary model request failed",
        )

    async def _fallback(
        self,
        payload: dict[str, Any],
    ) -> RouteResult:
        """Execute the single permitted secondary-provider attempt."""

        try:
            async with asyncio.timeout(
                self._secondary_timeout_seconds
            ):
                secondary = await self._secondary.generate(
                    payload
                )

        except TimeoutError as exc:
            raise GatewayRoutingError(
                "fallback_provider_unavailable",
                "Fallback model provider unavailable",
            ) from exc

        except ProviderTransportError as exc:
            raise GatewayRoutingError(
                "fallback_provider_unavailable",
                "Fallback model provider unavailable",
            ) from exc

        if not secondary.successful:
            raise GatewayRoutingError(
                "fallback_provider_failed",
                "Fallback model request failed",
            )

        return RouteResult(
            provider="secondary",
            response=secondary,
        )
