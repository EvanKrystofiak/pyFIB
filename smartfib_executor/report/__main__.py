"""python -m smartfib_executor.report <positions.json | session folder> [options]

Writes one self-contained HTML report into the session folder. Reads files only;
never connects to the microscope.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m smartfib_executor.report",
        description="Make an HTML report of a pyFIB session: every position's image with "
                    "its pattern drawn on it, coordinates, and what the run logs say was "
                    "milled. Reads files only; never connects to the microscope.")
    ap.add_argument("positions", help="the session's positions.json (or its folder)")
    ap.add_argument("--pattern", metavar="ELY",
                    help="standard .ely to draw on positions that have no milling record "
                         "and no authored pattern (default: pyFIB's default.ely in "
                         "assets/patterns)")
    ap.add_argument("--out", metavar="HTML",
                    help="where to write the report (default: <session>/report_<time>.html)")
    ap.add_argument("--no-coarse", action="store_true", help="leave out the 800x images")
    ap.add_argument("--quality", type=int, default=80,
                    help="JPEG quality of the embedded images, 50-95 (default 80)")
    ap.add_argument("--title", help="report title (default: Session report: <folder>)")
    ap.add_argument("--map-um-per-px", type=float, default=1.0,
                    help="scale of the positions map for pyCLEM (default 1 um per pixel)")
    ap.add_argument("--no-map", action="store_true",
                    help="do not write the positions map files for pyCLEM")
    ap.add_argument("--mosaic", action="store_true",
                    help="add the 800x mosaic (off by default since v0.43.2)")
    ap.add_argument("--mosaic-scale", type=float, default=0.25,
                    help="with --mosaic: downsampling (default 0.25 = quarter size)")
    a = ap.parse_args(argv)

    pf = Path(a.positions)
    if pf.is_dir():
        pf = pf / "positions.json"
    if not pf.exists():
        print(f"positions file not found: {pf}")
        return 2
    if a.pattern and not Path(a.pattern).exists():
        print(f"pattern not found: {a.pattern}")
        return 2
    if not 50 <= a.quality <= 95:
        print("--quality must be between 50 and 95")
        return 2
    if not 0.05 <= a.mosaic_scale <= 1.0:
        print("--mosaic-scale must be between 0.05 and 1")
        return 2
    if not 0.05 <= a.map_um_per_px <= 100:
        print("--map-um-per-px must be between 0.05 and 100")
        return 2
    from .html_report import build_report
    try:
        res = build_report(pf, standard_pattern=a.pattern, out_path=a.out,
                           include_coarse=not a.no_coarse, jpeg_quality=a.quality,
                           title=a.title, export_map=not a.no_map,
                           map_um_per_px=a.map_um_per_px, mosaic=a.mosaic,
                           mosaic_scale=a.mosaic_scale)
    except Exception as e:  # noqa: BLE001
        print(f"Report failed: {type(e).__name__}: {e}")
        return 1
    for w in res.warnings:
        print(f"WARNING: {w}")
    size_mb = res.path.stat().st_size / 1e6
    print(f"Report: {res.path} ({res.positions} positions, {res.milled} milled, of which "
          f"{res.assumed} assumed (no run log); {size_mb:.1f} MB)")
    if res.map_paths:
        print(f"Positions map for pyCLEM: {res.map_paths['png'].name} / .tif / .json / .csv")
    m = getattr(res, "mosaic", None)
    if m is not None and m.png_path is not None:
        how = (f"fitted from {m.pairs} overlaps, seams {m.seam_median_um:.2f} um median"
               if m.method == "fitted" else "refine stage model")
        print(f"800x mosaic: {m.png_path.name} ({len(m.centres)} tiles; {how})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
