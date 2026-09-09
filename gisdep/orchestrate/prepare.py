"""prepare_for_deposition: prerequisites -> autofocus -> WD gate.

This is the safe, testable core the GUI calls. It ends at a decision object and
NEVER touches the GIS. Whether to proceed to needle insertion is left to the caller,
so the operator always makes the final go/no-go with the WD verdict in front of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from gisdep.api.interface import SmartSEMInterface
from gisdep.api import params as P
from gisdep.clock import Clock, RealClock
from gisdep.focus.autofocus import AutoFocusConfig, AutoFocusResult, autofocus
from gisdep.focus.wd_gate import WDGateConfig, WDResult, WDVerdict, evaluate_wd
from gisdep.prereq.checks import PrereqReport, run_prerequisites
from gisdep.prereq.tilt import TiltGateConfig, CRYO_GIS_TILT

_log = logging.getLogger("gisdep.orchestrate.prepare")


class PrepStatus(Enum):
    READY = "ready"                        # ideal WD; clear to proceed
    READY_WITH_WARNING = "ready_warning"   # safe but suboptimal WD
    BLOCKED_PREREQ = "blocked_prereq"      # a prerequisite failed
    BLOCKED_WD = "blocked_wd"              # WD too close for the needle


@dataclass
class PrepResult:
    status: PrepStatus
    prereqs: PrereqReport
    autofocus: Optional[AutoFocusResult]
    wd: Optional[WDResult]
    message: str

    @property
    def clear_to_insert(self) -> bool:
        return self.status in (PrepStatus.READY, PrepStatus.READY_WITH_WARNING)


def prepare_for_deposition(
    api: SmartSEMInterface,
    chamber_confirmed: bool,
    tilt_cfg: Optional[TiltGateConfig] = None,
    do_coarse: bool = True,
    do_fine: bool = True,
    include_fib_valve_check: bool = False,
    clock: Optional[Clock] = None,
    af_cfg: Optional[AutoFocusConfig] = None,
    wd_cfg: Optional[WDGateConfig] = None,
    abort: Optional[object] = None,
    log: Optional[logging.Logger] = None,
) -> PrepResult:
    clock = clock or RealClock()
    af_cfg = af_cfg or AutoFocusConfig()
    wd_cfg = wd_cfg or WDGateConfig()
    # Default to the strict cryo tilt window: a missing choice fails safe rather than
    # silently accepting a wrong-tilt deposition. Dev on the flat stage must pass
    # FLAT_STAGE_DEV_TILT explicitly.
    tilt_cfg = tilt_cfg or CRYO_GIS_TILT
    log = log or _log

    # 1. Prerequisites (fail fast, before any auto routine).
    prereqs = run_prerequisites(
        api,
        chamber_confirmed=chamber_confirmed,
        tilt_cfg=tilt_cfg,
        include_fib_valve=include_fib_valve_check,
    )
    if not prereqs.ok:
        fails = ", ".join(r.name for r in prereqs.failures)
        return PrepResult(
            status=PrepStatus.BLOCKED_PREREQ,
            prereqs=prereqs,
            autofocus=None,
            wd=None,
            message=f"Prerequisites failed: {fails}",
        )

    # 2. Autofocus (coarse and/or fine). If BOTH are disabled, skip autofocus entirely
    #    and gate on the CURRENT working distance -- a fully read-only check that
    #    actuates nothing (useful for a first instrument run).
    if do_coarse or do_fine:
        af = autofocus(
            api, do_coarse=do_coarse, do_fine=do_fine,
            clock=clock, cfg=af_cfg, abort=abort, log=log,
        )
        wd_m = af.wd_after_m
    else:
        af = None
        wd_m = api.get_analogue(P.AP_WD)
        log.info("autofocus skipped; gating on current WD %.4f mm", wd_m * 1e3)

    # 3. WD gate.
    wd = evaluate_wd(wd_m, wd_cfg)

    if wd.verdict is WDVerdict.TOO_CLOSE:
        status = PrepStatus.BLOCKED_WD
    elif wd.verdict is WDVerdict.SAFE_SUBOPTIMAL:
        status = PrepStatus.READY_WITH_WARNING
    else:
        status = PrepStatus.READY

    return PrepResult(
        status=status,
        prereqs=prereqs,
        autofocus=af,
        wd=wd,
        message=wd.message(),
    )
