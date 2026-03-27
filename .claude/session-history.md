# Session History

## 2026-03-26: Major Feature Implementation Session

### Summary

Implemented Bluetooth multi-transport GPS support, comprehensive safety hardening, and USB-C tethering for BimmerDimmer (GPS-based speed limiter for BMW E90 325xi).

### What Was Done

**Initial changes (pre-plan):**
- Renamed project from "Slower" to "BimmerDimmer"
- Raised minimum Vmax floor from 25 km/h to 40 km/h
- Changed GPS/device loss behavior: caps at 120 km/h instead of releasing to factory max (250 km/h)
- Raised config `min_vmax_mph` default from 15 to 25
- Created CLAUDE.md
- Made GitHub repo public, updated description and topics

**Bluetooth + Safety Hardening (16-task implementation plan):**
1. GPSTransport protocol + TransportHealth tracking
2. GPS data validation (accuracy >100m filter, speed jump >50 km/h filter, teleportation >200 km/h filter)
3. ConnectionMonitor for all system link health
4. DME write counter (warn at 500, hard-stop at 1000), read-back verification, bounds guard (40-250 km/h)
5. Recovery module (stale Vmax detection, `slower --reset` command)
6. Standalone watchdog process (`slower-watchdog`) with heartbeat monitoring
7. TransportConfig and SafetyExtConfig added to config
8. WiFi transport refactored with health tracking
9. BLE GATT transport via dbus-fast (Web Bluetooth)
10. Bluetooth Serial (SPP/RFCOMM) transport
11. Controller safety improvements (confirmation ticks, fresh data requirement, heartbeat, connection monitor)
12. Main entry point wiring (--reset, transports, startup recovery)
13. Dashboard updates (transport chips, write counter, degraded banner, BLE connect button, active mode confirmation dialog)
14. Test updates for new safety values
15. Final integration verification
16. GitHub repo updates (already done)

**USB-C Tethering Transport:**
- Added USBTransport that monitors `usb0` network interface health
- GPS data flows through existing HTTP endpoint (no new data path)
- Dashboard shows USB chip alongside WiFi/BLE/SPP
- Config: `transports.usb` and `transports.usb_interface`

### Key Decisions

- GPS loss caps at 120 km/h (GPS_LOSS_CAP_KMH) instead of releasing to factory max
- Minimum Vmax floor is 40 km/h (non-configurable safety limit)
- DME write counter uses permanent session latch (writes_disabled cannot be reset without restart)
- Standalone watchdog intentionally does NOT import from slower package (isolation from shared failure modes)
- USB tethering reuses HTTP transport path (no new GPS data handling needed)
- Confirmation ticks required before lowering Vmax (default 2 ticks = 6 seconds)
- Teleportation filter has 0.5s minimum elapsed time guard to avoid false positives on rapid-fire updates

### Current State

- Version: 0.4.3
- 61 tests passing
- All code committed and pushed to main
- GitHub repo: public, MIT license, topics set

### Files Modified/Created

**New files:**
- `src/slower/transport/__init__.py`, `health.py`, `wifi.py`, `ble.py`, `spp.py`, `usb.py`
- `src/slower/bmw/watchdog.py`, `recovery.py`
- `src/slower_watchdog/__init__.py`, `main.py`
- `tests/test_transport_health.py`, `test_gps_validation.py`, `test_connection_monitor.py`, `test_dme_protection.py`, `test_recovery.py`, `test_usb_transport.py`
- `docs/superpowers/specs/` (design specs)
- `docs/superpowers/plans/` (implementation plan)
- `CLAUDE.md`, `CHANGELOG.md`

**Modified files:**
- `src/slower/gps/provider.py` (GPS validation)
- `src/slower/bmw/safety.py` (ConnectionMonitor, GPS loss cap)
- `src/slower/bmw/e90_dme.py` (write counter, read-back, bounds guard)
- `src/slower/limiter/controller.py` (confirmation ticks, heartbeat, connection monitor)
- `src/slower/config.py` (TransportConfig, SafetyExtConfig)
- `src/slower/main.py` (--reset, transports, recovery, watchdog)
- `src/slower/web/server.py` (WiFi transport, status API)
- `src/slower/web/templates/index.html` (dashboard overhaul)
- `README.md` (safety-first rewrite, Who's This For section)
- `pyproject.toml` (version, deps, entry points)

### Open Issues / Next Steps

- BLE GATT server uses a simplified BlueZ D-Bus registration (needs full GattManager1 integration for production)
- Unused `Variant` import in ble.py (for future status characteristic notifications)
- Unused `sys` import in slower_watchdog/main.py
- No integration tests for the full control loop (unit tests only)
- Web Bluetooth only works in Chrome/Edge (no Safari/iOS support)
- dbus-fast dependency only works on Linux (fine for Pi target, but dev on macOS needs awareness)
