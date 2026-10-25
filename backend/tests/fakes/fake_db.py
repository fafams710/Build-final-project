"""In-memory stand-in for app.db.Database (rule methods only)."""
from typing import Optional

from app.db import DatabaseUnavailable
from app.models import Rule


class FakeDatabase:
    def __init__(self, failing: bool = False) -> None:
        self.failing = failing
        self.rules: dict[str, Rule] = {}

    @property
    def available(self) -> bool:
        return not self.failing

    def _check(self) -> None:
        if self.failing:
            raise DatabaseUnavailable("fake db down")

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def enqueue_tick(self, tick) -> None:
        return None

    async def save_rule(self, rule: Rule) -> None:
        self._check()
        self.rules[rule.id] = rule

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
        return list(self.rules.values())
