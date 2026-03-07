"""Config flow for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HaierBridgeApi, HaierBridgeAuthError, HaierBridgeError, HaierCloudApi
from .const import (
    CONF_ACDEVICE_DRYMODE,
    CONF_ACDEVICE_FAN_RIGHTLEFT,
    CONF_ACDEVICE_FAN_UPDOWN,
    CONF_ACDEVICE_HEALTHMODE,
    CONF_ACDEVICE_NAME,
    CONF_CONNECTION_TYPE,
    CONF_HEALTH_MODE_TYPE,
    CONF_POLLING,
    CONF_SWING_TYPE,
    CONF_USE_DRY_MODE,
    CONF_USE_FAN_MODE,
    CONNECTION_TYPE_BRIDGE,
    CONNECTION_TYPE_CLOUD,
    CONNECTION_TYPES,
    DOMAIN,
    HEALTH_MODE_TYPES,
    OPTION_DEFAULTS,
    SWING_TYPES,
)


def _build_options_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    fields: dict[Any, Any] = {}
    fields[vol.Required(CONF_POLLING, default=defaults[CONF_POLLING])] = vol.All(
        vol.Coerce(int), vol.Range(min=1, max=60)
    )
    fields[vol.Required(CONF_USE_FAN_MODE, default=defaults[CONF_USE_FAN_MODE])] = bool
    fields[vol.Required(CONF_USE_DRY_MODE, default=defaults[CONF_USE_DRY_MODE])] = bool
    fields[vol.Required(CONF_HEALTH_MODE_TYPE, default=defaults[CONF_HEALTH_MODE_TYPE])] = vol.In(
        HEALTH_MODE_TYPES
    )
    fields[vol.Required(CONF_SWING_TYPE, default=defaults[CONF_SWING_TYPE])] = vol.In(SWING_TYPES)

    fields[vol.Required(CONF_ACDEVICE_NAME, default=defaults[CONF_ACDEVICE_NAME])] = str
    fields[vol.Required(
        CONF_ACDEVICE_FAN_RIGHTLEFT,
        default=defaults[CONF_ACDEVICE_FAN_RIGHTLEFT],
    )] = str
    fields[vol.Required(
        CONF_ACDEVICE_FAN_UPDOWN,
        default=defaults[CONF_ACDEVICE_FAN_UPDOWN],
    )] = str
    fields[vol.Required(
        CONF_ACDEVICE_HEALTHMODE,
        default=defaults[CONF_ACDEVICE_HEALTHMODE],
    )] = str
    fields[vol.Required(
        CONF_ACDEVICE_DRYMODE,
        default=defaults[CONF_ACDEVICE_DRYMODE],
    )] = str
    return fields


def _build_bridge_schema(defaults: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
        vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
    }
    fields.update(_build_options_fields(defaults))
    return vol.Schema(fields)


def _build_cloud_schema(defaults: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
        vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): str,
    }
    fields.update(_build_options_fields(defaults))
    return vol.Schema(fields)


def _build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(_build_options_fields(defaults))


class HaierAcBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haier AC Bridge."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]
            if connection_type == CONNECTION_TYPE_CLOUD:
                return await self.async_step_cloud()
            return await self.async_step_bridge()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CONNECTION_TYPE,
                        default=CONNECTION_TYPE_CLOUD,
                    ): vol.In(CONNECTION_TYPES)
                }
            ),
        )

    async def async_step_bridge(self, user_input: dict[str, Any] | None = None):
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
                unique_id = f"{CONNECTION_TYPE_BRIDGE}:{host}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                data = {
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_BRIDGE,
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
            step_id="bridge",
            data_schema=_build_bridge_schema(defaults),
            errors=errors,
        )

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            api = HaierCloudApi(
                hass=self.hass,
                email=email,
                password=password,
                session=async_get_clientsession(self.hass),
            )
            try:
                devices = await api.async_get_devices()
            except HaierBridgeAuthError:
                errors["base"] = "invalid_auth"
            except HaierBridgeError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    unique_id = f"{CONNECTION_TYPE_CLOUD}:{email.lower()}"
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()

                    data = {
                        CONF_CONNECTION_TYPE: CONNECTION_TYPE_CLOUD,
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    }
                    options = {
                        key: value
                        for key, value in user_input.items()
                        if key not in {CONF_EMAIL, CONF_PASSWORD}
                    }

                    return self.async_create_entry(
                        title=f"Haier AC Cloud ({email})",
                        data=data,
                        options=options,
                    )
            finally:
                await api.async_close()

        defaults = dict(OPTION_DEFAULTS)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="cloud",
            data_schema=_build_cloud_schema(defaults),
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
            data_schema=_build_options_schema(defaults),
        )
