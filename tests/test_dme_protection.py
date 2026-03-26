"""Tests for DME protection features."""

from unittest.mock import MagicMock
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
