"""Trusted token-budget tests for Task 4."""

from __future__ import annotations

import pytest

from task4_model_router.token_budget import (
    DEFAULT_COMPLETION_RESERVE,
    RequestTokenBudgeter,
    TokenBudgetError,
)


def test_budget_is_computed_from_actual_request_content() -> None:
    budgeter = RequestTokenBudgeter()

    short = budgeter.budget(
        {
            "model": "synthetic",
            "messages": [
                {
                    "role": "user",
                    "content": "hello",
                }
            ],
            "max_tokens": 100,
        }
    )

    long = budgeter.budget(
        {
            "model": "synthetic",
            "messages": [
                {
                    "role": "user",
                    "content": "hello " * 1_000,
                }
            ],
            "max_tokens": 100,
        }
    )

    assert long.input_tokens > short.input_tokens
    assert short.completion_reserve == 100
    assert long.completion_reserve == 100

    assert short.total_tokens == (
        short.input_tokens + 100
    )

    assert long.total_tokens == (
        long.input_tokens + 100
    )


def test_default_completion_reserve_is_applied() -> None:
    budgeter = RequestTokenBudgeter()

    budget = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "hello",
        }
    )

    assert (
        budget.completion_reserve
        == DEFAULT_COMPLETION_RESERVE
    )

    assert budget.total_tokens == (
        budget.input_tokens
        + DEFAULT_COMPLETION_RESERVE
    )


def test_max_completion_tokens_is_supported() -> None:
    budgeter = RequestTokenBudgeter()

    budget = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "hello",
            "max_completion_tokens": 321,
        }
    )

    assert budget.completion_reserve == 321


def test_caller_token_count_field_is_not_authoritative() -> None:
    budgeter = RequestTokenBudgeter()

    budget = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "security " * 1_000,
            "token_count": 1,
            "max_tokens": 100,
        }
    )

    # The caller-provided token_count is merely request content. It does not
    # become the limiter reservation.
    assert budget.input_tokens > 1
    assert budget.total_tokens > 100


@pytest.mark.parametrize(
    "invalid",
    [
        0,
        -1,
        True,
        "100",
    ],
)
def test_invalid_completion_limits_are_rejected(
    invalid,
) -> None:
    budgeter = RequestTokenBudgeter()

    with pytest.raises(
        TokenBudgetError,
        match="positive integer",
    ):
        budgeter.budget(
            {
                "model": "synthetic",
                "prompt": "hello",
                "max_tokens": invalid,
            }
        )


def test_conflicting_completion_fields_are_rejected() -> None:
    budgeter = RequestTokenBudgeter()

    with pytest.raises(
        TokenBudgetError,
        match="only one",
    ):
        budgeter.budget(
            {
                "model": "synthetic",
                "prompt": "hello",
                "max_tokens": 100,
                "max_completion_tokens": 100,
            }
        )


def test_excessive_completion_reserve_is_rejected() -> None:
    budgeter = RequestTokenBudgeter()

    with pytest.raises(
        TokenBudgetError,
        match="gateway maximum",
    ):
        budgeter.budget(
            {
                "model": "synthetic",
                "prompt": "hello",
                "max_tokens": 50_001,
            }
        )


def test_extension_fields_are_included_in_accounting() -> None:
    budgeter = RequestTokenBudgeter()

    baseline = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "hello",
            "max_tokens": 100,
        }
    )

    extended = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "hello",
            "custom_context": (
                "additional context " * 1_000
            ),
            "max_tokens": 100,
        }
    )

    assert (
        extended.input_tokens
        > baseline.input_tokens
    )


def test_budgeting_is_deterministic_for_equivalent_json_objects() -> None:
    budgeter = RequestTokenBudgeter()

    first = budgeter.budget(
        {
            "model": "synthetic",
            "prompt": "hello",
            "temperature": 0,
            "max_tokens": 100,
        }
    )

    second = budgeter.budget(
        {
            "temperature": 0,
            "prompt": "hello",
            "max_tokens": 100,
            "model": "synthetic",
        }
    )

    assert first == second
