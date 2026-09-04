"""Setup / unload / migrate tests for the Emporia EV Charger integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
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


@pytest.mark.asyncio
async def test_setup_social_entry_without_password(
    hass: HomeAssistant, mock_client: MagicMock, social_config_entry: MagicMock
) -> None:
    """A Google or Apple entry stores no password and must still load.

    This is the line that actually breaks for the target users: reading
    entry.data[CONF_PASSWORD] raises KeyError and the entry never loads.
    """
    social_config_entry.add_to_hass(hass)
    with (
        patch("custom_components.emporia_ev.PLATFORMS", []),
        patch("custom_components.emporia_ev.EmporiaClient", return_value=mock_client),
        patch("custom_components.emporia_ev.EmporiaAuth") as auth_cls,
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(social_config_entry.entry_id)
        await hass.async_block_till_done()

    assert social_config_entry.state is ConfigEntryState.LOADED
    # EmporiaAuth is constructed with the refresh token and no password.
    kwargs = auth_cls.call_args.kwargs
    assert kwargs["refresh_token"] == "refresh-from-hosted-ui"
    assert kwargs["password"] is None


@pytest.mark.asyncio
async def test_remove_social_entry_revokes_refresh_token(
    hass: HomeAssistant, social_config_entry: MagicMock
) -> None:
    """Deleting the integration must not leave a credential valid for weeks."""
    from custom_components.emporia_ev import async_remove_entry

    with (
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch("custom_components.emporia_ev.async_revoke", new_callable=AsyncMock) as revoke,
    ):
        revoke.return_value = None
        await async_remove_entry(hass, social_config_entry)

    assert revoke.await_args.args[1] == "refresh-from-hosted-ui"


@pytest.mark.asyncio
async def test_remove_password_entry_does_not_revoke(
    hass: HomeAssistant, mock_config_entry: MagicMock
) -> None:
    """A password entry has no hosted-UI token, so there is nothing to revoke."""
    from custom_components.emporia_ev import async_remove_entry

    with (
        patch(
            "custom_components.emporia_ev.async_get_clientsession",
            return_value=MagicMock(),
        ),
        patch("custom_components.emporia_ev.async_revoke", new_callable=AsyncMock) as revoke,
    ):
        await async_remove_entry(hass, mock_config_entry)

    revoke.assert_not_called()


@pytest.mark.asyncio
async def test_remove_social_entry_survives_revoke_failure(
    hass: HomeAssistant, social_config_entry: MagicMock
) -> None:
    """Removal must complete even when Emporia rejects the revoke.

    Home Assistant logs anything raised from async_remove_entry with
    _LOGGER.exception, so "best effort" has to be enforced at this call site.
    """
    from custom_components.emporia_ev import async_remove_entry
    from custom_components.emporia_ev.client import EmporiaError

    for failure in (EmporiaError("rejected"), aiohttp.ClientError("boom"), TimeoutError()):
        with (
            patch(
                "custom_components.emporia_ev.async_get_clientsession",
                return_value=MagicMock(),
            ),
            patch(
                "custom_components.emporia_ev.async_revoke",
                new_callable=AsyncMock,
                side_effect=failure,
            ),
        ):
            await async_remove_entry(hass, social_config_entry)
