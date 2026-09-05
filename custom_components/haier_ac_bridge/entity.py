"""Shared entity base classes for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACDEVICE_NAME, DEFAULT_ACDEVICE_NAME, DOMAIN
from .coordinator import HaierDataCoordinator


class HaierBaseEntity(CoordinatorEntity[HaierDataCoordinator]):
    """Shared coordinator-backed entity for a single Haier device."""

    def __init__(self, coordinator: HaierDataCoordinator, device_id: str) -> None:
        """Initialize the entity for one device."""
        super().__init__(coordinator)
        self._device_id = device_id
        # Resolved once so the device registry entry keeps a stable name even
        # while the device is unreachable.
        self._device_label = self._resolve_device_label()

    def _resolve_device_label(self) -> str:
        device = self.coordinator.get_device(self._device_id)
        raw_name = str((device or {}).get("name") or "").strip()
        return raw_name or self._device_id

    @property
    def base_name(self) -> str:
        """Return the user configured prefix for this integration's entities."""
        return str(self.coordinator.options.get(CONF_ACDEVICE_NAME) or DEFAULT_ACDEVICE_NAME)

    @property
    def device_label(self) -> str:
        """Return the device name reported by the bridge."""
        current = self._resolve_device_label()
        if current != self._device_id:
            self._device_label = current
        return self._device_label

    @property
    def device(self) -> dict[str, Any] | None:
        """Return the cached state for this device, if any."""
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        """Return whether the last poll reached this device."""
        return bool(super().available and self.coordinator.is_device_available(self._device_id))

    @property
    def device_info(self) -> DeviceInfo:
        """Return registry information, also while the device is offline."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Haier",
            model="HaierAC",
            name=f"{self.base_name} {self.device_label}".strip(),
        )
