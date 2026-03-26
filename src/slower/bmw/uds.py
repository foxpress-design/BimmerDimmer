"""UDS (Unified Diagnostic Services) protocol for BMW DCAN communication.

UDS is the standard diagnostic protocol used by BMW E90 and later vehicles.
This module implements the UDS services needed for speed limiter control.

Key UDS Services used:
  0x10 - DiagnosticSessionControl (switch to extended/programming session)
  0x22 - ReadDataByIdentifier (read DME parameters)
  0x27 - SecurityAccess (authenticate for write access)
  0x2E - WriteDataByIdentifier (set Vmax and other parameters)
  0x31 - RoutineControl (execute ECU routines)
  0x3E - TesterPresent (keep diagnostic session alive)

Positive response = Service ID + 0x40 (e.g., 0x10 -> 0x50)
Negative response = 0x7F + Service ID + NRC (error code)
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from enum import IntEnum

from slower.bmw.connection import ADDR_DME, KDCANConnection

logger = logging.getLogger(__name__)


class UDSService(IntEnum):
    """UDS Service Identifiers."""

    DIAGNOSTIC_SESSION_CONTROL = 0x10
    ECU_RESET = 0x11
    READ_DATA_BY_ID = 0x22
    SECURITY_ACCESS = 0x27
    WRITE_DATA_BY_ID = 0x2E
    ROUTINE_CONTROL = 0x31
    TESTER_PRESENT = 0x3E
    NEGATIVE_RESPONSE = 0x7F


class DiagnosticSession(IntEnum):
    """Diagnostic session types."""

    DEFAULT = 0x01
    PROGRAMMING = 0x02
    EXTENDED = 0x03


class NegativeResponseCode(IntEnum):
    """UDS Negative Response Codes."""

    GENERAL_REJECT = 0x10
    SERVICE_NOT_SUPPORTED = 0x11
    SUBFUNCTION_NOT_SUPPORTED = 0x12
    INCORRECT_MESSAGE_LENGTH = 0x13
    CONDITIONS_NOT_CORRECT = 0x22
    REQUEST_SEQUENCE_ERROR = 0x24
    REQUEST_OUT_OF_RANGE = 0x31
    SECURITY_ACCESS_DENIED = 0x33
    INVALID_KEY = 0x35
    EXCEEDED_ATTEMPTS = 0x36
    RESPONSE_PENDING = 0x78


@dataclass
class UDSResponse:
    """Parsed UDS response."""

    service: int
    positive: bool
    data: bytes
    nrc: int | None = None  # Negative Response Code, if negative

    @property
    def nrc_name(self) -> str:
        if self.nrc is None:
            return ""
        try:
            return NegativeResponseCode(self.nrc).name
        except ValueError:
            return f"UNKNOWN_0x{self.nrc:02X}"


class UDSClient:
    """UDS protocol client for BMW ECU communication."""

    def __init__(self, connection: KDCANConnection, target: int = ADDR_DME) -> None:
        self.conn = connection
        self.target = target

    def _request(self, service: int, data: bytes = b"", timeout: float = 2.0) -> UDSResponse:
        """Send a UDS request and parse the response."""
        payload = bytes([service]) + data
        response = self.conn.send_and_receive(self.target, payload, timeout=timeout)

        if response is None:
            raise TimeoutError(f"No response for service 0x{service:02X}")

        # Handle response pending (0x78) - ECU needs more time
        while (
            len(response) >= 3
            and response[0] == UDSService.NEGATIVE_RESPONSE
            and response[2] == NegativeResponseCode.RESPONSE_PENDING
        ):
            logger.debug("Response pending, waiting...")
            response = self.conn.receive_raw(timeout=5.0)
            if response is None:
                raise TimeoutError("Timeout after response pending")
            _, response = response

        return self._parse_response(service, response)

    def _parse_response(self, expected_service: int, raw: bytes) -> UDSResponse:
        """Parse raw response bytes into a UDSResponse."""
        if not raw:
            raise ValueError("Empty response")

        # Negative response: 0x7F [service] [NRC]
        if raw[0] == UDSService.NEGATIVE_RESPONSE:
            nrc = raw[2] if len(raw) >= 3 else 0x10
            svc = raw[1] if len(raw) >= 2 else expected_service
            resp = UDSResponse(service=svc, positive=False, data=raw, nrc=nrc)
            logger.warning(
                "Negative response for 0x%02X: %s (0x%02X)", svc, resp.nrc_name, nrc
            )
            return resp

        # Positive response: service + 0x40
        expected_positive = expected_service + 0x40
        if raw[0] != expected_positive:
            raise ValueError(
                f"Unexpected response ID: 0x{raw[0]:02X} (expected 0x{expected_positive:02X})"
            )

        return UDSResponse(service=expected_service, positive=True, data=raw[1:])

    def start_session(self, session: DiagnosticSession = DiagnosticSession.EXTENDED) -> bool:
        """Start a diagnostic session (0x10).

        Extended session is required before reading/writing most parameters.
        """
        logger.info("Starting diagnostic session: %s", session.name)
        resp = self._request(UDSService.DIAGNOSTIC_SESSION_CONTROL, bytes([session]))
        return resp.positive

    def tester_present(self) -> bool:
        """Send TesterPresent (0x3E) to keep session alive.

        Must be sent periodically (every ~2 seconds) to prevent session timeout.
        """
        resp = self._request(UDSService.TESTER_PRESENT, bytes([0x00]), timeout=1.0)
        return resp.positive

    def read_data(self, identifier: int) -> bytes | None:
        """Read a data identifier from the ECU (0x22).

        Args:
            identifier: 16-bit Data Identifier (DID)

        Returns:
            Raw data bytes, or None on failure.
        """
        did_bytes = struct.pack(">H", identifier)
        resp = self._request(UDSService.READ_DATA_BY_ID, did_bytes)
        if resp.positive:
            # Response: [DID high] [DID low] [data...]
            return resp.data[2:]  # Strip echoed DID
        return None

    def write_data(self, identifier: int, value: bytes) -> bool:
        """Write data to an ECU identifier (0x2E).

        Requires active extended/programming session and security access.

        Args:
            identifier: 16-bit Data Identifier (DID)
            value: raw bytes to write

        Returns:
            True if write was acknowledged.
        """
        did_bytes = struct.pack(">H", identifier)
        resp = self._request(UDSService.WRITE_DATA_BY_ID, did_bytes + value)
        if resp.positive:
            logger.info("Write to DID 0x%04X successful", identifier)
        return resp.positive

    def security_access_request_seed(self, level: int = 0x01) -> bytes | None:
        """Request security seed (0x27, step 1).

        Args:
            level: Security access level (odd number = request seed)

        Returns:
            Seed bytes from ECU, or None on failure.
        """
        resp = self._request(UDSService.SECURITY_ACCESS, bytes([level]))
        if resp.positive:
            return resp.data[1:]  # Strip echoed access level
        return None

    def security_access_send_key(self, level: int, key: bytes) -> bool:
        """Send security key (0x27, step 2).

        Args:
            level: Security access level + 1 (even number = send key)
            key: Computed key bytes

        Returns:
            True if security access granted.
        """
        resp = self._request(UDSService.SECURITY_ACCESS, bytes([level]) + key)
        return resp.positive

    def routine_control(
        self, control_type: int, routine_id: int, data: bytes = b""
    ) -> bytes | None:
        """Execute an ECU routine (0x31).

        Args:
            control_type: 0x01=start, 0x02=stop, 0x03=request results
            routine_id: 16-bit routine identifier
            data: optional routine parameters

        Returns:
            Routine result data, or None on failure.
        """
        rid_bytes = struct.pack(">H", routine_id)
        resp = self._request(
            UDSService.ROUTINE_CONTROL, bytes([control_type]) + rid_bytes + data
        )
        if resp.positive:
            return resp.data
        return None
