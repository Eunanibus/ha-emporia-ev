"""Tests for the shared entity base."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.emporia_ev.const import DOMAIN
from custom_components.emporia_ev.coordinator import EmporiaDataUpdateCoordinator
from custom_components.emporia_ev.entity import EmporiaBaseEntity


async def _coordinator(hass, entry, client):
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


async def test_entity_identity_and_device(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """Entity has correct unique_id, device info, and initial availability."""
    coordinator = await _coordinator(hass, mock_config_entry, mock_client)
    entity = EmporiaBaseEntity(coordinator, "chg-1", "charging")

    assert entity.unique_id == "SN-chg-1_charging"
    assert entity.has_entity_name is True
    assert (DOMAIN, "SN-chg-1") in entity.device_info["identifiers"]
    assert entity.device_info["name"] == "Garage"
    assert entity.device_info["manufacturer"] == "Emporia"
    assert entity.device_info["model"] == "EV-100"
    assert entity.available is True


async def test_entity_unavailable_when_charger_absent(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    """Entity becomes unavailable when charger_id is removed from coordinator.data."""
    coordinator = await _coordinator(hass, mock_config_entry, mock_client)
    entity = EmporiaBaseEntity(coordinator, "chg-1", "charging")
    coordinator.async_set_updated_data({})
    assert entity.available is False
