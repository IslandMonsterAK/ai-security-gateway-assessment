"""Adversarial tests for the Task 3 streaming PII redactor."""

from __future__ import annotations

import pytest

from task3_stream_guardrail.redactor import (
    MAX_PENDING_CHARS,
    REPLACEMENT,
    StreamingRedactor,
)


def _stream_two_chunks(source: str, split: int) -> str:
    redactor = StreamingRedactor()

    return (
        redactor.feed(source[:split])
        + redactor.feed(source[split:])
        + redactor.flush()
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Contact alice@example.com for help.",
            f"Contact {REPLACEMENT} for help.",
        ),
        (
            "SSN 123-45-6789 end",
            f"SSN {REPLACEMENT} end",
        ),
        (
            "Card 4111 1111 1111 1111 done",
            f"Card {REPLACEMENT} done",
        ),
        (
            "Card 4111 1111 1111 1111.",
            f"Card {REPLACEMENT}.",
        ),
        (
            "Card 4111-1111-1111-1111 done",
            f"Card {REPLACEMENT} done",
        ),
        (
            "Card 4111111111111111 done",
            f"Card {REPLACEMENT} done",
        ),
    ],
)
def test_every_possible_two_chunk_split_is_redacted(
    source: str,
    expected: str,
) -> None:
    """Every character boundary must produce the same safe result."""

    for split in range(len(source) + 1):
        assert _stream_two_chunks(source, split) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Email alice@example.com complete.",
            f"Email {REPLACEMENT} complete.",
        ),
        (
            "SSN 123-45-6789 complete.",
            f"SSN {REPLACEMENT} complete.",
        ),
        (
            "Card 4111111111111111 complete.",
            f"Card {REPLACEMENT} complete.",
        ),
    ],
)
def test_one_character_chunks_do_not_defeat_redaction(
    source: str,
    expected: str,
) -> None:
    """Model the most adversarial possible provider chunking."""

    redactor = StreamingRedactor()
    output = ""

    for char in source:
        output += redactor.feed(char)
        assert redactor.pending_size <= MAX_PENDING_CHARS

    output += redactor.flush()

    assert output == expected


def test_multiple_pii_values_in_one_stream_are_all_redacted() -> None:
    source = (
        "Email alice@example.com, "
        "SSN 123-45-6789, "
        "card 4111 1111 1111 1111."
    )

    expected = (
        f"Email {REPLACEMENT}, "
        f"SSN {REPLACEMENT}, "
        f"card {REPLACEMENT}."
    )

    redactor = StreamingRedactor()

    output = (
        redactor.feed(source[:9])
        + redactor.feed(source[9:28])
        + redactor.feed(source[28:47])
        + redactor.feed(source[47:])
        + redactor.flush()
    )

    assert output == expected


def test_pii_at_end_of_stream_is_redacted_during_flush() -> None:
    redactor = StreamingRedactor()

    output = redactor.feed(
        "Contact alice@example.com"
    )

    # The unresolved email is deliberately retained because no right-hand
    # boundary has arrived yet.
    assert output == "Contact "

    output += redactor.flush()

    assert output == f"Contact {REPLACEMENT}"
    assert redactor.pending_size == 0


def test_safe_text_is_not_held_behind_a_fixed_tail_buffer() -> None:
    redactor = StreamingRedactor()

    output = redactor.feed("Hello, ")

    assert output == "Hello, "
    assert redactor.pending_size == 0


def test_pending_memory_is_bounded_for_large_candidate_like_input() -> None:
    source = "a" * 10_000

    redactor = StreamingRedactor()

    output = redactor.feed(source)

    assert redactor.pending_size <= MAX_PENDING_CHARS

    output += redactor.flush()

    assert output == source
    assert redactor.pending_size == 0


def test_normal_non_pii_content_is_preserved() -> None:
    source = "The system completed successfully, with no sensitive data."

    redactor = StreamingRedactor()

    output = (
        redactor.feed(source[:17])
        + redactor.feed(source[17:39])
        + redactor.feed(source[39:])
        + redactor.flush()
    )

    assert output == source
