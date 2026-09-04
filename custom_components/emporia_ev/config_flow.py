"""Config, reauth, and options flows for Emporia EV Charger."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, cast

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import (
    AUTHORIZE_URL,
    PROVIDERS,
    SCOPE,
    TOKEN_URL,
    AuthError,
    EmporiaAuth,
    EmporiaClient,
    EmporiaConnectionError,
    EmporiaError,
    email_from_id_token,
    generate_pkce,
    menu_id_for_provider,
)
from .client.auth import CLIENT_ID
from .const import (
    AUTH_METHOD_OAUTH,
    CONF_ADAPTIVE,
    CONF_AUTH_METHOD,
    CONF_CHARGING_INTERVAL,
    CONF_DEFAULT_AMPS,
    CONF_IDLE_INTERVAL,
    CONF_OAUTH_PROVIDER,
    DEFAULT_ADAPTIVE,
    DEFAULT_AMPS,
    DEFAULT_CHARGING_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MAX_AMPS,
    DEFAULT_MIN_AMPS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


async def async_validate_login(
    hass: HomeAssistant, username: str, password: str
) -> tuple[str, str | None]:
    """Validate credentials; return (account_id, refresh_token).

    Raises:
        AuthError: Credentials were rejected.
        EmporiaConnectionError: The Emporia cloud was unreachable.
        EmporiaError: Login worked but no account id could be resolved. Never
            return a None account_id: it is used as the config-entry unique_id,
            and letting it through created an entry titled "Emporia (None)" with
            no entities (GitHub issue #1).
    """
    session = async_get_clientsession(hass)
    auth = EmporiaAuth(session, username=username, password=password)
    client = EmporiaClient(session, auth)
    await client.authenticate()
    if not client.account_id:
        raise EmporiaError("Emporia API returned no account id (customerGid)")
    return client.account_id, auth.refresh_token


async def async_validate_refresh_token(hass: HomeAssistant, refresh_token: str) -> str:
    """Return the account id for a hosted-UI refresh token.

    The OAuth sibling of ``async_validate_login``, which cannot be reused
    because a federated user has no password to authenticate with.

    This spends one REFRESH_TOKEN_AUTH round trip rather than reusing the
    ``id_token`` from the code exchange. Seeding tokens into ``EmporiaAuth``
    would add a method to an object that runs on every poll cycle, and the cost
    here is one request, once, at setup.

    Raises:
        AuthError: The refresh token was rejected.
        EmporiaConnectionError: The Emporia cloud was unreachable.
        EmporiaError: No account id could be resolved (see issue #1).
    """
    session = async_get_clientsession(hass)
    auth = EmporiaAuth(session, refresh_token=refresh_token)
    client = EmporiaClient(session, auth)
    await client.authenticate()
    if not client.account_id:
        raise EmporiaError("Emporia API returned no account id (customerGid)")
    return client.account_id


class EmporiaOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """Emporia's hosted UI, driven through Home Assistant's OAuth helper.

    One instance belongs to one config flow, because it carries that flow's
    PKCE verifier. It is deliberately never handed to
    ``async_register_implementation``, which would put a per-flow secret in
    ``hass.data`` where a later flow could pick it up.
    """

    def __init__(self, hass: HomeAssistant, menu_id: str) -> None:
        """Build an implementation for one provider and one flow."""
        identity_provider, display_name = PROVIDERS[menu_id]
        super().__init__(
            hass,
            DOMAIN,
            CLIENT_ID,
            # Emporia's app client is public and holds no secret. The helper
            # omits the field entirely when it is None, which is what Cognito
            # wants. The base class annotates this as str, hence the cast; it
            # must not be "corrected" to "", which older releases would post as
            # an empty secret.
            cast("str", None),
            AUTHORIZE_URL,
            TOKEN_URL,
        )
        self.menu_id = menu_id
        self.identity_provider = identity_provider
        self.display_name = display_name
        self._pkce = generate_pkce()

    @property
    def name(self) -> str:
        """Name of the implementation."""
        return f"Emporia ({self.display_name})"

    @property
    def redirect_uri(self) -> str:
        """Return the one redirect URI Emporia accepts for Home Assistant.

        Emporia registers this exact string, so the base class behaviour of
        deriving a URL from the current request is wrong here twice over: the
        derived URL is not registered, and during a reauth flow started in the
        background by ConfigEntryAuthFailed there is no request in context at
        all, which the base class answers with a RuntimeError.
        """
        return config_entry_oauth2_flow.MY_AUTH_CALLBACK_PATH

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra query data for the authorize URL."""
        return {
            "scope": SCOPE,
            "identity_provider": self.identity_provider,
            "code_challenge": self._pkce.challenge,
            "code_challenge_method": "S256",
        }

    @property
    def extra_token_resolve_data(self) -> dict[str, Any]:
        """Extra body fields for the code exchange.

        Newer Home Assistant reads this hook when building the token request.
        The supported floor has no such hook, so ``async_resolve_external_data``
        is overridden as well and merges this in. Defining both keeps the
        verifier attached whichever path core takes.
        """
        return {"code_verifier": self._pkce.verifier}

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        """Exchange the authorization code, adding the PKCE verifier.

        ``redirect_uri`` is read back out of the signed state, so it matches the
        value used at authorize time.
        """
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": external_data["state"]["redirect_uri"],
                **self.extra_token_resolve_data,
            }
        )


