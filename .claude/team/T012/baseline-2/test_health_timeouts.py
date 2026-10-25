"""T012: honest /health (M3) and database timeouts (M8). Offline."""
import asyncio
import logging
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import Services, create_app
from app.models import Alert, Rule, Tick
from tests.conftest import AUTH, TEST_KEY
from tests.fakes.fake_db import FakeDatabase
from tests.fakes.fake_finnhub import FakeFinnhubClient

RULE = {"symbol": "AAPL", "type": "price_above", "threshold": 100}
KEYS = {"status", "database", "upstream"}


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, finnhub_api_key="", database_url="", app_api_key=TEST_KEY, **kw)


def _no_upstream(settings, on_tick, get_symbols):
    return None


def _health_client(db=None, upstream="connected", persistence=True):
    """TestClient (no key header) with a FakeDatabase and the chosen upstream state."""
    FakeFinnhubClient.instances.clear()
    if upstream == "disabled":
        factory = _no_upstream
    else:
        factory = FakeFinnhubClient.factory
    app = create_app(
        _settings(rules_persistence_enabled=persistence), upstream_factory=factory,
        db=db or FakeDatabase(),
    )
    c = TestClient(app)
    c.__enter__()
    if upstream == "connected":
        FakeFinnhubClient.instances[0].connected = True
    return c


@pytest.fixture
def health_client():
    made = []

    def make(**kw):
        c = _health_client(**kw)
        made.append(c)
        return c

    yield make
    for c in made:
        c.__exit__(None, None, None)


# ---- M3: /health ---------------------------------------------------------
def test_health_all_ok_no_key(health_client):
    r = health_client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "upstream": "connected"}


def test_health_db_failing_503(health_client):
    db = FakeDatabase()
    c = health_client(db=db)
    db.failing = True
    r = c.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "down", "upstream": "connected"}


def test_health_ping_raises_503_and_leaks_nothing(health_client, caplog):
    db = FakeDatabase()
    db.ping_error = RuntimeError("password=hunter2 postgresql://u:hunter2@h/db")
    c = health_client(db=db)
    with caplog.at_level(logging.DEBUG):
        r = c.get("/health")
    assert r.status_code == 503
    assert set(r.json()) == KEYS and r.json()["database"] == "down"
    assert "hunter2" not in r.text and "hunter2" not in caplog.text
    assert "postgresql://" not in r.text


def test_health_ping_hang_is_bounded(health_client, monkeypatch):
    monkeypatch.setattr("app.db.HEALTH_DB_TIMEOUT_SECONDS", 0.05, raising=False)
    db = FakeDatabase()
    db.ping_hang = True
    c = health_client(db=db)
    t0 = time.monotonic()
    r = c.get("/health")
    assert time.monotonic() - t0 < 1.0
    assert r.status_code == 503 and r.json()["database"] == "down"


def test_health_opt_out_is_disabled_without_probe(health_client):
    db = FakeDatabase()
    c = health_client(db=db, upstream="disabled", persistence=False)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "disabled", "upstream": "disabled"}
    assert db.ping_calls == 0


def test_health_opt_out_real_database(client_no_db):
    # The fixture injects a fake upstream (not connected); the database is "disabled".
    r = client_no_db.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "degraded", "database": "disabled", "upstream": "disconnected"}


@pytest.mark.parametrize(
    "upstream,status", [("connected", "ok"), ("disconnected", "degraded"), ("disabled", "ok")]
)
def test_health_upstream_states(health_client, upstream, status):
    r = health_client(upstream=upstream).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": status, "database": "ok", "upstream": upstream}


