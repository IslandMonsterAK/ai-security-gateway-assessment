"""Task 2 authentication and authorization tests."""

from __future__ import annotations

import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from task2_mcp_gateway.auth import Role, TokenAuthenticator
from task2_mcp_gateway.mock_downstream import state
from task2_mcp_gateway.proxy import Gateway, HTTPDownstreamForwarder

TOKENS = {
    "admin-test-token": Role.ADMIN,
    "viewer-test-token": Role.VIEWER,
}


async def _gateway_app(
    downstream_app: Starlette,
) -> tuple[Starlette, httpx2.AsyncClient]:
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

    return app, downstream_client


async def _post(
    app: Starlette,
    token: str | None,
    payload: dict,
) -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)

    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx2.AsyncClient(
        transport=transport,
        base_url="http://gateway",
    ) as client:
        return await client.post(
            "/rpc",
            headers=headers,
            json=payload,
        )


@pytest.fixture(autouse=True)
def reset_mock_state() -> None:
    state.reset()


@pytest.mark.asyncio
async def test_viewer_tools_list_is_transparently_forwarded() -> None:
    from task2_mcp_gateway.mock_downstream import app as downstream_app

    gateway_app, downstream_client = await _gateway_app(downstream_app)

    try:
        response = await _post(
            gateway_app,
            "viewer-test-token",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )
    finally:
        await downstream_client.aclose()

    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["result"]["tools"]
    assert state.rpc_calls == 1


@pytest.mark.asyncio
async def test_viewer_normal_tool_call_is_forwarded() -> None:
    from task2_mcp_gateway.mock_downstream import app as downstream_app

    gateway_app, downstream_client = await _gateway_app(downstream_app)

    try:
        response = await _post(
            gateway_app,
            "viewer-test-token",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "get_profile",
                    "arguments": {},
                },
            },
        )
    finally:
        await downstream_client.aclose()

    assert response.status_code == 200
    assert "result" in response.json()
    assert state.rpc_calls == 1


@pytest.mark.asyncio
async def test_viewer_admin_tool_is_denied_without_downstream_call() -> None:
    from task2_mcp_gateway.mock_downstream import app as downstream_app

    gateway_app, downstream_client = await _gateway_app(downstream_app)

    try:
        response = await _post(
            gateway_app,
            "viewer-test-token",
            {
                "jsonrpc": "2.0",
                "id": 73,
                "method": "tools/call",
                "params": {
                    "name": "admin_rotate_key",
                    "arguments": {},
                },
            },
        )
    finally:
        await downstream_client.aclose()

    body = response.json()

    assert response.status_code == 200
    assert body == {
        "jsonrpc": "2.0",
        "id": 73,
        "error": {
            "code": -32001,
            "message": "Unauthorized Tool Call",
        },
    }

    # This is the central security invariant for Task 2.
    assert state.rpc_calls == 0
    assert state.methods == []


@pytest.mark.asyncio
async def test_admin_admin_tool_is_forwarded() -> None:
    from task2_mcp_gateway.mock_downstream import app as downstream_app

    gateway_app, downstream_client = await _gateway_app(downstream_app)

    try:
        response = await _post(
            gateway_app,
            "admin-test-token",
            {
                "jsonrpc": "2.0",
                "id": 74,
                "method": "tools/call",
                "params": {
                    "name": "admin_rotate_key",
                    "arguments": {},
                },
            },
        )
    finally:
        await downstream_client.aclose()

    assert response.status_code == 200
    assert "result" in response.json()
    assert state.rpc_calls == 1


@pytest.mark.asyncio
async def test_missing_token_fails_closed_without_downstream_call() -> None:
    from task2_mcp_gateway.mock_downstream import app as downstream_app

    gateway_app, downstream_client = await _gateway_app(downstream_app)

    try:
        response = await _post(
            gateway_app,
            None,
            {
                "jsonrpc": "2.0",
                "id": 75,
                "method": "tools/list",
                "params": {},
            },
        )
    finally:
        await downstream_client.aclose()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == -32000
    assert state.rpc_calls == 0
