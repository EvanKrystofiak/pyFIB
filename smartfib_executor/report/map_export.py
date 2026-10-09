"""Positions map for pyCLEM (v0.41.0).

Writes the session's positions as a calibrated image plus sidecars, so pyCLEM can
treat it like any other image and register it to a low-mag SEM overview with
landmarks:

    <stem>.png   8-bit, black background, a white dot at each position + its label
    <stem>.tif   the same pixels; ImageJ-style calibration (unit=micron, X/Y resolution)
    <stem>.json  the stage <-> pixel transform and every position's pixel coordinates
    <stem>.csv   name, label, stage X/Y (mm), pixel x/y

Geometry (exact, no instrument model involved):
    px = (x_left_mm - stage_x_mm) * 1000 / um_per_px    +stage X -> LEFT
    py = (stage_y_mm - y_top_mm) * 1000 / um_per_px     +stage Y -> DOWN
Stage X increases to the LEFT and stage Y DOWNWARD -- the orientation of the Crossbeam
images and of the report's 800x mosaic:
  * X: Evan, 2026-10-03 -- positions 5-7 (smallest X) sit on the RIGHT of the SEM
    overview; refine's STAGE_SIGN_X = -1 (measured 2026-09-22) agrees.
  * Y (v0.43.0): Evan, 2026-10-04 -- the map should match the 800x mosaic, whose Y
    direction is refine's measured STAGE_SIGN_Y = +1 (stage +Y moves the FIB image UP,
    so a site at larger Y lies further DOWN) and was confirmed by 34 tile overlaps
    lining up to 0.9 um.
STAGE_X_INCREASES_LEFT / STAGE_Y_INCREASES_DOWN are shared with the stage-layout plot. Pixel (0, 0) is the top-left corner of
the top-left pixel; a dot is centred on the pixel holding its exact coordinate. Stage
coordinates at capture: the landmark fit in pyCLEM does the rest (rotation, scale).

Stage Z (v0.45.0, Evan 2026-10-05: "include Z"). At the milling tilt the FIB meets the
grid at a few degrees, so a stage Z change moves the site under the FIB like a stage Y
move does (plus a little X). Positions at a different Z are drawn at the equivalent
stage X/Y at the reference Z (z_ref = median Z):
    map_x_mm = stage_x_mm + Z_TO_STAGE_X * (stage_z_mm - z_ref_mm)
    map_y_mm = stage_y_mm + Z_TO_STAGE_Y * (stage_z_mm - z_ref_mm)
Measured, not derived (session_20261001_170335, tilt 1.97 deg, R 12 deg; positions 5-7
captured 204 um higher than 8-25):
  * 800x FIB tiles, 11 overlaps across the Z step (SNR 8.8-29): Y -1.124 ... -1.139
    (median -1.130), X +0.041 ... +0.054 (median +0.047) stage um per stage-Z um.
    These compare the capture images directly, which is what the map plots.
  * SEM overview normal to the grid, lamella centres: best fit Y -1.104, X +0.049; with
    the FIB values the lamellae of 5-7 sit within 2 um in X and 24-27 um along the grid
    in Y (lamella placement scatters +/-16 um along the grid).
The factors are ratios of two moves seen by the same beam, so they do not depend on how
the grid sits in the holder (the ~1 deg loading slop changes the 4.85x Y stretch on the
overview, not these). It is only applied at the tilt/rotation where it was measured;
elsewhere positions are drawn by X/Y and the JSON says why. Same-Z sessions are unchanged.
"""

from __future__ import annotations

import csv
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = "pyfib.positions_map.v4"     # v4 (v0.45.0): Z model; v3: Y DOWN; v2: X mirrored
MAX_SIDE_PX = 8000
# How the stage-layout plot and the pyCLEM map draw the stage axes (module docstring).
STAGE_X_INCREASES_LEFT = True
STAGE_Y_INCREASES_DOWN = True

# Stage Z -> equivalent stage Y (module docstring). Measured 2026-10-05.
Z_TO_STAGE_Y = -1.13             # stage-Y um per stage-Z um (FIB 800x overlaps; SEM -1.104)
Z_TO_STAGE_X = +0.047            # stage-X um per stage-Z um (FIB 800x overlaps; SEM +0.049)
Z_MODEL_TILT_DEG = 1.97          # where it was measured ...
Z_MODEL_TILT_TOL_DEG = 1.0
Z_MODEL_ROT_DEG = 12.0
Z_MODEL_ROT_TOL_DEG = 1.0
Z_SAME_UM = 1.0                  # within this of the reference Z: nothing to correct


