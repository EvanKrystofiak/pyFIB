"""Closed-loop GIS actuation backend.

Executes the confirmed per-port GIS commands and CONFIRMS each one via the live
readbacks (never blind delays). Nominal mechanical timings are used only as timeout
bounds; the readback decides completion.

!!! INSTRUMENT-UNTESTED actuation !!!
This is tested offline against the faithful mock only. Every method here executes a real
command on the instrument. It must not be used for a live deposition until the readbacks
have been verified live (scripts/verify_gis_state.py) and the needle-only staged test
has passed.

Safety guards enforced here (defence in depth; the deposition sequence enforces them
too):
  * insert_needle refuses if the OTHER port's needle is not confirmed Parked
    ("under no conditions insert GIS1 and GIS2 at the same time").
  * open_valve refuses unless this port's needle is confirmed Inserted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from gisdep.api.interface import SmartSEMInterface
from gisdep.clock import Clock, RealClock
from gisdep.gis.backend import GISBackend
from gisdep.gis.commands import commands_for
from gisdep.gis.state import GISPortReading, read_port


class GISActuationError(Exception):
    """Base class for closed-loop actuation failures."""


class GISConfirmTimeout(GISActuationError):
    """A commanded state was not confirmed via readback within the timeout."""


class MutualExclusionError(GISActuationError):
    """Refused because the other GIS needle is not parked."""


class GISAborted(GISActuationError):
    """A stop/abort was requested during GIS actuation."""


@dataclass
class GISBackendConfig:
    poll_interval_s: float = 0.25
    # Timeouts sized with generous margin over MEASURED live needle times (insert
    # ~2-3.4 s, retract ~2 s). Valve times not yet measured live; kept conservative.
    insert_timeout_s: float = 10.0
    retract_timeout_s: float = 10.0
    open_timeout_s: float = 5.0
    close_timeout_s: float = 5.0


def _other(port: int) -> int:
    return 2 if port == 1 else 1


class CommandGISBackend(GISBackend):
    def __init__(
        self,
        api: SmartSEMInterface,
        clock: Optional[Clock] = None,
        cfg: Optional[GISBackendConfig] = None,
    ) -> None:
        self.api = api
        self.clock = clock or RealClock()
        self.cfg = cfg or GISBackendConfig()
        self.abort = None  # optional object with .is_set() -> bool; stops actuation

    # --- readbacks ---------------------------------------------------------
    def read(self, port: int) -> GISPortReading:
        return read_port(self.api, commands_for(port).readbacks)

    def needle_inserted(self, port: int) -> Optional[bool]:
        return self.read(port).needle_inserted

    def valve_open(self, port: int) -> Optional[bool]:
        return self.read(port).valve_open

    def _wait(
        self,
        port: int,
        predicate: Callable[[GISPortReading], bool],
        timeout_s: float,
        what: str,
    ) -> GISPortReading:
        t0 = self.clock.now()
        while True:
            if self.abort is not None and self.abort.is_set():
                raise GISAborted(f"GIS{port} {what} aborted")
            r = self.read(port)
            if predicate(r):
                return r
            if self.clock.now() - t0 > timeout_s:
                raise GISConfirmTimeout(
                    f"GIS{port} {what} not confirmed within {timeout_s:.0f}s "
                    f"(last: {r.line()})"
                )
            self.clock.sleep(self.cfg.poll_interval_s)

    # --- actuation ---------------------------------------------------------
    def insert_needle(self, port: int) -> None:
        other = _other(port)
        ro = self.read(other)
        if not ro.needle_parked:
            raise MutualExclusionError(
                f"refuse to insert GIS{port}: GIS{other} needle is not Parked "
                f"(reads {ro.needle_meaning}). Never both needles inserted."
            )
        self.api.execute(commands_for(port).insert_needle)
        self._wait(port, lambda r: r.needle_inserted, self.cfg.insert_timeout_s, "insert")

    def retract_needle(self, port: int) -> None:
        self.api.execute(commands_for(port).retract_needle)
        self._wait(port, lambda r: r.needle_parked, self.cfg.retract_timeout_s, "retract")

    def open_valve(self, port: int) -> None:
        if not self.read(port).needle_inserted:
            raise GISActuationError(
                f"refuse to open GIS{port} valve: needle not confirmed Inserted"
            )
        self.api.execute(commands_for(port).open_valve)
        self._wait(port, lambda r: r.valve_open, self.cfg.open_timeout_s, "valve open")

    def close_valve(self, port: int) -> None:
        self.api.execute(commands_for(port).close_valve)
        self._wait(port, lambda r: r.valve_closed, self.cfg.close_timeout_s, "valve close")
