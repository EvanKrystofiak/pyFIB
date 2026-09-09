"""pyFIB <-> elymaker handoff contract.

pyFIB captures a FIB image at a position and hands elymaker a *job*: the image,
its scale, where to write the resulting ``.ely``, and optionally an existing
pattern to edit. elymaker opens preloaded with the calibrated image (centred on
the FOV centre = layout origin, the milling convention), and Save writes the
job's ``out`` path.

This module is pure/stdlib (no Qt), so both the CLI launcher and headless
tooling use the same contract. Paths in a job file may be relative to the job
file's own directory; :func:`load_job` resolves them to absolute.

Scale note: ``pixel_size_m`` is the authoritative sample-plane pixel size
(metres/pixel) that pyFIB records at capture -- NOT ``AP_FIB_PIXEL_SIZE`` (the
store-grid value). elymaker needs nothing else to place shapes correctly:
``layout_x_um = (px - W/2) * pixel_size_m * 1e6``, etc. ``fov_width_m`` is
optional and used only to set the display ``frame_size`` (confirmed on the
instrument to not affect placement).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional

HANDOFF_SCHEMA = "elymaker.handoff.v1"
MARKER_SCHEMA = "elymaker.ready.v1"

_FIELDS = {
    "image", "pixel_size_m", "out", "load", "fov_width_m",
    "image_width_px", "image_height_px", "center", "site", "schema",
}


@dataclass
class HandoffJob:
    image: str                      # path to the FIB image (bmp/png/tif)
    pixel_size_m: float             # sample-plane pixel size (m/px), authoritative
    out: str                        # path to write the .ely
    load: Optional[str] = None      # optional existing .ely to preload
    fov_width_m: Optional[float] = None
    image_width_px: Optional[int] = None
    image_height_px: Optional[int] = None
    center: str = "origin"          # "origin" = FOV centre at layout (0,0)
    site: Optional[str] = None      # position/site id, echoed into the ready-marker
    schema: str = HANDOFF_SCHEMA

    # -- unit helpers ----------------------------------------------------- #
    def pixel_size_um(self) -> float:
        return self.pixel_size_m * 1e6

    def fov_width_um(self) -> Optional[float]:
        return None if self.fov_width_m is None else self.fov_width_m * 1e6


def save_job(job: HandoffJob, path: str) -> None:
    with open(path, "w") as fh:
        json.dump(asdict(job), fh, indent=2)


def load_job(path: str) -> HandoffJob:
    """Load a job; resolve relative image/load/out against the job's directory."""
    with open(path) as fh:
        data = json.load(fh)
    schema = data.get("schema", HANDOFF_SCHEMA)
    if not str(schema).startswith("elymaker.handoff."):
        raise ValueError("not an elymaker handoff job (schema=%r)" % schema)
    kwargs = {k: v for k, v in data.items() if k in _FIELDS}
    if "image" not in kwargs or "pixel_size_m" not in kwargs or "out" not in kwargs:
        raise ValueError("job missing required image/pixel_size_m/out")
    job = HandoffJob(**kwargs)
    base = os.path.dirname(os.path.abspath(path))

    def _resolve(p):
        return p if (p is None or os.path.isabs(p)) else os.path.normpath(os.path.join(base, p))

    job.image = _resolve(job.image)
    job.out = _resolve(job.out)
    job.load = _resolve(job.load)
    return job


