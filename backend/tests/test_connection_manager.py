from app.connection_manager import ConnectionManager


class FakeWS:
    def __init__(self, fail=False):
        self.fail = fail
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, msg):
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(msg)


async def test_send_to_drops_failed_connection():
    gone = []

    async def on_disc(cid):
        gone.append(cid)

    mgr = ConnectionManager(on_disconnect=on_disc)
    good, bad = FakeWS(), FakeWS(fail=True)
    gid = await mgr.connect(good)
    bid = await mgr.connect(bad)
    assert good.accepted and len(mgr) == 2

    await mgr.send_to([gid, bid], {"x": 1})  # must not raise
    assert good.sent == [{"x": 1}]
    assert len(mgr) == 1
    assert gone == [bid]

    await mgr.send_to([gid, bid], {"x": 2})
    assert good.sent == [{"x": 1}, {"x": 2}]


async def test_send_to_subset_only():
    mgr = ConnectionManager()
    a, b = FakeWS(), FakeWS()
    aid = await mgr.connect(a)
    await mgr.connect(b)
    await mgr.send_to([aid], {"only": "a"})
    assert a.sent == [{"only": "a"}] and b.sent == []


async def test_disconnect_is_idempotent():
    calls = []

    async def on_disc(cid):
        calls.append(cid)

    mgr = ConnectionManager(on_disconnect=on_disc)
    cid = await mgr.connect(FakeWS())
    await mgr.disconnect(cid)
    await mgr.disconnect(cid)
    assert calls == [cid]
