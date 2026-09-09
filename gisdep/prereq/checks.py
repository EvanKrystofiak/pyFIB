"""Prerequisite checks run before autofocus / any GIS action.

Per Evan:
  * The operator visually checks sample position in the chamber scope -- this is a
    MANUAL acknowledgement, represented here as a boolean the GUI must supply. gisdep
    cannot verify it and never assumes it.
  * gisdep checks the stage is idle (DP_STAGE_IS == Idle).
  * gisdep checks the stage TILT is in the required window. For real cryo GIS this is
    ~30 deg (29-31); during development the flat stage sits at 0. The window is chosen
    by the caller via a TiltGateConfig and defaults to the strict cryo window so a
    missing choice fails safe. Tilt is read via GetStagePosition (degrees, §4.10).
  * "FIB gun valve closed" is a POSSIBLE future prerequisite, but the Remote API
    documents no readback for the gun-valve position (only CMD_FIB_GUN_VALVE_OPEN /
    _CLOSE). It is therefore stubbed and disabled by default until the correct
    readback parameter is found on the instrument (see gisdep/gis/probe.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMInterface
from gisdep.prereq.tilt import TiltGateConfig, CRYO_GIS_TILT


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class PrereqReport:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.ok]

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def summary(self) -> str:
        lines = []
        for r in self.results:
            lines.append(f"[{'OK ' if r.ok else 'FAIL'}] {r.name}: {r.detail}")
        return "\n".join(lines)


def check_chamber_confirmed(chamber_confirmed: bool) -> CheckResult:
    return CheckResult(
        name="chamber_scope_position",
        ok=bool(chamber_confirmed),
        detail=(
            "operator confirmed sample position in chamber scope"
            if chamber_confirmed
            else "operator has NOT confirmed chamber-scope position"
        ),
    )


def check_stage_idle(api: SmartSEMInterface) -> CheckResult:
    state = api.get_digital(P.DP_STAGE_IS)
    ok = state == P.STAGE_IDLE
    name = P.STAGE_IS_NAMES.get(state, str(state))
    return CheckResult(
        name="stage_idle",
        ok=ok,
        detail=f"DP_STAGE_IS = {name}" + ("" if ok else " (must be Idle)"),
    )


def check_stage_tilt(
    api: SmartSEMInterface,
    tilt_cfg: TiltGateConfig = CRYO_GIS_TILT,
) -> CheckResult:
    """Blocking. Stage tilt must fall inside the configured window.

    The active mode name is always surfaced in the detail so an operator cannot miss
    that, e.g., the permissive flat-stage-dev window is in force rather than cryo.
    """
    pos = api.get_stage_position()
    t = pos.tilt_deg
    ok = tilt_cfg.contains(t)
    detail = (
        f"stage tilt {t:.2f} deg vs [{tilt_cfg.min_deg:g}, {tilt_cfg.max_deg:g}] "
        f"deg (mode: {tilt_cfg.name})"
    )
    if not ok:
        detail += " -- OUT OF RANGE"
    elif tilt_cfg.name != CRYO_GIS_TILT.name:
        detail += " -- NOTE: not the cryo window"
    return CheckResult(name="stage_tilt", ok=ok, detail=detail)


def check_fib_gun_valve_closed(api: SmartSEMInterface) -> CheckResult:
    """STUB. No confirmed readback exists yet for the FIB gun-valve position.

    Returns a non-blocking, clearly-flagged result so it can appear in the report
    without gating anything. Wire this up once the instrument probe identifies the
    correct parameter.
    """
    return CheckResult(
        name="fib_gun_valve_closed",
        ok=True,  # non-blocking until a real readback is confirmed
        detail="NOT IMPLEMENTED -- no confirmed API readback for FIB gun-valve state",
    )


def run_prerequisites(
    api: SmartSEMInterface,
    chamber_confirmed: bool,
    tilt_cfg: TiltGateConfig = CRYO_GIS_TILT,
    include_fib_valve: bool = False,
) -> PrereqReport:
    report = PrereqReport()
    report.add(check_chamber_confirmed(chamber_confirmed))
    report.add(check_stage_idle(api))
    report.add(check_stage_tilt(api, tilt_cfg))
    if include_fib_valve:
        report.add(check_fib_gun_valve_closed(api))
    return report
