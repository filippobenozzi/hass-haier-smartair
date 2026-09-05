"""Config flow for Haier AC Bridge."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    HaierBridgeApi,
    HaierBridgeAuthError,
    HaierBridgeError,
    HaierDirectApi,
    sanitize_host,
    sanitize_mac,
)
from .const import (
    CONF_ACDEVICE_DRYMODE,
    CONF_ACDEVICE_FAN_RIGHTLEFT,
    CONF_ACDEVICE_FAN_UPDOWN,
    CONF_ACDEVICE_HEALTHMODE,
    CONF_ACDEVICE_NAME,
    CONF_CONNECTION_TYPE,
    CONF_HEALTH_MODE_TYPE,
    CONF_MAC,
    CONF_POLLING,
    CONF_SWING_TYPE,
    CONF_USE_DRY_MODE,
    CONF_USE_FAN_MODE,
    CONNECTION_TYPE_BRIDGE,
    CONNECTION_TYPE_DIRECT,
    CONNECTION_TYPES,
    DOMAIN,
    HEALTH_MODE_TYPES,
    MAX_POLLING_SECONDS,
    MIN_POLLING_SECONDS,
    OPTION_DEFAULTS,
    SWING_TYPES,
)

_LOGGER = logging.getLogger(__name__)


def _build_options_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    """Return the behavior fields shared by the setup and the options flow."""
    merged = dict(OPTION_DEFAULTS)
    merged.update({key: value for key, value in defaults.items() if key in OPTION_DEFAULTS})

    return {
        vol.Required(CONF_POLLING, default=merged[CONF_POLLING]): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_POLLING_SECONDS, max=MAX_POLLING_SECONDS)
        ),
        vol.Required(CONF_USE_FAN_MODE, default=merged[CONF_USE_FAN_MODE]): bool,
        vol.Required(CONF_USE_DRY_MODE, default=merged[CONF_USE_DRY_MODE]): bool,
        vol.Required(CONF_HEALTH_MODE_TYPE, default=merged[CONF_HEALTH_MODE_TYPE]): vol.In(
            HEALTH_MODE_TYPES
        ),
        vol.Required(CONF_SWING_TYPE, default=merged[CONF_SWING_TYPE]): vol.In(SWING_TYPES),
        vol.Required(CONF_ACDEVICE_NAME, default=merged[CONF_ACDEVICE_NAME]): str,
        vol.Required(CONF_ACDEVICE_FAN_RIGHTLEFT, default=merged[CONF_ACDEVICE_FAN_RIGHTLEFT]): str,
        vol.Required(CONF_ACDEVICE_FAN_UPDOWN, default=merged[CONF_ACDEVICE_FAN_UPDOWN]): str,
        vol.Required(CONF_ACDEVICE_HEALTHMODE, default=merged[CONF_ACDEVICE_HEALTHMODE]): str,
        vol.Required(CONF_ACDEVICE_DRYMODE, default=merged[CONF_ACDEVICE_DRYMODE]): str,
    }


def _build_connection_fields(connection_type: str, defaults: dict[str, Any]) -> dict[Any, Any]:
    """Return the host/token or host/mac fields for a connection type."""
    fields: dict[Any, Any] = {
        vol.Required(CONF_HOST, default=str(defaults.get(CONF_HOST, ""))): str,
    }
    if connection_type == CONNECTION_TYPE_DIRECT:
        fields[vol.Required(CONF_MAC, default=str(defaults.get(CONF_MAC, "")))] = str
    else:
        fields[vol.Required(CONF_TOKEN, default=str(defaults.get(CONF_TOKEN, "")))] = str
    return fields


def _build_setup_schema(connection_type: str, defaults: dict[str, Any]) -> vol.Schema:
    """Return the initial setup schema: connection details plus behavior options."""
    fields = _build_connection_fields(connection_type, defaults)
    fields.update(_build_options_fields(defaults))
    return vol.Schema(fields)


def _build_connection_schema(connection_type: str, defaults: dict[str, Any]) -> vol.Schema:
    """Return the schema used whenever only the connection details are edited."""
    return vol.Schema(_build_connection_fields(connection_type, defaults))


def _build_options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return the schema holding the behavior options only."""
    return vol.Schema(_build_options_fields(defaults))


