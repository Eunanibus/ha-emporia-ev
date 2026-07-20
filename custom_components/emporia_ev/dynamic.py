"""Dynamic charger-entity addition shared across platforms."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EmporiaDataUpdateCoordinator


def async_setup_charger_platform(
    coordinator: EmporiaDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    build_entities: Callable[[str], list[Entity]],
) -> None:
    """Add entities for chargers as they appear; never rebuild existing ones.

    Disappeared chargers are handled by entity availability, not by removal.
    """
    known: set[str] = set()

    def _add_new() -> None:
        data = coordinator.data or {}
        current = set(coordinator.chargers) | set(data)
        new_ids = current - known
        entities: list[Entity] = []
        for charger_id in sorted(new_ids):
            if charger_id not in coordinator.chargers:
                continue
            entities.extend(build_entities(charger_id))
            known.add(charger_id)
        if entities:
            async_add_entities(entities)

    _add_new()
    coordinator.async_add_listener(_add_new)
