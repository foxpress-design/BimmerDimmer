# BimmerDimmer

GPS-based speed limiter for 2006 BMW 325xi (E90 / N52 engine / MSV70 DME).

Connects to your car's DME via a K+DCAN USB cable and dynamically adjusts
the Vmax (top speed) parameter based on posted speed limits from GPS data.

## What's New (v0.2.0)

- Renamed project to BimmerDimmer
- GPS or device connection loss now caps speed at 120 km/h instead of releasing the limiter
- Raised minimum Vmax floor from 25 km/h to 40 km/h

## How It Works

1. Your phone provides GPS coordinates via a web dashboard (browser Geolocation API)
2. The system looks up the posted speed limit for your location (OpenStreetMap or Google Maps)
3. It sends UDS commands over the K+DCAN cable to set the DME's Vmax parameter
4. The DME enforces the speed limit by cutting fuel injection (throttle-cut, NOT braking)

```
Phone (GPS) --> [WiFi] --> Slower (Python) --> [K+DCAN USB] --> BMW DME (Vmax)
```

## Hardware Required

- **K+DCAN USB cable** - FTDI FT232-based cable with DCAN switch (set to position 2/DCAN)
- **Laptop or Raspberry Pi** - runs Slower, connected to the K+DCAN cable via USB
- **Phone** - any smartphone with a modern browser (provides GPS via Geolocation API)
- **WiFi** - phone and laptop on the same network (or laptop as a hotspot)

The K+DCAN cable plugs into the OBD-II port under the dashboard (driver's side).

## Installation

```bash
pip install -e .
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
```

Open the dashboard on your phone: `http://<laptop-ip>:5555`

## Configuration

Copy and edit `config.yaml`:

- `cable.port` - Serial port for your K+DCAN cable (`/dev/ttyUSB0`, `COM3`, etc.)
- `limiter.active` - Set to `true` to enable DME writes (default: `false` / monitor only)
- `limiter.offset_mph` - Allow this many mph above the posted limit (default: `5`)
- `speed_limits.primary` - `"osm"` (free) or `"google"` (requires API key)

## Architecture

```
src/slower/
  main.py              # Entry point, CLI args, startup
  config.py            # YAML config loader with env var overrides
  bmw/
    connection.py      # K+DCAN USB serial connection (DCAN framing)
    uds.py             # UDS protocol (diagnostic sessions, read/write DIDs)
    e90_dme.py         # MSV70 DME specifics (Vmax DIDs, security access)
    safety.py          # Hard safety limits, rate limiting, fault handling
  gps/
    provider.py        # GPS position from phone browser
    speed_limits.py    # OSM Overpass / Google Roads API speed limit lookup
  limiter/
    controller.py      # Main control loop bridging GPS to DME
  web/
    server.py          # Flask API + dashboard server
    templates/
      index.html       # Mobile dashboard (speed, limit, controls)
```

## Safety

- **Default mode is MONITOR ONLY** - no DME commands are sent unless you explicitly enable active mode
- Hard minimum Vmax floor of 40 km/h (~25 mph) that cannot be overridden
- Rate limiting prevents sudden large decreases in Vmax
- GPS or device connection loss triggers a grace period, then caps speed at 120 km/h
- Emergency override button on the dashboard instantly disables the limiter
- 5 consecutive DME communication failures trigger automatic override
- The Vmax limiter works by fuel cut only - it does NOT apply brakes

## BMW Technical Details

- **ECU**: MSV70 (Siemens/Continental) - the DME in 2006 E90 N52 engines
- **Protocol**: UDS over DCAN (CAN bus at 500 kbps via K+DCAN cable at 115200 baud)
- **Security**: SA Level 0x01 challenge-response required for write access
- **Vmax**: Coded via WriteDataByIdentifier (0x2E) to MSV70-specific DIDs
- **Session**: Extended diagnostic session (0x03) with TesterPresent keepalive

NCS Expert equivalent: `GESCHWINDIGKEITSBEGRENZUNG` -> `VMAX` / `VMAX_AKT` in the DME SGBD.

## Disclaimer

This software modifies engine control parameters on a real vehicle. Incorrect use can
create dangerous driving conditions. This is experimental software provided as-is with
no warranty. You are solely responsible for any consequences of using this software.
Always maintain full control of your vehicle. Test thoroughly in a safe environment
before any road use.
