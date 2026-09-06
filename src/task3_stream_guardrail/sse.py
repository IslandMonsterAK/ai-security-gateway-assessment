"""SSE framing helpers for the Task 3 streaming guardrail."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class SSEDecodeError(ValueError):
    """Raised when an upstream SSE data event is not understood."""


@dataclass(frozen=True)
class UpstreamEvent:
    """One parsed OpenAI-compatible upstream streaming event."""

    text: str = ""
    done: bool = False


def decode_upstream_data_line(line: str) -> UpstreamEvent | None:
    """Decode one OpenAI-compatible SSE data line.

    Non-data SSE lines and blank lines are ignored.
    """

    if not line or not line.startswith("data:"):
        return None

    raw = line[5:].lstrip()

    if raw == "[DONE]":
        return UpstreamEvent(done=True)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SSEDecodeError("Invalid JSON in upstream SSE event") from exc

    if not isinstance(payload, dict):
        raise SSEDecodeError("Invalid upstream SSE payload")

    choices = payload.get("choices")

    if not isinstance(choices, list) or not choices:
        raise SSEDecodeError("Upstream SSE event is missing choices")

    choice = choices[0]

    if not isinstance(choice, dict):
        raise SSEDecodeError("Invalid upstream choice")

    delta = choice.get("delta")

    if not isinstance(delta, dict):
        raise SSEDecodeError("Invalid upstream delta")

    content = delta.get("content")

    # Role-only and finish events may legitimately contain no text.
    if content is None:
        return UpstreamEvent()

    if not isinstance(content, str):
        raise SSEDecodeError("Upstream content delta must be text")

    return UpstreamEvent(text=content)


def encode_openai_delta(text: str) -> bytes:
    """Encode one safe text delta as an OpenAI-compatible SSE event."""

    payload: dict[str, Any] = {
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": text,
                },
                "finish_reason": None,
            }
        ],
    }

    data = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return f"data: {data}\n\n".encode()


def encode_done() -> bytes:
    """Return the standard completion sentinel."""

    return b"data: [DONE]\n\n"


def encode_gateway_error(message: str) -> bytes:
    """Encode a caller-safe streaming gateway error."""

    payload = {
        "error": {
            "type": "gateway_stream_error",
            "message": message,
        }
    }

    data = json.dumps(payload, separators=(",", ":"))

    return f"event: error\ndata: {data}\n\n".encode()
