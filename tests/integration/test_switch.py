"""Switch platform tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.const import STATE_OFF, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.emporia_ev.client import EmporiaError

from .conftest import make_status


async def _setup(hass, mock_client, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", [Platform.SWITCH]),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_switch_reflects_enabled(hass: HomeAssistant, mock_client, mock_config_entry) -> None:
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(enabled=True, charging_state="charging")
    }
    await _setup(hass, mock_client, mock_config_entry)
    state = hass.states.get("switch.garage_charging")
    assert state is not None
    assert state.state == STATE_ON


async def test_switch_turn_on_calls_client_and_is_optimistic(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(enabled=False, charging_state="plugged_in_idle")
    }
    await _setup(hass, mock_client, mock_config_entry)
    assert hass.states.get("switch.garage_charging").state == STATE_OFF
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.garage_charging"}, blocking=True
    )
    mock_client.async_set_charger.assert_awaited_once()
    _, kwargs = mock_client.async_set_charger.call_args
    assert kwargs["enabled"] is True
    assert hass.states.get("switch.garage_charging").state == STATE_ON


async def test_switch_reverts_on_command_failure(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_charger_status.return_value = {"chg-1": make_status(enabled=False)}
    await _setup(hass, mock_client, mock_config_entry)
    mock_client.async_set_charger.side_effect = EmporiaError("cloud rejected")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.garage_charging"}, blocking=True
        )
    assert hass.states.get("switch.garage_charging").state == STATE_OFF
