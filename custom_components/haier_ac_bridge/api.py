"""Async API client for Haier AC Bridge."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import ClientError, ClientSession


class HaierBridgeError(Exception):
    """Base Haier bridge exception."""


class HaierBridgeAuthError(HaierBridgeError):
    """Raised when bridge authentication fails."""


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
