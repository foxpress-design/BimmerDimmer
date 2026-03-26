"""Tests for the safety manager."""

import time

from slower.bmw.safety import (
    ABSOLUTE_MAX_VMAX_KMH,
    ABSOLUTE_MIN_VMAX_KMH,
    SafetyManager,
)


def test_clamps_to_minimum():
    sm = SafetyManager()
    sm.state.last_update_time = time.monotonic() - 10  # Allow update
    result = sm.validate_vmax_change(100, 10)
    assert result >= ABSOLUTE_MIN_VMAX_KMH


def test_clamps_to_maximum():
    sm = SafetyManager()
    sm.state.last_update_time = time.monotonic() - 10
    result = sm.validate_vmax_change(100, 999)
    assert result <= ABSOLUTE_MAX_VMAX_KMH


def test_emergency_override_returns_max():
    sm = SafetyManager()
    sm.set_emergency_override(True)
    result = sm.validate_vmax_change(80, 50)
    assert result == ABSOLUTE_MAX_VMAX_KMH


def test_rate_limiting_prevents_sudden_drop():
    sm = SafetyManager()
    sm.state.last_update_time = time.monotonic() - 1.0  # 1 second ago
    # Try to drop from 200 to 50 (150 km/h drop in 1 second)
    result = sm.validate_vmax_change(200, 50)
    # Should be rate-limited: max drop is ~50 km/h per second
    assert result > 50
    assert result <= 200


def test_gps_loss_holds_then_releases():
    sm = SafetyManager()
    sm.state.last_vmax_kmh = 80

    # Initial loss - should hold current value
    result = sm.handle_gps_loss(grace_period_sec=5.0)
    assert result == 80

    # Still within grace period
    result = sm.handle_gps_loss(grace_period_sec=5.0)
    assert result == 80


def test_gps_restored_clears_loss():
    sm = SafetyManager()
    sm.handle_gps_loss(grace_period_sec=10.0)
    assert sm.state.gps_lost_time is not None
    sm.handle_gps_restored()
    assert sm.state.gps_lost_time is None


def test_dme_failures_trigger_override():
    sm = SafetyManager()
    for _ in range(5):
        sm.handle_dme_failure()
    assert sm.state.emergency_override is True


def test_update_too_fast_returns_current():
    sm = SafetyManager()
    sm.state.last_update_time = time.monotonic()  # Just updated
    result = sm.validate_vmax_change(100, 80)
    assert result == 100  # No change, too fast
