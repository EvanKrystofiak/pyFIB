"""Run gisdep's GIS workflow from inside pyFIB, on pyFIB's SmartSEM connection.

Phase 2 of the gisdep integration: drive the VENDORED gisdep deposition code through
the ``bridged_api`` adapter, so it uses pyFIB's single COM connection and its progress
streams to the same log the GUI shows.

Modes:
    probe     -- read-only GIS state sweep (valve/needle/tilt/stage). Always safe.
    deposit   -- one closed-loop deposition on a port for a dwell time.
    full-run  -- cryo position (via CLEM SAFE) -> autofocus + set WD -> deposition.

DRY-RUN unless --arm. Unarmed always runs the read-only probe and prints the intended
action; --arm executes the real gisdep routine. gisdep's own safety (stage-idle
precondition, tilt gate, per-step confirms) still applies underneath.

    python -m smartfib_executor.gis_run probe
    python -m smartfib_executor.gis_run deposit --port 1 --dwell 20
    python -m smartfib_executor.gis_run deposit --port 1 --dwell 20 --arm
    python -m smartfib_executor.gis_run full-run --grid "Grid1 GIS Normal" --port 1 --dwell 20 --arm
"""

from __future__ import annotations

import argparse
import logging
import sys


def _route_gisdep_logs_to_stdout() -> None:
    """Stream gisdep's INFO logs to stdout so the GUI subprocess shows progress."""
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("gisdep")
    root.addHandler(h)
    root.setLevel(logging.INFO)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.gis_run",
        description="Run gisdep's GIS workflow on pyFIB's connection. Dry-run "
                    "(read-only probe) unless --arm.")
    p.add_argument("mode", choices=("probe", "deposit", "full-run"))
    p.add_argument("--port", type=int, default=1, help="GIS port (1 or 2).")
    p.add_argument("--dwell", type=float, default=20.0, help="Valve-open dwell (s).")
    p.add_argument("--grid", default="", help="Grid name for full-run.")
    p.add_argument("--target-wd", type=float, default=5.5,
                   help="Target working distance in mm for full-run (default 5.5).")
    p.add_argument("--connect", choices=("remoting", "local"), default="remoting")
    p.add_argument("--machine", default="")
    p.add_argument("--arm", action="store_true",
                   help="Actually execute on the instrument (otherwise read-only).")
    return p


def _print_probe(api) -> None:
    from gisdep.gis.probe import GISProbe
    report = GISProbe().probe(api)
    print("GIS probe report:")
    print(report)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _route_gisdep_logs_to_stdout()

    try:
        from .smartsem_com import SmartSEM
        from .gisdep_adapter import bridged_api
    except Exception as e:  # pragma: no cover
        print(f"ERROR: could not import pyFIB SEM / adapter: {e}", file=sys.stderr)
        return 2

    raw = SmartSEM()
    api = bridged_api(raw, connect=args.connect, machine=args.machine)
    try:
        api.initialise()
    except Exception as e:
        print(f"ERROR: could not connect: {e}", file=sys.stderr)
        return 2

    try:
        # Read-only probe first -- always safe, and the whole action when unarmed.
        try:
            _print_probe(api)
        except Exception as e:  # noqa: BLE001
            print(f"(probe unavailable: {e})")

        if args.mode == "probe":
            return 0

        if not args.arm:
            print(f"\n[dry-run] would run {args.mode} "
                  f"(port={args.port}, dwell={args.dwell}s"
                  + (f", grid={args.grid!r}, WD={args.target_wd}mm"
                     if args.mode == "full-run" else "")
                  + "). Re-run with --arm to execute.")
            return 0

        if args.mode == "deposit":
            from gisdep.gis.deposition import deposit
            res = deposit(api, args.port, args.dwell)
            print(f"\nDeposition result: {res}")
            return 0

        if args.mode == "full-run":
            if not args.grid:
                print("ERROR: full-run needs --grid.", file=sys.stderr)
                return 2
            from gisdep.orchestrate.full_run import run_full_workflow
            res = run_full_workflow(api, args.grid, args.port, args.dwell,
                                    target_wd_m=args.target_wd * 1e-3)
            print(f"\nFull-run result: {res}")
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"\nGIS {args.mode} FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            api.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
