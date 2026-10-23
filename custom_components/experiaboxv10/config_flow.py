"""Config flow for the Experia Box V10."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CONSIDER_HOME,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .router import (
    CannotConnect,
    InvalidAuth,
    UnexpectedResponse,
    fetch_clients,
    normalize_host,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _credential_schema(
    *, host: str | None = None, username: str | None = None
) -> probatio.Schema:
    """Build the host, username, and password form."""
    host_field: probatio.Required
    if host is None:
        host_field = probatio.Required(CONF_HOST)
    else:
        host_field = probatio.Required(CONF_HOST, default=host)
    return probatio.Schema(
        {
            host_field: str,
            probatio.Required(
                CONF_USERNAME,
                default=DEFAULT_USERNAME if username is None else username,
            ): str,
            probatio.Required(CONF_PASSWORD): _PASSWORD,
        }
    )


def _seconds_selector(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class ExperiaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Experia Box V10."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ExperiaOptionsFlow:
        """Create the options flow."""
        return ExperiaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started from the UI."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            username = str(user_input[CONF_USERNAME]).strip()
            password = user_input[CONF_PASSWORD]
            if not host:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                error = await self._async_validate(host, username, password)
                if error is None:
                    return self.async_create_entry(
                        title=host,
                        data={
                            CONF_HOST: host,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )
                errors["base"] = error

        suggested = user_input or {}
        return self.async_show_form(
            step_id="user",
            data_schema=_credential_schema(
                host=suggested.get(CONF_HOST),
                username=suggested.get(CONF_USERNAME),
            ),
            errors=errors,
        )

    async def async_step_import(
        self, user_input: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Import a legacy ``device_tracker`` YAML platform."""
        host = normalize_host(str(user_input[CONF_HOST]))
        username = str(user_input[CONF_USERNAME]).strip()
        password = user_input[CONF_PASSWORD]
        if not host:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(host)
        self._abort_if_unique_id_configured()
        error = await self._async_validate(host, username, password)
        if error is not None:
            return self.async_abort(reason=error)

        options: dict[str, int] = {}
        if CONF_CONSIDER_HOME in user_input:
            options[CONF_CONSIDER_HOME] = int(user_input[CONF_CONSIDER_HOME])
        if CONF_SCAN_INTERVAL in user_input:
            options[CONF_SCAN_INTERVAL] = max(
                int(user_input[CONF_SCAN_INTERVAL]), MIN_SCAN_INTERVAL
            )
        return self.async_create_entry(
            title=host,
            data={
                CONF_HOST: host,
                CONF_USERNAME: username,
                CONF_PASSWORD: password,
            },
            options=options,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a reauthentication flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new username and password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input[CONF_USERNAME]).strip()
            password = user_input[CONF_PASSWORD]
            error = await self._async_validate(
                entry.data[CONF_HOST], username, password
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_USERNAME: username, CONF_PASSWORD: password},
                )
            errors["base"] = error

        username_default = entry.data[CONF_USERNAME]
        if user_input is not None:
            username_default = user_input[CONF_USERNAME]
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_USERNAME, default=username_default): str,
                    probatio.Required(CONF_PASSWORD): _PASSWORD,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update the host or login for an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            username = str(user_input[CONF_USERNAME]).strip()
            password = user_input[CONF_PASSWORD]
            if not host:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(host)
                existing = self.hass.config_entries.async_entry_for_domain_unique_id(
                    DOMAIN, host
                )
                if existing is not None and existing.entry_id != entry.entry_id:
                    return self.async_abort(reason="already_configured")
                error = await self._async_validate(host, username, password)
                if error is None:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=host,
                        title=host,
                        data_updates={
                            CONF_HOST: host,
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                        },
                    )
                errors["base"] = error

        suggested = user_input or entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credential_schema(
                host=suggested.get(CONF_HOST, entry.data[CONF_HOST]),
                username=suggested.get(CONF_USERNAME, entry.data[CONF_USERNAME]),
            ),
            errors=errors,
        )

    async def _async_validate(
        self, host: str, username: str, password: str
    ) -> str | None:
        """Return an error key, or None when the router accepted the login."""
        try:
            await self.hass.async_add_executor_job(
                fetch_clients, host, username, password
            )
        except CannotConnect:
            return "cannot_connect"
        except InvalidAuth:
            return "invalid_auth"
        except UnexpectedResponse:
            _LOGGER.debug(
                "Unexpected response from Experia Box at %s", host, exc_info=True
            )
            return "unknown"
        except Exception:
            _LOGGER.exception("Unexpected error validating Experia Box at %s", host)
            return "unknown"
        return None


class ExperiaOptionsFlow(OptionsFlowWithReload):
    """Handle Experia Box options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage polling and the consider-home grace period."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: max(
                        int(user_input[CONF_SCAN_INTERVAL]), MIN_SCAN_INTERVAL
                    ),
                    CONF_CONSIDER_HOME: int(user_input[CONF_CONSIDER_HOME]),
                },
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): _seconds_selector(MIN_SCAN_INTERVAL, 3600),
                    probatio.Required(
                        CONF_CONSIDER_HOME,
                        default=options.get(CONF_CONSIDER_HOME, DEFAULT_CONSIDER_HOME),
                    ): _seconds_selector(0, 86400),
                }
            ),
        )
