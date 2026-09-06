# Task 2 Walkthrough - MCP Security Gateway / JSON-RPC Reverse Proxy

## Purpose

Task 2 implements a lightweight HTTP/JSON-RPC reverse proxy between an AI-agent client and a downstream mock MCP service.

The security requirement is not merely to return an authorization error. The gateway must prevent an unauthorized `admin_*` tool invocation from reaching the downstream service at all.

## Required behavior

The gateway:

- accepts `Authorization: Bearer <token>`;
- maps the authenticated caller to `admin` or `viewer`;
- forwards `tools/list`;
- inspects `tools/call`;
- requires the `admin` role when `params.name` begins with `admin_`;
- returns JSON-RPC error `-32001: Unauthorized Tool Call` for a prohibited call;
- does not invoke the downstream service for that prohibited request.

## Architecture

    AI agent / JSON-RPC client
                |
                | HTTP POST /rpc
                | Authorization: Bearer <token>
                v
    +--------------------------------------+
    | Task 2 Security Gateway              |
    |                                      |
    | 1. Parse JSON-RPC                    |
    | 2. Authenticate Bearer token         |
    | 3. Resolve trusted role              |
    | 4. Inspect tools/call                |
    | 5. Authorize admin_* operations      |
    | 6. Forward only after authorization  |
    +------------------+-------------------+
                       |
                       | authorized request
                       v
              Mock downstream MCP service

Default local endpoints:

- Gateway: `http://127.0.0.1:8000/rpc`
- Mock downstream: `http://127.0.0.1:8001/rpc`

## Authentication design

The assessment specifies Bearer tokens and the roles `admin` and `viewer`, but it does not provide an identity provider, signing key, JWKS endpoint, issuer, or JWT schema.

For that reason, the assessment implementation uses deterministic opaque demo tokens mapped server-side to trusted roles:

- `assessment-admin-token` -> `admin`
- `assessment-viewer-token` -> `viewer`

They can be overridden with:

- `TASK2_ADMIN_TOKEN`
- `TASK2_VIEWER_TOKEN`

The gateway does not accept a role supplied by the JSON-RPC caller.

A production JWT/OIDC design would verify the token signature, issuer, audience, expiration/not-before constraints, and trusted authorization claims before using a role.

Simply decoding a JWT payload would not constitute authentication.

## Authorization policy

`tools/list` is transparently forwarded after authentication.

For `tools/call`, the gateway examines `params.name`.

    tools/call: get_profile
        viewer -> forward
        admin  -> forward

    tools/call: admin_rotate_key
        admin  -> forward
        viewer -> deny with -32001

The critical ordering is:

    authenticate
        |
        v
    inspect tool
        |
        v
    authorize
       / \
      /   \
    deny  allow
     |      |
     X      v
    no    downstream
    call

A gateway that forwarded the request first and only later returned an authorization error would be insecure.

## JSON-RPC handling

The implementation explicitly handles several boundary conditions:

- malformed JSON -> `-32700 Parse error`;
- invalid JSON-RPC request -> `-32600 Invalid Request`;
- malformed `tools/call` parameters -> `-32602 Invalid params`;
- unauthorized administrator tool -> `-32001 Unauthorized Tool Call`;
- downstream transport failure -> sanitized `-32002`.

Batch JSON-RPC requests are intentionally outside the bounded assessment scope and are rejected.

Where a valid request identifier is available, it is preserved in the response.

## Proving deny-before-forward

The synthetic downstream maintains observable state:

- `rpc_calls`
- `methods`

The primary security test sends a viewer request for `admin_rotate_key` and verifies:

    gateway response:
        code = -32001
        message = Unauthorized Tool Call

    downstream state:
        rpc_calls = 0
        methods = []

This is stronger than checking only the response returned to the caller.

It proves that the forbidden request did not cross the downstream boundary.

## Real HTTP runtime validation

The automated tests were supplemented with two actual localhost Uvicorn processes:

    Gateway:
        127.0.0.1:8000

    Mock downstream:
        127.0.0.1:8001

Observed sequence:

1. Downstream counters reset to zero.
2. Viewer requested `tools/list`.
3. Gateway returned success.
4. Downstream showed `rpc_calls = 1`.
5. Counters were reset again.
6. Viewer requested `admin_rotate_key`.
7. Gateway returned `-32001 Unauthorized Tool Call`.
8. Downstream remained at `rpc_calls = 0`.
9. Admin requested the same `admin_rotate_key` tool.
10. Gateway returned success.
11. Downstream showed `rpc_calls = 1` and `methods = {tools/call}`.

The downstream Uvicorn access log also contained no `/rpc` request corresponding to the prohibited viewer operation.

## Error sanitization

Downstream transport failures may contain internal implementation details such as hostnames, ports, topology, or exception text.

