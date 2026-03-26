# Changelog

## 0.3.3 (2026-03-26)

### What's New

- Added `ConnectionMonitor` to `src/slower/bmw/safety.py` for tracking health of all system connections (K+DCAN cable and GPS transports).
- `ConnectionMonitor` aggregates GPS transport state (healthy if any transport is healthy) and exposes `should_write_dme` to gate DME writes on K+DCAN health.
- Added `tests/test_connection_monitor.py` with six tests covering initial state, GPS aggregation, K+DCAN failure threshold, and DME write gating.

## 0.3.2 (2026-03-26)

### What's New

- Added GPS data validation to `GPSProvider.update()`: rejects fixes with accuracy > 100m, speed jumps > 50 km/h between consecutive fixes, and implied movement > 200 km/h (teleportation detection).
- `update()` now returns `GPSPosition | None` (None when a fix is rejected).
- Added `tests/test_gps_validation.py` with six tests covering all three validation filters.

## 0.3.1 (prior)

- GPS transport health monitoring and BLE/WiFi/SPP transport layer.

## 0.3.0 (initial)

- Initial GPS-based speed limiter implementation for BMW E90 325xi via K+DCAN cable.
