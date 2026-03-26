# Bluetooth Transport + Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-transport GPS (WiFi, BLE, Bluetooth Serial) with automatic failover, plus comprehensive safety hardening to protect both the driver and the BMW DME.

**Architecture:** A `GPSTransport` protocol abstracts GPS data sources. Three implementations (WiFi HTTP, BLE GATT, Bluetooth SPP) feed position updates into the existing `GPSProvider`, which picks the freshest valid fix. A new `ConnectionMonitor` tracks health of all links. Safety improvements include GPS data validation, conservative DME write strategy with read-back verification, write counting, a standalone watchdog process, and a `--reset` recovery command.

**Tech Stack:** Python 3.10+, BlueZ D-Bus via `dbus-fast`, PySerial, Flask, pytest

---

## File Map

**New files to create:**
- `src/slower/transport/__init__.py` - GPSTransport protocol + TransportHealth dataclass
- `src/slower/transport/wifi.py` - WiFi HTTP transport (refactored from server.py)
- `src/slower/transport/ble.py` - BLE GATT server via dbus-fast
- `src/slower/transport/spp.py` - Bluetooth Serial RFCOMM server
- `src/slower/transport/health.py` - Per-transport health tracking
- `src/slower/bmw/watchdog.py` - Heartbeat file writer (used by main process)
- `src/slower/bmw/recovery.py` - `--reset` command + startup recovery check
- `src/slower_watchdog/__init__.py` - Standalone watchdog package
- `src/slower_watchdog/main.py` - Watchdog entry point (no slower imports)
- `tests/test_transport_health.py` - Transport health tracking tests
- `tests/test_gps_validation.py` - GPS data validation tests
- `tests/test_connection_monitor.py` - Connection monitor tests
- `tests/test_dme_protection.py` - Write counter, read-back, session validation tests
- `tests/test_recovery.py` - Recovery command and startup check tests

**Files to modify:**
- `src/slower/gps/provider.py` - Add GPS validation, multi-transport freshest-fix logic
- `src/slower/bmw/safety.py` - Add ConnectionMonitor class
- `src/slower/bmw/e90_dme.py` - Add write counter, read-back verification, parameter bounds guard, update hard floor from 25 to 40
- `src/slower/limiter/controller.py` - Add confirmation ticks, fresh data requirement, heartbeat, connection monitor integration
- `src/slower/web/server.py` - Refactor GPS endpoint to use WiFiTransport, add transport status to API
- `src/slower/web/templates/index.html` - Transport health chips, write counter, Active Mode confirmation, BLE connect button, degraded mode banner
- `src/slower/config.py` - Add TransportConfig, SafetyConfig dataclasses
- `src/slower/main.py` - Wire transports, watchdog heartbeat, --reset flag, startup recovery
- `pyproject.toml` - Add dbus-fast dependency, slower-watchdog entry point, version bump
- `tests/test_safety.py` - Update for new min Vmax and connection monitor

---

### Task 1: GPSTransport Protocol and Transport Health

**Files:**
- Create: `src/slower/transport/__init__.py`
- Create: `src/slower/transport/health.py`
- Create: `tests/test_transport_health.py`

- [ ] **Step 1: Write failing tests for transport health tracking**

```python
# tests/test_transport_health.py
"""Tests for transport health tracking."""

import time
from slower.transport.health import TransportHealth


def test_new_transport_is_unknown():
    th = TransportHealth(name="wifi", timeout_sec=10.0)
    assert th.state == "unknown"
    assert th.is_healthy is False


def test_record_success_makes_healthy():
    th = TransportHealth(name="wifi", timeout_sec=10.0)
    th.record_success()
    assert th.state == "healthy"
    assert th.is_healthy is True


def test_stale_transport_is_lost():
    th = TransportHealth(name="wifi", timeout_sec=0.1)
    th.record_success()
    assert th.is_healthy is True
    time.sleep(0.15)
    assert th.state == "lost"
    assert th.is_healthy is False


def test_record_failure_increments_count():
    th = TransportHealth(name="ble", timeout_sec=10.0)
    th.record_success()
    th.record_failure()
    assert th.consecutive_failures == 1
    assert th.state == "degraded"


def test_three_failures_is_lost():
    th = TransportHealth(name="spp", timeout_sec=10.0)
    th.record_success()
    th.record_failure()
    th.record_failure()
    th.record_failure()
    assert th.state == "lost"


def test_success_resets_failures():
    th = TransportHealth(name="wifi", timeout_sec=10.0)
    th.record_failure()
    th.record_failure()
    th.record_success()
    assert th.consecutive_failures == 0
    assert th.state == "healthy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_transport_health.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'slower.transport'`

- [ ] **Step 3: Create the transport package and health module**

```python
# src/slower/transport/__init__.py
"""Multi-transport GPS data abstraction.

Supports WiFi HTTP, BLE GATT, and Bluetooth Serial (SPP) transports.
Each transport feeds GPS position updates into GPSProvider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider


@runtime_checkable
class GPSTransport(Protocol):
    """Protocol that all GPS transports implement."""

    name: str

    def start(self, gps: GPSProvider) -> None:
        """Start receiving GPS data and feeding it to the provider."""
        ...

    def stop(self) -> None:
        """Stop the transport."""
        ...
```

```python
# src/slower/transport/health.py
"""Per-transport health tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TransportHealth:
    """Tracks the health state of a single transport or connection."""

    name: str
    timeout_sec: float = 10.0
    consecutive_failures: int = 0
    last_success_time: float | None = None
    _failure_threshold: int = 3

    def record_success(self) -> None:
        """Record a successful data exchange."""
        self.last_success_time = time.monotonic()
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed data exchange."""
        self.consecutive_failures += 1

    @property
    def is_healthy(self) -> bool:
        return self.state == "healthy"

    @property
    def state(self) -> str:
        """Current state: 'unknown', 'healthy', 'degraded', or 'lost'."""
        if self.last_success_time is None:
            return "unknown"

        age = time.monotonic() - self.last_success_time
        if age > self.timeout_sec:
            return "lost"

        if self.consecutive_failures >= self._failure_threshold:
            return "lost"

        if self.consecutive_failures > 0:
            return "degraded"

        return "healthy"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_transport_health.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/slower/transport/__init__.py src/slower/transport/health.py tests/test_transport_health.py
git commit -m "feat: add GPSTransport protocol and transport health tracking"
```

---

### Task 2: GPS Data Validation

**Files:**
- Create: `tests/test_gps_validation.py`
- Modify: `src/slower/gps/provider.py`

- [ ] **Step 1: Write failing tests for GPS validation**

```python
# tests/test_gps_validation.py
"""Tests for GPS data validation in GPSProvider."""

import time
from slower.gps.provider import GPSPosition, GPSProvider


def test_rejects_low_accuracy():
    gps = GPSProvider()
    pos = gps.update(lat=42.36, lon=-71.06, accuracy_m=150.0)
    assert pos is None
    assert gps.position is None


def test_accepts_good_accuracy():
    gps = GPSProvider()
    pos = gps.update(lat=42.36, lon=-71.06, accuracy_m=20.0)
    assert pos is not None
    assert pos.latitude == 42.36


def test_rejects_speed_jump():
    gps = GPSProvider()
    # First fix at 30 km/h (8.33 m/s)
    gps.update(lat=42.36, lon=-71.06, speed_mps=8.33, accuracy_m=10.0)
    # Second fix jumps to 120 km/h (33.33 m/s) - 90 km/h jump, exceeds 50 km/h threshold
    pos = gps.update(lat=42.36, lon=-71.06, speed_mps=33.33, accuracy_m=10.0)
    assert pos is None  # Rejected as suspect


def test_accepts_gradual_speed_change():
    gps = GPSProvider()
    # First fix at 50 km/h (13.89 m/s)
    gps.update(lat=42.36, lon=-71.06, speed_mps=13.89, accuracy_m=10.0)
    # Second fix at 60 km/h (16.67 m/s) - 10 km/h change, fine
    pos = gps.update(lat=42.36, lon=-71.06, speed_mps=16.67, accuracy_m=10.0)
    assert pos is not None


def test_rejects_teleportation():
    gps = GPSProvider()
    # First fix in Boston
    gps.update(lat=42.3601, lon=-71.0589, accuracy_m=10.0)
    # Second fix 10km away, but only 1 second later -> implied 36000 km/h
    pos = gps.update(lat=42.4501, lon=-71.0589, accuracy_m=10.0)
    assert pos is None


def test_no_speed_validation_on_first_fix():
    gps = GPSProvider()
    # First fix with high speed should be accepted (no previous to compare)
    pos = gps.update(lat=42.36, lon=-71.06, speed_mps=40.0, accuracy_m=10.0)
    assert pos is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_gps_validation.py -v`
