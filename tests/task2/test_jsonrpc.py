"""Focused JSON-RPC boundary tests for Task 2."""

import pytest

from task2_mcp_gateway.jsonrpc import (
    INVALID_REQUEST,
    JsonRpcProblem,
    parse_request,
)


def test_batch_request_is_rejected_as_out_of_scope() -> None:
    with pytest.raises(JsonRpcProblem) as exc_info:
        parse_request(
            b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]'
        )

    assert exc_info.value.code == INVALID_REQUEST


def test_boolean_request_id_is_rejected() -> None:
    with pytest.raises(JsonRpcProblem) as exc_info:
        parse_request(
            b'{"jsonrpc":"2.0","id":true,"method":"tools/list"}'
        )

    assert exc_info.value.code == INVALID_REQUEST
