"""Async REST client for the Emporia cloud EV-charger API.

Accepts an injected aiohttp session (HA owns its lifecycle) and an EmporiaAuth.
All network methods map HTTP/transport failures to the typed error hierarchy so
the HA coordinator can react precisely (reauth / backoff / mark-unavailable).

Key design notes (from live capture, 2026-07-20):
- ``GET customers/devices`` returns the device list; top-level ``customerGid``
  is the account id used as the HA config-entry unique_id.
- ``GET customers/devices/status`` returns a flat ``evChargers[]`` list — NOT a
  ``devices`` list. Iterate that directly.
- Energy is NOT in the status payload. Use ``GET AppAPI?apiMethod=getDeviceListUsages``
  with ``scale=1MIN&energyUnit=KilowattHours``. The ``instant`` param must be
  passed in by the caller (coordinator) so tests can inject a fixed timestamp.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .auth import EmporiaAuth
from .errors import AuthError, EmporiaConnectionError, RateLimitError
from .models import Charger, ChargerStatus, Vehicle

BASE_URL = "https://api.emporiaenergy.com"

# Bounded retry for TRANSIENT connection blips (flaky DNS/IPv6 to the Emporia
# cloud). A momentary connect failure is retried a couple of times with short
# backoff so it never surfaces as a hard error — e.g. so the first poll after
# setup doesn't trip HA's "Failed setup, will retry" banner. Auth (401) and
# rate-limit (429) errors are NOT retried here; they surface immediately.
_MAX_REQUEST_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0


def _parse_retry_after(headers: Any) -> float | None:
    """Extract the Retry-After value (seconds) from response headers, or None."""
    raw = headers.get("Retry-After") if headers else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_account_id(payload: dict[str, Any]) -> str | None:
    """Pull customerGid from the customers/devices payload."""
    if "customerGid" in payload:
        return str(payload["customerGid"])
    return None


class EmporiaClient:
    """Typed async facade over the Emporia cloud REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        auth: EmporiaAuth,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        self._session = session
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self.account_id: str | None = None
        # Last-seen raw evChargers[] object per charger id. The set-charger PUT
        # must echo back the FULL charger object (deviceGid, loadGid, chargerOn,
        # chargingRate, maxChargingRate, breakerPIN, ...) — Emporia rejects a
        # partial body with HTTP 400 — so we cache what the status endpoint gave
        # us and mutate only chargerOn/chargingRate when sending a command.
        self._raw_chargers: dict[str, dict[str, Any]] = {}

    async def authenticate(self) -> None:
        """Ensure a valid token AND populate account_id (raises AuthError)."""
        await self._auth.async_get_access_token()
        if self.account_id is None:
            await self.async_get_chargers()

    async def async_get_chargers(self) -> list[Charger]:
        """GET customers/devices → list of Charger objects.

        Also sets ``self.account_id`` from the payload's ``customerGid`` if
        not already set.
        """
        payload = await self._request("GET", "customers/devices")
        if self.account_id is None:
            self.account_id = _extract_account_id(payload)
        return [Charger.from_device(d) for d in payload.get("devices", []) if d.get("evCharger")]

    async def async_get_charger_status(self) -> dict[str, ChargerStatus]:
        """GET customers/devices/status → ``{str(deviceGid): ChargerStatus}``.

        Iterates the flat ``evChargers[]`` list — NOT ``devices`` and NOT a
        nested ``evCharger`` sub-dict.
        """
        payload = await self._request("GET", "customers/devices/status")
        result: dict[str, ChargerStatus] = {}
        for evc in payload.get("evChargers", []):
            cid = str(evc["deviceGid"])
            # Cache the full raw object so async_set_charger can echo it back.
            self._raw_chargers[cid] = evc
            result[cid] = ChargerStatus.from_evcharger(evc)
        return result

    async def async_get_vehicles(self) -> dict[str, Vehicle]:
        """GET customers/devices/status → ``{str(deviceGid): Vehicle}`` (best-effort).

        Iterates ``evChargers[]``; calls ``Vehicle.from_device`` (which looks for
        a ``vehicle`` sub-dict and returns None if absent). With the real fixture
        (no car connected) this returns ``{}``.

        UNPINNED/PROVISIONAL: The vehicle block shape and which endpoint actually
        carries it (this STATUS payload vs the ``customers/devices`` payload) has
        NOT been confirmed — no capture was made with a car connected.  Re-capture
        with a connected vehicle and reconcile payload source + field names in
        ``Vehicle.from_device`` before relying on vehicle battery data.
        """
        payload = await self._request("GET", "customers/devices/status")
        vehicles: dict[str, Vehicle] = {}
        for evc in payload.get("evChargers", []):
            vehicle = Vehicle.from_device(evc)
            if vehicle is not None:
                vehicles[str(evc["deviceGid"])] = vehicle
        return vehicles

    async def async_get_energy(
        self,
        charger_gids: list[str],
        *,
        instant: str,
    ) -> dict[str, float]:
        """GET AppAPI?apiMethod=getDeviceListUsages → ``{str(deviceGid): kWh}``.

        Args:
            charger_gids: List of deviceGid strings to query.
            instant: ISO-8601 UTC timestamp with ``Z`` suffix, e.g.
                ``"2026-07-20T18:13:32Z"``. The coordinator passes
                ``datetime.utcnow().strftime(...)`` so tests can inject a
                fixed value and avoid a hidden clock dependency.

        Returns:
            Dict mapping str(deviceGid) to float kWh usage for the 1-minute
            bucket ending at ``instant``. Missing or empty channels are skipped.
        """
        gids_joined = ",".join(charger_gids)
        path = (
            f"AppAPI"
            f"?apiMethod=getDeviceListUsages"
            f"&deviceGids={gids_joined}"
            f"&instant={instant}"
            f"&scale=1MIN"
            f"&energyUnit=KilowattHours"
        )
        payload = await self._request("GET", path)
        result: dict[str, float] = {}
        devices = payload.get("deviceListUsages", {}).get("devices", [])
        for device in devices:
            gid = str(device.get("deviceGid", ""))
            if not gid:
                continue
            channel_usages = device.get("channelUsages", [])
            if not channel_usages:
                continue
            # Prefer "Main" channel or channelNum "1,2,3"; fall back to first.
            chosen = channel_usages[0]
            for ch in channel_usages:
                if ch.get("name") == "Main" or ch.get("channelNum") == "1,2,3":
                    chosen = ch
                    break
            usage = chosen.get("usage")
            if usage is not None:
                result[gid] = float(usage)
        return result

    async def async_set_charger(
        self, charger_id: str, *, enabled: bool, charge_rate_amps: int
    ) -> None:
        """PUT devices/evcharger — enable/disable the charger and set charge rate.

        Emporia requires the FULL charger object on this PUT (it returns HTTP 400
        for a partial body). We echo back the last raw ``evChargers[]`` object
        seen from the status endpoint, mutating only ``chargerOn`` and
        ``chargingRate`` — mirroring PyEmVue's ``charger.as_dictionary()``
        round-trip (deviceGid, loadGid, chargerOn, chargingRate, maxChargingRate,
        breakerPIN). If no raw object is cached yet (no status poll has run), fall
        back to the minimal identifying fields.
        """
        raw = self._raw_chargers.get(charger_id)
        if raw is not None:
            body: dict[str, Any] = {
                "deviceGid": raw.get("deviceGid", int(charger_id)),
                "loadGid": raw.get("loadGid"),
                "chargerOn": enabled,
                "chargingRate": charge_rate_amps,
                "maxChargingRate": raw.get("maxChargingRate"),
            }
            if raw.get("breakerPIN"):
                body["breakerPIN"] = raw["breakerPIN"]
        else:
            body = {
                "deviceGid": int(charger_id),
                "chargerOn": enabled,
                "chargingRate": charge_rate_amps,
            }
        await self._request("PUT", "devices/evcharger", json=body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Authenticated request with bounded retry on transient connection blips.

        Retries only ``EmporiaConnectionError`` (flaky DNS/IPv6 to the cloud) up
        to ``_MAX_REQUEST_ATTEMPTS`` with linear backoff, so a momentary connect
        failure never surfaces as a hard error (e.g. so the first poll after
        setup doesn't trip HA's "Failed setup, will retry" banner or leave an
        ``unavailable`` entry in the log). ``AuthError`` and ``RateLimitError``
        propagate immediately without retry.
        """
        for attempt in range(_MAX_REQUEST_ATTEMPTS):
            try:
                return await self._request_once(method, path, json=json)
            except EmporiaConnectionError:
                if attempt == _MAX_REQUEST_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise EmporiaConnectionError(f"{method} {path} exhausted retries")

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one authenticated request and return the parsed JSON body.

        Error mapping:
        - HTTP 401 → AuthError
        - HTTP 429 → RateLimitError (retry_after from Retry-After header)
        - Other bad status → EmporiaConnectionError (via raise_for_status)
        - ClientResponseError / ClientConnectionError → EmporiaConnectionError
        - ClientError / TimeoutError → EmporiaConnectionError
        - Empty body (content_length == 0) → {}
        """
        await self._auth.async_get_access_token()
        headers = self._auth.auth_headers()
        url = f"{self._base_url}/{path}"
        try:
            async with self._session.request(method, url, headers=headers, json=json) as resp:
                if resp.status == 401:
                    raise AuthError("Emporia API returned 401 Unauthorized")
                if resp.status == 429:
                    raise RateLimitError(
                        "Emporia API returned 429 Too Many Requests",
                        retry_after=_parse_retry_after(resp.headers),
                    )
                resp.raise_for_status()
                if resp.status == 204 or not resp.content_length:
                    return {}
                # content_type=None: don't reject on an unexpected/absent
                # Content-Type header (some Emporia endpoints mislabel JSON).
                return await resp.json(content_type=None)  # type: ignore[no-any-return]
        except (AuthError, RateLimitError):
            raise
        except (aiohttp.ClientResponseError, aiohttp.ClientConnectionError) as err:
            raise EmporiaConnectionError(f"{method} {path} failed: {err}") from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise EmporiaConnectionError(f"{method} {path} transport error: {err}") from err
