"""Safety layer for DME write operations.

This module enforces safety invariants that CANNOT be bypassed by configuration.
These are hard-coded limits to prevent dangerous operating conditions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Hard safety limits - these are NOT configurable
ABSOLUTE_MIN_VMAX_KMH = 25  # ~15 mph - never limit below this
ABSOLUTE_MAX_VMAX_KMH = 250  # ~155 mph - factory top speed
MAX_VMAX_CHANGE_PER_SEC_KMH = 50  # Max rate of Vmax change (prevent sudden drops)
MIN_UPDATE_INTERVAL_SEC = 1.0  # Don't hammer the DME faster than this


@dataclass
class SafetyState:
    """Tracks state for rate-limiting and safety checks."""

    last_vmax_kmh: int = ABSOLUTE_MAX_VMAX_KMH
    last_update_time: float = 0.0
    consecutive_failures: int = 0
    gps_lost_time: float | None = None
    emergency_override: bool = False
    fault_history: list[str] = field(default_factory=list)

    MAX_FAULT_HISTORY = 100


class SafetyManager:
    """Enforces safety constraints on all speed limiter operations."""

    def __init__(self) -> None:
        self.state = SafetyState()

    def validate_vmax_change(self, current_vmax_kmh: int, target_vmax_kmh: int) -> int:
        """Validate and potentially clamp a Vmax change.

        Enforces:
          1. Absolute min/max bounds
          2. Rate limiting (no sudden large decreases)
          3. Minimum update interval

        Args:
            current_vmax_kmh: current Vmax setting
            target_vmax_kmh: desired new Vmax

        Returns:
            Safe Vmax value to actually set (may differ from target).
        """
        if self.state.emergency_override:
            logger.warning("Emergency override active - releasing limiter")
            return ABSOLUTE_MAX_VMAX_KMH

        # Clamp to absolute bounds
        safe_vmax = max(ABSOLUTE_MIN_VMAX_KMH, min(ABSOLUTE_MAX_VMAX_KMH, target_vmax_kmh))

        # Rate limiting: don't decrease Vmax too fast
        now = time.monotonic()
        elapsed = now - self.state.last_update_time if self.state.last_update_time else 999.0

        if elapsed < MIN_UPDATE_INTERVAL_SEC:
            logger.debug("Update too fast (%.1fs), skipping", elapsed)
            return current_vmax_kmh  # No change

        max_decrease = int(MAX_VMAX_CHANGE_PER_SEC_KMH * elapsed)
        if current_vmax_kmh - safe_vmax > max_decrease:
            safe_vmax = current_vmax_kmh - max_decrease
            logger.info(
                "Rate-limiting Vmax decrease: target=%d, allowed=%d (max decrease %d in %.1fs)",
                target_vmax_kmh,
                safe_vmax,
                max_decrease,
                elapsed,
            )

        # Re-clamp after rate limiting
        safe_vmax = max(ABSOLUTE_MIN_VMAX_KMH, safe_vmax)

        self.state.last_vmax_kmh = safe_vmax
        self.state.last_update_time = now
        return safe_vmax

    def handle_gps_loss(self, grace_period_sec: float) -> int:
        """Handle GPS signal loss.

        Returns what Vmax should be set to. After grace period, releases limiter.
        """
        now = time.monotonic()

        if self.state.gps_lost_time is None:
            self.state.gps_lost_time = now
            self._record_fault("GPS signal lost")
            logger.warning("GPS signal lost, holding current Vmax for %.0fs", grace_period_sec)
            return self.state.last_vmax_kmh

        elapsed = now - self.state.gps_lost_time
        if elapsed > grace_period_sec:
            logger.warning("GPS grace period expired (%.0fs), releasing limiter", elapsed)
            return ABSOLUTE_MAX_VMAX_KMH

        # Still within grace period, hold current limit
        return self.state.last_vmax_kmh

    def handle_gps_restored(self) -> None:
        """Called when GPS signal is reacquired."""
        if self.state.gps_lost_time is not None:
            logger.info("GPS signal restored")
            self.state.gps_lost_time = None

    def handle_dme_failure(self) -> None:
        """Track consecutive DME communication failures."""
        self.state.consecutive_failures += 1
        self._record_fault(f"DME communication failure #{self.state.consecutive_failures}")

        if self.state.consecutive_failures >= 5:
            logger.error("Too many DME failures, triggering emergency override")
            self.state.emergency_override = True

    def handle_dme_success(self) -> None:
        """Reset failure counter on successful DME communication."""
        self.state.consecutive_failures = 0

    def set_emergency_override(self, active: bool) -> None:
        """Manually trigger or clear emergency override.

        When active, all Vmax requests return the maximum (factory) value,
        effectively disabling the speed limiter.
        """
        self.state.emergency_override = active
        if active:
            self._record_fault("Emergency override activated (manual)")
            logger.warning("EMERGENCY OVERRIDE ACTIVATED - speed limiter disabled")
        else:
            logger.info("Emergency override cleared")

    def _record_fault(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.state.fault_history.append(entry)
        if len(self.state.fault_history) > SafetyState.MAX_FAULT_HISTORY:
            self.state.fault_history = self.state.fault_history[-SafetyState.MAX_FAULT_HISTORY :]
        logger.warning("FAULT: %s", msg)
