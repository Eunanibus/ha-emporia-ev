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
from .oauth import (
    AUTHORIZE_URL,
    PROVIDER_APPLE,
    PROVIDER_GOOGLE,
    PROVIDERS,
    SCOPE,
    TOKEN_URL,
    PkceChallenge,
    async_revoke,
    email_from_id_token,
    generate_pkce,
    menu_id_for_provider,
)

__all__ = [
    "AUTHORIZE_URL",
    "BASE_URL",
    "PROVIDERS",
    "PROVIDER_APPLE",
    "PROVIDER_GOOGLE",
    "SCOPE",
    "STATE_CHARGING",
    "STATE_ERROR",
    "STATE_NOT_PLUGGED_IN",
    "STATE_PLUGGED_IN_IDLE",
    "TOKEN_URL",
    "AuthError",
    "Charger",
    "ChargerStatus",
    "EmporiaAuth",
    "EmporiaClient",
    "EmporiaConnectionError",
    "EmporiaError",
    "PkceChallenge",
    "RateLimitError",
    "Vehicle",
    "async_revoke",
    "email_from_id_token",
    "generate_pkce",
    "menu_id_for_provider",
]
