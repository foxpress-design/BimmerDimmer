# Changelog

## 0.4.4 (2026-03-27)

### What's New
- Added session history for development continuity

## 0.4.3 (2026-03-26)

### What's New

- Added `src/slower/transport/usb.py` with `USBTransport` class for USB-C tethering transport.
- `USBTransport` monitors a USB network interface (default `usb0`) by reading `/sys/class/net/{interface}/operstate` on Linux, recording health via `TransportHealth`.
- GPS data continues to flow through the existing HTTP endpoint; this transport only tracks whether the USB network link is active.
- Added `tests/test_usb_transport.py` with six tests covering name, default/custom interface, interface-up detection, operstate file reading, and initial health state.

## 0.4.2 (2026-03-26)

### What's New

- Added `test_gps_loss_caps_at_120_after_grace` to `tests/test_safety.py` to verify that GPS loss correctly caps speed at `GPS_LOSS_CAP_KMH` (120 km/h) after the grace period expires.

## 0.4.1 (2026-03-26)

### What's New

- Updated dashboard title and header to "BimmerDimmer".
- Added `transport_states`, `dme_write_count`, and `degraded_reason` fields to the `/api/status` JSON response.
- Added WiFi, BLE, and SPP transport health chips to the status bar, color-coded by state (healthy/degraded/lost).
- Added DME write counter display (shown only in active mode, with color warnings at 500 and 1000 writes).
- Added degraded mode banner that appears when `degraded_reason` is set, styled in red.
- Added BLE Connect button (hidden automatically when Web Bluetooth is not supported by the browser).
- Added active mode confirmation dialog to prevent accidental DME write activation.
- Added `connectBLE` and `sendGPSviaBLE` Web Bluetooth functions for direct BLE GPS forwarding.
- GPS watchPosition callback now also forwards position to BLE when a characteristic is connected.

## 0.4.0 (2026-03-26)

### What's New

- Added `--reset` CLI flag to connect to DME, call `reset_vmax()` from `slower.bmw.recovery`, and exit (recovery mode).
- Added startup recovery check after DME initialization: calls `check_stale_vmax()` and disables Vmax if a stale limit from a previous crash is detected.
- Wired WiFi, BLE, and SPP transports at startup with a shared `ConnectionMonitor` instance.
- Passed `connection_monitor` to `SpeedLimiterController` and `wifi_transport` to `create_app`.
- Added `remove_heartbeat()` call to the shutdown handler so the watchdog heartbeat file is cleaned up on graceful exit.
- Updated startup banner to "BimmerDimmer v0.3.0".
- Verified `slower-watchdog` entry point is present in `pyproject.toml`.

## 0.3.9 (2026-03-26)

### What's New

- Added confirmation tick logic to `SpeedLimiterController._control_tick`: lowering Vmax now requires the target to be stable for `config.safety.write_confirm_ticks` consecutive ticks before applying, preventing spurious speed limit decreases.
- Added GPS fix freshness check in `_control_tick`: fixes older than 5 seconds apply `GPS_LOSS_CAP_KMH` and set `degraded_reason = "GPS fix stale"`.
- Integrated `write_heartbeat()` call at the start of each control tick for watchdog support.
- Added `ConnectionMonitor` integration to `SpeedLimiterController`: constructor accepts an optional `connection_monitor` parameter, `_apply_vmax` gates DME writes on `should_write_dme`, and records K+DCAN health on success or failure.
- Added three new fields to `LimiterState`: `transport_states` (dict of transport names to state strings), `dme_write_count` (running DME write count), and `degraded_reason` (human-readable degraded state description).
- Updated `_control_tick` to refresh `state.transport_states` and `state.dme_write_count` each tick.
- Pending confirmation state (`_pending_vmax_kmh`, `_pending_ticks`) is reset on GPS loss.

## 0.3.8 (2026-03-26)

### What's New

- Added `src/slower/transport/spp.py` with `SPPTransport` class for Classic Bluetooth (SPP/RFCOMM) GPS data reception.
- `SPPTransport` listens on a configurable RFCOMM channel (default 1) and accepts newline-delimited JSON GPS payloads from a companion app.
- Gracefully disables itself on non-Linux platforms where `socket.AF_BLUETOOTH` is unavailable (catches `AttributeError`).
- Uses `TransportHealth` for connection health tracking, consistent with the WiFi and BLE transport patterns.
- `_process_line()` parses JSON, calls `GPSProvider.update()`, and records success or failure on the health tracker.

## 0.3.7 (2026-03-26)

### What's New

- Added `src/slower/transport/ble.py` with `BLETransport` class implementing a BLE GATT server for receiving GPS data from phones via Web Bluetooth.
- `BLETransport` runs a BlueZ D-Bus GATT server (via dbus-fast) in a background daemon thread with its own asyncio event loop.
- The `WriteValue` GATT characteristic method parses incoming JSON GPS payloads and delegates to `GPSProvider.update()`, recording health via `TransportHealth`.
- Gracefully handles systems without dbus-fast (ImportError caught, warning logged, transport disabled).
- Added `dbus-fast>=2.0` to project dependencies in `pyproject.toml`.

## 0.3.6 (2026-03-26)

### What's New

- Added `TransportConfig` dataclass to `src/slower/config.py` with fields: `wifi`, `ble`, `spp` (all bool, default True), and `spp_channel` (int, default 1).
- Added `SafetyExtConfig` dataclass with fields: `max_gps_accuracy_m`, `max_speed_jump_kmh`, `write_confirm_ticks`, `max_writes_per_session`, `watchdog_heartbeat_sec`, and `watchdog_timeout_sec`.
- Wired both new configs into the `Config` dataclass as `transports` and `safety` fields with appropriate defaults.
- Added parsing in `load_config()` for both new sections from YAML config files.
- Added `src/slower/transport/wifi.py` with `WiFiTransport` class wrapping the Flask GPS endpoint with health tracking via `TransportHealth`.
- `WiFiTransport.handle_update()` delegates to `GPSProvider.update()` and records success or failure on the health tracker.
- Updated `create_app()` in `src/slower/web/server.py` to accept an optional `wifi_transport` parameter.
- When `wifi_transport` is provided, `POST /api/gps` delegates to it; otherwise falls back to calling `gps.update()` directly (backward compatible).
- The `/api/gps` endpoint now handles `None` returns from `update()` (fix rejected by validation) with a descriptive JSON response instead of raising an error.

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
