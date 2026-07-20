"""Number platform tests (charge rate)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.emporia_ev.client import Charger, EmporiaError

from .conftest import make_status


async def _setup(hass, mock_client, mock_config_entry) -> None:
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", [Platform.NUMBER]),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_number_min_max_from_charger(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_chargers.return_value = [
        Charger(
            id="chg-1", name="Garage", model="EV-100", serial="SN-chg-1", max_amps=40, min_amps=8
        )
    ]
    await _setup(hass, mock_client, mock_config_entry)
    state = hass.states.get("number.garage_charge_rate")
    assert state is not None
    assert state.attributes["min"] == 8
    assert state.attributes["max"] == 40
    assert state.attributes["step"] == 1
    assert state.attributes["unit_of_measurement"] == "A"
    assert float(state.state) == 32


async def test_number_set_value_optimistic(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_charger_status.return_value = {
        "chg-1": make_status(charge_rate_amps=16, enabled=True)
    }
    await _setup(hass, mock_client, mock_config_entry)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.garage_charge_rate", "value": 24},
        blocking=True,
    )
    _, kwargs = mock_client.async_set_charger.call_args
    assert kwargs["charge_rate_amps"] == 24
    assert kwargs["enabled"] is True
    assert float(hass.states.get("number.garage_charge_rate").state) == 24


async def test_number_reverts_on_failure(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    mock_client.async_get_charger_status.return_value = {"chg-1": make_status(charge_rate_amps=16)}
    await _setup(hass, mock_client, mock_config_entry)
    mock_client.async_set_charger.side_effect = EmporiaError("nope")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": "number.garage_charge_rate", "value": 24},
            blocking=True,
        )
    assert float(hass.states.get("number.garage_charge_rate").state) == 16
