"""DataUpdateCoordinator for Emporia EV Charger.

Fetches charger status + energy each cycle, merges them, and applies an
adaptive poll interval with hysteresis so HA polls fast while charging and
relaxes back to the idle cadence after RELAX_AFTER_N non-charging polls.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    AuthError,
    Charger,
    ChargerStatus,
    EmporiaClient,
    EmporiaConnectionError,
    EmporiaError,
    RateLimitError,
    Vehicle,
)
from .const import (
    CONF_ADAPTIVE,
    CONF_CHARGING_INTERVAL,
    CONF_IDLE_INTERVAL,
    DEFAULT_ADAPTIVE,
    DEFAULT_CHARGING_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DOMAIN,
    MAX_TRANSIENT_FAILURES,
    RELAX_AFTER_N,
)

_LOGGER = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with Z suffix.

    Defined at module level so tests can monkeypatch it:
        monkeypatch.setattr("custom_components.emporia_ev.coordinator._utcnow_iso",
                            lambda: "2026-07-20T18:13:32Z")
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class EmporiaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, ChargerStatus]]):
    """One coordinator per config entry; one batched status+energy call per cycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EmporiaClient,
        entry: ConfigEntry,
    ) -> None:
        self.client = client
        self.entry = entry
        self.chargers: dict[str, Charger] = {}
        self.vehicles: dict[str, Vehicle] = {}
        self._non_charging_polls: int = 0
        self._consecutive_failures: int = 0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._idle_interval),
        )

    # ------------------------------------------------------------------
    # Option helpers
    # ------------------------------------------------------------------

    @property
    def _idle_interval(self) -> int:
        return int(self.entry.options.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL))

    @property
    def _charging_interval(self) -> int:
        return int(self.entry.options.get(CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL))

    @property
    def _adaptive(self) -> bool:
        return bool(self.entry.options.get(CONF_ADAPTIVE, DEFAULT_ADAPTIVE))

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, ChargerStatus]:
        """Fetch status + energy, merge them, apply adaptive interval."""
        try:
            if not self.chargers:
                await self._async_refresh_chargers()

            status = await self.client.async_get_charger_status()

            # If a new charger appeared, refresh the device list.
            if any(cid not in self.chargers for cid in status):
                await self._async_refresh_chargers()

            # Fetch energy for the 1-min bucket and merge into status objects.
            energy: dict[str, float] = await self.client.async_get_energy(
                list(status.keys()),
                instant=_utcnow_iso(),
            )
            merged: dict[str, ChargerStatus] = {}
            for cid, cs in status.items():
                kwh = energy.get(cid, 0.0)
                # Power = average watts over the 1-minute window.
                # kWh → kW: multiply by 60 (min⁻¹ → h⁻¹); kW → W: multiply by 1000.
                # Round to a whole watt so tiny float differences between polls
                # don't register as state changes (which would spam the logbook).
                merged[cid] = dataclasses.replace(
                    cs,
                    energy_kwh=round(kwh, 4),
                    power_w=round(kwh * 60 * 1000),
                )

        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RateLimitError as err:
            retry_after = err.retry_after or self._idle_interval
            self.update_interval = timedelta(seconds=max(retry_after, self._idle_interval))
            _LOGGER.debug("Rate limited; backing off %s s", retry_after)
            if self.data is not None:
                return self.data
            raise UpdateFailed("Rate limited before first data") from err
        except (EmporiaConnectionError, EmporiaError) as err:
            # Tolerate transient connection blips (e.g. flaky container DNS):
            # keep the last-known data so entities stay available rather than
            # flapping to "unavailable" and back — which would spam the activity
            # log with state changes that aren't real. Only surface UpdateFailed
            # once failures are sustained (or we have no prior data to fall back on).
            self._consecutive_failures += 1
            if self.data is not None and self._consecutive_failures < MAX_TRANSIENT_FAILURES:
                _LOGGER.debug(
                    "Transient fetch failure %s/%s; keeping last-known data: %s",
                    self._consecutive_failures,
                    MAX_TRANSIENT_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        # Successful cycle — reset the transient-failure counter.
        self._consecutive_failures = 0
        self._apply_adaptive_interval(merged)
        return merged

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _async_refresh_chargers(self) -> None:
        """Refresh device list and vehicles from the API."""
        chargers = await self.client.async_get_chargers()
        self.chargers = {c.id: c for c in chargers}
        self.vehicles = await self.client.async_get_vehicles()

    def _apply_adaptive_interval(self, status: dict[str, ChargerStatus]) -> None:
        """Adjust the poll interval based on charging state (with hysteresis).

        Rules:
        - Adaptive disabled: pin to idle interval.
        - Any charger is charging: switch to fast (charging) interval immediately
          and reset the non-charging counter.
        - No charger charging: increment counter; relax to idle only after
          RELAX_AFTER_N consecutive non-charging polls (hysteresis).
        """
        if not self._adaptive:
            self.update_interval = timedelta(seconds=self._idle_interval)
            return

        any_charging = any(s.charging_state == "charging" for s in status.values())
        if any_charging:
            self._non_charging_polls = 0
            self.update_interval = timedelta(seconds=self._charging_interval)
            return

        self._non_charging_polls += 1
        if self._non_charging_polls >= RELAX_AFTER_N:
            self.update_interval = timedelta(seconds=self._idle_interval)
        # else: hold at charging interval (hysteresis — do not change update_interval)
