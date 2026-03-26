"""Per-transport health tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TransportHealth:
    """Tracks the health state of a single transport or connection."""

    name: str
    timeout_sec: float = 10.0
    consecutive_failures: int = 0
    last_success_time: float | None = None
    _failure_threshold: int = 3

    def record_success(self) -> None:
        """Record a successful data exchange."""
        self.last_success_time = time.monotonic()
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed data exchange."""
        self.consecutive_failures += 1

    @property
    def is_healthy(self) -> bool:
        return self.state == "healthy"

    @property
    def state(self) -> str:
        """Current state: 'unknown', 'healthy', 'degraded', or 'lost'."""
        if self.last_success_time is None:
            return "unknown"

        age = time.monotonic() - self.last_success_time
        if age > self.timeout_sec:
            return "lost"

        if self.consecutive_failures >= self._failure_threshold:
            return "lost"

        if self.consecutive_failures > 0:
            return "degraded"

        return "healthy"
