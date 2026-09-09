"""GUI entry point.

Run with::

    python -m elymaker.gui        # or, once installed:  elymaker-gui

pyFIB handoff (open preloaded with a calibrated FIB image, Save writes --out)::

    elymaker-gui --job path/to/job.json
    elymaker-gui --image fib.png --pixel-size 5.58e-8 --out pos1.ely [--load prev.ely] [--fov-width 5.7e-5]

Requires a Qt binding via qtpy. On the Crossbeam PC (Python 3.9.13, Windows):
    pip install qtpy PyQt5
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="elymaker-gui", add_help=True)
    p.add_argument("--job", help="path to an elymaker handoff job JSON")
    p.add_argument("--image", help="FIB image to open as a calibrated underlay")
    p.add_argument("--pixel-size", type=float, dest="pixel_size",
                   help="sample-plane pixel size in METRES per pixel")
    p.add_argument("--fov-width", type=float, dest="fov_width",
                   help="FOV width in METRES (optional; display frame only)")
    p.add_argument("--out", help="path to write the .ely on Save")
    p.add_argument("--load", help="existing .ely to preload for editing")
    return p


def resolve_handoff(args) -> Optional[dict]:
    """Turn parsed args into open_handoff() kwargs (um), or None for a plain launch.

    Pure/testable: no Qt, no window. A --job file wins; otherwise --image +
    --pixel-size + --out are required together.
    """
    if args.job:
        from ..handoff import load_job
        job = load_job(args.job)
        return dict(image_path=job.image, pixel_size_um=job.pixel_size_um(),
                    out_path=job.out, load_path=job.load,
                    fov_width_um=job.fov_width_um(), site=job.site)
    if args.image or args.pixel_size or args.out:
        missing = [n for n, v in (("--image", args.image),
                                  ("--pixel-size", args.pixel_size),
                                  ("--out", args.out)) if not v]
        if missing:
            raise SystemExit("handoff needs %s together" % ", ".join(missing))
        return dict(image_path=args.image, pixel_size_um=args.pixel_size * 1e6,
                    out_path=args.out, load_path=args.load,
                    fov_width_um=(args.fov_width * 1e6 if args.fov_width else None),
                    site=None)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    try:
        from qtpy.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(
            "A Qt binding is required. Install one with:\n"
            "    pip install qtpy PyQt5\n"
            "(original error: %s)\n" % exc
        )
        return 2

    argv = list(sys.argv if argv is None else argv)
    args, _unknown = _build_parser().parse_known_args(argv[1:])

    from .main_window import MainWindow
    from .style import apply_theme

    app = QApplication.instance() or QApplication(argv)
    apply_theme(app)
    win = MainWindow()

    handoff = resolve_handoff(args)
    if handoff is not None:
        win.open_handoff(**handoff)

    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
