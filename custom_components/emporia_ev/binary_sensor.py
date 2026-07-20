"""Binary sensors for Emporia EV Charger."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EmporiaConfigEntry
from .const import KEY_PLUGGED_IN
from .coordinator import EmporiaDataUpdateCoordinator
from .dynamic import async_setup_charger_platform
from .entity import EmporiaBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EmporiaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Emporia EV Charger binary sensor platform."""
    coordinator: EmporiaDataUpdateCoordinator = entry.runtime_data

    def build(charger_id: str) -> list[Entity]:
        return [EmporiaPluggedInBinarySensor(coordinator, charger_id)]

    async_setup_charger_platform(coordinator, async_add_entities, build)


class EmporiaPluggedInBinarySensor(EmporiaBaseEntity, BinarySensorEntity):
    """Whether a vehicle is plugged into the charger."""

    _attr_translation_key = KEY_PLUGGED_IN
    _attr_name = "Plugged in"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_PLUGGED_IN)

    @property
    def is_on(self) -> bool | None:
        status = self.status
        return status.plugged_in if status is not None else None
