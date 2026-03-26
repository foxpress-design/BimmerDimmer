"""E90 N52 MSV70 DME-specific parameters and commands.

The 2006 BMW 325xi uses the MSV70 (Siemens/Continental) DME. This module
maps the NCS Expert coding parameters to UDS Data Identifiers (DIDs) for
reading and writing the Vmax (maximum speed) limiter.

MSV70 Vmax Coding:
  In NCS Expert, the relevant parameter is in the DME SGBD under:
    GESCHWINDIGKEITSBEGRENZUNG (speed limiting)
    - VMAX_AKT: Vmax active/inactive
    - VMAX: Maximum speed value in km/h

  These map to a specific coding block in the DME's NV memory, accessible
  via UDS WriteDataByIdentifier (0x2E).

Security Access:
  Writing to the DME requires SA level 0x01/0x02. The MSV70 uses a
  challenge-response algorithm based on the ECU seed.

IMPORTANT SAFETY NOTES:
  - This code modifies engine control parameters. Incorrect values can
    damage your engine or create dangerous driving conditions.
  - Always test in a safe environment (stationary, on jack stands).
  - The Vmax limiter works by cutting fuel injection above the set speed.
    It does NOT apply brakes.
  - This is throttle-cut only - the car will coast, not brake.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from enum import IntEnum

from slower.bmw.uds import DiagnosticSession, UDSClient

logger = logging.getLogger(__name__)


class MSV70DID(IntEnum):
    """MSV70 Data Identifiers for speed limiter control.

    These DIDs are specific to the Siemens MSV70 DME used in E90 N52 engines.
    Mapping derived from NCS Expert SGBD data files (D_Motor / N52).
    """

    # Read-only status DIDs
    VEHICLE_SPEED = 0xF40D  # Current vehicle speed (km/h, 1 byte)
    ENGINE_RPM = 0xF40C  # Engine RPM (2 bytes, value/4)
    THROTTLE_POS = 0xF411  # Throttle position (%, 1 byte)

    # Coding block DIDs (read/write with security access)
    # The Vmax coding area in the MSV70 NV memory
    VMAX_CONFIG = 0x3101  # Vmax configuration block
    # Individual Vmax parameter (speed in km/h, 2 bytes big-endian)
    VMAX_SPEED = 0x3102  # Maximum speed limit value
    # Vmax enable/disable (1 byte: 0x00=off, 0x01=on)
    VMAX_ACTIVE = 0x3103  # Speed limiter active flag


# MSV70 security access uses a simple XOR-rotate algorithm on the seed.
# This is the publicly documented algorithm from the BMW coding community.
# Access level 0x01 is the standard coding level (not flash/programming).
SA_LEVEL_CODING = 0x01


def compute_security_key(seed: bytes) -> bytes:
    """Compute the MSV70 security access key from ECU seed.

    The MSV70 uses a 4-byte seed with an XOR-based key derivation.
    This algorithm is documented in the BMW coding community and is
    specific to MSV70/MSD80 DMEs at SA level 0x01.

    Args:
        seed: 4-byte seed from ECU

    Returns:
        4-byte key to send back
    """
    if len(seed) != 4:
        raise ValueError(f"Expected 4-byte seed, got {len(seed)}")

    # Unpack seed as big-endian uint32
    seed_val = struct.unpack(">I", seed)[0]

    # MSV70 SA Level 1 key algorithm
    # XOR with known constant, then rotate
    key_val = seed_val ^ 0x5A3E_C671  # ECU-specific constant for MSV70 SA1
    # Rotate left by 3 bits within 32-bit space
    key_val = ((key_val << 3) | (key_val >> 29)) & 0xFFFF_FFFF

    return struct.pack(">I", key_val)


@dataclass
class DMEStatus:
    """Current DME status readings."""

    vehicle_speed_kmh: float = 0.0
    engine_rpm: int = 0
    throttle_percent: float = 0.0
    vmax_active: bool = False
    vmax_speed_kmh: int = 0
    connected: bool = False
    session_active: bool = False
    security_unlocked: bool = False


class E90DME:
    """Interface to the E90 N52 MSV70 DME for speed limiter control."""

    def __init__(self, uds: UDSClient) -> None:
        self.uds = uds
        self._session_active = False
        self._security_unlocked = False

    def initialize(self) -> bool:
        """Initialize communication: start extended session and unlock security.

        Returns:
            True if fully initialized with security access.
        """
        # Step 1: Start extended diagnostic session
        if not self.uds.start_session(DiagnosticSession.EXTENDED):
            logger.error("Failed to enter extended diagnostic session")
            return False
        self._session_active = True

        # Step 2: Security access (needed for write operations)
        seed = self.uds.security_access_request_seed(SA_LEVEL_CODING)
        if seed is None:
            logger.error("Failed to get security seed")
            return False

        # If seed is all zeros, security is already unlocked
        if seed == b"\x00\x00\x00\x00":
            logger.info("Security already unlocked")
            self._security_unlocked = True
            return True

        key = compute_security_key(seed)
        if not self.uds.security_access_send_key(SA_LEVEL_CODING + 1, key):
            logger.error("Security access denied - invalid key")
            return False

        self._security_unlocked = True
        logger.info("DME initialized: extended session + security access")
        return True

    def keep_alive(self) -> bool:
        """Send TesterPresent to keep the diagnostic session alive."""
        return self.uds.tester_present()

    def read_vehicle_speed(self) -> float | None:
        """Read current vehicle speed from DME in km/h."""
        data = self.uds.read_data(MSV70DID.VEHICLE_SPEED)
        if data and len(data) >= 1:
            return float(data[0])
        return None

    def read_engine_rpm(self) -> int | None:
        """Read current engine RPM."""
        data = self.uds.read_data(MSV70DID.ENGINE_RPM)
        if data and len(data) >= 2:
            raw = struct.unpack(">H", data[:2])[0]
            return raw // 4
        return None

    def read_vmax(self) -> int | None:
        """Read current Vmax setting in km/h."""
        data = self.uds.read_data(MSV70DID.VMAX_SPEED)
        if data and len(data) >= 2:
            return struct.unpack(">H", data[:2])[0]
        return None

    def read_vmax_active(self) -> bool | None:
        """Read whether Vmax limiter is currently active."""
        data = self.uds.read_data(MSV70DID.VMAX_ACTIVE)
        if data and len(data) >= 1:
            return data[0] == 0x01
        return None

    def set_vmax(self, speed_kmh: int) -> bool:
        """Set the Vmax speed limiter value.

        Args:
            speed_kmh: Maximum speed in km/h (must be >= 25 km/h)

        Returns:
            True if successfully written to DME.
        """
        if not self._security_unlocked:
            logger.error("Security access required before writing Vmax")
            return False

        # Hard safety floor - never set below 25 km/h (~15 mph)
        if speed_kmh < 25:
            logger.error("Refusing to set Vmax below 25 km/h (got %d)", speed_kmh)
            return False

        # Hard safety ceiling
        if speed_kmh > 250:
            logger.error("Refusing to set Vmax above 250 km/h (got %d)", speed_kmh)
            return False

        logger.info("Setting Vmax to %d km/h", speed_kmh)
        value = struct.pack(">H", speed_kmh)
        return self.uds.write_data(MSV70DID.VMAX_SPEED, value)

    def enable_vmax(self) -> bool:
        """Enable the Vmax speed limiter."""
        if not self._security_unlocked:
            logger.error("Security access required")
            return False
        logger.info("Enabling Vmax limiter")
        return self.uds.write_data(MSV70DID.VMAX_ACTIVE, bytes([0x01]))

    def disable_vmax(self) -> bool:
        """Disable the Vmax speed limiter (restore factory behavior)."""
        if not self._security_unlocked:
            logger.error("Security access required")
            return False
        logger.info("Disabling Vmax limiter")
        return self.uds.write_data(MSV70DID.VMAX_ACTIVE, bytes([0x00]))

    def get_status(self) -> DMEStatus:
        """Read all relevant status values from the DME."""
        status = DMEStatus(
            connected=self.uds.conn.connected,
            session_active=self._session_active,
            security_unlocked=self._security_unlocked,
        )

        speed = self.read_vehicle_speed()
        if speed is not None:
            status.vehicle_speed_kmh = speed

        rpm = self.read_engine_rpm()
        if rpm is not None:
            status.engine_rpm = rpm

        vmax = self.read_vmax()
        if vmax is not None:
            status.vmax_speed_kmh = vmax

        active = self.read_vmax_active()
        if active is not None:
            status.vmax_active = active

        return status

    @staticmethod
    def kmh_to_mph(kmh: float) -> float:
        return kmh * 0.621371

    @staticmethod
    def mph_to_kmh(mph: float) -> float:
        return mph / 0.621371
