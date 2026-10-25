import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes.fake_db import FakeDatabase
from tests.fakes.fake_finnhub import FakeFinnhubClient

TEST_KEY = "test-key"
AUTH = {"X-API-Key": TEST_KEY}


@pytest.fixture
def settings() -> Settings:
    # Explicit values: never reads .env or real credentials.
    return Settings(
        _env_file=None,
        finnhub_api_key="",
        database_url="",
        app_api_key=TEST_KEY,
    )


@pytest.fixture
def fake_db() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def client(settings, fake_db):
    FakeFinnhubClient.instances.clear()
    app = create_app(settings, upstream_factory=FakeFinnhubClient.factory, db=fake_db)
    with TestClient(app, headers=AUTH) as c:
        yield c


@pytest.fixture
def client_no_db():
    """Explicit opt-out (RULES_PERSISTENCE_ENABLED=false) with the real
    Database and no URL: the app runs, rule endpoints answer 503."""
    FakeFinnhubClient.instances.clear()
    opt_out = Settings(
        _env_file=None,
        finnhub_api_key="",
        database_url="",
        rules_persistence_enabled=False,
        app_api_key=TEST_KEY,
    )
    app = create_app(opt_out, upstream_factory=FakeFinnhubClient.factory)
    with TestClient(app, headers=AUTH) as c:
        yield c
