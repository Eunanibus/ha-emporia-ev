"""Config-flow tests for Emporia EV Charger."""

from __future__ import annotations

import base64
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import MY_AUTH_CALLBACK_PATH
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.emporia_ev.client.oauth import AUTHORIZE_URL, TOKEN_URL
from custom_components.emporia_ev.config_flow import EmporiaOAuth2Implementation
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


async def _start_password_flow(hass: HomeAssistant):
    """Start the flow and pick the email-and-password method off the menu."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )


def _fake_id_token(claims: dict) -> str:
    """Build a decodable, unsigned id token.

    Built at runtime rather than committed: scripts/scrub_fixtures.py rewrites
    any three-part ``eyJ...`` string to FAKE_TOKEN, which would destroy a
    committed fixture on the next scrub run.
    """
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


async def _run_oauth_flow(
    hass: HomeAssistant,
    *,
    menu_id: str = "google",
    context: dict | None = None,
    entry_data: dict | None = None,
    token_response: dict | None = None,
    account_id: str = "acct-42",
    email: str | None = "user@example.com",
    authenticate_error: Exception | None = None,
):
    """Drive the hosted-UI sign-in and return (result, authorize_url, token_calls).

    Three moves are required. Resuming the external step returns
    EXTERNAL_STEP_DONE, which parks in cur_step, and the flow manager only
    re-enters by itself for SHOW_PROGRESS_DONE, so ``creation`` needs a second
    async_configure with no input.

    ``_token_request`` is replaced rather than the HTTP layer mocked, so the
    request body this integration builds is asserted directly and no real
    ClientSession (and no pycares DNS thread) is ever created.
    """
    claims = {"email": email} if email else {}
    token = (
        token_response
        if token_response is not None
        else {
            "access_token": "access-xyz",
            "refresh_token": "refresh-from-hosted-ui",
            "id_token": _fake_id_token(claims),
            "expires_in": 3600,
            "token_type": "Bearer",
        }
    )
    token_calls: list[dict] = []

    async def fake_token_request(self, data):
        token_calls.append(data)
        return token

    client, auth = _patched_client(account_id=account_id)
    if authenticate_error is not None:
        client.authenticate.side_effect = authenticate_error
    with (
        patch(_PATCH_SESSION, return_value=MagicMock()),
        patch("custom_components.emporia_ev.config_flow.EmporiaAuth", return_value=auth),
        patch("custom_components.emporia_ev.config_flow.EmporiaClient", return_value=client),
        patch.object(EmporiaOAuth2Implementation, "_token_request", fake_token_request),
        patch(
            "custom_components.emporia_ev.async_setup_entry",
            new=AsyncMock(return_value=True),
            create=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context=context or {"source": SOURCE_USER}, data=entry_data
        )
        if result["type"] is FlowResultType.MENU:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": menu_id}
            )
        elif result["step_id"] == "reauth_social":
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

        assert result["type"] is FlowResultType.EXTERNAL_STEP
        authorize_url = result["url"]

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"state": {"redirect_uri": MY_AUTH_CALLBACK_PATH}, "code": "auth-code-1"},
        )
        assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        # Drain inside the patch: entry setup is scheduled as a task, and if it
        # lands after async_setup_entry is unpatched the real one runs.
        await hass.async_block_till_done()
    return result, authorize_url, token_calls


async def test_menu_offers_all_three_sign_in_methods(hass: HomeAssistant) -> None:
    """Google and Apple users cannot use SRP at all, so both must be offered."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert list(result["menu_options"]) == ["password", "apple", "google"]


