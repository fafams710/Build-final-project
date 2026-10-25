"""Plain Postgres persistence. Rules are the source of truth and their methods
raise on failure; tick writes are queued and best-effort."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Rule, Tick

log = logging.getLogger(__name__)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ticks (
        symbol TEXT NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        volume DOUBLE PRECISION,
        exchange_ts TIMESTAMPTZ NOT NULL,
        received_ts TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alert_rules (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        type TEXT NOT NULL,
        threshold DOUBLE PRECISION NOT NULL,
        window_seconds DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL
    )""",
    # Old volumes had a NOT NULL webhook_url column; drop it, keeping rows.
    "ALTER TABLE alert_rules DROP COLUMN IF EXISTS webhook_url",
]


class DatabaseUnavailable(RuntimeError):
    """Raised when rule persistence is requested but there is no usable database."""


def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


class Database:
    def __init__(
        self,
        url: str = "",
        persist_rules: bool = True,
        persist_ticks: bool = True,
        queue_size: int = 10000,
    ) -> None:
        self._url = url
        self._persist_rules = persist_rules
        self._persist_ticks = persist_ticks
        self._pool: Any = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._worker: Optional[asyncio.Task] = None

    @property
    def available(self) -> bool:
        return self._pool is not None

    async def connect(self) -> None:
        if not self._url:
            return
        try:
            import asyncpg

            self._pool = await asyncio.wait_for(
                asyncpg.create_pool(self._url, min_size=1, max_size=4, timeout=5), timeout=10
            )
            async with self._pool.acquire() as conn:
                for stmt in SCHEMA:
                    await conn.execute(stmt)
            if self._persist_ticks:
                self._worker = asyncio.create_task(self._tick_worker(), name="tick-writer")
        except Exception as exc:
            log.error("database unavailable, persistence disabled: %s", type(exc).__name__)
            await self._close_pool()

    async def _close_pool(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                pass

    async def close(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except (asyncio.CancelledError, Exception):
                pass
        await self._close_pool()

    # ---- ticks -------------------------------------------------------
    def enqueue_tick(self, tick: Tick) -> None:
        """Non-blocking; drops the tick if the writer is behind."""
        if self._pool is None or not self._persist_ticks:
            return
        try:
            self._queue.put_nowait(tick)
        except asyncio.QueueFull:
            log.warning("tick queue full; dropping tick for %s", tick.symbol)

    async def _tick_worker(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < 200 and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            rows = [
                (t.symbol, t.price, t.volume, _ts(t.ts_ms / 1000), _ts(t.received_at))
                for t in batch
            ]
            try:
                async with self._pool.acquire() as conn:
                    await conn.executemany(
                        "INSERT INTO ticks (symbol, price, volume, exchange_ts, received_ts) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        rows,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("tick insert failed: %s", type(exc).__name__)

    # ---- rules -------------------------------------------------------
    # Rule methods raise on failure so callers can map errors to 503.
    def _require_rules_pool(self) -> Any:
        if self._pool is None or not self._persist_rules:
            raise DatabaseUnavailable("database unavailable")
        return self._pool

    async def save_rule(self, rule: Rule) -> None:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO alert_rules (id, symbol, type, threshold, window_seconds, "
                "created_at) VALUES ($1,$2,$3,$4,$5,$6)",
                rule.id, rule.symbol, rule.type.value, rule.threshold,
                rule.window_seconds, rule.created_at,
            )

    async def delete_rule(self, rule_id: str) -> bool:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            result = await conn.execute("DELETE FROM alert_rules WHERE id = $1", rule_id)
        return result.endswith(" 1")

    async def list_rules(self, symbol: Optional[str] = None, limit: int = 50) -> list[Rule]:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch(
                    "SELECT * FROM alert_rules WHERE symbol = $1 "
                    "ORDER BY created_at DESC, id DESC LIMIT $2",
                    symbol, limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM alert_rules ORDER BY created_at DESC, id DESC LIMIT $1",
                    limit,
                )
        return [Rule(**dict(r)) for r in rows]

    async def load_rules(self) -> list[Rule]:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM alert_rules")
        return [Rule(**dict(r)) for r in rows]