class EmporiaConfigFlow(config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Handle the Emporia EV Charger config flow."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._reauth_entry: ConfigEntry | None = None

    @property
    def logger(self) -> logging.Logger:
        """Return the logger the OAuth helper writes through."""
        return _LOGGER

    def _async_update_and_abort(self, entry: ConfigEntry, data: dict[str, Any]) -> ConfigFlowResult:
        """Persist re-authenticated data and finish the flow.

        Not ``async_update_reload_and_abort``: that reloads the entry itself and
        warns when the entry has an update listener, which this integration
        registers in ``async_setup_entry``. Updating the entry lets the existing
        listener perform exactly one reload.
        """
        self.hass.config_entries.async_update_entry(entry, data=data)
        return self.async_abort(reason="reauth_successful")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the sign-in methods.

        Home Assistant renders either a form or a menu per step and has no
        icon support, so credential fields and branded provider buttons cannot
        share one screen. ``password`` is listed first because it is the common
        case.

        ``user_input`` is ignored on purpose: ``async_show_menu`` does not mark
        ``next_step_id`` required, so an empty submit validates and the flow
        manager dispatches straight back here.
        """
        return self.async_show_menu(step_id="user", menu_options=["password", "apple", "google"])

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with an Emporia email and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                account_id, refresh_token = await async_validate_login(
                    self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except EmporiaConnectionError:
                errors["base"] = "cannot_connect"
            # Must stay AFTER the two branches above: both subclass EmporiaError.
            except EmporiaError:
                _LOGGER.exception("Unexpected Emporia error during setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Emporia ({account_id})",
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "account_id": account_id,
                        "refresh_token": refresh_token,
                    },
                )
        return self.async_show_form(step_id="password", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_apple(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign in with Apple."""
        return await self._async_start_oauth("apple")

    async def async_step_google(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign in with Google."""
        return await self._async_start_oauth("google")

    async def _async_start_oauth(self, menu_id: str) -> ConfigFlowResult:
        """Hand the browser to Emporia's hosted UI for one provider."""
        self.flow_impl = EmporiaOAuth2Implementation(self.hass, menu_id)
        return await self.async_step_auth()

    async def async_step_authorize_rejected(self, data: None = None) -> ConfigFlowResult:
        """Explain a refused sign-in in this integration's own words.

        The base class aborts with `user_rejected_authorize`, which newer Home
        Assistant owns and renders as "Account linking rejected: access_denied".
        That names an internal error code and says nothing about what to do, and
        because the reason is in core's shared set the wording here cannot
        replace it. Private reasons can carry it, so declining is separated from
        a genuine provider error.
        """
        error = ""
        if isinstance(self.external_data, dict):
            error = str(self.external_data.get("error", ""))
        if error == "access_denied":
            return self.async_abort(reason="sign_in_cancelled")
        return self.async_abort(
            reason="sign_in_rejected",
            description_placeholders={"error": error or "unknown"},
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create or update an entry from a completed hosted-UI sign-in.

        ``data`` is the helper's ``{"auth_implementation": ..., "token": ...}``
        envelope, not the token itself. Only the refresh token is persisted;
        ``EmporiaAuth`` derives everything else from it.
        """
        token: dict[str, Any] = data["token"]
        refresh_token = token.get("refresh_token")
        # These reasons are deliberately integration-specific. Newer Home
        # Assistant keeps a _SHARED_ABORT_REASONS set (oauth_error,
        # oauth_unauthorized and friends) and forces those to translate against
        # the homeassistant domain, so reusing one here would silently replace
        # our wording with core's generic OAuth copy and misdescribe the cause.
        if not refresh_token:
            _LOGGER.error("Emporia returned no refresh token for the hosted UI sign-in")
            return self.async_abort(reason="no_refresh_token")

        try:
            account_id = await async_validate_refresh_token(self.hass, refresh_token)
        except AuthError:
            # Not invalid_auth: no email or password was involved here.
            return self.async_abort(reason="account_lookup_failed")
        except EmporiaConnectionError:
            return self.async_abort(reason="cannot_connect")
        # Must stay AFTER the two branches above: both subclass EmporiaError.
        except EmporiaError:
            _LOGGER.exception("Unexpected Emporia error during hosted UI sign-in")
            return self.async_abort(reason="unknown")

        if self.source == SOURCE_REAUTH:
            entry = self._reauth_entry
            assert entry is not None
            if account_id != entry.unique_id:
                return self.async_abort(reason="wrong_account")
            return self._async_update_and_abort(
                entry, {**entry.data, "refresh_token": refresh_token}
            )

        await self.async_set_unique_id(account_id)
        self._abort_if_unique_id_configured()

        impl = cast(EmporiaOAuth2Implementation, self.flow_impl)
        entry_data: dict[str, Any] = {
            CONF_AUTH_METHOD: AUTH_METHOD_OAUTH,
            "account_id": account_id,
            "refresh_token": refresh_token,
            CONF_OAUTH_PROVIDER: impl.identity_provider,
        }
        # Present for Google, not guaranteed for Apple: it depends on the pool's
        # IdP attribute mapping. The entry title never carries it, because
        # diagnostics emit the title unredacted.
        if email := email_from_id_token(token.get("id_token", "")):
            entry_data[CONF_USERNAME] = email
        return self.async_create_entry(title=f"Emporia ({account_id})", data=entry_data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Route re-authentication by how the entry signs in."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry_data.get(CONF_AUTH_METHOD) == AUTH_METHOD_OAUTH:
            return await self.async_step_reauth_social()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_social(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then repeat the browser sign-in for an OAuth entry.

        The empty form is what makes this usable. ConfigEntryAuthFailed starts
        the reauth flow in the background, so this step runs at the moment the
        refresh token expires, with nobody watching. Going straight to the
        browser hand-off there would park the flow in an external step and then
        throw the user into a browser with no explanation of what it is for.
        """
        entry = self._reauth_entry
        assert entry is not None
        menu_id = menu_id_for_provider(entry.data.get(CONF_OAUTH_PROVIDER, ""))
        _identity_provider, display_name = PROVIDERS[menu_id]
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_social",
                data_schema=vol.Schema({}),
                description_placeholders={"provider": display_name},
            )
        return await self._async_start_oauth(menu_id)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter the password for an email-and-password entry."""
        assert self._reauth_entry is not None
        username: str = self._reauth_entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                account_id, refresh_token = await async_validate_login(
                    self.hass, username, user_input[CONF_PASSWORD]
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except EmporiaConnectionError:
                errors["base"] = "cannot_connect"
            # Must stay AFTER the two branches above: both subclass EmporiaError.
            except EmporiaError:
                _LOGGER.exception("Unexpected Emporia error during re-authentication")
                errors["base"] = "unknown"
            else:
                if account_id != self._reauth_entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self._async_update_and_abort(
                    self._reauth_entry,
                    {
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "refresh_token": refresh_token,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": username},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> EmporiaOptionsFlow:
        """Return the options flow."""
        return EmporiaOptionsFlow()


class EmporiaOptionsFlow(OptionsFlow):
    """Options flow: intervals, adaptive toggle, default amperage."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit polling and charging options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADAPTIVE, default=current.get(CONF_ADAPTIVE, DEFAULT_ADAPTIVE)
                ): bool,
                vol.Required(
                    CONF_IDLE_INTERVAL,
                    default=current.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
                vol.Required(
                    CONF_CHARGING_INTERVAL,
                    default=current.get(CONF_CHARGING_INTERVAL, DEFAULT_CHARGING_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
                vol.Required(
                    CONF_DEFAULT_AMPS, default=current.get(CONF_DEFAULT_AMPS, DEFAULT_AMPS)
                ): vol.All(vol.Coerce(int), vol.Range(min=DEFAULT_MIN_AMPS, max=DEFAULT_MAX_AMPS)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
