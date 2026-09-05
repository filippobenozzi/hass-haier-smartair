"""Climate platform for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import clamp_temperature
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
from .coordinator import HaierDataCoordinator, as_float
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

DEVICE_TO_HVAC_MODE = {
    DEVICE_MODE_COOL: HVACMode.COOL,
    DEVICE_MODE_HEAT: HVACMode.HEAT,
    DEVICE_MODE_SMART: HVACMode.AUTO,
    DEVICE_MODE_FAN: HVACMode.FAN_ONLY,
    DEVICE_MODE_DRY: HVACMode.DRY,
}

HVAC_TO_DEVICE_MODE = {value: key for key, value in DEVICE_TO_HVAC_MODE.items()}

DEVICE_TO_HVAC_ACTION = {
    DEVICE_MODE_COOL: HVACAction.COOLING,
    DEVICE_MODE_HEAT: HVACAction.HEATING,
    DEVICE_MODE_FAN: HVACAction.FAN,
    DEVICE_MODE_DRY: getattr(HVACAction, "DRYING", HVACAction.IDLE),
}

# TURN_ON/TURN_OFF were introduced in HA 2024.2; fall back to 0 on older cores.
_FEATURE_TURN_ON = getattr(ClimateEntityFeature, "TURN_ON", 0)
_FEATURE_TURN_OFF = getattr(ClimateEntityFeature, "TURN_OFF", 0)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Haier climate entities from a config entry."""
    coordinator: HaierDataCoordinator = hass.data[DOMAIN][entry.entry_id].coordinator

    known_ids: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_ids = [
            device_id for device_id in (coordinator.data or {}) if device_id not in known_ids
        ]
        if not new_ids:
            return

        known_ids.update(new_ids)
        async_add_entities([HaierClimateEntity(coordinator, device_id) for device_id in new_ids])

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class HaierClimateEntity(HaierBaseEntity, ClimateEntity):
    """Representation of one Haier AC device as climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_fan_modes = [FAN_MODE_LOW, FAN_MODE_MEDIUM, FAN_MODE_HIGH, FAN_MODE_AUTO]
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.AUTO,
        HVACMode.FAN_ONLY,
        HVACMode.DRY,
    ]
    # Opt out of the deprecated implicit turn_on/turn_off shim; the features are
    # declared explicitly below. Harmless on cores that no longer read it.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator: HaierDataCoordinator, device_id: str) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_climate"

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | _FEATURE_TURN_ON
            | _FEATURE_TURN_OFF
        )
        if coordinator.options.get(CONF_SWING_TYPE) == SWING_TYPE_BOTH:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = [SWING_MODE_OFF, SWING_MODE_ON]
        self._attr_supported_features = features

    @property
    def name(self) -> str:
        """Return the entity name."""
        return f"{self.base_name} {self.device_label}".strip()

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the active HVAC mode."""
        device = self.device
        if not device:
            return None
        if not device["powerstate"]:
            return HVACMode.OFF
        return DEVICE_TO_HVAC_MODE.get(device["mode"], HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return what the AC is currently doing."""
        device = self.device
        if not device:
            return None
        if not device["powerstate"]:
            return HVACAction.OFF

        mode = device["mode"]
        if mode == DEVICE_MODE_SMART:
            current = as_float(device.get("temp"))
            target = as_float(device.get("tempset"))
            if current > target:
                return HVACAction.COOLING
            if current < target:
                return HVACAction.HEATING
            return HVACAction.IDLE

        return DEVICE_TO_HVAC_ACTION.get(mode, HVACAction.IDLE)

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature measured by the AC."""
        device = self.device
        return as_float(device.get("temp")) if device else None

    @property
    def target_temperature(self) -> float | None:
        """Return the configured target temperature."""
        device = self.device
        return float(clamp_temperature(device.get("tempset"), default=22)) if device else None

    @property
    def current_humidity(self) -> int | None:
        """Return the humidity, or None when the AC does not report it."""
        device = self.device
        if not device:
            return None
        humidity = int(device.get("humidity") or 0)
        return humidity if 0 < humidity <= 100 else None

    @property
    def fan_mode(self) -> str | None:
        """Return the active fan speed."""
        device = self.device
        if not device:
            return None
        return DEVICE_TO_HA_FAN_MODE.get(device["fanspeed"], FAN_MODE_HIGH)

    @property
    def swing_mode(self) -> str | None:
        """Return the combined swing state, when combined swing is enabled."""
        if self.coordinator.options.get(CONF_SWING_TYPE) != SWING_TYPE_BOTH:
            return None

        device = self.device
        if not device or not device["powerstate"]:
            return SWING_MODE_OFF

        return SWING_MODE_ON if (device["swing_ud"] and device["swing_rl"]) else SWING_MODE_OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the AC off, or on into the requested mode."""
        if hvac_mode != HVACMode.OFF and hvac_mode not in HVAC_TO_DEVICE_MODE:
            raise ServiceValidationError(f"Unsupported HVAC mode: {hvac_mode}")

        def _mutator(state: dict[str, Any]) -> None:
            if hvac_mode == HVACMode.OFF:
                state["last_power_state"] = state["powerstate"]
                state["last_target_mode"] = state["mode"]
                state["powerstate"] = False
                return

            state["powerstate"] = True
            state["mode"] = HVAC_TO_DEVICE_MODE[hvac_mode]

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_turn_on(self) -> None:
        """Turn the AC on, restoring the last used mode."""

        def _mutator(state: dict[str, Any]) -> None:
            state["powerstate"] = True
            state["mode"] = state.get("last_target_mode") or state.get("mode") or DEVICE_MODE_COOL

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_turn_off(self) -> None:
        """Turn the AC off, remembering the current mode."""

        def _mutator(state: dict[str, Any]) -> None:
            state["last_power_state"] = state["powerstate"]
            state["last_target_mode"] = state["mode"]
            state["powerstate"] = False

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        if ATTR_TARGET_TEMP_LOW in kwargs or ATTR_TARGET_TEMP_HIGH in kwargs:
            raise ServiceValidationError("Only a single target temperature is supported")

        if ATTR_TEMPERATURE not in kwargs:
            return

        requested = kwargs[ATTR_TEMPERATURE]
        try:
            float(requested)
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(f"Invalid target temperature: {requested!r}") from err

        target = clamp_temperature(requested)

        def _mutator(state: dict[str, Any]) -> None:
            state["tempset"] = target

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set a new fan speed."""
        device_fan_mode = HA_TO_DEVICE_FAN_MODE.get(fan_mode)
        if device_fan_mode is None:
            raise ServiceValidationError(f"Unsupported fan mode: {fan_mode}")

        use_fan_mode = bool(self.coordinator.options.get(CONF_USE_FAN_MODE))

        def _mutator(state: dict[str, Any]) -> None:
            if not state["powerstate"] and use_fan_mode:
                state["powerstate"] = True
                state["mode"] = DEVICE_MODE_FAN

            state["fanspeed"] = device_fan_mode
            state["last_fan_speed"] = device_fan_mode

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set both swing axes at once, when combined swing is enabled."""
        if self.coordinator.options.get(CONF_SWING_TYPE) != SWING_TYPE_BOTH:
            raise ServiceValidationError(
                "Combined swing control is disabled in the integration options"
            )

        if swing_mode not in (SWING_MODE_ON, SWING_MODE_OFF):
            raise ServiceValidationError(f"Unsupported swing mode: {swing_mode}")

        enabled = swing_mode == SWING_MODE_ON

        def _mutator(state: dict[str, Any]) -> None:
            if not state["powerstate"]:
                raise HomeAssistantError("Turn the AC on before changing the swing mode")
            state["swing_ud"] = enabled
            state["swing_rl"] = enabled

        await self.coordinator.async_apply_device_changes(self._device_id, _mutator)
