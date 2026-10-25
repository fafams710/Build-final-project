"""Shared API key check (REST dependency, websocket gate) and log masking."""
import logging
import re
import secrets
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException, Request

_KEY_PARAM = re.compile(r"([?&]key=)[^&\s\"']*")
_MASK = "***"
_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def key_matches(supplied: Optional[str], expected: str) -> bool:
    """Constant-time comparison on UTF-8 bytes (str compare raises on non-ASCII)."""
    if supplied is None or not expected:
        return False
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def require_api_key(request: Request) -> None:
    expected = request.app.state.services.settings.app_api_key
    if not key_matches(request.headers.get("x-api-key"), expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


class KeyMaskFilter(logging.Filter):
    """Masks `key=<value>` query parameters and the configured key itself in
    the message and args of every record. Never drops a record."""

    def __init__(self, secret: str = "") -> None:
        super().__init__()
        self.secret = secret if secret and secret.strip() else ""

    def _mask(self, value):
        if not isinstance(value, str):
            return value
        value = _KEY_PARAM.sub(r"\1" + _MASK, value)
        if self.secret:
            for form in (self.secret, quote(self.secret, safe=""), quote(self.secret)):
                value = value.replace(form, _MASK)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = self._mask(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(self._mask(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: self._mask(v) for k, v in record.args.items()}
        except Exception:  # masking must never break logging
            pass
        return True


def install_key_mask(secret: str) -> None:
    """Idempotently put one KeyMaskFilter on the uvicorn and app loggers and on
    every handler already attached to them (handler filters also see records
    propagated from child loggers; logger filters do not)."""
    for name in _LOGGERS + ("app",):
        lg = logging.getLogger(name)
        for target in [lg, *lg.handlers]:
            existing = [f for f in target.filters if isinstance(f, KeyMaskFilter)]
            if existing:
                if secret and secret.strip():
                    existing[0].secret = secret
                continue
            target.addFilter(KeyMaskFilter(secret))
