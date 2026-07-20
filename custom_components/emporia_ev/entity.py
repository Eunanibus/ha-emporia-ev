"""Shared CoordinatorEntity base for Emporia EV charger entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client import Charger, ChargerStatus
from .const import DOMAIN, MANUFACTURER
from .coordinator import EmporiaDataUpdateCoordinator


class EmporiaBaseEntity(CoordinatorEntity[EmporiaDataUpdateCoordinator]):
    """Base entity: device grouping, stable unique_id, availability."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._charger_id = charger_id
        self._key = key
        charger = coordinator.chargers[charger_id]
        self._serial = charger.serial
        self._attr_unique_id = f"{charger.serial}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger.serial)},
            name=charger.name,
            manufacturer=MANUFACTURER,
            model=charger.model,
            serial_number=charger.serial,
        )

    @property
    def charger(self) -> Charger:
        """Return the charger device."""
        return self.coordinator.chargers[self._charger_id]

    @property
    def status(self) -> ChargerStatus | None:
        """Return the charger status, or None if not available."""
        return self.coordinator.data.get(self._charger_id)

    @property
    def available(self) -> bool:
        """Return True if charger is available and has current status data."""
        return (
            super().available
            and self.coordinator.data is not None
            and self._charger_id in self.coordinator.data
        )
