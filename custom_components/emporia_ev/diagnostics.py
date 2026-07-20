"""Diagnostics support for Emporia EV Charger."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EmporiaConfigEntry

TO_REDACT = {
    "username",
    "password",
    "email",
    "id_token",
    "access_token",
    "refresh_token",
    "serial",
    "serial_number",
}


def _dump(obj: Any) -> Any:
    return asdict(obj) if is_dataclass(obj) and not isinstance(obj, type) else obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EmporiaConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    status = {cid: _dump(s) for cid, s in (coordinator.data or {}).items()}
    chargers = {cid: _dump(c) for cid, c in coordinator.chargers.items()}
    vehicles = {cid: _dump(v) for cid, v in coordinator.vehicles.items()}
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "chargers": async_redact_data(chargers, TO_REDACT),
        "status": async_redact_data(status, TO_REDACT),
        "vehicles": async_redact_data(vehicles, TO_REDACT),
    }
