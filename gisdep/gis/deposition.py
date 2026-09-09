"""Closed-loop GIS deposition sequence for a single port.

Implements the sequence exactly as specified:

  preconditions -> insert needle -> open valve -> dwell -> close valve -> retract needle

with these invariants:
  * Dwell (valve-open duration) must be within [2, 30] s.
  * The other port's needle must be Parked before starting AND the backend re-checks the
    interlock at insert time -- never both GIS1 and GIS2 inserted.
  * The valve is only opened once the needle is confirmed Inserted.
  * The needle is only retracted once the valve is confirmed Closed -- EXCEPT on a
    close failure, where policy is to retract the failed needle anyway and raise a loud
    alarm (gas may still be flowing).
  * On any failure/abort, safe teardown runs: close valve, then retract needle (retract
    even if the close could not be confirmed, per the close-fail policy).

No autofocus / WD gate / tilt gate here -- those are prerequisites the caller runs first
(see gisdep.orchestrate.prepare). This module owns only the actuation sequence.

VALIDATED LIVE: a full short deposition (GIS1, 2 s dwell, per-step confirmation) ran
successfully on the instrument -- insert -> open valve -> dwell -> close -> retract, each
readback-confirmed. Keep per-step confirmation on for live runs. !!!
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from gisdep.api.interface import SmartSEMInterface
from gisdep.clock import Clock, RealClock
from gisdep.gis.actuate import (
    CommandGISBackend,
    GISBackend,
    GISConfirmTimeout,
)
from gisdep.gis.commands import commands_for
from gisdep.gis.state import read_port

_log = logging.getLogger("gisdep.gis.deposition")

DWELL_MIN_S = 2.0
DWELL_MAX_S = 30.0


class DepositionError(Exception):
    """Base class for deposition-sequence failures."""


class NotSafeToStartError(DepositionError):
    """Preconditions for starting the sequence were not met."""


class CloseFailure(DepositionError):
    """The valve did not confirm Closed. Needle retracted per policy; gas may flow."""


class OperatorDeclined(DepositionError):
    """The confirm callback returned False for a step."""


@dataclass
class DepositionResult:
    port: int
    dwell_s: float
    completed: bool
    message: str


def _other(port: int) -> int:
    return 2 if port == 1 else 1


def _confirm(confirm: Optional[Callable[[str], bool]], step: str) -> None:
    if confirm is not None and not confirm(step):
        raise OperatorDeclined(f"operator declined at step: {step}")


def _safe_teardown(backend: GISBackend, port: int, log: logging.Logger) -> None:
    """Close valve, then retract needle. Retract even if close cannot be confirmed."""
    close_ok = True
    try:
        backend.close_valve(port)
    except Exception:
        close_ok = False
        log.error("GIS%d teardown: valve close not confirmed; retracting anyway", port)
    try:
        backend.retract_needle(port)
    except Exception:
        log.critical(
            "GIS%d teardown: retract FAILED after close issue; MANUAL INTERVENTION", port
        )
        raise
    if not close_ok:
        raise CloseFailure(
            f"GIS{port} valve did not confirm Closed during teardown; needle retracted "
            f"-- gas may still be flowing, CHECK INSTRUMENT"
        )


def deposit(
    api: SmartSEMInterface,
    port: int,
    dwell_s: float,
    backend: Optional[GISBackend] = None,
    clock: Optional[Clock] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    tilt_cfg: Optional["TiltGateConfig"] = None,
    precheck_confirm: Optional[Callable[[str], bool]] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> DepositionResult:
    """Run one closed-loop GIS deposition on ``port`` with ``dwell_s`` valve-open time.

    Before insertion: the stage must be Idle (hard precondition) and the tilt must be in
    the cryo window (soft -- if it is not, ``precheck_confirm`` is asked whether to proceed
    anyway; declining, or no callback, aborts). ``confirm`` gates the mechanical steps of
    the sequence itself and is normally None so the dwell is exact.
    """
    from gisdep.prereq.checks import check_stage_idle, check_stage_tilt
    from gisdep.prereq.tilt import CRYO_GIS_TILT
    clock = clock or RealClock()
    log = log or _log
    backend = backend or CommandGISBackend(api, clock=clock)
    tilt_cfg = tilt_cfg or CRYO_GIS_TILT
    cmds = commands_for(port)  # validates the port

    if not (DWELL_MIN_S <= dwell_s <= DWELL_MAX_S):
        raise ValueError(
            f"dwell {dwell_s}s is outside the allowed [{DWELL_MIN_S:g}, {DWELL_MAX_S:g}] s"
        )

    # --- preconditions (live readback) ------------------------------------
    r = read_port(api, cmds.readbacks)
    if not (r.needle_parked and r.valve_closed):
        raise NotSafeToStartError(
            f"GIS{port} not in a safe start state (need Parked + Closed): {r.line()}"
        )
    other = _other(port)
    ro = read_port(api, commands_for(other).readbacks)
    if not ro.needle_parked:
        raise NotSafeToStartError(
            f"cannot start GIS{port}: GIS{other} needle is not Parked "
            f"(reads {ro.needle_meaning}). Never both needles inserted."
        )

    # stage must be idle before any needle insertion (hard -- no override)
    idle = check_stage_idle(api)
    if not idle.ok:
        raise NotSafeToStartError(
            f"stage is not idle before GIS{port} insertion: {idle.detail}"
        )

    # tilt must be in the cryo window; if not, confirm-to-proceed or abort (soft)
    tilt = check_stage_tilt(api, tilt_cfg)
    if not tilt.ok:
        log.warning("GIS%d insertion: %s", port, tilt.detail)
        msg = f"{tilt.detail} — proceed with GIS{port} insertion anyway?"
        if precheck_confirm is None or not precheck_confirm(msg):
            raise NotSafeToStartError(
                f"tilt not confirmed for GIS{port} insertion: {tilt.detail}"
            )

    attempted = False
    backend.abort = abort  # let insert/open/close/retract polling stop on request
    try:
        _confirm(confirm, f"insert GIS{port} needle")
        attempted = True
        backend.insert_needle(port)          # interlock re-checked inside

        _confirm(confirm, f"open GIS{port} valve for {dwell_s:.1f}s")
        backend.open_valve(port)             # guarded: needle must be Inserted

        log.info("GIS%d dwell: valve open for %.1fs", port, dwell_s)
        # Interruptible dwell: sleep in small slices so a stop request ends it promptly.
        dwell_end = clock.now() + dwell_s
        while clock.now() < dwell_end:
            if abort is not None and abort.is_set():
                from gisdep.gis.actuate import GISAborted
                raise GISAborted(f"GIS{port} deposition aborted during dwell")
            clock.sleep(min(0.1, max(0.0, dwell_end - clock.now())))

        _confirm(confirm, f"close GIS{port} valve")
        try:
            backend.close_valve(port)
        except GISConfirmTimeout as exc:
            # CLOSE-FAIL policy: retract the failed needle anyway, then alarm.
            log.error("GIS%d valve CLOSE not confirmed; retracting needle per policy", port)
            try:
                backend.retract_needle(port)
            except Exception:
                log.critical("GIS%d retract also failed after close-fail; MANUAL", port)
                raise
            raise CloseFailure(
                f"GIS{port} valve did not confirm Closed; needle retracted -- gas may "
                f"still be flowing, CHECK INSTRUMENT"
            ) from exc

        _confirm(confirm, f"retract GIS{port} needle")
        backend.retract_needle(port)         # guarded: valve confirmed Closed above
        log.info("GIS%d deposition complete", port)
        return DepositionResult(port, dwell_s, True, "completed")

    except CloseFailure:
        raise  # already retracted + alarmed
    except Exception as exc:
        if attempted:
            log.error("GIS%d deposition aborted (%s); safe teardown", port, exc)
            backend.abort = None  # teardown must run even when a stop was requested
            _safe_teardown(backend, port, log)
        raise
