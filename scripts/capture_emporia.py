"""THROWAWAY capture tool: log in to Emporia, dump raw API responses.

Writes UNSCRUBBED JSON to tests/library/fixtures/raw/ (gitignored). Run
scripts/scrub_fixtures.py afterwards; only scrubbed output may be committed.

Usage:
    export EMPORIA_USERNAME=... EMPORIA_PASSWORD=... EMPORIA_POOL_ID=... EMPORIA_CLIENT_ID=...
    python scripts/capture_emporia.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import aiohttp
from pycognito import Cognito  # type: ignore[import-untyped]

BASE_URL = "https://api.emporiaenergy.com"
RAW_DIR = Path("tests/library/fixtures/raw")

POOL_ID = os.environ["EMPORIA_POOL_ID"]
CLIENT_ID = os.environ["EMPORIA_CLIENT_ID"]


def _login() -> str:
    user = Cognito(POOL_ID, CLIENT_ID, username=os.environ["EMPORIA_USERNAME"])
    user.authenticate(password=os.environ["EMPORIA_PASSWORD"])
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "cognito_auth_response.raw.json").write_text(
        json.dumps(
            {
                "AuthenticationResult": {
                    "IdToken": user.id_token,
                    "AccessToken": user.access_token,
                    "RefreshToken": user.refresh_token,
                    "ExpiresIn": 3600,
                    "TokenType": "Bearer",
                }
            },
            indent=2,
        )
    )
    return user.id_token


async def _dump(session: aiohttp.ClientSession, path: str, out_name: str) -> dict:
    async with session.get(f"{BASE_URL}/{path}") as resp:
        resp.raise_for_status()
        body = await resp.json()
    (RAW_DIR / out_name).write_text(json.dumps(body, indent=2))
    print(f"captured {path} -> {out_name}")
    return body


def _first_charger(status: dict) -> dict | None:
    for device in status.get("devices", []):
        if device.get("evCharger"):
            return device["evCharger"]
    return None


async def main() -> None:
    token = _login()
    headers = {"authtoken": token}
    # Force aiohttp's threaded DNS resolver: the installed aiodns/pycares pair
    # has an incompatible Channel.getaddrinfo() signature. This only affects
    # this throwaway capture script; the HA integration uses HA's own session.
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        await _dump(session, "customers/devices", "devices.raw.json")
        status = await _dump(session, "customers/devices/status", "device_status.raw.json")
        charger = _first_charger(status)
        if charger is not None:
            payload = {
                "deviceGid": charger.get("deviceGid") or charger.get("parentDeviceGid"),
                "chargerOn": charger.get("chargerOn", False),
                "chargingRate": charger.get("chargingRate", 6),
                "maxChargingRate": charger.get("maxChargingRate", 48),
            }
            async with session.put(f"{BASE_URL}/devices/evcharger", json=payload) as resp:
                resp.raise_for_status()
                echo = await resp.json()
            (RAW_DIR / "set_charger_response.raw.json").write_text(json.dumps(echo, indent=2))
            print("captured PUT devices/evcharger -> set_charger_response.raw.json")


if __name__ == "__main__":
    asyncio.run(main())