def apply_z_model(stages) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Any]]:
    """stages: (name, x_mm, y_mm, z_mm|None, t_deg|None, r_deg|None).

    Returns ({name: (map_x_mm, map_y_mm)}, info): stage X/Y moved to the reference Z when
    the measured model applies; otherwise stage X/Y unchanged and info["reason"] says
    why. Never raises for missing values."""
    st = [(str(s[0]), (float(s[1]), float(s[2])), s[3], s[4], s[5]) for s in stages]
    out = {n: xy for n, xy, _z, _t, _r in st}
    info: Dict[str, Any] = {"stage_x_per_stage_z": Z_TO_STAGE_X,
                            "stage_y_per_stage_z": Z_TO_STAGE_Y, "applied": False,
                            "z_ref_mm": None, "shifted_um": {}, "unmodelled": [],
                            "model_tilt_deg": Z_MODEL_TILT_DEG,
                            "model_rot_deg": Z_MODEL_ROT_DEG, "reason": ""}
    zs = sorted(float(z) for _n, _y, z, _t, _r in st if z is not None)
    if not zs:
        info["reason"] = "no stage Z recorded"
        return out, info
    z_ref = zs[len(zs) // 2] if len(zs) % 2 else 0.5 * (zs[len(zs) // 2 - 1] + zs[len(zs) // 2])
    info["z_ref_mm"] = round(z_ref, 6)
    off = [n for n, _y, z, _t, _r in st if z is None or abs(float(z) - z_ref) * 1000 > Z_SAME_UM]
    if not off:
        info["reason"] = "all positions at one stage Z"
        return out, info
    why = []
    for n, _y, z, t, r in st:
        if z is None:
            why.append(f"{n}: no stage Z")
        elif t is None:
            why.append(f"{n}: no stage tilt recorded")
        elif abs(float(t) - Z_MODEL_TILT_DEG) > Z_MODEL_TILT_TOL_DEG:
            why.append(f"{n}: tilt {float(t):.2f} deg")
        elif r is None or abs(float(r) - Z_MODEL_ROT_DEG) > Z_MODEL_ROT_TOL_DEG:
            why.append(f"{n}: rotation {r if r is None else round(float(r), 2)} deg")
    if why:
        info["unmodelled"] = off
        info["reason"] = ("stage Z differs but the Z model was only measured at tilt "
                          f"{Z_MODEL_TILT_DEG} +/- {Z_MODEL_TILT_TOL_DEG} deg, R "
                          f"{Z_MODEL_ROT_DEG} +/- {Z_MODEL_ROT_TOL_DEG} deg ("
                          + "; ".join(why[:6]) + ("; ..." if len(why) > 6 else "")
                          + "); drawn by X/Y only")
        return out, info
    for n, (x, y), z, _t, _r in st:
        dx = Z_TO_STAGE_X * (float(z) - z_ref)
        dy = Z_TO_STAGE_Y * (float(z) - z_ref)
        out[n] = (x + dx, y + dy)
        if n in off:
            info["shifted_um"][n] = [round(dx * 1000.0, 1), round(dy * 1000.0, 1)]
    info["applied"] = True
    info["reason"] = "positions at other Z drawn at the equivalent stage X/Y at z_ref"
    return out, info


def short_label(name: str) -> str:
    """'position_12' -> '12'; any other name is kept as it is."""
    m = re.fullmatch(r"position_(\d+)", str(name))
    return m.group(1) if m else str(name)


def _font(size: int):
    from PIL import ImageFont
    for f in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size=size)        # Pillow >= 10.1
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _nice_bar_um(width_um: float) -> float:
    best = 10.0
    for v in (10, 20, 50, 100, 200, 500, 1000, 2000):
        if v <= 0.25 * width_um:
            best = float(v)
    return best


def map_geometry(points: List[Tuple[str, float, float]], *, um_per_px: float = 1.0,
                 margin_um: float = 60.0) -> Dict[str, Any]:
    """Extent and transform for a list of (name, x_mm, y_mm)."""
    xs = [p[1] for p in points]; ys = [p[2] for p in points]
    m_mm = margin_um / 1000.0
    x0, x1 = min(xs) - m_mm, max(xs) + m_mm         # x1 is the LEFT edge (X increases left)
    yb, yt = min(ys) - m_mm, max(ys) + m_mm
    upp = float(um_per_px)
    w = math.ceil((x1 - x0) * 1000.0 / upp) + 1
    h = math.ceil((yt - yb) * 1000.0 / upp) + 1
    if max(w, h) > MAX_SIDE_PX:                     # whole-grid spreads: coarser pixels
        upp = upp * max(w, h) / MAX_SIDE_PX
        w = math.ceil((x1 - x0) * 1000.0 / upp) + 1
        h = math.ceil((yt - yb) * 1000.0 / upp) + 1
    return {"x_left_mm": x1 if STAGE_X_INCREASES_LEFT else x0,
            "y_top_mm": yb if STAGE_Y_INCREASES_DOWN else yt,
            "um_per_px": upp, "width_px": int(w), "height_px": int(h)}


def stage_to_pixel(x_mm: float, y_mm: float, geom: Dict[str, Any]) -> Tuple[float, float]:
    """Stage (mm) -> map pixel: +X to the LEFT, +Y DOWN (module docstring)."""
    sx = -1.0 if STAGE_X_INCREASES_LEFT else 1.0
    sy = 1.0 if STAGE_Y_INCREASES_DOWN else -1.0
    return (sx * (x_mm - geom["x_left_mm"]) * 1000.0 / geom["um_per_px"],
            sy * (y_mm - geom["y_top_mm"]) * 1000.0 / geom["um_per_px"])


