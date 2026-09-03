# Task 1 Walkthrough - Strict MCP Server over stdio

## What the task is testing

Task 1 is not primarily about customer data or refunds. It tests whether the server honors the MCP/JSON-RPC boundary correctly:

1. Advertise exactly two tools.
2. Express strict input contracts.
3. Reject malformed tool arguments as protocol-level `Invalid params` errors (`-32602`).
4. Run over stdio without contaminating stdout with logs or debug text.
5. Distinguish a malformed request from a valid request that fails at the application layer.

## Why the implementation uses the low-level MCP `Server`

The official Python SDK provides both a high-level `MCPServer` API and a low-level `Server` API. The high-level API derives schemas and performs validation automatically. The low-level API exposes the actual MCP protocol objects and lets the application control validation and JSON-RPC error mapping explicitly.

For this assessment, the low-level API is intentional because the requirement specifically calls for strict validation and standard JSON-RPC error codes. The server advertises Pydantic-generated JSON Schemas, then applies the same Pydantic models inside `tools/call`. A validation failure is translated to `MCPError(INVALID_PARAMS, ...)`, which is JSON-RPC `-32602`.

This avoids relying on undocumented convenience-layer behavior and makes the protocol decision visible in the code and tests.

## Data flow

```text
MCP client
   |
   | JSON-RPC over stdin/stdout
   v
stdio transport
   |
   v
MCP Server
   |
   +-- tools/list --------------------> two Tool definitions
   |
   +-- tools/call
          |
          v
     route by tool name
          |
          v
     Pydantic validation
       /          \
      /            \
 invalid           valid
    |                |
    v                v
 -32602          application logic
                     |
              +------+------+
              |             |
          successful    valid call but
          result        record missing
              |             |
              v             v
          isError=false  isError=true
```

## Trust boundary

Everything arriving in `params.arguments` is untrusted input.

The handler does not use a field until it has passed the corresponding Pydantic model. Models use:

- `strict=True` - prevents convenient type coercion such as the string `"12.5"` becoming a float.
- `extra="forbid"` - rejects undeclared fields.
- `str_strip_whitespace=True` - applies string length constraints after surrounding whitespace is removed.
- a fixed customer ID pattern: `^CUST-[0-9]{5}$`.
- `amount > 0`.
- refund reason length from 10 to 500 characters.

## Protocol error vs. tool error

This is an important distinction.

### Protocol error

Input:

```json
{"customer_id":"bad"}
```

The request violates the advertised tool contract. The server raises:

```text
JSON-RPC -32602 Invalid params
```

No tool result is produced.

### Application/tool error

Input:

```json
{"customer_id":"CUST-99999"}
```

The input is structurally valid. The customer simply does not exist in the synthetic store. That is returned as a normal MCP tool result with `isError=true` rather than misclassifying it as malformed JSON-RPC.

## Why stdout is treated as a security/protocol boundary

With MCP stdio, stdout is not a console. It is the protocol transport.

A line such as:

```python
print("server started")
```

can be consumed by the MCP client as if it were protocol data and break the session.

For that reason, application logging is explicitly configured with a `StreamHandler(sys.stderr)`. The real-stdio integration test intentionally starts the server while startup logging is enabled and connects with the official SDK client. If the startup message were accidentally emitted to stdout, initialization or `tools/list` would fail.

## Synthetic side effects

`trigger_refund` does not contact a payment provider and does not modify external state. It creates a deterministic synthetic receipt. The assessment is testing protocol handling, not payment integration, and introducing a real side effect would add risk without proving anything relevant.

The deterministic refund ID also makes tests reproducible.

## Files

- `src/task1_mcp_server/models.py` - strict request and response models.
- `src/task1_mcp_server/store.py` - synthetic customer records and mock refund receipt creation.
- `src/task1_mcp_server/server.py` - MCP tools, validation, JSON-RPC mapping, and stdio runner.
- `tests/task1/test_models.py` - validation edge cases.
- `tests/task1/test_protocol.py` - MCP protocol behavior and real stdio integration.

## What to say in an interview

A concise explanation:

> I treated stdio and `tools/call` as trust boundaries. The tools advertise schemas generated from strict Pydantic models, but because I wanted explicit control of the required JSON-RPC behavior, I used the official SDK's low-level Server and apply those models myself when a call arrives. Schema violations become `-32602 Invalid params`; a valid call that cannot be fulfilled becomes an MCP tool error instead. I also bind all application logging to stderr because stdout belongs exclusively to the stdio protocol. The tests cover both validation behavior and an actual SDK stdio connection so a stray stdout log would break CI rather than silently ship.

## Likely follow-up questions

### Why low-level `Server` rather than `MCPServer`?

Because this assessment specifically evaluates protocol compliance and JSON-RPC error mapping. The low-level server makes the wire-level behavior explicit. I still use Pydantic to avoid hand-writing duplicate validation logic.

### Why not trust the JSON Schema advertised in `tools/list`?

The low-level SDK advertises that schema but does not automatically apply it to `tools/call`. The server therefore validates the actual arguments itself using the same model that generated the advertised schema.

### Why is a missing customer not `-32602`?

Because `CUST-99999` satisfies the request schema. The request is valid; the application cannot fulfill it. Conflating those two cases would make client recovery less precise.

### Why strict numeric validation?

Gateway boundaries should not silently reinterpret caller input. Requiring a JSON number for `amount` keeps the contract deterministic and avoids ambiguous coercion behavior.

### What would change in production?

The synthetic store would be replaced by an authenticated downstream service, with authorization added before any refund side effect. I would add request correlation, audit events that avoid sensitive data, idempotency for refund operations, service-level timeouts, and production secrets/configuration management. None of those concerns are necessary to prove Task 1's requested MCP behavior.
