import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes.fake_finnhub import FakeFinnhubClient


@pytest.fixture
def settings() -> Settings:
    # Explicit values: never reads .env or real credentials.
    return Settings(
        _env_file=None,
        finnhub_api_key="",
        database_url="",
    )


@pytest.fixture
def client(settings):
    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory)
    with TestClient(app) as c:
        yield c
