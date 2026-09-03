# AI Security Gateway Assessment

A standalone technical assessment repository for MCP server, security gateway, streaming guardrail, and resilient LLM routing exercises.

This repository is intentionally independent of any employer or client codebase. It contains only assessment-specific code, synthetic test data, and public documentation.

## Status

- Task 1 - MCP server with strict validation and stdio transport: implemented; CI verification pending
- Task 2 - MCP security gateway proxy: planned
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
