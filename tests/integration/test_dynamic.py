"""Tests for the dynamic charger-add helper."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
import pytest

from custom_components.emporia_ev.coordinator import EmporiaDataUpdateCoordinator
from custom_components.emporia_ev.dynamic import async_setup_charger_platform

from .conftest import make_charger, make_status


async def _coordinator(hass, entry, client):
    entry.add_to_hass(hass)
    coordinator = EmporiaDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_refresh()
    return coordinator


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_dynamic_add_initial_and_new(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    coordinator = await _coordinator(hass, mock_config_entry, mock_client)
    added: list[str] = []

    def build(charger_id: str) -> list[Entity]:
        ent = Entity()
        ent._attr_unique_id = f"{charger_id}_probe"
        added.append(charger_id)
        return [ent]

    def add_entities(entities, update_before_add=False):
        list(entities)

    async_setup_charger_platform(coordinator, add_entities, build)
    assert added == ["chg-1"]

    coordinator.chargers = {
        "chg-1": make_charger("chg-1"),
        "chg-2": make_charger("chg-2", name="Driveway"),
    }
    coordinator.async_set_updated_data({"chg-1": make_status(), "chg-2": make_status()})
    assert added == ["chg-1", "chg-2"]

    coordinator.async_set_updated_data({"chg-2": make_status()})
    assert added == ["chg-1", "chg-2"]