def write_positions_map(points: Iterable[tuple], out_stem, *,
                        um_per_px: float = 1.0, margin_um: float = 60.0,
                        meta: Optional[Dict[str, Any]] = None,
                        clock=time.time) -> Dict[str, Path]:
    """Write <stem>.png/.tif/.json/.csv. Returns {kind: path}.

    points: (name, x_mm, y_mm) or (name, x_mm, y_mm, z_mm, t_deg, r_deg); with Z the
    measured Z model is applied (apply_z_model) and recorded in the JSON."""
    from PIL import Image, ImageDraw
    raw = [tuple(p) for p in points]
    if not raw:
        raise ValueError("no positions to map")
    full = [(str(p[0]), float(p[1]), float(p[2]),
             (None if len(p) < 4 or p[3] is None else float(p[3])),
             (p[4] if len(p) > 4 else None), (p[5] if len(p) > 5 else None)) for p in raw]
    map_xy, zinfo = apply_z_model(full)
    zof = {f[0]: f[3] for f in full}
    stage_xy = {f[0]: (f[1], f[2]) for f in full}
    pts = [(f[0], *map_xy[f[0]]) for f in full]
    geom = map_geometry(pts, um_per_px=um_per_px, margin_um=margin_um)
    W, H, upp = geom["width_px"], geom["height_px"], geom["um_per_px"]
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    fsize = max(12, min(28, int(round(H / 40)) if H < 1200 else 24))
    font = _font(fsize)
    r = max(3, int(round(fsize / 4)))
    rows = []
    for name, x, y in pts:
        px, py = stage_to_pixel(x, y, geom)
        cx, cy = int(math.floor(px)), int(math.floor(py))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        d.text((cx + r + 3, cy - r - fsize), short_label(name), fill=255, font=font)
        z = zof[name]
        rows.append({"name": name, "label": short_label(name),
                     "stage_x_mm": round(stage_xy[name][0], 6),
                     "stage_y_mm": round(stage_xy[name][1], 6),
                     "stage_z_mm": (None if z is None else round(z, 6)),
                     "map_x_mm": round(x, 6), "map_y_mm": round(y, 6),
                     "px": round(px, 2), "py": round(py, 2)})
    # scale bar, bottom left (well clear of the margin's edge)
    bar_um = _nice_bar_um(W * upp)
    bar_px = int(round(bar_um / upp))
    by = H - max(10, fsize // 2) - 6
    d.rectangle((10, by, 10 + bar_px, by + 4), fill=255)
    d.text((10, by - fsize - 4), f"{bar_um:g} µm", fill=255, font=font)

    out_stem = Path(out_stem)
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    paths = {k: out_stem.with_suffix(f".{k}") for k in ("png", "tif", "json", "csv")}
    img.save(paths["png"], format="PNG", optimize=True)
    px_per_um = 1.0 / upp
    desc = f"ImageJ=1.53t\nunit=micron\nspacing={upp:g}\n"
    img.save(paths["tif"], format="TIFF",
             tiffinfo={270: desc, 282: px_per_um, 283: px_per_um, 296: 1})
    sidecar = {
        "schema": SCHEMA,
        "created": datetime.fromtimestamp(clock()).astimezone().isoformat(timespec="seconds"),
        "image": {"png": paths["png"].name, "tif": paths["tif"].name,
                  "width_px": W, "height_px": H},
        "um_per_px": upp,
        "transform": {
            "x_left_mm": round(geom["x_left_mm"], 6), "y_top_mm": round(geom["y_top_mm"], 6),
            "px": ("(x_left_mm - map_x_mm) * 1000 / um_per_px" if STAGE_X_INCREASES_LEFT
                   else "(map_x_mm - x_left_mm) * 1000 / um_per_px"),
            "py": ("(map_y_mm - y_top_mm) * 1000 / um_per_px" if STAGE_Y_INCREASES_DOWN
                   else "(y_top_mm - map_y_mm) * 1000 / um_per_px"),
            "map_xy_mm": "stage_x/y_mm + stage_x/y_per_stage_z * (stage_z_mm - z_ref_mm) "
                         "when z_model.applied, else stage_x/y_mm",
            "frame": ("stage coordinates at capture, oriented like the Crossbeam images and "
                      "the 800x mosaic: +X to the " + ("LEFT" if STAGE_X_INCREASES_LEFT else "right")
                      + ", +Y " + ("DOWN" if STAGE_Y_INCREASES_DOWN else "up")),
        },
        "z_model": zinfo,
        "positions": rows,
    }
    if meta:
        sidecar.update(meta)
    paths["json"].write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    with open(paths["csv"], "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "label", "stage_x_mm", "stage_y_mm",
                                           "stage_z_mm", "map_x_mm", "map_y_mm", "px", "py"])
        w.writeheader()
        w.writerows(rows)
    return paths