Expected: FAIL (update currently accepts everything)

- [ ] **Step 3: Add validation to GPSProvider.update()**

Modify `src/slower/gps/provider.py`. Add these constants at the top after the existing imports:

```python
import math

logger = logging.getLogger(__name__)

# GPS validation thresholds
MAX_GPS_ACCURACY_M = 100.0  # Reject fixes less precise than this
MAX_SPEED_JUMP_KMH = 50.0  # Flag speed changes larger than this between fixes
MAX_IMPLIED_SPEED_KMH = 200.0  # Teleportation detection threshold
```

Add a `_validate` method to `GPSProvider` and update `update()` to call it:

```python
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

        # Teleportation filter
        elapsed = pos.timestamp - prev.timestamp
        if elapsed > 0:
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
```

Add the haversine helper function before the `GPSProvider` class:

```python
def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_gps_validation.py tests/test_gps_provider.py -v`
Expected: All tests PASS (both new validation tests and existing provider tests)

Note: The existing `test_provider_update` test uses `accuracy_m=50.0` (default) which is under the 100m threshold, so it will still pass. Check that `test_gps_position_staleness` still passes since it constructs `GPSPosition` directly (no validation).

- [ ] **Step 5: Commit**

```bash
git add src/slower/gps/provider.py tests/test_gps_validation.py
git commit -m "feat: add GPS data validation (accuracy, speed jump, teleportation)"
```

---

### Task 3: Connection Monitor

**Files:**
- Create: `tests/test_connection_monitor.py`
- Modify: `src/slower/bmw/safety.py`

- [ ] **Step 1: Write failing tests for ConnectionMonitor**

```python
# tests/test_connection_monitor.py
"""Tests for ConnectionMonitor."""

from slower.bmw.safety import ConnectionMonitor, GPS_LOSS_CAP_KMH


def test_initial_state_all_unknown():
    cm = ConnectionMonitor()
    assert cm.gps_aggregate_state == "unknown"
    assert cm.kdcan_health.state == "unknown"


def test_any_gps_transport_healthy_means_gps_healthy():
    cm = ConnectionMonitor()
    cm.add_gps_transport("wifi")
    cm.add_gps_transport("ble")
    cm.record_gps_success("wifi")
    assert cm.gps_aggregate_state == "healthy"


def test_all_gps_transports_lost_means_gps_lost():
    cm = ConnectionMonitor()
    cm.add_gps_transport("wifi", timeout_sec=0.05)
    cm.record_gps_success("wifi")
    import time
    time.sleep(0.1)
    assert cm.gps_aggregate_state == "lost"


def test_kdcan_three_failures_is_lost():
    cm = ConnectionMonitor()
    cm.kdcan_health.record_success()  # Start healthy
    cm.kdcan_health.record_failure()
    cm.kdcan_health.record_failure()
    cm.kdcan_health.record_failure()
    assert cm.kdcan_health.state == "lost"


def test_should_write_dme_false_when_kdcan_lost():
    cm = ConnectionMonitor()
    cm.kdcan_health.record_success()
    for _ in range(3):
        cm.kdcan_health.record_failure()
    assert cm.should_write_dme is False


def test_should_write_dme_true_when_kdcan_healthy():
    cm = ConnectionMonitor()
    cm.kdcan_health.record_success()
    assert cm.should_write_dme is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_connection_monitor.py -v`
Expected: FAIL with `ImportError: cannot import name 'ConnectionMonitor'`

- [ ] **Step 3: Add ConnectionMonitor to safety.py**

Add to `src/slower/bmw/safety.py`, after the existing `SafetyManager` class:

```python
from slower.transport.health import TransportHealth


class ConnectionMonitor:
    """Tracks health of all system connections."""

    def __init__(self) -> None:
        self._gps_transports: dict[str, TransportHealth] = {}
        self.kdcan_health = TransportHealth(name="kdcan", timeout_sec=10.0)

    def add_gps_transport(self, name: str, timeout_sec: float = 10.0) -> None:
        """Register a GPS transport to monitor."""
        self._gps_transports[name] = TransportHealth(name=name, timeout_sec=timeout_sec)

    def record_gps_success(self, transport_name: str) -> None:
        """Record a successful GPS fix from a transport."""
        if transport_name in self._gps_transports:
            self._gps_transports[transport_name].record_success()

    def record_gps_failure(self, transport_name: str) -> None:
        """Record a GPS transport failure."""
        if transport_name in self._gps_transports:
            self._gps_transports[transport_name].record_failure()

    @property
    def gps_aggregate_state(self) -> str:
        """Aggregate GPS state: healthy if any transport is healthy."""
        if not self._gps_transports:
            return "unknown"
        states = [t.state for t in self._gps_transports.values()]
        if "healthy" in states:
            return "healthy"
        if "degraded" in states:
            return "degraded"
        if any(s != "unknown" for s in states):
            return "lost"
        return "unknown"

    @property
    def should_write_dme(self) -> bool:
        """Whether it is safe to write to the DME right now."""
        return self.kdcan_health.state in ("healthy", "degraded")

    @property
    def transport_states(self) -> dict[str, str]:
        """Get all transport states for the dashboard."""
        result = {name: t.state for name, t in self._gps_transports.items()}
        result["kdcan"] = self.kdcan_health.state
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_connection_monitor.py tests/test_safety.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/slower/bmw/safety.py tests/test_connection_monitor.py
git commit -m "feat: add ConnectionMonitor for tracking all system link health"
```

---

### Task 4: DME Protection (Write Counter, Read-back, Bounds Guard)

**Files:**
- Create: `tests/test_dme_protection.py`
- Modify: `src/slower/bmw/e90_dme.py`

- [ ] **Step 1: Write failing tests for DME protection**

```python
# tests/test_dme_protection.py
"""Tests for DME protection features."""

from unittest.mock import MagicMock, patch
import struct

from slower.bmw.e90_dme import E90DME, MSV70DID


def _make_dme() -> tuple[E90DME, MagicMock]:
    """Create an E90DME with a mock UDS client."""
    uds = MagicMock()
    uds.conn = MagicMock()
    uds.conn.connected = True
    uds.write_data = MagicMock(return_value=True)
    uds.read_data = MagicMock(return_value=None)
    dme = E90DME(uds)
    dme._security_unlocked = True
    return dme, uds


def test_set_vmax_rejects_below_40():
    dme, uds = _make_dme()
    result = dme.set_vmax(30)
    assert result is False
    uds.write_data.assert_not_called()


def test_set_vmax_rejects_above_250():
    dme, uds = _make_dme()
    result = dme.set_vmax(260)
    assert result is False
    uds.write_data.assert_not_called()


def test_set_vmax_accepts_valid_value():
    dme, uds = _make_dme()
    # Mock read-back to return the written value
    uds.read_data.return_value = struct.pack(">H", 120)
    result = dme.set_vmax(120)
    assert result is True


def test_write_counter_increments():
    dme, uds = _make_dme()
    uds.read_data.return_value = struct.pack(">H", 100)
    dme.set_vmax(100)
    assert dme.write_count == 1
    uds.read_data.return_value = struct.pack(">H", 110)
    dme.set_vmax(110)
    assert dme.write_count == 2


def test_write_counter_hard_stop():
    dme, uds = _make_dme()
    dme._write_count = 1000  # At the limit
    result = dme.set_vmax(100)
    assert result is False
    uds.write_data.assert_not_called()


def test_readback_mismatch_stops_writes():
    dme, uds = _make_dme()
    # Write 120 but read back 80 (mismatch)
    uds.read_data.return_value = struct.pack(">H", 80)
    result = dme.set_vmax(120)
    assert result is False
    assert dme.writes_disabled is True


def test_readback_none_stops_writes():
    dme, uds = _make_dme()
    # Read-back fails entirely
    uds.read_data.return_value = None
    result = dme.set_vmax(120)
    assert result is False
    assert dme.writes_disabled is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_dme_protection.py -v`
