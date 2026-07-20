"""Bundled async Emporia cloud client (no Home Assistant imports)."""

from __future__ import annotations

from .auth import EmporiaAuth
from .client import BASE_URL, EmporiaClient
from .errors import (
    AuthError,
    EmporiaConnectionError,
    EmporiaError,
    RateLimitError,
)
from .models import (
    STATE_CHARGING,
    STATE_ERROR,
    STATE_NOT_PLUGGED_IN,
    STATE_PLUGGED_IN_IDLE,
    Charger,
    ChargerStatus,
    Vehicle,
)

__all__ = [
    "BASE_URL",
    "STATE_CHARGING",
    "STATE_ERROR",
    "STATE_NOT_PLUGGED_IN",
    "STATE_PLUGGED_IN_IDLE",
    "AuthError",
    "Charger",
    "ChargerStatus",
    "EmporiaAuth",
    "EmporiaClient",
    "EmporiaConnectionError",
    "EmporiaError",
    "RateLimitError",
    "Vehicle",
]
