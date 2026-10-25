"""Application settings. All values come from environment variables or .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    finnhub_api_key: str = ""
    finnhub_ws_url: str = "wss://ws.finnhub.io"
    database_url: str = ""
    cors_origins: str = "http://localhost:5173"

    rules_persistence_enabled: bool = True
    ticks_persistence_enabled: bool = True

    watchlist_max_symbols: int = 50

    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
