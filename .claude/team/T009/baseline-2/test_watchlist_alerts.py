"""Fast tests (fake DB + fake Finnhub): watchlist/upstream coordination, cap,
alert enqueueing and GET /api/alerts."""
import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import ALERT_DROP_WARN_INTERVAL_SECONDS, Database
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
    s._upstream_sent = set(watchlist)  # as if the connect-time resubscribe already ran
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
    # watch_symbol queues behind the in-flight subscribe (it cannot be awaited
    # inline); it must find AAPL already sent and not subscribe again.
    rule = asyncio.create_task(s.watch_symbol("AAPL"))
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(task, rule), timeout=5)
    assert s.upstream.subscribed == ["AAPL"]


async def test_interleaving_rule_first_then_browser():
    s = _services()
    s.upstream = BlockingUpstream()
    task = asyncio.create_task(s.watch_symbol("AAPL"))
    await s.upstream.entered.wait()
    # queues behind the in-flight rule subscribe; AAPL is then already sent
    browser = asyncio.create_task(s.subscriptions.subscribe("c1", "AAPL"))
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(task, browser), timeout=5)
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


def _failing_start(settings, db, caplog):
    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=db)
    with caplog.at_level(logging.ERROR, logger="app"):
        with pytest.raises(Exception):
            with TestClient(app):
                pass
    assert [r for r in caplog.records if r.levelno >= logging.ERROR and "startup failed" in r.getMessage()]
    assert all(not i.start_calls for i in FakeFinnhubClient.instances)
    assert db.close_calls == 1


def test_startup_db_down_fails_loudly(settings, caplog):
    _failing_start(settings, FakeDatabase(failing=True), caplog)


def test_startup_rule_load_failure_fails_loudly(settings, caplog):
    db = FakeDatabase()
    db.load_rules_error = RuntimeError("boom")
    _failing_start(settings, db, caplog)


def test_startup_watchlist_load_failure_fails_loudly(settings, caplog):
    db = FakeDatabase()
    db.load_watchlist_error = RuntimeError("boom")
    _failing_start(settings, db, caplog)


def test_startup_opt_out_starts_empty_and_503():
    FakeFinnhubClient.instances.clear()
    opt_out = Settings(_env_file=None, finnhub_api_key="", database_url="",
                       rules_persistence_enabled=False)
    app = create_app(opt_out, upstream_factory=FakeFinnhubClient.factory)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.app.state.services.watchlist == set()
        assert c.get("/api/rules").status_code == 503
        assert c.get("/api/alerts").status_code == 503
        assert c.post("/api/rules", json=RULE).status_code == 503


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


