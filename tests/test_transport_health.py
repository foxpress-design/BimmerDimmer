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
