"""Standalone watchdog that resets DME Vmax if the main process dies.

This is intentionally simple and does NOT import from the slower package
to avoid shared failure modes. It has its own minimal K+DCAN/UDS logic.

Usage:
    slower-watchdog
    slower-watchdog --port /dev/ttyUSB0
    slower-watchdog --heartbeat-path /tmp/slower-heartbeat
"""

from __future__ import annotations

import argparse
import logging
import os
import struct
import sys
import time

import serial

logger = logging.getLogger("slower-watchdog")

# Minimal constants (duplicated intentionally, no slower imports)
ADDR_DME = 0x12
ADDR_TESTER = 0xF1
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 115200
DEFAULT_HEARTBEAT_PATH = "/tmp/slower-heartbeat"
DEFAULT_CHECK_INTERVAL = 5.0
DEFAULT_TIMEOUT = 10.0


def _build_frame(target: int, data: bytes) -> bytes:
    """Build a DCAN frame: [Length][Target][Source][Data...][XOR Checksum]."""
    length = len(data) + 3
    frame = bytearray([length, target, ADDR_TESTER]) + bytearray(data)
    checksum = 0
    for b in frame:
        checksum ^= b
    frame.append(checksum)
    return bytes(frame)


def _send_disable_vmax(port: str, baudrate: int) -> bool:
    """Open a connection and send disable_vmax command.

    Sequence: Extended Session (0x10 0x03), then WriteDataByID (0x2E)
    to set VMAX_ACTIVE (0x3103) to 0x00.
    """
    try:
        ser = serial.Serial(port, baudrate, timeout=2.0, write_timeout=2.0)
        ser.reset_input_buffer()

        # 1. Enter Extended Diagnostic Session
        frame = _build_frame(ADDR_DME, bytes([0x10, 0x03]))
        ser.write(frame)
        ser.flush()
        time.sleep(0.5)
        ser.reset_input_buffer()

        # 2. Disable Vmax: WriteDataByIdentifier(0x2E, DID=0x3103, value=0x00)
        did_bytes = struct.pack(">H", 0x3103)
        frame = _build_frame(ADDR_DME, bytes([0x2E]) + did_bytes + bytes([0x00]))
        ser.write(frame)
        ser.flush()
        time.sleep(0.5)

        ser.close()
        logger.info("Sent disable_vmax command to DME")
        return True
    except (serial.SerialException, OSError) as e:
        logger.error("Failed to send disable_vmax: %s", e)
        return False


def _read_heartbeat_age(path: str) -> float | None:
    """Read the heartbeat file age in seconds."""
    try:
        with open(path) as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except (OSError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="BimmerDimmer watchdog")
    parser.add_argument("--port", default=os.environ.get("SLOWER_CABLE_PORT", DEFAULT_PORT))
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--heartbeat-path", default=DEFAULT_HEARTBEAT_PATH)
    parser.add_argument("--check-interval", type=float, default=DEFAULT_CHECK_INTERVAL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [WATCHDOG] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Watchdog started. Monitoring heartbeat at %s", args.heartbeat_path)
    logger.info("Serial port: %s, Timeout: %.0fs", args.port, args.timeout)

    sent_reset = False

    while True:
        age = _read_heartbeat_age(args.heartbeat_path)

        if age is None:
            logger.debug("No heartbeat file found (main process may not be running)")
            sent_reset = False
        elif age > args.timeout:
            if not sent_reset:
                logger.warning(
                    "Heartbeat stale (%.1fs > %.1fs). Main process appears dead.",
                    age, args.timeout,
                )
                logger.warning("Sending disable_vmax to DME...")
                _send_disable_vmax(args.port, args.baudrate)
                sent_reset = True
        else:
            if sent_reset:
                logger.info("Heartbeat restored. Main process is back.")
            sent_reset = False
            logger.debug("Heartbeat OK (age: %.1fs)", age)

        time.sleep(args.check_interval)


if __name__ == "__main__":
    main()
