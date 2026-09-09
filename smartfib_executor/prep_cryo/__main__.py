"""Run Prep CryoFIBSEM. Dry-run unless --arm; --simulate for offline demo."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .steps import (run_prep_cryo, CLEM_SAFE_NAME, CRYO_INSERTION_NAME)
from .scope import SimulatedPrepSem


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.prep_cryo",
        description="Prep CryoFIBSEM: ready the FIB for sample insertion. Dry-run "
                    "unless --arm.")
    p.add_argument("--positions-file",
                   help="positions JSON with the two waypoints. Default: the shipped "
                        "setup_positions.json (CLEM SAFE + Cryo Insertion fixtures).")
    p.add_argument("--simulate", action="store_true",
                   help="Run against a built-in fake SEM (no hardware).")
    p.add_argument("--arm", action="store_true",
                   help="Actually execute on the instrument (otherwise dry-run).")
    return p


def _sim_db():
    from ..positions import PositionDatabase, PositionRecord, StageAt
    db = PositionDatabase()

    def rec(name, x, y, z, m):
        return PositionRecord(name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
                              fib_objective_potential_v=16400.0,
                              stage_at=StageAt(x, y, z, 0.0, m, 0.0))
    db.positions[CLEM_SAFE_NAME] = rec(CLEM_SAFE_NAME, 1e-3, 2e-3, 0.030, 0.0)
    db.positions[CRYO_INSERTION_NAME] = rec(CRYO_INSERTION_NAME, 5e-3, 6e-3, 0.025, 2e-3)
    return db


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.simulate:
        db = _sim_db()
        sem = SimulatedPrepSem()
        res = run_prep_cryo(sem, db, armed=args.arm, logger=print)
    else:
        from ..positions import PositionDatabase
        if args.positions_file:
            pos_path = Path(args.positions_file)
        else:
            # Default to the shipped setup fixtures (CLEM SAFE + Cryo Insertion).
            pos_path = (Path(__file__).resolve().parent.parent / "assets"
                        / "setup_positions.json")
        try:
            db = PositionDatabase.load(pos_path)
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not load positions ({pos_path}): {e}", file=sys.stderr)
            return 2
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
        try:
            res = run_prep_cryo(sem, db, armed=args.arm, logger=print)
        finally:
            sem.close()

    if not res.ok:
        print(f"\nPrep CryoFIBSEM FAILED: {res.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
