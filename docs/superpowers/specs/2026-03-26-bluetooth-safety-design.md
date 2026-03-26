# BimmerDimmer v0.3.0 - Bluetooth Transport + Safety Hardening

## Summary

Add multi-transport GPS support (WiFi HTTP, BLE GATT, Bluetooth Serial) with automatic failover, plus comprehensive safety hardening to protect both the driver and the DME from connection failures, bad data, software crashes, and accidental misconfiguration.

Target platform: Raspberry Pi (BlueZ for all Bluetooth).

## 1. Transport Layer (Multi-Transport GPS)

### 1.1 GPSTransport Protocol

New abstraction that any transport implements:

```python
class GPSTransport(Protocol):
    name: str  # "wifi", "ble", "spp"

    def start(self) -> None: ...
    def stop(self) -> None: ...

    @property
    def is_healthy(self) -> bool: ...

    @property
    def last_fix_age_sec(self) -> float | None: ...
```

Each transport calls `GPSProvider.update()` when it receives a position. The provider doesn't care where the data came from.

### 1.2 WiFi Transport

Refactor the existing `POST /api/gps` endpoint in `web/server.py`. The endpoint stays in the Flask app but delegates to a `WiFiTransport` object that holds health state (last update time, error count). No behavioral change to the existing flow.

### 1.3 BLE Transport (Web Bluetooth)

BLE GATT server running on the Pi via BlueZ D-Bus (`dbus-fast` library).

**GATT Service:**
- Service UUID: custom (e.g., `0000fff0-0000-1000-8000-00805f9b34fb`)
- GPS Characteristic (write): receives JSON GPS payloads from the phone browser
- Status Characteristic (read/notify): sends limiter state back to the phone

**Phone side:** The existing `index.html` dashboard gains a "Connect Bluetooth" button. On click, it uses the Web Bluetooth API (`navigator.bluetooth.requestDevice()`) to pair with the Pi's GATT server, then writes GPS data to the characteristic instead of (or in addition to) the HTTP endpoint.

**Browser support:** Chrome and Edge on Android. Chrome on desktop. No Safari/iOS support for Web Bluetooth. This is documented as a known limitation; WiFi remains the universal fallback.

**BLE data format:** Same JSON structure as the HTTP endpoint:
```json
{
  "latitude": 42.3601,
  "longitude": -71.0589,
  "speed": 13.4,
  "heading": 180.0,
  "accuracy": 8.5
}
```

Characteristic max write size is 512 bytes (negotiated MTU). GPS payloads are well under this.

### 1.4 SPP Transport (Bluetooth Serial)

Classic Bluetooth RFCOMM server on the Pi using Python's `socket` module with `AF_BLUETOOTH`/`BTPROTO_RFCOMM` (no extra dependencies beyond PySerial for framing convenience).

**Protocol:** Newline-delimited JSON over RFCOMM. Same JSON structure as above. One position per line.

**Use case:** Companion mobile app (native or hybrid) that connects via Bluetooth Serial. This transport is for users who build or install a dedicated app rather than using the browser.

**RFCOMM channel:** Configurable, default 1.

### 1.5 Failover Logic (in GPSProvider)

`GPSProvider` is updated to:
- Accept updates from all active transports simultaneously
- Always use the most recent valid fix, regardless of which transport delivered it
- Track per-transport health: `last_seen` timestamp, consecutive error count, state (`healthy`/`degraded`/`lost`)
- Transport health timeout: 10 seconds with no data = `lost`
- Expose per-transport health in `LimiterState` for the dashboard
- If ALL transports go silent, existing GPS loss handling applies (grace period, then 120 km/h cap)

No priority ordering between transports. Freshest valid fix wins.

## 2. Safety Hardening

### 2.1 Connection Health Monitor

New `ConnectionMonitor` class in `bmw/safety.py` that tracks the health of every link in the system:

**GPS transports (WiFi, BLE, SPP):**
- Per-transport states: `healthy` (data within 10s), `degraded` (intermittent, 1-3 missed cycles), `lost` (> 10s silence)
- Aggregate GPS state: `healthy` if any transport is healthy, `lost` if all are lost

**K+DCAN cable:**
- Health based on TesterPresent keepalive responses
- 3 consecutive keepalive failures = `lost`
- When `lost`: stop all DME writes immediately, alert dashboard, log fault
- When `degraded`: reduce write frequency to every other tick, warn user

**Aggregate system health drives behavior:**
- All healthy: normal operation
- GPS lost: grace period, then 120 km/h cap (existing)
- K+DCAN degraded: warn user, reduce write frequency
- K+DCAN lost: cease all DME writes, alert user prominently

### 2.2 GPS Data Validation

New sanity checks in `GPSProvider.update()`:

