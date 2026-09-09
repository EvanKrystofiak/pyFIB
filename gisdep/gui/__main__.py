"""Launch the gisdep GUI:

    python -m gisdep.gui                  mock, full interactive (no instrument)
    python -m gisdep.gui --monitor        read-only monitor UI on the mock (preview)
    python -m gisdep.gui --instrument     read-only live instrument monitor (COM)

Requires PyQt5 + qtpy.
"""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="gisdep GUI")
    parser.add_argument("--instrument", action="store_true",
                        help="connect read-only to the microscope (live monitor)")
    parser.add_argument("--monitor", action="store_true",
                        help="read-only monitor UI on the mock (offline preview)")
    parser.add_argument("--machine", default="", help="machine name for the COM backend")
    args = parser.parse_args(argv)

    try:
        from gisdep.gui.app import run
    except ImportError as exc:  # Qt not installed
        sys.stderr.write(
            "The gisdep GUI needs PyQt5 and qtpy:\n    pip install PyQt5 qtpy\n"
            f"(import error: {exc})\n"
        )
        return 1

    if args.instrument:
        from gisdep.api.com import ComSmartSEM
        api = ComSmartSEM(machine=args.machine)  # read-only monitor (forced)
        return run(api=api, monitor=args.monitor)
    return run(api=None, monitor=args.monitor)


if __name__ == "__main__":
    sys.exit(main())
