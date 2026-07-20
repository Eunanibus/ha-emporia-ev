"""The Emporia EV Charger integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import EmporiaAuth, EmporiaClient
from .const import PLATFORMS
from .coordinator import EmporiaDataUpdateCoordinator

type EmporiaConfigEntry = ConfigEntry[EmporiaDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: EmporiaConfigEntry) -> bool:
    """Set up Emporia EV Charger from a config entry."""
    session = async_get_clientsession(hass)
    auth = EmporiaAuth(
        session,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        refresh_token=entry.data.get("refresh_token"),
    )
    client = EmporiaClient(session, auth)

    coordinator = EmporiaDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EmporiaConfigEntry) -> bool:
    """Unload a config entry. The injected HA session is NOT closed here."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: EmporiaConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: EmporiaConfigEntry) -> bool:
    """Migrate old config entries. VERSION is 1; refuse unknown newer versions."""
    return not entry.version > 1
