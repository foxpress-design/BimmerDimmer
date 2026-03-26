"""Tests for E90 DME module."""

import struct

from slower.bmw.e90_dme import E90DME, compute_security_key


def test_security_key_computation():
    """Test that the security key algorithm produces consistent output."""
    seed = b"\x12\x34\x56\x78"
    key = compute_security_key(seed)
    assert len(key) == 4
    # Verify deterministic
    assert compute_security_key(seed) == key


def test_security_key_rejects_wrong_length():
    """Test that non-4-byte seeds are rejected."""
    try:
        compute_security_key(b"\x01\x02\x03")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_kmh_mph_conversion():
    assert abs(E90DME.kmh_to_mph(100) - 62.1371) < 0.01
    assert abs(E90DME.mph_to_kmh(62.1371) - 100) < 0.1


def test_security_key_zero_seed():
    """Zero seed should still produce a valid 4-byte key."""
    key = compute_security_key(b"\x00\x00\x00\x00")
    assert len(key) == 4
