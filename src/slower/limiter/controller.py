"""Main speed limiter controller.

This is the core loop that:
  1. Reads GPS position from phone
  2. Looks up the speed limit for that location
  3. Computes the target Vmax
  4. Sends the Vmax command to the DME (if active mode is enabled)
  5. Keeps the diagnostic session alive
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from slower.bmw.e90_dme import E90DME
from slower.bmw.safety import ABSOLUTE_MAX_VMAX_KMH, GPS_LOSS_CAP_KMH, ConnectionMonitor, SafetyManager
from slower.bmw.watchdog import write_heartbeat
from slower.config import Config
from slower.gps.provider import GPSProvider
from slower.gps.speed_limits import SpeedLimitResult, SpeedLimitService

logger = logging.getLogger(__name__)


@dataclass
class LimiterState:
    """Current state of the speed limiter for the dashboard."""

    running: bool = False
    active_mode: bool = False  # True = writing to DME, False = monitor only
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


class SpeedLimiterController:
    """Bridges GPS speed limit data to BMW DME Vmax control."""

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

    def start(self) -> None:
        """Start the speed limiter control loop in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Controller already running")
            return

        self._stop_event.clear()
        self.state.running = True

        # Main control loop
        self._thread = threading.Thread(target=self._control_loop, daemon=True, name="limiter")
        self._thread.start()

        # TesterPresent keepalive (every 2 seconds)
        if self.dme:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True, name="keepalive"
            )
            self._keepalive_thread.start()

        self._add_status("Speed limiter started")
        logger.info("Speed limiter controller started (active=%s)", self.state.active_mode)

    def stop(self) -> None:
        """Stop the speed limiter and release Vmax to factory default."""
        self._stop_event.set()
        self.state.running = False

        if self._thread:
            self._thread.join(timeout=5)
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=5)

        # Release Vmax limiter on shutdown
        if self.dme and self.state.active_mode:
            try:
                self.dme.disable_vmax()
                self._add_status("Vmax limiter disabled on shutdown")
            except Exception as e:
                logger.error("Failed to disable Vmax on shutdown: %s", e)

        self._add_status("Speed limiter stopped")
        logger.info("Speed limiter controller stopped")

    def set_active_mode(self, active: bool) -> None:
        """Toggle between active (DME write) and monitor-only mode."""
        with self._lock:
            self.state.active_mode = active
            if not active and self.dme:
                # Switching to monitor mode - release limiter
                try:
                    self.dme.disable_vmax()
                except Exception:
                    pass
            self._add_status(f"Mode: {'ACTIVE (DME write)' if active else 'MONITOR ONLY'}")

    def set_offset(self, offset_mph: int) -> None:
        """Set the offset above posted limit."""
        with self._lock:
            self.state.offset_mph = max(0, min(20, offset_mph))
            self._add_status(f"Offset set to +{self.state.offset_mph} mph")

    def emergency_override(self, active: bool) -> None:
        """Toggle emergency override (disables limiter immediately)."""
        self.safety.set_emergency_override(active)
        self.state.emergency_override = active
        self._add_status(
            "EMERGENCY OVERRIDE ON" if active else "Emergency override cleared"
        )

    def _control_loop(self) -> None:
        """Main control loop - runs in background thread."""
        interval = self.config.limiter.update_interval_sec

        while not self._stop_event.is_set():
            try:
                self._control_tick()
            except Exception as e:
                logger.error("Control loop error: %s", e)
                self.state.last_error = str(e)

            self._stop_event.wait(timeout=interval)

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

    def _keepalive_loop(self) -> None:
        """Send TesterPresent every 2 seconds to keep the session alive."""
        while not self._stop_event.is_set():
            if self.dme:
                try:
                    self.dme.keep_alive()
                except Exception as e:
                    logger.debug("Keepalive failed: %s", e)
            self._stop_event.wait(timeout=2.0)

    def _update_state_from_limit(self, result: SpeedLimitResult) -> None:
        """Update dashboard state from speed limit result."""
        self.state.current_speed_limit_mph = result.speed_limit_mph
        self.state.road_name = result.road_name
        self.state.speed_limit_source = result.source

    def _add_status(self, msg: str) -> None:
        """Add a status message to the dashboard feed."""
        ts = time.strftime("%H:%M:%S")
        self.state.status_messages.append(f"[{ts}] {msg}")
        # Keep last 50 messages
        if len(self.state.status_messages) > 50:
            self.state.status_messages = self.state.status_messages[-50:]
