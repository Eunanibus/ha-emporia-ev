"""Sensor platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import pytest

from custom_components.emporia_ev.client import Vehicle

from .conftest import make_status


async def _setup(hass, mock_client, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", [Platform.SENSOR]),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_power_energy_status_sensors(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    # The coordinator merges: energy_kwh = kWh bucket, power_w = kWh * 60 * 1000.
    # 0.12 kWh → power_w = 7200 W; charging_state comes from async_get_charger_status.
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charging_state="charging")
    }
    mock_client.async_get_energy.return_value = {"chg-1": 0.12}
    await _setup(hass, mock_client, mock_config_entry)

    power = hass.states.get("sensor.garage_power")
    assert power is not None
    assert float(power.state) == pytest.approx(7200.0)
    assert power.attributes["device_class"] == "power"
    assert power.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert power.attributes["unit_of_measurement"] == "W"

    energy = hass.states.get("sensor.garage_energy")
    assert energy is not None
    assert float(energy.state) == pytest.approx(0.12)
    assert energy.attributes["device_class"] == "energy"
    # Per reconciliation: energy is a per-1-minute bucket reading, NOT a lifetime counter.
    # MEASUREMENT is the correct semantic even though HA warns that ENERGY typically uses
    # TOTAL_INCREASING; a Riemann-sum/utility_meter helper can produce a lifetime total.
    assert energy.attributes["state_class"] == SensorStateClass.MEASUREMENT
    assert energy.attributes["state_class"] != SensorStateClass.TOTAL_INCREASING
    assert energy.attributes["unit_of_measurement"] == "kWh"

    status = hass.states.get("sensor.garage_status")
    assert status is not None
    assert status.state == "charging"
    assert status.attributes["device_class"] == "enum"
    assert "plugged_in_idle" in status.attributes["options"]


async def test_vehicle_battery_absent_when_no_vehicle(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_vehicles.return_value = {}
    await _setup(hass, mock_client, mock_config_entry)
    assert hass.states.get("sensor.garage_vehicle_battery") is None


async def test_vehicle_battery_present_when_linked(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_vehicles.return_value = {
        "chg-1": Vehicle(battery_pct=64, charging_state="charging")
    }
    await _setup(hass, mock_client, mock_config_entry)
    veh = hass.states.get("sensor.garage_vehicle_battery")
    assert veh is not None
    assert float(veh.state) == 64
    assert veh.attributes["device_class"] == "battery"
    assert veh.attributes["unit_of_measurement"] == "%"
