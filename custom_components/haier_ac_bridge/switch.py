"""Switch platform for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ACDEVICE_DRYMODE,
    CONF_ACDEVICE_FAN_RIGHTLEFT,
    CONF_ACDEVICE_FAN_UPDOWN,
    CONF_ACDEVICE_HEALTHMODE,
    CONF_HEALTH_MODE_TYPE,
    CONF_SWING_TYPE,
    CONF_USE_DRY_MODE,
    DEFAULT_ACDEVICE_DRYMODE,
    DEFAULT_ACDEVICE_FAN_RIGHTLEFT,
    DEFAULT_ACDEVICE_FAN_UPDOWN,
    DEFAULT_ACDEVICE_HEALTHMODE,
    DEVICE_MODE_COOL,
    DEVICE_MODE_DRY,
    DOMAIN,
    HEALTH_MODE_SHOW,
    SWING_TYPE_INDIVIDUAL,
)
from .coordinator import HaierDataCoordinator
from .entity import HaierBaseEntity

SWITCH_HEALTH = "health"
SWITCH_DRY = "dry"
SWITCH_SWING_RL = "swing_rl"
SWITCH_SWING_UD = "swing_ud"

# switch type -> (option key holding the display prefix, fallback prefix)
SWITCH_NAME_OPTIONS: dict[str, tuple[str, str]] = {
    SWITCH_HEALTH: (CONF_ACDEVICE_HEALTHMODE, DEFAULT_ACDEVICE_HEALTHMODE),
    SWITCH_DRY: (CONF_ACDEVICE_DRYMODE, DEFAULT_ACDEVICE_DRYMODE),
    SWITCH_SWING_RL: (CONF_ACDEVICE_FAN_RIGHTLEFT, DEFAULT_ACDEVICE_FAN_RIGHTLEFT),
    SWITCH_SWING_UD: (CONF_ACDEVICE_FAN_UPDOWN, DEFAULT_ACDEVICE_FAN_UPDOWN),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haier switch entities from config entry."""
    coordinator: HaierDataCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator

    enabled_switches: list[str] = []
    if coordinator.options.get(CONF_HEALTH_MODE_TYPE) == HEALTH_MODE_SHOW:
        enabled_switches.append(SWITCH_HEALTH)
    if coordinator.options.get(CONF_USE_DRY_MODE):
        enabled_switches.append(SWITCH_DRY)
    if coordinator.options.get(CONF_SWING_TYPE) == SWING_TYPE_INDIVIDUAL:
        enabled_switches.extend([SWITCH_SWING_RL, SWITCH_SWING_UD])

    if not enabled_switches:
        return

    known_keys: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        to_add: list[HaierSwitchEntity] = []
        for device_id in coordinator.data or {}:
            for switch_type in enabled_switches:
                entity_key = f"{device_id}_{switch_type}"
                if entity_key in known_keys:
                    continue
                known_keys.add(entity_key)
                to_add.append(HaierSwitchEntity(coordinator, device_id, switch_type))

        if to_add:
            async_add_entities(to_add)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class HaierSwitchEntity(HaierBaseEntity, SwitchEntity):
    """Representation of one auxiliary Haier switch."""

    def __init__(self, coordinator: HaierDataCoordinator, device_id: str, switch_type: str) -> None:
        """Initialize an auxiliary switch."""
        super().__init__(coordinator, device_id)
        self._switch_type = switch_type
        self._attr_unique_id = f"{device_id}_{switch_type}"

    @property
    def name(self) -> str:
        """Return the entity name built from the configured prefix."""
        option_key, fallback = SWITCH_NAME_OPTIONS[self._switch_type]
        prefix = str(self.coordinator.options.get(option_key) or fallback)
        return f"{prefix} {self.device_label}".strip()

    @property
    def is_on(self) -> bool | None:
        """Return the switch state, which is always off while the AC is off."""
        device = self.device
        if not device or not device["powerstate"]:
            return False if device else None

        if self._switch_type == SWITCH_HEALTH:
            return bool(device["healthmode"])
        if self._switch_type == SWITCH_DRY:
            return device["mode"] == DEVICE_MODE_DRY
        if self._switch_type == SWITCH_SWING_RL:
            return bool(device["swing_rl"])
        return bool(device["swing_ud"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the auxiliary feature."""

        def _mutator(state: dict[str, Any]) -> None:
            if self._switch_type == SWITCH_HEALTH:
                if state["powerstate"]:
                    state["healthmode"] = True
                return

            if self._switch_type == SWITCH_DRY:
                if state["mode"] == DEVICE_MODE_DRY and state["powerstate"]:
                    return
                state["last_power_state"] = state["powerstate"]
                # Never remember DRY as the mode to restore, or turning the
                # switch back off would leave the AC stuck in dry mode.
                if state["mode"] != DEVICE_MODE_DRY:
                    state["last_target_mode"] = state["mode"]
                state["powerstate"] = True
                state["mode"] = DEVICE_MODE_DRY
                return

            if self._switch_type == SWITCH_SWING_RL and state["powerstate"]:
                state["swing_rl"] = True
                return

            if self._switch_type == SWITCH_SWING_UD and state["powerstate"]:
                state["swing_ud"] = True

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the auxiliary feature."""

        def _mutator(state: dict[str, Any]) -> None:
            if self._switch_type == SWITCH_HEALTH:
                if state["powerstate"]:
                    state["healthmode"] = False
                return

            if self._switch_type == SWITCH_DRY:
                restore = state.get("last_target_mode") or DEVICE_MODE_COOL
                state["mode"] = DEVICE_MODE_COOL if restore == DEVICE_MODE_DRY else restore
                if not state.get("last_power_state", False):
                    state["powerstate"] = False
                return

            if self._switch_type == SWITCH_SWING_RL and state["powerstate"]:
                state["swing_rl"] = False
                return

            if self._switch_type == SWITCH_SWING_UD and state["powerstate"]:
                state["swing_ud"] = False

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)
