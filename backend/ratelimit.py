"""Sliding-window rate limiter.

FIX(P2#4 refactor): extracted from ``backend/main.py`` so the limiter and
its configured instances live in one place.

FIX(M3/H2): the backing store is SQLite (see ``backend.db.RateEvent``) rather
than a per-process in-memory dict. The deployment runs two uvicorn processes
(IPv4 + IPv6); an in-memory window gave each process its own budget, so every
limit was effectively doubled and not shared. Persisting events -- exactly like
the auth-token store already does -- gives a single shared budget. It also
removes the unbounded-key memory growth of the old ``defaultdict(list)``: rows
are pruned on every touch and by a periodic sweep.

The public API (``allow`` / ``check_only`` / ``record`` / ``reset`` /
``remaining``) is unchanged so call sites did not have to change.
"""

from __future__ import annotations

from backend.db import rate_add, rate_count, rate_reset

# Widest window any limiter uses; the periodic sweep in main.py deletes events
# older than this so quiet keys leave no rows behind.
_MAX_PERIOD_SEEN = 60.0


def max_period_seen() -> float:
    return _MAX_PERIOD_SEEN


class RateLimiter:
    def __init__(self, scope: str, max_calls: int, period: float):
        global _MAX_PERIOD_SEEN
        self._scope  = scope
        self._max    = max_calls
        self._period = period
        if period > _MAX_PERIOD_SEEN:
            _MAX_PERIOD_SEEN = period

    def allow(self, key: str) -> bool:
        """Check the limit and record this attempt."""
        if rate_count(self._scope, key, self._period) >= self._max:
            return False
        rate_add(self._scope, key)
        return True

    def check_only(self, key: str) -> bool:
        """FIX(P2#2): non-recording probe. Used by the login flow to gate
        before validating credentials so a flood of well-formed login requests
        does not consume the budget for legitimate users."""
        return rate_count(self._scope, key, self._period) < self._max

    def record(self, key: str) -> None:
        """FIX(P2#2): record an event without a guard check. Paired with
        check_only() the caller implements count-failures-only semantics."""
        rate_add(self._scope, key)

    def reset(self, key: str) -> None:
        """FIX(P2#2): clear the window after a successful login."""
        rate_reset(self._scope, key)

    def remaining(self, key: str) -> int:
        return max(0, self._max - rate_count(self._scope, key, self._period))
