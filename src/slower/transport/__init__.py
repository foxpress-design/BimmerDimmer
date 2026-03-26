"""Multi-transport GPS data abstraction.

Supports WiFi HTTP, BLE GATT, and Bluetooth Serial (SPP) transports.
Each transport feeds GPS position updates into GPSProvider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider


@runtime_checkable
class GPSTransport(Protocol):
    """Protocol that all GPS transports implement."""

    name: str

    def start(self, gps: GPSProvider) -> None:
        """Start receiving GPS data and feeding it to the provider."""
        ...

    def stop(self) -> None:
        """Stop the transport."""
        ...
