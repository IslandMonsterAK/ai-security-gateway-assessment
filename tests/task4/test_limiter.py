"""Concurrency and sliding-window tests for the Task 4 limiter."""

from __future__ import annotations

import asyncio

import pytest

from task4_model_router.limiter import (
    SQLiteSlidingWindowLimiter,
)


@pytest.mark.asyncio
async def test_exact_50000_token_limit_is_allowed(
    tmp_path,
) -> None:
    limiter = SQLiteSlidingWindowLimiter(
        tmp_path / "rate-limit.sqlite3"
    )
    await limiter.initialize()

    allowed = await limiter.reserve(
        "tenant-alpha-key",
        50_000,
        now_ms=1_000_000,
    )

    denied = await limiter.reserve(
        "tenant-alpha-key",
        1,
        now_ms=1_000_001,
    )

    assert allowed.allowed is True
    assert allowed.used_tokens == 50_000
    assert allowed.remaining_tokens == 0

    assert denied.allowed is False
    assert denied.used_tokens == 50_000
    assert denied.remaining_tokens == 0


@pytest.mark.asyncio
async def test_event_expires_at_exact_window_boundary(
    tmp_path,
) -> None:
    limiter = SQLiteSlidingWindowLimiter(
        tmp_path / "rate-limit.sqlite3"
    )
    await limiter.initialize()

    start = 2_000_000

    first = await limiter.reserve(
        "tenant-alpha-key",
        50_000,
        now_ms=start,
    )

    second = await limiter.reserve(
        "tenant-alpha-key",
        50_000,
        now_ms=start + 60_000,
    )

    assert first.allowed is True
    assert second.allowed is True
    assert second.used_tokens == 50_000


@pytest.mark.asyncio
async def test_tenants_have_independent_windows(
    tmp_path,
) -> None:
    limiter = SQLiteSlidingWindowLimiter(
        tmp_path / "rate-limit.sqlite3"
    )
    await limiter.initialize()

    now = 3_000_000

    alpha = await limiter.reserve(
        "tenant-alpha-key",
        50_000,
        now_ms=now,
    )

    beta = await limiter.reserve(
        "tenant-beta-key",
        50_000,
        now_ms=now,
    )

    assert alpha.allowed is True
    assert beta.allowed is True

    assert await limiter.usage(
        "tenant-alpha-key",
        now_ms=now,
    ) == 50_000

    assert await limiter.usage(
        "tenant-beta-key",
        now_ms=now,
    ) == 50_000


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_oversubscribe_budget(
    tmp_path,
) -> None:
    limiter = SQLiteSlidingWindowLimiter(
        tmp_path / "rate-limit.sqlite3"
    )
    await limiter.initialize()

    now = 4_000_000

    baseline = await limiter.reserve(
        "tenant-alpha-key",
        45_000,
        now_ms=now,
    )

    assert baseline.allowed is True

    first, second = await asyncio.gather(
        limiter.reserve(
            "tenant-alpha-key",
            4_000,
            now_ms=now + 1,
        ),
        limiter.reserve(
            "tenant-alpha-key",
            4_000,
            now_ms=now + 1,
        ),
    )

    assert sorted(
        [first.allowed, second.allowed]
    ) == [False, True]

    assert await limiter.usage(
        "tenant-alpha-key",
        now_ms=now + 1,
    ) == 49_000


@pytest.mark.asyncio
async def test_usage_persists_across_limiter_instances(
    tmp_path,
) -> None:
    database = tmp_path / "rate-limit.sqlite3"

    first = SQLiteSlidingWindowLimiter(database)
    await first.initialize()

    admitted = await first.reserve(
        "tenant-alpha-key",
        12_345,
        now_ms=5_000_000,
    )

    assert admitted.allowed is True
    assert database.exists()

    second = SQLiteSlidingWindowLimiter(database)
    await second.initialize()

    assert await second.usage(
        "tenant-alpha-key",
        now_ms=5_000_001,
    ) == 12_345


@pytest.mark.asyncio
async def test_raw_api_key_is_not_stored_in_database(
    tmp_path,
) -> None:
    database = tmp_path / "rate-limit.sqlite3"

    limiter = SQLiteSlidingWindowLimiter(database)
    await limiter.initialize()

    raw_key = "synthetic-secret-tenant-key"

    await limiter.reserve(
        raw_key,
        100,
        now_ms=6_000_000,
    )

    database_bytes = database.read_bytes()

    assert raw_key.encode() not in database_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_count",
    [
        0,
        -1,
    ],
)
async def test_invalid_token_counts_are_rejected(
    tmp_path,
    invalid_count: int,
) -> None:
    limiter = SQLiteSlidingWindowLimiter(
        tmp_path / "rate-limit.sqlite3"
    )
    await limiter.initialize()

    with pytest.raises(
        ValueError,
        match="positive integer",
    ):
        await limiter.reserve(
            "tenant-alpha-key",
            invalid_count,
            now_ms=7_000_000,
        )
