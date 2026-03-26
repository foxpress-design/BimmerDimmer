# BimmerDimmer

GPS-based speed limiter for 2006 BMW 325xi (E90 / N52 engine / MSV70 DME).

Connects to your car's DME via a K+DCAN USB cable and dynamically adjusts
the Vmax (top speed) parameter based on posted speed limits from GPS data.
Supports WiFi, Bluetooth Low Energy, and Bluetooth Serial for phone connectivity.

## Safety First

BimmerDimmer is built with multiple layers of protection for both the driver and the vehicle:

**Driver safety:**
- **Default mode is MONITOR ONLY.** No DME commands are sent unless you explicitly enable active mode with a confirmation prompt.
- Hard minimum Vmax floor of 40 km/h (~25 mph) that cannot be overridden by any configuration.
- GPS or device connection loss triggers a grace period, then enforces a hard cap of 120 km/h. The system never leaves you without a speed limit when connections fail.
- Rate limiting prevents sudden large decreases in Vmax (max 50 km/h per second).
- Emergency override button on the dashboard instantly disables the limiter.
- 5 consecutive DME communication failures trigger automatic override (limiter released).
- GPS data validation rejects inaccurate fixes (>100m), impossible speed jumps, and teleportation glitches.
- Speed limit changes must be confirmed across multiple control ticks before Vmax is lowered, filtering transient bad data.
- The Vmax limiter works by fuel cut only. It does NOT apply brakes.

**Vehicle protection (anti-brick):**
- Write counter tracks total DME writes per session. Warns at 500, hard-stops at 1000 to protect EEPROM endurance.
- Read-back verification after every Vmax write. If the DME reports a different value than what was written, all writes are disabled immediately.
- Session validation before every write confirms the UDS diagnostic session is still active.
- Parameter bounds enforced at the UDS layer as a final guard: 40-250 km/h only.
- Startup recovery check detects stale Vmax values left by a previous crash and clears them automatically.
- Software watchdog (`slower-watchdog`) runs as a separate process. If the main process dies, the watchdog resets the DME to factory Vmax.
- `slower --reset` recovery command connects to the DME, clears any Vmax limit, and exits. Use after power loss or crashes.

## What's New (v0.3.1)

- Added `GPSTransport` protocol defining the interface all transport implementations must satisfy
- Added `TransportHealth` for per-transport state tracking (unknown, healthy, degraded, lost) with configurable timeout and failure threshold
- Added 6 tests covering all `TransportHealth` state transitions

## What's New (v0.3.0)

- Multi-transport GPS: WiFi HTTP, Bluetooth Low Energy (BLE), and Bluetooth Serial (SPP)
- Automatic failover between transports (freshest GPS fix wins)
- GPS data validation (accuracy, speed jump, and teleportation filters)
- DME write counter with session hard-stop at 1000 writes
- Read-back verification after every DME write
- Startup recovery check for stale Vmax values
- Software watchdog process (`slower-watchdog`)
- `slower --reset` recovery command
- Connection health monitoring for all links (GPS transports, K+DCAN)
- Confirmation ticks before lowering Vmax (filters transient bad data)
- Active Mode confirmation dialog on dashboard
- Web Bluetooth connect button on dashboard
- Transport health indicators on dashboard

## How It Works

1. Your phone provides GPS coordinates via the web dashboard or Bluetooth
2. The system looks up the posted speed limit for your location (OpenStreetMap or Google Maps)
3. Safety checks validate the GPS data and confirm the speed limit across multiple ticks
4. It sends UDS commands over the K+DCAN cable to set the DME's Vmax parameter
5. The DME enforces the speed limit by cutting fuel injection (throttle-cut, NOT braking)

```
Phone (GPS) --> [WiFi / BLE / Bluetooth Serial] --> BimmerDimmer (Pi) --> [K+DCAN USB] --> BMW DME (Vmax)
```

## Hardware Required

