"""Diagnostics tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.emporia_ev.diagnostics import async_get_config_entry_diagnostics


async def _setup(hass, mock_client, mock_config_entry):
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
    return mock_config_entry


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant, mock_client, mock_config_entry
) -> None:
    entry = await _setup(hass, mock_client, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    assert diag["entry"]["data"]["refresh_token"] == "**REDACTED**"
    assert "chargers" in diag
    assert "status" in diag
    assert diag["status"]["chg-1"]["charge_rate_amps"] == 32
    # Charger name (can be a street-address-like label) and serial must be redacted.
    assert diag["chargers"]["chg-1"]["name"] == "**REDACTED**"
    assert diag["chargers"]["chg-1"]["serial"] == "**REDACTED**"


async def test_diagnostics_redacts_social_entry_secrets(
    hass: HomeAssistant, mock_client, social_config_entry
) -> None:
    """An OAuth entry's refresh token is a live credential for weeks.

    The title is emitted unredacted, so it must never carry the email either.
    """
    entry = await _setup(hass, mock_client, social_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["refresh_token"] == "**REDACTED**"
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    # Not a secret, and useful when triaging a provider-specific report.
    assert diag["entry"]["data"]["oauth_provider"] == "Google"
    assert diag["entry"]["data"]["auth_method"] == "oauth"
    assert "@" not in diag["entry"]["title"]
