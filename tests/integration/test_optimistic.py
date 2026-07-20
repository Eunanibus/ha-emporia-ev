"""Tests for the optimistic-command helper (used by switch + number)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.emporia_ev.optimistic import OptimisticState


async def test_optimistic_holds_then_expires(hass: HomeAssistant) -> None:
    opt = OptimisticState(grace=timedelta(seconds=30))
    now = dt_util.utcnow()
    opt.set(True, now=now)
    assert opt.value(coordinator_value=False, now=now) is True
    assert opt.value(coordinator_value=False, now=now + timedelta(seconds=10)) is True
    assert opt.value(coordinator_value=False, now=now + timedelta(seconds=31)) is False


async def test_optimistic_clears_early_when_read_agrees(hass: HomeAssistant) -> None:
    opt = OptimisticState(grace=timedelta(seconds=30))
    now = dt_util.utcnow()
    opt.set(True, now=now)
    assert opt.value(coordinator_value=True, now=now + timedelta(seconds=5)) is True
    assert opt.value(coordinator_value=False, now=now + timedelta(seconds=6)) is False


async def test_optimistic_no_pending_returns_coordinator(hass: HomeAssistant) -> None:
    opt = OptimisticState(grace=timedelta(seconds=30))
    assert opt.value(coordinator_value=17, now=dt_util.utcnow()) == 17