# --------------------------------------------------------------------------- #
# Headless job runner (no Qt): produce the .ely for a job without a GUI.
# --------------------------------------------------------------------------- #
def run_job(job: "HandoffJob", lamella_spec=None, write_marker: bool = False):
    """Write ``job.out`` without launching the editor.

    Source of the pattern, in priority order:
      1. an explicit ``lamella_spec`` (elymaker.lamella.LamellaSpec) -> generate;
      2. ``job.load`` (an existing .ely) -> copy through the model;
      3. otherwise an empty layout.

    If ``write_marker`` is set, also publish the ready-marker beside ``job.out``
    (carrying ``job.site``). Returns the number of shapes written. Pure/stdlib +
    core only -- safe to call from pyFIB in CI or for scripted 'regenerate this
    position' runs.
    """
    from .reader import read_ely
    from .writer import write_ely
    from .model import Layout, Layer, LayerReference, Structure, VersionInfo

    frame_um = job.fov_width_um() or 100.0
    if lamella_spec is not None:
        from .lamella import build_lamella
        shapes = build_lamella(lamella_spec)
        lay = Layout(name="lamella",
                     version=VersionInfo(created="", modified=""), layers=[Layer()])
        lref = LayerReference(ref="Layer", frame_size=frame_um)
        lref.shapes = shapes
        lay.structures = [Structure(name="Structure",
                          version=VersionInfo(created="", modified=""), layer_refs=[lref])]
    elif job.load:
        lay = read_ely(job.load)
    else:
        lay = Layout(name="pattern",
                     version=VersionInfo(created="", modified=""), layers=[Layer()])
        lref = LayerReference(ref="Layer", frame_size=frame_um)
        lay.structures = [Structure(name="Structure",
                          version=VersionInfo(created="", modified=""), layer_refs=[lref])]

    out_dir = os.path.dirname(os.path.abspath(job.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    write_ely(lay, job.out)
    if write_marker:
        write_ready_marker(job.out, target_site=job.site)
    return lay.shape_count()


def _main(argv=None) -> int:
    """`python -m elymaker.handoff run <job.json>` -- headless job runner."""
    import argparse
    p = argparse.ArgumentParser(prog="elymaker.handoff")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="write a job's .ely without the GUI")
    r.add_argument("job", help="path to a handoff job JSON")
    args = p.parse_args(argv)
    if args.cmd == "run":
        job = load_job(args.job)
        n = run_job(job)
        print("wrote %d shapes -> %s" % (n, job.out))
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(_main())


# --------------------------------------------------------------------------- #
# Ready-marker: pyFIB watches for this JSON sidecar. It means "a pattern is
# staged and ready to load" -- NOT "mill". Milling stays behind pyFIB's
# ARM/interlocks. The marker carries enough to detect staleness (content hash)
# and to route the file (ely path + target site).
# --------------------------------------------------------------------------- #
def ely_content_hash(ely_path: str) -> str:
    """SHA-256 of the .ely file bytes (staleness / integrity check)."""
    import hashlib
    h = hashlib.sha256()
    with open(ely_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def marker_path_for(ely_path: str) -> str:
    """Conventional marker path beside the .ely (``<name>.ely.ready.json``)."""
    return ely_path + ".ready.json"


def write_ready_marker(ely_path: str, target_site: Optional[str] = None,
                       marker_path: Optional[str] = None,
                       extra: Optional[dict] = None) -> str:
    """Write the ready-marker for *ely_path*; return the marker path.

    Fields: schema, ely path, sha-256 content hash, elymaker version, shape
    count, timestamp, and optional ``target_site`` (position/site id). ``extra``
    is merged in for anything pyFIB wants to round-trip. Written atomically
    (temp + replace) so pyFIB never sees a half-written marker.
    """
    import time
    from . import __version__ as _ver
    from .reader import read_ely

    ely_abs = os.path.abspath(ely_path)
    try:
        n = read_ely(ely_abs).shape_count()
    except Exception:
        n = None
    data = {
        "schema": MARKER_SCHEMA,
        "ely": ely_abs,
        "content_hash": ely_content_hash(ely_abs),
        "elymaker_version": _ver,
        "shape_count": n,
        "target_site": target_site,
        "written_unix_s": time.time(),
        "state": "ready",          # ready != mill
    }
    if extra:
        data.update(extra)
    mp = marker_path or marker_path_for(ely_abs)
    tmp = mp + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, mp)            # atomic publish
    return mp


def read_ready_marker(marker_path: str) -> dict:
    with open(marker_path) as fh:
        data = json.load(fh)
    if str(data.get("schema", "")).split(".")[:2] != MARKER_SCHEMA.split(".")[:2]:
        raise ValueError("not an elymaker ready-marker (schema=%r)"
                         % data.get("schema"))
    return data


def marker_is_current(marker: dict) -> bool:
    """True if the marker's hash still matches the .ely on disk (not stale)."""
    ely = marker.get("ely")
    if not ely or not os.path.exists(ely):
        return False
    try:
        return ely_content_hash(ely) == marker.get("content_hash")
    except Exception:
        return False
