"""Home Assistant integration bootstrap for Haier AC Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HaierBridgeApi
from .const import DOMAIN, OPTION_DEFAULTS, PLATFORMS
from .coordinator import HaierDataCoordinator


@dataclass
class HaierRuntimeData:
    """Runtime objects for a config entry."""

    api: HaierBridgeApi
    coordinator: HaierDataCoordinator
    options: dict[str, Any]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Haier AC Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    options = dict(OPTION_DEFAULTS)
    options.update(entry.options)

    api = HaierBridgeApi(
        host=entry.data[CONF_HOST],
        token=entry.data[CONF_TOKEN],
        session=async_get_clientsession(hass),
    )

    coordinator = HaierDataCoordinator(hass, api, options)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = HaierRuntimeData(
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
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry after options update."""
    await hass.config_entries.async_reload(entry.entry_id)
