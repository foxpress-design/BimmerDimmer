"""WiFi HTTP transport for GPS data.

Wraps the existing Flask POST /api/gps endpoint with health tracking.
The actual HTTP endpoint remains in web/server.py; this module provides
the transport wrapper that server.py delegates to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)


class WiFiTransport:
    """WiFi HTTP GPS transport with health tracking."""

    name: str = "wifi"

    def __init__(self) -> None:
        self.health = TransportHealth(name="wifi", timeout_sec=10.0)
        self._gps: GPSProvider | None = None

    def start(self, gps: GPSProvider) -> None:
        self._gps = gps
        logger.info("WiFi transport started")

    def stop(self) -> None:
        self._gps = None
        logger.info("WiFi transport stopped")

    def handle_update(self, lat: float, lon: float, speed_mps: float | None = None,
                      heading: float | None = None, accuracy_m: float = 50.0):
        """Called by the Flask endpoint when GPS data arrives via HTTP."""
        if self._gps is None:
            return None
        pos = self._gps.update(
            lat=lat, lon=lon, speed_mps=speed_mps,
            heading=heading, accuracy_m=accuracy_m,
        )
        if pos is not None:
            self.health.record_success()
        else:
            self.health.record_failure()
        return pos
