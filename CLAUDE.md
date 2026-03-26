# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

BimmerDimmer: GPS-based speed limiter for a 2006 BMW 325xi (E90 / N52 / MSV70 DME). Connects via K+DCAN USB cable and dynamically adjusts the DME's Vmax parameter based on posted speed limits from GPS data. Supports WiFi, BLE, and Bluetooth Serial for phone-to-Pi connectivity. The DME enforces limits via fuel cut (not braking).

## Commands

```bash
pip install -e ".[dev]"                    # Install with dev dependencies
pytest                                     # Run all tests
pytest tests/test_safety.py                # Run a single test file
pytest tests/test_safety.py::test_name -v  # Run a single test
ruff check src/ tests/                     # Lint
ruff check --fix src/ tests/               # Auto-fix lint issues
slower                                     # Run the app (entry point)
slower --reset                             # Recovery: reset DME Vmax to factory
slower-watchdog                            # Standalone watchdog process
```

## Architecture

```
Phone (GPS) --> [WiFi / USB-C / BLE / Bluetooth Serial] --> BimmerDimmer (Pi) --> [K+DCAN USB] --> BMW DME (Vmax)
```

**Transport layer** (`transport/`): `GPSTransport` protocol with four implementations: `WiFiTransport` (HTTP POST), `BLETransport` (BlueZ GATT server via dbus-fast), `SPPTransport` (RFCOMM socket), `USBTransport` (USB-C tethering via network interface). All transports feed `GPSProvider`, which always uses the freshest valid fix. `TransportHealth` tracks per-transport state (healthy/degraded/lost).

**Data flow per control tick** (runs every 3s in a background thread):
1. `GPSProvider` receives position updates from any active transport, with validation (accuracy, speed jump, teleportation filters)
2. `SpeedLimitService` looks up the limit from OSM Overpass (default) or Google Roads API, with grid-based caching
3. `SpeedLimiterController._control_tick()` computes target Vmax = posted limit + offset, requires confirmation ticks before lowering
4. `SafetyManager.validate_vmax_change()` clamps to hard bounds and applies rate limiting
5. `ConnectionMonitor` checks K+DCAN health before allowing writes
6. `E90DME` writes the Vmax via UDS WriteDataByIdentifier with read-back verification

**Safety layer** (`bmw/safety.py`): Hard-coded, non-configurable limits: min 40 km/h, max 250 km/h, max decrease rate 50 km/h per second. GPS or device connection loss holds current Vmax during grace period, then caps at 120 km/h. `ConnectionMonitor` tracks health of all GPS transports and K+DCAN cable. DME write counter hard-stops at 1000 writes per session. Read-back verification after every write.

**Recovery** (`bmw/recovery.py`): Startup check detects stale Vmax from previous crashes. `slower --reset` CLI mode for manual recovery. `slower-watchdog` is a standalone process that resets DME if the main process dies (heartbeat file monitoring).

**BMW comms stack**: `KDCANConnection` (serial framing with XOR checksum) -> `UDSClient` (diagnostic services) -> `E90DME` (MSV70-specific DIDs and security access). Requires Extended Diagnostic Session (0x03) with TesterPresent keepalive every 2s.

**Web layer**: Flask app on port 5555 serves a mobile dashboard and REST API for GPS updates, status polling, and control commands (mode toggle with confirmation, offset, emergency override, BLE pairing).

## Key Design Decisions

- Default mode is MONITOR ONLY. Active DME writes require `limiter.active: true` in config plus a confirmation dialog.
- Config loaded from `config.yaml` (CWD), then `~/.config/slower/config.yaml`, with env var overrides (`SLOWER_CABLE_PORT`, `SLOWER_GOOGLE_API_KEY`, `SLOWER_ACTIVE`).
- All state objects and configs use dataclasses. Thread safety via `threading.Lock` in the controller.
- `from __future__ import annotations` is used in every module. Type hints use `X | Y` union syntax (Python 3.10+).
- The standalone watchdog (`slower_watchdog/`) intentionally does NOT import from the `slower` package to avoid shared failure modes.

## Writing Style

Never use em dashes (--) in any output. Use commas, periods, or parentheses instead. This applies to all written content, code comments, commit messages, and copy.