async def _async_validate_connection(
    hass: HomeAssistant, connection_type: str, user_input: dict[str, Any]
) -> tuple[dict[str, str], dict[str, Any], str | None]:
    """Probe the device and return (errors, normalized entry data, unique id)."""
    try:
        host = sanitize_host(user_input.get(CONF_HOST, ""))
    except ValueError:
        return {CONF_HOST: "invalid_host"}, {}, None

    if connection_type == CONNECTION_TYPE_DIRECT:
        try:
            mac = sanitize_mac(user_input.get(CONF_MAC, ""))
        except ValueError:
            return {CONF_MAC: "invalid_mac"}, {}, None

        api = HaierDirectApi(host, mac)
        try:
            devices = await api.async_get_devices()
        except HaierBridgeError as err:
            _LOGGER.debug("Direct connection check failed: %s", err)
            return {"base": "cannot_connect"}, {}, None
        except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
            _LOGGER.exception("Unexpected error while probing the direct AC")
            return {"base": "unknown"}, {}, None
        finally:
            await api.async_close()

        data = {
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_DIRECT,
            CONF_HOST: host,
            CONF_MAC: mac,
        }
        return {}, data, f"{CONNECTION_TYPE_DIRECT}:{devices[0]['id']}"

    token = str(user_input.get(CONF_TOKEN, "")).strip()
    if not token:
        return {CONF_TOKEN: "invalid_auth"}, {}, None

    api = HaierBridgeApi(host, token, async_get_clientsession(hass))
    try:
        await api.async_get_devices()
    except HaierBridgeAuthError as err:
        _LOGGER.debug("Bridge rejected the token: %s", err)
        return {"base": "invalid_auth"}, {}, None
    except HaierBridgeError as err:
        _LOGGER.debug("Bridge connection check failed: %s", err)
        return {"base": "cannot_connect"}, {}, None
    except Exception:  # noqa: BLE001 - surfaced to the user as "unknown"
        _LOGGER.exception("Unexpected error while probing the bridge")
        return {"base": "unknown"}, {}, None

    data = {
        CONF_CONNECTION_TYPE: CONNECTION_TYPE_BRIDGE,
        CONF_HOST: host,
        CONF_TOKEN: token,
    }
    # Legacy unique id scheme: keep the bare host so entries created by earlier
    # versions keep matching.
    return {}, data, host


def _entry_title(connection_type: str, host: str) -> str:
    """Return the entry title for a connection type."""
    if connection_type == CONNECTION_TYPE_DIRECT:
        return f"Haier AC Direct ({host})"
    return f"Haier AC Bridge ({host})"


