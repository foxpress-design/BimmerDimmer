"""Tests for speed limit parsing."""

from slower.gps.speed_limits import _parse_osm_maxspeed


def test_parse_mph():
    assert _parse_osm_maxspeed("45 mph") == 45
    assert _parse_osm_maxspeed("25mph") == 25


def test_parse_kmh():
    result = _parse_osm_maxspeed("50 km/h")
    assert result is not None
    assert 30 <= result <= 32  # 50 km/h ~ 31 mph


def test_parse_plain_number_us():
    assert _parse_osm_maxspeed("55") == 55
    assert _parse_osm_maxspeed("30") == 30


def test_parse_plain_number_high_as_kmh():
    # Values > 85 treated as km/h
    result = _parse_osm_maxspeed("100")
    assert result is not None
    assert 60 <= result <= 63  # 100 km/h ~ 62 mph


def test_parse_special_values():
    assert _parse_osm_maxspeed("none") is None
    assert _parse_osm_maxspeed("signals") is None
    assert _parse_osm_maxspeed("walk") is None


def test_parse_empty():
    assert _parse_osm_maxspeed("") is None
    assert _parse_osm_maxspeed(None) is None
