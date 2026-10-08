"""Simulation clock for accelerated replays (HW-6)."""

from __future__ import annotations

import time

import pandas as pd

from bdo.timeutil import UTC


class Clock:
    """Maps wall time to simulated time. speed=1 with no start is plain real time."""

    def __init__(self, speed: float = 1.0, start: pd.Timestamp | None = None) -> None:
        self.speed = speed
        self.restart(start)

    def restart(self, start: pd.Timestamp | None = None) -> None:
        self._wall0 = time.monotonic()
        self._sim0 = start if start is not None else pd.Timestamp.now(tz=UTC)

    def now(self) -> pd.Timestamp:
        elapsed = (time.monotonic() - self._wall0) * self.speed
        return self._sim0 + pd.Timedelta(seconds=elapsed)
