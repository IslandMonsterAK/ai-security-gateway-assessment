"""Task 2 gateway edge cases and failure-path tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from task2_mcp_gateway.auth import Role, TokenAuthenticator
from task2_mcp_gateway.mock_downstream import app as downstream_app
from task2_mcp_gateway.mock_downstream import state
from task2_mcp_gateway.proxy import (
    DownstreamResponse,
    Gateway,
    HTTPDownstreamForwarder,
)

TOKENS = {
    "admin-test-token": Role.ADMIN,
    "viewer-test-token": Role.VIEWER,
}


@pytest.fixture(autouse=True)
def reset_mock_state() -> None:
    state.reset()


@pytest.fixture
async def gateway_app() -> AsyncIterator[Starlette]:
    """Create a gateway connected to the real mock downstream ASGI app."""

    downstream_transport = httpx2.ASGITransport(app=downstream_app)

    downstream_client = httpx2.AsyncClient(
        transport=downstream_transport,
        base_url="http://downstream",
    )

    gateway = Gateway(
        TokenAuthenticator(TOKENS),
        HTTPDownstreamForwarder(
            "http://downstream/rpc",
            downstream_client,
        ),
    )

    async def rpc(request: Request) -> Response:
        return await gateway.handle(request)

    app = Starlette(routes=[Route("/rpc", rpc, methods=["POST"])])

    try:
        yield app
    finally:
        await downstream_client.aclose()


async def _post_json(
    app: Starlette,
    authorization: str | None,
    payload: dict[str, Any],
) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)

    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization

    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://gateway",
    ) as client:
        return await client.post(
            "/rpc",
            headers=headers,
            json=payload,
        )


async def _post_raw(
    app: Starlette,
    authorization: str | None,
    body: bytes,
) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)

    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization

    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://gateway",
    ) as client:
        return await client.post(
            "/rpc",
            headers=headers,
            content=body,
        )


@pytest.mark.asyncio
async def test_unknown_bearer_token_fails_closed(
    gateway_app: Starlette,
) -> None:
    response = await _post_json(
        gateway_app,
        "Bearer forged-token",
        {
            "jsonrpc": "2.0",
            "id": 80,
            "method": "tools/list",
            "params": {},
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32000
    assert state.rpc_calls == 0


@pytest.mark.asyncio
async def test_wrong_authorization_scheme_fails_closed(
    gateway_app: Starlette,
) -> None:
    response = await _post_json(
        gateway_app,
        "Basic viewer-test-token",
        {
            "jsonrpc": "2.0",
            "id": 81,
            "method": "tools/list",
            "params": {},
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32000
    assert state.rpc_calls == 0


@pytest.mark.asyncio
async def test_malformed_json_is_parse_error_without_forwarding(
    gateway_app: Starlette,
) -> None:
    response = await _post_raw(
        gateway_app,
        "Bearer viewer-test-token",
        b'{"jsonrpc":"2.0","id":82,',
    )

    body = response.json()

    assert response.status_code == 200
    assert body["id"] is None
    assert body["error"]["code"] == -32700
    assert body["error"]["message"] == "Parse error"
    assert state.rpc_calls == 0


@pytest.mark.asyncio
async def test_invalid_jsonrpc_version_is_rejected_without_forwarding(
    gateway_app: Starlette,
) -> None:
    response = await _post_json(
        gateway_app,
        "Bearer viewer-test-token",
        {
            "jsonrpc": "1.0",
            "id": 83,
            "method": "tools/list",
            "params": {},
        },
    )

    body = response.json()

    assert body["id"] == 83
    assert body["error"]["code"] == -32600
    assert state.rpc_calls == 0


@pytest.mark.asyncio
async def test_tools_call_missing_name_is_invalid_params_without_forwarding(
    gateway_app: Starlette,
) -> None:
    response = await _post_json(
        gateway_app,
        "Bearer viewer-test-token",
        {
            "jsonrpc": "2.0",
            "id": 84,
            "method": "tools/call",
            "params": {
                "arguments": {},
            },
        },
    )

    body = response.json()

    assert body["id"] == 84
    assert body["error"]["code"] == -32602
    assert body["error"]["message"] == "Invalid params"
    assert state.rpc_calls == 0


@pytest.mark.asyncio
async def test_uninspected_method_is_forwarded_to_downstream(
    gateway_app: Starlette,
) -> None:
    response = await _post_json(
        gateway_app,
        "Bearer viewer-test-token",
        {
            "jsonrpc": "2.0",
            "id": 85,
            "method": "ping",
            "params": {},
        },
    )

    body = response.json()

    assert body["id"] == 85
    assert body["error"]["code"] == -32601
    assert state.rpc_calls == 1
    assert state.methods == ["ping"]


class FailingForwarder(HTTPDownstreamForwarder):
    """Simulate a transport failure containing private internal detail."""

    def __init__(self) -> None:
        pass

    async def forward(
        self,
        payload: dict[str, Any],
    ) -> DownstreamResponse:
        del payload
        raise httpx2.HTTPError(
            "simulated connection failure to http://secret.internal:9999"
        )


@pytest.mark.asyncio
async def test_downstream_transport_failure_is_sanitized() -> None:
    gateway = Gateway(
        TokenAuthenticator(TOKENS),
        FailingForwarder(),
    )

    async def rpc(request: Request) -> Response:
        return await gateway.handle(request)

    app = Starlette(routes=[Route("/rpc", rpc, methods=["POST"])])

    response = await _post_json(
        app,
        "Bearer viewer-test-token",
        {
            "jsonrpc": "2.0",
            "id": 86,
            "method": "tools/list",
            "params": {},
        },
    )

    body = response.json()

    assert response.status_code == 502
    assert body == {
        "jsonrpc": "2.0",
        "id": 86,
        "error": {
            "code": -32002,
            "message": "Downstream MCP server unavailable",
        },
    }

    assert "secret.internal" not in response.text
    assert "simulated connection failure" not in response.text