@pytest.mark.parametrize("database", ["ok", "down", "disabled"])
@pytest.mark.parametrize("upstream", ["connected", "disconnected", "disabled"])
def test_health_503_iff_database_down(health_client, database, upstream):
    db = FakeDatabase()
    c = health_client(db=db, upstream=upstream, persistence=database != "disabled")
    if database == "down":
        db.failing = True
    r = c.get("/health")
    assert (r.status_code == 503) == (database == "down")
    body = r.json()
    assert set(body) == KEYS and body["database"] == database and body["upstream"] == upstream
    healthy = database in ("ok", "disabled") and upstream in ("connected", "disabled")
    assert body["status"] == ("ok" if healthy else "degraded")


def test_health_upstream_without_connected_attribute(health_client):
    c = health_client()
    del FakeFinnhubClient.instances[0].connected
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["upstream"] == "disconnected"


async def test_database_ping_false_without_pool_and_health_down():
    db = Database("")
    assert await db.ping() is False
    FakeFinnhubClient.instances.clear()
    settings = _settings()
    app = create_app(settings, upstream_factory=_no_upstream)
    app.state.services = Services(settings, db=db)  # lifespan deliberately not run
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await asyncio.wait_for(c.get("/health"), 3)
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "down", "upstream": "disabled"}


# ---- M8: constants and kwargs --------------------------------------------
class _RecConn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *a):
        return "DELETE 1" if sql.startswith("DELETE") else "INSERT 0 1"

    async def executemany(self, sql, rows):
        return None

    async def fetch(self, sql, *a):
        return []

    async def fetchval(self, sql, *a):
        return 0 if "count(*)" in sql else None

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Tx()


class _RecPool:
    """Records the timeout kwarg of every acquire; `fail_batch` makes executemany raise."""

    def __init__(self):
        self.timeouts = []
        self.fail_batch = False

    def acquire(self, *args, **kwargs):
        assert not args
        self.timeouts.append(kwargs.get("timeout", "MISSING"))
        conn = _RecConn()
        if self.fail_batch:
            async def boom(sql, rows):
                raise RuntimeError("batch")

            conn.executemany = boom

        class _Ctx:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *exc):
                return False

        return _Ctx()

    async def close(self):
        return None


def _rule():
    return Rule(symbol="AAPL", type="price_above", threshold=1, created_at=1.0)


def _alert():
    return Alert(rule_id="r", symbol="AAPL", rule_type="price_above", threshold=1, price=2, triggered_at=1)


def test_constants_defined():
    import app.db as d

    assert d.DB_COMMAND_TIMEOUT_SECONDS == 5.0
    assert d.DB_ACQUIRE_TIMEOUT_SECONDS == 5.0
    assert d.DB_STARTUP_TIMEOUT_SECONDS == 30.0
    assert d.HEALTH_DB_TIMEOUT_SECONDS == 2.0


async def test_every_acquire_passes_timeout(monkeypatch):
    monkeypatch.setattr("app.db.DB_ACQUIRE_TIMEOUT_SECONDS", 7.5, raising=False)
    pool = _RecPool()
    db = Database("", persist_ticks=False)
    db._pool = pool
    calls = [
        db.save_rule(_rule()),
        db.delete_rule("x"),
        db.list_rules(),
        db.list_rules("AAPL"),
        db.load_rules(),
        db.create_rule_with_watchlist(_rule(), 10),
        db.load_watchlist(),
        db.list_alerts(),
        db._backfill_watchlist(),
        db._write_alert_rows("sql", [(1,)]),
    ]
    for c in calls:
        await c
    assert pool.timeouts and all(t == 7.5 for t in pool.timeouts), pool.timeouts
    n = len(pool.timeouts)
    # batch fails -> per-row retry also passes the timeout
    pool.fail_batch = True
    await db._write_alert_rows("sql", [(1,), (2,)])
    assert len(pool.timeouts) == n + 1 + 2
    assert all(t == 7.5 for t in pool.timeouts)


