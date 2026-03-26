# Changelog

## 0.3.5 (2026-03-26)

### What's New

- Added `src/slower/bmw/watchdog.py` with `write_heartbeat`, `read_heartbeat_age`, and `remove_heartbeat` for heartbeat file management in the main slower process.
- Added `src/slower_watchdog/__init__.py` and `src/slower_watchdog/main.py` as a standalone watchdog process (`slower-watchdog` CLI entry point).
- The watchdog monitors the heartbeat file and sends a disable_vmax UDS command directly to the DME if the main process goes silent for more than 10 seconds.
- The standalone watchdog intentionally duplicates minimal K+DCAN framing (no imports from slower) to avoid shared failure modes.
- Registered `slower-watchdog` as a project script entry point in `pyproject.toml`.

## 0.3.4 (2026-03-26)

### What's New

- Added `src/slower/bmw/recovery.py` with `check_stale_vmax` and `reset_vmax` functions for DME startup safety checks and recovery.
- `check_stale_vmax` detects leftover active Vmax limits below 120 km/h (GPS_LOSS_CAP_KMH), indicating a crash from a previous session.
- `reset_vmax` disables the DME Vmax limiter and logs the result, used by the `--reset` CLI command.
- Added `tests/test_recovery.py` with five tests covering stale detection, high-value pass-through, inactive pass-through, successful reset, and failed reset.

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
