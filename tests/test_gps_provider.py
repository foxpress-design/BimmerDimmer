"""Tests for GPS provider."""

import time

from slower.gps.provider import GPSPosition, GPSProvider


def test_gps_position_speed_conversion():
    pos = GPSPosition(
        latitude=40.0, longitude=-74.0,
        speed_mps=10.0, heading=90.0,
        accuracy_m=5.0, timestamp=time.time(),
    )
    assert pos.speed_mph is not None
    assert abs(pos.speed_mph - 22.3694) < 0.01
    assert pos.speed_kmh is not None
    assert abs(pos.speed_kmh - 36.0) < 0.01


def test_gps_position_none_speed():
    pos = GPSPosition(
        latitude=40.0, longitude=-74.0,
        speed_mps=None, heading=None,
        accuracy_m=50.0, timestamp=time.time(),
    )
    assert pos.speed_mph is None
    assert pos.speed_kmh is None


def test_gps_position_staleness():
    pos = GPSPosition(
        latitude=40.0, longitude=-74.0,
        speed_mps=0, heading=0,
        accuracy_m=5.0, timestamp=time.time() - 20,  # 20 seconds ago
    )
    assert pos.is_stale is True


def test_provider_update():
    provider = GPSProvider()
    assert provider.has_fix is False

    provider.update(40.7128, -74.0060, speed_mps=5.0)
    assert provider.has_fix is True
    assert provider.position is not None
    assert abs(provider.position.latitude - 40.7128) < 0.001


def test_provider_history():
    provider = GPSProvider()
    for i in range(10):
        provider.update(40.0 + i * 0.001, -74.0)

    recent = provider.get_recent_positions(5)
    assert len(recent) == 5
