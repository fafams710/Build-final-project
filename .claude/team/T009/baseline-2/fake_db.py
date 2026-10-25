"""In-memory stand-in for app.db.Database (rule methods only)."""
from typing import Optional

from app.db import DatabaseUnavailable, WatchlistFull
from app.models import Alert, Rule


class FakeDatabase:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.rules: dict[str, Rule] = {}
        self.watchlist: set[str] = set()
        self.alerts: list[Alert] = []
        self.enqueue_error: Optional[Exception] = None
        self.load_rules_error: Optional[Exception] = None
        self.load_watchlist_error: Optional[Exception] = None
        self.close_calls = 0

    @property
    def available(self) -> bool:
        return not self.failing

    def _check(self) -> None:
        if self.failing:
            raise DatabaseUnavailable("fake db down")

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self.close_calls += 1

    def enqueue_tick(self, tick) -> None:
        return None

    async def save_rule(self, rule: Rule) -> None:
        self._check()
        self.rules[rule.id] = rule

    async def create_rule_with_watchlist(self, rule: Rule, max_symbols: int) -> bool:
        self._check()
        new = rule.symbol not in self.watchlist
        if new and len(self.watchlist) >= max_symbols:
            raise WatchlistFull("watchlist full")
        self.watchlist.add(rule.symbol)
        self.rules[rule.id] = rule
        return new

    async def load_watchlist(self) -> list[str]:
        self._check()
        if self.load_watchlist_error is not None:
            raise self.load_watchlist_error
        return sorted(self.watchlist)

    def enqueue_alert(self, alert: Alert) -> None:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        self.alerts.append(alert)

    async def list_alerts(self, limit: int = 50) -> list[Alert]:
        self._check()
        rows = sorted(self.alerts, key=lambda a: (a.triggered_at, a.id), reverse=True)
        return rows[:limit]

    async def delete_rule(self, rule_id: str) -> bool:
        self._check()
        return self.rules.pop(rule_id, None) is not None

    async def list_rules(self, symbol: Optional[str] = None, limit: int = 50) -> list[Rule]:
        self._check()
        rows = [r for r in self.rules.values() if not symbol or r.symbol == symbol]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        return rows[:limit]

    async def load_rules(self) -> list[Rule]:
        self._check()
        if self.load_rules_error is not None:
            raise self.load_rules_error
        return list(self.rules.values())
