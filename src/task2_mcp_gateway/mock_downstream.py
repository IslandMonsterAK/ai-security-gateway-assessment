"""Runnable mock JSON-RPC MCP downstream for Task 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@dataclass
class MockDownstreamState:
    """Observable state used to prove whether forwarding occurred."""

    rpc_calls: int = 0
    methods: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.rpc_calls = 0
        self.methods.clear()


state = MockDownstreamState()


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


async def rpc(request: Request) -> JSONResponse:
    """Handle the tiny subset needed to exercise the proxy."""

    payload = await request.json()
    request_id = payload.get("id")
    method = payload.get("method")

    state.rpc_calls += 1
    state.methods.append(str(method))

    if method == "tools/list":
        return JSONResponse(
            _rpc_result(
                request_id,
                {
                    "tools": [
                        {
                            "name": "get_profile",
                            "description": "Return a synthetic profile.",
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "admin_rotate_key",
                            "description": "Synthetic administrator operation.",
                            "inputSchema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                        },
                    ]
                },
            )
        )

    if method == "tools/call":
        params = payload.get("params", {})
        tool_name = params.get("name")

        return JSONResponse(
            _rpc_result(
                request_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": f"mock downstream executed {tool_name}",
                        }
                    ],
                    "isError": False,
                },
            )
        )

    return JSONResponse(_rpc_error(request_id, -32601, "Method not found"))


async def stats(_: Request) -> JSONResponse:
    """Expose mock-only counters for manual verification."""

    return JSONResponse(
        {
            "rpc_calls": state.rpc_calls,
            "methods": list(state.methods),
        }
    )


async def reset_stats(_: Request) -> JSONResponse:
    state.reset()
    return JSONResponse({"reset": True})


app = Starlette(
    routes=[
        Route("/rpc", rpc, methods=["POST"]),
        Route("/stats", stats, methods=["GET"]),
        Route("/stats/reset", reset_stats, methods=["POST"]),
    ]
)


def main() -> None:
    """Run the mock downstream on localhost only."""

    uvicorn.run(
        "task2_mcp_gateway.mock_downstream:app",
        host="127.0.0.1",
        port=8001,
        reload=False,
    )


if __name__ == "__main__":
    main()
