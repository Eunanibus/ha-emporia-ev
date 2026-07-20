"""Tests for EmporiaClient — driven by the real committed fixtures.

Key fixture values verified from tests/library/fixtures/:
  devices.json:
    customerGid: "1111111111"
    devices[0].deviceGid: "1111111111"
    devices[0].evCharger.chargingRate: 40

  device_status.json:
    evChargers[0].deviceGid: "1111111111"
    evChargers[0].chargingRate: 40
    evChargers[0].icon: "CarNotConnected" → no vehicle, not plugged in

  usage_1min.json:
    deviceListUsages.devices[0].deviceGid: "1111111111"
    deviceListUsages.devices[0].channelUsages[0].usage: 0.0

ThreadedResolver is used in every test to prevent the pycares background-thread
leak that fails the pytest-homeassistant-custom-component cleanup check.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.emporia_ev.client.client import BASE_URL, EmporiaClient
from custom_components.emporia_ev.client.errors import (
    AuthError,
    EmporiaConnectionError,
    RateLimitError,
)
from custom_components.emporia_ev.client.models import Charger, ChargerStatus

FIXTURES = Path(__file__).parent / "fixtures"


def _content_length_header(payload: dict) -> dict:  # type: ignore[type-arg]
    """Return a headers dict with Content-Length set for the given payload.

    aioresponses doesn't set Content-Length by default, so all responses
    have content_length=None. For 200 responses with actual bodies, we need
    to explicitly set the header so the guard `if not resp.content_length`
    can distinguish between empty (None/0) and present bodies.
    """
    body_bytes = json.dumps(payload).encode("utf-8")
    return {"Content-Length": str(len(body_bytes))}


# Real fixture values (verified from committed fixtures 2026-07-20)
_DEVICE_GID = "1111111111"
_CUSTOMER_GID = "1111111111"  # customerGid at top level of devices.json
_CHARGING_RATE = 40  # chargingRate in both devices.json evCharger and device_status.json


def _load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / name).read_text())  # type: ignore[return-value]


def _stub_auth() -> AsyncMock:
    """Return a mock EmporiaAuth with async_get_access_token as coroutine.

    auth_headers() is a synchronous method on the real EmporiaAuth. AsyncMock
    makes ALL methods async, which would cause auth_headers() to return a
    coroutine instead of a dict — breaking the aioresponses header-processing
    pipeline. We explicitly replace it with a plain MagicMock.
    """
    auth = AsyncMock()
    auth.async_get_access_token.return_value = "ACCESS"
    # auth_headers is sync — replace with a plain MagicMock so it returns a dict
    auth.auth_headers = MagicMock(return_value={"authtoken": "ID"})
    return auth


def _session() -> aiohttp.ClientSession:
    """Return a ClientSession backed by ThreadedResolver.

    The default resolver (c-ares via pycares) starts a daemon background
    thread (*_run_safe_shutdown_loop*) on the first DNS look-up.  That
    thread lingers past the pytest-homeassistant-custom-component
    ``verify_cleanup`` fixture's thread check, causing a false positive.
    aiohttp.ThreadedResolver uses Python's stdlib getaddrinfo instead and
    never starts a background thread, so the cleanup check stays clean.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    return aiohttp.ClientSession(connector=connector)


# ---------------------------------------------------------------------------
# async_get_chargers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chargers_parses_device_list_and_sets_account_id() -> None:
    """async_get_chargers returns Charger list and sets account_id from customerGid."""
    payload = _load("devices.json")
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices",
                status=200,
                payload=payload,
                headers=_content_length_header(payload),
            )
            chargers = await client.async_get_chargers()

    assert chargers, "expected at least one charger"
    assert all(isinstance(c, Charger) for c in chargers)
    assert all(c.id for c in chargers)
    # account_id should be set from customerGid = "1111111111"
    assert client.account_id == _CUSTOMER_GID


# ---------------------------------------------------------------------------
# async_get_charger_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_charger_status_keyed_by_str_device_gid() -> None:
    """async_get_charger_status returns dict[str, ChargerStatus] from evChargers[]."""
    payload = _load("device_status.json")
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices/status",
                status=200,
                payload=payload,
                headers=_content_length_header(payload),
            )
            status = await client.async_get_charger_status()

    assert isinstance(status, dict)
    # The known device GID must be present as a string key
    assert _DEVICE_GID in status, f"expected key '{_DEVICE_GID}' in status dict"
    st = status[_DEVICE_GID]
    assert isinstance(st, ChargerStatus)
    # chargingRate in fixture is 40
    assert st.charge_rate_amps == _CHARGING_RATE


# ---------------------------------------------------------------------------
# async_get_vehicles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_vehicles_returns_empty_when_no_car_connected() -> None:
    """async_get_vehicles returns {} when device_status has no vehicle block.

    The device_status.json fixture has icon="CarNotConnected" and no vehicle
    sub-dict on the evChargers entry — Vehicle.from_device returns None for
    each entry, so the result is an empty dict.
    """
    payload = _load("device_status.json")
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices/status",
                status=200,
                payload=payload,
                headers=_content_length_header(payload),
            )
            vehicles = await client.async_get_vehicles()

    assert isinstance(vehicles, dict)
    assert vehicles == {}


