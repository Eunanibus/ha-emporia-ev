"""Charge-rate number entity for Emporia EV Charger."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EmporiaConfigEntry
from .client import EmporiaError
from .const import DEFAULT_MAX_AMPS, DEFAULT_MIN_AMPS, KEY_CHARGE_RATE, OPTIMISTIC_GRACE_SECONDS
from .coordinator import EmporiaDataUpdateCoordinator
from .dynamic import async_setup_charger_platform
from .entity import EmporiaBaseEntity
from .optimistic import OptimisticState


async def async_setup_entry(
    hass: HomeAssistant, entry: EmporiaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def build(charger_id: str) -> list[Entity]:
        return [EmporiaChargeRateNumber(coordinator, charger_id)]

    async_setup_charger_platform(coordinator, async_add_entities, build)


class EmporiaChargeRateNumber(EmporiaBaseEntity, NumberEntity):
    """Charge-rate slider in amps."""

    _attr_translation_key = KEY_CHARGE_RATE
    _attr_name = "Charge rate"
    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_step = 1
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_CHARGE_RATE)
        charger = coordinator.chargers[charger_id]
        self._attr_native_min_value = (
            charger.min_amps if charger.min_amps is not None else DEFAULT_MIN_AMPS
        )
        self._attr_native_max_value = (
            charger.max_amps if charger.max_amps is not None else DEFAULT_MAX_AMPS
        )
        self._optimistic: OptimisticState[float] = OptimisticState(
            grace=timedelta(seconds=OPTIMISTIC_GRACE_SECONDS)
        )

    @property
    def native_value(self) -> float | None:
        status = self.status
        if status is None:
            return None
        return self._optimistic.value(coordinator_value=float(status.charge_rate_amps))

    async def async_set_native_value(self, value: float) -> None:
        status = self.status
        prior = float(status.charge_rate_amps) if status is not None else value
        enabled = status.enabled if status is not None else True
        amps = round(value)
        self._optimistic.set(float(amps))
        self.async_write_ha_state()
        try:
            await self.coordinator.client.async_set_charger(
                self._charger_id, enabled=enabled, charge_rate_amps=amps
            )
        except EmporiaError as err:
            self._optimistic.set(prior)
            self.async_write_ha_state()
            self._optimistic.clear()
            raise HomeAssistantError(f"Failed to set charge rate: {err}") from err
