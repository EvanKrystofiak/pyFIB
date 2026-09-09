"""Stage-tilt gate configuration and named presets.

Deliberately dependency-free (no api / no CheckResult import) so gisdep.prereq.checks
can import these without a circular import.

Cryo GIS deposition requires the stage tilted to ~30 deg (29-31 deg window). During
development a different, FLAT stage is used at tilt 0. These are two distinct modes and
the caller must choose explicitly; the gate defaults to the strict CRYO window so that
forgetting to choose fails safe (blocked on a flat dev stage) rather than silently
allowing a wrong-tilt cryo deposition.

Ranges are in DEGREES (matching GetStagePosition). Confirm the readback convention on
the instrument -- see PROBE_PROTOCOL.md -- and turn the confirmed values into tests.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TiltGateConfig:
    name: str
    min_deg: float
    max_deg: float

    def __post_init__(self) -> None:
        if self.min_deg > self.max_deg:
            raise ValueError("TiltGateConfig min_deg must be <= max_deg")

    def contains(self, tilt_deg: float) -> bool:
        return self.min_deg <= tilt_deg <= self.max_deg


# Real cryo GIS deposition: stage tilted to ~30 deg. Window comes from the config file.
def _cryo_tilt_from_config() -> TiltGateConfig:
    from gisdep.config import load_config
    lo, hi = load_config().tilt_window_deg()
    return TiltGateConfig(name="cryo_gis", min_deg=lo, max_deg=hi)


CRYO_GIS_TILT = _cryo_tilt_from_config()

# Development stand-in: a different, flat stage at tilt 0 (+/- 1 deg readback slack).
# NOT valid for real cryo deposition.
FLAT_STAGE_DEV_TILT = TiltGateConfig(name="flat_stage_dev", min_deg=-1.0, max_deg=1.0)
