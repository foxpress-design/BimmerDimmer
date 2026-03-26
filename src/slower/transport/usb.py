"""USB-C tethering transport for GPS data.

Monitors a USB network interface (typically usb0) created when a phone
enables USB tethering. GPS data flows through the existing HTTP endpoint;
this transport only tracks whether the USB network link is active.

Requires: Linux (reads /sys/class/net/).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)


class USBTransport:
    """USB tethering transport that monitors network interface health."""

    name: str = "usb"

    def __init__(self, interface: str = "usb0") -> None:
        self.interface = interface
        self.health = TransportHealth(name="usb", timeout_sec=10.0)
        self._gps: GPSProvider | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self, gps: GPSProvider) -> None:
        """Start monitoring the USB network interface."""
        self._gps = gps
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True, name="usb-monitor")
        self._thread.start()
        logger.info("USB transport started, monitoring interface %s", self.interface)

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self._gps = None
        logger.info("USB transport stopped")

    def _monitor(self) -> None:
        """Periodically check if the USB network interface is up."""
        while self._running:
            if self._is_interface_up():
                self.health.record_success()
            else:
                self.health.record_failure()
            time.sleep(3.0)

    def _is_interface_up(self) -> bool:
        """Check if the USB network interface exists and is up.

        Reads /sys/class/net/{interface}/operstate on Linux.
        Returns False on non-Linux systems.
        """
        operstate_path = Path(f"/sys/class/net/{self.interface}/operstate")
        try:
            state = operstate_path.read_text().strip().lower()
            return state == "up"
        except (OSError, IOError):
            return False