def test_alert_queue_full_warning_is_rate_limited(caplog, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("app.db.time.monotonic", lambda: clock[0])
    db = Database("", alert_queue_size=1)
    db._pool = object()
    a = Alert(rule_id="r", symbol="AAPL", rule_type="price_above", threshold=1, price=2, triggered_at=3)

    def warnings():
        return [r.getMessage() for r in caplog.records
                if r.name == "app.db" and "alert queue full" in r.getMessage()]

    with caplog.at_level(logging.WARNING, logger="app.db"):
        db.enqueue_alert(a)  # fills the queue
        for _ in range(50):
            db.enqueue_alert(a)  # never raises
        first = warnings()
        assert len(first) == 1 and "dropped 1 " in first[0]
        assert db._alert_queue.qsize() == 1
        clock[0] += ALERT_DROP_WARN_INTERVAL_SECONDS + 1
        db.enqueue_alert(a)
        second = warnings()
        assert len(second) == 2 and "dropped 50 " in second[1]


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


# ---- iteration 2: upstream ordering race ---------------------------------
class UnsubBlockingUpstream(FakeFinnhubClient):
    """Unsubscribe blocks until released; effects are recorded at completion."""

    def __init__(self):
        super().__init__(None, None)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.effects: list[tuple[str, str]] = []
        self.state: set[str] = set()
        self.dup_subscribes: list[str] = []

    async def subscribe(self, symbol):
        if symbol in self.state:
            self.dup_subscribes.append(symbol)
        self.state.add(symbol)
        self.effects.append(("sub", symbol))

    async def unsubscribe(self, symbol):
        self.entered.set()
        await self.release.wait()
        self.state.discard(symbol)
        self.effects.append(("unsub", symbol))


async def _yield(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_rule_created_while_last_browser_unsubscribe_in_flight():
    s = _services()
    s.upstream = UnsubBlockingUpstream()
    await s.subscriptions.subscribe("c1", "AAPL")
    t1 = asyncio.create_task(s.subscriptions.disconnect("c1"))
    await asyncio.wait_for(s.upstream.entered.wait(), timeout=5)
    t2 = asyncio.create_task(s.watch_symbol("AAPL"))
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
    assert "AAPL" in s.watchlist
    assert s.upstream.state == {"AAPL"}
    assert s.upstream.effects[-1] == ("sub", "AAPL")
    assert s.upstream.dup_subscribes == []


async def test_unsub_in_flight_rule_then_browser_resubscribes_no_extra_traffic():
    s = _services()
    s.upstream = UnsubBlockingUpstream()
    await s.subscriptions.subscribe("c1", "AAPL")
    t1 = asyncio.create_task(s.subscriptions.disconnect("c1"))
    await asyncio.wait_for(s.upstream.entered.wait(), timeout=5)
    t2 = asyncio.create_task(s.watch_symbol("AAPL"))
    t3 = asyncio.create_task(s.subscriptions.subscribe("c2", "AAPL"))
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(t1, t2, t3), timeout=5)
    assert s.upstream.state == {"AAPL"}
    assert s.upstream.effects == [("sub", "AAPL"), ("unsub", "AAPL"), ("sub", "AAPL")]
    assert s.upstream.dup_subscribes == []


async def test_concurrent_watch_symbol_subscribes_once():
    s = _services()
    s.upstream = BlockingUpstream()
    t1 = asyncio.create_task(s.watch_symbol("AAPL"))
    await asyncio.wait_for(s.upstream.entered.wait(), timeout=5)
    t2 = asyncio.create_task(s.watch_symbol("AAPL"))
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
    assert s.upstream.subscribed == ["AAPL"]


async def test_upstream_failure_in_lock_does_not_wedge_later_calls():
    s = _services()
    calls = []

    async def boom(symbol):
        calls.append(symbol)
        raise RuntimeError("x")

    s.upstream.subscribe = boom
    await asyncio.wait_for(s.watch_symbol("AAPL"), timeout=5)
    await asyncio.wait_for(s.watch_symbol("MSFT"), timeout=5)
    assert calls == ["AAPL", "MSFT"]


# ---- iteration 2: alert drain / backoff ----------------------------------
class _Conn:
    def __init__(self, pool):
        self.pool = pool

    async def executemany(self, sql, rows):
        await self.pool.on_executemany(rows)

    async def execute(self, sql, *row):
        await self.pool.on_execute(row)


class _Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return _Conn(self.pool)

    async def __aexit__(self, *exc):
        return False


class StubPool:
    def __init__(self):
        self.rows: list = []
        self.executemany_calls = 0
        self.closed_after_rows: int | None = None
        self.mode = "ok"  # ok | fail | block

    def acquire(self):
        return _Acquire(self)

    async def on_executemany(self, rows):
        self.executemany_calls += 1
        if self.mode == "block":
            await asyncio.Event().wait()
        if self.mode in ("fail", "batch_fail"):
            raise RuntimeError("down")
        self.rows.extend(rows)

    async def on_execute(self, row):
        if self.mode == "fail":
            raise RuntimeError("down")
        self.rows.append(row)

    async def close(self):
        self.closed_after_rows = len(self.rows)


def _db_with_stub(pool):
    db = Database("")
    db._pool = pool
    db._alert_worker = asyncio.create_task(db._alert_writer())
    return db


def _a(i):
    return Alert(rule_id="r", symbol="AAPL", rule_type="price_above", threshold=1, price=2, triggered_at=i)


async def test_close_drains_queued_alerts_before_pool_close():
    pool = StubPool()
    db = _db_with_stub(pool)
    for i in range(5):
        db.enqueue_alert(_a(i))
    await asyncio.wait_for(db.close(), timeout=5)
    assert len(pool.rows) == 5 and pool.closed_after_rows == 5


async def test_close_is_bounded_when_write_blocks(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_DRAIN_TIMEOUT_SECONDS", 0.1)
    pool = StubPool()
    pool.mode = "block"
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    await asyncio.sleep(0)
    await asyncio.wait_for(db.close(), timeout=5)
    assert pool.closed_after_rows == 0


async def test_close_edge_cases():
    db = Database("")
    await asyncio.wait_for(db.close(), timeout=5)  # no pool, no worker
    pool = StubPool()
    db = _db_with_stub(pool)
    await asyncio.wait_for(db.close(), timeout=5)  # empty queue
    await asyncio.wait_for(db.close(), timeout=5)  # twice


async def test_failed_batch_backs_off_and_stays_bounded(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 0.05)
    pool = StubPool()
    pool.mode = "fail"
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    for _ in range(50):
        await asyncio.sleep(0)
    assert pool.executemany_calls == 1  # writer is sleeping in backoff
    # enqueue while sleeping returns immediately; written after recovery
    pool.mode = "ok"
    db.enqueue_alert(_a(2))
    assert db._alert_queue.qsize() == 1
    await asyncio.sleep(0.2)
    assert len(pool.rows) == 1
    await asyncio.wait_for(db.close(), timeout=5)


async def test_successful_write_does_not_sleep(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 30)
    pool = StubPool()
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    db.enqueue_alert(_a(2))
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(pool.rows) == 2
    await asyncio.wait_for(db.close(), timeout=5)


async def test_close_during_backoff_is_not_delayed(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 30)
    pool = StubPool()
    pool.mode = "fail"
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    for _ in range(50):
        await asyncio.sleep(0)
    await asyncio.wait_for(db.close(), timeout=5)
    assert pool.closed_after_rows == 0


# ---- iteration 2: logging --------------------------------------------------
def test_app_logger_info_and_idempotent_handlers():
    create_app()
    app_log = logging.getLogger("app")
    assert app_log.getEffectiveLevel() <= logging.INFO
    count = len(app_log.handlers)
    if count:
        assert app_log.propagate is False
    root_level = logging.getLogger().level
    uvicorn_levels = [logging.getLogger(n).level for n in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    create_app()
    assert len(app_log.handlers) == count
    assert logging.getLogger().level == root_level
    assert [logging.getLogger(n).level for n in ("uvicorn", "uvicorn.error", "uvicorn.access")] == uvicorn_levels


def test_rule_fired_info_record_emitted(client, caplog):
    client.post("/api/rules", json=RULE)
    with caplog.at_level(logging.INFO, logger="app"):
        _tick(client, 101)
    assert "rule fired" in caplog.text


# ---- iteration 3: sent-set reconciliation ---------------------------------
class SlowFirstUpstream(FakeFinnhubClient):
    """Subscribe of `slow_symbol` blocks until released; all calls are recorded."""

    def __init__(self, slow_symbol):
        super().__init__(None, None)
        self.slow_symbol = slow_symbol
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def subscribe(self, symbol):
        self.subscribed.append(symbol)
        if symbol == self.slow_symbol:
            self.entered.set()
            await self.release.wait()


async def test_browser_and_rule_both_wait_on_lock_symbol_still_subscribed():
    s = _services()
    s.upstream = SlowFirstUpstream("X")
    t1 = asyncio.create_task(s.watch_symbol("X"))  # holds the lock in a slow subscribe
    await asyncio.wait_for(s.upstream.entered.wait(), timeout=5)
    t2 = asyncio.create_task(s.subscriptions.subscribe("c1", "AAPL"))  # ref_count=1, waits
    await _yield()
    t3 = asyncio.create_task(s.watch_symbol("AAPL"))  # watchlist add, waits
    await _yield()
    s.upstream.release.set()
    await asyncio.wait_for(asyncio.gather(t1, t2, t3), timeout=5)
    assert s.upstream.subscribed.count("AAPL") == 1
    assert "AAPL" in s._upstream_sent and "X" in s._upstream_sent


async def test_failed_subscribe_is_not_marked_sent_and_is_retried():
    s = _services()
    ok = s.upstream.subscribe
    fail = True

    async def flaky(symbol):
        if fail:
            raise RuntimeError("x")
        await ok(symbol)

    s.upstream.subscribe = flaky
    await s.watch_symbol("AAPL")
    assert s._upstream_sent == set()
    fail = False
    await s.subscriptions.subscribe("c1", "AAPL")  # reconcile retries
    assert s.upstream.subscribed == ["AAPL"] and s._upstream_sent == {"AAPL"}


async def test_connect_snapshot_resets_sent_to_wanted():
    s = _services(watchlist={"A"})
    await s.subscriptions.subscribe("c1", "B")
    s._upstream_sent = {"stale"}
    assert s._symbols_for_connect() == ["A", "B"]
    assert s._upstream_sent == {"A", "B"}
    await s.subscriptions.disconnect("c1")
    assert s.upstream.unsubscribed == ["B"] and s._upstream_sent == {"A"}


async def test_row_retry_recovers_means_no_backoff(monkeypatch):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 30)
    pool = StubPool()
    pool.mode = "batch_fail"  # only executemany fails; per-row inserts succeed
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    await _yield(50)
    db.enqueue_alert(_a(2))
    await _yield(50)
    assert len(pool.rows) == 2  # second batch written: writer did not sleep
    await asyncio.wait_for(db.close(), timeout=5)


async def test_row_retry_logs_one_summary_per_batch(monkeypatch, caplog):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 30)
    pool = StubPool()
    pool.mode = "fail"
    db = _db_with_stub(pool)
    with caplog.at_level(logging.ERROR, logger="app.db"):
        for i in range(3):
            db._alert_queue.put_nowait(_a(i))
        await _yield(50)
    summaries = [r for r in caplog.records if "alerts failed" in r.getMessage()]
    assert len(summaries) == 1 and "3 of 3 alerts failed: RuntimeError" in summaries[0].getMessage()
    assert not [r for r in caplog.records if "alert insert failed" in r.getMessage()]
    await asyncio.wait_for(db.close(), timeout=5)


async def test_drain_skipped_when_alert_worker_done():
    pool = StubPool()
    db = _db_with_stub(pool)
    db.enqueue_alert(_a(1))
    db._alert_worker.cancel()
    await _yield()
    assert db._alert_worker.done() and db._alert_queue.qsize() == 1
    await asyncio.wait_for(db.close(), timeout=2)  # would wait 3s on join otherwise


# ---- T009: alert writer survives bad alerts --------------------------------
class _BadAlert:
    """Stand-in whose row building raises (no .threshold attribute)."""
    id = "bad"
    rule_id = "r"
    symbol = "AAPL"
    rule_type = "price_above"
    price = 2
    triggered_at = 0


async def test_writer_survives_bad_alert_in_same_batch(caplog):
    pool = StubPool()
    db = _db_with_stub(pool)
    with caplog.at_level(logging.ERROR, logger="app.db"):
        db._alert_queue.put_nowait(_BadAlert())
        db._alert_queue.put_nowait(_a(7))
        await asyncio.wait_for(db._alert_queue.join(), 1)
    assert [row[-1] for row in pool.rows] == [7]
    assert not db._alert_worker.done()
    assert [r for r in caplog.records if r.exc_info]
    await asyncio.wait_for(db.close(), timeout=5)


async def test_writer_survives_bad_alert_in_separate_batches(monkeypatch, caplog):
    monkeypatch.setattr("app.db.ALERT_FAILURE_BACKOFF_SECONDS", 0.01)
    pool = StubPool()
    db = _db_with_stub(pool)
    with caplog.at_level(logging.ERROR, logger="app.db"):
        db._alert_queue.put_nowait(_BadAlert())
        await asyncio.wait_for(db._alert_queue.join(), 1)
        assert not db._alert_worker.done()
        db._alert_queue.put_nowait(_a(8))
        await asyncio.wait_for(db._alert_queue.join(), 1)
    assert [row[-1] for row in pool.rows] == [8]
    assert not db._alert_worker.done()
    assert [r for r in caplog.records if r.exc_info]
    await asyncio.wait_for(db.close(), timeout=5)
