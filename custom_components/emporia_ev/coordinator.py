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
        self._warned_no_chargers: bool = False
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
                # Still missing after a refresh? The status endpoint knows about a
                # charger the device list doesn't return. Synthesize its identity
                # from the status payload so it gets entities — otherwise its
                # telemetry shows up in diagnostics/debug logs but is unreachable
                # from the UI, with no entity to read it (GitHub issue #1).
                self._add_status_only_chargers(status)

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
        """Refresh device list and vehicles from the API.

        Logs a warning when the account exposes no EV chargers. Without it, an
        empty device list looks identical to a healthy setup — the entry loads,
        no error is raised, and the user simply sees no entities with nothing in
        the log to explain why (GitHub issue #1).
        """
        chargers = await self.client.async_get_chargers()
        self.chargers = {c.id: c for c in chargers}
        if not self.chargers:
            # Warn once, not on every cycle: _async_update_data re-runs this
            # refresh on each poll while the list is empty, which would spam
            # the log roughly every 30 s indefinitely.
            if not self._warned_no_chargers:
                self._warned_no_chargers = True
                _LOGGER.warning(
                    "Emporia account %s reports no EV chargers; no entities will be created. "
                    "If you do own an Emporia EV charger, please report this with debug logs "
                    "enabled for %s",
                    self.client.account_id,
                    DOMAIN,
                )
        else:
            # Reset so a later disappearance is reported again.
            self._warned_no_chargers = False
        self.vehicles = await self.client.async_get_vehicles()

    def _add_status_only_chargers(self, status: dict[str, ChargerStatus]) -> None:
        """Create identities for chargers seen in status but not in the device list.

        Entity creation is gated on ``self.chargers`` (see ``dynamic.py``), so a
        charger missing from ``customers/devices`` would otherwise have live
        telemetry in ``self.data`` and no entities to expose it. Identity is
        built from the raw status object the client cached for this charger,
        falling back to the device gid for name/serial.
        """
        for cid in status:
            if cid in self.chargers:
                continue
            raw = self.client.raw_charger(cid) or {"deviceGid": cid}
            self.chargers[cid] = Charger.from_device(raw)
            _LOGGER.warning(
                "Charger %s is reported by the status endpoint but missing from the "
                "device list; creating entities from status data only. Device name and "
                "model may be incomplete.",
                cid,
            )

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
