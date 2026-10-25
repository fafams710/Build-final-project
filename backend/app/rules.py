"""In-memory rule store and edge-triggered rule engine."""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from .models import Rule, RuleCreate, RuleType, Tick

log = logging.getLogger(__name__)


class RuleStore:
    """In-memory copy of the rules used for evaluation. Postgres is the source
    of truth; `repo` is only used to load rules at startup."""

    def __init__(self, repo: Any = None) -> None:
        self._rules: dict[str, Rule] = {}
        self._repo = repo

    async def load(self) -> None:
        """Load persisted rules; on any failure log and keep the store empty."""
        if self._repo is None:
            return
        try:
            for rule in await self._repo.load_rules():
                self._rules[rule.id] = rule
        except Exception as exc:
            log.warning("loading persisted rules failed: %s", type(exc).__name__)

    def put(self, rule: Rule) -> None:
        self._rules[rule.id] = rule

    def discard(self, rule_id: str) -> None:
        self._rules.pop(rule_id, None)

    async def add(self, data: RuleCreate) -> Rule:
        """In-memory only helper (no persistence)."""
        rule = Rule(**data.model_dump())
        self.put(rule)
        return rule

    def for_symbol(self, symbol: str) -> list[Rule]:
        return [r for r in self._rules.values() if r.symbol == symbol]

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._rules


class RuleEngine:
    """Evaluates ticks against rules; a rule fires only when its condition
    flips from false to true (edge-triggered) and re-arms when false again."""

    def __init__(self, store: RuleStore) -> None:
        self._store = store
        self._active: dict[str, bool] = {}
        self._history: dict[str, deque] = {}

    def evaluate(self, tick: Tick) -> list[tuple[Rule, dict]]:
        rules = self._store.for_symbol(tick.symbol)
        live_ids = {r.id for r in rules}
        for rid in [k for k in self._active if k not in live_ids]:
            # drop state of deleted rules (cheap: bounded by rule count)
            if rid not in self._store:
                del self._active[rid]
        if not rules:
            self._history.pop(tick.symbol, None)
            return []

        now = tick.ts_ms / 1000.0
        history = self._history.setdefault(tick.symbol, deque())
        max_window = max((r.window_seconds or 0) for r in rules)
        while history and history[0][0] < now - max_window:
            history.popleft()

        fired = []
        for rule in rules:
            cond = self._condition(rule, tick.price, now, history)
            was_active = self._active.get(rule.id, False)
            self._active[rule.id] = cond
            if cond and not was_active:
                fired.append((rule, self._event(rule, tick)))
        history.append((now, tick.price))
        return fired

    @staticmethod
    def _condition(rule: Rule, price: float, now: float, history: deque) -> bool:
        if rule.type == RuleType.PRICE_ABOVE:
            return price > rule.threshold
        if rule.type == RuleType.PRICE_BELOW:
            return price < rule.threshold
        # percent change vs. the oldest price still inside the rule's window
        cutoff = now - (rule.window_seconds or 0)
        for ts, ref in history:
            if ts >= cutoff:
                return ref > 0 and abs(price - ref) / ref * 100.0 >= rule.threshold
        return False

    @staticmethod
    def _event(rule: Rule, tick: Tick) -> dict:
        return {
            "event": "rule_triggered",
            "rule_id": rule.id,
            "symbol": rule.symbol,
            "rule_type": rule.type.value,
            "threshold": rule.threshold,
            "window_seconds": rule.window_seconds,
            "price": tick.price,
            "tick_ts_ms": tick.ts_ms,
            "triggered_at": time.time(),
        }
