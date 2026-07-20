"""Setup / unload / migrate tests for the Emporia EV Charger integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest

from custom_components.emporia_ev.coordinator import EmporiaDataUpdateCoordinator


@pytest.mark.asyncio
async def test_setup_and_unload_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test that a config entry can be set up and then unloaded cleanly."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", []),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert isinstance(mock_config_entry.runtime_data, EmporiaDataUpdateCoordinator)

    with patch("custom_components.emporia_ev.PLATFORMS", []):
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_get_charger_status.assert_awaited()


@pytest.mark.asyncio
async def test_options_update_triggers_reload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MagicMock
) -> None:
    """Test that updating options triggers a reload that keeps the entry loaded."""
    mock_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", []),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth", return_value=MagicMock()),
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        hass.config_entries.async_update_entry(mock_config_entry, options={"idle_interval": 45})
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_migrate_entry_future_version(
    hass: HomeAssistant, mock_config_entry: MagicMock
) -> None:
    """Test that a config entry with a version > 1 is rejected by migration."""
    from custom_components.emporia_ev import async_migrate_entry

    mock_config_entry.add_to_hass(hass)
    object.__setattr__(mock_config_entry, "version", 99)
    assert await async_migrate_entry(hass, mock_config_entry) is False