1. **Accuracy filter:** Reject fixes with `accuracy_m > 100` (too imprecise for speed limit lookup). Log and discard, don't update position.
2. **Speed jump filter:** If the reported speed changes by more than 50 km/h from the previous fix within a single update interval, flag as suspect. Hold the previous speed value for one tick. If the next fix confirms the new speed, accept it.
3. **Teleportation filter:** Calculate implied speed from distance between consecutive fixes. If implied speed > 200 km/h and GPS-reported speed is much lower, discard the fix (GPS jumped).
4. **Speed limit sanity:** In `SpeedLimitService`, if the returned limit changes by more than 40 mph between consecutive lookups AND distance traveled is < 100m, hold the previous limit for one tick. Likely bad OSM data or a mismatched road segment.

All rejected fixes are logged at WARNING level with the reason.

### 2.3 Conservative DME Write Strategy

Rules enforced in `SpeedLimiterController._apply_vmax()`:

1. **Fresh data requirement:** Never write Vmax below `GPS_LOSS_CAP_KMH` (120 km/h) unless we have a confirmed GPS fix < 5 seconds old AND a confirmed speed limit from the lookup service.
2. **Confirmation ticks:** Before lowering Vmax to a new value, the target must be stable for 2 consecutive control ticks (6 seconds at default interval). This filters transient bad data. Raising Vmax (less restrictive) applies immediately.
3. **Existing rate limiter:** Max 50 km/h decrease per second (unchanged).

### 2.4 DME Protection (Anti-Brick)

