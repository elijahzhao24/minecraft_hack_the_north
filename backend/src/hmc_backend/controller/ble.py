"""Resilient Bleak central for the Hacker Badge peripheral."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from hmc_backend.controller.state import ControllerStore
from hmc_backend.settings import Settings

LOGGER = logging.getLogger(__name__)


class BadgeBleReceiver:
    def __init__(self, settings: Settings, store: ControllerStore) -> None:
        self.settings = settings
        self.store = store
        self._stop = asyncio.Event()

    async def run(self) -> None:
        if not self.settings.badge_controller_enabled:
            return
        from bleak import BleakClient, BleakScanner

        backoff = 0.5
        while not self._stop.is_set():
            try:
                device = await BleakScanner.find_device_by_filter(
                    self._matches,
                    timeout=self.settings.badge_scan_timeout_s,
                    service_uuids=[self.settings.badge_service_uuid],
                )
                if device is None:
                    raise RuntimeError("Hacker Badge not found")
                disconnected = asyncio.Event()
                async with BleakClient(
                    device,
                    services=[self.settings.badge_service_uuid],
                    disconnected_callback=partial(self._on_disconnect, disconnected),
                ) as client:
                    await client.start_notify(
                        self.settings.badge_state_uuid,
                        self._on_notification,
                    )
                    LOGGER.info("badge controller connected: %s", device.name)
                    backoff = 0.5
                    while not self._stop.is_set() and not disconnected.is_set():
                        self.store.expire_if_stale()
                        try:
                            await asyncio.wait_for(disconnected.wait(), timeout=0.05)
                        except TimeoutError:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect after all transport failures
                LOGGER.warning("badge controller unavailable: %s", exc)
            finally:
                self.store.disconnect()
            if not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(15.0, backoff * 2)

    def stop(self) -> None:
        self._stop.set()

    def _on_notification(self, _characteristic: Any, data: bytearray) -> None:
        self.store.accept_bytes(bytes(data))

    @staticmethod
    def _on_disconnect(event: asyncio.Event, _client: Any) -> None:
        event.set()

    def _matches(self, device, advertisement) -> bool:
        name = advertisement.local_name or device.name or ""
        return name == self.settings.badge_controller_name or name.startswith(
            self.settings.badge_controller_name + "-"
        )
