# AI Security Gateway Assessment

A standalone technical assessment repository for MCP server, security gateway, streaming guardrail, and resilient LLM routing exercises.

This repository is intentionally independent of any employer or client codebase. It contains only assessment-specific code, synthetic test data, and public documentation.

## Status

- Task 1 - MCP server with strict validation and stdio transport: implemented and CI verified
- Task 2 - MCP security gateway proxy: implemented with automated and real HTTP runtime validation
- Task 3 - streaming PII guardrail: planned
- Task 4 - token-aware rate limiting and model fallback: planned

The implementation favors explicit trust boundaries, fail-closed validation, reproducible tests, and clear evidence for both positive and negative security paths.

## Task 1 quick start

Python 3.12 or 3.13 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
pytest -q tests/task1
ruff check .
```

Run the MCP server over stdio:

```bash
python -m task1_mcp_server.server
```

Because stdio is the MCP protocol channel, the server is normally launched by an MCP client rather than used as an interactive console program. Application logs are intentionally written only to stderr.

For the design rationale and interview-level walkthrough, see `docs/TASK1_WALKTHROUGH.md`.


## Task 2 quick start

Start the synthetic downstream MCP service in one terminal:

```bash
task2-mock-downstream
```

Start the authorization gateway in another terminal:

```bash
task2-mcp-gateway
```

Defaults:

```text
gateway:     http://127.0.0.1:8000/rpc
downstream:  http://127.0.0.1:8001/rpc

viewer token: assessment-viewer-token
admin token:  assessment-admin-token
```

The token values are explicit assessment-only demo credentials and can be
overridden with `TASK2_VIEWER_TOKEN` and `TASK2_ADMIN_TOKEN`.

Run Task 1 and Task 2 validation:

```bash
pytest -q tests/task1 tests/task2
ruff check .
```

For the security model, failure paths, runtime evidence, and interview-level
design rationale, see `docs/TASK2_WALKTHROUGH.md`.
