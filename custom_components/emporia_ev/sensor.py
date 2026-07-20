"""Sensor entities for Emporia EV Charger."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EmporiaConfigEntry
from .const import KEY_ENERGY, KEY_POWER, KEY_STATUS, KEY_VEHICLE_BATTERY, STATUS_OPTIONS
from .coordinator import EmporiaDataUpdateCoordinator
from .dynamic import async_setup_charger_platform
from .entity import EmporiaBaseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: EmporiaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Emporia EV Charger sensor platform."""
    coordinator: EmporiaDataUpdateCoordinator = entry.runtime_data

    def build(charger_id: str) -> list[Entity]:
        entities: list[Entity] = [
            EmporiaPowerSensor(coordinator, charger_id),
            EmporiaEnergySensor(coordinator, charger_id),
            EmporiaStatusSensor(coordinator, charger_id),
        ]
        if charger_id in coordinator.vehicles:
            entities.append(EmporiaVehicleBatterySensor(coordinator, charger_id))
        return entities

    async_setup_charger_platform(coordinator, async_add_entities, build)


class EmporiaPowerSensor(EmporiaBaseEntity, SensorEntity):
    """Instantaneous charging power (W).

    Reads power_w from ChargerStatus, which is computed by the coordinator as
    the average watts over the last 1-minute energy bucket (kWh * 60 * 1000).
    """

    _attr_translation_key = KEY_POWER
    _attr_name = "Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_POWER)

    @property
    def native_value(self) -> float | None:
        status = self.status
        return status.power_w if status is not None else None


class EmporiaEnergySensor(EmporiaBaseEntity, SensorEntity):
    """Per-1-minute kWh bucket — plain MEASUREMENT sensor, NO device_class.

    HA forbids device_class=ENERGY with state_class=MEASUREMENT (ENERGY requires
    TOTAL or TOTAL_INCREASING). This sensor reports the kWh consumed in the most
    recent 1-minute window, which resets each minute and is not monotonic, so
    TOTAL_INCREASING would be wrong. The Energy-dashboard lifetime total should
    be derived from a Riemann-sum helper on the POWER sensor instead.
    """

    _attr_translation_key = KEY_ENERGY
    _attr_name = "Energy (last minute)"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_ENERGY)

    @property
    def native_value(self) -> float | None:
        status = self.status
        return status.energy_kwh if status is not None else None


class EmporiaStatusSensor(EmporiaBaseEntity, SensorEntity):
    """Charger status as an enum with explicit options + translation_key."""

    _attr_translation_key = "charger_status"
    _attr_name = "Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUS_OPTIONS

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_STATUS)

    @property
    def native_value(self) -> str | None:
        status = self.status
        if status is None:
            return None
        state = status.charging_state
        return state if state in STATUS_OPTIONS else "error"


class EmporiaVehicleBatterySensor(EmporiaBaseEntity, SensorEntity):
    """Linked-vehicle battery percentage (created only when a vehicle is present).

    This entity is only instantiated when coordinator.vehicles contains the
    charger's ID at setup time. The available property also guards against
    the vehicle disappearing after initial setup.
    """

    _attr_translation_key = KEY_VEHICLE_BATTERY
    _attr_name = "Vehicle battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: EmporiaDataUpdateCoordinator, charger_id: str) -> None:
        super().__init__(coordinator, charger_id, KEY_VEHICLE_BATTERY)

    @property
    def native_value(self) -> float | None:
        vehicle = self.coordinator.vehicles.get(self._charger_id)
        return vehicle.battery_pct if vehicle is not None else None

    @property
    def available(self) -> bool:
        return super().available and self._charger_id in self.coordinator.vehicles
