from app.models import RuleCreate, Tick
from app.rules import RuleEngine, RuleStore


def tick(price, t=0, symbol="AAPL"):
    return Tick(symbol=symbol, price=price, ts_ms=int(t * 1000))


async def setup(*rules):
    store = RuleStore()
    for r in rules:
        await store.add(RuleCreate(symbol="AAPL", **r))
    return store, RuleEngine(store)


async def test_price_above_is_edge_triggered_and_rearms():
    _, eng = await setup({"type": "price_above", "threshold": 100})
    assert eng.evaluate(tick(99)) == []
    assert len(eng.evaluate(tick(101))) == 1
    assert eng.evaluate(tick(102)) == []  # sustained breach: no spam
    assert eng.evaluate(tick(99)) == []   # re-arm
    assert len(eng.evaluate(tick(105))) == 1


async def test_price_below():
    _, eng = await setup({"type": "price_below", "threshold": 50})
    assert eng.evaluate(tick(60)) == []
    fired = eng.evaluate(tick(49))
    assert len(fired) == 1 and fired[0][1]["event"] == "rule_triggered"
    assert fired[0][1]["price"] == 49


async def test_percent_change_over_window():
    _, eng = await setup({"type": "percent_change", "threshold": 5, "window_seconds": 60})
    assert eng.evaluate(tick(100, t=0)) == []
    assert eng.evaluate(tick(102, t=10)) == []
    assert len(eng.evaluate(tick(106, t=20))) == 1  # +6% vs t=0
    assert eng.evaluate(tick(107, t=30)) == []


async def test_percent_change_ignores_prices_outside_window():
    _, eng = await setup({"type": "percent_change", "threshold": 5, "window_seconds": 10})
    eng.evaluate(tick(100, t=0))
    assert eng.evaluate(tick(110, t=100)) == []  # old reference expired


async def test_multiple_rules_fire_independently_and_other_symbols_ignored():
    _, eng = await setup(
        {"type": "price_above", "threshold": 100},
        {"type": "price_above", "threshold": 110},
    )
    assert eng.evaluate(tick(120, symbol="MSFT")) == []
    assert len(eng.evaluate(tick(105))) == 1
    assert len(eng.evaluate(tick(115))) == 1  # only the second newly fires


async def test_store_crud_and_persistence_errors_are_swallowed():
    class BrokenRepo:
        async def save_rule(self, r):
            raise RuntimeError("db down")

        async def delete_rule(self, i):
            raise RuntimeError("db down")

        async def load_rules(self):
            raise RuntimeError("db down")

    store = RuleStore(repo=BrokenRepo())
    await store.load()
    rule = await store.add(RuleCreate(symbol="aapl", type="price_above", threshold=1))
    assert store.list() == [rule] and rule.symbol == "AAPL"
    assert await store.remove(rule.id) is True
    assert await store.remove(rule.id) is False
