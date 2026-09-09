"""Command-line interface for elymaker.

    python -m elymaker info      FILE.ely
    python -m elymaker validate  FILE.ely
    python -m elymaker preview    FILE.ely  [-o OUT.svg]
    python -m elymaker roundtrip FILE.ely  [-o OUT.ely]

`roundtrip` reads and rewrites a file (useful to confirm lossless parsing and to
normalise formatting). All commands are read-only with respect to the
instrument; this tool never talks to SmartSEM.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__
from .reader import read_ely
from .writer import write_ely, dumps
from .validate import validate
from .preview import write_svg
from .model import Rect, Polygon, Arc, Text


def _cmd_info(args) -> int:
    lay = read_ely(args.file)
    print("name:        %s" % lay.name)
    print("version:     ELAYOUT %s (doc %s)" % (lay.version_attr, lay.version.number))
    print("structures:  %d" % len(lay.structures))
    print("shapes:      %d" % lay.shape_count())
    counts = {"RECT": 0, "POLYGON": 0, "ARC": 0, "TEXT": 0}
    probes = set()
    recipes = set()
    depths = set()
    for _st, _lr, sh in lay.iter_shapes():
        counts[type(sh).__name__.upper()] = counts.get(type(sh).__name__.upper(), 0) + 1
        depths.add(round(sh.depth_um, 6))
        if sh.exposure:
            recipes.add(sh.exposure.file)
            if sh.exposure.probe:
                probes.add(sh.exposure.probe.name)
    print("  by type:   " + ", ".join("%s=%d" % (k, v) for k, v in counts.items() if v))
    print("depths (um): " + ", ".join(str(d) for d in sorted(depths)))
    print("probes:      " + (", ".join(sorted(probes)) or "(none)"))
    print("recipes:     " + ("; ".join(sorted(recipes)) or "(none)"))
    for lr in (lay.structures[0].layer_refs if lay.structures else []):
        print("fov (um):    %g about (%g, %g)" % (lr.frame_size, lr.frame_cx, lr.frame_cy))
        break
    return 0


def _cmd_validate(args) -> int:
    lay = read_ely(args.file)
    issues = validate(lay)
    if not issues:
        print("OK: no issues found (%d shapes)" % lay.shape_count())
        return 0
    errors = [i for i in issues if i.startswith("ERROR")]
    for i in issues:
        print(i)
    print("\n%d issue(s): %d error(s)" % (len(issues), len(errors)))
    return 1 if errors else 0


def _cmd_preview(args) -> int:
    lay = read_ely(args.file)
    out = args.output or (os.path.splitext(args.file)[0] + ".svg")
    write_svg(lay, out)
    print("wrote %s" % out)
    return 0


def _cmd_roundtrip(args) -> int:
    lay = read_ely(args.file)
    out = args.output or (os.path.splitext(args.file)[0] + ".roundtrip.ely")
    write_ely(lay, out)
    print("wrote %s (%d shapes)" % (out, lay.shape_count()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="elymaker", description="Author/inspect SmartFIB .ely files")
    p.add_argument("--version", action="version", version="elymaker %s" % __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("info", help="summarise a .ely file")
    pi.add_argument("file")
    pi.set_defaults(func=_cmd_info)

    pv = sub.add_parser("validate", help="check geometry/params")
    pv.add_argument("file")
    pv.set_defaults(func=_cmd_validate)

    pp = sub.add_parser("preview", help="render an SVG preview")
    pp.add_argument("file")
    pp.add_argument("-o", "--output")
    pp.set_defaults(func=_cmd_preview)

    pr = sub.add_parser("roundtrip", help="read and rewrite (lossless check)")
    pr.add_argument("file")
    pr.add_argument("-o", "--output")
    pr.set_defaults(func=_cmd_roundtrip)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
