"""Fast tests (fake DB + fake Finnhub): watchlist/upstream coordination, cap,
alert enqueueing and GET /api/alerts."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.main import Services, create_app
from app.models import Alert, Rule, Tick
from tests.fakes.fake_db import FakeDatabase
from tests.fakes.fake_finnhub import FakeFinnhubClient

RULE = {"symbol": "AAPL", "type": "price_above", "threshold": 100}


def _rule(symbol="AAPL"):
    return Rule(symbol=symbol, type="price_above", threshold=100)


def _services(watchlist=()):
    s = Services(Settings(_env_file=None, finnhub_api_key="", database_url=""), db=FakeDatabase())
    s.upstream = FakeFinnhubClient(None, None)
    s.watchlist = set(watchlist)
    return s


# ---- cap / create rule ---------------------------------------------------
@pytest.fixture
def small_client(fake_db):
    FakeFinnhubClient.instances.clear()
    settings = Settings(_env_file=None, finnhub_api_key="", database_url="", watchlist_max_symbols=2)
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=fake_db)
    with TestClient(app) as c:
        yield c


def test_settings_default_and_env(monkeypatch):
    assert Settings(_env_file=None).watchlist_max_symbols == 50
    monkeypatch.setenv("WATCHLIST_MAX_SYMBOLS", "7")
    assert Settings(_env_file=None).watchlist_max_symbols == 7


def test_create_rule_watches_normalized_symbol_and_subscribes_once(client, fake_db):
    up = FakeFinnhubClient.instances[0]
    r = client.post("/api/rules", json={**RULE, "symbol": " aapl "})
    assert r.status_code == 201
    assert fake_db.watchlist == {"AAPL"}
    assert up.subscribed == ["AAPL"]
    client.post("/api/rules", json={**RULE, "threshold": 200})
    assert up.subscribed == ["AAPL"]
    assert client.app.state.services.watchlist == {"AAPL"}


def test_cap_returns_409_and_saves_nothing(small_client, fake_db):
    up = FakeFinnhubClient.instances[0]
    for sym in ("A", "B"):
        assert small_client.post("/api/rules", json={**RULE, "symbol": sym}).status_code == 201
    r = small_client.post("/api/rules", json={**RULE, "symbol": "C"})
    assert r.status_code == 409
    assert len(fake_db.rules) == 2 and fake_db.watchlist == {"A", "B"}
    svc = small_client.app.state.services
    assert svc.watchlist == {"A", "B"} and len(svc.rule_store.for_symbol("C")) == 0
    assert up.subscribed == ["A", "B"]
    # already-watched symbol at the cap still works
    assert small_client.post("/api/rules", json={**RULE, "symbol": "A"}).status_code == 201


def test_create_rule_db_failure_changes_nothing(client, fake_db):
    up = FakeFinnhubClient.instances[0]
    fake_db.failing = True
    r = client.post("/api/rules", json=RULE)
    assert r.status_code == 503 and r.json()["detail"] == "rule storage unavailable"
    svc = client.app.state.services
    assert svc.watchlist == set() and svc.rule_store.for_symbol("AAPL") == []
    assert up.subscribed == []


def test_upstream_subscribe_failure_still_201(client, caplog):
    up = FakeFinnhubClient.instances[0]

    async def boom(symbol):
        raise RuntimeError("secret detail")

    up.subscribe = boom
    r = client.post("/api/rules", json=RULE)
    assert r.status_code == 201
    assert "secret detail" not in caplog.text


def test_browser_holding_symbol_means_rule_sends_no_subscribe(client):
    up = FakeFinnhubClient.instances[0]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "aapl"})
        assert ws.receive_json()["type"] == "subscribed"
        assert up.subscribed == ["AAPL"]
        assert client.post("/api/rules", json=RULE).status_code == 201
        assert up.subscribed == ["AAPL"]
    assert up.unsubscribed == []  # watched symbol is kept upstream


def test_no_upstream_rule_creation_works(settings, fake_db):
    app = create_app(settings, upstream_factory=lambda *a: None, db=fake_db)
    with TestClient(app) as c:
        assert c.post("/api/rules", json=RULE).status_code == 201


# ---- coordination (Services level) ---------------------------------------
async def test_browser_on_watched_symbol_no_traffic():
    s = _services(watchlist={"AAPL"})
    await s.subscriptions.subscribe("c1", "AAPL")
    await s.subscriptions.subscribe("c2", "AAPL")
    await s.subscriptions.disconnect("c1")
    await s.subscriptions.unsubscribe("c2", "AAPL")
    await s.subscriptions.subscribe("c2", "AAPL")
    assert s.upstream.subscribed == [] and s.upstream.unsubscribed == []


async def test_browser_on_unwatched_symbol_subscribes_and_unsubscribes_once():
    s = _services()
    await s.subscriptions.subscribe("c1", "MSFT")
    await s.subscriptions.subscribe("c2", "MSFT")
    assert s.upstream.subscribed == ["MSFT"]
    await s.subscriptions.disconnect("c1")
    assert s.upstream.unsubscribed == []
    await s.subscriptions.disconnect("c2")
    assert s.upstream.unsubscribed == ["MSFT"]


async def test_rule_on_browser_held_symbol_then_browser_leaves():
    s = _services()
    await s.subscriptions.subscribe("c1", "AAPL")
    await s.watch_symbol("AAPL")
    await s.subscriptions.disconnect("c1")
    assert s.upstream.subscribed == ["AAPL"] and s.upstream.unsubscribed == []


class BlockingUpstream(FakeFinnhubClient):
    def __init__(self):
        super().__init__(None, None)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def subscribe(self, symbol):
        self.subscribed.append(symbol)
        self.entered.set()
        await self.release.wait()


async def test_interleaving_browser_suspended_in_upstream_while_rule_created():
    s = _services()
    s.upstream = BlockingUpstream()
    task = asyncio.create_task(s.subscriptions.subscribe("c1", "AAPL"))
    await s.upstream.entered.wait()
    await s.watch_symbol("AAPL")  # must not subscribe again
    s.upstream.release.set()
    await task
    assert s.upstream.subscribed == ["AAPL"]


async def test_interleaving_rule_first_then_browser():
    s = _services()
    s.upstream = BlockingUpstream()
    task = asyncio.create_task(s.watch_symbol("AAPL"))
    await s.upstream.entered.wait()
    await s.subscriptions.subscribe("c1", "AAPL")  # watched: no second subscribe
    s.upstream.release.set()
    await task
    assert s.upstream.subscribed == ["AAPL"]


async def test_get_symbols_is_sorted_union_and_connect_subscribes_each_once():
    s = _services(watchlist={"A", "B"})
    await s.subscriptions.subscribe("c1", "B")
    await s.subscriptions.subscribe("c1", "C")
    assert s.upstream_symbols() == ["A", "B", "C"]


def test_factory_receives_union_callable(fake_db):
    fake_db.watchlist = {"A", "B"}
    FakeFinnhubClient.instances.clear()
    settings = Settings(_env_file=None, finnhub_api_key="", database_url="")
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=fake_db)
    with TestClient(app) as c:
        up = FakeFinnhubClient.instances[0]
        assert up.get_symbols() == ["A", "B"]
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "symbol": "b"})
            ws.receive_json()
            ws.send_json({"action": "subscribe", "symbol": "c"})
            ws.receive_json()
            assert up.get_symbols() == ["A", "B", "C"]


def test_startup_db_down_empty_watchlist_health_ok(settings):
    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=FakeDatabase(failing=True))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.app.state.services.watchlist == set()
        assert FakeFinnhubClient.instances[0].get_symbols() == []


# ---- alerts on tick -------------------------------------------------------
def _tick(c, price, ts=1):
    up = FakeFinnhubClient.instances[0]
    c.portal.call(up.on_tick, Tick(symbol="AAPL", price=price, ts_ms=ts))


def test_alert_enqueue_edge_triggered(client, fake_db):
    rid = client.post("/api/rules", json=RULE).json()["id"]
    _tick(client, 99)
    assert fake_db.alerts == []
    _tick(client, 101)
    assert len(fake_db.alerts) == 1
    a = fake_db.alerts[0]
    assert (a.rule_id, a.symbol, a.rule_type, a.threshold, a.price) == (rid, "AAPL", "price_above", 100, 101)
    assert a.id and a.triggered_at > 0
    _tick(client, 102)
    assert len(fake_db.alerts) == 1
    _tick(client, 99)
    _tick(client, 105)
    assert len(fake_db.alerts) == 2
    assert len({x.id for x in fake_db.alerts}) == 2


def test_multiple_rules_one_alert_each(client, fake_db):
    client.post("/api/rules", json=RULE)
    client.post("/api/rules", json={**RULE, "threshold": 50})
    _tick(client, 101)
    assert len(fake_db.alerts) == 2


def test_enqueue_failure_does_not_stop_broadcast(client, fake_db):
    client.post("/api/rules", json=RULE)
    fake_db.enqueue_error = RuntimeError("boom")
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "symbol": "AAPL"})
        assert ws.receive_json()["type"] == "subscribed"
        _tick(client, 101)
        assert ws.receive_json()["price"] == 101


def test_alert_queue_full_drops_with_warning(caplog):
    db = Database("", alert_queue_size=1)
    db._pool = object()  # pretend connected; no worker consumes the queue
    a = Alert(rule_id="r", symbol="AAPL", rule_type="price_above", threshold=1, price=2, triggered_at=3)
    b = a.model_copy(update={"id": "other"})
    with caplog.at_level(logging.WARNING, logger="app.db"):
        db.enqueue_alert(a)
        db.enqueue_alert(b)  # must not raise
    assert db._alert_queue.qsize() == 1
    assert "alert queue full" in caplog.text


def test_enqueue_without_pool_is_noop():
    db = Database("")
    db.enqueue_alert(Alert(rule_id="r", symbol="A", rule_type="t", threshold=1, price=2, triggered_at=3))
    assert db._alert_queue.qsize() == 0


# ---- GET /api/alerts ------------------------------------------------------
def _alert(id, t, symbol="AAPL"):
    return Alert(id=id, rule_id="r", symbol=symbol, rule_type="price_above",
                 threshold=100, price=101, triggered_at=t)


def test_list_alerts_order_limit_and_shape(client, fake_db):
    assert client.get("/api/alerts").json() == []
    fake_db.alerts = [_alert("a", 1), _alert("b", 3), _alert("c", 3), _alert("d", 2)]
    body = client.get("/api/alerts").json()
    assert [x["id"] for x in body] == ["c", "b", "d", "a"]
    assert set(body[0]) == {"id", "rule_id", "symbol", "rule_type", "threshold", "price", "triggered_at"}
    assert [x["id"] for x in client.get("/api/alerts", params={"limit": 2}).json()] == ["c", "b"]
    assert client.get("/api/alerts", params={"limit": 1}).status_code == 200
    assert client.get("/api/alerts", params={"limit": 500}).status_code == 200


@pytest.mark.parametrize("limit", ["0", "501", "abc"])
def test_list_alerts_bad_limit_422(client, limit):
    assert client.get("/api/alerts", params={"limit": limit}).status_code == 422


def test_list_alerts_503(client, client_no_db, fake_db):
    fake_db.failing = True
    r = client.get("/api/alerts")
    assert r.status_code == 503 and r.json()["detail"] == "alert storage unavailable"
    assert client_no_db.get("/api/alerts").status_code == 503