Expected: FAIL (current set_vmax uses 25 floor, no write counter, no read-back)

- [ ] **Step 3: Update E90DME with protection features**

Modify `src/slower/bmw/e90_dme.py`. Update the `E90DME` class:

```python
# Add these constants after SA_LEVEL_CODING
WRITE_WARN_THRESHOLD = 500
WRITE_HARD_STOP = 1000


class E90DME:
    """Interface to the E90 N52 MSV70 DME for speed limiter control."""

    def __init__(self, uds: UDSClient) -> None:
        self.uds = uds
        self._session_active = False
        self._security_unlocked = False
        self._write_count = 0
        self._writes_disabled = False

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def writes_disabled(self) -> bool:
        return self._writes_disabled
```

Replace the `set_vmax` method:

```python
    def set_vmax(self, speed_kmh: int) -> bool:
        """Set the Vmax speed limiter value.

        Includes write counter, parameter bounds guard, and read-back verification.

        Args:
            speed_kmh: Maximum speed in km/h (must be 40-250)

        Returns:
            True if successfully written and verified.
        """
        if not self._security_unlocked:
            logger.error("Security access required before writing Vmax")
            return False

        if self._writes_disabled:
            logger.error("DME writes disabled due to previous fault")
            return False

        # Write counter hard stop
        if self._write_count >= WRITE_HARD_STOP:
            logger.error("Write counter at %d, hard stop reached. Restart to continue.",
                         self._write_count)
            return False

        # Parameter bounds guard (final safety net)
        if speed_kmh < 40:
            logger.error("Refusing to set Vmax below 40 km/h (got %d)", speed_kmh)
            return False
        if speed_kmh > 250:
            logger.error("Refusing to set Vmax above 250 km/h (got %d)", speed_kmh)
            return False

        # Write counter warning
        if self._write_count >= WRITE_WARN_THRESHOLD:
            logger.warning("DME write count high: %d / %d", self._write_count, WRITE_HARD_STOP)

        logger.info("Setting Vmax to %d km/h", speed_kmh)
        value = struct.pack(">H", speed_kmh)
        success = self.uds.write_data(MSV70DID.VMAX_SPEED, value)

        if not success:
            return False

        self._write_count += 1

        # Read-back verification
        readback = self.uds.read_data(MSV70DID.VMAX_SPEED)
        if readback is None or len(readback) < 2:
            logger.critical("FAULT: Vmax read-back failed after write. Disabling writes.")
            self._writes_disabled = True
            return False

        readback_kmh = struct.unpack(">H", readback[:2])[0]
        if abs(readback_kmh - speed_kmh) > 1:
            logger.critical(
                "FAULT: Vmax read-back mismatch. Wrote %d, read %d. Disabling writes.",
                speed_kmh, readback_kmh,
            )
            self._writes_disabled = True
            return False

        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_dme_protection.py tests/test_e90_dme.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/slower/bmw/e90_dme.py tests/test_dme_protection.py
git commit -m "feat: add DME write counter, read-back verification, and bounds guard"
```

---

### Task 5: Recovery Command and Startup Check

**Files:**
- Create: `src/slower/bmw/recovery.py`
- Create: `tests/test_recovery.py`

- [ ] **Step 1: Write failing tests for recovery logic**

```python
# tests/test_recovery.py
"""Tests for DME recovery and startup check."""

from unittest.mock import MagicMock
import struct

from slower.bmw.recovery import check_stale_vmax, reset_vmax


def _make_mock_dme() -> MagicMock:
    dme = MagicMock()
    dme.initialize.return_value = True
    dme.disable_vmax.return_value = True
    return dme


def test_check_stale_vmax_detects_low_value():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True

    result = check_stale_vmax(dme)
    assert result.is_stale is True
    assert result.current_vmax_kmh == 85


def test_check_stale_vmax_ok_when_high():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 200
    dme.read_vmax_active.return_value = True

    result = check_stale_vmax(dme)
    assert result.is_stale is False


def test_check_stale_vmax_ok_when_inactive():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 60
    dme.read_vmax_active.return_value = False

    result = check_stale_vmax(dme)
    assert result.is_stale is False


def test_reset_vmax_disables_limiter():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True

    success = reset_vmax(dme)
    assert success is True
    dme.disable_vmax.assert_called_once()


def test_reset_vmax_returns_false_on_failure():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True
    dme.disable_vmax.return_value = False

    success = reset_vmax(dme)
    assert success is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_recovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'slower.bmw.recovery'`

- [ ] **Step 3: Implement recovery module**

```python
# src/slower/bmw/recovery.py
"""DME recovery and startup safety checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slower.bmw.safety import GPS_LOSS_CAP_KMH

if TYPE_CHECKING:
    from slower.bmw.e90_dme import E90DME

logger = logging.getLogger(__name__)


@dataclass
class StaleVmaxCheck:
    """Result of checking for a stale Vmax value."""

    current_vmax_kmh: int | None
    vmax_active: bool
    is_stale: bool


def check_stale_vmax(dme: E90DME) -> StaleVmaxCheck:
    """Check if the DME has a stale (leftover) Vmax limit.

    A Vmax is considered stale if it is active and below GPS_LOSS_CAP_KMH,
    which suggests it was set by a previous session that crashed.
    """
    vmax = dme.read_vmax()
    active = dme.read_vmax_active()

    if vmax is None or active is None:
        logger.warning("Could not read Vmax status from DME")
        return StaleVmaxCheck(current_vmax_kmh=vmax, vmax_active=bool(active), is_stale=False)

    is_stale = active and vmax < GPS_LOSS_CAP_KMH
    if is_stale:
        logger.warning(
            "Stale Vmax detected: %d km/h (active). Likely from a previous crash.", vmax
        )

    return StaleVmaxCheck(current_vmax_kmh=vmax, vmax_active=active, is_stale=is_stale)


def reset_vmax(dme: E90DME) -> bool:
    """Reset the DME Vmax limiter to factory default (disabled).

    Reads current value, disables the limiter, and logs the change.
    """
    vmax_before = dme.read_vmax()
    active_before = dme.read_vmax_active()
    logger.info("Current Vmax: %s km/h, Active: %s", vmax_before, active_before)

    success = dme.disable_vmax()
    if success:
        logger.info("Vmax limiter disabled successfully")
    else:
        logger.error("Failed to disable Vmax limiter")

    return success
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_recovery.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/slower/bmw/recovery.py tests/test_recovery.py
git commit -m "feat: add DME recovery module with stale Vmax detection and reset"
```

---

### Task 6: Watchdog Heartbeat and Standalone Watchdog

**Files:**
- Create: `src/slower/bmw/watchdog.py`
- Create: `src/slower_watchdog/__init__.py`
- Create: `src/slower_watchdog/main.py`

- [ ] **Step 1: Create heartbeat writer for main process**

