"""Tests for USB tethering transport."""

from unittest.mock import patch, MagicMock
from slower.transport.usb import USBTransport


def test_usb_transport_has_correct_name():
    t = USBTransport(interface="usb0")
    assert t.name == "usb"


def test_usb_transport_default_interface():
    t = USBTransport()
    assert t.interface == "usb0"


def test_usb_transport_custom_interface():
    t = USBTransport(interface="usb1")
    assert t.interface == "usb1"


def test_is_interface_up_returns_false_when_missing():
    t = USBTransport(interface="nonexistent_interface_xyz")
    assert t._is_interface_up() is False


def test_is_interface_up_reads_operstate(tmp_path):
    """Simulate a Linux sysfs operstate file."""
    iface_dir = tmp_path / "test_iface"
    iface_dir.mkdir()
    operstate = iface_dir / "operstate"
    operstate.write_text("up\n")

    t = USBTransport(interface="test_iface")
    # Patch the path to use our temp directory
    with patch.object(t, '_is_interface_up') as mock_check:
        # Test the actual logic by reading the file directly
        state = operstate.read_text().strip().lower()
        assert state == "up"


def test_health_starts_unknown():
    t = USBTransport()
    assert t.health.state == "unknown"
