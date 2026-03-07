"""Data coordinator for Haier AC Bridge devices."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HaierBridgeApi, HaierBridgeAuthError, HaierBridgeError
from .const import (
    CONF_HEALTH_MODE_TYPE,
    CONF_POLLING,
    DEVICE_FAN_AUTO,
    DEVICE_FAN_HIGH,
    DEVICE_MODE_COOL,
    HEALTH_MODE_FORCE,
)

Mutator = Callable[[dict[str, Any]], None]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "on", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class HaierDataCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Fetch and mutate Haier AC state for all discovered devices."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: HaierBridgeApi,
        options: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name="Haier AC Bridge",
            update_interval=timedelta(seconds=int(options[CONF_POLLING])),
        )
        self.api = api
        self.options = options
        self._command_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            devices = await self.api.async_get_devices()
        except HaierBridgeAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except HaierBridgeError as err:
            raise UpdateFailed(f"Cannot fetch devices: {err}") from err

        previous = self.data or {}
        updated: dict[str, dict[str, Any]] = {}

        for item in devices:
            device_id = str(item.get("id", "")).strip()
            if not device_id:
                continue

            old_state = previous.get(device_id, {})
            try:
                raw_state = await self.api.async_get_device_data(device_id)
            except HaierBridgeError:
                stale_state = deepcopy(old_state)
                if not stale_state:
                    stale_state = self._normalize_state(item, {}, old_state)
                stale_state["online"] = False
                stale_state["temp"] = 0.0
                updated[device_id] = stale_state
                continue

            updated[device_id] = self._normalize_state(item, raw_state, old_state)

        return updated

    def _normalize_state(
        self,
        descriptor: dict[str, Any],
        raw_state: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(descriptor.get("name") or previous.get("name") or descriptor.get("id") or "AC")

        powerstate = _as_bool(raw_state.get("powerstate"), _as_bool(previous.get("powerstate"), False))
        mode = str(raw_state.get("mode") or previous.get("mode") or DEVICE_MODE_COOL).upper()
        tempset = _as_int(raw_state.get("tempset"), _as_int(previous.get("tempset"), 22))
        temp = _as_float(raw_state.get("temp"), _as_float(previous.get("temp"), 0.0))
        humidity = _as_int(raw_state.get("humidity"), _as_int(previous.get("humidity"), 0))
        fanspeed = str(raw_state.get("fanspeed") or previous.get("fanspeed") or DEVICE_FAN_HIGH).upper()
        swing_ud = _as_bool(raw_state.get("swing_ud"), _as_bool(previous.get("swing_ud"), False))
        swing_rl = _as_bool(raw_state.get("swing_rl"), _as_bool(previous.get("swing_rl"), False))
        healthmode = _as_bool(raw_state.get("healthmode"), _as_bool(previous.get("healthmode"), False))
        online = _as_bool(raw_state.get("online"), temp != 0.0)

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
            "last_power_state": _as_bool(previous.get("last_power_state"), powerstate),
            "last_target_mode": str(previous.get("last_target_mode") or mode).upper(),
            "last_fan_speed": str(previous.get("last_fan_speed") or fanspeed or DEVICE_FAN_AUTO).upper(),
        }

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        return (self.data or {}).get(device_id)

    def is_device_available(self, device_id: str) -> bool:
        device = self.get_device(device_id)
        if not device:
            return False

        # The original plugin uses current temp == 0 as an offline marker.
        if _as_float(device.get("temp"), 0.0) == 0.0:
            return False

        return _as_bool(device.get("online"), True)

    async def async_apply_device_changes(self, device_id: str, mutator: Mutator) -> None:
        async with self._command_lock:
            current = self.get_device(device_id)
            if current is None:
                raise UpdateFailed(f"Unknown device id: {device_id}")

            next_state = deepcopy(current)
            mutator(next_state)

            payload = {
                "powerstate": _as_bool(next_state.get("powerstate"), False),
                "mode": str(next_state.get("mode") or DEVICE_MODE_COOL).upper(),
                "tempset": _as_int(next_state.get("tempset"), 22),
                "fanspeed": str(next_state.get("fanspeed") or DEVICE_FAN_HIGH).upper(),
                "swing_rl": _as_bool(next_state.get("swing_rl"), False),
                "swing_ud": _as_bool(next_state.get("swing_ud"), False),
                "healthmode": True
                if self.options[CONF_HEALTH_MODE_TYPE] == HEALTH_MODE_FORCE
                else _as_bool(next_state.get("healthmode"), False),
            }

            try:
                await self.api.async_set_device_data(device_id, payload)
            except HaierBridgeError as err:
                raise UpdateFailed(f"Cannot send command: {err}") from err

            new_data = dict(self.data or {})
            new_data[device_id] = next_state
            self.async_set_updated_data(new_data)

        self.hass.async_create_task(self.async_request_refresh())
