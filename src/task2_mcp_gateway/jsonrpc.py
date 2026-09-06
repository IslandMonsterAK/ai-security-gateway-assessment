"""Small JSON-RPC 2.0 parsing helpers for the Task 2 gateway."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INVALID_PARAMS = -32602

AUTHENTICATION_REQUIRED = -32000
UNAUTHORIZED_TOOL_CALL = -32001
DOWNSTREAM_UNAVAILABLE = -32002

JsonRpcId = str | int | None


@dataclass(frozen=True)
class JsonRpcProblem(Exception):
    """A JSON-RPC problem safe to return to the caller."""

    code: int
    message: str
    request_id: JsonRpcId = None


def error_response(
    request_id: JsonRpcId,
    code: int,
    message: str,
) -> dict[str, Any]:
    """Build a standard JSON-RPC 2.0 error response."""

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _validated_id(payload: dict[str, Any]) -> JsonRpcId:
    request_id = payload.get("id")

    if request_id is None:
        return None

    # bool is a subclass of int in Python but is not accepted as an RPC id here.
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise JsonRpcProblem(INVALID_REQUEST, "Invalid Request")

    return request_id


def parse_request(body: bytes) -> dict[str, Any]:
    """Parse and minimally validate one JSON-RPC 2.0 request."""

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JsonRpcProblem(PARSE_ERROR, "Parse error") from exc

    # Batch requests are intentionally outside this assessment's scope.
    if not isinstance(payload, dict):
        raise JsonRpcProblem(INVALID_REQUEST, "Invalid Request")

    request_id = _validated_id(payload)

    if payload.get("jsonrpc") != "2.0":
        raise JsonRpcProblem(INVALID_REQUEST, "Invalid Request", request_id)

    method = payload.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcProblem(INVALID_REQUEST, "Invalid Request", request_id)

    return payload


def request_id(payload: dict[str, Any]) -> JsonRpcId:
    """Return a previously validated request id."""

    return payload.get("id")


def called_tool_name(payload: dict[str, Any]) -> str | None:
    """Return the tools/call name, validating only fields needed by policy."""

    if payload["method"] != "tools/call":
        return None

    params = payload.get("params")
    rpc_id = request_id(payload)

    if not isinstance(params, dict):
        raise JsonRpcProblem(INVALID_PARAMS, "Invalid params", rpc_id)

    name = params.get("name")

    if not isinstance(name, str) or not name:
        raise JsonRpcProblem(INVALID_PARAMS, "Invalid params", rpc_id)

    return name


def requires_admin(tool_name: str) -> bool:
    """Return True when Task 2 policy protects this tool."""

    return tool_name.startswith("admin_")
