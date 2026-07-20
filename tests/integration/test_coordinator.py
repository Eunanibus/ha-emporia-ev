"""Integration tests for EmporiaDataUpdateCoordinator.

Tests cover:
- First refresh: status dict keyed by charger id, chargers populated.
- Energy merge: energy_kwh filled from async_get_energy, power_w derived.
- Energy default: 0.0 when async_get_energy returns nothing for a charger.
- Error mapping: AuthError → ConfigEntryAuthFailed,
                  EmporiaConnectionError → UpdateFailed,
                  RateLimitError → keeps last data + backs off.
- Adaptive interval: idle 30 s after first non-charging poll; charging → 15 s
  immediately; one more non-charging poll HOLDS at 15 s (hysteresis N=2);
  second non-charging → back to 30 s.
- Adaptive disabled: pinned to 30 s even when charging.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.emporia_ev.client import (
    AuthError,
    EmporiaConnectionError,
    RateLimitError,
)
from custom_components.emporia_ev.coordinator import EmporiaDataUpdateCoordinator

from .conftest import make_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_coordinator(
    hass: HomeAssistant,
    entry,
    client,
) -> EmporiaDataUpdateCoordinator:
    """Create, register, and perform an initial data refresh.

    Uses ``async_refresh()`` directly (instead of
    ``async_config_entry_first_refresh()``) so the test does not need to
    wrestle with entry lifecycle states — this is a unit-level integration
    test and the coordinator contract is fully exercised by verifying
    ``coordinator.data`` after the refresh.
    """
    entry.add_to_hass(hass)
    coordinator = EmporiaDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_refresh()
    return coordinator


# ---------------------------------------------------------------------------
# Basic fetch
# ---------------------------------------------------------------------------


async def test_first_refresh_stores_status_dict(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """First refresh populates coordinator.data and coordinator.chargers."""
    coordinator = await _make_coordinator(hass, mock_config_entry, mock_client)

    assert set(coordinator.data) == {"chg-1"}
    assert coordinator.data["chg-1"].charge_rate_amps == 32
    assert "chg-1" in coordinator.chargers
    mock_client.async_get_charger_status.assert_awaited_once()


# ---------------------------------------------------------------------------
# Energy merge
# ---------------------------------------------------------------------------


async def test_energy_merge_sets_energy_kwh_and_power_w(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """Energy returned by async_get_energy is merged into ChargerStatus.

    energy_kwh must equal the returned kWh value; power_w = kWh * 60 * 1000
    (average watts over the 1-minute bucket).
    """
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charging_state="charging", power_w=0.0, energy_kwh=0.0)
    }
    mock_client.async_get_energy.return_value = {"chg-1": 0.5}

    coordinator = await _make_coordinator(hass, mock_config_entry, mock_client)

    assert coordinator.data["chg-1"].energy_kwh == 0.5
    assert coordinator.data["chg-1"].power_w == 0.5 * 60 * 1000  # 30 000.0 W


async def test_energy_defaults_to_zero_when_missing(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """If async_get_energy does not include a charger id, energy_kwh stays 0.0."""
    mock_client.async_get_energy.return_value = {}

    coordinator = await _make_coordinator(hass, mock_config_entry, mock_client)

    assert coordinator.data["chg-1"].energy_kwh == 0.0
    assert coordinator.data["chg-1"].power_w == 0.0


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


async def test_auth_error_maps_to_config_entry_auth_failed(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """AuthError raised by the client is re-raised as ConfigEntryAuthFailed."""
    mock_config_entry.add_to_hass(hass)
    coordinator = EmporiaDataUpdateCoordinator(hass, mock_client, mock_config_entry)
    mock_client.async_get_chargers.side_effect = AuthError("token dead")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_connection_error_maps_to_update_failed(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """EmporiaConnectionError is re-raised as UpdateFailed."""
    mock_config_entry.add_to_hass(hass)
    coordinator = EmporiaDataUpdateCoordinator(hass, mock_client, mock_config_entry)
    mock_client.async_get_charger_status.side_effect = EmporiaConnectionError("boom")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_rate_limit_keeps_last_data_and_backs_off(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """RateLimitError preserves prior data and sets update_interval to retry_after."""
    coordinator = await _make_coordinator(hass, mock_config_entry, mock_client)
    prior = coordinator.data

    mock_client.async_get_charger_status.side_effect = RateLimitError(retry_after=90)
    result = await coordinator._async_update_data()

    assert result == prior
    assert coordinator.update_interval == timedelta(seconds=90)


# ---------------------------------------------------------------------------
# Adaptive interval + hysteresis
# ---------------------------------------------------------------------------


async def test_adaptive_speeds_up_immediately_relaxes_after_n(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """Full adaptive hysteresis cycle.

    1. First refresh (non-charging) → idle 30 s.
    2. Charging poll → 15 s immediately.
    3. First non-charging poll after charging → HOLD at 15 s (N=2 hysteresis).
    4. Second non-charging poll → relax back to 30 s.
    """
    coordinator = await _make_coordinator(hass, mock_config_entry, mock_client)
    # Step 1: non-charging → idle interval
    assert coordinator.update_interval == timedelta(seconds=30)

    # Step 2: charging → fast interval
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charging_state="charging", power_w=7000.0)
    }
    mock_client.async_get_energy.return_value = {"chg-1": 0.116}
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=15)

    # Step 3: first non-charging poll after charging → hold (hysteresis)
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charging_state="plugged_in_idle")
    }
    mock_client.async_get_energy.return_value = {}
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=15)  # hold

    # Step 4: second non-charging poll → relax
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=30)


async def test_adaptive_disabled_pins_idle_interval(
    hass: HomeAssistant,
    mock_client,
    mock_config_entry,
) -> None:
    """When adaptive=False, the interval stays at idle even when charging."""
    # Build a separate config entry with adaptive disabled so we don't fight
    # the ConfigEntry immutability guard on .options.
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.emporia_ev.const import (
        CONF_ADAPTIVE,
        CONF_CHARGING_INTERVAL,
        CONF_DEFAULT_AMPS,
        CONF_IDLE_INTERVAL,
        DOMAIN,
    )

    no_adaptive_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Emporia (acct-42)",
        unique_id="acct-42-no-adaptive",
        data=mock_config_entry.data,
        options={
            CONF_IDLE_INTERVAL: 30,
            CONF_CHARGING_INTERVAL: 15,
            CONF_ADAPTIVE: False,
            CONF_DEFAULT_AMPS: 32,
        },
    )
    coordinator = await _make_coordinator(hass, no_adaptive_entry, mock_client)

    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charging_state="charging")
    }
    mock_client.async_get_energy.return_value = {}
    await coordinator._async_update_data()
    assert coordinator.update_interval == timedelta(seconds=30)
