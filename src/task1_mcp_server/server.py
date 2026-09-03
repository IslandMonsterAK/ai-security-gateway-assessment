"""Task 1: strict MCP server over stdio.

Security invariant: stdout belongs exclusively to the MCP stdio transport.
Application diagnostics are emitted through logging, which is explicitly bound
to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TypeVar

from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import BaseModel, ValidationError

from .models import GetCustomerRecordInput, TriggerRefundInput
from .store import create_refund, get_customer

LOGGER = logging.getLogger("task1_mcp_server")
ModelT = TypeVar("ModelT", bound=BaseModel)

GET_CUSTOMER_RECORD = Tool(
    name="get_customer_record",
    description="Return a synthetic customer record by customer ID.",
    input_schema=GetCustomerRecordInput.model_json_schema(),
)

TRIGGER_REFUND = Tool(
    name="trigger_refund",
    description="Create a synthetic refund receipt for an existing customer.",
    input_schema=TriggerRefundInput.model_json_schema(),
)


def configure_logging() -> None:
    """Configure diagnostics for stderr only.

    Never add a stdout handler to a stdio MCP server: stdout is the protocol
    channel and stray text can corrupt JSON-RPC framing.
    """

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def _validate(model: type[ModelT], arguments: dict[str, Any] | None) -> ModelT:
    """Validate a tools/call argument object or raise JSON-RPC Invalid params."""

    try:
        return model.model_validate(arguments or {})
    except ValidationError as exc:
        # Pydantic's normalized errors are useful to the caller, but exclude the
        # original input values so malformed or sensitive values are not echoed.
        details = [
            {
                "location": list(error["loc"]),
                "type": error["type"],
                "message": error["msg"],
            }
            for error in exc.errors(include_input=False, include_url=False)
        ]
        raise MCPError(INVALID_PARAMS, "Invalid tool arguments", data={"errors": details}) from exc


def _json_result(payload: BaseModel) -> CallToolResult:
    """Return deterministic JSON as normal MCP text content."""

    text = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)


def _tool_error(message: str) -> CallToolResult:
    """Return an application-level tool error after a syntactically valid call."""

    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


async def list_tools(
    ctx: ServerRequestContext, params: PaginatedRequestParams | None
) -> ListToolsResult:
    """Advertise the two assessment tools and their strict JSON Schemas."""

    del ctx, params
    return ListToolsResult(tools=[GET_CUSTOMER_RECORD, TRIGGER_REFUND])


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    """Validate, dispatch, and execute a tool call."""

    del ctx

    if params.name == "get_customer_record":
        request = _validate(GetCustomerRecordInput, params.arguments)
        record = get_customer(request.customer_id)
        if record is None:
            return _tool_error("Customer record not found")
        LOGGER.info("get_customer_record completed for %s", request.customer_id)
        return _json_result(record)

    if params.name == "trigger_refund":
        request = _validate(TriggerRefundInput, params.arguments)
        if get_customer(request.customer_id) is None:
            return _tool_error("Refund not created: customer record not found")
        receipt = create_refund(request.customer_id, request.amount, request.reason)
        LOGGER.info("trigger_refund accepted for %s", request.customer_id)
        return _json_result(receipt)

    raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}")


server = Server(
    "ai-security-gateway-task1",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


async def run_stdio() -> None:
    """Run one MCP connection over stdin/stdout."""

    configure_logging()
    LOGGER.info("Task 1 MCP server starting on stdio")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Console entry point."""

    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
