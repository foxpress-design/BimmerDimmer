"""GPS data provider - receives location from phone via WebSocket/HTTP.

The phone's web browser provides GPS coordinates via the Geolocation API.
The web dashboard (served by Flask) sends position updates to this module.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# GPS validation thresholds
MAX_GPS_ACCURACY_M = 100.0
MAX_SPEED_JUMP_KMH = 50.0
MAX_IMPLIED_SPEED_KMH = 200.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in meters."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


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
               heading: float | None = None, accuracy_m: float = 50.0) -> GPSPosition | None:
        """Update with a new GPS fix from the phone.

        Returns the new GPSPosition, or None if the fix was rejected by validation.
        """
        # Accuracy filter
        if accuracy_m > MAX_GPS_ACCURACY_M:
            logger.warning("GPS fix rejected: accuracy %.0fm exceeds %.0fm threshold",
                            accuracy_m, MAX_GPS_ACCURACY_M)
            return None

        pos = GPSPosition(
            latitude=lat,
            longitude=lon,
            speed_mps=speed_mps,
            heading=heading,
            accuracy_m=accuracy_m,
            timestamp=time.time(),
        )

        # Validate against previous fix (if we have one)
        prev = self._position
        if prev is not None:
            # Speed jump filter
            if speed_mps is not None and prev.speed_mps is not None:
                new_kmh = speed_mps * 3.6
                old_kmh = prev.speed_mps * 3.6
                if abs(new_kmh - old_kmh) > MAX_SPEED_JUMP_KMH:
                    logger.warning("GPS fix rejected: speed jump %.0f -> %.0f km/h",
                                    old_kmh, new_kmh)
                    return None

            # Teleportation filter (only when enough time has passed to compute a meaningful speed)
            elapsed = pos.timestamp - prev.timestamp
            if elapsed >= 0.5:
                dist_m = _haversine_m(prev.latitude, prev.longitude, lat, lon)
                implied_kmh = (dist_m / elapsed) * 3.6
                if implied_kmh > MAX_IMPLIED_SPEED_KMH:
                    logger.warning("GPS fix rejected: implied speed %.0f km/h (teleportation)",
                                    implied_kmh)
                    return None

        self._position = pos
        self._position_history.append(pos)
        if len(self._position_history) > self._max_history:
            self._position_history = self._position_history[-self._max_history:]

        logger.debug("GPS update: %s", pos)
        return pos

    def get_recent_positions(self, count: int = 5) -> list[GPSPosition]:
        """Get the most recent positions for averaging/smoothing."""
        return self._position_history[-count:]
