"""Make a TEST copy of a captured position with a known stage offset (file only).

For testing refine's stage correction inside mill-positions: the copy keeps the
original's reference image, magnification and objective voltage, but its recorded
stage X/Y is shifted by a known amount. Recalling the copy therefore lands off target,
and refine must bring the site back (stage move, then beam shift).

    python -m smartfib_executor.refine.offset_position <positions.json> <name> --dx-um 14

creates "<name>_dx14" (or _dy.. for Y) in the same positions file. It touches NO
instrument, writes a backup (positions.json.bak) first, clears any authored pattern on
the copy (so a test pattern is milled, not the real lamella), marks the copy's notes as
a TEST DECOY, and refuses offsets above 30 um per axis.
"""

from __future__ import annotations

import argparse
import dataclasses
import shutil
from pathlib import Path

from ..positions import PositionDatabase

MAX_DECOY_OFFSET_M = 30.0e-6


def make_offset_position(positions_file, name: str, dx_um: float = 0.0,
                         dy_um: float = 0.0, new_name: str = None) -> str:
    pf = Path(positions_file)
    if abs(dx_um) * 1e-6 > MAX_DECOY_OFFSET_M or abs(dy_um) * 1e-6 > MAX_DECOY_OFFSET_M:
        raise ValueError(f"offset above {MAX_DECOY_OFFSET_M * 1e6:.0f} um per axis refused")
    if dx_um == 0.0 and dy_um == 0.0:
        raise ValueError("give a non-zero --dx-um or --dy-um")
    db = PositionDatabase.load(pf)
    if name not in db.positions:
        raise KeyError(f"no position {name!r} in {pf}")
    rec = db.positions[name]
    if new_name is None:
        parts = []
        if dx_um:
            parts.append(f"dx{dx_um:g}")
        if dy_um:
            parts.append(f"dy{dy_um:g}")
        new_name = f"{name}_{'_'.join(parts)}".replace("-", "m").replace(".", "p")
    if new_name in db.positions:
        raise ValueError(f"{new_name!r} already exists")
    st = dataclasses.replace(rec.stage_at, x_m=rec.stage_at.x_m + dx_um * 1e-6,
                             y_m=rec.stage_at.y_m + dy_um * 1e-6)
    note = (f"TEST DECOY: stage offset ({dx_um:+g}, {dy_um:+g}) um from '{name}'; "
            f"reference image is the original's. For refine stage-correction tests.")
    db.positions[new_name] = dataclasses.replace(
        rec, name=new_name, stage_at=st, ely_pattern=None,
        notes=(note + ("\n" + rec.notes if rec.notes else "")))
    shutil.copy2(pf, pf.with_name(pf.name + ".bak"))
    db.save(pf)
    return new_name


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m smartfib_executor.refine.offset_position",
                                description=__doc__.splitlines()[0])
    p.add_argument("positions_file")
    p.add_argument("name", help="the captured position to copy")
    p.add_argument("--dx-um", type=float, default=0.0)
    p.add_argument("--dy-um", type=float, default=0.0)
    p.add_argument("--new-name", default=None)
    a = p.parse_args(argv)
    try:
        n = make_offset_position(a.positions_file, a.name, a.dx_um, a.dy_um, a.new_name)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        return 2
    print(f"Created test position '{n}' (stage offset {a.dx_um:+g}, {a.dy_um:+g} um from "
          f"'{a.name}'). Backup: {Path(a.positions_file).name}.bak")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
