"""Run the read-only mnemonic probe.

Real instrument (Windows + SmartSEM):
    python -m smartfib_executor.probe --save probe_results.json

Offline demo (no hardware):
    python -m smartfib_executor.probe --simulate

This tool only ever READS. It cannot change the instrument state.
"""

from __future__ import annotations

import argparse
import sys

from .probe import run_probe, format_report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.probe",
        description="Read-only probe of SmartSEM mnemonics available on this machine.",
    )
    p.add_argument("--simulate", action="store_true",
                   help="Run against a built-in fake instrument (no hardware).")
    p.add_argument("--save", metavar="PATH",
                   help="Write the full results to a JSON file.")
    p.add_argument("--verbose", action="store_true",
                   help="Log every probe as it happens.")
    return p


def _simulated_sem():
    """A fake SmartSEM for demonstrating the report offline."""
    from .catalog import CAPABILITIES

    class SmartSEMError(RuntimeError):
        def __init__(self, msg, code=None):
            super().__init__(msg)
            self.code = code

    # Pretend this machine exposes AP_PIXEL_SIZE but not the FIB-specific names.
    present = {
        "AP_PIXEL_SIZE": ("= 55.82 nm", 5.582e-8),
        # field of view directly (matches the 57.2 um measured at 2000x)
        "AP_WIDTH": ("= 57.15 um", 5.715e-5),
        "AP_HEIGHT": ("= 42.86 um", 4.286e-5),
        # scan rotation -- zero here, which is what we currently assume
        "AP_FIB_SCAN_ROTATION": ("= 0.00 deg", 0.0),
        "AP_SCANROTATION": ("= 0.00 deg", 0.0),
        "DP_SCAN_ROT": ("Off", 0.0),
        # probe current as a continuous value
        "AP_IPROBE": ("= 50.0 pA", 5.0e-11),
        # SEM column state
        "AP_WD": ("= 4.98 mm", 4.98e-3),
        # dwell / scan speed (simulate a column that exposes the enum + line/frame time)
        "DP_SCANRATE": ("7", 7.0),
        "DP_FIB_SCANRATE": ("7", 7.0),
        "AP_DWELL_TIME": ("= 3.20 us", 3.2e-6),
        "AP_LINE_TIME": ("= 3.28 ms", 3.28e-3),
        "AP_FRAME_TIME": ("= 2.52 s", 2.52),
        "DP_NOISE_REDUCTION": ("Frame Avg", 2.0),
        "DP_FRAME_INT": ("4", 4.0),
        "AP_ACTUALKV": ("= 2.00 kV", 2000.0),
        "AP_MANUALKV": ("= 2.00 kV", 2000.0),
        "DP_IMAGE_STORE": ("1024 * 768", 0.0),
        "DP_FIB_IMAGE_PROBE": ("50 pA", 6.0),
        "DP_FIB_PROBE": ("50 pA", 6.0),
        "DP_FIB_MILL_PROBE": ("1 nA", 10.0),
        "DP_AUTO_FUNCTION": ("Idle", 0.0),
        "DP_AUTO_VIDEO": ("Off", 0.0),
        "DP_DETECTOR_CHANNEL": ("0", 0.0),
        "DP_DETECTOR_TYPE": ("SE2", 2.0),
        "AP_FIB_EHT_TARGET": ("= 30.00 kV", 30000.0),
        "AP_FIB_EHT_ACTUAL": ("= 30.00 kV", 30000.0),
        "AP_BEAMSHIFT_X": ("= 0.0 %", 0.0),
        "AP_BEAMSHIFT_Y": ("= 0.0 %", 0.0),
        "AP_FIB_EMISSION_CURRENT": ("= 2.10 uA", 2.1e-6),
        "AP_FIB_MAGNIFICATION": ("2.00 K X", 2000.0),
        "AP_FIB_BEAM_SHIFT_X": ("= 0.0 nm", 0.0),
        "AP_FIB_BEAM_SHIFT_Y": ("= 0.0 nm", 0.0),
        "DP_PATTERNING_MODE": ("Off", 0.0),
        "DP_FIB_IMAGING": ("FIB", 1.0),
        "DP_FIB_MODE": ("Imaging", 0.0),
        "DP_FROZEN": ("Frozen", 1.0),
        "SV_SERIAL_NUMBER": ("1550XB-00-00 (SIMULATED)", None),
        "SV_VERSION": ("V05.02 (SIMULATED)", None),
    }
    unavailable = {"AP_FIB_PROBE_CURRENT"}

    class FakeSem:
        def get_str(self, m):
            if m in present:
                return present[m][0]
            if m in unavailable:
                raise SmartSEMError("digital read failed", code=1002)
            raise SmartSEMError("failed to translate parameter into an id", code=1000)

        def get_float(self, m):
            if m in present:
                v = present[m][1]
                if v is None:
                    raise SmartSEMError("not analogue nor digital", code=1003)
                return v
            if m in unavailable:
                raise SmartSEMError("analogue read failed", code=1001)
            raise SmartSEMError("failed to translate parameter into an id", code=1000)

    return FakeSem()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    log = print if args.verbose else None

    if args.simulate:
        sem = _simulated_sem()
        report = run_probe(sem, logger=log)
    else:
        try:
            from ..smartsem_com import SmartSEM
        except Exception as e:  # pragma: no cover
            print(f"ERROR: could not import SmartSEM (pywin32 missing, or not on the "
                  f"SmartSEM PC?): {e}", file=sys.stderr)
            return 2
        sem = SmartSEM()
        try:
            sem.connect_remoting()
        except Exception:
            sem.connect_local()
        try:
            report = run_probe(sem, logger=log)
        finally:
            sem.close()

    print()
    print(format_report(report))
    if args.save:
        report.save(args.save)
        print(f"\nSaved full results -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
