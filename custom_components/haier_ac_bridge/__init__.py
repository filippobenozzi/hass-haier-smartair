"""Home Assistant integration bootstrap for Haier AC Bridge."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HaierApiClient, HaierBridgeApi, HaierDirectApi
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_MAC,
    CONNECTION_TYPE_BRIDGE,
    CONNECTION_TYPE_DIRECT,
    DOMAIN,
    OPTION_DEFAULTS,
    PLATFORMS,
)
from .coordinator import HaierDataCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class HaierRuntimeData:
    """Runtime objects for a config entry."""

    api: HaierApiClient
    coordinator: HaierDataCoordinator
    options: dict[str, Any]


def _build_api(hass: HomeAssistant, entry: ConfigEntry) -> HaierApiClient:
    """Create the API client described by the entry data."""
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_BRIDGE)

    try:
        if connection_type == CONNECTION_TYPE_DIRECT:
            return HaierDirectApi(
                host=entry.data[CONF_HOST],
                mac=entry.data[CONF_MAC],
            )
        return HaierBridgeApi(
            host=entry.data[CONF_HOST],
            token=entry.data[CONF_TOKEN],
            session=async_get_clientsession(hass),
        )
    except KeyError as err:
        # Stored data is incomplete; ask the user to reconfigure instead of
        # crashing on every restart.
        raise ConfigEntryError(f"Missing configuration value: {err}") from err
    except ValueError as err:
        raise ConfigEntryError(f"Invalid configuration value: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Haier AC Bridge from a config entry."""
    options = dict(OPTION_DEFAULTS)
    options.update(entry.options)

    api = _build_api(hass, entry)
    coordinator = HaierDataCoordinator(hass, api, options, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # Do not leak the direct-mode TCP socket while HA retries the setup.
        await api.async_close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = HaierRuntimeData(
        api=api,
        coordinator=coordinator,
        options=options,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime: HaierRuntimeData | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        await runtime.coordinator.async_shutdown()
        await runtime.api.async_close()

    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)

    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry after its data or options changed."""
    await hass.config_entries.async_reload(entry.entry_id)
