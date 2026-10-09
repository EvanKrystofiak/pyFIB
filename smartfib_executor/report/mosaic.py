"""Mosaic of the positions' 800x images, placed by stage position (report, v0.43.0).

An aid, not a stitched survey: each 800x reference frame is pasted at the place its
stage X/Y puts it in the FIB view, downsampled (default 1/4).

Placement = a 2x2 stage -> image matrix M (pixels per metre of stage motion):
    centre_px(tile) = M @ (stage_xy(tile) - stage_xy(first tile))     (+u right, +v down)
  1. Start from refine's MEASURED stage model (refine/geometry.py, 2026-09-22):
     larger stage X moves a site LEFT in the FIB image, larger stage Y moves it DOWN,
     scales stage_scale(tilt) (X 0.912; Y 0.946*cos(53.9 deg - tilt)).
  2. Where tiles overlap, phase correlation measures how far each pair is off. With at
     least MIN_PAIRS confident pairs (SNR >= MIN_SNR, same Z within 5 um), M is refitted
     from those measurements (least squares), re-checked, and used only if it lines the
     seams up better than the model. The caption says which was used and how well the
     seams agree.

First run (session_20261001_170335, 800x, tilt 1.97 deg, R 12): the model left a
median 4.8 um seam error that grew with distance; the fit (39 pairs) gave scale
X 0.985, Y 0.614 (model 0.912 / 0.583) and a median seam error of 0.97 um.

Tiles whose stage Z differs from the majority are placed by X/Y only and flagged:
a Z change also shifts the FIB view, which this placement does not model.
Reads files only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MIN_PAIRS = 4
MIN_SNR = 8.0
MIN_OVERLAP_FRAC = (0.3, 0.33)     # overlap worth correlating, as a fraction of W, H
Z_TOL_M = 5e-6


@dataclass
class Tile:
    name: str
    path: Path
    x_m: float
    y_m: float
    z_m: float
    t_deg: Optional[float]
    r_deg: Optional[float]
    mag: float
    img: Any = None                # float numpy array (H, W)
    u: float = 0.0                 # centre, full-res mosaic px (+right)
    v: float = 0.0                 # centre, full-res mosaic px (+down)
    stage_xy_m: Optional[Tuple[float, float]] = None   # recorded X/Y (x_m/y_m may be Z-corrected)


@dataclass
class MosaicResult:
    png_path: Optional[Path] = None          # labelled, for use outside the report
    json_path: Optional[Path] = None
    clean_jpeg: Optional[bytes] = None       # unlabelled, for the report (labels are SVG)
    width: int = 0                           # composite size (downsampled px)
    height: int = 0
    scale: float = 0.25
    centres: Dict[str, Tuple[float, float]] = field(default_factory=dict)   # downsampled px
    method: str = ""                         # "fitted" | "model"
    matrix: Optional[List[List[float]]] = None   # px per metre (full-res)
    stage_scale_used: Tuple[float, float] = (0.0, 0.0)
    stage_scale_model: Tuple[float, float] = (0.0, 0.0)
    pairs: int = 0
    seam_median_um: Optional[float] = None
    seam_max_um: Optional[float] = None
    model_seam_median_um: Optional[float] = None
    tile_um: Tuple[float, float] = (0.0, 0.0)
    warnings: List[str] = field(default_factory=list)


def _model_matrix(tilt_deg: float, ps_m: float):
    import numpy as np
    from ..refine import geometry as G
    sx, sy = G.stage_scale(tilt_deg)
    return np.array([[G.STAGE_SIGN_X * sx / ps_m, 0.0],
                     [0.0, G.STAGE_SIGN_Y * sy / ps_m]])


def _pairs(tiles: List[Tile], M) -> List[Tuple[Any, Any, Any, float]]:
    """For every overlapping same-Z pair: (stage delta m, predicted px, measured px
    correction, snr). The correction is how far the second tile's content sits from
    where M put it; true offset = predicted - correction (see the module tests)."""
    import numpy as np
    from ..refine.registration import phase_cross_correlation
    out = []
    for i, a in enumerate(tiles):
        H, W = a.img.shape
        for b in tiles[i + 1:]:
            if b.img.shape != a.img.shape or abs(b.z_m - a.z_m) > Z_TOL_M:
                continue
            ds = np.array([b.x_m - a.x_m, b.y_m - a.y_m])
            du, dv = M @ ds
            ax0, ay0 = int(round(max(0.0, du))), int(round(max(0.0, dv)))
            ax1, ay1 = int(round(min(W, W + du))), int(round(min(H, H + dv)))
            if ax1 - ax0 < MIN_OVERLAP_FRAC[0] * W or ay1 - ay0 < MIN_OVERLAP_FRAC[1] * H:
                continue
            bx0, by0 = ax0 - int(round(du)), ay0 - int(round(dv))
            A = a.img[ay0:ay1, ax0:ax1]
            B = b.img[by0:by0 + (ay1 - ay0), bx0:bx0 + (ax1 - ax0)]
            if A.shape != B.shape:
                continue
            try:
                r = phase_cross_correlation(A, B, crop_bar=False)
            except Exception:  # noqa: BLE001
                continue
            out.append((ds, np.array([du, dv]), np.array([r.dx_px, r.dy_px]), float(r.snr)))
    return out


def _median_um(pairs, ps_m) -> Optional[float]:
    import numpy as np
    good = [p for p in pairs if p[3] >= MIN_SNR]
    if not good:
        return None
    return float(np.median([math.hypot(*p[2]) * ps_m * 1e6 for p in good]))


def build_mosaic(items: List[Tuple[str, Any, Path]], out_stem, *, scale: float = 0.25,
                 max_side_px: int = 4000, fit: bool = True,
                 label_of=None,
                 xy_eff_m: Optional[Dict[str, Tuple[float, float]]] = None) -> MosaicResult:
    """items: (name, PositionRecord, path to its 800x BMP). Never raises; problems are
    returned as warnings (and no mosaic if nothing could be placed).

    xy_eff_m (v0.45.0): {name: (x, y) stage metres moved to a common Z} from
    map_export.apply_z_model, used for PLACEMENT so tiles at another Z land where their
    content is. The scale fit still uses same-Z pairs only (their differences are the
    same either way)."""
    xy_eff_m = xy_eff_m or {}
    res = MosaicResult(scale=scale)
    try:
        import numpy as np
        from PIL import Image, ImageDraw
        from .. import fov
        from ..refine import geometry as G
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"mosaic needs numpy and Pillow ({e})")
        return res
    label_of = label_of or (lambda n: n)

    tiles: List[Tile] = []
    for name, rec, path in items:
        st = rec.stage_at
        mag = float(getattr(rec, "coarse_magnification_x", None) or 800.0)
        try:
            with Image.open(path) as im:
                arr = np.asarray(im.convert("L"), dtype=np.float64)
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"{name}: 800x image not readable ({e})")
            continue
        ex, ey = xy_eff_m.get(name, (st.x_m, st.y_m))
        tiles.append(Tile(name=name, path=Path(path), x_m=float(ex), y_m=float(ey),
                          stage_xy_m=(float(st.x_m), float(st.y_m)),
                          z_m=float(st.z_m), t_deg=st.t_deg, r_deg=st.r_deg, mag=mag, img=arr))
    if not tiles:
        res.warnings.append("no 800x images to place")
        return res
    mags = {round(t.mag, 3) for t in tiles}
    shapes = {t.img.shape for t in tiles}
    if len(mags) > 1 or len(shapes) > 1:
        keep_mag, keep_shape = tiles[0].mag, tiles[0].img.shape
        dropped = [t.name for t in tiles if round(t.mag, 3) != round(keep_mag, 3)
                   or t.img.shape != keep_shape]
        tiles = [t for t in tiles if t.name not in dropped]
        res.warnings.append(f"left out (different magnification or image size): "
                            f"{', '.join(dropped)}")
    H, W = tiles[0].img.shape
    ps = fov.fov_width_m(tiles[0].mag) / W
    res.tile_um = (W * ps * 1e6, H * ps * 1e6)

    tilts = [t.t_deg for t in tiles if t.t_deg is not None]
    rots = [t.r_deg for t in tiles if t.r_deg is not None]
    tilt = float(np.median(tilts)) if tilts else 0.0
    rot = float(np.median(rots)) if rots else G.STAGE_CALIBRATION_ROT_DEG
    if not tilts:
        res.warnings.append("no stage tilt recorded; the model assumes 0 deg")
    elif max(tilts) - min(tilts) > 0.5:
        res.warnings.append(f"positions span tilts {min(tilts):.1f}-{max(tilts):.1f} deg; "
                            f"one scale (at {tilt:.1f} deg) is used for all")
    ok, why = G.stage_model_applies(tilt, rot)
    if not ok:
        res.warnings.append(f"refine stage model not calibrated here ({why}); placement "
                            f"relies on the overlap fit, else is approximate")
    zs = sorted(t.z_m for t in tiles)
    z_major = zs[len(zs) // 2]
    off_z = [t for t in tiles if abs(t.z_m - z_major) > Z_TOL_M and t.name not in xy_eff_m]
    if off_z:
        res.warnings.append("placed by X/Y only, at a different stage Z than the others "
                            "(a Z change also shifts the FIB view): "
                            + ", ".join(f"{t.name} ({(t.z_m - z_major) * 1e6:+.0f} um)"
                                        for t in off_z))

    M0 = _model_matrix(tilt, ps)
    res.stage_scale_model = tuple(round(v, 3) for v in G.stage_scale(tilt))
    M, method = M0, "model"
    pairs0 = _pairs(tiles, M0)
    res.model_seam_median_um = _median_um(pairs0, ps)
    good = [p for p in pairs0 if p[3] >= MIN_SNR]
    final_pairs = pairs0
    if fit and len(good) >= MIN_PAIRS:
        X = np.array([p[0] for p in good])
        Y = np.array([p[1] - p[2] for p in good])           # true offset = predicted - correction
        Mt, *_ = np.linalg.lstsq(X, Y, rcond=None)
        M1 = Mt.T
        pairs1 = _pairs(tiles, M1)
        med1 = _median_um(pairs1, ps)
        if med1 is not None and (res.model_seam_median_um is None
                                 or med1 < res.model_seam_median_um):
            M, method, final_pairs = M1, "fitted", pairs1
    elif fit:
        res.warnings.append(f"only {len(good)} confident overlaps (need {MIN_PAIRS}); "
                            f"placed with refine's stage model")
    res.method = method
    res.matrix = [[float(M[0, 0]), float(M[0, 1])], [float(M[1, 0]), float(M[1, 1])]]
    res.stage_scale_used = (round(abs(M[0, 0]) * ps, 3), round(abs(M[1, 1]) * ps, 3))
    good_final = [p for p in final_pairs if p[3] >= MIN_SNR]
    res.pairs = len(good_final)
    if good_final:
        errs = [math.hypot(*p[2]) * ps * 1e6 for p in good_final]
        res.seam_median_um = float(np.median(errs))
        res.seam_max_um = float(max(errs))

    # --- place and compose (downsampled)
    s0 = np.array([tiles[0].x_m, tiles[0].y_m])
    for t in tiles:
        t.u, t.v = (M @ (np.array([t.x_m, t.y_m]) - s0)).tolist()
    left = min(t.u for t in tiles) - W / 2
    top = min(t.v for t in tiles) - H / 2
    full_w = max(t.u for t in tiles) + W / 2 - left
    full_h = max(t.v for t in tiles) + H / 2 - top
    k = min(float(scale), max_side_px / max(full_w, full_h))
    res.scale = k
    cw, ch = int(math.ceil(full_w * k)) + 1, int(math.ceil(full_h * k)) + 1
    canvas = Image.new("L", (cw, ch), 18)
    tw, th = max(1, int(round(W * k))), max(1, int(round(H * k)))
    for t in tiles:
        tile = Image.fromarray(np.clip(t.img, 0, 255).astype("uint8")).resize((tw, th), Image.LANCZOS)
        x0 = int(round((t.u - W / 2 - left) * k))
        y0 = int(round((t.v - H / 2 - top) * k))
        canvas.paste(tile, (x0, y0))
        res.centres[t.name] = ((t.u - left) * k, (t.v - top) * k)
    res.width, res.height = cw, ch

    import io
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=82, optimize=True)
    res.clean_jpeg = buf.getvalue()

    # labelled copy for use outside the report
    try:
        from .map_export import _font
        lab = canvas.convert("RGB")
        d = ImageDraw.Draw(lab)
        fsize = max(12, int(round(th / 9)))
        font = _font(fsize)
        r = max(3, fsize // 4)
        for name, (cx, cy) in res.centres.items():
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255), outline=(0, 0, 0))
            try:
                d.text((cx + r + 3, cy - r - fsize), label_of(name), fill=(255, 255, 255),
                       font=font, stroke_width=2, stroke_fill=(0, 0, 0))
            except TypeError:                                   # very old Pillow
                d.text((cx + r + 3, cy - r - fsize), label_of(name), fill=(255, 255, 255),
                       font=font)
        out_stem = Path(out_stem)
        res.png_path = out_stem.with_suffix(".png")
        lab.save(res.png_path, format="PNG", optimize=True)
        res.json_path = out_stem.with_suffix(".json")
        res.json_path.write_text(json.dumps({
            "schema": "pyfib.mosaic_800x.v1",
            "image": res.png_path.name, "width_px": cw, "height_px": ch,
            "downsample": k, "um_per_px": ps * 1e6 / k,
            "frame": "FIB view: larger stage X to the LEFT, larger stage Y DOWN",
            "method": method, "matrix_px_per_m_fullres": res.matrix,
            "stage_scale_used": res.stage_scale_used, "stage_scale_model": res.stage_scale_model,
            "tilt_deg": tilt, "rotation_deg": rot,
            "overlap_pairs": res.pairs, "seam_median_um": res.seam_median_um,
            "seam_max_um": res.seam_max_um, "model_seam_median_um": res.model_seam_median_um,
            "tiles": [{"name": t.name, "label": label_of(t.name), "image": t.path.name,
                       "centre_px": [round(res.centres[t.name][0], 1),
                                     round(res.centres[t.name][1], 1)],
                       "stage_x_mm": round((t.stage_xy_m or (t.x_m, t.y_m))[0] * 1e3, 6),
                       "stage_y_mm": round((t.stage_xy_m or (t.x_m, t.y_m))[1] * 1e3, 6),
                       "placed_x_mm": round(t.x_m * 1e3, 6), "placed_y_mm": round(t.y_m * 1e3, 6),
                       "stage_z_mm": round(t.z_m * 1e3, 6)} for t in tiles],
            "warnings": res.warnings,
        }, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        res.warnings.append(f"labelled mosaic file not written: {e}")
    return res
