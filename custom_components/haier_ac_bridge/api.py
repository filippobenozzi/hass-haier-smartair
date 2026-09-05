"""Async API clients for Haier AC."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol

import aiohttp
from aiohttp import ClientError, ClientSession

from .const import (
    BRIDGE_PORT,
    BRIDGE_TIMEOUT,
    DEVICE_FAN_AUTO,
    DEVICE_FAN_HIGH,
    DEVICE_FAN_LOW,
    DEVICE_FAN_MID,
    DEVICE_MODE_COOL,
    DEVICE_MODE_DRY,
    DEVICE_MODE_FAN,
    DEVICE_MODE_HEAT,
    DEVICE_MODE_SMART,
    DIRECT_PORT,
    DIRECT_TIMEOUT,
    MAX_TEMP,
    MIN_TEMP,
)

_LOGGER = logging.getLogger(__name__)

_DIRECT_RESPONSE_PREFIX = bytes.fromhex("00002715")
_DIRECT_HEADER_LEN = 8 + 16 + 16 + 16 + 16 + 8
_DIRECT_SEQ_LEN_OFFSET = 8 + 16 + 16 + 16 + 16
# Hard cap on the receive buffer so a malformed length byte can never make the
# integration grow memory without bound.
_DIRECT_MAX_RX_BUFFER = 64 * 1024

# Responses the bridge app returns as plain text instead of JSON when the token
# is not accepted. Matched case-insensitively.
_BRIDGE_AUTH_RESPONSES = frozenset({"wrong token", "missing token header"})

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

    async def async_set_device_data(
        self, device_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply state changes for one device."""

    async def async_close(self) -> None:
        """Release resources."""


def sanitize_host(host: str) -> str:
    """Normalize a user supplied host, stripping scheme, port and whitespace."""
    value = str(host).strip()
    if not value:
        raise ValueError("Host must not be empty")

    value = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", value)
    value = value.strip("/")
    # Drop an explicit port; both transports use a fixed one.
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    if not value:
        raise ValueError("Host must not be empty")
    return value


def sanitize_mac(mac: str) -> str:
    """Return the 12 uppercase hex characters of a MAC address."""
    value = re.sub(r"[^0-9A-Fa-f]", "", str(mac)).upper()
    if len(value) != 12:
        raise ValueError("MAC must contain exactly 12 hex characters")
    return value


def clamp_temperature(value: Any, default: int = MIN_TEMP) -> int:
    """Clamp any user or device supplied temperature into the supported range."""
    try:
        target = int(round(float(value)))
    except (TypeError, ValueError):
        target = default
    return max(MIN_TEMP, min(MAX_TEMP, target))


class HaierBridgeApi:
    """Thin async API wrapper around the Android Haier bridge endpoints."""

    def __init__(self, host: str, token: str, session: ClientSession) -> None:
        """Initialize the bridge client."""
        self._host = sanitize_host(host)
        self._base_url = f"http://{self._host}:{BRIDGE_PORT}"
        self._token = str(token)
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT)

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return the devices exposed by the bridge."""
        payload = await self._async_request("GET", "/listDevices")
        if not isinstance(payload, list):
            raise HaierBridgeError(f"Unexpected /listDevices payload: {type(payload).__name__}")
        return [item for item in payload if isinstance(item, dict)]

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        """Return the current state of a single device."""
        payload = await self._async_request("GET", "/deviceData", headers={"id": device_id})
        if not isinstance(payload, dict):
            raise HaierBridgeError(f"Unexpected /deviceData payload: {type(payload).__name__}")
        return payload

    async def async_set_device_data(
        self, device_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Push a full state payload to a single device."""
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
        """No-op: the aiohttp session is owned by Home Assistant."""

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
                timeout=self._timeout,
            ) as response:
                status = response.status
                raw_text = (await response.text()).strip()
        except TimeoutError as err:
            raise HaierBridgeError(f"Timeout talking to bridge at {self._host}") from err
        except ClientError as err:
            raise HaierBridgeError(f"Bridge request failed: {err}") from err

        if status in (401, 403):
            raise HaierBridgeAuthError(f"Bridge rejected the token (HTTP {status})")

        if raw_text.lower() in _BRIDGE_AUTH_RESPONSES:
            raise HaierBridgeAuthError(raw_text)

        if status >= 400:
            raise HaierBridgeError(f"Bridge returned HTTP {status} for {path}")

        if not raw_text:
            return None

        try:
            return json.loads(raw_text)
        except ValueError as err:
            raise HaierBridgeError(f"Invalid JSON payload from {path}: {raw_text[:200]}") from err