- **K+DCAN USB cable** - FTDI FT232-based cable with DCAN switch (set to position 2/DCAN)
- **Raspberry Pi** - runs BimmerDimmer, connected to the K+DCAN cable via USB
- **Phone** - any smartphone with a modern browser (provides GPS via Geolocation API)
- **Connectivity** - WiFi (phone and Pi on the same network or Pi as hotspot), Bluetooth Low Energy (Chrome/Edge on Android), or Bluetooth Serial (companion app)

The K+DCAN cable plugs into the OBD-II port under the dashboard (driver's side).

## Installation

```bash
pip install -e .
```

For the watchdog (recommended for active mode):

```bash
# Run as a separate systemd service alongside the main process
slower-watchdog --port /dev/ttyUSB0
```

## Usage

```bash
# Monitor-only mode (no DME commands, GPS + dashboard only)
slower --no-dme

# With DME connection in monitor mode (reads DME but doesn't write)
slower --monitor

# Full active mode (requires explicit config change)
# Edit config.yaml: limiter.active: true
slower

# Custom config file
slower --config /path/to/config.yaml

# Recovery: reset DME Vmax to factory after a crash or power loss
slower --reset
```

Open the dashboard on your phone: `http://<pi-ip>:5555`

## Configuration

Copy and edit `config.yaml`:

- `cable.port` - Serial port for your K+DCAN cable (`/dev/ttyUSB0`, `COM3`, etc.)
- `limiter.active` - Set to `true` to enable DME writes (default: `false` / monitor only)
- `limiter.offset_mph` - Allow this many mph above the posted limit (default: `5`)
- `speed_limits.primary` - `"osm"` (free) or `"google"` (requires API key)
- `transports.ble` - Enable BLE GATT server (default: `true`)
- `transports.spp` - Enable Bluetooth Serial server (default: `true`)
- `safety.max_writes_per_session` - DME write limit per session (default: `1000`)
- `safety.write_confirm_ticks` - Ticks to confirm before lowering Vmax (default: `2`)

## BMW Technical Details

- **ECU**: MSV70 (Siemens/Continental), the DME in 2006 E90 N52 engines
- **Protocol**: UDS over DCAN (CAN bus at 500 kbps via K+DCAN cable at 115200 baud)
- **Security**: SA Level 0x01 challenge-response required for write access
- **Vmax**: Coded via WriteDataByIdentifier (0x2E) to MSV70-specific DIDs
- **Session**: Extended diagnostic session (0x03) with TesterPresent keepalive

NCS Expert equivalent: `GESCHWINDIGKEITSBEGRENZUNG` -> `VMAX` / `VMAX_AKT` in the DME SGBD.

## Who's This For?

**Speed demons who want a safety net.** You know you have a lead foot. BimmerDimmer gives you a configurable ceiling that follows the posted limit, so you can enjoy the drive without watching the speedometer constantly. Set your offset, let the DME handle the rest.

**Anyone concerned about speeding tickets.** Speed cameras, school zones, construction zones. BimmerDimmer reads the posted limit from OpenStreetMap or Google Maps and keeps your Vmax in check. One less thing to worry about on your commute.

**Parents of new drivers.** Your teenager just got their license and inherited the family E90. BimmerDimmer lets you set a hard speed ceiling that the car enforces at the engine level. No app to disable, no setting to change from the driver's seat. Monitor mode lets you track without limiting, active mode sets the boundary.

**Track day preppers who drive to the track.** You removed the speed limiter for the track, now you need it back for the highway home. BimmerDimmer makes the Vmax coding dynamic instead of a one-time NCS Expert session.

**Tinkerers and BMW coding enthusiasts.** If you've ever opened NCS Expert, ISTA, or Tool32, you'll appreciate having programmatic UDS access to the MSV70. BimmerDimmer is open source, MIT licensed, and built to be readable. Fork it, extend it, learn from it.

## Disclaimer

This software modifies engine control parameters on a real vehicle. Incorrect use can
create dangerous driving conditions. This is experimental software provided as-is with
no warranty. You are solely responsible for any consequences of using this software.
Always maintain full control of your vehicle. Test thoroughly in a safe environment
before any road use.
