"""Real-Postgres tests for rule persistence.

Set TEST_DATABASE_URL to a DISPOSABLE database: these tests DROP the
`alert_rules`, `ticks`, `watchlist` and `alerts` tables before and after every test.
"""
import asyncio
import os
import time

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database, DatabaseUnavailable, WatchlistFull
from app.main import create_app
from app.models import Alert, Rule, Tick
from tests.fakes.fake_finnhub import FakeFinnhubClient

URL = os.environ.get("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not URL, reason="TEST_DATABASE_URL not set; real-Postgres tests skipped"
)


async def _exec(*statements: str):
    conn = await asyncpg.connect(URL)
    try:
        for s in statements:
            await conn.execute(s)
    finally:
        await conn.close()


_DROPS = (
    "DROP TABLE IF EXISTS alert_rules",
    "DROP TABLE IF EXISTS ticks",
    "DROP TABLE IF EXISTS watchlist",
    "DROP TABLE IF EXISTS alerts",
)


@pytest.fixture(autouse=True)
async def clean_tables():
    await _exec(*_DROPS)
    yield
    await _exec(*_DROPS)


@pytest.fixture
async def db():
    d = Database(URL, persist_ticks=False)
    await d.connect()
    assert d.available
    yield d
    await d.close()


def _rule(symbol="AAPL", created_at=1000.0, **kw):
    return Rule(symbol=symbol, type="price_above", threshold=100, created_at=created_at, **kw)


async def test_round_trip_order_limit_filter_delete(db):
    r1, r2, r3 = _rule(created_at=1.0), _rule("MSFT", 2.0), _rule(created_at=3.0)
    for r in (r1, r2, r3):
        await db.save_rule(r)
    assert [r.id for r in await db.list_rules(None, 50)] == [r3.id, r2.id, r1.id]
    assert [r.id for r in await db.list_rules(None, 1)] == [r3.id]
    assert [r.id for r in await db.list_rules("AAPL", 50)] == [r3.id, r1.id]
    assert [r.id for r in await db.list_rules("AAPL", 1)] == [r3.id]
    assert {r.id for r in await db.load_rules()} == {r1.id, r2.id, r3.id}
    assert await db.delete_rule(r1.id) is True
    assert await db.delete_rule(r1.id) is False
    assert len(await db.list_rules(None, 50)) == 2


async def test_null_window_round_trips(db):
    await db.save_rule(_rule())
    assert (await db.list_rules(None, 5))[0].window_seconds is None


async def test_unavailable_database_raises():
    d = Database("postgresql://u:p@127.0.0.1:1/none")
    await d.connect()
    assert not d.available
    with pytest.raises(DatabaseUnavailable):
        await d.save_rule(_rule())
    with pytest.raises(DatabaseUnavailable):
        await d.delete_rule("x")
    with pytest.raises(DatabaseUnavailable):
        await d.list_rules(None, 5)
    with pytest.raises(DatabaseUnavailable):
        await d.load_rules()
    await d.close()


async def test_connect_drops_webhook_url_and_keeps_rows():
    await _exec(
        "CREATE TABLE alert_rules ("
        "id TEXT PRIMARY KEY, symbol TEXT NOT NULL, type TEXT NOT NULL, "
        "threshold DOUBLE PRECISION NOT NULL, window_seconds DOUBLE PRECISION, "
        "created_at DOUBLE PRECISION NOT NULL, webhook_url TEXT NOT NULL)",
        "INSERT INTO alert_rules VALUES ('old','AAPL','price_above',1,NULL,5,'http://x')",
    )
    d = Database(URL, persist_ticks=False)
    await d.connect()
    try:
        conn = await asyncpg.connect(URL)
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'alert_rules'"
            )
        finally:
            await conn.close()
        assert "webhook_url" not in {c["column_name"] for c in cols}
        await d.save_rule(_rule())  # no NOT NULL violation
        assert "old" in {r.id for r in await d.list_rules(None, 50)}
        assert "old" in {r.id for r in await d.load_rules()}
    finally:
        await d.close()
    d2 = Database(URL, persist_ticks=False)  # idempotent second run
    await d2.connect()
    assert d2.available
    await d2.close()


