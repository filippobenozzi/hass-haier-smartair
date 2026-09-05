"""Data coordinator for Haier AC Bridge devices."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta
import inspect
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HaierApiClient, HaierBridgeAuthError, HaierBridgeError, clamp_temperature
from .const import (
    CONF_HEALTH_MODE_TYPE,
    CONF_POLLING,
    DEFAULT_POLLING_SECONDS,
    DEVICE_FAN_AUTO,
    DEVICE_FAN_HIGH,
    DEVICE_FAN_SPEEDS,
    DEVICE_MODE_COOL,
    DEVICE_MODES,
    HEALTH_MODE_FORCE,
    MAX_POLLING_SECONDS,
    MIN_POLLING_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

Mutator = Callable[[dict[str, Any]], None]

# HA started accepting (and later expecting) the owning config entry in the
# coordinator constructor; detect it once so we stay compatible either way.
_COORDINATOR_ACCEPTS_ENTRY = (
    "config_entry" in inspect.signature(DataUpdateCoordinator.__init__).parameters
)


def as_bool(value: Any, default: bool = False) -> bool:
    """Coerce a bridge supplied value to bool without raising."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "on", "yes"}:
            return True
        if normalized in {"0", "false", "off", "no"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def as_float(value: Any, default: float = 0.0) -> float:
    """Coerce a bridge supplied value to float without raising."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # Guard against NaN/inf leaking into entity state.
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def as_int(value: Any, default: int = 0) -> int:
    """Coerce a bridge supplied value to int without raising."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def as_enum(value: Any, allowed: list[str], default: str) -> str:
    """Return an uppercase value constrained to the known protocol vocabulary."""
    if value is None:
        return default
    candidate = str(value).strip().upper()
    return candidate if candidate in allowed else default


def resolve_polling_interval(options: dict[str, Any]) -> int:
    """Return a sane polling interval, whatever is stored in the options."""
    seconds = as_int(options.get(CONF_POLLING), DEFAULT_POLLING_SECONDS)
    return max(MIN_POLLING_SECONDS, min(MAX_POLLING_SECONDS, seconds))


class HaierDataCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch and mutate Haier AC state for all discovered devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: HaierApiClient,
        options: dict[str, Any],
        entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        kwargs: dict[str, Any] = {
            "logger": _LOGGER,
            "name": "Haier AC Bridge",
            "update_interval": timedelta(seconds=resolve_polling_interval(options)),
        }
        if _COORDINATOR_ACCEPTS_ENTRY:
            kwargs["config_entry"] = entry

        super().__init__(hass, **kwargs)
        self.api = api
        self.options = options
        self._command_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
        except HaierBridgeAuthError as err:
            # Surfaces a "Reconfigure"/"Reauthenticate" prompt in the UI instead
            # of silently retrying with a token that will never be accepted.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except HaierBridgeError as err:
            raise UpdateFailed(f"Cannot fetch devices: {err}") from err

        previous = self.data or {}
        updated: dict[str, dict[str, Any]] = {}
        failures = 0

        for item in devices:
            device_id = str(item.get("id", "")).strip()
            if not device_id:
                _LOGGER.debug("Skipping device without id: %s", item)
                continue

            old_state = previous.get(device_id, {})
            try:
                raw_state = await self.api.async_get_device_data(device_id)
            except HaierBridgeAuthError as err:
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            except HaierBridgeError as err:
                _LOGGER.debug("Device %s is unreachable: %s", device_id, err)
                failures += 1
                updated[device_id] = self._offline_state(item, old_state)
                continue

            updated[device_id] = self._normalize_state(item, raw_state, old_state)

        if not updated:
            raise UpdateFailed("The bridge did not report any usable device")

        if failures == len(updated):
            raise UpdateFailed(f"All {failures} device(s) failed to answer")

        return updated

    def _offline_state(
        self, descriptor: dict[str, Any], previous: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the last known state flagged as offline."""
        state = deepcopy(previous) if previous else self._normalize_state(descriptor, {}, {})
        state["online"] = False
        return state

    def _normalize_state(
        self,
        descriptor: dict[str, Any],
        raw_state: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a fully typed device state out of an untrusted payload."""
        name = str(descriptor.get("name") or previous.get("name") or descriptor.get("id") or "AC")

        powerstate = as_bool(
            raw_state.get("powerstate"), as_bool(previous.get("powerstate"), False)
        )
        mode = as_enum(
            raw_state.get("mode"),
            DEVICE_MODES,
            as_enum(previous.get("mode"), DEVICE_MODES, DEVICE_MODE_COOL),
        )
        tempset = clamp_temperature(
            raw_state.get("tempset", previous.get("tempset", 22)), default=22
        )
        temp = as_float(raw_state.get("temp"), as_float(previous.get("temp"), 0.0))
        humidity = as_int(raw_state.get("humidity"), as_int(previous.get("humidity"), 0))
        fanspeed = as_enum(
            raw_state.get("fanspeed"),
            DEVICE_FAN_SPEEDS,
            as_enum(previous.get("fanspeed"), DEVICE_FAN_SPEEDS, DEVICE_FAN_HIGH),
        )
        swing_ud = as_bool(raw_state.get("swing_ud"), as_bool(previous.get("swing_ud"), False))
        swing_rl = as_bool(raw_state.get("swing_rl"), as_bool(previous.get("swing_rl"), False))
        healthmode = as_bool(
            raw_state.get("healthmode"), as_bool(previous.get("healthmode"), False)
        )

        # Legacy bridge builds omit the flag and report 0 degrees instead when
        # the AC cannot be reached.
        online = as_bool(raw_state.get("online"), False) if "online" in raw_state else temp != 0.0

        return {
            "id": str(descriptor.get("id") or previous.get("id") or ""),
            "name": name,
            "powerstate": powerstate,
            "mode": mode,
            "tempset": tempset,
            "temp": temp,
            "humidity": humidity,
            "fanspeed": fanspeed,
            "swing_ud": swing_ud,
            "swing_rl": swing_rl,
            "healthmode": healthmode,
            "online": online,
            "last_power_state": as_bool(previous.get("last_power_state"), powerstate),
            "last_target_mode": as_enum(previous.get("last_target_mode"), DEVICE_MODES, mode),
            "last_fan_speed": as_enum(
                previous.get("last_fan_speed"), DEVICE_FAN_SPEEDS, fanspeed or DEVICE_FAN_AUTO
            ),
        }

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Return the cached state of one device, if known."""
        return (self.data or {}).get(device_id)

    def is_device_available(self, device_id: str) -> bool:
        """Return whether the device answered on the last successful poll."""
        device = self.get_device(device_id)
        if not device:
            return False
        return as_bool(device.get("online"), False)

    async def async_apply_device_changes(self, device_id: str, mutator: Mutator) -> None:
        """Serialize a read-modify-write cycle against one device."""
        async with self._command_lock:
            current = self.get_device(device_id)
            if current is None:
                raise HomeAssistantError(f"Unknown device id: {device_id}")

            next_state = deepcopy(current)
            mutator(next_state)

            payload = {
                "powerstate": as_bool(next_state.get("powerstate"), False),
                "mode": as_enum(next_state.get("mode"), DEVICE_MODES, DEVICE_MODE_COOL),
                "tempset": clamp_temperature(next_state.get("tempset"), default=22),
                "fanspeed": as_enum(next_state.get("fanspeed"), DEVICE_FAN_SPEEDS, DEVICE_FAN_HIGH),
                "swing_rl": as_bool(next_state.get("swing_rl"), False),
                "swing_ud": as_bool(next_state.get("swing_ud"), False),
                "healthmode": True
                if self.options.get(CONF_HEALTH_MODE_TYPE) == HEALTH_MODE_FORCE
                else as_bool(next_state.get("healthmode"), False),
            }

            try:
                await self.api.async_set_device_data(device_id, payload)
            except HaierBridgeAuthError as err:
                raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
            except HaierBridgeError as err:
                raise HomeAssistantError(f"Cannot send command to {device_id}: {err}") from err

            # Keep the optimistic state aligned with what actually went out.
            next_state.update(payload)
            next_state["online"] = True

            new_data = dict(self.data or {})
            new_data[device_id] = next_state
            self.async_set_updated_data(new_data)

        # Confirm the optimistic state with a real read, outside the lock.
        await self.async_request_refresh()
