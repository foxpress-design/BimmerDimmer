"""DME recovery and startup safety checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slower.bmw.safety import GPS_LOSS_CAP_KMH

if TYPE_CHECKING:
    from slower.bmw.e90_dme import E90DME

logger = logging.getLogger(__name__)


@dataclass
class StaleVmaxCheck:
    """Result of checking for a stale Vmax value."""

    current_vmax_kmh: int | None
    vmax_active: bool
    is_stale: bool


def check_stale_vmax(dme: E90DME) -> StaleVmaxCheck:
    """Check if the DME has a stale (leftover) Vmax limit.

    A Vmax is considered stale if it is active and below GPS_LOSS_CAP_KMH,
    which suggests it was set by a previous session that crashed.
    """
    vmax = dme.read_vmax()
    active = dme.read_vmax_active()

    if vmax is None or active is None:
        logger.warning("Could not read Vmax status from DME")
        return StaleVmaxCheck(current_vmax_kmh=vmax, vmax_active=bool(active), is_stale=False)

    is_stale = active and vmax < GPS_LOSS_CAP_KMH
    if is_stale:
        logger.warning(
            "Stale Vmax detected: %d km/h (active). Likely from a previous crash.", vmax
        )

    return StaleVmaxCheck(current_vmax_kmh=vmax, vmax_active=active, is_stale=is_stale)


def reset_vmax(dme: E90DME) -> bool:
    """Reset the DME Vmax limiter to factory default (disabled).

    Reads current value, disables the limiter, and logs the change.
    """
    vmax_before = dme.read_vmax()
    active_before = dme.read_vmax_active()
    logger.info("Current Vmax: %s km/h, Active: %s", vmax_before, active_before)

    success = dme.disable_vmax()
    if success:
        logger.info("Vmax limiter disabled successfully")
    else:
        logger.error("Failed to disable Vmax limiter")

    return success
