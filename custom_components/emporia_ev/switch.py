"""Charging switch for Emporia EV Charger."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EmporiaConfigEntry
from .client import EmporiaError
from .const import CONF_DEFAULT_AMPS, DEFAULT_AMPS, KEY_CHARGING, OPTIMISTIC_GRACE_SECONDS
from .coordinator import EmporiaDataUpdateCoordinator
from .dynamic import async_setup_charger_platform
from .entity import EmporiaBaseEntity
from .optimistic import OptimisticState


async def async_setup_entry(
    hass: HomeAssistant, entry: EmporiaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    def build(charger_id: str) -> list[Entity]:
        return [EmporiaChargingSwitch(coordinator, charger_id)]

    async_setup_charger_platform(coordinator, async_add_entities, build)


class EmporiaChargingSwitch(EmporiaBaseEntity, SwitchEntity):
    """Enable/disable charging with anti-flicker optimistic state."""

    _attr_translation_key = KEY_CHARGING
    _attr_name = "Charging"

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_CHARGING)
        self._optimistic: OptimisticState[bool] = OptimisticState(
            grace=timedelta(seconds=OPTIMISTIC_GRACE_SECONDS)
        )

    @property
    def is_on(self) -> bool | None:
        status = self.status
        if status is None:
            return None
        return self._optimistic.value(coordinator_value=status.enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, enabled: bool) -> None:
        status = self.status
        prior = status.enabled if status is not None else not enabled
        amps = (
            status.charge_rate_amps
            if status is not None
            else self.coordinator.entry.options.get(CONF_DEFAULT_AMPS, DEFAULT_AMPS)
        )
        self._optimistic.set(enabled)
        self.async_write_ha_state()
        try:
            await self.coordinator.client.async_set_charger(
                self._charger_id, enabled=enabled, charge_rate_amps=amps
            )
        except EmporiaError as err:
            self._optimistic.set(prior)
            self.async_write_ha_state()
            self._optimistic.clear()
            raise HomeAssistantError(f"Failed to set charging: {err}") from err
        # No async_request_refresh here (anti-flicker); coordinator ticks reconcile.
