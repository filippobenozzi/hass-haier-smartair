"""Home Assistant integration bootstrap for Haier AC Bridge."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HaierApiClient, HaierBridgeApi, HaierCloudApi
from .const import (
    CONF_CONNECTION_TYPE,
    CONNECTION_TYPE_BRIDGE,
    CONNECTION_TYPE_CLOUD,
    DOMAIN,
    OPTION_DEFAULTS,
    PLATFORMS,
)
from .coordinator import HaierDataCoordinator


@dataclass
class HaierRuntimeData:
    """Runtime objects for a config entry."""

    api: HaierApiClient
    coordinator: HaierDataCoordinator
    options: dict[str, Any]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Haier AC Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    options = dict(OPTION_DEFAULTS)
    options.update(entry.options)

    session = async_get_clientsession(hass)
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_TYPE_BRIDGE)

    if connection_type == CONNECTION_TYPE_CLOUD:
        api: HaierApiClient = HaierCloudApi(
            hass=hass,
            email=entry.data[CONF_EMAIL],
            password=entry.data[CONF_PASSWORD],
            session=session,
        )
        await api.async_get_devices()
    else:
        api = HaierBridgeApi(
            host=entry.data[CONF_HOST],
            token=entry.data[CONF_TOKEN],
            session=session,
        )

    coordinator = HaierDataCoordinator(hass, api, options)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = HaierRuntimeData(
        api=api,
        coordinator=coordinator,
        options=options,
    )

    # Pre-import platform modules in executor to avoid blocking import warnings.
    for platform in PLATFORMS:
        await hass.async_add_executor_job(
            importlib.import_module,
            f"{__package__}.{platform}",
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = hass.data[DOMAIN].get(entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if runtime is not None:
            await runtime.api.async_close()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)
