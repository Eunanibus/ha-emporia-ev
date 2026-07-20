"""Optimistic-value holder shared by command entities (switch, number).

Anti-flicker contract: on command success set an optimistic value + timestamp;
within the grace window return it even if the coordinator still reports the
pre-command state (cloud propagation lag); once the coordinator agrees OR the
window elapses, release and defer to coordinator data. Never fire an immediate
refresh — normal coordinator ticks reconcile.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util


class OptimisticState[T]:
    """Holds an optimistic value through a short grace window."""

    def __init__(self, grace: timedelta) -> None:
        self._grace = grace
        self._pending: T | None = None
        self._set_at: datetime | None = None
        self._has_pending = False

    def set(self, value: T, *, now: datetime | None = None) -> None:
        self._pending = value
        self._set_at = now or dt_util.utcnow()
        self._has_pending = True

    def clear(self) -> None:
        self._pending = None
        self._set_at = None
        self._has_pending = False

    def value(self, *, coordinator_value: T, now: datetime | None = None) -> T:
        if not self._has_pending or self._set_at is None:
            return coordinator_value
        now = now or dt_util.utcnow()
        if now - self._set_at >= self._grace:
            self.clear()
            return coordinator_value
        if coordinator_value == self._pending:
            self.clear()
            return coordinator_value
        return self._pending  # type: ignore[return-value]
