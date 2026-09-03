import json
import sys

import pytest
from mcp import Client, ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import INVALID_PARAMS, TextContent

from task1_mcp_server.server import server


@pytest.mark.asyncio
async def test_in_memory_client_lists_exactly_two_tools() -> None:
    async with Client(server) as client:
        result = await client.list_tools()
    assert [tool.name for tool in result.tools] == ["get_customer_record", "trigger_refund"]


@pytest.mark.asyncio
async def test_valid_customer_call_returns_record() -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_customer_record", {"customer_id": "CUST-00001"})
    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, TextContent)
    payload = json.loads(content.text)
    assert payload["customer_id"] == "CUST-00001"
    assert payload["status"] == "active"


@pytest.mark.asyncio
async def test_malformed_customer_id_is_jsonrpc_invalid_params() -> None:
    async with Client(server) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool("get_customer_record", {"customer_id": "bad"})
    assert exc_info.value.code == INVALID_PARAMS
    assert exc_info.value.message == "Invalid tool arguments"


@pytest.mark.asyncio
async def test_malformed_refund_is_jsonrpc_invalid_params() -> None:
    async with Client(server) as client:
        with pytest.raises(MCPError) as exc_info:
            await client.call_tool(
                "trigger_refund",
                {"customer_id": "CUST-00001", "amount": 0.0, "reason": "too short"},
            )
    assert exc_info.value.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_missing_customer_is_application_tool_error_not_protocol_error() -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_customer_record", {"customer_id": "CUST-99999"})
    assert result.is_error is True


@pytest.mark.asyncio
async def test_real_stdio_transport_survives_startup_logging() -> None:
    """A real SDK stdio client must parse the server stream successfully.

    The server intentionally logs during startup. If that log is accidentally sent
    to stdout instead of stderr, the SDK client will encounter non-JSON protocol
    data and this test will fail during initialization or tools/list.
    """

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "task1_mcp_server.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()

    assert [tool.name for tool in tools.tools] == ["get_customer_record", "trigger_refund"]
