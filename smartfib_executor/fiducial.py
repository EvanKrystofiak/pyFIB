"""Fiducial patterns: where they live and how a side maps to a file.

A fiducial is a small reference mark milled to the LEFT or RIGHT of the FIB image at
a position, so drift correction can re-find the site later. Each side has its own
.ely, authored on the FOV-centred convention (+X right), so the file ITSELF encodes
the left/right offset -- pyFIB does not compute offsets, it just picks the file.

Drop the operator-supplied files here:
    smartfib_executor/assets/fiducial_left.ely
    smartfib_executor/assets/fiducial_right.ely
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

SIDES = ("left", "right")
ASSETS = Path(__file__).resolve().parent / "assets"


def fiducial_ely_path(side: str) -> Path:
    side = str(side).strip().lower()
    if side not in SIDES:
        raise ValueError(f"fiducial side must be one of {SIDES}, got {side!r}")
    return ASSETS / f"fiducial_{side}.ely"


def fiducial_available(side: str) -> bool:
    try:
        return fiducial_ely_path(side).exists()
    except ValueError:
        return False


def missing_fiducials() -> list:
    return [s for s in SIDES if not fiducial_available(s)]


# ----------------------------- FOV fit check ----------------------------- #
def fiducial_extent_um(side: str):
    """(min_x, max_x, min_y, max_y) in um of the side's PROTOTYPE fiducial shapes."""
    return ely_extent_um(fiducial_ely_path(side))


def ely_extent_um(path):
    """(min_x, max_x, min_y, max_y) in um of all shapes in an .ely (rects and polygons),
    via the vendored elymaker reader. Returns None if unreadable."""
    try:
        from elymaker.reader import read_ely
        lay = read_ely(str(path))
    except Exception:
        return None
    xs, ys = [], []
    for st in getattr(lay, "structures", []):
        for lr in getattr(st, "layer_refs", []):
            for s in getattr(lr, "shapes", []):
                pts = getattr(s, "points", None)
                if pts:
                    xs += [float(a) for a, _ in pts]; ys += [float(b) for _, b in pts]
                    continue
                x, y = float(getattr(s, "x", 0.0)), float(getattr(s, "y", 0.0))
                w, h = float(getattr(s, "width", 0.0)), float(getattr(s, "height", 0.0))
                xs += [x, x + w]; ys += [y, y + h]
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def fiducial_fit(side: str, magnification: float, path=None):
    """Does the fiducial fit inside the FIB field of view at this magnification?

    Returns (fits: bool, margin_um: float, detail: str). margin is the distance from
    the fiducial's outermost X edge to the FOV edge (negative = outside). The FOV is
    the imaging/patterning frame at the position's mag, so a fiducial beyond it would
    be clipped or not milled at all.
    """
    from .fov import fov_width_m
    ext = ely_extent_um(path) if path is not None else fiducial_extent_um(side)
    if ext is None:
        return True, float("nan"), "fiducial extent unreadable; fit not checked"
    half_w = fov_width_m(float(magnification)) * 1e6 / 2.0
    outer_x = max(abs(ext[0]), abs(ext[1]))
    margin = half_w - outer_x
    detail = (f"fiducial |x| out to {outer_x:.1f} um; FOV half-width {half_w:.1f} um "
              f"at {float(magnification):.0f}x -> margin {margin:+.1f} um")
    return margin >= 0.0, margin, detail



# ------------------------- NUMBERED fiducials (1-30) ------------------------- #
# Loop-free seven-segment polygons from elynumbers v0.3.0 (assets/fiducial_numbers).
# Each number is written THROUGH ELYMAKER (LayoutBuilder.polygon + write_ely) -- never a
# hand-rolled .ely -- at the centre of the side's prototype square, with the prototype's
# exposure, so a number mills where and how the square did.
import json as _json