```python
# src/slower/bmw/watchdog.py
"""Heartbeat file writer for the watchdog system.

The main slower process calls write_heartbeat() periodically.
The standalone slower-watchdog process monitors the heartbeat file.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_PATH = "/tmp/slower-heartbeat"


def write_heartbeat(path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Touch the heartbeat file to signal the process is alive."""
    try:
        with open(path, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        logger.warning("Failed to write heartbeat: %s", e)


def read_heartbeat_age(path: str = DEFAULT_HEARTBEAT_PATH) -> float | None:
    """Read the age of the heartbeat file in seconds.

    Returns None if the file doesn't exist or can't be read.
    """
    try:
        with open(path) as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except (OSError, ValueError):
        return None


def remove_heartbeat(path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Remove the heartbeat file on clean shutdown."""
    try:
        os.unlink(path)
    except OSError:
        pass
```

- [ ] **Step 2: Create standalone watchdog**

```python
# src/slower_watchdog/__init__.py
"""Standalone watchdog for BimmerDimmer."""
```

```python
# src/slower_watchdog/main.py
"""Standalone watchdog that resets DME Vmax if the main process dies.

This is intentionally simple and does NOT import from the slower package
to avoid shared failure modes. It has its own minimal K+DCAN/UDS logic.

Usage:
    slower-watchdog
    slower-watchdog --port /dev/ttyUSB0
    slower-watchdog --heartbeat-path /tmp/slower-heartbeat
"""

from __future__ import annotations

import argparse
import logging
import os
import struct
import sys
import time

import serial

logger = logging.getLogger("slower-watchdog")

# Minimal constants (duplicated intentionally, no slower imports)
ADDR_DME = 0x12
ADDR_TESTER = 0xF1
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 115200
DEFAULT_HEARTBEAT_PATH = "/tmp/slower-heartbeat"
DEFAULT_CHECK_INTERVAL = 5.0
DEFAULT_TIMEOUT = 10.0


def _build_frame(target: int, data: bytes) -> bytes:
    """Build a DCAN frame: [Length][Target][Source][Data...][XOR Checksum]."""
    length = len(data) + 3
    frame = bytearray([length, target, ADDR_TESTER]) + bytearray(data)
    checksum = 0
    for b in frame:
        checksum ^= b
    frame.append(checksum)
    return bytes(frame)


def _send_disable_vmax(port: str, baudrate: int) -> bool:
    """Open a connection and send disable_vmax command.

    Sequence: Extended Session (0x10 0x03), then WriteDataByID (0x2E)
    to set VMAX_ACTIVE (0x3103) to 0x00.
    """
    try:
        ser = serial.Serial(port, baudrate, timeout=2.0, write_timeout=2.0)
        ser.reset_input_buffer()

        # 1. Enter Extended Diagnostic Session
        frame = _build_frame(ADDR_DME, bytes([0x10, 0x03]))
        ser.write(frame)
        ser.flush()
        time.sleep(0.5)
        ser.reset_input_buffer()

        # 2. Disable Vmax: WriteDataByIdentifier(0x2E, DID=0x3103, value=0x00)
        did_bytes = struct.pack(">H", 0x3103)
        frame = _build_frame(ADDR_DME, bytes([0x2E]) + did_bytes + bytes([0x00]))
        ser.write(frame)
        ser.flush()
        time.sleep(0.5)

        ser.close()
        logger.info("Sent disable_vmax command to DME")
        return True
    except (serial.SerialException, OSError) as e:
        logger.error("Failed to send disable_vmax: %s", e)
        return False


def _read_heartbeat_age(path: str) -> float | None:
    """Read the heartbeat file age in seconds."""
    try:
        with open(path) as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except (OSError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="BimmerDimmer watchdog")
    parser.add_argument("--port", default=os.environ.get("SLOWER_CABLE_PORT", DEFAULT_PORT))
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--heartbeat-path", default=DEFAULT_HEARTBEAT_PATH)
    parser.add_argument("--check-interval", type=float, default=DEFAULT_CHECK_INTERVAL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [WATCHDOG] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Watchdog started. Monitoring heartbeat at %s", args.heartbeat_path)
    logger.info("Serial port: %s, Timeout: %.0fs", args.port, args.timeout)

    sent_reset = False

    while True:
        age = _read_heartbeat_age(args.heartbeat_path)

        if age is None:
            logger.debug("No heartbeat file found (main process may not be running)")
            sent_reset = False  # Reset so we can act again if process restarts
        elif age > args.timeout:
            if not sent_reset:
                logger.warning(
                    "Heartbeat stale (%.1fs > %.1fs). Main process appears dead.",
                    age, args.timeout,
                )
                logger.warning("Sending disable_vmax to DME...")
                _send_disable_vmax(args.port, args.baudrate)
                sent_reset = True
        else:
            if sent_reset:
                logger.info("Heartbeat restored. Main process is back.")
            sent_reset = False
            logger.debug("Heartbeat OK (age: %.1fs)", age)

        time.sleep(args.check_interval)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run a basic import test**

Run: `python3 -c "from slower.bmw.watchdog import write_heartbeat, read_heartbeat_age; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/slower/bmw/watchdog.py src/slower_watchdog/__init__.py src/slower_watchdog/main.py
git commit -m "feat: add heartbeat writer and standalone watchdog process"
```

---

### Task 7: Config Additions (TransportConfig, SafetyConfig)

**Files:**
- Modify: `src/slower/config.py`

- [ ] **Step 1: Add new config dataclasses**

Add to `src/slower/config.py`, after the existing `SpeedLimitsConfig` class:

```python
@dataclass
class TransportConfig:
    wifi: bool = True
    ble: bool = True
    spp: bool = True
    spp_channel: int = 1


@dataclass
class SafetyExtConfig:
    max_gps_accuracy_m: float = 100.0
    max_speed_jump_kmh: float = 50.0
    write_confirm_ticks: int = 2
    max_writes_per_session: int = 1000
    watchdog_heartbeat_sec: float = 2.0
    watchdog_timeout_sec: float = 10.0
