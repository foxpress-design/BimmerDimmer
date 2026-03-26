# src/slower/transport/ble.py
"""BLE GATT server transport for GPS data.

Uses BlueZ via dbus-fast to create a GATT server on the Raspberry Pi.
The phone connects via Web Bluetooth and writes GPS data to a characteristic.

Requires: Linux with BlueZ, dbus-fast package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)

# Custom GATT UUIDs for BimmerDimmer
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
GPS_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
STATUS_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"


class BLETransport:
    """BLE GATT server GPS transport."""

    name: str = "ble"

    def __init__(self) -> None:
        self.health = TransportHealth(name="ble", timeout_sec=10.0)
        self._gps: GPSProvider | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def start(self, gps: GPSProvider) -> None:
        """Start the BLE GATT server in a background thread."""
        self._gps = gps
        self._running = True
        self._thread = threading.Thread(target=self._run_server, daemon=True, name="ble-gatt")
        self._thread.start()
        logger.info("BLE transport starting")

    def stop(self) -> None:
        """Stop the BLE GATT server."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._gps = None
        logger.info("BLE transport stopped")

    def _run_server(self) -> None:
        """Run the async GATT server in its own event loop."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        except ImportError:
            logger.warning("dbus-fast not available. BLE transport disabled.")
        except Exception as e:
            logger.error("BLE GATT server error: %s", e)
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    async def _serve(self) -> None:
        """Set up and run the BLE GATT server using dbus-fast."""
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method
        from dbus_fast import Variant

        bus = await MessageBus().connect()

        transport = self

        class GPSGattService(ServiceInterface):
            """D-Bus service interface for BLE GPS data reception."""

            def __init__(self):
                super().__init__("org.bluez.GattCharacteristic1")

            @method()
            def WriteValue(self, value: "ay", options: "a{sv}") -> None:
                """Called when phone writes GPS data to the characteristic."""
                try:
                    json_str = bytes(value).decode("utf-8")
                    data = json.loads(json_str)
                    if transport._gps:
                        pos = transport._gps.update(
                            lat=float(data["latitude"]),
                            lon=float(data["longitude"]),
                            speed_mps=data.get("speed"),
                            heading=data.get("heading"),
                            accuracy_m=float(data.get("accuracy", 50)),
                        )
                        if pos is not None:
                            transport.health.record_success()
                        else:
                            transport.health.record_failure()
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                    logger.warning("BLE: invalid GPS data: %s", e)
                    transport.health.record_failure()

        service = GPSGattService()
        bus.export("/org/bluez/bimmerdimmer/gps", service)

        logger.info("BLE GATT server registered on D-Bus")

        while self._running:
            await asyncio.sleep(1.0)

        bus.disconnect()
