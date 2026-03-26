"""K+DCAN USB cable serial connection management.

The K+DCAN cable (typically FTDI FT232-based) bridges USB to the OBD-II port.
In DCAN mode (pin 6 active, switch position 2), it communicates over CAN bus
at 500 kbps, presented as a serial port at 115200 baud.

BMW DCAN serial framing:
  [Length] [Target ECU] [Source (Tester)] [Service ID] [Data...] [Checksum]

  - Length: total bytes from Target through Checksum (inclusive)
  - Target: ECU address (0x12 for DME)
  - Source: Tester address (0xF1)
  - Checksum: XOR of all preceding bytes
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import serial

if TYPE_CHECKING:
    from slower.config import CableConfig

logger = logging.getLogger(__name__)

# BMW ECU addresses
ADDR_DME = 0x12  # Digital Motor Electronics (engine control)
ADDR_TESTER = 0xF1  # External diagnostic tester


class KDCANConnection:
    """Manages the serial connection to the K+DCAN cable."""

    def __init__(self, config: CableConfig) -> None:
        self.config = config
        self._serial: serial.Serial | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        """Open serial connection to the K+DCAN cable."""
        if self.connected:
            logger.warning("Already connected")
            return

        logger.info("Connecting to K+DCAN cable on %s", self.config.port)
        try:
            self._serial = serial.Serial(
                port=self.config.port,
                baudrate=self.config.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.config.timeout,
                write_timeout=self.config.timeout,
            )
            # Flush any stale data
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._connected = True
            logger.info("Connected to K+DCAN cable")
        except serial.SerialException as e:
            self._connected = False
            raise ConnectionError(f"Failed to connect to K+DCAN cable on {self.config.port}: {e}")

    def disconnect(self) -> None:
        """Close the serial connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._connected = False
        logger.info("Disconnected from K+DCAN cable")

    def send_raw(self, target: int, data: bytes) -> None:
        """Send a raw DCAN frame.

        Args:
            target: ECU address byte (e.g., 0x12 for DME)
            data: payload bytes (UDS service ID + parameters)
        """
        if not self.connected:
            raise ConnectionError("Not connected to K+DCAN cable")

        # Build frame: [Length] [Target] [Source] [Data...] [Checksum]
        # Length = 3 (target + source + checksum) + len(data)
        length = len(data) + 3
        frame = bytearray([length, target, ADDR_TESTER]) + bytearray(data)

        # Checksum: XOR of all bytes
        checksum = 0
        for b in frame:
            checksum ^= b
        frame.append(checksum)

        logger.debug("TX -> %s", frame.hex(" "))
        self._serial.write(frame)
        self._serial.flush()

    def receive_raw(self, timeout: float | None = None) -> tuple[int, bytes] | None:
        """Receive a DCAN response frame.

        Returns:
            Tuple of (source_ecu_address, payload_bytes) or None on timeout.
        """
        if not self.connected:
            raise ConnectionError("Not connected to K+DCAN cable")

        old_timeout = self._serial.timeout
        if timeout is not None:
            self._serial.timeout = timeout

        try:
            # Read length byte
            length_byte = self._serial.read(1)
            if not length_byte:
                return None

            length = length_byte[0]
            if length < 3:
                logger.warning("Invalid frame length: %d", length)
                return None

            # Read remaining bytes: target + source + data + checksum
            remaining = self._serial.read(length)
            if len(remaining) < length:
                logger.warning("Incomplete frame: expected %d, got %d", length, len(remaining))
                return None

            # Validate checksum
            frame = bytearray(length_byte) + bytearray(remaining)
            checksum = 0
            for b in frame[:-1]:
                checksum ^= b
            if checksum != frame[-1]:
                logger.warning(
                    "Checksum mismatch: expected 0x%02X, got 0x%02X", checksum, frame[-1]
                )
                return None

            source = frame[2]  # Who sent this response
            payload = bytes(frame[3:-1])  # Strip length, target, source, checksum

            logger.debug("RX <- [0x%02X] %s", source, payload.hex(" "))
            return source, payload

        finally:
            if timeout is not None:
                self._serial.timeout = old_timeout

    def send_and_receive(
        self, target: int, data: bytes, timeout: float = 2.0
    ) -> bytes | None:
        """Send a request and wait for the corresponding response.

        Args:
            target: ECU address
            data: UDS request payload
            timeout: seconds to wait for response

        Returns:
            Response payload bytes, or None on timeout.
        """
        self.send_raw(target, data)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            result = self.receive_raw(timeout=remaining)
            if result is None:
                continue
            source, payload = result
            # Accept response from the target ECU
            if source == target:
                return payload
            logger.debug("Ignoring response from 0x%02X (waiting for 0x%02X)", source, target)

        logger.warning("Timeout waiting for response from 0x%02X", target)
        return None

    def __enter__(self) -> KDCANConnection:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()