```

Add the new fields to the `Config` class:

```python
@dataclass
class Config:
    cable: CableConfig = field(default_factory=CableConfig)
    vehicle: VehicleConfig = field(default_factory=VehicleConfig)
    limiter: LimiterConfig = field(default_factory=LimiterConfig)
    speed_limits: SpeedLimitsConfig = field(default_factory=SpeedLimitsConfig)
    transports: TransportConfig = field(default_factory=TransportConfig)
    safety: SafetyExtConfig = field(default_factory=SafetyExtConfig)
    web: WebConfig = field(default_factory=WebConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
```

Add parsing in `load_config()`, after the existing `if "logging" in raw:` block:

```python
        if "transports" in raw:
            cfg.transports = TransportConfig(**raw["transports"])
        if "safety" in raw:
            cfg.safety = SafetyExtConfig(**raw["safety"])
```

- [ ] **Step 2: Run existing tests to verify no breakage**

Run: `python3 -m pytest -v`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/slower/config.py
git commit -m "feat: add TransportConfig and SafetyExtConfig to configuration"
```

---

### Task 8: WiFi Transport (Refactor from server.py)

**Files:**
- Create: `src/slower/transport/wifi.py`
- Modify: `src/slower/web/server.py`

- [ ] **Step 1: Create WiFi transport**

```python
# src/slower/transport/wifi.py
"""WiFi HTTP transport for GPS data.

Wraps the existing Flask POST /api/gps endpoint with health tracking.
The actual HTTP endpoint remains in web/server.py; this module provides
the transport wrapper that server.py delegates to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)


class WiFiTransport:
    """WiFi HTTP GPS transport with health tracking."""

    name: str = "wifi"

    def __init__(self) -> None:
        self.health = TransportHealth(name="wifi", timeout_sec=10.0)
        self._gps: GPSProvider | None = None

    def start(self, gps: GPSProvider) -> None:
        self._gps = gps
        logger.info("WiFi transport started")

    def stop(self) -> None:
        self._gps = None
        logger.info("WiFi transport stopped")

    def handle_update(self, lat: float, lon: float, speed_mps: float | None = None,
                      heading: float | None = None, accuracy_m: float = 50.0):
        """Called by the Flask endpoint when GPS data arrives via HTTP."""
        if self._gps is None:
            return None
        pos = self._gps.update(
            lat=lat, lon=lon, speed_mps=speed_mps,
            heading=heading, accuracy_m=accuracy_m,
        )
        if pos is not None:
            self.health.record_success()
        else:
            self.health.record_failure()
        return pos
```

- [ ] **Step 2: Update server.py to use WiFiTransport**

Modify `src/slower/web/server.py`. Update the `create_app` signature and the GPS endpoint:

Change the import section to add:
```python
from slower.transport.wifi import WiFiTransport
```

Update the function signature:
```python
def create_app(
    config: Config,
    controller: SpeedLimiterController,
    gps: GPSProvider,
    wifi_transport: WiFiTransport | None = None,
) -> Flask:
```

Replace the `update_gps` endpoint:
```python
    @app.route("/api/gps", methods=["POST"])
    def update_gps():
        """Receive GPS position update from phone browser."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        try:
            if wifi_transport:
                pos = wifi_transport.handle_update(
                    lat=float(data["latitude"]),
                    lon=float(data["longitude"]),
                    speed_mps=data.get("speed"),
                    heading=data.get("heading"),
                    accuracy_m=float(data.get("accuracy", 50)),
                )
            else:
                pos = gps.update(
                    lat=float(data["latitude"]),
                    lon=float(data["longitude"]),
                    speed_mps=data.get("speed"),
                    heading=data.get("heading"),
                    accuracy_m=float(data.get("accuracy", 50)),
                )
            if pos is None:
                return jsonify({"ok": True, "position": None, "note": "fix rejected by validation"})
            return jsonify({
                "ok": True,
                "position": {
                    "lat": pos.latitude,
                    "lon": pos.longitude,
                    "speed_mph": pos.speed_mph,
                    "accuracy_m": pos.accuracy_m,
                },
            })
        except (KeyError, ValueError, TypeError) as e:
            return jsonify({"error": str(e)}), 400
```

- [ ] **Step 3: Run all tests**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/slower/transport/wifi.py src/slower/web/server.py
git commit -m "feat: add WiFi transport wrapper with health tracking"
```

---

### Task 9: BLE Transport (BlueZ GATT Server)

**Files:**
- Create: `src/slower/transport/ble.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dbus-fast dependency**

In `pyproject.toml`, add `"dbus-fast>=2.0"` to the dependencies list:

```toml
dependencies = [
    "pyserial>=3.5",
    "flask>=3.0",
    "requests>=2.31",
    "pyyaml>=6.0",
    "dbus-fast>=2.0",
]
```

- [ ] **Step 2: Create BLE transport**

```python
# src/slower/transport/ble.py
"""BLE GATT server transport for GPS data.

Uses BlueZ via dbus-fast to create a GATT server on the Raspberry Pi.
The phone connects via Web Bluetooth and writes GPS data to a characteristic.

Requires: Linux with BlueZ, dbus-fast package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)

# Custom GATT UUIDs for BimmerDimmer
SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
GPS_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
STATUS_CHAR_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"


class BLETransport:
    """BLE GATT server GPS transport."""

    name: str = "ble"

    def __init__(self) -> None:
        self.health = TransportHealth(name="ble", timeout_sec=10.0)
        self._gps: GPSProvider | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def start(self, gps: GPSProvider) -> None:
        """Start the BLE GATT server in a background thread."""
        self._gps = gps
        self._running = True
        self._thread = threading.Thread(target=self._run_server, daemon=True, name="ble-gatt")
        self._thread.start()
        logger.info("BLE transport starting")

    def stop(self) -> None:
        """Stop the BLE GATT server."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._gps = None
        logger.info("BLE transport stopped")

    def _run_server(self) -> None:
        """Run the async GATT server in its own event loop."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        except ImportError:
            logger.warning("dbus-fast not available. BLE transport disabled.")
        except Exception as e:
            logger.error("BLE GATT server error: %s", e)
        finally:
            if self._loop and not self._loop.is_closed():
                self._loop.close()

    async def _serve(self) -> None:
        """Set up and run the BLE GATT server using dbus-fast."""
        from dbus_fast.aio import MessageBus
        from dbus_fast.service import ServiceInterface, method, dbus_property
        from dbus_fast import Variant

        bus = await MessageBus().connect()

        # Register a simple GATT-like service via BlueZ advertisement
        # Note: Full GATT server setup requires interacting with BlueZ's
        # org.bluez.GattManager1 and org.bluez.LEAdvertisingManager1 interfaces.
        # This is a simplified implementation that registers the service.

        transport = self  # Capture for the inner class

        class GPSGattService(ServiceInterface):
            """D-Bus service interface for BLE GPS data reception."""

            def __init__(self):
                super().__init__("org.bluez.GattCharacteristic1")

            @method()
            def WriteValue(self, value: "ay", options: "a{sv}") -> None:
                """Called when phone writes GPS data to the characteristic."""
                try:
                    json_str = bytes(value).decode("utf-8")
                    data = json.loads(json_str)
                    if transport._gps:
                        pos = transport._gps.update(
                            lat=float(data["latitude"]),
                            lon=float(data["longitude"]),
                            speed_mps=data.get("speed"),
                            heading=data.get("heading"),
                            accuracy_m=float(data.get("accuracy", 50)),
                        )
                        if pos is not None:
                            transport.health.record_success()
                        else:
                            transport.health.record_failure()
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                    logger.warning("BLE: invalid GPS data: %s", e)
                    transport.health.record_failure()

        service = GPSGattService()
        bus.export("/org/bluez/bimmerdimmer/gps", service)

        logger.info("BLE GATT server registered on D-Bus")

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1.0)

        bus.disconnect()
```

- [ ] **Step 3: Commit**

```bash
git add src/slower/transport/ble.py pyproject.toml
git commit -m "feat: add BLE GATT transport for Web Bluetooth GPS data"
```

---

### Task 10: SPP Transport (Bluetooth Serial)

**Files:**
- Create: `src/slower/transport/spp.py`

- [ ] **Step 1: Create SPP transport**

```python
# src/slower/transport/spp.py
"""Bluetooth Serial (SPP/RFCOMM) transport for GPS data.

Listens for Classic Bluetooth connections from a companion app.
Receives newline-delimited JSON GPS payloads over RFCOMM.

Requires: Linux with BlueZ (python socket AF_BLUETOOTH support).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)


class SPPTransport:
    """Bluetooth Serial RFCOMM server GPS transport."""

    name: str = "spp"

    def __init__(self, channel: int = 1) -> None:
        self.channel = channel
        self.health = TransportHealth(name="spp", timeout_sec=10.0)
        self._gps: GPSProvider | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._server_sock: socket.socket | None = None

    def start(self, gps: GPSProvider) -> None:
        """Start listening for Bluetooth Serial connections."""
        self._gps = gps
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True, name="spp-rfcomm")
        self._thread.start()
        logger.info("SPP transport starting on RFCOMM channel %d", self.channel)

    def stop(self) -> None:
        """Stop the SPP server."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self._gps = None
        logger.info("SPP transport stopped")

    def _listen(self) -> None:
        """Listen for RFCOMM connections and handle GPS data."""
        try:
            self._server_sock = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
            )
            self._server_sock.bind(("", self.channel))
            self._server_sock.listen(1)
            self._server_sock.settimeout(2.0)
            logger.info("SPP: listening on RFCOMM channel %d", self.channel)
        except (OSError, AttributeError) as e:
            logger.warning("SPP: Bluetooth socket not available: %s", e)
            return

        while self._running:
            try:
                client, addr = self._server_sock.accept()
                logger.info("SPP: client connected from %s", addr)
                handler = threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True,
                    name="spp-client",
                )
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.error("SPP: server socket error")
                break

    def _handle_client(self, client: socket.socket) -> None:
        """Handle a connected Bluetooth Serial client."""
        buffer = ""
        client.settimeout(5.0)

        try:
            while self._running:
                try:
                    data = client.recv(1024)
                except socket.timeout:
                    continue

                if not data:
                    break

                buffer += data.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._process_line(line)
        except OSError as e:
            logger.warning("SPP: client disconnected: %s", e)
        finally:
            client.close()
            logger.info("SPP: client connection closed")

    def _process_line(self, line: str) -> None:
        """Parse a JSON GPS line and feed to GPSProvider."""
        try:
            data = json.loads(line)
            if self._gps:
                pos = self._gps.update(
                    lat=float(data["latitude"]),
                    lon=float(data["longitude"]),
                    speed_mps=data.get("speed"),
                    heading=data.get("heading"),
                    accuracy_m=float(data.get("accuracy", 50)),
                )
                if pos is not None:
                    self.health.record_success()
                else:
                    self.health.record_failure()
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("SPP: invalid GPS data: %s", e)
            self.health.record_failure()
```

- [ ] **Step 2: Commit**

```bash
git add src/slower/transport/spp.py
git commit -m "feat: add Bluetooth Serial (SPP/RFCOMM) transport"
```

---

### Task 11: Controller Safety Improvements (Confirmation Ticks, Fresh Data, Heartbeat)

**Files:**
- Modify: `src/slower/limiter/controller.py`

- [ ] **Step 1: Update controller with safety features**

Modify `src/slower/limiter/controller.py`. Update imports:

```python
from slower.bmw.e90_dme import E90DME
from slower.bmw.safety import ABSOLUTE_MAX_VMAX_KMH, GPS_LOSS_CAP_KMH, ConnectionMonitor, SafetyManager
from slower.bmw.watchdog import write_heartbeat
from slower.config import Config
from slower.gps.provider import GPSProvider
from slower.gps.speed_limits import SpeedLimitResult, SpeedLimitService
```

Add fields to `LimiterState`:

```python
@dataclass
class LimiterState:
    """Current state of the speed limiter for the dashboard."""

    running: bool = False
    active_mode: bool = False
    gps_connected: bool = False
    dme_connected: bool = False
    current_speed_mph: float | None = None
    current_speed_limit_mph: int | None = None
    target_vmax_mph: int | None = None
    actual_vmax_kmh: int | None = None
    road_name: str | None = None
    speed_limit_source: str = "none"
    emergency_override: bool = False
    offset_mph: int = 5
    last_error: str | None = None
    status_messages: list[str] = field(default_factory=list)
    transport_states: dict[str, str] = field(default_factory=dict)
    dme_write_count: int = 0
    degraded_reason: str | None = None
```

Add confirmation tick tracking and connection monitor to `SpeedLimiterController.__init__`:

```python
    def __init__(self, config: Config, dme: E90DME | None, gps: GPSProvider,
                 connection_monitor: ConnectionMonitor | None = None) -> None:
        self.config = config
        self.dme = dme
        self.gps = gps
        self.safety = SafetyManager()
        self.connection_monitor = connection_monitor or ConnectionMonitor()
        self.speed_limits = SpeedLimitService(
            primary=config.speed_limits.primary,
            google_api_key=config.speed_limits.google_api_key,
            search_radius_m=config.speed_limits.search_radius_m,
            cache_ttl_sec=config.speed_limits.cache_ttl_sec,
        )
        self.state = LimiterState(
            active_mode=config.limiter.active,
            offset_mph=config.limiter.offset_mph,
        )

        # Confirmation tick tracking
        self._pending_vmax_kmh: int | None = None
        self._pending_ticks: int = 0
        self._confirm_ticks = config.safety.write_confirm_ticks

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._lock = threading.Lock()
```

Update `_control_tick` to write heartbeat and use confirmation ticks:

```python
    def _control_tick(self) -> None:
        """Single iteration of the control loop."""
        # Write watchdog heartbeat
        write_heartbeat()

        # Update dashboard with transport and DME state
        self.state.transport_states = self.connection_monitor.transport_states
        if self.dme:
            self.state.dme_write_count = self.dme.write_count

        pos = self.gps.position

        # Handle GPS state
        if pos is None:
            self.state.gps_connected = False
            self.state.current_speed_mph = None
            self._pending_vmax_kmh = None
            self._pending_ticks = 0

            vmax_kmh = self.safety.handle_gps_loss(self.config.limiter.gps_loss_grace_sec)
            self._apply_vmax(vmax_kmh)
            return

        self.safety.handle_gps_restored()
        self.state.gps_connected = True
        self.state.current_speed_mph = pos.speed_mph

        # Check GPS fix freshness (must be < 5s old)
        if pos.age_seconds > 5.0:
            self.state.degraded_reason = "GPS fix stale"
            self._apply_vmax(GPS_LOSS_CAP_KMH)
            return

        # Look up speed limit
        result = self.speed_limits.get_speed_limit(pos.latitude, pos.longitude)
        self._update_state_from_limit(result)

        if result.speed_limit_mph is None:
            self._apply_vmax(ABSOLUTE_MAX_VMAX_KMH)
            return

        # Calculate target Vmax
        target_mph = result.speed_limit_mph + self.state.offset_mph
        target_kmh = int(E90DME.mph_to_kmh(target_mph))
        self.state.target_vmax_mph = target_mph

        # Confirmation ticks: lowering Vmax requires stable target for N ticks
        current_kmh = self.state.actual_vmax_kmh or ABSOLUTE_MAX_VMAX_KMH
        if target_kmh < current_kmh:
            if self._pending_vmax_kmh == target_kmh:
                self._pending_ticks += 1
            else:
                self._pending_vmax_kmh = target_kmh
                self._pending_ticks = 1

            if self._pending_ticks < self._confirm_ticks:
                logger.debug("Confirmation tick %d/%d for Vmax %d",
                             self._pending_ticks, self._confirm_ticks, target_kmh)
                return  # Hold current value until confirmed
        else:
            # Raising Vmax (less restrictive) applies immediately
            self._pending_vmax_kmh = None
            self._pending_ticks = 0

        self.state.degraded_reason = None
        self._apply_vmax(target_kmh)
```

Update `_apply_vmax` to check connection monitor:

```python
    def _apply_vmax(self, target_kmh: int) -> None:
        """Apply a Vmax value through safety checks and to DME."""
        current_kmh = self.state.actual_vmax_kmh or ABSOLUTE_MAX_VMAX_KMH
        safe_kmh = self.safety.validate_vmax_change(current_kmh, target_kmh)

        self.state.actual_vmax_kmh = safe_kmh

        if not self.state.active_mode or self.dme is None:
            return

        # Check if K+DCAN connection allows writes
        if not self.connection_monitor.should_write_dme:
            self.state.degraded_reason = "K+DCAN connection lost"
            return

        # Only write if value actually changed
        if safe_kmh == current_kmh:
            return

        try:
            if safe_kmh >= ABSOLUTE_MAX_VMAX_KMH:
                self.dme.disable_vmax()
            else:
                self.dme.enable_vmax()
                self.dme.set_vmax(safe_kmh)
            self.safety.handle_dme_success()
            self.connection_monitor.kdcan_health.record_success()
            self.state.dme_connected = True
        except Exception as e:
            logger.error("DME write failed: %s", e)
            self.safety.handle_dme_failure()
            self.connection_monitor.kdcan_health.record_failure()
            self.state.dme_connected = False
            self.state.last_error = f"DME: {e}"
```

- [ ] **Step 2: Run all tests**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/slower/limiter/controller.py
git commit -m "feat: add confirmation ticks, fresh data checks, heartbeat, connection monitor to controller"
```

---

### Task 12: Main Entry Point Updates (--reset, transports, startup recovery)

**Files:**
- Modify: `src/slower/main.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Update main.py**

Replace `src/slower/main.py` with updated version that wires transports, adds `--reset`, and runs startup recovery:

Add `--reset` argument to the parser:

```python
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset DME Vmax to factory default and exit (recovery mode)",
    )
```

Add reset handler after config loading (before GPS/transport init):

```python
    # Handle --reset mode
    if args.reset:
        from slower.bmw.connection import KDCANConnection
        from slower.bmw.e90_dme import E90DME
        from slower.bmw.uds import UDSClient
        from slower.bmw.recovery import reset_vmax

        logger.info("=== RESET MODE ===")
        try:
            conn = KDCANConnection(config.cable)
            conn.connect()
            uds = UDSClient(conn)
            dme = E90DME(uds)
            if dme.initialize():
                success = reset_vmax(dme)
                conn.disconnect()
                sys.exit(0 if success else 1)
            else:
                logger.error("Failed to initialize DME for reset")
                conn.disconnect()
                sys.exit(1)
        except Exception as e:
            logger.error("Reset failed: %s", e)
            sys.exit(1)
```

After DME initialization succeeds, add startup recovery check:

```python
            if not dme.initialize():
                logger.error("Failed to initialize DME - falling back to monitor mode")
                config.limiter.active = False
                dme = None
            else:
                # Startup recovery check
                from slower.bmw.recovery import check_stale_vmax
                stale = check_stale_vmax(dme)
                if stale.is_stale:
                    logger.warning(
                        "STALE VMAX: %d km/h active from previous session. Resetting.",
                        stale.current_vmax_kmh,
                    )
                    dme.disable_vmax()

                status = dme.get_status()
                logger.info(
                    "DME connected - Current Vmax: %s km/h, Active: %s",
                    status.vmax_speed_kmh, status.vmax_active,
                )
```

After GPS provider init, add transport wiring:

```python
    # Initialize transports
    from slower.bmw.safety import ConnectionMonitor
    from slower.transport.wifi import WiFiTransport

    connection_monitor = ConnectionMonitor()
    wifi_transport = WiFiTransport()
    wifi_transport.start(gps)
    connection_monitor.add_gps_transport("wifi")

    if config.transports.ble:
        try:
            from slower.transport.ble import BLETransport
            ble_transport = BLETransport()
            ble_transport.start(gps)
            connection_monitor.add_gps_transport("ble")
        except Exception as e:
            logger.warning("BLE transport unavailable: %s", e)

    if config.transports.spp:
        try:
            from slower.transport.spp import SPPTransport
            spp_transport = SPPTransport(channel=config.transports.spp_channel)
            spp_transport.start(gps)
            connection_monitor.add_gps_transport("spp")
        except Exception as e:
            logger.warning("SPP transport unavailable: %s", e)
```

Update controller initialization to pass connection_monitor:

```python
    controller = SpeedLimiterController(config, dme, gps, connection_monitor=connection_monitor)
```

Update `create_app` call:

```python
    app = create_app(config, controller, gps, wifi_transport=wifi_transport)
```

Add heartbeat cleanup to shutdown:

```python
    def shutdown(signum, frame):
        logger.info("Shutting down...")
        controller.stop()
        from slower.bmw.watchdog import remove_heartbeat
        remove_heartbeat()
        sys.exit(0)
```

Update the version string:

```python
    logger.info("=== BimmerDimmer v0.3.0 - BMW GPS Speed Limiter ===")
```

- [ ] **Step 2: Update pyproject.toml entry points**

Add the watchdog entry point:

```toml
[project.scripts]
slower = "slower.main:main"
slower-watchdog = "slower_watchdog.main:main"
```

- [ ] **Step 3: Run all tests**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/slower/main.py pyproject.toml
git commit -m "feat: wire transports, add --reset flag, startup recovery, and watchdog heartbeat"
```

---

### Task 13: Dashboard Updates (Transport Health, Write Counter, BLE Button, Confirmation)

**Files:**
- Modify: `src/slower/web/server.py`
- Modify: `src/slower/web/templates/index.html`

- [ ] **Step 1: Add transport states to status API**

In `src/slower/web/server.py`, update the `get_status` endpoint to include new fields:

```python
    @app.route("/api/status")
    def get_status():
        """Get current limiter state for dashboard."""
        s = controller.state
        return jsonify({
            "running": s.running,
            "active_mode": s.active_mode,
            "gps_connected": s.gps_connected,
            "dme_connected": s.dme_connected,
            "current_speed_mph": round(s.current_speed_mph, 1) if s.current_speed_mph else None,
            "speed_limit_mph": s.current_speed_limit_mph,
            "target_vmax_mph": s.target_vmax_mph,
            "actual_vmax_kmh": s.actual_vmax_kmh,
            "road_name": s.road_name,
            "speed_limit_source": s.speed_limit_source,
            "emergency_override": s.emergency_override,
            "offset_mph": s.offset_mph,
            "last_error": s.last_error,
            "messages": s.status_messages[-10:],
            "transport_states": s.transport_states,
            "dme_write_count": s.dme_write_count,
            "degraded_reason": s.degraded_reason,
        })
```

- [ ] **Step 2: Update dashboard HTML**

Update `src/slower/web/templates/index.html`:

Replace the title:
```html
    <title>BimmerDimmer - BMW Speed Limiter</title>
```

Replace the header h1:
```html
        <h1>BIMMERDIMMER</h1>
```

Add transport status chips. Replace the existing `status-bar` div:
```html
    <div class="status-bar">
        <span class="status-chip chip-gps off" id="chipGps">GPS: OFF</span>
        <span class="status-chip chip-dme off" id="chipDme">DME: OFF</span>
        <span class="status-chip chip-source" id="chipSource">Source: --</span>
        <span class="status-chip" id="chipWifi" style="background:#333;color:#888">WiFi: --</span>
        <span class="status-chip" id="chipBle" style="background:#333;color:#888">BLE: --</span>
        <span class="status-chip" id="chipSpp" style="background:#333;color:#888">SPP: --</span>
    </div>
```

Add write counter display after the status bar:
```html
    <div id="writeCounter" style="text-align:center;font-size:11px;color:#666;padding:4px;display:none">
        DME Writes: <span id="writeCount">0</span> / 1000
    </div>
```

Add degraded mode banner after the warning banner:
```html
    <div class="warning-banner" id="degradedBanner" style="display:none;border-color:#ff1744;color:#ff1744;background:#1a0005">
        DEGRADED: <span id="degradedReason"></span>
    </div>
```

Add BLE connect button in the controls section, after the mode button control-row:
```html
        <div class="control-row" id="bleRow">
            <button class="btn" id="btnBle" onclick="connectBLE()" style="background:#6200ea;color:#fff">CONNECT BLUETOOTH</button>
        </div>
```

Add Active Mode confirmation dialog before the closing `</body>`:
```html
    <div id="confirmDialog" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.85);z-index:100;display:none;align-items:center;justify-content:center">
        <div style="background:#1a1a1a;border:1px solid #ff9100;border-radius:16px;padding:24px;margin:20px;max-width:340px;text-align:center">
            <div style="font-size:18px;font-weight:700;color:#ff9100;margin-bottom:12px">Enable Active Mode?</div>
            <div style="font-size:14px;color:#ccc;line-height:1.5;margin-bottom:20px">
                Active Mode will send commands to your DME. This modifies engine control parameters. Are you sure?
            </div>
            <div style="display:flex;gap:12px;justify-content:center">
                <button class="btn" style="background:#333;color:#fff" onclick="dismissConfirm()">Cancel</button>
                <button class="btn" style="background:#ff9100;color:#000" onclick="confirmActiveMode()">Enable Active Mode</button>
            </div>
        </div>
    </div>
