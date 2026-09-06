"""Authorization and HTTP forwarding for Task 2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx2
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .auth import AuthenticationError, Role, TokenAuthenticator
from .jsonrpc import (
    AUTHENTICATION_REQUIRED,
    DOWNSTREAM_UNAVAILABLE,
    UNAUTHORIZED_TOOL_CALL,
    JsonRpcProblem,
    called_tool_name,
    error_response,
    parse_request,
    request_id,
    requires_admin,
)


@dataclass(frozen=True)
class DownstreamResponse:
    """Transport-neutral representation of the downstream HTTP response."""

    status_code: int
    content: bytes
    content_type: str


class HTTPDownstreamForwarder:
    """Forward authorized JSON-RPC requests to the configured downstream."""

    def __init__(self, url: str, client: httpx2.AsyncClient) -> None:
        self._url = url
        self._client = client

    async def forward(self, payload: dict[str, Any]) -> DownstreamResponse:
        response = await self._client.post(self._url, json=payload)

        return DownstreamResponse(
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get(
                "content-type",
                "application/json",
            ),
        )


class Gateway:
    """Task 2 authorization gateway."""

    def __init__(
        self,
        authenticator: TokenAuthenticator,
        forwarder: HTTPDownstreamForwarder,
    ) -> None:
        self._authenticator = authenticator
        self._forwarder = forwarder

    async def handle(self, request: Request) -> Response:
        """Authenticate, authorize, then forward one JSON-RPC request."""

        body = await request.body()

        try:
            payload = parse_request(body)
        except JsonRpcProblem as exc:
            return JSONResponse(
                error_response(exc.request_id, exc.code, exc.message)
            )

        rpc_id = request_id(payload)

        try:
            role = self._authenticator.authenticate(
                request.headers.get("authorization")
            )
        except AuthenticationError:
            return JSONResponse(
                error_response(
                    rpc_id,
                    AUTHENTICATION_REQUIRED,
                    "Authentication Required",
                ),
                status_code=401,
            )

        try:
            tool_name = called_tool_name(payload)
        except JsonRpcProblem as exc:
            return JSONResponse(
                error_response(exc.request_id, exc.code, exc.message)
            )

        if (
            tool_name is not None
            and requires_admin(tool_name)
            and role is not Role.ADMIN
        ):
            return JSONResponse(
                error_response(
                    rpc_id,
                    UNAUTHORIZED_TOOL_CALL,
                    "Unauthorized Tool Call",
                )
            )

        try:
            downstream = await self._forwarder.forward(payload)
        except httpx2.HTTPError:
            return JSONResponse(
                error_response(
                    rpc_id,
                    DOWNSTREAM_UNAVAILABLE,
                    "Downstream MCP server unavailable",
                ),
                status_code=502,
            )

        return Response(
            content=downstream.content,
            status_code=downstream.status_code,
            headers={"content-type": downstream.content_type},
        )
