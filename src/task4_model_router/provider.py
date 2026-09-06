"""Model-provider abstraction for the Task 4 routing gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx2


class ProviderTransportError(RuntimeError):
    """Internal normalized provider transport failure."""


@dataclass(frozen=True)
class ProviderResponse:
    """Provider response needed by the routing layer."""

    status_code: int
    content: bytes
    content_type: str = "application/json"

    @property
    def successful(self) -> bool:
        """Return whether the provider returned a 2xx response."""

        return 200 <= self.status_code < 300


class ModelProvider(Protocol):
    """Minimal asynchronous model-provider contract."""

    async def generate(
        self,
        payload: dict[str, Any],
    ) -> ProviderResponse:
        """Send one generation request."""


class HTTPModelProvider:
    """HTTP implementation of the model-provider contract."""

    def __init__(
        self,
        url: str,
        client: httpx2.AsyncClient,
    ) -> None:
        self._url = url
        self._client = client

    async def generate(
        self,
        payload: dict[str, Any],
    ) -> ProviderResponse:
        """Send a model request without exposing transport details."""

        try:
            response = await self._client.post(
                self._url,
                json=payload,
            )
        except httpx2.HTTPError as exc:
            raise ProviderTransportError(
                "Provider transport failure"
            ) from exc

        return ProviderResponse(
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get(
                "content-type",
                "application/json",
            ),
        )
