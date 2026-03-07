"""Async API clients for Haier AC."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

from aiohttp import ClientError, ClientSession

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

_DIRECT_PORT = 56800
_DIRECT_TIMEOUT = 3.0
_DIRECT_RESPONSE_PREFIX = bytes.fromhex("00002715")

_DIRECT_MODE_TO_DEVICE: dict[int, str] = {
    0: DEVICE_MODE_SMART,
    1: DEVICE_MODE_COOL,
    2: DEVICE_MODE_HEAT,
    3: DEVICE_MODE_FAN,
    4: DEVICE_MODE_DRY,
}
_DEVICE_MODE_TO_DIRECT: dict[str, int] = {
    DEVICE_MODE_SMART: 0,
    DEVICE_MODE_COOL: 1,
    DEVICE_MODE_HEAT: 2,
    DEVICE_MODE_FAN: 3,
    DEVICE_MODE_DRY: 4,
}

_DIRECT_FAN_TO_DEVICE: dict[int, str] = {
    0: DEVICE_FAN_HIGH,
    1: DEVICE_FAN_MID,
    2: DEVICE_FAN_LOW,
    3: DEVICE_FAN_AUTO,
}
_DEVICE_FAN_TO_DIRECT: dict[str, int] = {
    DEVICE_FAN_HIGH: 0,
    DEVICE_FAN_MID: 1,
    DEVICE_FAN_LOW: 2,
    DEVICE_FAN_AUTO: 3,
}


class HaierBridgeError(Exception):
    """Base Haier bridge exception."""


class HaierBridgeAuthError(HaierBridgeError):
    """Raised when bridge authentication fails."""


class HaierApiClient(Protocol):
    """Interface shared by bridge and direct clients."""

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return discovered devices."""

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        """Return state for one device."""

    async def async_set_device_data(self, device_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        """Apply state changes for one device."""

    async def async_close(self) -> None:
        """Release resources."""


class HaierBridgeApi:
    """Thin async API wrapper around the Android Haier bridge endpoints."""

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
        """No-op for bridge client."""

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


class HaierDirectApi:
    """Direct TCP client inspired by bstuff/haier-ac-remote protocol."""

    def __init__(self, host: str, mac: str) -> None:
        self._host = host
        self._mac = _sanitize_mac(mac)
        self._device_id = self._mac
        self._device_name = f"Haier {self._mac[-4:]}"
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._lock = asyncio.Lock()
        self._rx_buffer = b""
        self._state = {
            "id": self._device_id,
            "name": self._device_name,
            "powerstate": False,
            "mode": DEVICE_MODE_FAN,
            "tempset": 21,
            "temp": 21.0,
            "humidity": 0,
            "fanspeed": DEVICE_FAN_LOW,
            "swing_ud": False,
            "swing_rl": False,
            "healthmode": False,
            "online": False,
        }

    async def async_get_devices(self) -> list[dict[str, Any]]:
        await self._async_ensure_connected()
        return [{"id": self._device_id, "name": self._device_name}]

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        if device_id != self._device_id:
            raise HaierBridgeError(f"Unknown direct device id: {device_id}")

        hello_ok = await self._async_send_and_wait(_payload_hello())
        init_ok = await self._async_send_and_wait(_payload_init())
        success = hello_ok or init_ok
        self._state["online"] = success
        if not success:
            raise HaierBridgeError("No response from direct AC device")

        return dict(self._state)

    async def async_set_device_data(self, device_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        if device_id != self._device_id:
            raise HaierBridgeError(f"Unknown direct device id: {device_id}")

        next_state = dict(self._state)
        next_state["powerstate"] = bool(values.get("powerstate", next_state["powerstate"]))
        next_state["mode"] = str(values.get("mode", next_state["mode"])).upper()
        next_state["tempset"] = max(16, min(30, int(values.get("tempset", next_state["tempset"]))))
        next_state["fanspeed"] = str(values.get("fanspeed", next_state["fanspeed"])).upper()
        next_state["swing_ud"] = bool(values.get("swing_ud", next_state["swing_ud"]))
        next_state["swing_rl"] = bool(values.get("swing_rl", next_state["swing_rl"]))
        next_state["healthmode"] = bool(values.get("healthmode", next_state["healthmode"]))

        if not next_state["powerstate"]:
            ok = await self._async_send_and_wait(_payload_off())
            if not ok:
                raise HaierBridgeError("Direct command OFF failed")
            next_state["powerstate"] = False
            next_state["online"] = True
            self._state.update(next_state)
            return {"result": "ok"}

        if not self._state["powerstate"]:
            ok = await self._async_send_and_wait(_payload_on())
            if not ok:
                raise HaierBridgeError("Direct command ON failed")

        mode_code = _DEVICE_MODE_TO_DIRECT.get(next_state["mode"], _DEVICE_MODE_TO_DIRECT[DEVICE_MODE_FAN])
        fan_code = _DEVICE_FAN_TO_DIRECT.get(next_state["fanspeed"], _DEVICE_FAN_TO_DIRECT[DEVICE_FAN_LOW])
        limits = 1 if (next_state["swing_ud"] or next_state["swing_rl"]) else 0

        ok = await self._async_send_and_wait(
            _payload_set_state(
                mode=mode_code,
                fan_speed=fan_code,
                limits=limits,
                health=next_state["healthmode"],
                target_temperature=next_state["tempset"],
            )
        )
        if not ok:
            raise HaierBridgeError("Direct command SET_STATE failed")

        next_state["online"] = True
        self._state.update(next_state)
        return {"result": "ok"}

    async def async_close(self) -> None:
        await self._async_disconnect()

    async def _async_ensure_connected(self) -> None:
        if self._reader is not None and self._writer is not None:
            return

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, _DIRECT_PORT),
                timeout=_DIRECT_TIMEOUT,
            )
        except (asyncio.TimeoutError, OSError) as err:
            self._reader = None
            self._writer = None
            raise HaierBridgeError(f"Cannot connect to direct AC at {self._host}:{_DIRECT_PORT}") from err

    async def _async_disconnect(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = None
        self._writer = None
        self._rx_buffer = b""

    async def _async_send_and_wait(self, payload: bytes) -> bool:
        async with self._lock:
            await self._async_ensure_connected()
            assert self._writer is not None

            seq = self._seq
            self._seq = (self._seq + 1) % 256
            packet = _build_packet(self._mac, seq, payload)

            try:
                self._writer.write(packet)
                await asyncio.wait_for(self._writer.drain(), timeout=_DIRECT_TIMEOUT)
            except (asyncio.TimeoutError, OSError) as err:
                await self._async_disconnect()
                raise HaierBridgeError("Failed to write direct AC command") from err

            try:
                return await self._async_wait_for_seq(seq)
            except HaierBridgeError:
                await self._async_disconnect()
                return False

    async def _async_wait_for_seq(self, seq: int) -> bool:
        frames = self._consume_frames()
        if self._apply_frames(frames, seq):
            return True

        deadline = asyncio.get_running_loop().time() + _DIRECT_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            timeout = max(0.01, deadline - asyncio.get_running_loop().time())
            assert self._reader is not None
            try:
                chunk = await asyncio.wait_for(self._reader.read(2048), timeout=timeout)
            except asyncio.TimeoutError as err:
                raise HaierBridgeError("Direct AC response timeout") from err
            except OSError as err:
                raise HaierBridgeError("Direct AC connection error while waiting response") from err

            if not chunk:
                raise HaierBridgeError("Direct AC closed the TCP connection")

            self._rx_buffer += chunk
            frames = self._consume_frames()
            if self._apply_frames(frames, seq):
                return True

        raise HaierBridgeError("Direct AC response timeout")

    def _consume_frames(self) -> list[tuple[int, bytes]]:
        frames: list[tuple[int, bytes]] = []
        buffer = self._rx_buffer
        cursor = 0

        while True:
            idx = buffer.find(_DIRECT_RESPONSE_PREFIX, cursor)
            if idx < 0:
                if len(buffer) > 3:
                    self._rx_buffer = buffer[-3:]
                else:
                    self._rx_buffer = buffer
                return frames

            minimum = idx + 8 + 16 + 16 + 16 + 16 + 8
            if len(buffer) < minimum:
                self._rx_buffer = buffer[idx:]
                return frames

            seq_len_offset = idx + 8 + 16 + 16 + 16 + 16
            frame_seq = buffer[seq_len_offset + 3]
            cmd_len = buffer[seq_len_offset + 7]
            cmd_start = seq_len_offset + 8
            cmd_end = cmd_start + cmd_len
            if len(buffer) < cmd_end:
                self._rx_buffer = buffer[idx:]
                return frames

            frames.append((frame_seq, buffer[cmd_start:cmd_end]))
            cursor = cmd_end
            if cursor >= len(buffer):
                self._rx_buffer = b""
                return frames

    def _apply_frames(self, frames: list[tuple[int, bytes]], seq: int) -> bool:
        matched = False
        for frame_seq, command in frames:
            if state := _parse_state_command(self._device_id, self._device_name, command):
                self._state.update(state)
                self._state["online"] = True
            if frame_seq == seq:
                matched = True
        return matched


def _sanitize_mac(mac: str) -> str:
    value = re.sub(r"[^0-9A-Fa-f]", "", mac).upper()
    if len(value) != 12:
        raise ValueError("MAC must contain exactly 12 hex characters")
    return value


def _build_packet(mac: str, seq: int, command_payload: bytes) -> bytes:
    mac_ascii = mac.encode("ascii") + b"\x00\x00\x00\x00"
    if len(mac_ascii) != 16:
        raise ValueError("Invalid MAC payload")

    packet = bytearray()
    packet.extend(bytes.fromhex("0000271400000000"))
    packet.extend(b"\x00" * 16)
    packet.extend(b"\x00" * 16)
    packet.extend(mac_ascii)
    packet.extend(b"\x00" * 16)
    packet.extend(bytes([0x00, 0x00, 0x00, seq & 0xFF]))
    packet.extend(bytes([0x00, 0x00, 0x00, len(command_payload) & 0xFF]))
    packet.extend(command_payload)
    return bytes(packet)


def _payload_hello() -> bytes:
    return bytes.fromhex("ffff0a000000000000014d0159")


def _payload_on() -> bytes:
    return bytes.fromhex("ffff0a000000000000014d025a")


def _payload_off() -> bytes:
    return bytes.fromhex("ffff0a000000000000014d035b")


def _payload_init() -> bytes:
    return bytes.fromhex("ffff08000000000000737b")


def _payload_set_state(
    *,
    mode: int,
    fan_speed: int,
    limits: int,
    health: bool,
    target_temperature: int,
) -> bytes:
    target = max(16, min(30, int(target_temperature)))
    payload = bytearray.fromhex("ffff22000000000000014d5f00000000000000000000")
    payload.extend([0x00, mode & 0xFF])
    payload.extend([0x00, fan_speed & 0xFF])
    payload.extend([0x00, limits & 0xFF])
    payload.extend([0x00, 0x09 if health else 0x01])  # Keep parity with bstuff implementation.
    payload.extend([0x00, 0x01 if health else 0x00])
    payload.extend([0x00, 0x00, 0x00, max(0, target - 16) & 0xFF])
    payload.append(_payload_checksum(payload))
    return bytes(payload)


def _payload_checksum(payload: bytes) -> int:
    hex_digits = payload.hex()
    total = 0
    for index, digit in enumerate(hex_digits):
        value = int(digit, 16)
        total += value * (16 if index % 2 == 0 else 1)
    return (total - (2 * 255)) & 0xFF


def _parse_state_command(device_id: str, device_name: str, command: bytes) -> dict[str, Any] | None:
    if len(command) < 37:
        return None
    if command[0:2] != b"\xFF\xFF":
        return None
    if command[2] != 0x22:
        return None

    def _u16(offset: int) -> int:
        return int.from_bytes(command[offset : offset + 2], "big")

    current_temp = _u16(12)
    mode_raw = _u16(22)
    fan_raw = _u16(24)
    limits_raw = _u16(26)
    power_raw = _u16(28)
    health_raw = _u16(30)
    target_raw = _u16(34)

    return {
        "id": device_id,
        "name": device_name,
        "powerstate": bool(power_raw % 2),
        "mode": _DIRECT_MODE_TO_DEVICE.get(mode_raw, DEVICE_MODE_FAN),
        "tempset": max(16, min(30, target_raw + 16)),
        "temp": float(current_temp),
        "humidity": 0,
        "fanspeed": _DIRECT_FAN_TO_DEVICE.get(fan_raw, DEVICE_FAN_LOW),
        "swing_ud": limits_raw == 1,
        "swing_rl": limits_raw == 1,
        "healthmode": bool(health_raw % 2),
        "online": True,
    }
