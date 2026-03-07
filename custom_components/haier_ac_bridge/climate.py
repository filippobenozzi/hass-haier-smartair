"""Climate platform for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TEMPERATURE,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_SWING_TYPE,
    CONF_USE_FAN_MODE,
    DEVICE_FAN_AUTO,
    DEVICE_FAN_HIGH,
    DEVICE_FAN_LOW,
    DEVICE_FAN_MID,
    DEVICE_MODE_COOL,
    DEVICE_MODE_DRY,
    DEVICE_MODE_FAN,
    DEVICE_MODE_HEAT,
    DEVICE_MODE_SMART,
    DOMAIN,
    MAX_TEMP,
    MIN_TEMP,
    SWING_TYPE_BOTH,
)
from .entity import HaierBaseEntity

FAN_MODE_LOW = "low"
FAN_MODE_MEDIUM = "medium"
FAN_MODE_HIGH = "high"
FAN_MODE_AUTO = "auto"
SWING_MODE_OFF = "off"
SWING_MODE_ON = "on"

DEVICE_TO_HA_FAN_MODE = {
    DEVICE_FAN_LOW: FAN_MODE_LOW,
    DEVICE_FAN_MID: FAN_MODE_MEDIUM,
    DEVICE_FAN_HIGH: FAN_MODE_HIGH,
    DEVICE_FAN_AUTO: FAN_MODE_AUTO,
}

HA_TO_DEVICE_FAN_MODE = {value: key for key, value in DEVICE_TO_HA_FAN_MODE.items()}

HVAC_DRY_ACTION = getattr(HVACAction, "DRYING", HVACAction.IDLE)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haier climate entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator

    known_ids: set[str] = set()

    def _async_add_new_entities() -> None:
        new_ids = [device_id for device_id in (coordinator.data or {}) if device_id not in known_ids]
        if not new_ids:
            return

        async_add_entities([HaierClimateEntity(coordinator, device_id) for device_id in new_ids])
        known_ids.update(new_ids)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class HaierClimateEntity(HaierBaseEntity, ClimateEntity):
    """Representation of one Haier AC device as climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_climate"

        features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
        if coordinator.options[CONF_SWING_TYPE] == SWING_TYPE_BOTH:
            features |= ClimateEntityFeature.SWING_MODE
        self._attr_supported_features = features

    @property
    def name(self) -> str:
        device = self.device
        base_name = self.coordinator.options["acdevice_name"]
        return f"{base_name} {device['name']}" if device else f"{base_name} {self._device_id}"

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [
            HVACMode.OFF,
            HVACMode.COOL,
            HVACMode.HEAT,
            HVACMode.AUTO,
            HVACMode.FAN_ONLY,
            HVACMode.DRY,
        ]

    @property
    def hvac_mode(self) -> HVACMode | None:
        device = self.device
        if not device:
            return None

        if not device["powerstate"]:
            return HVACMode.OFF

        mode = device["mode"]
        if mode == DEVICE_MODE_COOL:
            return HVACMode.COOL
        if mode == DEVICE_MODE_HEAT:
            return HVACMode.HEAT
        if mode == DEVICE_MODE_SMART:
            return HVACMode.AUTO
        if mode == DEVICE_MODE_FAN:
            return HVACMode.FAN_ONLY
        if mode == DEVICE_MODE_DRY:
            return HVACMode.DRY

        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        device = self.device
        if not device:
            return None

        if not device["powerstate"]:
            return HVACAction.OFF

        mode = device["mode"]
        if mode == DEVICE_MODE_COOL:
            return HVACAction.COOLING
        if mode == DEVICE_MODE_HEAT:
            return HVACAction.HEATING
        if mode == DEVICE_MODE_FAN:
            return HVACAction.FAN
        if mode == DEVICE_MODE_DRY:
            return HVAC_DRY_ACTION
        if mode == DEVICE_MODE_SMART:
            current = float(device["temp"])
            target = float(device["tempset"])
            if current > target:
                return HVACAction.COOLING
            if current < target:
                return HVACAction.HEATING
            return HVACAction.IDLE

        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        device = self.device
        return float(device["temp"]) if device else None

    @property
    def target_temperature(self) -> float | None:
        device = self.device
        return float(device["tempset"]) if device else None

    @property
    def current_humidity(self) -> int | None:
        device = self.device
        return int(device["humidity"]) if device else None

    @property
    def fan_modes(self) -> list[str]:
        return [FAN_MODE_LOW, FAN_MODE_MEDIUM, FAN_MODE_HIGH, FAN_MODE_AUTO]

    @property
    def fan_mode(self) -> str | None:
        device = self.device
        if not device:
            return None
        return DEVICE_TO_HA_FAN_MODE.get(device["fanspeed"], FAN_MODE_HIGH)

    @property
    def swing_modes(self) -> list[str] | None:
        if self.coordinator.options[CONF_SWING_TYPE] != SWING_TYPE_BOTH:
            return None
        return [SWING_MODE_OFF, SWING_MODE_ON]

    @property
    def swing_mode(self) -> str | None:
        if self.coordinator.options[CONF_SWING_TYPE] != SWING_TYPE_BOTH:
            return None

        device = self.device
        if not device or not device["powerstate"]:
            return SWING_MODE_OFF

        return SWING_MODE_ON if (device["swing_ud"] and device["swing_rl"]) else SWING_MODE_OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        def _mutator(state: dict[str, Any]) -> None:
            if hvac_mode == HVACMode.OFF:
                state["powerstate"] = False
                return

            state["powerstate"] = True

            if hvac_mode == HVACMode.COOL:
                state["mode"] = DEVICE_MODE_COOL
            elif hvac_mode == HVACMode.HEAT:
                state["mode"] = DEVICE_MODE_HEAT
            elif hvac_mode == HVACMode.AUTO:
                state["mode"] = DEVICE_MODE_SMART
            elif hvac_mode == HVACMode.FAN_ONLY:
                state["mode"] = DEVICE_MODE_FAN
            elif hvac_mode == HVACMode.DRY:
                state["mode"] = DEVICE_MODE_DRY
            else:
                raise HomeAssistantError(f"Unsupported mode: {hvac_mode}")

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if ATTR_TARGET_TEMP_LOW in kwargs or ATTR_TARGET_TEMP_HIGH in kwargs:
            raise HomeAssistantError("Only single target temperature is supported")

        if ATTR_TEMPERATURE not in kwargs:
            return

        target = int(kwargs[ATTR_TEMPERATURE])

        def _mutator(state: dict[str, Any]) -> None:
            state["tempset"] = max(MIN_TEMP, min(MAX_TEMP, target))

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        device_fan_mode = HA_TO_DEVICE_FAN_MODE.get(fan_mode)
        if device_fan_mode is None:
            raise HomeAssistantError(f"Unsupported fan mode: {fan_mode}")

        def _mutator(state: dict[str, Any]) -> None:
            if not state["powerstate"] and self.coordinator.options[CONF_USE_FAN_MODE]:
                state["powerstate"] = True
                state["mode"] = DEVICE_MODE_FAN

            state["fanspeed"] = device_fan_mode

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if self.coordinator.options[CONF_SWING_TYPE] != SWING_TYPE_BOTH:
            return

        if swing_mode not in {SWING_MODE_ON, SWING_MODE_OFF}:
            raise HomeAssistantError(f"Unsupported swing mode: {swing_mode}")

        enabled = swing_mode == SWING_MODE_ON

        def _mutator(state: dict[str, Any]) -> None:
            if state["powerstate"]:
                state["swing_ud"] = enabled
                state["swing_rl"] = enabled

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)
