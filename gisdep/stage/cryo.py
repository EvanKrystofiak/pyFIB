"""Cryo stage positioning -- the FIRST step, before autofocus / WD / GIS deposition.

Drives the cryostage to a grid position for GIS deposition, always via the CLEM SAFE
waypoint, with the axis ordering Evan specified as safest for big stage moves:

  1. To CLEM SAFE: move Z FIRST, then X, Y, T, R.   (M is NEVER moved.)
  2. Verify CLEM SAFE by reading back the stage position.
  3. To the chosen grid: move X, Y, T, R FIRST, then Z.   (M is NEVER moved.)
  4. Verify the grid position.

Rationale: get Z into the safe configuration before swinging X/Y/tilt/rotation around,
and only bring Z in (the approach) last.

VALIDATED LIVE on the instrument: a full go_to_grid("GRID1") executed the choreography
and verified every axis exactly (CLEM SAFE and GRID1 read back on target to displayed
precision; M was already 12.99 mm and was not moved; the 10 um / 0.1 deg verify
tolerances were adequate). DEFAULT_WAYPOINTS below are the REAL coordinates from Evan's
files. Re-verify on the instrument if the sample/holder setup changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from gisdep.api.interface import SmartSEMInterface, StagePosition
from gisdep.clock import Clock, RealClock
from gisdep.stage.mover import StageMover, StageMoveConfig, StageError

_log = logging.getLogger("gisdep.stage.cryo")

_XYTR = ("x", "y", "t", "r")
_FIELD = {"x": "x_m", "y": "y_m", "z": "z_m", "t": "tilt_deg", "r": "rot_deg"}


@dataclass(frozen=True)
class CryoWaypoint:
    name: str
    x_m: float
    y_m: float
    z_m: float
    tilt_deg: float
    rot_deg: float
    placeholder: bool = False  # True until real coordinates are provided
    m_ref_m: float = None      # master-z (M) these coords were recorded at; NOT moved


# Waypoints come from the config file (gisdep_config.json) if present, else the built-in
# real coordinates. Edit coordinates there, not here.
def _build_default_waypoints() -> Dict[str, CryoWaypoint]:
    from gisdep.config import load_config
    out = {}
    for name, w in load_config().waypoints_si().items():
        out[name] = CryoWaypoint(
            name, w["x_m"], w["y_m"], w["z_m"], w["tilt_deg"], w["rot_deg"],
            placeholder=False, m_ref_m=w["m_ref_m"],
        )
    return out


DEFAULT_WAYPOINTS: Dict[str, CryoWaypoint] = _build_default_waypoints()


@dataclass
class CryoMoveReport:
    grid: str
    steps: List[str]
    final: StagePosition
    reached: bool
    used_placeholders: bool
    m_current_m: float = None
    m_ref_m: float = None
    m_matches: bool = True


class CryoPositioner:
    def __init__(
        self,
        api: SmartSEMInterface,
        waypoints: Optional[Dict[str, CryoWaypoint]] = None,
        clock: Optional[Clock] = None,
        cfg: Optional[StageMoveConfig] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.api = api
        self.waypoints = waypoints if waypoints is not None else DEFAULT_WAYPOINTS
        self.clock = clock or RealClock()
        # Looser verify tolerances than a nudge; big moves take longer.
        self.cfg = cfg or StageMoveConfig(
            verify_tol_m=1e-5, verify_tol_deg=0.1, idle_timeout_s=120.0
        )
        self.log = log or _log
        self.mover = StageMover(api, clock=self.clock, cfg=self.cfg, log=self.log)

    def go_to_grid(
        self, grid_name: str, confirm: Optional[Callable[[str], bool]] = None,
        abort: Optional[object] = None,
    ) -> CryoMoveReport:
        self.mover.abort = abort
        if "CLEM_SAFE" not in self.waypoints:
            raise ValueError("CLEM_SAFE waypoint is required")
        if grid_name not in self.waypoints:
            raise ValueError(
                f"unknown waypoint {grid_name!r}; have {sorted(self.waypoints)}"
            )
        clem = self.waypoints["CLEM_SAFE"]
        grid = self.waypoints[grid_name]
        placeholders = clem.placeholder or grid.placeholder
        if placeholders:
            self.log.warning(
                "cryo: using PLACEHOLDER coordinates -- not for real positioning"
            )
        steps: List[str] = []

        # M sanity check: these positions were recorded at a specific master-z. We never
        # move M, so warn (do not block) if the current M differs from the recorded one.
        m_ref = grid.m_ref_m if grid.m_ref_m is not None else clem.m_ref_m
        m_now = self.mover.read().m_m
        m_matches = True
        if m_ref is not None:
            m_matches = abs(m_now - m_ref) <= 1e-4  # 0.1 mm
            if not m_matches:
                self.log.warning(
                    "master-z M is %.4f mm but %s was recorded at %.4f mm; M is NOT moved "
                    "-- verify M before trusting the position.",
                    m_now * 1e3, grid_name, m_ref * 1e3,
                )
                steps.append(
                    f"WARNING: M {m_now*1e3:.4f} mm != recorded {m_ref*1e3:.4f} mm "
                    f"(M not moved)"
                )

        # Phase A -- to CLEM SAFE: ONE confirmation for the whole move (Z first, then
        # X,Y,T,R). M never moves.
        self._gate(confirm, "CLEM SAFE", clem)
        self.log.info("cryo: -> CLEM_SAFE (Z first, then X,Y,T,R)")
        self._move_axes(("z",), clem, steps, "CLEM_SAFE Z")
        self._move_axes(_XYTR, clem, steps, "CLEM_SAFE X,Y,T,R")
        self._verify_at(clem, "CLEM_SAFE")
        steps.append("CLEM_SAFE reached and verified")

        # Phase B -- to grid: ONE confirmation for the whole move (X,Y,T,R first, then Z).
        self._gate(confirm, grid_name, grid)
        self.log.info("cryo: -> %s (X,Y,T,R first, then Z)", grid_name)
        self._move_axes(_XYTR, grid, steps, f"{grid_name} X,Y,T,R")
        self._move_axes(("z",), grid, steps, f"{grid_name} Z")
        self._verify_at(grid, grid_name)
        steps.append(f"{grid_name} reached and verified")

        return CryoMoveReport(grid_name, steps, self.mover.read(), True, placeholders,
                              m_current_m=m_now, m_ref_m=m_ref, m_matches=m_matches)

    # --- helpers -----------------------------------------------------------
    def _gate(self, confirm, name: str, wp: CryoWaypoint) -> None:
        if confirm is None:
            return
        desc = (
            f"Move to {name}: X={wp.x_m*1e3:.3f} Y={wp.y_m*1e3:.3f} Z={wp.z_m*1e3:.3f} mm, "
            f"T={wp.tilt_deg:.1f} R={wp.rot_deg:.1f} deg (M not moved)"
        )
        if not confirm(desc):
            raise StageError(f"operator declined move to {name}")

    def _move_axes(self, axes, wp: CryoWaypoint, steps: List[str], label: str) -> None:
        cur = self.mover.read()
        target = StagePosition(
            x_m=wp.x_m if "x" in axes else cur.x_m,
            y_m=wp.y_m if "y" in axes else cur.y_m,
            z_m=wp.z_m if "z" in axes else cur.z_m,
            tilt_deg=wp.tilt_deg if "t" in axes else cur.tilt_deg,
            rot_deg=wp.rot_deg if "r" in axes else cur.rot_deg,
            m_m=cur.m_m,  # never move M
        )
        self.mover.move_absolute(target, confirm=None)  # the phase gate already confirmed
        steps.append(f"moved {label}")

    def _verify_at(self, wp: CryoWaypoint, name: str) -> None:
        # explicit GetStagePosition read-back verification
        pos = self.mover.read()
        tm, td = self.cfg.verify_tol_m, self.cfg.verify_tol_deg
        ok = (
            abs(pos.x_m - wp.x_m) <= tm
            and abs(pos.y_m - wp.y_m) <= tm
            and abs(pos.z_m - wp.z_m) <= tm
            and abs(pos.tilt_deg - wp.tilt_deg) <= td
            and abs(pos.rot_deg - wp.rot_deg) <= td
        )
        if not ok:
            raise StageError(
                f"{name} verification failed: at X={pos.x_m*1e3:.4f} Y={pos.y_m*1e3:.4f} "
                f"Z={pos.z_m*1e3:.4f} mm T={pos.tilt_deg:.3f} R={pos.rot_deg:.3f} deg"
            )
        self.log.info(
            "%s verified: X=%.4f Y=%.4f Z=%.4f mm T=%.3f R=%.3f deg (M unchanged)",
            name, pos.x_m * 1e3, pos.y_m * 1e3, pos.z_m * 1e3, pos.tilt_deg, pos.rot_deg,
        )
