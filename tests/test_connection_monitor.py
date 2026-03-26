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
    cm.kdcan_health.record_success()
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
