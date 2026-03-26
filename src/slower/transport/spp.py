"""Bluetooth Serial (SPP/RFCOMM) transport for GPS data.

Listens for Classic Bluetooth connections from a companion app.
Receives newline-delimited JSON GPS payloads over RFCOMM.

Requires: Linux with BlueZ (python socket AF_BLUETOOTH support).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from typing import TYPE_CHECKING

from slower.transport.health import TransportHealth

if TYPE_CHECKING:
    from slower.gps.provider import GPSProvider

logger = logging.getLogger(__name__)


class SPPTransport:
    """Bluetooth Serial RFCOMM server GPS transport."""

    name: str = "spp"

    def __init__(self, channel: int = 1) -> None:
        self.channel = channel
        self.health = TransportHealth(name="spp", timeout_sec=10.0)
        self._gps: GPSProvider | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._server_sock: socket.socket | None = None

    def start(self, gps: GPSProvider) -> None:
        """Start listening for Bluetooth Serial connections."""
        self._gps = gps
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True, name="spp-rfcomm")
        self._thread.start()
        logger.info("SPP transport starting on RFCOMM channel %d", self.channel)

    def stop(self) -> None:
        """Stop the SPP server."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        self._gps = None
        logger.info("SPP transport stopped")

    def _listen(self) -> None:
        """Listen for RFCOMM connections and handle GPS data."""
        try:
            self._server_sock = socket.socket(
                socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
            )
            self._server_sock.bind(("", self.channel))
            self._server_sock.listen(1)
            self._server_sock.settimeout(2.0)
            logger.info("SPP: listening on RFCOMM channel %d", self.channel)
        except (OSError, AttributeError) as e:
            logger.warning("SPP: Bluetooth socket not available: %s", e)
            return

        while self._running:
            try:
                client, addr = self._server_sock.accept()
                logger.info("SPP: client connected from %s", addr)
                handler = threading.Thread(
                    target=self._handle_client, args=(client,), daemon=True,
                    name="spp-client",
                )
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.error("SPP: server socket error")
                break

    def _handle_client(self, client: socket.socket) -> None:
        """Handle a connected Bluetooth Serial client."""
        buffer = ""
        client.settimeout(5.0)

        try:
            while self._running:
                try:
                    data = client.recv(1024)
                except socket.timeout:
                    continue

                if not data:
                    break

                buffer += data.decode("utf-8", errors="replace")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._process_line(line)
        except OSError as e:
            logger.warning("SPP: client disconnected: %s", e)
        finally:
            client.close()
            logger.info("SPP: client connection closed")

    def _process_line(self, line: str) -> None:
        """Parse a JSON GPS line and feed to GPSProvider."""
        try:
            data = json.loads(line)
            if self._gps:
                pos = self._gps.update(
                    lat=float(data["latitude"]),
                    lon=float(data["longitude"]),
                    speed_mps=data.get("speed"),
                    heading=data.get("heading"),
                    accuracy_m=float(data.get("accuracy", 50)),
                )
                if pos is not None:
                    self.health.record_success()
                else:
                    self.health.record_failure()
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            logger.warning("SPP: invalid GPS data: %s", e)
            self.health.record_failure()
