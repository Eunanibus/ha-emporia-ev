"""Config, reauth, and options flows for Emporia EV Charger."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import AuthError, EmporiaAuth, EmporiaClient, EmporiaConnectionError
from .const import (
    CONF_ADAPTIVE,
    CONF_CHARGING_INTERVAL,
    CONF_DEFAULT_AMPS,
    CONF_IDLE_INTERVAL,
    DEFAULT_ADAPTIVE,
    DEFAULT_AMPS,
    DEFAULT_CHARGING_INTERVAL,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MAX_AMPS,
    DEFAULT_MIN_AMPS,
    DOMAIN,
)

USER_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


async def async_validate_login(
    hass: HomeAssistant, username: str, password: str
) -> tuple[str, str | None]:
    """Validate credentials; return (account_id, refresh_token). Raises client errors."""
    session = async_get_clientsession(hass)
    auth = EmporiaAuth(session, username=username, password=password)
    client = EmporiaClient(session, auth)
    await client.authenticate()
    return client.account_id, auth.refresh_token  # type: ignore[return-value]


class EmporiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Emporia EV Charger config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            username = self._reauth_entry.data[CONF_USERNAME]
            try:
                account_id, refresh_token = await async_validate_login(
                    self.hass, username, user_input[CONF_PASSWORD]
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except EmporiaConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if account_id != self._reauth_entry.unique_id:
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "refresh_token": refresh_token,
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> EmporiaOptionsFlow:
        return EmporiaOptionsFlow()


class EmporiaOptionsFlow(OptionsFlow):
    """Options flow: intervals, adaptive toggle, default amperage."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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