**Write counter:**
- Track total `WriteDataByIdentifier` calls to the DME per session
- Dashboard shows current write count
- At 500 writes: log warning, show dashboard alert
- At 1000 writes: hard-stop all DME writes for the session. User must restart to continue.
- Rationale: MSV70 EEPROM has finite write endurance (typically 100k+ cycles, but we don't want to erode it unnecessarily during development/testing)

**Session validation:**
- Before every write, verify the UDS session is still Extended Diagnostic (0x03)
- If the session has timed out or dropped, re-initialize (re-enter session, re-authenticate security access) before attempting the write
- If re-initialization fails, stop writing and alert the user

**Read-back verification:**
- After writing a new Vmax value, immediately read it back via `ReadDataByIdentifier`
- If the read-back value doesn't match what was written (within 1 km/h tolerance), log a CRITICAL fault, stop all writes, alert the user
- This catches corrupted writes, wrong DID addressing, or unexpected DME behavior

**Parameter bounds (final guard):**
- At the `UDSClient` or `E90DME` layer, reject any `WriteDataByIdentifier` call for Vmax DIDs with values outside 40-250 km/h
- This is a last-resort sanity check independent of the safety manager
- If triggered, it indicates a bug in the higher layers; log CRITICAL and stop writes

### 2.5 Software Watchdog

Separate lightweight process: `slower-watchdog`.

**Design:**
- Runs as a systemd service alongside the main `slower` service
- Main process writes a heartbeat file (touches `/tmp/slower-heartbeat`) every 2 seconds
- Watchdog checks the heartbeat file age every 5 seconds
- If heartbeat is stale > 10 seconds:
  1. Log that the main process appears dead
  2. Open its own K+DCAN connection (using the same serial port config)
  3. Enter Extended Diagnostic Session
  4. Send `disable_vmax` command
  5. Close connection and continue monitoring (in case main process restarts)

**Implementation constraints:**
- Intentionally simple: < 100 lines, no dependencies beyond `pyserial`
- Does NOT import from `slower` package (to avoid shared failure modes)
- Has its own minimal K+DCAN framing and UDS command construction
- Config (serial port, baud rate) read from a simple env var or config snippet, not the full `config.yaml`

**Limitations:**
- Cannot help with sudden power loss (Pi unplugged). Only covers software crashes.
- If the K+DCAN cable is physically disconnected, the watchdog also cannot reach the DME.

### 2.6 Recovery Command: `slower --reset`

Standalone CLI mode added to `main.py`:

```bash
slower --reset                    # Connect to DME, disable Vmax, exit
slower --reset --port /dev/ttyUSB0  # Explicit serial port
```

Behavior:
1. Connect to K+DCAN cable
2. Enter Extended Diagnostic Session
3. Read current Vmax and display it
4. Send `disable_vmax` command
5. Read back Vmax to confirm it was cleared
6. Print result and exit

For use after crashes, power loss, or any situation where the driver suspects a stale Vmax is active.

### 2.7 Startup Recovery Check

On every normal launch (not `--reset`), after DME connection is established:
1. Read current Vmax from DME
2. If Vmax is below `GPS_LOSS_CAP_KMH` (120 km/h), display a prominent warning:
   ```
   WARNING: DME has a stale Vmax limit of 85 km/h (possibly from a previous crash).
   The limiter will reset this to factory default before starting.
   ```
3. Automatically send `disable_vmax` to clear the stale value
4. Log the event as a fault for the record
5. Proceed with normal startup

## 3. Dashboard Updates

### 3.1 Transport Health Indicators

New status chips in the status bar for each active transport:
- `WiFi: ON/OFF`
- `BLE: ON/OFF` (with connected device count)
- `SPP: ON/OFF`

Each chip uses the same color scheme as existing GPS/DME chips.

### 3.2 K+DCAN Health

The existing DME status chip gains a `degraded` state (yellow/warn color) in addition to on/off.

### 3.3 DME Write Counter

Small counter shown when in Active Mode: "Writes: 42 / 1000". Turns yellow at 500, red at 1000.

### 3.4 Degraded Mode Banner

When the system is operating in any degraded state (GPS loss cap active, K+DCAN degraded, high write count), show a persistent yellow warning banner describing the degradation.

### 3.5 Active Mode Confirmation

When the user taps the mode button to switch from Monitor to Active, show a confirmation dialog:
> "Active Mode will send commands to your DME. Are you sure?"

With "Cancel" and "Enable Active Mode" buttons. Prevents accidental activation.

### 3.6 Web Bluetooth Connect Button

New button in the controls area: "Connect Bluetooth". On tap:
1. Calls `navigator.bluetooth.requestDevice()` with the BimmerDimmer GATT service filter
2. Connects to the GATT server
3. Begins writing GPS data to the characteristic
4. Button changes to "Bluetooth Connected" (green) or shows error

Hidden on browsers that don't support Web Bluetooth.

## 4. Configuration

New config sections in `config.yaml`:

```yaml
transports:
  wifi: true            # HTTP endpoint (default: true)
  ble: true             # BLE GATT server (default: true)
  spp: true             # Bluetooth Serial (default: true)
  spp_channel: 1        # RFCOMM channel number

safety:
  max_gps_accuracy_m: 100       # Reject GPS fixes less precise than this
  max_speed_jump_kmh: 50        # Flag speed changes larger than this
  write_confirm_ticks: 2        # Ticks to confirm before lowering Vmax
  max_writes_per_session: 1000  # Hard stop for DME writes
  watchdog_heartbeat_sec: 2     # How often main process writes heartbeat
  watchdog_timeout_sec: 10      # How long before watchdog intervenes
```

New dataclasses: `TransportConfig`, `SafetyConfig` added to `config.py`.

## 5. New Files

```
src/slower/
  transport/
    __init__.py          # GPSTransport protocol definition
    wifi.py              # WiFi HTTP transport (refactored from web/server.py)
    ble.py               # BLE GATT server via dbus-fast + BlueZ
    spp.py               # Bluetooth Serial RFCOMM server
    health.py            # Per-transport health tracking
  bmw/
    watchdog.py          # Heartbeat writer (called by main process)
    recovery.py          # --reset command logic, startup recovery check
src/slower_watchdog/
  __init__.py
  main.py               # Standalone watchdog (separate entry point, no slower imports)
```

## 6. Dependencies

**New runtime dependencies:**
- `dbus-fast` - Async D-Bus client for BlueZ BLE GATT server. Pure Python, no C extensions. Linux only (which is fine, target is Raspberry Pi).

**No new dependencies for SPP.** Python's `socket` module supports `AF_BLUETOOTH`/`BTPROTO_RFCOMM` on Linux natively. PySerial (already a dependency) can also be used for RFCOMM if preferred.

**No new dependencies for the watchdog.** It uses only `pyserial` (already a dependency) and stdlib.

## 7. Entry Points

Updated in `pyproject.toml`:

```toml
[project.scripts]
slower = "slower.main:main"
slower-watchdog = "slower_watchdog.main:main"
```

## 8. GitHub Repository Changes

- **About/description:** "GPS-based speed limiter for BMW E90 325xi. Dynamically adjusts DME Vmax via K+DCAN cable using GPS speed limit data over WiFi or Bluetooth."
- **Visibility:** Public
- **License:** MIT (already in place)
- **Topics:** `bmw`, `e90`, `obd2`, `raspberry-pi`, `gps`, `speed-limiter`, `bluetooth`, `ble`

## 9. Non-Goals

- iOS Web Bluetooth support (Apple does not implement the Web Bluetooth API in Safari)
- Building a native mobile companion app (SPP transport supports one, but building it is out of scope)
- CAN bus sniffing or passive speed reading (we use GPS for speed, DME for Vmax control)
- Multi-vehicle support (this is specifically for E90 N52 / MSV70)
- OTA updates for the Pi (standard package management is sufficient)