async def test_oauth_flow_creates_entry(hass: HomeAssistant) -> None:
    """The whole point of the feature: a federated user gets a working entry."""
    result, _url, _calls = await _run_oauth_flow(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "acct-42"
    assert result["title"] == "Emporia (acct-42)"
    data = result["data"]
    assert data["auth_method"] == "oauth"
    assert data["oauth_provider"] == "Google"
    assert data["refresh_token"] == "refresh-from-hosted-ui"
    assert data["account_id"] == "acct-42"
    assert data["username"] == "user@example.com"
    # No password key at all: __init__ must not KeyError on setup.
    assert "password" not in data
    # The helper's own envelope keys are not persisted; EmporiaAuth owns tokens.
    assert "token" not in data
    assert "auth_implementation" not in data


async def test_oauth_flow_without_email_claim_still_creates_entry(hass: HomeAssistant) -> None:
    """Apple may omit the email claim, which must not produce 'Emporia (None)'."""
    result, _url, _calls = await _run_oauth_flow(hass, menu_id="apple", email=None)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Emporia (acct-42)"
    assert result["data"]["oauth_provider"] == "SignInWithApple"
    assert "username" not in result["data"]


async def test_oauth_authorize_url_carries_pkce_and_provider(hass: HomeAssistant) -> None:
    """Cognito matches redirect_uri byte for byte and needs the provider hint."""
    _result, url, _calls = await _run_oauth_flow(hass, menu_id="apple")
    query = parse_qs(urlparse(url).query)
    assert url.startswith(AUTHORIZE_URL)
    assert query["identity_provider"] == ["SignInWithApple"]
    assert query["redirect_uri"] == [MY_AUTH_CALLBACK_PATH]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    assert query["scope"] == ["openid email"]
    assert query["response_type"] == ["code"]
    assert "client_secret" not in query


async def test_oauth_token_request_sends_matching_verifier(hass: HomeAssistant) -> None:
    """PKCE only protects anything if the verifier matches the sent challenge."""
    _result, url, token_calls = await _run_oauth_flow(hass)
    challenge = parse_qs(urlparse(url).query)["code_challenge"][0]
    body = token_calls[-1]
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "auth-code-1"
    assert body["redirect_uri"] == MY_AUTH_CALLBACK_PATH
    # The verifier must hash to the challenge that was sent to authorize.
    digest = hashlib.sha256(body["code_verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == challenge


async def test_oauth_reauth_updates_refresh_token_without_aborting(hass: HomeAssistant) -> None:
    """Reauth must not hit already_configured, which would strand these users."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "auth_method": "oauth",
            "oauth_provider": "Google",
            "account_id": "acct-42",
            "refresh_token": "expired-token",
        },
    )
    entry.add_to_hass(hass)
    result, _url, _calls = await _run_oauth_flow(
        hass,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        entry_data=entry.data,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["refresh_token"] == "refresh-from-hosted-ui"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_oauth_reauth_wrong_account_aborts(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "auth_method": "oauth",
            "oauth_provider": "Google",
            "account_id": "acct-42",
            "refresh_token": "expired-token",
        },
    )
    entry.add_to_hass(hass)
    result, _url, _calls = await _run_oauth_flow(
        hass,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        entry_data=entry.data,
        account_id="acct-99",
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["refresh_token"] == "expired-token"


async def test_oauth_reauth_shows_confirm_form_before_the_browser(hass: HomeAssistant) -> None:
    """ConfigEntryAuthFailed starts this flow with nobody watching.

    Handing straight to the browser would park an external step and then throw
    the user into a sign-in page with no explanation of what it is for.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "auth_method": "oauth",
            "oauth_provider": "SignInWithApple",
            "account_id": "acct-42",
            "refresh_token": "expired-token",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_social"
    assert result["description_placeholders"]["provider"] == "Apple"


async def test_oauth_reauth_survives_unknown_stored_provider(hass: HomeAssistant) -> None:
    """An unrecognised oauth_provider must not KeyError mid-reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="acct-42",
        data={
            "auth_method": "oauth",
            "oauth_provider": "SignInWithSomethingNew",
            "account_id": "acct-42",
            "refresh_token": "expired-token",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_social"


async def _reject_oauth(hass: HomeAssistant, error: str):
    """Start the Google flow and come back carrying an error instead of a code."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "google"}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"state": {"redirect_uri": MY_AUTH_CALLBACK_PATH}, "error": error},
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def test_oauth_cancelled_at_provider_uses_our_own_message(hass: HomeAssistant) -> None:
    """Declining must not surface core's "Account linking rejected: access_denied".

    user_rejected_authorize is in core's _SHARED_ABORT_REASONS, so newer Home
    Assistant translates it against the homeassistant domain and this
    integration's wording is discarded. A private reason is the only way to say
    something useful, so the reason must never be that shared one.
    """
    result = await _reject_oauth(hass, "access_denied")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sign_in_cancelled"
    assert result["reason"] != "user_rejected_authorize"


async def test_oauth_provider_error_is_distinguished_from_cancelling(
    hass: HomeAssistant,
) -> None:
    """A real provider failure is not the same as the user changing their mind."""
    result = await _reject_oauth(hass, "server_error")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sign_in_rejected"
    assert result["description_placeholders"]["error"] == "server_error"


async def test_oauth_rejection_without_error_code_still_aborts_cleanly(
    hass: HomeAssistant,
) -> None:
    """An empty error must not render an empty placeholder."""
    result = await _reject_oauth(hass, "")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "sign_in_rejected"
    assert result["description_placeholders"]["error"] == "unknown"


async def test_oauth_flow_without_refresh_token_aborts(hass: HomeAssistant) -> None:
    """No refresh token means nothing to persist, so refuse rather than half-create."""
    result, _url, _calls = await _run_oauth_flow(
        hass,
        token_response={
            "access_token": "access-xyz",
            "id_token": _fake_id_token({"email": "user@example.com"}),
            "expires_in": 3600,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_refresh_token"


async def test_oauth_flow_connection_error_aborts(hass: HomeAssistant) -> None:
    """There is no form to return to after the external step, so this aborts."""
    from custom_components.emporia_ev.client import EmporiaConnectionError

    result, _url, _calls = await _run_oauth_flow(
        hass, authenticate_error=EmporiaConnectionError("connection refused")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_oauth_flow_rejected_token_aborts_without_credential_wording(
    hass: HomeAssistant,
) -> None:
    """AuthError subclasses EmporiaError, so ordering matters.

    It must not surface invalid_auth, whose string reads "Invalid email or
    password" and would be nonsense after a Google sign-in. It also must not
    reuse a reason from core's _SHARED_ABORT_REASONS set, which newer Home
    Assistant translates against the homeassistant domain, discarding our copy.
    """
    from custom_components.emporia_ev.client import AuthError

    result, _url, _calls = await _run_oauth_flow(
        hass, authenticate_error=AuthError("token rejected")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_lookup_failed"


async def test_oauth_flow_missing_account_id_aborts(hass: HomeAssistant) -> None:
    """Guard from issue #1: never create an entry titled "Emporia (None)"."""
    result, _url, _calls = await _run_oauth_flow(hass, account_id=None)  # type: ignore[arg-type]
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


def test_oauth_implementation_is_a_public_client(hass: HomeAssistant) -> None:
    """Cognito rejects a secret on this client, and the helper omits it when None.

    Asserted on the implementation rather than on a request body, because the
    helper adds the field itself: 2025.1.4 omits it when it ``is not None`` and
    2026.9 when it is falsy, so None is the one value correct on both. It must
    never become "", which the older check would post as an empty secret.
    """
    impl = EmporiaOAuth2Implementation(hass, "google")
    assert impl.client_secret is None
    assert impl.token_url == TOKEN_URL
    assert impl.redirect_uri == MY_AUTH_CALLBACK_PATH
    assert impl.identity_provider == "Google"


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
        result = await _start_password_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "password"
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
        result = await _start_password_flow(hass)
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
        result = await _start_password_flow(hass)
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
        result = await _start_password_flow(hass)
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
        result = await _start_password_flow(hass)
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
        result = await _start_password_flow(hass)
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
