import asyncio

from app.finnhub_client import FinnhubClient
from app.subscription_manager import SubscriptionManager
from tests.fakes.fake_finnhub import FakeUpstream


def make():
    up, down = [], []

    async def u(s):
        up.append(s)

    async def d(s):
        down.append(s)

    return SubscriptionManager(u, d), up, down


async def test_second_subscriber_sends_no_second_upstream_subscribe():
    m, up, down = make()
    await m.subscribe("a", "AAPL")
    await m.subscribe("b", "AAPL")
    assert up == ["AAPL"] and m.ref_count("AAPL") == 2
    await m.unsubscribe("a", "AAPL")
    assert down == []
    await m.unsubscribe("b", "AAPL")
    assert down == ["AAPL"] and m.ref_count("AAPL") == 0


async def test_idempotent_subscribe():
    m, up, _ = make()
    assert await m.subscribe("a", "AAPL") is True
    assert await m.subscribe("a", "AAPL") is False
    assert up == ["AAPL"] and m.ref_count("AAPL") == 1


async def test_disconnect_decrements_all_symbols():
    m, up, down = make()
    await m.subscribe("a", "AAPL")
    await m.subscribe("a", "MSFT")
    await m.subscribe("b", "AAPL")
    await m.disconnect("a")
    assert down == ["MSFT"]
    assert m.ref_count("AAPL") == 1
    await m.disconnect("b")
    assert sorted(down) == ["AAPL", "MSFT"] and m.active_symbols() == []


async def test_unsubscribe_unknown_never_negative():
    m, _, down = make()
    assert await m.unsubscribe("a", "AAPL") is False
    await m.subscribe("a", "AAPL")
    await m.unsubscribe("a", "AAPL")
    await m.unsubscribe("a", "AAPL")
    assert down == ["AAPL"] and m.ref_count("AAPL") == 0


async def test_upstream_failure_does_not_raise():
    async def boom(_):
        raise RuntimeError("x")

    m = SubscriptionManager(boom, boom)
    await m.subscribe("a", "AAPL")
    await m.unsubscribe("a", "AAPL")


async def _wait_for(cond, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "timed out"
        await asyncio.sleep(0.005)


async def test_upstream_reconnect_resubscribes_active_symbols():
    fake = FakeUpstream()
    ticks = []

    async def on_tick(t):
        ticks.append(t)

    m = SubscriptionManager()
    client = FinnhubClient(
        "wss://example.invalid", "k", on_tick, m.active_symbols,
        connect=fake, backoff_initial=0.01, backoff_max=0.02,
    )
    m._up_sub, m._up_unsub = client.subscribe, client.unsubscribe
    await client.start()
    await _wait_for(lambda: client.connected)

    await m.subscribe("a", "AAPL")
    await m.subscribe("b", "AAPL")
    await m.subscribe("b", "MSFT")
    subs = [s for s in fake.sent if s["type"] == "subscribe"]
    assert [s["symbol"] for s in subs] == ["AAPL", "MSFT"]

    fake.emit_trade("AAPL", 10.5)
    await _wait_for(lambda: len(ticks) == 1)
    assert ticks[0].symbol == "AAPL" and ticks[0].price == 10.5

    fake.sent.clear()
    fake.drop()
    await _wait_for(lambda: fake.connect_count == 2 and client.connected)
    await _wait_for(lambda: len(fake.sent) == 2)
    assert sorted(s["symbol"] for s in fake.sent if s["type"] == "subscribe") == ["AAPL", "MSFT"]
    assert fake.connect_count == 2

    await client.stop()
    assert not client.connected
