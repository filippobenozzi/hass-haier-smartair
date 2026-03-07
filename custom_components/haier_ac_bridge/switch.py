"""Switch platform for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ACDEVICE_DRYMODE,
    CONF_ACDEVICE_FAN_RIGHTLEFT,
    CONF_ACDEVICE_FAN_UPDOWN,
    CONF_ACDEVICE_HEALTHMODE,
    CONF_HEALTH_MODE_TYPE,
    CONF_SWING_TYPE,
    CONF_USE_DRY_MODE,
    DOMAIN,
    HEALTH_MODE_SHOW,
    SWING_TYPE_INDIVIDUAL,
)
from .entity import HaierBaseEntity

SWITCH_HEALTH = "health"
SWITCH_DRY = "dry"
SWITCH_SWING_RL = "swing_rl"
SWITCH_SWING_UD = "swing_ud"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haier switch entities from config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator

    enabled_switches: list[str] = []
    if coordinator.options[CONF_HEALTH_MODE_TYPE] == HEALTH_MODE_SHOW:
        enabled_switches.append(SWITCH_HEALTH)
    if coordinator.options[CONF_USE_DRY_MODE]:
        enabled_switches.append(SWITCH_DRY)
    if coordinator.options[CONF_SWING_TYPE] == SWING_TYPE_INDIVIDUAL:
        enabled_switches.extend([SWITCH_SWING_RL, SWITCH_SWING_UD])

    known_ids: set[str] = set()

    def _async_add_new_entities() -> None:
        to_add: list[HaierSwitchEntity] = []
        for device_id in (coordinator.data or {}):
            for switch_type in enabled_switches:
                entity_key = f"{device_id}_{switch_type}"
                if entity_key in known_ids:
                    continue
                to_add.append(HaierSwitchEntity(coordinator, device_id, switch_type))
                known_ids.add(entity_key)

        if to_add:
            async_add_entities(to_add)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class HaierSwitchEntity(HaierBaseEntity, SwitchEntity):
    """Representation of one auxiliary Haier switch."""

    def __init__(self, coordinator, device_id: str, switch_type: str) -> None:
        super().__init__(coordinator, device_id)
        self._switch_type = switch_type
        self._attr_unique_id = f"{device_id}_{switch_type}"

    @property
    def name(self) -> str:
        device = self.device
        if device:
            suffix = device["name"]
        else:
            suffix = self._device_id

        if self._switch_type == SWITCH_HEALTH:
            prefix = self.coordinator.options[CONF_ACDEVICE_HEALTHMODE]
        elif self._switch_type == SWITCH_DRY:
            prefix = self.coordinator.options[CONF_ACDEVICE_DRYMODE]
        elif self._switch_type == SWITCH_SWING_RL:
            prefix = self.coordinator.options[CONF_ACDEVICE_FAN_RIGHTLEFT]
        else:
            prefix = self.coordinator.options[CONF_ACDEVICE_FAN_UPDOWN]

        return f"{prefix} {suffix}"

    @property
    def is_on(self) -> bool | None:
        device = self.device
        if not device:
            return None

        if self._switch_type == SWITCH_HEALTH:
            return bool(device["powerstate"] and device["healthmode"])

        if self._switch_type == SWITCH_DRY:
            return bool(device["powerstate"] and device["mode"] == "DRY")

        if self._switch_type == SWITCH_SWING_RL:
            return bool(device["powerstate"] and device["swing_rl"])

        return bool(device["powerstate"] and device["swing_ud"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        def _mutator(state: dict[str, Any]) -> None:
            if self._switch_type == SWITCH_HEALTH:
                if state["powerstate"]:
                    state["healthmode"] = True
                return

            if self._switch_type == SWITCH_DRY:
                state["last_power_state"] = state["powerstate"]
                state["last_target_mode"] = state["mode"]
                state["powerstate"] = True
                state["mode"] = "DRY"
                return

            if self._switch_type == SWITCH_SWING_RL and state["powerstate"]:
                state["swing_rl"] = True
                return

            if self._switch_type == SWITCH_SWING_UD and state["powerstate"]:
                state["swing_ud"] = True

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_turn_off(self, **kwargs: Any) -> None:
        def _mutator(state: dict[str, Any]) -> None:
            if self._switch_type == SWITCH_HEALTH:
                if state["powerstate"]:
                    state["healthmode"] = False
                return

            if self._switch_type == SWITCH_DRY:
                state["mode"] = state.get("last_target_mode", "COOL")
                if not state.get("last_power_state", False):
                    state["powerstate"] = False
                return

            if self._switch_type == SWITCH_SWING_RL and state["powerstate"]:
                state["swing_rl"] = False
                return

            if self._switch_type == SWITCH_SWING_UD and state["powerstate"]:
                state["swing_ud"] = False

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)
