"""Typed, immutable models for Emporia chargers, parsed from the cloud API.

Key names (deviceGid, chargerOn, chargingRate, maxChargingRate, icon, ...)
come from the captured fixtures in tests/library/fixtures/ (Task 3 capture,
2026-07-20). If the live API shape changes, update these parsers AND the
fixtures together — they must agree.

Important: ChargerStatus.from_evcharger() takes a FLAT evCharger dict from
the ``evChargers[]`` list in the ``GET customers/devices/status`` response —
NOT a device entry from ``GET customers/devices``. Task 7 calls it while
iterating ``payload["evChargers"]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

STATE_CHARGING = "charging"
STATE_PLUGGED_IN_IDLE = "plugged_in_idle"
STATE_NOT_PLUGGED_IN = "not_plugged_in"
STATE_ERROR = "error"

# ---------------------------------------------------------------------------
# Internal defaults
# ---------------------------------------------------------------------------

_DEFAULT_MIN_AMPS: int = 6
_DEFAULT_MAX_AMPS: int = 48


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _device_id(device: dict[str, Any]) -> str:
    return str(device["deviceGid"])


def _device_name(device: dict[str, Any]) -> str:
    props = device.get("locationProperties") or {}
    return props.get("deviceName") or device.get("model") or _device_id(device)


def _derive_charging_state(evc: dict[str, Any]) -> str:
    """Derive the human-readable charging state from a flat evCharger status dict.

    Decision logic (in priority order):
    1. Any non-null faultText → STATE_ERROR.
    2. icon == "CarNotConnected" → STATE_NOT_PLUGGED_IN.
    3. chargerOn=True AND chargingRate>0 → STATE_CHARGING.
    4. Otherwise → STATE_PLUGGED_IN_IDLE (car connected but not drawing power).
    """
    if evc.get("faultText"):
        return STATE_ERROR
    if evc.get("icon") == "CarNotConnected":
        return STATE_NOT_PLUGGED_IN
    if evc.get("chargerOn") and int(evc.get("chargingRate", 0) or 0) > 0:
        return STATE_CHARGING
    return STATE_PLUGGED_IN_IDLE


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Charger:
    """Static identity and capability of a single EV charger.

    Parsed from a device entry in ``GET customers/devices`` (``devices.json``
    fixture). Fields are stable across polling cycles.
    """

    id: str
    name: str
    model: str
    serial: str
    max_amps: int
    min_amps: int

    @classmethod
    def from_device(cls, device: dict[str, Any]) -> Charger:
        """Build a Charger from one entry in the ``devices`` list.

        Args:
            device: A single dict from ``devices.json["devices"]``.

        Returns:
            A frozen Charger instance.
        """
        evc = device.get("evCharger") or {}
        raw_max = evc.get("maxChargingRate")
        raw_min = evc.get("minChargingRate")  # not observed in API; future-proof
        return cls(
            id=_device_id(device),
            name=_device_name(device),
            model=str(device.get("model") or ""),
            serial=str(device.get("manufacturerDeviceId") or device["deviceGid"]),
            max_amps=int(raw_max) if raw_max is not None else _DEFAULT_MAX_AMPS,
            min_amps=int(raw_min) if raw_min is not None else _DEFAULT_MIN_AMPS,
        )


@dataclass(frozen=True, slots=True)
class ChargerStatus:
    """Volatile per-poll state of a single EV charger.

    Parsed from a FLAT entry in the ``evChargers[]`` list of
    ``GET customers/devices/status`` (``device_status.json`` fixture).

    Note: ``power_w`` and ``energy_kwh`` are NOT in the status payload.
    They are always 0.0 here; the coordinator (Task 9) fills them from the
    ``getDeviceListUsages`` endpoint.
    """

    enabled: bool
    charging_state: str
    power_w: float
    energy_kwh: float
    plugged_in: bool
    charge_rate_amps: int

    @classmethod
    def from_evcharger(cls, evc: dict[str, Any]) -> ChargerStatus:
        """Build a ChargerStatus from one flat entry in ``evChargers[]``.

        Args:
            evc: A single dict from ``device_status.json["evChargers"]``.
                 Fields are flat (no nested ``evCharger`` sub-dict).

        Returns:
            A frozen ChargerStatus instance with ``power_w`` and
            ``energy_kwh`` set to 0.0 (filled later by the coordinator).
        """
        return cls(
            enabled=bool(evc.get("chargerOn", False)),
            charging_state=_derive_charging_state(evc),
            power_w=0.0,
            energy_kwh=0.0,
            plugged_in=evc.get("icon") != "CarNotConnected",
            charge_rate_amps=int(evc.get("chargingRate", 0) or 0),
        )


@dataclass(frozen=True, slots=True)
class Vehicle:
    """Optional linked-vehicle state.

    ``None`` is returned by ``from_device`` when no vehicle block exists on the
    device entry — i.e. when no car was connected at capture time.

    PROVISIONAL: The ``vehicle`` sub-dict shape has NOT been observed in a live
    capture (the only capture was with no car plugged in, so the field was
    absent). Field names ``batteryLevel`` / ``chargingStatus`` are inferred from
    the HA ecosystem and similar integrations. Re-capture with a car plugged in
    to pin the exact keys and update this parser accordingly.
    """

    battery_pct: int | None
    charging_state: str | None

    @classmethod
    def from_device(cls, device: dict[str, Any]) -> Vehicle | None:
        """Build a Vehicle from the ``vehicle`` sub-dict of a device entry, or None.

        Args:
            device: A single dict from ``devices.json["devices"]``.

        Returns:
            A frozen Vehicle instance, or None when no ``vehicle`` block exists.
        """
        vehicle = device.get("vehicle")
        if not vehicle:
            return None
        battery = vehicle.get("batteryLevel")
        status = vehicle.get("chargingStatus")
        return cls(
            battery_pct=int(battery) if battery is not None else None,
            charging_state=str(status).lower() if status is not None else None,
        )
