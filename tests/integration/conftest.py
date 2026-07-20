"""Shared fixtures for Emporia EV integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components as _cc_pkg
from custom_components.emporia_ev.client import Charger, ChargerStatus
from custom_components.emporia_ev.const import (
    CONF_ADAPTIVE,
    CONF_CHARGING_INTERVAL,
    CONF_DEFAULT_AMPS,
    CONF_IDLE_INTERVAL,
    DOMAIN,
)

# ---------------------------------------------------------------------------
# Editable-install path fixup
# ---------------------------------------------------------------------------
# The editable install injects a fake "__editable__.emporia_ev-*.finder.__path_hook__"
# sentinel into custom_components.__path__ so the namespace finder works.
# HA's loader tries to iterate every path in __path__ as a real directory,
# which causes a FileNotFoundError.  We strip the sentinel here at import
# time (once per session) — it is NOT a real directory and HA doesn't need it
# because it resolves the real path from the first entry.
_cc_pkg.__path__ = [  # type: ignore[attr-defined]
    p for p in _cc_pkg.__path__ if not str(p).startswith("__editable__")
]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom_components in every test."""
    yield


def make_charger(charger_id: str = "chg-1", name: str = "Garage") -> Charger:
    """Build a real Charger dataclass with sensible defaults."""
    return Charger(
        id=charger_id,
        name=name,
        model="EV-100",
        serial=f"SN-{charger_id}",
        max_amps=48,
        min_amps=6,
    )


def make_status(
    *,
    enabled: bool = True,
    charging_state: str = "plugged_in_idle",
    power_w: float = 0.0,
    energy_kwh: float = 12.5,
    plugged_in: bool = True,
    charge_rate_amps: int = 32,
) -> ChargerStatus:
    """Build a real ChargerStatus dataclass with a plugged-in-idle default."""
    return ChargerStatus(
        enabled=enabled,
        charging_state=charging_state,
        power_w=power_w,
        energy_kwh=energy_kwh,
        plugged_in=plugged_in,
        charge_rate_amps=charge_rate_amps,
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """A mocked EmporiaClient with one charger, no vehicle, and energy data."""
    client = MagicMock()
    client.account_id = "acct-42"
    client.authenticate = AsyncMock(return_value=None)
    client.async_get_chargers = AsyncMock(return_value=[make_charger()])
    client.async_get_charger_status = AsyncMock(return_value={"chg-1": make_status()})
    client.async_get_vehicles = AsyncMock(return_value={})
    client.async_get_energy = AsyncMock(return_value={"chg-1": 0.0})
    client.async_set_charger = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry for the integration, unique_id = account id."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Emporia (acct-42)",
        unique_id="acct-42",
        data={
            "username": "user@example.com",
            "password": "hunter2",
            "refresh_token": "refresh-abc",
            "account_id": "acct-42",
        },
        options={
            CONF_IDLE_INTERVAL: 30,
            CONF_CHARGING_INTERVAL: 15,
            CONF_ADAPTIVE: True,
            CONF_DEFAULT_AMPS: 32,
        },
    )
