"""Constants for the Emporia EV Charger integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "emporia_ev"

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

# Options-flow keys
CONF_IDLE_INTERVAL = "idle_interval"
CONF_CHARGING_INTERVAL = "charging_interval"
CONF_ADAPTIVE = "adaptive"
CONF_DEFAULT_AMPS = "default_amps"

# Defaults
DEFAULT_IDLE_INTERVAL = 30  # seconds
DEFAULT_CHARGING_INTERVAL = 15  # seconds
DEFAULT_ADAPTIVE = True
DEFAULT_MIN_AMPS = 6
DEFAULT_MAX_AMPS = 48
DEFAULT_AMPS = 32

# Adaptive-interval hysteresis: relax to idle only after N non-charging polls.
RELAX_AFTER_N = 2

# Optimistic-command grace window (seconds). Must comfortably exceed Emporia's
# cloud propagation lag (a command takes several seconds to reflect in the
# status endpoint) so the switch/number don't briefly flip back to the stale
# pre-command value before the cloud catches up. ~3 charging poll cycles.
OPTIMISTIC_GRACE_SECONDS = 45

MANUFACTURER = "Emporia"

# Entity keys (unique_id = f"{serial}_{key}")
KEY_CHARGING = "charging"
KEY_CHARGE_RATE = "charge_rate"
KEY_POWER = "power"
KEY_ENERGY = "energy"
KEY_STATUS = "status"
KEY_PLUGGED_IN = "plugged_in"
KEY_VEHICLE_BATTERY = "vehicle_battery"

# Status enum — must match client charging_state values + translation keys.
STATUS_OPTIONS = ["charging", "plugged_in_idle", "not_plugged_in", "error"]
