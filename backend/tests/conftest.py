import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Tick
from tests.fakes.fake_finnhub import FakeFinnhubClient


class FakeCache:
    def __init__(self) -> None:
        self.prices: dict[str, dict] = {}

    async def set_price(self, tick: Tick) -> None:
        self.prices[tick.symbol] = {"price": tick.price, "ts_ms": tick.ts_ms, "volume": tick.volume}

    async def get_price(self, symbol: str):
        return self.prices.get(symbol)

    async def close(self) -> None:
        pass


class RecordingWebhooks:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, url: str, payload: dict) -> None:
        self.calls.append((url, payload))

    async def close(self) -> None:
        pass


@pytest.fixture
def settings() -> Settings:
    # Explicit values: never reads .env or real credentials.
    return Settings(
        _env_file=None,
        finnhub_api_key="",
        webhook_signing_secret="test-secret",
        database_url="",
        redis_url="",
    )


@pytest.fixture
def fake_cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def webhooks() -> RecordingWebhooks:
    return RecordingWebhooks()


@pytest.fixture
def client(settings, fake_cache, webhooks):
    FakeFinnhubClient.instances.clear()
    app = create_app(
        settings,
        upstream_factory=FakeFinnhubClient.factory,
        cache=fake_cache,
        webhooks=webhooks,
    )
    with TestClient(app) as c:
        yield c