class HaierDirectApi:
    """Direct TCP client inspired by the bstuff/haier-ac-remote protocol."""

    def __init__(self, host: str, mac: str) -> None:
        """Initialize the direct LAN client."""
        self._host = sanitize_host(host)
        self._mac = sanitize_mac(mac)
        self._device_id = self._mac
        self._device_name = f"Haier {self._mac[-4:]}"
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._seq = 0
        self._io_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._rx_buffer = b""
        self._state: dict[str, Any] = {
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
        """Return the single device reachable over this connection."""
        async with self._io_lock:
            await self._async_ensure_connected()
        return [{"id": self._device_id, "name": self._device_name}]

    async def async_get_device_data(self, device_id: str) -> dict[str, Any]:
        """Poll the AC and return its decoded state."""
        self._check_device(device_id)

        async with self._command_lock:
            errors: list[str] = []
            success = False
            for payload in (_payload_hello(), _payload_init()):
                try:
                    if await self._async_send_and_wait(payload):
                        success = True
                except HaierBridgeError as err:
                    errors.append(str(err))

            self._state["online"] = success
            if not success:
                detail = "; ".join(errors) if errors else "no matching response"
                raise HaierBridgeError(f"No response from direct AC device ({detail})")

            return dict(self._state)

    async def async_set_device_data(
        self, device_id: str, values: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Send a full state to the AC."""
        self._check_device(device_id)

        async with self._command_lock:
            current = self._state
            powerstate = bool(values.get("powerstate", current["powerstate"]))

            if not powerstate:
                if not await self._async_send_and_wait(_payload_off()):
                    raise HaierBridgeError("Direct command OFF failed")
                self._state["powerstate"] = False
                self._state["online"] = True
                return {"result": "ok"}

            mode = str(values.get("mode", current["mode"])).upper()
            fanspeed = str(values.get("fanspeed", current["fanspeed"])).upper()
            tempset = clamp_temperature(values.get("tempset", current["tempset"]))
            swing_ud = bool(values.get("swing_ud", current["swing_ud"]))
            swing_rl = bool(values.get("swing_rl", current["swing_rl"]))
            healthmode = bool(values.get("healthmode", current["healthmode"]))

            if not current["powerstate"] and not await self._async_send_and_wait(_payload_on()):
                raise HaierBridgeError("Direct command ON failed")

            mode_code = _DEVICE_MODE_TO_DIRECT.get(mode, _DEVICE_MODE_TO_DIRECT[DEVICE_MODE_FAN])
            fan_code = _DEVICE_FAN_TO_DIRECT.get(fanspeed, _DEVICE_FAN_TO_DIRECT[DEVICE_FAN_LOW])
            limits = 1 if (swing_ud or swing_rl) else 0

            sent = await self._async_send_and_wait(
                _payload_set_state(
                    mode=mode_code,
                    fan_speed=fan_code,
                    limits=limits,
                    health=healthmode,
                    target_temperature=tempset,
                )
            )
            if not sent:
                raise HaierBridgeError("Direct command SET_STATE failed")

            # Apply the optimistic values on top of whatever the device just
            # reported, so a concurrent state frame cannot revert the command.
            self._state.update(
                {
                    "powerstate": True,
                    "mode": mode,
                    "fanspeed": fanspeed,
                    "tempset": tempset,
                    "swing_ud": swing_ud,
                    "swing_rl": swing_rl,
                    "healthmode": healthmode,
                    "online": True,
                }
            )
            return {"result": "ok"}

    async def async_close(self) -> None:
        """Close the TCP connection."""
        async with self._io_lock:
            await self._async_disconnect()

    def _check_device(self, device_id: str) -> None:
        if device_id != self._device_id:
            raise HaierBridgeError(f"Unknown direct device id: {device_id}")

    async def _async_ensure_connected(self) -> None:
        if self._reader is not None and self._writer is not None and not self._writer.is_closing():
            return

        await self._async_disconnect()

        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, DIRECT_PORT),
                timeout=DIRECT_TIMEOUT,
            )
        except (TimeoutError, OSError) as err:
            self._reader = None
            self._writer = None
            raise HaierBridgeError(
                f"Cannot connect to direct AC at {self._host}:{DIRECT_PORT}: {err}"
            ) from err

    async def _async_disconnect(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        self._rx_buffer = b""

        if writer is None:
            return

        try:
            writer.close()
            await writer.wait_closed()
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("Error while closing direct connection: %s", err)

    async def _async_send_and_wait(self, payload: bytes) -> bool:
        """Send one command, retrying once if the socket was already dead."""
        async with self._io_lock:
            last_error = HaierBridgeError("Direct AC command was never attempted")

            for attempt in (1, 2):
                try:
                    await self._async_ensure_connected()
                    return await self._async_send_once(payload)
                except HaierBridgeError as err:
                    last_error = err
                    await self._async_disconnect()
                    if attempt == 1:
                        _LOGGER.debug("Direct command failed (%s), reconnecting", err)

            raise last_error

    async def _async_send_once(self, payload: bytes) -> bool:
        writer = self._writer
        if writer is None:
            raise HaierBridgeError("Direct AC connection is not available")

        seq = self._seq
        self._seq = (self._seq + 1) % 256
        packet = _build_packet(self._mac, seq, payload)

        try:
            writer.write(packet)
            await asyncio.wait_for(writer.drain(), timeout=DIRECT_TIMEOUT)
        except (TimeoutError, OSError) as err:
            raise HaierBridgeError(f"Failed to write direct AC command: {err}") from err

        return await self._async_wait_for_seq(seq)

    async def _async_wait_for_seq(self, seq: int) -> bool:
        if self._apply_frames(self._consume_frames(), seq):
            return True

        loop = asyncio.get_running_loop()
        deadline = loop.time() + DIRECT_TIMEOUT

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise HaierBridgeError("Direct AC response timeout")

            reader = self._reader
            if reader is None:
                raise HaierBridgeError("Direct AC connection closed while waiting response")

            try:
                chunk = await asyncio.wait_for(reader.read(2048), timeout=remaining)
            except TimeoutError as err:
                raise HaierBridgeError("Direct AC response timeout") from err
            except OSError as err:
                raise HaierBridgeError(f"Direct AC connection error: {err}") from err

            if not chunk:
                raise HaierBridgeError("Direct AC closed the TCP connection")

            self._rx_buffer += chunk
            if len(self._rx_buffer) > _DIRECT_MAX_RX_BUFFER:
                _LOGGER.warning(
                    "Discarding %d bytes of unparsable data from the direct AC",
                    len(self._rx_buffer),
                )
                self._rx_buffer = b""
                continue

            if self._apply_frames(self._consume_frames(), seq):
                return True

    def _consume_frames(self) -> list[tuple[int, bytes]]:
        """Extract every complete frame from the receive buffer."""
        frames: list[tuple[int, bytes]] = []
        buffer = self._rx_buffer
        cursor = 0
        keep = len(_DIRECT_RESPONSE_PREFIX) - 1

        while True:
            idx = buffer.find(_DIRECT_RESPONSE_PREFIX, cursor)
            if idx < 0:
                # Nothing more to parse: retain only a possible partial prefix.
                tail = buffer[cursor:]
                self._rx_buffer = tail[-keep:] if len(tail) > keep else tail
                return frames

            if len(buffer) - idx < _DIRECT_HEADER_LEN:
                self._rx_buffer = buffer[idx:]
                return frames

            seq_len_offset = idx + _DIRECT_SEQ_LEN_OFFSET
            frame_seq = buffer[seq_len_offset + 3]
            cmd_len = buffer[seq_len_offset + 7]
            cmd_start = seq_len_offset + 8
            cmd_end = cmd_start + cmd_len

            if len(buffer) < cmd_end:
                self._rx_buffer = buffer[idx:]
                return frames

            frames.append((frame_seq, buffer[cmd_start:cmd_end]))
            cursor = cmd_end

    def _apply_frames(self, frames: list[tuple[int, bytes]], seq: int) -> bool:
        matched = False
        for frame_seq, command in frames:
            if state := _parse_state_command(self._device_id, self._device_name, command):
                self._state.update(state)
            if frame_seq == seq:
                matched = True
        return matched


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
    target = clamp_temperature(target_temperature)
    payload = bytearray.fromhex("ffff22000000000000014d5f00000000000000000000")
    payload.extend([0x00, mode & 0xFF])
    payload.extend([0x00, fan_speed & 0xFF])
    payload.extend([0x00, limits & 0xFF])
    payload.extend([0x00, 0x09 if health else 0x01])  # Keep parity with bstuff implementation.
    payload.extend([0x00, 0x01 if health else 0x00])
    payload.extend([0x00, 0x00, 0x00, (target - MIN_TEMP) & 0xFF])
    payload.append(_payload_checksum(payload))
    return bytes(payload)


def _payload_checksum(payload: bytes) -> int:
    """Return the trailing checksum byte, matching the bstuff implementation."""
    return (sum(payload) - 2 * 0xFF) & 0xFF


def _parse_state_command(device_id: str, device_name: str, command: bytes) -> dict[str, Any] | None:
    if len(command) < 37:
        return None
    if command[0:2] != b"\xff\xff":
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
        "tempset": clamp_temperature(target_raw + MIN_TEMP),
        "temp": float(current_temp),
        "humidity": 0,
        "fanspeed": _DIRECT_FAN_TO_DEVICE.get(fan_raw, DEVICE_FAN_LOW),
        "swing_ud": limits_raw == 1,
        "swing_rl": limits_raw == 1,
        "healthmode": bool(health_raw % 2),
        "online": True,
    }
