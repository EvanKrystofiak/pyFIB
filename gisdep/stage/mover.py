"""Safety-gated stage movement with nudge-and-measure verification.

Stage motion is the most collision-prone operation in gisdep, so every move goes through
StageMover, which:

  * refuses to move unless BOTH GIS needles are Parked (a moving stage with an inserted
    needle is a crash) and the stage is Idle;
  * caps the size of a single relative move;
  * checks the target against the axis soft limits (AP_STAGE_HIGH/LOW_*), skipping the
    check with a warning if those params don't resolve;
  * confirms completion by reading BOTH idle AND arrival-at-target (so the premature-Idle
    behaviour that affects DP_* readbacks cannot end the wait early); and
  * VERIFIES the achieved move by readback -- command a known delta, measure the actual
    change, and fail if they disagree. This is how we learn the true sign/scale/units
    instead of assuming them.

nudge_and_verify() does a round trip (out then back), leaving the stage where it started.

!!! INSTRUMENT-UNTESTED actuation. Tested offline against the mock only. Use small
deltas, with GIS parked, under supervision, for the first live runs. !!!
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Callable, Optional

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMInterface, SmartSEMError, StagePosition
from gisdep.clock import Clock, RealClock
from gisdep.gis.state import GIS1, GIS2, read_port

_log = logging.getLogger("gisdep.stage.mover")

# axis key -> (StagePosition field, unit)
_AXES = {
    "x": ("x_m", "m"),
    "y": ("y_m", "m"),
    "z": ("z_m", "m"),
    "m": ("m_m", "m"),
    "t": ("tilt_deg", "deg"),
    "r": ("rot_deg", "deg"),
}
_LINEAR = {"x", "y", "z"}  # axes with AP_STAGE_HIGH/LOW_* limits we check


class StageError(Exception):
    pass


class StageSafetyError(StageError):
    """A safety precondition (idle / GIS parked / limits / cap) was not met."""


class StageMoveTimeout(StageError):
    pass


class StageVerifyError(StageError):
    """The measured move did not match the requested delta."""


class StageMoveDeclined(StageError):
    pass


class StageAborted(StageError):
    """A stop/abort was requested during a move."""


@dataclass
class StageMoveConfig:
    poll_interval_s: float = 0.25
    idle_timeout_s: float = 60.0
    max_delta_m: float = 0.001      # 1 mm cap on one relative linear move
    max_delta_deg: float = 2.0      # cap on one tilt/rotation nudge
    verify_tol_m: float = 5e-6      # 5 um position-verify tolerance
    verify_tol_deg: float = 0.05
    require_gis_parked: bool = True
    check_limits: bool = True
    # Hard Z limits (metres). Default [20, 40] mm, from the coincidence work
    # (+Z shortens WD). CONFIRM on the instrument before live use -- a wrong Z limit is
    # a crash risk. Enforced for Z IN ADDITION to any instrument AP_STAGE_HIGH/LOW_Z.
    z_min_m: float = 0.020
    z_max_m: float = 0.040
    # Confirmed dWD/dZ on both dev and cryo stages: +Z shortens WD 1:1.
    wd_per_z: float = -1.0
    # "reached target WD" tolerance for move_to_wd (5 um).
    wd_target_tol_m: float = 5e-6


@dataclass
class StageMoveResult:
    axis: str
    requested_delta: float
    measured_delta: float
    before: StagePosition
    after: StagePosition


@dataclass
class NudgeReport:
    axis: str
    delta: float
    out: StageMoveResult
    back: StageMoveResult
    returned_ok: bool


@dataclass
class WDMoveResult:
    target_wd_m: float
    wd_before_m: float
    wd_after_m: float
    requested_dz_m: float   # full Z delta needed to reach target (unclamped)
    applied_dz_m: float     # Z delta actually applied (clamped to cap + Z limits)
    clamped: bool           # True if a single step could not fully correct
    reached: bool           # within wd_target_tol_m of the target
    moved: bool             # whether a stage move was issued

    def message(self) -> str:
        b, a, t = self.wd_before_m * 1e3, self.wd_after_m * 1e3, self.target_wd_m * 1e3
        if not self.moved:
            if self.reached:
                return f"WD {b:.4f} mm already at target {t:.3f} mm; no move."
            return (f"WD {b:.4f} mm off target {t:.3f} mm but no safe Z step available "
                    f"(at a limit).")
        head = (f"WD {b:.4f} -> {a:.4f} mm (target {t:.3f}); "
                f"applied {self.applied_dz_m * 1e3:+.4f} mm Z")
        if self.reached:
            return head + " -- reached."
        return (head + f" -- capped; still {(t - a):+.4f} mm off, run again to continue.")


class StageMover:
    def __init__(
        self,
        api: SmartSEMInterface,
        clock: Optional[Clock] = None,
        cfg: Optional[StageMoveConfig] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.api = api
        self.clock = clock or RealClock()
        self.cfg = cfg or StageMoveConfig()
        self.log = log or _log
        self.abort = None  # optional object with .is_set() -> bool; stops a move

    def read(self) -> StagePosition:
        return self.api.get_stage_position()

    # --- safety ------------------------------------------------------------
    def _check_safe_to_move(self) -> None:
        if self.api.get_digital(P.DP_STAGE_IS) != P.STAGE_IDLE:
            raise StageSafetyError("stage is not idle; refuse to move")
        if self.cfg.require_gis_parked:
            for desc in (GIS1, GIS2):
                r = read_port(self.api, desc)
                if not r.needle_parked:
                    raise StageSafetyError(
                        f"GIS{desc.port} needle not Parked (reads {r.needle_meaning}); "
                        f"refuse to move stage"
                    )

    def _check_hard_z(self, target_value: float) -> None:
        if not (self.cfg.z_min_m <= target_value <= self.cfg.z_max_m):
            raise StageSafetyError(
                f"z target {target_value * 1e3:.4f} mm outside gisdep hard Z limits "
                f"[{self.cfg.z_min_m * 1e3:.1f}, {self.cfg.z_max_m * 1e3:.1f}] mm"
            )

    def _check_instrument_limits(self, axis: str, target_value: float) -> None:
        hi_p, lo_p = f"AP_STAGE_HIGH_{axis.upper()}", f"AP_STAGE_LOW_{axis.upper()}"
        try:
            high = self.api.get_analogue(hi_p)
            low = self.api.get_analogue(lo_p)
        except SmartSEMError:
            self.log.warning("stage limits %s/%s unavailable; skipping limit check", hi_p, lo_p)
            return
        if not (low <= target_value <= high):
            raise StageSafetyError(
                f"{axis} target {target_value:.6g} outside instrument limits "
                f"[{low:.6g}, {high:.6g}]"
            )

    def _check_axis_limits(self, axis: str, target_value: float) -> None:
        # relative nudges / WD corrections: enforce the gisdep hard Z range too
        if axis == "z":
            self._check_hard_z(target_value)
        self._check_instrument_limits(axis, target_value)

    # --- completion --------------------------------------------------------
    def _at_target(self, pos: StagePosition, target: StagePosition) -> bool:
        tm, td = self.cfg.verify_tol_m, self.cfg.verify_tol_deg
        return (
            abs(pos.x_m - target.x_m) <= tm
            and abs(pos.y_m - target.y_m) <= tm
            and abs(pos.z_m - target.z_m) <= tm
            and abs(pos.m_m - target.m_m) <= tm
            and abs(pos.tilt_deg - target.tilt_deg) <= td
            and abs(pos.rot_deg - target.rot_deg) <= td
        )

    def _wait_for_move_complete(self, target: StagePosition) -> StagePosition:
        t0 = self.clock.now()
        while True:
            if self.abort is not None and self.abort.is_set():
                raise StageAborted("stage move aborted")
            pos = self.read()
            idle = self.api.get_digital(P.DP_STAGE_IS) == P.STAGE_IDLE
            if idle and self._at_target(pos, target):
                return pos
            if self.clock.now() - t0 > self.cfg.idle_timeout_s:
                raise StageMoveTimeout(
                    f"stage move did not complete within {self.cfg.idle_timeout_s:.0f}s"
                )
            self.clock.sleep(self.cfg.poll_interval_s)

    # --- moves -------------------------------------------------------------
    def move_axis_relative(
        self,
        axis: str,
        delta: float,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> StageMoveResult:
        if axis not in _AXES:
            raise ValueError(f"unknown axis {axis!r}; choose from {sorted(_AXES)}")
        field, unit = _AXES[axis]
        cap = self.cfg.max_delta_m if unit == "m" else self.cfg.max_delta_deg
        if abs(delta) > cap:
            raise StageSafetyError(
                f"requested {axis} delta {delta:.6g}{unit} exceeds cap {cap:.6g}{unit}"
            )

        self._check_safe_to_move()
        before = self.read()
        target = replace(before, **{field: getattr(before, field) + delta})

        if axis in _LINEAR and self.cfg.check_limits:
            self._check_axis_limits(axis, getattr(target, field))

        if confirm is not None and not confirm(f"move stage {axis} by {delta:.6g}{unit}"):
            raise StageMoveDeclined(f"operator declined {axis} move")

        self.log.info("stage: move %s by %.6g%s", axis, delta, unit)
        self.api.move_stage(
            target.x_m, target.y_m, target.z_m,
            target.tilt_deg, target.rot_deg, target.m_m,
        )
        after = self._wait_for_move_complete(target)

        measured = getattr(after, field) - getattr(before, field)
        tol = self.cfg.verify_tol_m if unit == "m" else self.cfg.verify_tol_deg
        if abs(measured - delta) > tol:
            raise StageVerifyError(
                f"{axis}: requested {delta:.6g}{unit}, measured {measured:.6g}{unit} "
                f"(tol {tol:.6g})"
            )
        return StageMoveResult(axis, delta, measured, before, after)

    def nudge_and_verify(
        self,
        axis: str,
        delta: float,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> NudgeReport:
        """Move +delta, verify, then move -delta back. Leaves the stage where it started."""
        out = self.move_axis_relative(axis, delta, confirm=confirm)
        back = self.move_axis_relative(axis, -delta, confirm=confirm)
        field, unit = _AXES[axis]
        returned = abs(getattr(back.after, field) - getattr(out.before, field))
        tol = (self.cfg.verify_tol_m if unit == "m" else self.cfg.verify_tol_deg) * 2
        return NudgeReport(axis, delta, out, back, returned_ok=returned <= tol)

    def move_absolute(
        self,
        target: StagePosition,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> StagePosition:
        """Absolute move to ``target`` (for BIG moves; no delta cap).

        Master-z (M) is ALWAYS preserved -- it is never moved. Changed linear axes are
        checked against the instrument's AP_STAGE_HIGH/LOW limits (NOT the gisdep hard Z
        [20,40] range, which is a GIS-deposition constraint, not a positioning one). The
        move is confirmed by idle + arrival, then verified against the target.
        """
        self._check_safe_to_move()
        before = self.read()
        tgt = replace(target, m_m=before.m_m)  # never move M

        for axis in ("x", "y", "z"):
            field, _ = _AXES[axis]
            if abs(getattr(tgt, field) - getattr(before, field)) > 1e-9:
                self._check_instrument_limits(axis, getattr(tgt, field))

        changed = []
        for a in ("x", "y", "z", "t", "r"):
            field, unit = _AXES[a]
            if abs(getattr(tgt, field) - getattr(before, field)) > 1e-9:
                scale = 1e3 if unit == "m" else 1.0
                changed.append(f"{a}={getattr(tgt, field) * scale:.4g}{'mm' if unit=='m' else '°'}")
        desc = ", ".join(changed) if changed else "no change"

        if confirm is not None and not confirm(f"stage move -> {desc}"):
            raise StageMoveDeclined(f"operator declined move -> {desc}")

        self.log.info("stage move -> %s", desc)
        self.api.move_stage(
            tgt.x_m, tgt.y_m, tgt.z_m, tgt.tilt_deg, tgt.rot_deg, tgt.m_m
        )
        after = self._wait_for_move_complete(tgt)
        if not self._at_target(after, tgt):
            raise StageVerifyError(f"absolute move did not reach target ({desc})")
        return after

    def move_to_wd(
        self,
        target_wd_m: float,
        confirm: Optional[Callable[[str], bool]] = None,
    ) -> WDMoveResult:
        """Take ONE bounded Z step toward a target working distance.

        Uses the confirmed coupling dWD/dZ = cfg.wd_per_z (default -1, i.e. +Z shortens
        WD 1:1, confirmed on both the dev and cryo stages). The step is clamped to the
        max single-move cap AND the hard Z limits, so it never exceeds the maximum
        allowed movement. If one step cannot fully correct WD, it moves as far as is safe
        and reports the remaining error (run again to continue). All the usual safety
        gates still apply via move_axis_relative (GIS parked, stage idle, limits, cap).
        """
        wd0 = self.api.get_analogue(P.AP_WD)
        err = target_wd_m - wd0
        # already there?
        if abs(err) <= self.cfg.wd_target_tol_m:
            return WDMoveResult(target_wd_m, wd0, wd0, 0.0, 0.0, False, True, False)

        dz_full = err / self.cfg.wd_per_z
        cap = self.cfg.max_delta_m
        z_now = self.read().z_m
        # clamp to the max single move, then to the hard Z limits
        dz = max(-cap, min(cap, dz_full))
        dz = max(self.cfg.z_min_m - z_now, min(self.cfg.z_max_m - z_now, dz))
        clamped = abs(dz - dz_full) > 1e-12

        if abs(dz) < 1e-9:
            # off target but no safe step available (at a Z limit)
            return WDMoveResult(target_wd_m, wd0, wd0, dz_full, 0.0, True, False, False)

        self.log.info("move_to_wd: target %.4f mm -> Z step %+.4f mm",
                      target_wd_m * 1e3, dz * 1e3)
        self.move_axis_relative("z", dz, confirm=confirm)
        wd1 = self.api.get_analogue(P.AP_WD)
        reached = abs(target_wd_m - wd1) <= self.cfg.wd_target_tol_m
        return WDMoveResult(target_wd_m, wd0, wd1, dz_full, dz, clamped, reached, True)