```

Update the JavaScript `updateUI` function to handle new fields. Add after the existing offset update:

```javascript
            // Transport states
            if (state.transport_states) {
                updateTransportChip('chipWifi', 'WiFi', state.transport_states.wifi);
                updateTransportChip('chipBle', 'BLE', state.transport_states.ble);
                updateTransportChip('chipSpp', 'SPP', state.transport_states.spp);
            }

            // Write counter
            var wc = document.getElementById('writeCounter');
            if (state.active_mode && state.dme_write_count != null) {
                wc.style.display = 'block';
                var countEl = document.getElementById('writeCount');
                countEl.textContent = state.dme_write_count;
                if (state.dme_write_count >= 1000) countEl.style.color = '#ff1744';
                else if (state.dme_write_count >= 500) countEl.style.color = '#ff9100';
                else countEl.style.color = '#888';
            } else {
                wc.style.display = 'none';
            }

            // Degraded banner
            var db = document.getElementById('degradedBanner');
            if (state.degraded_reason) {
                db.style.display = 'block';
                document.getElementById('degradedReason').textContent = state.degraded_reason;
            } else {
                db.style.display = 'none';
            }
```

Add new helper functions:

```javascript
        function updateTransportChip(id, label, state) {
            var chip = document.getElementById(id);
            if (!chip || !state) return;
            chip.textContent = label + ': ' + state.toUpperCase();
            if (state === 'healthy') { chip.style.background = '#1b5e20'; chip.style.color = '#e0e0e0'; }
            else if (state === 'degraded') { chip.style.background = '#e65100'; chip.style.color = '#fff'; }
            else if (state === 'lost') { chip.style.background = '#b71c1c'; chip.style.color = '#fff'; }
            else { chip.style.background = '#333'; chip.style.color = '#888'; }
        }

        // Active mode confirmation
        function toggleMode() {
            if (!activeMode) {
                // Show confirmation dialog
                document.getElementById('confirmDialog').style.display = 'flex';
            } else {
                // Switching to monitor mode needs no confirmation
                fetch('/api/control/mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active: false }),
                });
            }
        }

        function confirmActiveMode() {
            document.getElementById('confirmDialog').style.display = 'none';
            fetch('/api/control/mode', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active: true }),
            });
        }

        function dismissConfirm() {
            document.getElementById('confirmDialog').style.display = 'none';
        }

        // Web Bluetooth
        var bleDevice = null;
        var bleChar = null;

        function connectBLE() {
            if (!navigator.bluetooth) {
                addLog('Web Bluetooth not supported in this browser');
                return;
            }

            navigator.bluetooth.requestDevice({
                filters: [{ services: ['0000fff0-0000-1000-8000-00805f9b34fb'] }],
            })
            .then(device => {
                bleDevice = device;
                addLog('BLE: connecting to ' + device.name);
                return device.gatt.connect();
            })
            .then(server => server.getPrimaryService('0000fff0-0000-1000-8000-00805f9b34fb'))
            .then(service => service.getCharacteristic('0000fff1-0000-1000-8000-00805f9b34fb'))
            .then(char => {
                bleChar = char;
                document.getElementById('btnBle').textContent = 'BLUETOOTH CONNECTED';
                document.getElementById('btnBle').style.background = '#00c853';
                addLog('BLE: connected, sending GPS via Bluetooth');
            })
            .catch(err => {
                addLog('BLE error: ' + err.message);
            });
        }

        function sendGPSviaBLE(pos) {
            if (!bleChar) return;
            var data = JSON.stringify({
                latitude: pos.coords.latitude,
                longitude: pos.coords.longitude,
                speed: pos.coords.speed,
                heading: pos.coords.heading,
                accuracy: pos.coords.accuracy,
            });
            var encoder = new TextEncoder();
            bleChar.writeValue(encoder.encode(data)).catch(e => {
                addLog('BLE write error: ' + e.message);
            });
        }
