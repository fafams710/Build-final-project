"""Real-Postgres tests for rule persistence.

Set TEST_DATABASE_URL to a DISPOSABLE database: these tests DROP the
`alert_rules` and `ticks` tables before and after every test.
"""
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database, DatabaseUnavailable
from app.main import create_app
from app.models import Rule, Tick
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


@pytest.fixture(autouse=True)
async def clean_tables():
    await _exec("DROP TABLE IF EXISTS alert_rules", "DROP TABLE IF EXISTS ticks")
    yield
    await _exec("DROP TABLE IF EXISTS alert_rules", "DROP TABLE IF EXISTS ticks")


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
