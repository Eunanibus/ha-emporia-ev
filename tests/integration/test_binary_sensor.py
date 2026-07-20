"""Binary-sensor platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant

from .conftest import make_status


async def _setup(hass, mock_client, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", [Platform.BINARY_SENSOR]),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_plugged_in_binary_sensor(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_charger_status.return_value = {"chg-1": make_status(plugged_in=True)}
    await _setup(hass, mock_client, mock_config_entry)
    state = hass.states.get("binary_sensor.garage_plugged_in")
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["device_class"] == "plug"


async def test_unplugged_reflects_off(hass: HomeAssistant, mock_client, mock_config_entry) -> None:
    mock_client.async_get_charger_status.return_value = {"chg-1": make_status(plugged_in=False)}
    await _setup(hass, mock_client, mock_config_entry)
    assert hass.states.get("binary_sensor.garage_plugged_in").state == STATE_OFF