def _app():
    FakeFinnhubClient.instances.clear()
    settings = Settings(_env_file=None, finnhub_api_key="", database_url=URL)
    return create_app(settings, upstream_factory=FakeFinnhubClient.factory)


def test_api_round_trip():
    body = {"symbol": "aapl", "type": "price_above", "threshold": 100}
    with TestClient(_app()) as c:
        r = c.post("/api/rules", json=body)
        assert r.status_code == 201
        rid = r.json()["id"]
        assert [x["id"] for x in c.get("/api/rules").json()] == [rid]
        assert c.delete(f"/api/rules/{rid}").status_code == 204
        assert c.get("/api/rules").json() == []
        assert c.delete(f"/api/rules/{rid}").status_code == 404


def test_restart_reloads_rules_and_engine_fires():
    body = {"symbol": "AAPL", "type": "price_above", "threshold": 100}
    with TestClient(_app()) as c:
        rid = c.post("/api/rules", json=body).json()["id"]
    with TestClient(_app()) as c:
        assert [x["id"] for x in c.get("/api/rules").json()] == [rid]
        services = c.app.state.services
        assert rid in services.rule_store
        fired = services.engine.evaluate(Tick(symbol="AAPL", price=101.0, ts_ms=1))
        assert [rule.id for rule, _ in fired] == [rid]


