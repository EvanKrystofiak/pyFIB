"""Injectable clock so all timing (poll loops, sustained-idle windows) is
deterministic and instant in tests.

Ported from gisdep.clock so the coincidence autofocus matches gisdep's proven
method and folds together cleanly when gisdep is integrated into pyFIB.

RealClock uses monotonic wall-clock time. FakeClock uses virtual time that advances
only when sleep() is called, so a 10-second sustained-idle confirmation completes
instantly in a test while exercising the exact same code path.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import List


class Clock(ABC):
    @abstractmethod
    def now(self) -> float:
        """Monotonic seconds. Only differences are meaningful."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Block (or, for FakeClock, advance virtual time) by ``seconds``."""


class RealClock(Clock):
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock(Clock):
    """Virtual clock. sleep() advances time instantly; now() reflects it."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)
        self.sleeps: List[float] = []

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            seconds = 0.0
        self._t += seconds
        self.sleeps.append(seconds)

    def advance(self, seconds: float) -> None:
        """Advance virtual time without recording a sleep (test helper)."""
        self._t += seconds