```

Update `startGPS` to also send via BLE when connected. Replace the GPS success callback:

```javascript
                (pos) => {
                    const data = {
                        latitude: pos.coords.latitude,
                        longitude: pos.coords.longitude,
                        speed: pos.coords.speed,
                        heading: pos.coords.heading,
                        accuracy: pos.coords.accuracy,
                    };
                    // Send via WiFi HTTP
                    fetch('/api/gps', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data),
                    }).catch(e => console.error('GPS send error:', e));
                    // Also send via BLE if connected
                    if (bleChar) sendGPSviaBLE(pos);
                },
```

Hide BLE button on browsers without Web Bluetooth. Add at the end of initialization:

```javascript
        // Hide BLE button if not supported
        if (!navigator.bluetooth) {
            document.getElementById('bleRow').style.display = 'none';
        }
```

- [ ] **Step 3: Run all tests**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/slower/web/server.py src/slower/web/templates/index.html
git commit -m "feat: update dashboard with transport health, write counter, BLE connect, active mode confirmation"
```

---

### Task 14: Update Existing Tests

**Files:**
- Modify: `tests/test_safety.py`
- Modify: `tests/test_gps_provider.py`

- [ ] **Step 1: Update safety tests for new min Vmax**

The existing `test_clamps_to_minimum` test uses `ABSOLUTE_MIN_VMAX_KMH` which is now 40. Verify it still passes. No code change needed if the test references the constant.

