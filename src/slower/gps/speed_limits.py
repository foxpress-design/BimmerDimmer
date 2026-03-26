"""Speed limit lookup from GPS coordinates.

Supports two data sources:
  1. OpenStreetMap Overpass API (free, no API key needed)
  2. Google Maps Roads API (requires premium API key)

OSM is the default and recommended source. Google Maps Roads API requires
a Google Cloud project with the Roads API enabled and a valid API key.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# Common US speed limits for validation (mph)
VALID_US_SPEED_LIMITS = {15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85}


@dataclass
class SpeedLimitResult:
    """Result of a speed limit lookup."""

    speed_limit_mph: int | None
    source: str  # "osm", "google", "cache", "none"
    road_name: str | None = None
    confidence: float = 0.0  # 0.0 to 1.0
    timestamp: float = 0.0

    @property
    def speed_limit_kmh(self) -> int | None:
        if self.speed_limit_mph is None:
            return None
        return int(self.speed_limit_mph / 0.621371)


class SpeedLimitCache:
    """Simple TTL cache for speed limit lookups."""

    def __init__(self, ttl_sec: int = 30) -> None:
        self.ttl_sec = ttl_sec
        self._cache: dict[str, SpeedLimitResult] = {}

    def _key(self, lat: float, lon: float) -> str:
        # Round to ~100m grid for cache hits
        return f"{lat:.4f},{lon:.4f}"

    def get(self, lat: float, lon: float) -> SpeedLimitResult | None:
        key = self._key(lat, lon)
        result = self._cache.get(key)
        if result and (time.time() - result.timestamp) < self.ttl_sec:
            return SpeedLimitResult(
                speed_limit_mph=result.speed_limit_mph,
                source="cache",
                road_name=result.road_name,
                confidence=result.confidence,
                timestamp=result.timestamp,
            )
        return None

    def put(self, lat: float, lon: float, result: SpeedLimitResult) -> None:
        key = self._key(lat, lon)
        result.timestamp = time.time()
        self._cache[key] = result

        # Evict old entries
        now = time.time()
        stale = [k for k, v in self._cache.items() if now - v.timestamp > self.ttl_sec * 10]
        for k in stale:
            del self._cache[k]


def _parse_osm_maxspeed(maxspeed: str) -> int | None:
    """Parse an OSM maxspeed tag value to mph.

    OSM maxspeed can be: "45 mph", "30", "50 km/h", "none", etc.
    """
    if not maxspeed:
        return None

    maxspeed = maxspeed.strip().lower()
    if maxspeed in ("none", "signals", "variable", "walk"):
        return None

    # "45 mph" or "45mph"
    if "mph" in maxspeed:
        try:
            return int(maxspeed.replace("mph", "").strip())
        except ValueError:
            return None

    # "50 km/h" or "50 kmh"
    if "km" in maxspeed:
        try:
            kmh = int(maxspeed.replace("km/h", "").replace("kmh", "").strip())
            return int(kmh * 0.621371)
        except ValueError:
            return None

    # Plain number - in US context, assume mph; internationally, assume km/h
    try:
        val = int(maxspeed)
        # Heuristic: values > 90 are likely km/h (no US road is 90+ mph commonly)
        if val > 85:
            return int(val * 0.621371)
        return val
    except ValueError:
        return None


class OSMSpeedLimitProvider:
    """Look up speed limits from OpenStreetMap via Overpass API."""

    OVERPASS_URL = "https://overpass-api.de/api/interpreter"

    def __init__(self, search_radius_m: int = 50) -> None:
        self.search_radius_m = search_radius_m
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "Slower/0.1 (BMW speed limiter)"

    def lookup(self, lat: float, lon: float) -> SpeedLimitResult:
        """Query OSM for the speed limit at the given coordinates.

        Uses Overpass API to find the nearest road with a maxspeed tag.
        """
        # Overpass QL: find ways with maxspeed within radius of point
        query = f"""
        [out:json][timeout:5];
        way(around:{self.search_radius_m},{lat},{lon})["maxspeed"]["highway"];
        out tags;
        """

        try:
            resp = self._session.post(
                self.OVERPASS_URL,
                data={"data": query},
                timeout=8,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning("OSM Overpass query failed: %s", e)
            return SpeedLimitResult(speed_limit_mph=None, source="osm")

        elements = data.get("elements", [])
        if not elements:
            logger.debug("No roads with maxspeed found near (%.6f, %.6f)", lat, lon)
            return SpeedLimitResult(speed_limit_mph=None, source="osm")

        # Use the first result (closest road)
        tags = elements[0].get("tags", {})
        maxspeed_raw = tags.get("maxspeed", "")
        road_name = tags.get("name", tags.get("ref", "Unknown road"))

        speed_mph = _parse_osm_maxspeed(maxspeed_raw)

        logger.info(
            "OSM: %s near (%.4f, %.4f) - maxspeed=%s -> %s mph",
            road_name, lat, lon, maxspeed_raw, speed_mph,
        )

        return SpeedLimitResult(
            speed_limit_mph=speed_mph,
            source="osm",
            road_name=road_name,
            confidence=0.8 if speed_mph else 0.0,
        )


class GoogleSpeedLimitProvider:
    """Look up speed limits from Google Maps Roads API.

    Requires a Google Cloud API key with the Roads API enabled.
    Note: The speedLimits endpoint requires a premium/asset tracking plan.
    """

    ROADS_URL = "https://roads.googleapis.com/v1/speedLimits"

    def __init__(self, api_key: str, search_radius_m: int = 50) -> None:
        if not api_key:
            raise ValueError("Google Maps API key is required")
        self.api_key = api_key
        self.search_radius_m = search_radius_m
        self._session = requests.Session()

    def lookup(self, lat: float, lon: float) -> SpeedLimitResult:
        """Query Google Roads API for the speed limit."""
        # First snap to road, then get speed limit
        snap_url = "https://roads.googleapis.com/v1/nearestRoads"
        try:
            snap_resp = self._session.get(
                snap_url,
                params={
                    "points": f"{lat},{lon}",
                    "key": self.api_key,
                },
                timeout=5,
            )
            snap_resp.raise_for_status()
            snap_data = snap_resp.json()

            snapped = snap_data.get("snappedPoints", [])
            if not snapped:
                return SpeedLimitResult(speed_limit_mph=None, source="google")

            place_id = snapped[0].get("placeId", "")
            if not place_id:
                return SpeedLimitResult(speed_limit_mph=None, source="google")

            # Get speed limit for the snapped road
            limit_resp = self._session.get(
                self.ROADS_URL,
                params={
                    "placeId": place_id,
                    "key": self.api_key,
                },
                timeout=5,
            )
            limit_resp.raise_for_status()
            limit_data = limit_resp.json()

            limits = limit_data.get("speedLimits", [])
            if not limits:
                return SpeedLimitResult(speed_limit_mph=None, source="google")

            speed_limit = limits[0]
            speed_mph = int(speed_limit.get("speedLimit", 0))
            # Google returns in the posted unit; convert if km/h
            if speed_limit.get("units") == "KPH":
                speed_mph = int(speed_mph * 0.621371)

            return SpeedLimitResult(
                speed_limit_mph=speed_mph,
                source="google",
                confidence=0.95,
            )

        except requests.RequestException as e:
            logger.warning("Google Roads API failed: %s", e)
            return SpeedLimitResult(speed_limit_mph=None, source="google")


class SpeedLimitService:
    """Unified speed limit lookup with caching and fallback."""

    def __init__(
        self,
        primary: str = "osm",
        google_api_key: str = "",
        search_radius_m: int = 50,
        cache_ttl_sec: int = 30,
    ) -> None:
        self.cache = SpeedLimitCache(ttl_sec=cache_ttl_sec)

        self._providers: list = []
        if primary == "google" and google_api_key:
            self._providers.append(GoogleSpeedLimitProvider(google_api_key, search_radius_m))
            self._providers.append(OSMSpeedLimitProvider(search_radius_m))
        else:
            self._providers.append(OSMSpeedLimitProvider(search_radius_m))
            if google_api_key:
                self._providers.append(GoogleSpeedLimitProvider(google_api_key, search_radius_m))

        self._last_known: SpeedLimitResult | None = None

    def get_speed_limit(self, lat: float, lon: float) -> SpeedLimitResult:
        """Get the speed limit for the given coordinates.

        Checks cache first, then queries providers in priority order.
        Falls back to last known limit if all providers fail.
        """
        # Check cache
        cached = self.cache.get(lat, lon)
        if cached and cached.speed_limit_mph is not None:
            return cached

        # Query providers in order
        for provider in self._providers:
            result = provider.lookup(lat, lon)
            if result.speed_limit_mph is not None:
                self.cache.put(lat, lon, result)
                self._last_known = result
                return result

        # All providers failed - return last known or unknown
        if self._last_known and self._last_known.speed_limit_mph is not None:
            return SpeedLimitResult(
                speed_limit_mph=self._last_known.speed_limit_mph,
                source="last_known",
                road_name=self._last_known.road_name,
                confidence=0.3,
            )

        return SpeedLimitResult(speed_limit_mph=None, source="none")
