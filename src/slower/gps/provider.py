"""GPS data provider - receives location from phone via WebSocket/HTTP.

The phone's web browser provides GPS coordinates via the Geolocation API.
The web dashboard (served by Flask) sends position updates to this module.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GPSPosition:
    """A GPS position fix from the phone."""

    latitude: float
    longitude: float
    speed_mps: float | None  # Speed in meters/sec from GPS (may be None)
    heading: float | None  # Heading in degrees (0=North, may be None)
    accuracy_m: float  # Position accuracy in meters
    timestamp: float  # Unix timestamp of the fix

    @property
    def speed_mph(self) -> float | None:
        if self.speed_mps is None:
            return None
        return self.speed_mps * 2.23694

    @property
    def speed_kmh(self) -> float | None:
        if self.speed_mps is None:
            return None
        return self.speed_mps * 3.6

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def is_stale(self) -> bool:
        """Position is stale if older than 10 seconds."""
        return self.age_seconds > 10.0

    def __repr__(self) -> str:
        return (
            f"GPS({self.latitude:.6f}, {self.longitude:.6f}, "
            f"speed={self.speed_mph or 0:.1f}mph, age={self.age_seconds:.1f}s)"
        )


class GPSProvider:
    """Manages GPS position data received from the phone."""

    def __init__(self) -> None:
        self._position: GPSPosition | None = None
        self._position_history: list[GPSPosition] = []
        self._max_history = 60  # Keep last 60 positions

    @property
    def has_fix(self) -> bool:
        return self._position is not None and not self._position.is_stale

    @property
    def position(self) -> GPSPosition | None:
        if self._position and self._position.is_stale:
            return None
        return self._position

    def update(self, lat: float, lon: float, speed_mps: float | None = None,
               heading: float | None = None, accuracy_m: float = 50.0) -> GPSPosition:
        """Update with a new GPS fix from the phone.

        Args:
            lat: Latitude in decimal degrees
            lon: Longitude in decimal degrees
            speed_mps: Speed in meters/second (from browser Geolocation API)
            heading: Heading in degrees from north
            accuracy_m: Accuracy radius in meters

        Returns:
            The new GPSPosition
        """
        pos = GPSPosition(
            latitude=lat,
            longitude=lon,
            speed_mps=speed_mps,
            heading=heading,
            accuracy_m=accuracy_m,
            timestamp=time.time(),
        )

        self._position = pos
        self._position_history.append(pos)
        if len(self._position_history) > self._max_history:
            self._position_history = self._position_history[-self._max_history:]

        logger.debug("GPS update: %s", pos)
        return pos

    def get_recent_positions(self, count: int = 5) -> list[GPSPosition]:
        """Get the most recent positions for averaging/smoothing."""
        return self._position_history[-count:]