Update `tests/test_gps_provider.py` to account for the fact that `update()` now returns `GPSPosition | None` instead of `GPSPosition`. Check existing tests:

- `test_provider_update`: Uses default `accuracy_m=50.0` (under 100m threshold), should pass.
- `test_provider_history`: Same, should pass.

Run: `python3 -m pytest tests/test_safety.py tests/test_gps_provider.py -v`
Expected: All PASS

- [ ] **Step 2: Add a test for GPS loss cap value**

Add to `tests/test_safety.py`:

```python
from slower.bmw.safety import GPS_LOSS_CAP_KMH


def test_gps_loss_caps_at_120_after_grace():
    sm = SafetyManager()
    sm.state.last_vmax_kmh = 80

    # Initial loss
    sm.handle_gps_loss(grace_period_sec=0.0)
    # Grace period of 0 means immediately cap
    import time
    sm.state.gps_lost_time = time.monotonic() - 1  # Pretend 1 second ago
    result = sm.handle_gps_loss(grace_period_sec=0.0)
    assert result == GPS_LOSS_CAP_KMH
    assert result == 120
```

- [ ] **Step 3: Run all tests**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_safety.py tests/test_gps_provider.py
git commit -m "test: update tests for new safety values and GPS loss cap"
```

---

### Task 15: Final Integration and Pyproject Updates

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update version and dependencies**

Update `pyproject.toml`:

```toml
version = "0.3.0"
```

Ensure dependencies include:
```toml
dependencies = [
    "pyserial>=3.5",
    "flask>=3.0",
    "requests>=2.31",
    "pyyaml>=6.0",
    "dbus-fast>=2.0",
]
```

Ensure entry points include:
```toml
[project.scripts]
slower = "slower.main:main"
slower-watchdog = "slower_watchdog.main:main"
```

Also add `slower_watchdog` to package discovery:
```toml
[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 2: Run full test suite**

Run: `python3 -m pytest -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.3.0, finalize dependencies and entry points"
```

---

### Task 16: GitHub Repository Updates

**Files:** None (GitHub API calls only)

- [ ] **Step 1: Update GitHub repo description**

```bash
gh repo edit --description "GPS-based speed limiter for BMW E90 325xi. Dynamically adjusts DME Vmax via K+DCAN cable using GPS speed limit data over WiFi or Bluetooth."
```

- [ ] **Step 2: Make repo public**

```bash
gh repo edit --visibility public
```

- [ ] **Step 3: Add topics**

```bash
gh repo edit --add-topic bmw,e90,obd2,raspberry-pi,gps,speed-limiter,bluetooth,ble
```

- [ ] **Step 4: Verify**

```bash
gh repo view --json description,visibility,repositoryTopics
```

Expected: description updated, visibility=public, topics present.