# ---------------------------------------------------------------------------
# async_get_energy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_energy_parses_usage_1min_fixture() -> None:
    """async_get_energy returns {gid: 0.0} from usage_1min.json fixture.

    Asserts the URL contains getDeviceListUsages, scale=1MIN, energyUnit=KilowattHours
    by registering the full expected URL with aioresponses (it only matches
    if the client builds the exact URL).
    """
    instant = "2026-07-20T18:13:32Z"
    expected_url = (
        f"{BASE_URL}/AppAPI"
        f"?apiMethod=getDeviceListUsages"
        f"&deviceGids={_DEVICE_GID}"
        f"&instant={instant}"
        f"&scale=1MIN"
        f"&energyUnit=KilowattHours"
    )

    payload = _load("usage_1min.json")
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                expected_url,
                status=200,
                payload=payload,
                headers=_content_length_header(payload),
            )
            result = await client.async_get_energy([_DEVICE_GID], instant=instant)

    # usage_1min.json channelUsages[0].usage is 0.0
    assert result == {_DEVICE_GID: 0.0}


# ---------------------------------------------------------------------------
# async_set_charger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_charger_puts_expected_payload() -> None:
    """async_set_charger PUTs with chargerOn, chargingRate, and deviceGid in the body."""
    captured: dict = {}  # type: ignore[type-arg]

    def _capture_body(url: object, **kwargs: object) -> None:
        captured.update(kwargs.get("json") or {})  # type: ignore[arg-type]

    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.put(
                f"{BASE_URL}/devices/evcharger",
                status=200,
                payload={},
                headers=_content_length_header({}),
                callback=_capture_body,
            )
            await client.async_set_charger("42", enabled=True, charge_rate_amps=32)

    assert captured.get("chargerOn") is True
    assert captured.get("chargingRate") == 32
    assert captured.get("deviceGid") == 42


@pytest.mark.asyncio
async def test_set_charger_echoes_full_object_after_status_poll() -> None:
    """Regression: Emporia rejects a partial set-charger body with HTTP 400.

    After a status poll caches the raw evChargers[] object, the PUT body must
    echo the FULL object (loadGid, maxChargingRate, breakerPIN when present) with
    only chargerOn/chargingRate mutated — mirroring PyEmVue's as_dictionary().
    A live test previously failed with 400 because we sent only 3 fields.
    """
    status_payload = _load("device_status.json")
    # Pull the real charger's identifiers from the fixture to assert against.
    evc = status_payload["evChargers"][0]
    gid = str(evc["deviceGid"])
    captured: dict = {}  # type: ignore[type-arg]

    def _capture_body(url: object, **kwargs: object) -> None:
        captured.update(kwargs.get("json") or {})  # type: ignore[arg-type]

    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices/status",
                status=200,
                payload=status_payload,
                headers=_content_length_header(status_payload),
            )
            mocked.put(
                f"{BASE_URL}/devices/evcharger",
                status=200,
                payload={},
                headers=_content_length_header({}),
                callback=_capture_body,
            )
            await client.async_get_charger_status()  # caches the raw object
            await client.async_set_charger(gid, enabled=False, charge_rate_amps=24)

    # Mutated fields
    assert captured.get("chargerOn") is False
    assert captured.get("chargingRate") == 24
    # Full-object fields echoed from the cached status (these were MISSING before,
    # causing the 400). loadGid + maxChargingRate must be present.
    assert captured.get("deviceGid") == evc["deviceGid"]
    assert captured.get("loadGid") == evc["loadGid"]
    assert captured.get("maxChargingRate") == evc["maxChargingRate"]


@pytest.mark.asyncio
async def test_set_charger_succeeds_with_204_no_content() -> None:
    """async_set_charger succeeds when PUT returns HTTP 204 with no body.

    HTTP 204 No Content has no response body and content_length may be None
    (not 0) for chunked responses. The _request method must handle both
    status==204 and falsy content_length to avoid calling resp.json() and
    raising.
    """
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.put(
                f"{BASE_URL}/devices/evcharger",
                status=204,
            )
            # Should complete without raising
            result = await client.async_set_charger("42", enabled=True, charge_rate_amps=32)

    assert result is None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_maps_to_autherror() -> None:
    """HTTP 401 is mapped to AuthError."""
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(f"{BASE_URL}/customers/devices/status", status=401)
            with pytest.raises(AuthError):
                await client.async_get_charger_status()


@pytest.mark.asyncio
async def test_429_maps_to_ratelimit_with_retry_after() -> None:
    """HTTP 429 with Retry-After header maps to RateLimitError with retry_after."""
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices/status",
                status=429,
                headers={"Retry-After": "30"},
            )
            with pytest.raises(RateLimitError) as exc_info:
                await client.async_get_charger_status()
    assert exc_info.value.retry_after == 30.0


@pytest.mark.asyncio
async def test_timeout_maps_to_connectionerror() -> None:
    """asyncio.TimeoutError (== TimeoutError in 3.11+) maps to EmporiaConnectionError."""
    async with _session() as session:
        client = EmporiaClient(session, _stub_auth())
        with aioresponses() as mocked:
            mocked.get(
                f"{BASE_URL}/customers/devices/status",
                exception=TimeoutError(),
            )
            with pytest.raises(EmporiaConnectionError):
                await client.async_get_charger_status()