async def test_tick_worker_acquire_timeout(monkeypatch):
    monkeypatch.setattr("app.db.DB_ACQUIRE_TIMEOUT_SECONDS", 7.5, raising=False)
    pool = _RecPool()
    db = Database("", persist_ticks=True)
    db._pool = pool
    task = asyncio.create_task(db._tick_worker())
    try:
        db.enqueue_tick(Tick(symbol="AAPL", price=1.0, ts_ms=1))
        for _ in range(100):
            if pool.timeouts:
                break
            await asyncio.sleep(0.01)
        assert pool.timeouts == [7.5]
    finally:
        task.cancel()


async def test_connect_passes_command_timeout_and_acquire_timeout(monkeypatch):
    import asyncpg

    monkeypatch.setattr("app.db.DB_COMMAND_TIMEOUT_SECONDS", 4.25, raising=False)
    monkeypatch.setattr("app.db.DB_ACQUIRE_TIMEOUT_SECONDS", 7.5, raising=False)
    seen = {}
    pool = _RecPool()

    async def fake_create_pool(*args, **kwargs):
        seen.update(kwargs)
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
    db = Database("postgresql://u:p@h/db", persist_ticks=False)
    await db.connect()
    try:
        assert db.available, db.last_error
        assert seen.get("command_timeout") == 4.25
        assert pool.timeouts and all(t == 7.5 for t in pool.timeouts)
    finally:
        await db.close()


# ---- M8: request paths ---------------------------------------------------
class _ModePool:
    """ok | query_timeout | acquire_timeout | acquire_hang. The hang mode only ends
    through the acquire timeout kwarg, like asyncpg's."""

    def __init__(self):
        self.mode = "ok"

    def acquire(self, timeout=None):
        pool = self

        class _Ctx:
            async def __aenter__(self_):
                if pool.mode == "acquire_timeout":
                    raise asyncio.TimeoutError()
                if pool.mode == "acquire_hang":
                    await asyncio.wait_for(asyncio.Event().wait(), timeout)
                return _ModeConn(pool)

            async def __aexit__(self_, *exc):
                return False

        return _Ctx()

    async def close(self):
        return None


class _ModeConn(_RecConn):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool

    def _maybe(self):
        if self.pool.mode == "query_timeout":
            raise asyncio.TimeoutError()

    async def execute(self, sql, *a):
        self._maybe()
        return await super().execute(sql, *a)

    async def fetch(self, sql, *a):
        self._maybe()
        return await super().fetch(sql, *a)

    async def fetchval(self, sql, *a):
        self._maybe()
        return await super().fetchval(sql, *a)


class _StubDb(Database):
    def __init__(self, pool):
        super().__init__("", persist_ticks=False)
        self._stub = pool

    async def connect(self):
        self._pool = self._stub


def _stub_app(pool):
    settings = _settings()
    app = create_app(settings, upstream_factory=_no_upstream, db=_StubDb(pool))
    return app


async def _call(app, method, path, **kw):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=AUTH) as c:
        return await asyncio.wait_for(c.request(method, path, **kw), 3)


@pytest.mark.parametrize("mode", ["query_timeout", "acquire_timeout", "acquire_hang"])
async def test_request_paths_503_on_timeout(monkeypatch, mode):
    monkeypatch.setattr("app.db.DB_ACQUIRE_TIMEOUT_SECONDS", 0.1, raising=False)
    pool = _ModePool()
    app = _stub_app(pool)
    async with app.router.lifespan_context(app):
        services = app.state.services
        existing = _rule()
        services.rule_store.put(existing)
        pool.mode = mode
        t0 = time.monotonic()
        assert (await _call(app, "GET", "/api/rules")).status_code == 503
        assert (await _call(app, "POST", "/api/rules", json=RULE)).status_code == 503
        assert (await _call(app, "DELETE", f"/api/rules/{existing.id}")).status_code == 503
        assert (await _call(app, "GET", "/api/alerts")).status_code == 503
        assert time.monotonic() - t0 < 2.5
        assert existing.id in services.rule_store
        assert services.rule_store.for_symbol("AAPL") == [existing]
        assert services.watchlist == set()
        pool.mode = "ok"
        assert (await _call(app, "GET", "/api/rules")).status_code == 200


