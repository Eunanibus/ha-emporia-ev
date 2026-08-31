"""Config-flow tests for Emporia EV Charger."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emporia_ev.const import (
    CONF_ADAPTIVE,
    CONF_CHARGING_INTERVAL,
    CONF_DEFAULT_AMPS,
    CONF_IDLE_INTERVAL,
    DOMAIN,
)

# Patch target for the HA aiohttp session helper.
# Prevents config-flow tests from creating a real aiohttp.ClientSession
# (which spawns a pycares DNS background thread and fails the HA test-harness
# lingering-thread assertion at teardown).
_PATCH_SESSION = "custom_components.emporia_ev.config_flow.async_get_clientsession"


def _patched_client(account_id: str = "acct-42"):
    """Patch EmporiaClient + EmporiaAuth used inside config_flow."""
    client = MagicMock()
    client.authenticate = AsyncMock(return_value=None)
    client.account_id = account_id
    auth = MagicMock()
    auth.refresh_token = "refresh-abc"
    return client, auth


async def test_user_flow_success(hass: HomeAssistant) -> None:
    client, auth = _patched_client()
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
        patch(
            "custom_components.emporia_ev.async_setup_entry",
            new=AsyncMock(return_value=True),
            create=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "hunter2"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "acct-42"
    assert result["data"]["account_id"] == "acct-42"
    assert result["data"]["refresh_token"] == "refresh-abc"


async def test_user_flow_bad_credentials(hass: HomeAssistant) -> None:
    from custom_components.emporia_ev.client import AuthError

    client, auth = _patched_client()
    client.authenticate.side_effect = AuthError("bad creds")
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "wrong"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    from custom_components.emporia_ev.client import EmporiaConnectionError

    client, auth = _patched_client()
    client.authenticate.side_effect = EmporiaConnectionError("connection refused")
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "anything"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error_shows_form_not_broken_entry(hass: HomeAssistant) -> None:
    """A non-auth, non-connection EmporiaError must show an error, not create an entry.

    Regression for GitHub issue #1: the flow only caught AuthError and
    EmporiaConnectionError. Any other failure escaped as an unhandled exception
    or (when account_id was silently None) created a useless entry titled
    "Emporia (None)" with no entities. The user must get actionable feedback.
    """
    from custom_components.emporia_ev.client import EmporiaError

    client, auth = _patched_client()
    client.authenticate.side_effect = EmporiaError("no customerGid in payload")
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "anything"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_rejects_missing_account_id(hass: HomeAssistant) -> None:
    """The flow must never create an entry when account_id is None.

    Direct guard against the reported "Created configuration for Emporia (None)":
    even if authenticate() succeeds, a None account_id cannot serve as a stable
    unique_id, so the flow must show an error instead of creating the entry.
    """
    client, auth = _patched_client(account_id=None)  # type: ignore[arg-type]
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "hunter2"}
        )
    assert result["type"] is FlowResultType.FORM, "must not create an 'Emporia (None)' entry"
    assert result["errors"] == {"base": "unknown"}


async def test_duplicate_account_aborts(hass: HomeAssistant) -> None:
    existing = MockConfigEntry(domain=DOMAIN, unique_id="acct-42")
    existing.add_to_hass(hass)
    client, auth = _patched_client()
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "user@example.com", "password": "hunter2"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_entry_in_place(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "username": "user@example.com",
            "password": "old",
            "account_id": "acct-42",
            "refresh_token": "old-token",
        },
    )
    entry.add_to_hass(hass)
    client, auth = _patched_client()
    auth.refresh_token = "new-token"
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
        patch(
            "custom_components.emporia_ev.async_setup_entry",
            new=AsyncMock(return_value=True),
            create=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
        )
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "newpass"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data["password"] == "newpass"
    assert entry.data["refresh_token"] == "new-token"


async def test_reauth_wrong_account_aborts(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={"username": "user@example.com", "password": "old", "account_id": "acct-42"},
    )
    entry.add_to_hass(hass)
    client, auth = _patched_client(account_id="acct-99")
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"password": "whatever"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={"username": "u", "password": "p", "account_id": "acct-42"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.emporia_ev.async_setup_entry",
        new=AsyncMock(return_value=True),
        create=True,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_IDLE_INTERVAL: 60,
                CONF_CHARGING_INTERVAL: 10,
                CONF_ADAPTIVE: False,
                CONF_DEFAULT_AMPS: 40,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_IDLE_INTERVAL] == 60
    assert entry.options[CONF_ADAPTIVE] is False
    assert entry.options[CONF_DEFAULT_AMPS] == 40


async def test_reauth_confirm_passes_username_placeholder(hass: HomeAssistant) -> None:
    """strings.json interpolates {username}; without description_placeholders the user
    sees the literal token rather than the actual email address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "username": "user@example.com",
            "password": "hunter2",
            "account_id": "acct-42",
            "refresh_token": "old-token",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    # HA injects a 'name' key into description_placeholders on reauth flows
    # (config_entries.py), so check the username key specifically rather than
    # asserting dict equality.
    assert result["description_placeholders"]["username"] == "user@example.com"
