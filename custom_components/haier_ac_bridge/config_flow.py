"""Config flow for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HaierBridgeApi, HaierBridgeAuthError, HaierBridgeError
from .const import (
    CONF_ACDEVICE_DRYMODE,
    CONF_ACDEVICE_FAN_RIGHTLEFT,
    CONF_ACDEVICE_FAN_UPDOWN,
    CONF_ACDEVICE_HEALTHMODE,
    CONF_ACDEVICE_NAME,
    CONF_HEALTH_MODE_TYPE,
    CONF_POLLING,
    CONF_SWING_TYPE,
    CONF_USE_DRY_MODE,
    CONF_USE_FAN_MODE,
    DOMAIN,
    HEALTH_MODE_TYPES,
    OPTION_DEFAULTS,
    SWING_TYPES,
)


def _build_schema(defaults: dict[str, Any], *, include_connection: bool) -> vol.Schema:
    data_schema: dict[Any, Any] = {}

    if include_connection:
        data_schema[vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, ""))] = str
        data_schema[vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, ""))] = str

    data_schema[vol.Required(CONF_POLLING, default=defaults[CONF_POLLING])] = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=60)
    )
    data_schema[vol.Required(CONF_USE_FAN_MODE, default=defaults[CONF_USE_FAN_MODE])] = bool
    data_schema[vol.Required(CONF_USE_DRY_MODE, default=defaults[CONF_USE_DRY_MODE])] = bool
    data_schema[vol.Required(CONF_HEALTH_MODE_TYPE, default=defaults[CONF_HEALTH_MODE_TYPE])] = vol.In(
        HEALTH_MODE_TYPES
    )
    data_schema[vol.Required(CONF_SWING_TYPE, default=defaults[CONF_SWING_TYPE])] = vol.In(SWING_TYPES)

    data_schema[vol.Required(CONF_ACDEVICE_NAME, default=defaults[CONF_ACDEVICE_NAME])] = str
    data_schema[vol.Required(
        CONF_ACDEVICE_FAN_RIGHTLEFT,
        default=defaults[CONF_ACDEVICE_FAN_RIGHTLEFT],
    )] = str
    data_schema[vol.Required(
        CONF_ACDEVICE_FAN_UPDOWN,
        default=defaults[CONF_ACDEVICE_FAN_UPDOWN],
    )] = str
    data_schema[vol.Required(
        CONF_ACDEVICE_HEALTHMODE,
        default=defaults[CONF_ACDEVICE_HEALTHMODE],
    )] = str
    data_schema[vol.Required(
        CONF_ACDEVICE_DRYMODE,
        default=defaults[CONF_ACDEVICE_DRYMODE],
    )] = str

    return vol.Schema(data_schema)


class HaierAcBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haier AC Bridge."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            token = user_input[CONF_TOKEN]

            api = HaierBridgeApi(host, token, async_get_clientsession(self.hass))
            try:
                await api.async_get_devices()
            except HaierBridgeAuthError:
                errors["base"] = "invalid_auth"
            except HaierBridgeError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()

                data = {
                    CONF_HOST: host,
                    CONF_TOKEN: token,
                }
                options = {
                    key: value
                    for key, value in user_input.items()
                    if key not in {CONF_HOST, CONF_TOKEN}
                }

                return self.async_create_entry(
                    title=f"Haier AC Bridge ({host})",
                    data=data,
                    options=options,
                )

        defaults = dict(OPTION_DEFAULTS)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(defaults, include_connection=True),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get options flow handler."""
        return HaierAcBridgeOptionsFlow(config_entry)


class HaierAcBridgeOptionsFlow(OptionsFlow):
    """Handle options for Haier AC Bridge."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        defaults = dict(OPTION_DEFAULTS)
        defaults.update(self.config_entry.options)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(defaults, include_connection=False),
        )