# ---- watchlist / alerts (T008) ------------------------------------------
async def _fetch(sql: str, *args):
    conn = await asyncpg.connect(URL)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _wait_for(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


def _alert(id="a1", t=10.0, rule_id="r1"):
    return Alert(id=id, rule_id=rule_id, symbol="AAPL", rule_type="price_above",
                 threshold=100.0, price=101.5, triggered_at=t)


async def test_schema_has_watchlist_alerts_index_and_no_fk(db):
    tables = {r["table_name"] for r in await _fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert {"watchlist", "alerts"} <= tables
    idx = await _fetch("SELECT indexdef FROM pg_indexes WHERE tablename='alerts'")
    assert any("triggered_at DESC" in r["indexdef"] for r in idx)
    fks = await _fetch("SELECT 1 FROM pg_constraint WHERE contype='f'")
    assert fks == []
    d2 = Database(URL, persist_ticks=False)  # second startup does not error
    await d2.connect()
    assert d2.available
    await d2.close()


async def test_create_rule_rolls_back_watchlist_on_rule_failure(db):
    r = _rule()
    assert await db.create_rule_with_watchlist(r, 5) is True
    dup = _rule("MSFT")
    dup.id = r.id  # forces the alert_rules INSERT to fail
    with pytest.raises(Exception):
        await db.create_rule_with_watchlist(dup, 5)
    assert await db.load_watchlist() == ["AAPL"]
    assert await db.create_rule_with_watchlist(_rule(), 5) is False


async def test_cap_enforced_in_db(db):
    await db.create_rule_with_watchlist(_rule("A"), 2)
    await db.create_rule_with_watchlist(_rule("B"), 2)
    with pytest.raises(WatchlistFull):
        await db.create_rule_with_watchlist(_rule("C"), 2)
    await db.create_rule_with_watchlist(_rule("A"), 2)
    assert await db.load_watchlist() == ["A", "B"]
    assert len(await db.list_rules(None, 50)) == 3


async def test_concurrent_creations_at_cap_minus_one(db):
    await db.create_rule_with_watchlist(_rule("A"), 2)
    results = await asyncio.gather(
        db.create_rule_with_watchlist(_rule("B"), 2),
        db.create_rule_with_watchlist(_rule("C"), 2),
        return_exceptions=True,
    )
    assert sum(r is True for r in results) == 1
    assert sum(isinstance(r, WatchlistFull) for r in results) == 1
    assert len(await db.load_watchlist()) == 2


async def test_alert_worker_persists_and_survives_failure(db):
    a = _alert("a1", 10.0)
    db.enqueue_alert(a)

    async def got(n):
        rows = await db.list_alerts(50)
        return rows if len(rows) >= n else None

    rows = await _wait_for(lambda: got(1))
    assert rows[0] == a
    db.enqueue_alert(_alert("a1", 11.0))  # duplicate id: insert fails
    db.enqueue_alert(_alert("a2", 12.0))
    rows = await _wait_for(lambda: got(2))
    assert [x.id for x in rows] == ["a2", "a1"]


async def test_alerts_survive_rule_deletion_via_api():
    body = {"symbol": "AAPL", "type": "price_above", "threshold": 100}
    with TestClient(_app()) as c:
        rid = c.post("/api/rules", json=body).json()["id"]
        db = c.app.state.services.db
        db.enqueue_alert(_alert("a1", 10.0, rule_id=rid))
        deadline = time.time() + 5
        while time.time() < deadline and not c.get("/api/alerts").json():
            time.sleep(0.02)
        assert c.delete(f"/api/rules/{rid}").status_code == 204
        got = c.get("/api/alerts").json()
        assert len(got) == 1
        assert got[0]["rule_id"] == rid and got[0]["rule_type"] == "price_above"
        assert got[0]["threshold"] == 100.0


def test_restart_reloads_watchlist_and_subscribes():
    body = {"symbol": "aapl", "type": "price_above", "threshold": 100}
    with TestClient(_app()) as c:
        assert c.post("/api/rules", json=body).status_code == 201
        assert FakeFinnhubClient.instances[0].subscribed == ["AAPL"]
    with TestClient(_app()) as c:
        up = FakeFinnhubClient.instances[0]
        assert c.app.state.services.watchlist == {"AAPL"}
        assert "AAPL" in up.get_symbols()


async def test_preexisting_rule_backfilled_into_watchlist():
    await _exec(
        "CREATE TABLE alert_rules ("
        "id TEXT PRIMARY KEY, symbol TEXT NOT NULL, type TEXT NOT NULL, "
        "threshold DOUBLE PRECISION NOT NULL, window_seconds DOUBLE PRECISION, "
        "created_at DOUBLE PRECISION NOT NULL)",
        "INSERT INTO alert_rules VALUES ('old','MSFT','price_above',1,NULL,5)",
        "INSERT INTO alert_rules VALUES ('old2','msft','price_below',1,NULL,6)",
    )
    d = Database(URL, persist_ticks=False)
    await d.connect()
    try:
        assert await d.load_watchlist() == ["MSFT"]
    finally:
        await d.close()
    d2 = Database(URL, persist_ticks=False)  # idempotent
    await d2.connect()
    try:
        assert await d2.load_watchlist() == ["MSFT"]
    finally:
        await d2.close()


def test_api_cap_409_with_real_db():
    FakeFinnhubClient.instances.clear()
    settings = Settings(_env_file=None, finnhub_api_key="", database_url=URL,
                        watchlist_max_symbols=1)
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory)
    with TestClient(app) as c:
        assert c.post("/api/rules", json={"symbol": "A", "type": "price_above", "threshold": 1}).status_code == 201
        assert c.post("/api/rules", json={"symbol": "B", "type": "price_above", "threshold": 1}).status_code == 409
        assert [r["symbol"] for r in c.get("/api/rules").json()] == ["A"]


# ---- T009: fail-loud startup ---------------------------------------------
def test_startup_fails_loudly_when_postgres_unreachable():
    FakeFinnhubClient.instances.clear()
    settings = Settings(_env_file=None, finnhub_api_key="",
                        database_url="postgresql://u:p@127.0.0.1:1/none")
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
    assert all(not i.start_calls for i in FakeFinnhubClient.instances)


async def test_backfill_failure_leaves_database_unavailable_and_startup_fails():
    # Pre-existing watchlist without created_at: schema is IF NOT EXISTS, so
    # the backfill INSERT fails.
    await _exec(
        "CREATE TABLE watchlist (symbol TEXT PRIMARY KEY)",
        "CREATE TABLE alert_rules ("
        "id TEXT PRIMARY KEY, symbol TEXT NOT NULL, type TEXT NOT NULL, "
        "threshold DOUBLE PRECISION NOT NULL, window_seconds DOUBLE PRECISION, "
        "created_at DOUBLE PRECISION NOT NULL)",
        "INSERT INTO alert_rules VALUES ('old','MSFT','price_above',1,NULL,5)",
    )
    d = Database(URL, persist_ticks=False)
    await d.connect()
    assert not d.available
    assert d._alert_worker is None
    await d.close()
    with pytest.raises(RuntimeError):
        with TestClient(_app()):
            pass
