"""Run the SEM working-distance / coincidence measurement.

Offline demo (no hardware):
    python -m smartfib_executor.coincidence --simulate
    python -m smartfib_executor.coincidence --simulate --sim-wd 5.30 --sim-z 30.0

Against the instrument (autofocuses the SEM and reads AP_WD; NEVER moves Z):
    python -m smartfib_executor.coincidence
    python -m smartfib_executor.coincidence --target-wd 5.0

The tool reports a recommended Z move for you to make by hand. It does not move
the stage. After you move Z, re-run to verify the new working distance.
"""

from __future__ import annotations

import argparse
import sys

from .limits import TARGET_WD_M
from .sem import FocusSem, SimulatedFocusSem
from .workflow import run_coincidence


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.coincidence",
        description="Measure SEM working distance and recommend a Z move to reach "
                    "the 5 mm FIB/SEM coincidence point. Never moves the stage.")
    p.add_argument("--simulate", action="store_true",
                   help="Run against a built-in fake SEM (no hardware).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report/recommend only; never move Z (even if <= 0.5 mm).")
    p.add_argument("--image-dir", default=None,
                   help="After reaching the 5 mm coincidence WD (armed adjust only), "
                        "grab an SEM then a FIB overview image into this directory and "
                        "print their paths (SEM_IMAGE:/FIB_IMAGE:).")
    p.add_argument("--stable-idle-s", type=float, default=10.0,
                   help="Require DP_AUTO_FUNCTION to read Idle CONTINUOUSLY for this "
                        "many seconds before accepting an autofocus as complete "
                        "(guards against a premature/flickering Idle). Default 10.")
    p.add_argument("--target-wd", type=float, default=TARGET_WD_M * 1e3,
                   help="Target working distance in mm (default 5.0).")
    p.add_argument("--sim-wd", type=float, default=5.30,
                   help="[simulate] starting focused WD in mm (default 5.30).")
    p.add_argument("--sim-z", type=float, default=30.0,
                   help="[simulate] starting Z in mm (default 30.0).")
    p.add_argument("--sim-eht", type=float, default=2000.0,
                   help="[simulate] AP_ACTUALKV in volts (default 2000; 0 = beam off).")
    p.add_argument("--sim-tilt", type=float, default=0.0,
                   help="[simulate] stage tilt in degrees (default 0).")
    return p


def _stdin_confirm(rec) -> bool:
    try:
        ans = input(f"\nMove stage Z by {rec.delta_z_m * 1e3:+.4f} mm to "
                    f"{rec.z_target_m * 1e3:.4f} mm? Type 'yes' to proceed: ")
    except EOFError:
        return False
    return ans.strip().lower() == "yes"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    target_wd_m = args.target_wd * 1e-3

    if args.simulate:
        sem = SimulatedFocusSem(z_m=args.sim_z * 1e-3, wd_m=args.sim_wd * 1e-3,
                                eht_v=args.sim_eht, tilt_deg=args.sim_tilt)
        guarded = FocusSem(sem)
        res = run_coincidence(guarded, target_wd_m=target_wd_m,
                              allow_move=not args.dry_run, confirm_fn=_stdin_confirm,
                              stable_idle_s=args.stable_idle_s,
                              image_out_dir=args.image_dir, logger=print)
    else:
        try:
            from ..smartsem_com import SmartSEM
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not import SmartSEM (pywin32 missing / not on the "
                  f"SmartSEM PC?): {e}", file=sys.stderr)
            return 2
        raw = SmartSEM()
        try:
            raw.connect_remoting()
        except Exception:
            raw.connect_local()
        guarded = FocusSem(raw)
        try:
            res = run_coincidence(guarded, target_wd_m=target_wd_m,
                                  allow_move=not args.dry_run, confirm_fn=_stdin_confirm,
                                  stable_idle_s=args.stable_idle_s,
                                  image_out_dir=args.image_dir, logger=print)
        finally:
            raw.close()

    if not res.ok:
        print(f"\nRefused: {res.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