NUMBERS_DIR = ASSETS / "fiducial_numbers"
NUMBER_MIN, NUMBER_MAX = 1, 30
_DEFAULT_CENTRE_X_UM = 21.0          # used only if a side's prototype is missing
_DEFAULT_TEMPLATE = dict(probe="30kV:300pA", recipe=r"D:\FIB Recipes\Vitriuos_Ice.epm",
                         depth_um=5.0, frame_um=76.2993)


def number_polygons(n: int):
    """Polygons (list of [(x, y), ...] in um, centred on the number) for number n."""
    n = int(n)
    if not (NUMBER_MIN <= n <= NUMBER_MAX):
        raise ValueError(f"fiducial number must be {NUMBER_MIN}-{NUMBER_MAX}, got {n}")
    d = _json.loads((NUMBERS_DIR / "polygons_um.json").read_text(encoding="utf-8"))
    return [[(float(x), float(y)) for x, y in poly] for poly in d["numbers"][str(n)]]


def side_centre_um(side: str):
    """Centre (x, y) um of the side's prototype square -- where a number is placed."""
    ext = fiducial_extent_um(side) if fiducial_available(side) else None
    if ext is None:
        return ((-1.0 if str(side).lower() == "left" else 1.0) * _DEFAULT_CENTRE_X_UM, 0.0)
    return ((ext[0] + ext[1]) / 2.0, (ext[2] + ext[3]) / 2.0)


def exposure_template(side: str) -> dict:
    """Probe / recipe / depth / frame copied from the side's prototype fiducial (the
    operator's own known-good settings); defaults if the prototype is unavailable."""
    t = dict(_DEFAULT_TEMPLATE)
    try:
        from elymaker.reader import read_ely
        lay = read_ely(str(fiducial_ely_path(side)))
        lr = lay.structures[0].layer_refs[0]
        sh = lr.shapes[0]
        t["frame_um"] = float(lr.frame_size)
        t["depth_um"] = float(sh.depth_um)
        if sh.exposure is not None:
            t["recipe"] = sh.exposure.file
            if sh.exposure.probe is not None:
                t["probe"] = sh.exposure.probe          # the prototype's exact probe
    except Exception:
        pass
    return t


def write_number_fiducial(n: int, side: str, out_path) -> Path:
    """Write number n as a polygon .ely, offset to `side`, through elymaker."""
    from elymaker.builder import LayoutBuilder
    from elymaker.writer import write_ely
    side = str(side).strip().lower()
    if side not in SIDES:
        raise ValueError(f"fiducial side must be one of {SIDES}, got {side!r}")
    polys = number_polygons(n)
    cx, cy = side_centre_um(side)
    t = exposure_template(side)
    b = LayoutBuilder(name=f"fiducial_{int(n):02d}_{side}", recipe=t["recipe"],
                      fov_um=t["frame_um"])
    for poly in polys:
        b.polygon([(x + cx, y + cy) for x, y in poly], depth_um=t["depth_um"],
                  probe=t["probe"])
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_ely(b.build(), str(out))
    return out


def number_fiducial_path(session_dir, n: int, side: str) -> Path:
    """<session>/fiducials/num_NN_<side>.ely, (re)written from the current template."""
    out = Path(session_dir) / "fiducials" / f"num_{int(n):02d}_{str(side).lower()}.ely"
    return write_number_fiducial(n, side, out)


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m smartfib_executor.fiducial",
                                 description="Write numbered fiducial .ely files (via elymaker).")
    ap.add_argument("--write-all", metavar="DIR",
                    help="write num_01..30 for left and right into DIR for inspection")
    ap.add_argument("--number", type=int)
    ap.add_argument("--side", choices=SIDES)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    if a.write_all:
        for side in SIDES:
            for n in range(NUMBER_MIN, NUMBER_MAX + 1):
                write_number_fiducial(n, side, Path(a.write_all) / f"num_{n:02d}_{side}.ely")
        print(f"wrote {2 * (NUMBER_MAX - NUMBER_MIN + 1)} files to {a.write_all}")
        return 0
    if a.number is None or not a.side or not a.out:
        ap.error("give --write-all DIR, or --number N --side left|right --out FILE")
    print(write_number_fiducial(a.number, a.side, a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
