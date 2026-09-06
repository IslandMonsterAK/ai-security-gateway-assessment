"""Trusted server-side request token budgeting for Task 4."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import tiktoken

DEFAULT_COMPLETION_RESERVE = 512
MAX_COMPLETION_RESERVE = 50_000
ENCODING_NAME = "cl100k_base"


class TokenBudgetError(ValueError):
    """Raised when a request cannot be safely token-budgeted."""


@dataclass(frozen=True)
class TokenBudget:
    """Server-computed token reservation for one model request."""

    input_tokens: int
    completion_reserve: int
    total_tokens: int


class RequestTokenBudgeter:
    """Compute a deterministic token reservation from request content.

    The caller does not provide the authoritative token count. The gateway
    encodes the actual provider-facing request and adds the requested maximum
    completion allowance before admission to the sliding-window limiter.
    """

    def __init__(
        self,
        encoding_name: str = ENCODING_NAME,
    ) -> None:
        self._encoding = tiktoken.get_encoding(
            encoding_name
        )

    def budget(
        self,
        payload: dict[str, Any],
    ) -> TokenBudget:
        """Return the trusted token reservation for one request."""

        if not isinstance(payload, dict):
            raise TokenBudgetError(
                "Request body must be a JSON object"
            )

        completion_reserve = self._completion_reserve(
            payload
        )

        # Exclude only the completion-budget fields themselves. All other
        # provider-facing content is included in tokenization so the caller
        # cannot hide prompt material in an uncounted extension field.
        tokenized_payload = dict(payload)
        tokenized_payload.pop(
            "max_tokens",
            None,
        )
        tokenized_payload.pop(
            "max_completion_tokens",
            None,
        )

        try:
            canonical = json.dumps(
                tokenized_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise TokenBudgetError(
                "Request contains non-JSON data"
            ) from exc

        input_tokens = len(
            self._encoding.encode(canonical)
        )

        # Even an otherwise-empty JSON request consumes framing tokens.
        input_tokens = max(1, input_tokens)

        total_tokens = (
            input_tokens
            + completion_reserve
        )

        return TokenBudget(
            input_tokens=input_tokens,
            completion_reserve=completion_reserve,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _completion_reserve(
        payload: dict[str, Any],
    ) -> int:
        """Resolve and validate the requested completion allowance."""

        has_max_tokens = "max_tokens" in payload
        has_max_completion = (
            "max_completion_tokens" in payload
        )

        if (
            has_max_tokens
            and has_max_completion
        ):
            raise TokenBudgetError(
                "Specify only one completion token limit"
            )

        if has_max_completion:
            value = payload[
                "max_completion_tokens"
            ]
        elif has_max_tokens:
            value = payload["max_tokens"]
        else:
            value = DEFAULT_COMPLETION_RESERVE

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise TokenBudgetError(
                "Completion token limit must be a positive integer"
            )

        if value > MAX_COMPLETION_RESERVE:
            raise TokenBudgetError(
                "Completion token limit exceeds gateway maximum"
            )

        return value
