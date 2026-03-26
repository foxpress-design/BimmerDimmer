"""Heartbeat file writer for the watchdog system.

The main slower process calls write_heartbeat() periodically.
The standalone slower-watchdog process monitors the heartbeat file.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_PATH = "/tmp/slower-heartbeat"


def write_heartbeat(path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Touch the heartbeat file to signal the process is alive."""
    try:
        with open(path, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        logger.warning("Failed to write heartbeat: %s", e)


def read_heartbeat_age(path: str = DEFAULT_HEARTBEAT_PATH) -> float | None:
    """Read the age of the heartbeat file in seconds.

    Returns None if the file doesn't exist or can't be read.
    """
    try:
        with open(path) as f:
            ts = float(f.read().strip())
        return time.time() - ts
    except (OSError, ValueError):
        return None


def remove_heartbeat(path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Remove the heartbeat file on clean shutdown."""
    try:
        os.unlink(path)
    except OSError:
        pass
