"""Safety limits and the pure Z-move recommendation for SEM/FIB coincidence.

This module NEVER touches the instrument. It only turns a measured working
distance and current Z into a *recommended* stage move, and checks that move
against hard safety limits. The rest of the branch reports the recommendation to
the operator, who makes the move by hand -- pyFIB does not move Z itself.

Geometry (confirmed on the instrument):
    * AP_WD is the true working distance, in metres.
    * Positive Z is CLOSER to the pole piece, which SHORTENS the working distance.
      So Z and WD move in opposite directions:
          delta_WD = target_WD - WD_meas
          delta_Z  = -delta_WD = WD_meas - target_WD
      e.g. WD measured 5.3 mm, target 5.0 mm -> delta_Z = +0.3 mm (raise Z toward
      the pole, shortening WD to 5.0). WD 4.7 mm -> delta_Z = -0.3 mm.

Coincidence: at SEM focus == 5 mm working distance the FIB is coincident,
regardless of tilt.

All limits are metres/degrees and live here so they are changed in exactly one
place. The operator has said 40 mm and 0.5 mm may be tightened later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

# --- The coincidence target -------------------------------------------------
TARGET_WD_M = 5.0e-3          # SEM/FIB coincidence working distance (5 mm)

# --- Hard Z bounds: NEVER cross these --------------------------------------
Z_HARD_MIN_M = 20.0e-3        # 20 mm
Z_HARD_MAX_M = 40.0e-3        # 40 mm -- must never be exceeded (may drop later)

# --- Per-move cap ----------------------------------------------------------
MAX_Z_STEP_M = 0.5e-3         # 0.5 mm max single Z change (may change later)

# --- Sanity window on the measured WD (focus is suspect outside this) ------
WD_SANE_MIN_M = 3.0e-3        # 3 mm
WD_SANE_MAX_M = 8.0e-3        # 8 mm

# --- Preconditions ---------------------------------------------------------
TILT_TOL_DEG = 0.1            # refuse unless stage tilt is ~0 (for now)
EHT_MIN_ON_V = 100.0          # AP_ACTUALKV above this => SEM beam considered ON

# How close to target counts as "already there" (no move needed).
AT_TARGET_TOL_M = 5.0e-6      # 5 um


def _mm(x_m: float) -> str:
    return f"{x_m * 1e3:.4f} mm"


@dataclass
class ZRecommendation:
    """A recommended (but NOT executed) Z move to reach the target WD."""
    wd_meas_m: float
    z_now_m: float
    target_wd_m: float
    delta_z_m: float
    z_target_m: float
    wd_in_window: bool
    within_step_cap: bool
    within_hard_bounds: bool
    already_at_target: bool
    messages: List[str] = field(default_factory=list)

    @property
    def safe_to_recommend(self) -> bool:
        """True only if the move passes every hard check."""
        return (self.wd_in_window and self.within_step_cap
                and self.within_hard_bounds)

    def format(self) -> str:
        L: List[str] = []
        L.append("SEM working-distance / coincidence measurement")
        L.append("-" * 60)
        L.append(f"  Measured WD (AP_WD)   : {_mm(self.wd_meas_m)}")
        L.append(f"  Target WD             : {_mm(self.target_wd_m)}")
        L.append(f"  Current Z             : {_mm(self.z_now_m)}")
        if self.already_at_target:
            L.append(f"  WD is already at the target within {_mm(AT_TARGET_TOL_M)}.")
            return "\n".join(L)
        L.append(f"  Z change to reach WD  : {self.delta_z_m * 1e3:+.4f} mm")
        L.append(f"  Would put Z at        : {_mm(self.z_target_m)}")
        for m in self.messages:
            L.append(f"  {m}")
        return "\n".join(L)


def recommend_z_move(wd_meas_m: float, z_now_m: float, *,
                     target_wd_m: float = TARGET_WD_M) -> ZRecommendation:
    """Compute the recommended Z move to bring WD to the target. Pure function.

    Never moves anything. Applies the sign convention (+Z shortens WD) and checks
    the result against the WD sanity window, the per-move cap, and the hard Z
    bounds. Failing checks are reported as messages; safe_to_recommend is False.
    """
    delta_z_m = wd_meas_m - target_wd_m          # +Z shortens WD
    z_target_m = z_now_m + delta_z_m

    wd_in_window = WD_SANE_MIN_M <= wd_meas_m <= WD_SANE_MAX_M
    within_step_cap = abs(delta_z_m) <= MAX_Z_STEP_M + 1e-12
    within_hard_bounds = Z_HARD_MIN_M <= z_target_m <= Z_HARD_MAX_M
    already = abs(wd_meas_m - target_wd_m) <= AT_TARGET_TOL_M

    messages: List[str] = []
    if not wd_in_window:
        messages.append(
            f"WARNING: measured WD {_mm(wd_meas_m)} is outside the trusted window "
            f"[{_mm(WD_SANE_MIN_M)}, {_mm(WD_SANE_MAX_M)}]; focus may have failed. "
            f"Do not move on this value.")
    if not within_step_cap:
        messages.append(
            f"WARNING: recommended move {abs(delta_z_m) * 1e3:.4f} mm exceeds the "
            f"per-move cap of {MAX_Z_STEP_M * 1e3:.3f} mm. Do NOT make this move in "
            f"one step; re-check focus.")
    if not within_hard_bounds:
        messages.append(
            f"WARNING: target Z {_mm(z_target_m)} is outside the hard limits "
            f"[{_mm(Z_HARD_MIN_M)}, {_mm(Z_HARD_MAX_M)}]. DO NOT MOVE.")
    if not messages and not already:
        messages.append("All safety checks passed.")

    return ZRecommendation(
        wd_meas_m=wd_meas_m, z_now_m=z_now_m, target_wd_m=target_wd_m,
        delta_z_m=delta_z_m, z_target_m=z_target_m,
        wd_in_window=wd_in_window, within_step_cap=within_step_cap,
        within_hard_bounds=within_hard_bounds, already_at_target=already,
        messages=messages,
    )