# ---- M8: startup ---------------------------------------------------------
class _HangConnectDb(FakeDatabase):
    async def connect(self):
        await asyncio.Event().wait()


class _HangLoadDb(FakeDatabase):
    async def load_rules(self):
        await asyncio.Event().wait()


@pytest.mark.parametrize("db_cls", [_HangConnectDb, _HangLoadDb])
async def test_startup_overall_timeout_fails_loudly(monkeypatch, caplog, db_cls):
    monkeypatch.setattr("app.db.DB_STARTUP_TIMEOUT_SECONDS", 0.1, raising=False)
    FakeFinnhubClient.instances.clear()
    settings = Settings(
        _env_file=None, finnhub_api_key="",
        database_url="postgresql://user:SENTINELPW@h:5432/db", app_api_key=TEST_KEY,
    )
    db = db_cls()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=db)

    async def run():
        async with app.router.lifespan_context(app):
            pass

    with caplog.at_level(logging.ERROR, logger="app"):
        with pytest.raises(Exception):
            await asyncio.wait_for(run(), 3)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    startup = [m for m in errors if m.startswith("startup failed:")]
    assert len(startup) == 1 and "timed out" in startup[0]
    assert "RULES_PERSISTENCE_ENABLED=false" in startup[0]
    assert "SENTINELPW" not in caplog.text
    assert not FakeFinnhubClient.instances
    assert db.close_calls == 1


# ---- M8: writers ---------------------------------------------------------
class _WriterConn:
    def __init__(self, pool):
        self.pool = pool

    async def executemany(self, sql, rows):
        self.pool.attempts += 1
        if self.pool.mode == "timeout":
            raise asyncio.TimeoutError()
        if self.pool.mode == "block":
            await asyncio.Event().wait()
        self.pool.rows.extend(rows)

    async def execute(self, sql, *row):
        if self.pool.mode == "timeout":
            raise asyncio.TimeoutError()
        self.pool.rows.append(row)


class _WriterPool:
    def __init__(self):
        self.mode = "timeout"
        self.rows = []
        self.attempts = 0

    def acquire(self, timeout=None):
        conn = _WriterConn(self)

        class _Ctx:
            async def __aenter__(self_):
                return conn

            async def __aexit__(self_, *exc):
                return False

        return _Ctx()

    async def close(self):
        return None


async def test_alert_writer_survives_timeouts_and_recovers(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 0.02)
    pool = _WriterPool()
    db = Database("")
    db._pool = pool
    db._alert_worker = asyncio.create_task(db._alert_writer())
    try:
        db.enqueue_alert(_alert())
        for _ in range(100):
            if pool.attempts:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        assert pool.rows == [] and not db._alert_worker.done()
        pool.mode = "ok"
        db.enqueue_alert(_alert())
        for _ in range(100):
            if pool.rows:
                break
            await asyncio.sleep(0.01)
        assert len(pool.rows) == 1 and not db._alert_worker.done()
    finally:
        db._alert_worker.cancel()


async def test_on_tick_does_not_wait_for_stuck_writer():
    pool = _WriterPool()
    pool.mode = "block"
    settings = _settings()
    db = Database("")
    db._pool = pool
    db._alert_worker = asyncio.create_task(db._alert_writer())
    services = Services(settings, db=db)
    try:
        db.enqueue_alert(_alert())
        for _ in range(100):
            if pool.attempts:
                break
            await asyncio.sleep(0.01)
        services.rule_store.put(
            Rule(symbol="AAPL", type="price_above", threshold=100, created_at=1.0)
        )
        await asyncio.wait_for(services.on_tick(Tick(symbol="AAPL", price=99.0, ts_ms=1)), 1)
        await asyncio.wait_for(services.on_tick(Tick(symbol="AAPL", price=101.0, ts_ms=2)), 1)
        assert db._alert_queue.qsize() == 1
    finally:
        db._alert_worker.cancel()
