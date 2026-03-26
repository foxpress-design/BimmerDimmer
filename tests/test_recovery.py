"""Tests for DME recovery and startup check."""

from unittest.mock import MagicMock
import struct

from slower.bmw.recovery import check_stale_vmax, reset_vmax


def _make_mock_dme() -> MagicMock:
    dme = MagicMock()
    dme.initialize.return_value = True
    dme.disable_vmax.return_value = True
    return dme


def test_check_stale_vmax_detects_low_value():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True

    result = check_stale_vmax(dme)
    assert result.is_stale is True
    assert result.current_vmax_kmh == 85


def test_check_stale_vmax_ok_when_high():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 200
    dme.read_vmax_active.return_value = True

    result = check_stale_vmax(dme)
    assert result.is_stale is False


def test_check_stale_vmax_ok_when_inactive():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 60
    dme.read_vmax_active.return_value = False

    result = check_stale_vmax(dme)
    assert result.is_stale is False


def test_reset_vmax_disables_limiter():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True

    success = reset_vmax(dme)
    assert success is True
    dme.disable_vmax.assert_called_once()


def test_reset_vmax_returns_false_on_failure():
    dme = _make_mock_dme()
    dme.read_vmax.return_value = 85
    dme.read_vmax_active.return_value = True
    dme.disable_vmax.return_value = False

    success = reset_vmax(dme)
    assert success is False
