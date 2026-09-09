"""Working-distance gate for GIS insertion.

Physical picture (from Evan):
  * Ideal cryo GIS deposition is at WD = 5.5 mm.
  * The needle physically inserts to the equivalent of ~5.0 mm; the extra 0.5 mm is
    the safety clearance.
  * LARGER working distances are SAFER but deposit differently (weaker) than 5.5 mm.
  * SMALLER working distances eat into the 0.5 mm clearance -> collision risk.

So the gate is asymmetric:
  * below the floor  -> TOO_CLOSE  (BLOCK insertion; collision risk)
  * at ~5.5 mm       -> IDEAL      (proceed; correct deposition)
  * above ~5.5 mm    -> SAFE_SUBOPTIMAL (physically safe, but deposition differs)

!!! THRESHOLD VALUES ARE ASSUMPTIONS pending confirmation from Evan. !!!
The default floor equals the target (5.5 mm) because the 0.5 mm clearance is already
baked into the needle insertion depth, so anything below 5.5 mm reduces that margin.
``ideal_tol_m`` (how wide a band around 5.5 counts as "ideal") and ``measure_tol_m``
(float/measurement slack on the floor comparison) are guesses -- tune on the
instrument and turn the result into a regression test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WDVerdict(Enum):
    TOO_CLOSE = "too_close"            # BLOCK
    IDEAL = "ideal"                    # proceed
    SAFE_SUBOPTIMAL = "safe_suboptimal"  # proceed with warning


@dataclass
class WDGateConfig:
    target_m: float = 0.0055       # ideal deposition WD (5.5 mm)
    floor_m: float = 0.0055        # hard minimum; below this insertion is blocked
    ideal_tol_m: float = 0.0001    # +band above target still counted "ideal" (0.1 mm)
    measure_tol_m: float = 2e-5    # slack on the floor comparison (20 um)

    def __post_init__(self) -> None:
        if self.floor_m > self.target_m + self.ideal_tol_m:
            raise ValueError("floor_m is above the ideal band; check configuration")
        for name in ("target_m", "floor_m", "ideal_tol_m", "measure_tol_m"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass
class WDResult:
    wd_m: float
    verdict: WDVerdict
    cfg: WDGateConfig

    @property
    def wd_mm(self) -> float:
        return self.wd_m * 1e3

    @property
    def blocks_insertion(self) -> bool:
        return self.verdict is WDVerdict.TOO_CLOSE

    @property
    def is_ideal(self) -> bool:
        return self.verdict is WDVerdict.IDEAL

    def message(self) -> str:
        t = self.cfg.target_m * 1e3
        if self.verdict is WDVerdict.TOO_CLOSE:
            return (
                f"WD {self.wd_mm:.3f} mm is below the {self.cfg.floor_m * 1e3:.3f} mm "
                f"floor -- too close for the needle; insertion BLOCKED."
            )
        if self.verdict is WDVerdict.IDEAL:
            return f"WD {self.wd_mm:.3f} mm is at the {t:.1f} mm target -- OK to insert."
        return (
            f"WD {self.wd_mm:.3f} mm is safely above the {t:.1f} mm target -- insertion "
            f"is physically safe but deposition will differ from the {t:.1f} mm ideal."
        )


def evaluate_wd(wd_m: float, cfg: WDGateConfig = None) -> WDResult:
    """Classify a measured working distance against the gate configuration."""
    cfg = cfg or WDGateConfig()
    if wd_m < cfg.floor_m - cfg.measure_tol_m:
        verdict = WDVerdict.TOO_CLOSE
    elif wd_m <= cfg.target_m + cfg.ideal_tol_m:
        verdict = WDVerdict.IDEAL
    else:
        verdict = WDVerdict.SAFE_SUBOPTIMAL
    return WDResult(wd_m=wd_m, verdict=verdict, cfg=cfg)
