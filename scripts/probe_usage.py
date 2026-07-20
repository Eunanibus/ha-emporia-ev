"""THROWAWAY probe: does Emporia expose power/energy for the EV charger?

Hits getDeviceListUsages for the charger's deviceGid two ways (instant WATTS,
and 1H KilowattHours) and dumps the raw responses to fixtures/raw/ so we can
decide whether Power/Energy sensors are feasible in v1.

Usage:
    set -a && . scripts/.emporia_env && set +a
    ./.venv/bin/python scripts/probe_usage.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
    return user.id_token


def _charger_gid() -> int:
    dev = json.loads((RAW_DIR / "devices.raw.json").read_text())
    return dev["devices"][0]["evCharger"]["deviceGid"]


async def _get(session: aiohttp.ClientSession, path: str, out: str) -> None:
    async with session.get(f"{BASE_URL}/{path}") as resp:
        text = await resp.text()
        print(f"{path.split('&')[1]} -> HTTP {resp.status}")
    (RAW_DIR / out).write_text(text)


async def main() -> None:
    token = _login()
    gid = _charger_gid()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    headers = {"authtoken": token}
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        # Instantaneous power in WATTS at scale 1S.
        await _get(
            session,
            f"AppAPI?apiMethod=getDeviceListUsages&deviceGids={gid}"
            f"&instant={now}&scale=1S&energyUnit=WATTS",
            "usage_watts.raw.json",
        )
        # 1-hour bucket in KilowattHours.
        await _get(
            session,
            f"AppAPI?apiMethod=getDeviceListUsages&deviceGids={gid}"
            f"&instant={now}&scale=1H&energyUnit=KilowattHours",
            "usage_kwh.raw.json",
        )


if __name__ == "__main__":
    asyncio.run(main())
