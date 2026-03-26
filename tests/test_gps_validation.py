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
    # Wait long enough for elapsed time check to trigger, then jump 10km -> ~72000 km/h
    time.sleep(0.6)
    pos = gps.update(lat=42.4501, lon=-71.0589, accuracy_m=10.0)
    assert pos is None


def test_no_speed_validation_on_first_fix():
    gps = GPSProvider()
    # First fix with high speed should be accepted (no previous to compare)
    pos = gps.update(lat=42.36, lon=-71.06, speed_mps=40.0, accuracy_m=10.0)
    assert pos is not None