The gateway converts such transport failures to a caller-safe response:

    {
      "jsonrpc": "2.0",
      "id": 86,
      "error": {
        "code": -32002,
        "message": "Downstream MCP server unavailable"
      }
    }

The automated test deliberately creates an exception containing a synthetic internal hostname and verifies that neither the hostname nor raw exception text is returned to the caller.

## Implementation structure

`src/task2_mcp_gateway/auth.py`

- Bearer-token parsing
- trusted opaque-token mapping
- `admin` / `viewer` role resolution
- credential comparison using `hmac.compare_digest`

`src/task2_mcp_gateway/jsonrpc.py`

- JSON parsing
- request validation
- safe JSON-RPC error construction
- request-ID handling
- `admin_*` tool classification

`src/task2_mcp_gateway/proxy.py`

- authentication
- authorization-before-forwarding
- asynchronous HTTP forwarding
- downstream error sanitization

`src/task2_mcp_gateway/app.py`

- runnable Starlette gateway
- pooled asynchronous HTTP client
- localhost runtime entry point

`src/task2_mcp_gateway/mock_downstream.py`

- synthetic JSON-RPC downstream
- `tools/list`
- `tools/call`
- observable forwarding counters

`tests/task2/test_gateway.py`

- primary authentication and authorization behavior
- deny-before-forward invariant

`tests/task2/test_edges.py`

- forged token
- incorrect authorization scheme
- malformed JSON
- invalid JSON-RPC version
- malformed tool call
- unknown-method forwarding
- downstream error sanitization

`tests/task2/test_jsonrpc.py`

- focused JSON-RPC wire-format edge cases

## Validation evidence

Local environment:

- Python 3.12.10
- `httpx2` 2.12.0
- Starlette 1.6.0
- Uvicorn 0.52.4

Static validation:

    ruff check .
    All checks passed!

Automated validation:

    pytest -q
    34 passed

The 34-test repository suite includes both Task 1 and Task 2, so Task 2 development did not regress the Task 1 MCP server.

## Why a synthetic downstream is appropriate

The task evaluates proxy behavior, authorization, and JSON-RPC handling rather than a specific business system.

The synthetic downstream provides:

- a real HTTP destination;
- deterministic behavior;
- no external credentials;
- no production side effects;
- direct evidence about whether forwarding occurred.

No employer data, customer data, private infrastructure, or production credentials are used.

## Production considerations

A production implementation would likely add:

- verified OIDC/JWT identity;
- centralized authorization policy;
- TLS;
- request and body-size limits;
- structured security audit events;
- correlation identifiers;
- production secrets management;
- service discovery;
- observability and metrics;
- controlled retry behavior;
- deployment-specific availability controls.

Those additions are intentionally separated from the bounded property required by this assessment.

## Interview explanation

A concise explanation:

> I separated authentication, JSON-RPC parsing, authorization, and forwarding so the authorization boundary could be tested directly. The assessment specifies Bearer tokens and roles but no identity provider, so I used deterministic opaque demo credentials mapped server-side to admin or viewer instead of inventing an insecure JWT model. For `tools/call`, the gateway checks the requested tool before the forwarding function is invoked. A viewer requesting an `admin_` tool receives `-32001 Unauthorized Tool Call`. More importantly, the mock downstream maintains an invocation counter, and the test verifies that it remains zero. I repeated that validation with two actual localhost HTTP processes so I could prove the prohibited request never crossed the downstream boundary.

## Likely interview questions

### Why opaque tokens instead of JWTs?

Because the assessment does not provide a trusted issuer, signing key, JWKS endpoint, audience, or token schema. The opaque-token mapping creates deterministic authentication without pretending that decoding an unsigned JWT is secure authentication.

### Why `hmac.compare_digest`?

Bearer tokens are credentials. `compare_digest` avoids ordinary string comparison for credential matching.

### Why does authorization return HTTP 200?

The HTTP request was processed successfully and produced a JSON-RPC application error. The authorization failure is represented at the JSON-RPC layer with `-32001`.

Authentication failures additionally use HTTP 401 because acceptable HTTP credentials were not supplied.

### Why forward JSON-RPC methods other than `tools/call`?

The assessment defines authorization policy specifically for `tools/call`, while `tools/list` should be transparently forwarded. Authenticated methods outside the defined policy remain a downstream protocol concern rather than being silently redefined by the gateway.

### How do you prove the prohibited operation was not executed?

The automated tests inspect the downstream invocation counter, and the real runtime test independently inspected both the downstream `/stats` endpoint and its HTTP access log. For the prohibited viewer request, all showed zero downstream invocation.

### What is the central design decision?

Authorization occurs before forwarding. The returned error alone is not considered sufficient evidence.
