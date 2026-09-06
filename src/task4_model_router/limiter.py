"""Atomic SQLite sliding-window token limiting for Task 4."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

DEFAULT_LIMIT_TOKENS = 50_000
DEFAULT_WINDOW_MS = 60_000
DEFAULT_BUSY_TIMEOUT_MS = 5_000


@dataclass(frozen=True)
class Admission:
    """Result of one atomic token-budget admission attempt."""

    allowed: bool
    used_tokens: int
    requested_tokens: int
    remaining_tokens: int
    retry_after_seconds: float | None = None


def _tenant_hash(api_key: str) -> str:
    """Return a stable non-reversible identifier for a tenant API key."""

    if not api_key:
        raise ValueError("Tenant API key must not be empty")

    return hashlib.sha256(
        api_key.encode("utf-8")
    ).hexdigest()


class SQLiteSlidingWindowLimiter:
    """Per-tenant token limiter backed by an on-disk SQLite database.

    Expiry, usage calculation, admission, and reservation occur inside one
    BEGIN IMMEDIATE transaction. Concurrent writers therefore cannot
    independently observe stale capacity and oversubscribe a tenant budget.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        limit_tokens: int = DEFAULT_LIMIT_TOKENS,
        window_ms: int = DEFAULT_WINDOW_MS,
    ) -> None:
        if limit_tokens <= 0:
            raise ValueError("limit_tokens must be positive")

        if window_ms <= 0:
            raise ValueError("window_ms must be positive")

        self._database_path = Path(database_path)
        self._limit_tokens = limit_tokens
        self._window_ms = window_ms

    @property
    def database_path(self) -> Path:
        """Return the configured on-disk database path."""

        return self._database_path

    @property
    def limit_tokens(self) -> int:
        """Return the configured token limit."""

        return self._limit_tokens

    @property
    def window_ms(self) -> int:
        """Return the configured sliding-window length."""

        return self._window_ms

    async def initialize(self) -> None:
        """Create the SQLite database and indexes if necessary."""

        self._database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        async with aiosqlite.connect(
            str(self._database_path)
        ) as db:
            await db.execute(
                f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}"
            )
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS token_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_hash TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL,
                    token_count INTEGER NOT NULL
                        CHECK (token_count > 0)
                )
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_token_events_tenant_time
                ON token_events (
                    tenant_hash,
                    occurred_at_ms
                )
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    idx_token_events_time
                ON token_events (occurred_at_ms)
                """
            )

            await db.commit()

    async def reserve(
        self,
        api_key: str,
        token_count: int,
        *,
        now_ms: int | None = None,
    ) -> Admission:
        """Atomically reserve token capacity for one tenant request."""

        tenant = _tenant_hash(api_key)

        if (
            isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or token_count <= 0
        ):
            raise ValueError(
                "token_count must be a positive integer"
            )

        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000

        cutoff_ms = now_ms - self._window_ms

        async with aiosqlite.connect(
            str(self._database_path),
            isolation_level=None,
            timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000,
        ) as db:
            await db.execute(
                f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}"
            )

            await db.execute("BEGIN IMMEDIATE")

            try:
                # Exactly-at-cutoff events are outside the active window.
                await db.execute(
                    """
                    DELETE FROM token_events
                    WHERE occurred_at_ms <= ?
                    """,
                    (cutoff_ms,),
                )

                cursor = await db.execute(
                    """
                    SELECT COALESCE(SUM(token_count), 0)
                    FROM token_events
                    WHERE tenant_hash = ?
                      AND occurred_at_ms > ?
                    """,
                    (tenant, cutoff_ms),
                )

                row = await cursor.fetchone()
                used_tokens = int(row[0]) if row else 0

                proposed = used_tokens + token_count

                if proposed > self._limit_tokens:
                    retry_after = await self._retry_after_seconds(
                        db,
                        tenant,
                        used_tokens,
                        token_count,
                        now_ms,
                        cutoff_ms,
                    )

                    await db.execute("COMMIT")

                    return Admission(
                        allowed=False,
                        used_tokens=used_tokens,
                        requested_tokens=token_count,
                        remaining_tokens=max(
                            0,
                            self._limit_tokens - used_tokens,
                        ),
                        retry_after_seconds=retry_after,
                    )

                await db.execute(
                    """
                    INSERT INTO token_events (
                        tenant_hash,
                        occurred_at_ms,
                        token_count
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        tenant,
                        now_ms,
                        token_count,
                    ),
                )

                await db.execute("COMMIT")

                return Admission(
                    allowed=True,
                    used_tokens=proposed,
                    requested_tokens=token_count,
                    remaining_tokens=(
                        self._limit_tokens - proposed
                    ),
                )

            except BaseException:
                await db.execute("ROLLBACK")
                raise

    async def _retry_after_seconds(
        self,
        db: aiosqlite.Connection,
        tenant: str,
        used_tokens: int,
        requested_tokens: int,
        now_ms: int,
        cutoff_ms: int,
    ) -> float | None:
        """Calculate when enough active capacity will expire."""

        excess = (
            used_tokens
            + requested_tokens
            - self._limit_tokens
        )

        if excess <= 0:
            return 0.0

        cursor = await db.execute(
            """
            SELECT occurred_at_ms, token_count
            FROM token_events
            WHERE tenant_hash = ?
              AND occurred_at_ms > ?
            ORDER BY occurred_at_ms ASC, id ASC
            """,
            (tenant, cutoff_ms),
        )

        rows = await cursor.fetchall()

        released_tokens = 0

        for occurred_at_ms, token_count in rows:
            released_tokens += int(token_count)

            if released_tokens >= excess:
                release_at_ms = (
                    int(occurred_at_ms)
                    + self._window_ms
                )

                remaining_ms = max(
                    1,
                    release_at_ms - now_ms,
                )

                return remaining_ms / 1000

        # A request larger than the maximum cannot become admissible merely
        # by waiting for previous reservations to expire.
        return None

    async def usage(
        self,
        api_key: str,
        *,
        now_ms: int | None = None,
    ) -> int:
        """Return current active-window usage without modifying state."""

        tenant = _tenant_hash(api_key)

        if now_ms is None:
            now_ms = time.time_ns() // 1_000_000

        cutoff_ms = now_ms - self._window_ms

        async with aiosqlite.connect(
            str(self._database_path)
        ) as db:
            cursor = await db.execute(
                """
                SELECT COALESCE(SUM(token_count), 0)
                FROM token_events
                WHERE tenant_hash = ?
                  AND occurred_at_ms > ?
                """,
                (tenant, cutoff_ms),
            )

            row = await cursor.fetchone()

        return int(row[0]) if row else 0
