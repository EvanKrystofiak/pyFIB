"""Move to a setup position via CLEM SAFE. Dry-run unless --arm."""

from __future__ import annotations

import argparse
import sys

from .loader import load_setup_db, DEFAULT_SETUP_FILE
from .scope import MoveScope
from .steps import move_to_setup_position, CLEM_SAFE_NAME


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.setup_move",
        description="Move the stage to a setup position via CLEM SAFE. Dry-run "
                    "unless --arm.")
    p.add_argument("destination", nargs="?", help="setup position name.")
    p.add_argument("--setup-file", default=None,
                   help=f"setup positions JSON (default: {DEFAULT_SETUP_FILE}).")
    p.add_argument("--list", action="store_true", help="List setup positions and exit.")
    p.add_argument("--simulate", action="store_true",
                   help="Run against a built-in fake stage (no hardware).")
    p.add_argument("--arm", action="store_true",
                   help="Actually move the stage (otherwise dry-run).")
    return p


class _SimStage:
    """Fake SEM whose stage snaps to each commanded move (always idle)."""
    def __init__(self, start):
        self.stage = dict(start)

    def get_float(self, m):
        if m == "DP_STAGE_IS":
            return 0.0
        if m.startswith("AP_STAGE_AT_"):
            return self.stage[m.split("_")[-1].lower()]
        raise KeyError(m)

    def move_stage(self, x, y, z, t, r, m):
        self.stage.update({"x": x, "y": y, "z": z, "t": t, "r": r, "m": m})


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        db = load_setup_db(args.setup_file)
    except Exception as e:
        print(f"ERROR: could not load setup positions: {e}", file=sys.stderr)
        return 2

    names = list(db.positions.keys())
    if args.list or not args.destination:
        print("Setup positions:")
        for n in names:
            st = db.positions[n].stage_at
            print(f"  {n:20s} X={st.x_m*1e3:.3f} Y={st.y_m*1e3:.3f} Z={st.z_m*1e3:.3f} "
                  f"T={st.t_deg} R={st.r_deg} M={st.m_m*1e3:.3f} mm")
        return 0 if args.destination or args.list else 2

    if args.simulate:
        # Start far from CLEM SAFE so the full choreography runs in the demo.
        start = {"x": 0.0, "y": 0.0, "z": 0.010, "t": 0.0, "r": 0.0, "m": 0.0}
        sem = _SimStage(start)
        scope = MoveScope(sem, armed=args.arm, logger=print)
        res = move_to_setup_position(scope, db, args.destination, logger=print)
    else:
        try:
            from ..smartsem_com import SmartSEM
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not import SmartSEM: {e}", file=sys.stderr)
            return 2
        sem = SmartSEM()
        try:
            sem.connect_remoting()
        except Exception:
            sem.connect_local()
        scope = MoveScope(sem, armed=args.arm, logger=print)
        try:
            res = move_to_setup_position(scope, db, args.destination, logger=print)
        finally:
            sem.close()

    if not res.ok:
        print(f"\nMove FAILED: {res.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
