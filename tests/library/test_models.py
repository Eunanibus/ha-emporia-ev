"""Tests for client models — driven by the real committed fixtures.

Key fixture values (verified 2026-07-20):
  devices.json devices[0]:
    deviceGid: "1111111111" (string in fixture)
    model: "VVDN01"
    manufacturerDeviceId: "1111111111"
    locationProperties.deviceName: "701 Zephyr"
    evCharger.maxChargingRate: 40
    evCharger.chargerOn: true
    evCharger.chargingRate: 40
    evCharger.icon: "CarNotConnected"
    evCharger.faultText: null

  device_status.json evChargers[0]:
    chargerOn: true
    chargingRate: 40
    maxChargingRate: 40
    icon: "CarNotConnected"
    faultText: null

These tests follow the REAL fixture shapes — NOT the brief's assumed shapes.
ChargerStatus uses from_evcharger() (flat status entry), not from_device().
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from custom_components.emporia_ev.client.models import (
    STATE_CHARGING,
    STATE_ERROR,
    STATE_NOT_PLUGGED_IN,
    STATE_PLUGGED_IN_IDLE,
    Charger,
    ChargerStatus,
    Vehicle,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Charger — from devices.json
# ---------------------------------------------------------------------------


def test_charger_from_device_real_fixture() -> None:
    """Charger parsed from the first device in the committed devices.json fixture."""
    data = _load("devices.json")
    device = data["devices"][0]
    charger = Charger.from_device(device)

    # Non-empty identity fields
    assert charger.id  # "1111111111"
    assert charger.serial  # "1111111111"
    assert charger.name  # "701 Zephyr"

    # Exact values from fixture
    assert charger.model == "VVDN01"
    assert charger.max_amps == 40  # evCharger.maxChargingRate in fixture
    assert charger.min_amps == 6  # no minChargingRate in fixture → fallback


def test_charger_from_device_fallback_when_no_max_charging_rate() -> None:
    """Charger falls back to 48 A max / 6 A min when evCharger sub-dict has no rate."""
    device = {
        "deviceGid": 42,
        "model": "EVSE",
        "manufacturerDeviceId": "SN-1",
        "locationProperties": {"deviceName": "Garage"},
        "evCharger": {},  # no maxChargingRate / minChargingRate
    }
    charger = Charger.from_device(device)
    assert charger.id == "42"
    assert charger.name == "Garage"
    assert charger.model == "EVSE"
    assert charger.serial == "SN-1"
    assert charger.max_amps == 48  # default
    assert charger.min_amps == 6  # default


def test_charger_from_device_fallback_when_no_ev_charger_key() -> None:
    """Charger falls back to defaults when the evCharger key is absent entirely."""
    device: dict = {  # type: ignore[type-arg]
        "deviceGid": "99",
        "model": "UNKNOWN",
    }
    charger = Charger.from_device(device)
    assert charger.max_amps == 48
    assert charger.min_amps == 6
    # Name falls back to model since locationProperties absent
    assert charger.name == "UNKNOWN"


def test_charger_from_device_serial_falls_back_to_device_gid() -> None:
    """When manufacturerDeviceId is absent, serial falls back to str(deviceGid)."""
    device: dict = {"deviceGid": "77", "model": "X"}  # type: ignore[type-arg]
    charger = Charger.from_device(device)
    assert charger.serial == "77"
    assert charger.id == "77"


# ---------------------------------------------------------------------------
# ChargerStatus — from_evcharger() with FLAT status entry (device_status.json)
# ---------------------------------------------------------------------------


def test_charger_status_from_real_fixture() -> None:
    """ChargerStatus from the first flat entry in device_status.json evChargers."""
    data = _load("device_status.json")
    evc = data["evChargers"][0]
    status = ChargerStatus.from_evcharger(evc)

    # Exact fixture values
    assert status.enabled is True  # chargerOn: true
    assert status.charge_rate_amps == 40  # chargingRate: 40
    assert status.plugged_in is False  # icon: "CarNotConnected"
    assert status.charging_state == STATE_NOT_PLUGGED_IN
    assert status.power_w == 0.0  # not in status payload
    assert status.energy_kwh == 0.0  # not in status payload


def test_charger_status_state_charging() -> None:
    """STATE_CHARGING when chargerOn=True, chargingRate>0, icon != CarNotConnected."""
    evc = {
        "chargerOn": True,
        "chargingRate": 40,
        "icon": "Charging",
        "faultText": None,
    }
    status = ChargerStatus.from_evcharger(evc)
    assert status.enabled is True
    assert status.plugged_in is True
    assert status.charging_state == STATE_CHARGING
    assert status.charge_rate_amps == 40


def test_charger_status_state_plugged_in_idle() -> None:
    """STATE_PLUGGED_IN_IDLE when chargerOn=False and car IS connected."""
    evc = {
        "chargerOn": False,
        "chargingRate": 0,
        "icon": "Connected",  # any icon other than CarNotConnected
        "faultText": None,
    }
    status = ChargerStatus.from_evcharger(evc)
    assert status.enabled is False
    assert status.plugged_in is True
    assert status.charging_state == STATE_PLUGGED_IN_IDLE


def test_charger_status_state_error_from_fault_text() -> None:
    """STATE_ERROR when faultText is a non-empty string."""
    evc = {
        "chargerOn": False,
        "chargingRate": 0,
        "icon": "Error",
        "faultText": "GFCI fault detected",
    }
    status = ChargerStatus.from_evcharger(evc)
    assert status.charging_state == STATE_ERROR


def test_charger_status_state_not_plugged_in_explicit() -> None:
    """STATE_NOT_PLUGGED_IN when icon is exactly 'CarNotConnected'."""
    evc = {
        "chargerOn": False,
        "chargingRate": 0,
        "icon": "CarNotConnected",
        "faultText": None,
    }
    status = ChargerStatus.from_evcharger(evc)
    assert status.plugged_in is False
    assert status.charging_state == STATE_NOT_PLUGGED_IN


def test_charger_status_power_and_energy_always_zero() -> None:
    """power_w and energy_kwh are always 0.0 — coordinator fills them later."""
    evc = {"chargerOn": True, "chargingRate": 32, "icon": "Charging", "faultText": None}
    status = ChargerStatus.from_evcharger(evc)
    assert status.power_w == 0.0
    assert status.energy_kwh == 0.0


# ---------------------------------------------------------------------------
# Vehicle — best-effort (no car-connected fixture available)
# ---------------------------------------------------------------------------


def test_vehicle_absent_from_real_fixture_device() -> None:
    """devices.json device has no vehicle block → from_device returns None."""
    data = _load("devices.json")
    device = data["devices"][0]
    # No vehicle block in the fixture (no car was connected at capture)
    assert Vehicle.from_device(device) is None


def test_vehicle_absent_hand_built() -> None:
    """from_device returns None when device dict has no 'vehicle' key."""
    device: dict = {"deviceGid": "1"}  # type: ignore[type-arg]
    assert Vehicle.from_device(device) is None


def test_vehicle_present_hand_built() -> None:
    """from_device returns a Vehicle when a 'vehicle' sub-dict is present."""
    device: dict = {  # type: ignore[type-arg]
        "deviceGid": "1",
        "vehicle": {
            "batteryLevel": 80,
            "chargingStatus": "charging",
        },
    }
    vehicle = Vehicle.from_device(device)
    assert vehicle is not None
    assert vehicle.battery_pct == 80
    assert vehicle.charging_state is not None


def test_vehicle_present_null_battery() -> None:
    """Vehicle with no batteryLevel → battery_pct is None."""
    device: dict = {  # type: ignore[type-arg]
        "deviceGid": "1",
        "vehicle": {"chargingStatus": None},
    }
    vehicle = Vehicle.from_device(device)
    assert vehicle is not None
    assert vehicle.battery_pct is None
    assert vehicle.charging_state is None


# ---------------------------------------------------------------------------
# Frozen / slots
# ---------------------------------------------------------------------------


def test_charger_is_frozen() -> None:
    charger = Charger(id="1", name="n", model="m", serial="s", max_amps=48, min_amps=6)
    assert dataclasses.is_dataclass(charger)
    with pytest.raises(dataclasses.FrozenInstanceError):
        charger.name = "changed"  # type: ignore[misc]


def test_charger_status_is_frozen() -> None:
    status = ChargerStatus(
        enabled=False,
        charging_state=STATE_NOT_PLUGGED_IN,
        power_w=0.0,
        energy_kwh=0.0,
        plugged_in=False,
        charge_rate_amps=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.enabled = True  # type: ignore[misc]


def test_vehicle_is_frozen() -> None:
    vehicle = Vehicle(battery_pct=50, charging_state=STATE_CHARGING)
    with pytest.raises(dataclasses.FrozenInstanceError):
        vehicle.battery_pct = 60  # type: ignore[misc]
