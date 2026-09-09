"""Site configuration -- stage positions and related constants, editable without touching
source.

Values live in a JSON file (human units: mm and degrees). Load order:
  1. an explicit path passed to load_config(), else
  2. $GISDEP_CONFIG, else
  3. ./gisdep_config.json,
falling back to the built-in DEFAULTS (which are the real, confirmed values) for anything
absent. A malformed file logs a warning and falls back to defaults -- config never breaks
a run. See gisdep_config.example.json for the shape.

Consumers: gisdep.stage.cryo builds DEFAULT_WAYPOINTS from here, and gisdep.prereq.tilt
builds the cryo tilt window from here; the GUI and CLIs read the scalar values (Z limits,
WD target, default dwell).

This module imports only the standard library (no gisdep imports) to avoid cycles.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

_log = logging.getLogger("gisdep.config")

# Built-in defaults = the real, confirmed values (mm / degrees).
DEFAULTS = {
    "wd_target_mm": 5.5,
    "z_limits_mm": [20.0, 40.0],
    "tilt_window_deg": [29.0, 31.0],
    "dwell_default_s": 2.0,
    "m_ref_mm": 12.99,
    "waypoints": {
        "CLEM_SAFE": {"x_mm": 40.561, "y_mm": 57.164, "z_mm": 23.000, "t_deg": 0, "r_deg": 12},
        "GRID1": {"x_mm": 44.691, "y_mm": 51.183, "z_mm": 29.497, "t_deg": 30, "r_deg": 12},
        "GRID2": {"x_mm": 48.534, "y_mm": 51.383, "z_mm": 29.497, "t_deg": 30, "r_deg": 12},
    },
}


@dataclass
class GisdepConfig:
    wd_target_m: float
    z_min_m: float
    z_max_m: float
    tilt_min_deg: float
    tilt_max_deg: float
    dwell_default_s: float
    m_ref_m: float
    _waypoints_mm: Dict[str, dict]
    source: str  # "defaults" or the file path used

    def tilt_window_deg(self) -> Tuple[float, float]:
        return (self.tilt_min_deg, self.tilt_max_deg)

    def waypoints_si(self) -> Dict[str, dict]:
        """Waypoints as SI dicts (metres/degrees) -- gisdep.stage.cryo builds
        CryoWaypoint objects from these (kept plain here to avoid an import cycle)."""
        out = {}
        for name, w in self._waypoints_mm.items():
            out[name] = {
                "x_m": w["x_mm"] / 1e3, "y_m": w["y_mm"] / 1e3, "z_m": w["z_mm"] / 1e3,
                "tilt_deg": float(w["t_deg"]), "rot_deg": float(w["r_deg"]),
                "m_ref_m": self.m_ref_m,
            }
        return out


def _deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _config_path(path: Optional[str]) -> Optional[Path]:
    p = path or os.environ.get("GISDEP_CONFIG") or "gisdep_config.json"
    return Path(p)


def load_config(path: Optional[str] = None) -> GisdepConfig:
    data = copy.deepcopy(DEFAULTS)
    source = "defaults"
    cfg_path = _config_path(path)
    if cfg_path is not None and cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                user = json.load(f)
            _deep_merge(data, user)
            source = str(cfg_path)
            _log.info("loaded config from %s", cfg_path)
        except Exception as exc:  # never break a run on a bad config
            _log.warning("failed to read config %s (%s); using defaults", cfg_path, exc)
    zlo, zhi = data["z_limits_mm"]
    tlo, thi = data["tilt_window_deg"]
    return GisdepConfig(
        wd_target_m=data["wd_target_mm"] / 1e3,
        z_min_m=zlo / 1e3, z_max_m=zhi / 1e3,
        tilt_min_deg=float(tlo), tilt_max_deg=float(thi),
        dwell_default_s=float(data["dwell_default_s"]),
        m_ref_m=data["m_ref_mm"] / 1e3,
        _waypoints_mm=data["waypoints"],
        source=source,
    )


def write_example(path: str = "gisdep_config.example.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULTS, f, indent=2)
