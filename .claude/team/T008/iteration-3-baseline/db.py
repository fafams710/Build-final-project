"""Plain Postgres persistence. Rules are the source of truth and their methods
raise on failure; tick writes are queued and best-effort."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Alert, Rule, Tick, normalize_symbol

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
    """CREATE TABLE IF NOT EXISTS watchlist (
        symbol TEXT PRIMARY KEY,
        created_at DOUBLE PRECISION NOT NULL
    )""",
    # No foreign key to alert_rules: alerts outlive their rule.
    """CREATE TABLE IF NOT EXISTS alerts (
        id TEXT PRIMARY KEY,
        rule_id TEXT NOT NULL,
        symbol TEXT NOT NULL,
        rule_type TEXT NOT NULL,
        threshold DOUBLE PRECISION NOT NULL,
        price DOUBLE PRECISION NOT NULL,
        triggered_at DOUBLE PRECISION NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS alerts_triggered_at_idx ON alerts (triggered_at DESC)",
]

# Shutdown waits at most this long for queued alerts to be written.
ALERT_DRAIN_TIMEOUT_SECONDS = 3.0
# Pause after a failed alert batch so a down database is not hammered.
ALERT_FAILURE_BACKOFF_SECONDS = 1.0

# Serialises watchlist cap checks across concurrent rule creations.
_WATCHLIST_LOCK_KEY = 8_008_001


class DatabaseUnavailable(RuntimeError):
    """Raised when rule persistence is requested but there is no usable database."""


class WatchlistFull(RuntimeError):
    """Raised when a rule for a new symbol would exceed the watchlist cap."""


def _ts(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


class Database:
    def __init__(
        self,
        url: str = "",
        persist_rules: bool = True,
        persist_ticks: bool = True,
        queue_size: int = 10000,
        alert_queue_size: int = 1000,
    ) -> None:
        self._url = url
        self._persist_rules = persist_rules
        self._persist_ticks = persist_ticks
        self._pool: Any = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self._worker: Optional[asyncio.Task] = None
        self._alert_queue: asyncio.Queue = asyncio.Queue(maxsize=alert_queue_size)
        self._alert_worker: Optional[asyncio.Task] = None

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
            await self._backfill_watchlist()
            self._alert_worker = asyncio.create_task(
                self._alert_writer(), name="alert-writer"
            )
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

    async def _drain_alerts(self) -> None:
        if self._alert_worker is None or self._pool is None:
            return
        try:
            await asyncio.wait_for(
                self._alert_queue.join(), timeout=ALERT_DRAIN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            log.warning("alert drain timed out; %d alert(s) unwritten", self._alert_queue.qsize())
        except Exception as exc:
            log.warning("alert drain failed: %s", type(exc).__name__)

    async def close(self) -> None:
        await self._drain_alerts()
        for attr in ("_worker", "_alert_worker"):
            worker = getattr(self, attr)
            setattr(self, attr, None)
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

    async def create_rule_with_watchlist(self, rule: Rule, max_symbols: int) -> bool:
        """Cap check + watchlist insert + rule insert in one transaction.

        Returns True if the symbol was newly added to the watchlist. Raises
        WatchlistFull when the symbol is new and the cap is reached.
        """
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", _WATCHLIST_LOCK_KEY)
                exists = await conn.fetchval(
                    "SELECT 1 FROM watchlist WHERE symbol = $1", rule.symbol
                )
                if not exists:
                    count = await conn.fetchval("SELECT count(*) FROM watchlist")
                    if count >= max_symbols:
                        raise WatchlistFull("watchlist full")
                result = await conn.execute(
                    "INSERT INTO watchlist (symbol, created_at) VALUES ($1, $2) "
                    "ON CONFLICT DO NOTHING",
                    rule.symbol, rule.created_at,
                )
                await conn.execute(
                    "INSERT INTO alert_rules (id, symbol, type, threshold, window_seconds, "
                    "created_at) VALUES ($1,$2,$3,$4,$5,$6)",
                    rule.id, rule.symbol, rule.type.value, rule.threshold,
                    rule.window_seconds, rule.created_at,
                )
        return result.endswith(" 1")

    async def load_watchlist(self) -> list[str]:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT symbol FROM watchlist ORDER BY symbol")
        return [r["symbol"] for r in rows]

    async def _backfill_watchlist(self) -> None:
        """Idempotently watch symbols of rules that predate the watchlist table
        (cap not applied). Never fails startup."""
        if self._pool is None or not self._persist_rules:
            return
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT symbol, MIN(created_at) AS created_at FROM alert_rules "
                    "GROUP BY symbol"
                )
                values = []
                for r in rows:
                    try:
                        values.append((normalize_symbol(r["symbol"]), r["created_at"]))
                    except ValueError:
                        continue
                if values:
                    await conn.executemany(
                        "INSERT INTO watchlist (symbol, created_at) VALUES ($1, $2) "
                        "ON CONFLICT DO NOTHING",
                        values,
                    )
        except Exception as exc:
            log.error("watchlist backfill failed: %s", type(exc).__name__)

    # ---- alerts ------------------------------------------------------
    def enqueue_alert(self, alert: Alert) -> None:
        """Non-blocking; no-op without a pool, drops the alert if the writer is behind."""
        if self._pool is None:
            return
        try:
            self._alert_queue.put_nowait(alert)
        except asyncio.QueueFull:
            log.warning("alert queue full; dropping alert for rule %s", alert.rule_id)

    async def _alert_writer(self) -> None:
        while True:
            batch = [await self._alert_queue.get()]
            while len(batch) < 200 and not self._alert_queue.empty():
                batch.append(self._alert_queue.get_nowait())
            rows = [
                (a.id, a.rule_id, a.symbol, a.rule_type, a.threshold, a.price, a.triggered_at)
                for a in batch
            ]
            sql = (
                "INSERT INTO alerts (id, rule_id, symbol, rule_type, threshold, "
                "price, triggered_at) VALUES ($1,$2,$3,$4,$5,$6,$7)"
            )
            failed = False
            try:
                try:
                    async with self._pool.acquire() as conn:
                        await conn.executemany(sql, rows)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed = True
                    log.error("alert batch insert failed: %s", type(exc).__name__)
                    # Retry row by row so one bad alert does not lose the rest.
                    for row in rows:
                        try:
                            async with self._pool.acquire() as conn:
                                await conn.execute(sql, *row)
                        except asyncio.CancelledError:
                            raise
                        except Exception as row_exc:
                            log.error("alert insert failed: %s", type(row_exc).__name__)
            finally:
                for _ in batch:
                    self._alert_queue.task_done()
            if failed:
                await asyncio.sleep(ALERT_FAILURE_BACKOFF_SECONDS)

    async def list_alerts(self, limit: int = 50) -> list[Alert]:
        pool = self._require_rules_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, rule_id, symbol, rule_type, threshold, price, triggered_at "
                "FROM alerts ORDER BY triggered_at DESC, id DESC LIMIT $1",
                limit,
            )
        return [Alert(**dict(r)) for r in rows]
