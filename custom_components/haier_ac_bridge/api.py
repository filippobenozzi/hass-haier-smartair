"""Async API clients for Haier AC integration."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import types
from typing import Any, Protocol

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant

from .const import (
    DEVICE_FAN_AUTO,
    DEVICE_FAN_HIGH,
    DEVICE_FAN_LOW,
    DEVICE_FAN_MID,
    DEVICE_MODE_COOL,
    DEVICE_MODE_DRY,
    DEVICE_MODE_FAN,
    DEVICE_MODE_HEAT,
    DEVICE_MODE_SMART,
)

_LOGGER = logging.getLogger(__name__)

CLOUD_TO_DEVICE_MODE = {
    0: DEVICE_MODE_SMART,
    1: DEVICE_MODE_COOL,
    2: DEVICE_MODE_COOL,
    3: DEVICE_MODE_DRY,
    4: DEVICE_MODE_HEAT,
    5: DEVICE_MODE_FAN,
    6: DEVICE_MODE_FAN,
}

DEVICE_TO_CLOUD_MODE = {
    DEVICE_MODE_SMART: "0",
    DEVICE_MODE_COOL: "1",
    DEVICE_MODE_DRY: "3",
    DEVICE_MODE_HEAT: "4",
    DEVICE_MODE_FAN: "5",
}

CLOUD_TO_DEVICE_FAN = {
    1: DEVICE_FAN_HIGH,
    2: DEVICE_FAN_MID,
    3: DEVICE_FAN_LOW,
    4: DEVICE_FAN_AUTO,
    5: DEVICE_FAN_AUTO,
}

DEVICE_TO_CLOUD_FAN = {
    DEVICE_FAN_HIGH: "1",
    DEVICE_FAN_MID: "2",
    DEVICE_FAN_LOW: "3",
    DEVICE_FAN_AUTO: "4",
}

HEALTH_SETTING_KEYS = (
    "settings.healthMode",
    "settings.healthmode",
    "settings.selfCleaning56DegreeStatus",
    "settings.selfCleanStatus",
    "settings.selfCleaningStatus",
)

HEALTH_STATE_KEYS = (
    "healthMode",
    "healthmode",
    "selfCleaning56DegreeStatus",
    "selfCleanStatus",
    "selfCleaningStatus",
)


class HaierBridgeError(Exception):
    """Base Haier bridge exception."""


class HaierBridgeAuthError(HaierBridgeError):
    """Raised when bridge authentication fails."""


class HaierBridgePasswordChangeRequired(HaierBridgeAuthError):
    """Raised when hOn forces a password update before API login."""


class HaierApiClient(Protocol):
    """Interface shared by bridge and cloud API clients."""

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return discovered devices."""

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        """Return state for one device."""

    async def async_set_device_data(self, device_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        """Apply state changes for one device."""

    async def async_close(self) -> None:
        """Release resources."""


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "on", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class HaierBridgeApi:
    """Thin async API wrapper around Android Haier bridge endpoints."""

    def __init__(self, host: str, token: str, session: ClientSession) -> None:
        self._base_url = f"http://{host}:10000"
        self._token = token
        self._session = session

    async def async_get_devices(self) -> list[dict[str, Any]]:
        payload = await self._async_request("GET", "/listDevices")
        if not isinstance(payload, list):
            raise HaierBridgeError("Unexpected /listDevices payload")
        return payload

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        payload = await self._async_request("GET", "/deviceData", headers={"id": device_id})
        if not isinstance(payload, dict):
            raise HaierBridgeError("Unexpected /deviceData payload")
        return payload

    async def async_set_device_data(self, device_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        payload = await self._async_request(
            "POST",
            "/deviceData",
            headers={"id": device_id, "Content-Type": "application/json"},
            json_body=values,
        )
        if payload is None or isinstance(payload, dict):
            return payload
        raise HaierBridgeError("Unexpected response while setting device data")

    async def async_close(self) -> None:
        """No-op for local bridge client."""

    async def _async_request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        request_headers = {"token": self._token}
        if headers:
            request_headers.update(headers)

        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=request_headers,
                json=json_body,
                timeout=10,
            ) as response:
                raw_text = (await response.text()).strip()
        except (ClientError, asyncio.TimeoutError) as err:
            raise HaierBridgeError(str(err)) from err

        if raw_text in {"Wrong token", "Wrong Token", "Missing token header"}:
            raise HaierBridgeAuthError(raw_text)

        if not raw_text:
            return None

        try:
            return json.loads(raw_text)
        except Exception as err:  # noqa: BLE001
            raise HaierBridgeError(f"Invalid JSON payload: {raw_text}") from err


class HaierCloudApi:
    """Direct cloud backend using pyhOn (no Android bridge app required)."""

    def __init__(
        self,
        hass: HomeAssistant,
        email: str,
        password: str,
        session: ClientSession,
    ) -> None:
        self._hass = hass
        self._email = email
        self._password = password
        self._session = session
        self._hon: Any | None = None
        self._devices: dict[str, Any] = {}
        self._connect_lock = asyncio.Lock()

    async def async_get_devices(self) -> list[dict[str, Any]]:
        await self.async_connect()
        await self._async_refresh_devices()
        return [
            {
                "id": device_id,
                "name": self._device_name(appliance),
            }
            for device_id, appliance in self._devices.items()
        ]

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        appliance = await self._async_get_device(device_id)
        try:
            await appliance.update(force=True)
        except Exception as err:  # noqa: BLE001
            raise HaierBridgeError(f"Cloud update failed: {err}") from err
        return self._normalize_cloud_state(device_id, appliance)

    async def async_set_device_data(self, device_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        appliance = await self._async_get_device(device_id)
        commands = appliance.commands
        settings_command = commands.get("settings")
        if settings_command is None:
            raise HaierBridgeError("This AC model does not expose a settings command")

        powerstate = _as_bool(values.get("powerstate"), False)

        if not powerstate and (stop_program := commands.get("stopProgram")) is not None:
            if not await stop_program.send():
                raise HaierBridgeError("Cloud command stopProgram failed")
            appliance.sync_command("stopProgram", "settings")
            self._set_setting(appliance, "settings.onOffStatus", "0")
            return {"result": "ok"}

        self._set_setting(appliance, "settings.onOffStatus", "1" if powerstate else "0")

        if powerstate:
            mode = str(values.get("mode") or DEVICE_MODE_COOL).upper()
            cloud_mode = DEVICE_TO_CLOUD_MODE.get(mode)
            if cloud_mode is not None:
                self._set_setting(appliance, "settings.machMode", cloud_mode)

            self._set_setting(appliance, "settings.tempSel", _as_int(values.get("tempset"), 22))

            fan_speed = str(values.get("fanspeed") or DEVICE_FAN_HIGH).upper()
            cloud_fan = DEVICE_TO_CLOUD_FAN.get(fan_speed)
            if cloud_fan is not None:
                self._set_setting(appliance, "settings.windSpeed", cloud_fan)

            swing_rl = _as_bool(values.get("swing_rl"), False)
            swing_ud = _as_bool(values.get("swing_ud"), False)
            self._set_setting(appliance, "settings.windDirectionHorizontal", "7" if swing_rl else "0")
            self._set_setting(appliance, "settings.windDirectionVertical", "8" if swing_ud else "5")

            health_mode = "1" if _as_bool(values.get("healthmode"), False) else "0"
            for setting_key in HEALTH_SETTING_KEYS:
                if self._set_setting(appliance, setting_key, health_mode):
                    break

        if not await settings_command.send():
            raise HaierBridgeError("Cloud command settings failed")
        appliance.sync_command("settings")
        return {"result": "ok"}

    async def async_close(self) -> None:
        if self._hon is None:
            return
        try:
            await self._hon.close()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to close pyhOn session cleanly: %s", err)
        finally:
            self._hon = None
            self._devices = {}

    async def async_connect(self) -> None:
        async with self._connect_lock:
            if self._hon is not None:
                return

            await self._hass.async_add_executor_job(self._prepare_pyhon_imports)
            try:
                from pyhon import Hon
                from pyhon import exceptions as pyhon_exceptions

                hon = Hon(
                    email=self._email,
                    password=self._password,
                    session=self._session,
                    mobile_id="haier_ac_bridge",
                )
                self._hon = await hon.create()
            except Exception as err:  # noqa: BLE001
                self._hon = None
                message = str(err)
                lowered = message.lower()
                if "changepassword" in lowered or "change password" in lowered:
                    raise HaierBridgePasswordChangeRequired(
                        "hOn requires a password change before cloud login can continue"
                    ) from err
                if "pyhon_exceptions" in locals() and isinstance(
                    err,
                    (
                        pyhon_exceptions.HonAuthenticationError,
                        pyhon_exceptions.NoAuthenticationException,
                    ),
                ):
                    raise HaierBridgeAuthError("Cloud authentication failed") from err
                if "auth" in lowered or "password" in lowered or "login" in lowered:
                    raise HaierBridgeAuthError("Cloud authentication failed") from err
                raise HaierBridgeError(f"Cloud initialization failed: {err}") from err

            await self._async_refresh_devices()

    async def _async_get_device(self, device_id: str) -> Any:
        await self.async_connect()

        appliance = self._devices.get(device_id)
        if appliance is not None:
            return appliance

        await self._async_refresh_devices()
        appliance = self._devices.get(device_id)
        if appliance is None:
            raise HaierBridgeError(f"Unknown cloud device id: {device_id}")
        return appliance

    async def _async_refresh_devices(self) -> None:
        if self._hon is None:
            raise HaierBridgeError("Cloud backend not initialized")

        devices: dict[str, Any] = {}
        for appliance in self._hon.appliances:
            if str(appliance.appliance_type).upper() != "AC":
                continue
            device_id = str(appliance.mac_address or appliance.unique_id or "").strip()
            if not device_id:
                continue
            devices[device_id] = appliance
        self._devices = devices

    def _prepare_pyhon_imports(self) -> None:
        """Preload pyhOn modules and install an AC stub to reduce loop blocking warnings."""
        importlib.import_module("pyhon")
        importlib.import_module("pyhon.appliance")

        # pyhOn has no dedicated ac.py appliance module; avoid repeated failed imports.
        if importlib.util.find_spec("pyhon.appliances.ac") is None and "pyhon.appliances.ac" not in sys.modules:
            module = types.ModuleType("pyhon.appliances.ac")

            class Appliance:  # pylint: disable=too-few-public-methods
                def __init__(self, _appliance: Any) -> None:
                    self._appliance = _appliance

                def attributes(self, values: dict[str, Any]) -> dict[str, Any]:
                    return values

                def settings(self, values: dict[str, Any]) -> dict[str, Any]:
                    return values

            module.Appliance = Appliance
            sys.modules["pyhon.appliances.ac"] = module

    @staticmethod
    def _set_setting(appliance: Any, key: str, value: Any) -> bool:
        setting = appliance.settings.get(key)
        if setting is None:
            return False
        try:
            setting.value = str(value)
        except Exception:  # noqa: BLE001
            return False
        return True

    @staticmethod
    def _device_name(appliance: Any) -> str:
        return str(appliance.nick_name or appliance.model_name or appliance.mac_address or "AC")

    @staticmethod
    def _normalize_cloud_state(device_id: str, appliance: Any) -> dict[str, Any]:
        mode = CLOUD_TO_DEVICE_MODE.get(_as_int(appliance.get("machMode"), 1), DEVICE_MODE_COOL)
        fanspeed = CLOUD_TO_DEVICE_FAN.get(_as_int(appliance.get("windSpeed"), 1), DEVICE_FAN_HIGH)
        swing_ud = _as_int(appliance.get("windDirectionVertical"), 5) == 8
        swing_rl = _as_int(appliance.get("windDirectionHorizontal"), 0) == 7
        health_mode = False
        for health_key in HEALTH_STATE_KEYS:
            if (value := appliance.get(health_key)) is not None:
                health_mode = _as_bool(value, False)
                break

        temp = _as_float(appliance.get("tempIndoor"), _as_float(appliance.get("tempSel"), 0.0))
        humidity = _as_int(appliance.get("humidityIndoor"), _as_int(appliance.get("humidity"), 0))

        return {
            "id": device_id,
            "name": HaierCloudApi._device_name(appliance),
            "powerstate": _as_bool(appliance.get("onOffStatus"), False),
            "mode": mode,
            "tempset": _as_int(appliance.get("tempSel"), 22),
            "temp": temp,
            "humidity": humidity,
            "fanspeed": fanspeed,
            "swing_ud": swing_ud,
            "swing_rl": swing_rl,
            "healthmode": health_mode,
            "online": bool(appliance.connection),
        }
