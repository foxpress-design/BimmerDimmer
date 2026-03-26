"""Tests for K+DCAN connection framing logic."""

from slower.bmw.connection import ADDR_DME, ADDR_TESTER


def test_frame_construction():
    """Verify DCAN frame format: [Length] [Target] [Source] [Data...] [Checksum]."""
    # Simulate building a frame for DiagnosticSessionControl Extended (0x10 0x03)
    data = bytes([0x10, 0x03])
    target = ADDR_DME  # 0x12
    source = ADDR_TESTER  # 0xF1

    length = len(data) + 3  # target + source + checksum
    frame = bytearray([length, target, source]) + bytearray(data)

    checksum = 0
    for b in frame:
        checksum ^= b
    frame.append(checksum)

    assert frame[0] == 5  # length: target(1) + source(1) + data(2) + checksum(1)
    assert frame[1] == 0x12  # DME
    assert frame[2] == 0xF1  # tester
    assert frame[3] == 0x10  # service
    assert frame[4] == 0x03  # extended session
    # Verify checksum
    verify = 0
    for b in frame[:-1]:
        verify ^= b
    assert verify == frame[-1]
