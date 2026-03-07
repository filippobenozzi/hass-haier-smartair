"""Shared entity base classes for Haier AC Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HaierDataCoordinator


class HaierBaseEntity(CoordinatorEntity[HaierDataCoordinator]):
    """Shared coordinator-backed entity for a single Haier device."""

    def __init__(self, coordinator: HaierDataCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id

    @property
    def device(self) -> dict[str, Any] | None:
        return self.coordinator.get_device(self._device_id)

    @property
    def available(self) -> bool:
        return bool(super().available and self.coordinator.is_device_available(self._device_id))

    @property
    def device_info(self):
        device = self.device
        if device is None:
            return None

        return {
            "identifiers": {(DOMAIN, self._device_id)},
            "manufacturer": "Haier",
            "model": "HaierAC",
            "name": f"{self.coordinator.options['acdevice_name']} {device['name']}",
        }