def _async_reload_if_not_loaded(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload an entry that has no update listener registered.

    While the entry is loaded, ``_async_entry_updated`` already reloads it. When
    setup failed (which is exactly when the user comes here to fix the IP or the
    token) no listener exists, so the reload has to be scheduled explicitly.
    """
    if entry.state is not ConfigEntryState.LOADED:
        hass.config_entries.async_schedule_reload(entry.entry_id)


class HaierAcBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Haier AC Bridge."""

    VERSION = 1

    # Set by the reconfigure/reauth steps to the entry being edited.
    _entry: ConfigEntry | None = None

    # -- initial setup ----------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask which transport to use."""
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_TYPE_DIRECT:
                return await self.async_step_direct()
            return await self.async_step_bridge()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TYPE_BRIDGE): vol.In(
                        CONNECTION_TYPES
                    )
                }
            ),
        )

    async def async_step_bridge(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect bridge host and token."""
        return await self._async_step_setup(CONNECTION_TYPE_BRIDGE, "bridge", user_input)

    async def async_step_direct(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect AC host and MAC."""
        return await self._async_step_setup(CONNECTION_TYPE_DIRECT, "direct", user_input)

    async def _async_step_setup(
        self, connection_type: str, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, data, unique_id = await _async_validate_connection(
                self.hass, connection_type, user_input
            )
            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                options = {
                    key: value for key, value in user_input.items() if key in OPTION_DEFAULTS
                }
                return self.async_create_entry(
                    title=_entry_title(connection_type, data[CONF_HOST]),
                    data=data,
                    options=options,
                )

        defaults = dict(OPTION_DEFAULTS)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id=step_id,
            data_schema=_build_setup_schema(connection_type, defaults),
            errors=errors,
        )

    # -- reconfigure (change IP / token / MAC of an existing entry) --------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user edit the connection details of a configured entry."""
        entry = self._async_current_flow_entry()
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        self._entry = entry
        connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_BRIDGE)
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, data, unique_id = await _async_validate_connection(
                self.hass, connection_type, user_input
            )
            if not errors:
                conflict = self._async_entry_with_unique_id(unique_id, entry.entry_id)
                if conflict is not None:
                    return self.async_abort(reason="already_configured")

                new_data = {**entry.data, **data}
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=new_data,
                    unique_id=unique_id,
                    title=_entry_title(connection_type, data[CONF_HOST]),
                )
                _async_reload_if_not_loaded(self.hass, entry)
                return self.async_abort(reason="reconfigure_successful")

        defaults = dict(entry.data)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_connection_schema(connection_type, defaults),
            errors=errors,
            description_placeholders={"host": str(entry.data.get(CONF_HOST, ""))},
        )

    # -- reauth (token no longer accepted) --------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start the re-authentication flow."""
        self._entry = self._async_current_flow_entry()
        if self._entry is None:
            return self.async_abort(reason="entry_not_found")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh host/token pair."""
        entry = self._entry
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_BRIDGE)
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, data, unique_id = await _async_validate_connection(
                self.hass, connection_type, user_input
            )
            if not errors:
                conflict = self._async_entry_with_unique_id(unique_id, entry.entry_id)
                if conflict is not None:
                    return self.async_abort(reason="already_configured")

                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, **data},
                    unique_id=unique_id,
                    title=_entry_title(connection_type, data[CONF_HOST]),
                )
                _async_reload_if_not_loaded(self.hass, entry)
                return self.async_abort(reason="reauth_successful")

        defaults = dict(entry.data)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_build_connection_schema(connection_type, defaults),
            errors=errors,
            description_placeholders={"host": str(entry.data.get(CONF_HOST, ""))},
        )

    # -- helpers ----------------------------------------------------------

    def _async_current_flow_entry(self) -> ConfigEntry | None:
        """Return the entry this reconfigure/reauth flow belongs to."""
        entry_id = self.context.get("entry_id")
        if not entry_id:
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

    def _async_entry_with_unique_id(
        self, unique_id: str | None, exclude_entry_id: str
    ) -> ConfigEntry | None:
        """Return another entry already claiming this unique id, if any."""
        if unique_id is None:
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id != exclude_entry_id and entry.unique_id == unique_id:
                return entry
        return None

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get options flow handler."""
        return HaierAcBridgeOptionsFlow(config_entry)


class HaierAcBridgeOptionsFlow(OptionsFlow):
    """Handle options for Haier AC Bridge.

    Besides the behavior settings this flow can also edit the connection
    details, so host, token and MAC stay reachable after the initial setup even
    on cores that do not expose the reconfigure button.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Remember the entry being configured."""
        super().__init__()
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the choice between connection details and behavior."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["connection", "settings"],
        )

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit host, token or MAC of the configured entry."""
        entry = self._entry
        connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_BRIDGE)
        errors: dict[str, str] = {}

        if user_input is not None:
            errors, data, unique_id = await _async_validate_connection(
                self.hass, connection_type, user_input
            )
            if not errors:
                duplicate = any(
                    other.entry_id != entry.entry_id and other.unique_id == unique_id
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                )
                if duplicate:
                    errors["base"] = "already_configured"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, **data},
                        unique_id=unique_id,
                        title=_entry_title(connection_type, data[CONF_HOST]),
                    )
                    _async_reload_if_not_loaded(self.hass, entry)
                    # Close the flow without touching the behavior options.
                    return self.async_create_entry(title="", data=dict(entry.options))

        defaults = dict(entry.data)
        if user_input:
            defaults.update(user_input)

        return self.async_show_form(
            step_id="connection",
            data_schema=_build_connection_schema(connection_type, defaults),
            errors=errors,
            description_placeholders={"host": str(entry.data.get(CONF_HOST, ""))},
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the behavior options."""
        if user_input is not None:
            options = dict(self._entry.options)
            options.update(
                {key: value for key, value in user_input.items() if key in OPTION_DEFAULTS}
            )
            return self.async_create_entry(title="", data=options)

        defaults = dict(OPTION_DEFAULTS)
        defaults.update(self._entry.options)

        return self.async_show_form(
            step_id="settings",
            data_schema=_build_options_schema(defaults),
        )
