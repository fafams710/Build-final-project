"""Pydantic models shared across the backend."""
import re
import time
import uuid
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.:_\-]{0,31}$")


def normalize_symbol(value: str) -> str:
    sym = (value or "").strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError("invalid symbol")
    return sym


class Tick(BaseModel):
    symbol: str
    price: float
    volume: Optional[float] = None
    ts_ms: int
    received_at: float = Field(default_factory=time.time)


class RuleType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    PERCENT_CHANGE = "percent_change"


class RuleCreate(BaseModel):
    symbol: str
    type: RuleType
    threshold: float = Field(gt=0)
    window_seconds: Optional[float] = Field(default=None, gt=0)
    webhook_url: str

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        return normalize_symbol(v)

    @field_validator("webhook_url")
    @classmethod
    def _url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("webhook_url must be an http(s) URL")
        return v

    @model_validator(mode="after")
    def _window(self) -> "RuleCreate":
        if self.type == RuleType.PERCENT_CHANGE and self.window_seconds is None:
            raise ValueError("window_seconds is required for percent_change rules")
        return self


class Rule(RuleCreate):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = Field(default_factory=time.time)
