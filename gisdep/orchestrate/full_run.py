"""Full end-to-end workflow: cryo position -> focus + set WD -> deposition.

Every sub-operation here has been confirmed LIVE individually (cryo positioning,
autofocus, go-to-WD, needle in/out, deposition). This module chains them. Run the full
CHAIN with per-step confirmation for the first supervised runs.

The focus+WD step, per Evan: autofocus (coarse + fine) -> one bounded Z step toward the
target WD -> autofocus (fine) to CONFIRM the WD at the new position.

The single ``confirm(step)->bool`` callback gates every actuation sub-step (returning
False aborts). Pass confirm=None for an unattended run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMInterface
from gisdep.clock import Clock, RealClock
from gisdep.focus.autofocus import autofocus, AutoFocusConfig
from gisdep.gis.deposition import deposit, DepositionResult
from gisdep.stage.cryo import CryoMoveReport, CryoPositioner, CryoWaypoint
from gisdep.stage.mover import StageMover, StageMoveConfig, WDMoveResult

_log = logging.getLogger("gisdep.orchestrate.full_run")


class FullRunError(Exception):
    pass


class FullRunAborted(FullRunError):
    """A confirmation gate returned False."""


def _gate(confirm: Optional[Callable[[str], bool]], step: str) -> None:
    if confirm is not None and not confirm(step):
        raise FullRunAborted(f"operator declined: {step}")


@dataclass
class FocusWDResult:
    wd_before_m: float
    wd_after_focus_m: float
    wd_move: WDMoveResult
    wd_after_confirm_m: float

    def message(self) -> str:
        return (
            f"WD {self.wd_before_m*1e3:.4f} -> focus {self.wd_after_focus_m*1e3:.4f} "
            f"-> Z step -> confirm {self.wd_after_confirm_m*1e3:.4f} mm"
        )


def focus_and_set_wd(
    api: SmartSEMInterface,
    target_wd_m: float = 0.0055,
    clock: Optional[Clock] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    af_cfg: Optional[AutoFocusConfig] = None,
    wd_cfg: Optional[StageMoveConfig] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> FocusWDResult:
    """Autofocus (coarse+fine) -> one bounded Z step to target WD -> autofocus (fine) to
    confirm the WD at the new position."""
    clock = clock or RealClock()
    log = log or _log
    af_cfg = af_cfg or AutoFocusConfig()

    wd0 = api.get_analogue(P.AP_WD)
    log.info("focus+WD: autofocus coarse + fine")
    autofocus(api, do_coarse=True, do_fine=True, clock=clock, cfg=af_cfg, abort=abort, log=log)
    wd_focus = api.get_analogue(P.AP_WD)

    mover = StageMover(api, clock=clock, cfg=wd_cfg or StageMoveConfig(), log=log)
    mover.abort = abort
    wd_move = mover.move_to_wd(target_wd_m, confirm=confirm)  # gates the Z move itself

    log.info("focus+WD: autofocus fine (confirm)")
    autofocus(api, do_coarse=False, do_fine=True, clock=clock, cfg=af_cfg, abort=abort, log=log)
    wd_confirm = api.get_analogue(P.AP_WD)

    log.info(
        "focus+WD done: %.4f -> %.4f (focus) -> %.4f mm (confirmed)",
        wd0 * 1e3, wd_focus * 1e3, wd_confirm * 1e3,
    )
    return FocusWDResult(wd0, wd_focus, wd_move, wd_confirm)


@dataclass
class FullRunResult:
    grid: str
    port: int
    dwell_s: float
    cryo: CryoMoveReport
    focus_wd: FocusWDResult
    deposition: DepositionResult


def run_full_workflow(
    api: SmartSEMInterface,
    grid_name: str,
    port: int,
    dwell_s: float,
    target_wd_m: float = 0.0055,
    waypoints: Optional[Dict[str, CryoWaypoint]] = None,
    clock: Optional[Clock] = None,
    confirm: Optional[Callable[[str], bool]] = None,
    af_cfg: Optional[AutoFocusConfig] = None,
    wd_cfg: Optional[StageMoveConfig] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> FullRunResult:
    """cryo position (Step 1) -> focus + set WD (Step 2) -> deposition (Step 3)."""
    clock = clock or RealClock()
    log = log or _log

    def _check_abort():
        if abort is not None and abort.is_set():
            raise FullRunAborted("stop requested")

    log.info("=== FULL RUN: %s, GIS%d, dwell %.1fs ===", grid_name, port, dwell_s)

    _check_abort()
    log.info("STEP 1: cryo positioning -> %s (via CLEM SAFE)", grid_name)
    cryo = CryoPositioner(api, waypoints=waypoints, clock=clock, log=log).go_to_grid(
        grid_name, confirm=confirm, abort=abort
    )

    _check_abort()
    log.info("STEP 2: autofocus + set working distance (%.3f mm)", target_wd_m * 1e3)
    fw = focus_and_set_wd(
        api, target_wd_m, clock=clock, confirm=confirm, af_cfg=af_cfg, wd_cfg=wd_cfg,
        abort=abort, log=log,
    )

    _check_abort()
    log.info("STEP 3: GIS%d deposition, dwell %.1fs", port, dwell_s)
    # One confirmation before the gas step; the deposition itself runs with NO per-step
    # confirmation so the dwell is exactly dwell_s (never waiting on a click).
    _gate(confirm, f"START deposition GIS{port}, dwell {dwell_s:.1f}s (runs uninterrupted)")
    dep = deposit(api, port, dwell_s, clock=clock, confirm=None,
                  precheck_confirm=confirm, abort=abort, log=log)

    log.info("=== FULL RUN complete ===")
    return FullRunResult(grid_name, port, dwell_s, cryo, fw, dep)
