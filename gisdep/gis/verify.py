"""Live, read-only GIS state monitor.

Two uses:
  * snapshot -- read the four readbacks once and interpret them.
  * watch    -- poll on an interval and report every CHANGE, so an operator can drive
                the GIS by hand (insert/retract, open/close in SmartFIB) and confirm the
                readbacks track each transition.

Strictly read-only: this only calls get_digital / get_digital_text. It never sets a
parameter and never executes a command. It is therefore safe to run on the instrument
while you manually cycle the GIS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from gisdep.api.interface import SmartSEMInterface
from gisdep.clock import Clock, RealClock
from gisdep.gis.state import (
    DEFAULT_PORTS,
    GISPortParams,
    GISPortReading,
    read_port,
)


@dataclass
class GISChange:
    port: int
    field: str            # "needle" or "valve"
    old_raw: Optional[int]
    new_raw: Optional[int]
    old_meaning: str
    new_meaning: str

    def line(self) -> str:
        return (
            f"GIS{self.port} {self.field}: "
            f"{self.old_raw} ({self.old_meaning}) -> {self.new_raw} ({self.new_meaning})"
        )


class GISStateMonitor:
    def __init__(
        self,
        api: SmartSEMInterface,
        ports: Tuple[GISPortParams, ...] = DEFAULT_PORTS,
        read_text: bool = True,
    ) -> None:
        self._api = api
        self._ports = ports
        self._read_text = read_text
        self._last: Dict[int, GISPortReading] = {}

    def poll(self) -> Tuple[Dict[int, GISPortReading], List[GISChange]]:
        """Read all ports once. Returns (readings, changes-since-last-poll).

        The first poll establishes a baseline and reports no changes.
        """
        readings = {
            p.port: read_port(self._api, p, self._read_text) for p in self._ports
        }
        changes: List[GISChange] = []
        for port, r in readings.items():
            last = self._last.get(port)
            if last is None:
                continue
            if last.needle_raw != r.needle_raw:
                changes.append(
                    GISChange(
                        port, "needle", last.needle_raw, r.needle_raw,
                        last.needle_meaning, r.needle_meaning,
                    )
                )
            if last.valve_raw != r.valve_raw:
                changes.append(
                    GISChange(
                        port, "valve", last.valve_raw, r.valve_raw,
                        last.valve_meaning, r.valve_meaning,
                    )
                )
        self._last = readings
        return readings, changes

    def watch(
        self,
        clock: Optional[Clock] = None,
        interval_s: float = 1.0,
        on_change: Optional[Callable[[GISChange], None]] = None,
        stop: Optional[Callable[[Dict[int, GISPortReading]], bool]] = None,
        hook: Optional[Callable[[int], None]] = None,
        max_iterations: Optional[int] = None,
    ) -> None:
        """Poll repeatedly, invoking on_change for each transition.

        ``hook(i)`` runs at the start of each iteration (a test seam for mutating a
        mock between polls). ``stop(readings)`` ends the loop when it returns True.
        ``max_iterations`` bounds the loop for tests.
        """
        clock = clock or RealClock()
        i = 0
        while True:
            if hook is not None:
                hook(i)
            readings, changes = self.poll()
            if on_change is not None:
                for ch in changes:
                    on_change(ch)
            i += 1
            if max_iterations is not None and i >= max_iterations:
                return
            if stop is not None and stop(readings):
                return
            clock.sleep(interval_s)


def snapshot_text(readings: Dict[int, GISPortReading]) -> str:
    lines = ["GIS state (READ-ONLY):"]
    for port in sorted(readings):
        r = readings[port]
        lines.append("  " + r.line())
        safe = "safe (parked + closed)" if r.is_safe else "NOT in safe state"
        lines.append(f"        -> {safe}")
    return "\n".join(lines)
