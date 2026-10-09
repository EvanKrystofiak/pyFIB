"""Session report: one self-contained HTML file per session.

Reads only files: positions.json, the position images, the .ely patterns, the
fiducial files and the run logs (<session>/runs). It never connects to SmartSEM,
and the only file it writes is the report itself.

What each position shows:
  * the reference FIB image captured before milling, with the pattern drawn on it
    (the pattern from the latest logged attempt; else the position's authored
    pattern; else the standard pattern given with --pattern) and, if one was milled
    there, the fiducial;
  * the 800x image, if captured, with the 2000x field marked;
  * coordinates, imaging conditions and what the run log says happened.

Compact layout (v0.51.1, Evan: less text): per position one line for the stage position,
one for FIB imaging, then the patterns Rough -> Prepolish -> Polish, each with its status
and refine residual (run, time and after image on hover). All patterns used are drawn,
with their names, in the Patterns section after the stage layout. Positions are shown two
per row by default ("2 per row" toggle).

Milling stages (v0.51.0): the status, pattern and after image above are the Rough Mill
ones (runs without a stage, or stage "rough"). Prepolish and Polish runs (batch or
manual) are shown per position as their own after images -- each with its stage
pattern drawn on it, switchable with Before / Rough / Prepolish / Polish -- and their
own rows in the details.
"""

from __future__ import annotations

import base64
import html
import io
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .overlay import Overlay, load_overlay
from .runlog import file_sha256, load_run_logs

VIEW_LABEL = {"before": "Before", "rough": "Rough", "prepolish": "Prepolish",
              "polish": "Polish"}

STATUS_LABEL = {
    "milled": "Milled",
    "error": "Error",
    "stopped": "Stopped",
    "interrupted": "Interrupted",
    "running": "Interrupted",
    "not_milled": "Not milled",
    "assumed": "Milled (assumed)",
}

# The default lamella pattern (Evan, 2026-10-03). Drawn on positions with no run-log
# record and no authored pattern, unless --pattern names another file.
DEFAULT_PATTERN = Path(__file__).resolve().parents[1] / "assets" / "patterns" / "default.ely"


# --------------------------------------------------------------------------- helpers
def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _natural_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(name))]


def _fmt_time(t: Optional[float]) -> str:
    if t is None:
        return ""
    try:
        return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


def _fmt_iso(s: Optional[str]) -> str:
    if not s:
        return ""
    try:
        return datetime.fromisoformat(str(s)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return str(s)


def _f(v, nd=3, unit="") -> str:
    try:
        x = float(v)
        if not math.isfinite(x):
            return "n/a"
        return f"{x:.{nd}f}{unit}"
    except Exception:  # noqa: BLE001
        return "n/a"


def _pair(p, nd=2, unit=" µm") -> str:
    if not p or p[0] is None or p[1] is None:
        return "n/a"
    return f"({float(p[0]):+.{nd}f}, {float(p[1]):+.{nd}f}){unit}"


def _resolve(base: Path, p) -> Optional[Path]:
    if not p:
        return None
    q = Path(str(p).replace("\\", os.sep))
    return q if q.is_absolute() else base / q


def _image_data_uri(path: Path, *, max_width: Optional[int] = None,
                    quality: int = 85) -> Tuple[Optional[str], Optional[Tuple[int, int]], Optional[str]]:
    """(data URI, (w, h) of the ORIGINAL image, warning)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            size = im.size
            g = im.convert("L")
            if max_width and g.width > max_width:
                h = round(g.height * max_width / g.width)
                g = g.resize((max_width, h), Image.LANCZOS)
            buf = io.BytesIO()
            g.save(buf, format="JPEG", quality=int(quality), optimize=True)
        return ("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"),
                size, None)
    except FileNotFoundError:
        return None, None, f"image not found: {path}"
    except Exception as e:  # noqa: BLE001
        return None, None, f"could not read image {path.name}: {e}"


def _scale_bar_um(fov_um: float) -> float:
    target = 0.2 * fov_um
    best = 1.0
    for v in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        if v <= target:
            best = v
    return best


# --------------------------------------------------------------------------- model
@dataclass
class PositionView:
    name: str
    rec: Any
    status: str = "assumed"       # no run-log record -> assumed milled (Evan, 2026-10-03)
    skip_note: Optional[str] = None   # v0.46.0: a run skipped it (tilt limit)
    last_site: Optional[Dict[str, Any]] = None
    last_run: Optional[Dict[str, Any]] = None
    attempts: int = 0
    milled_runs: List[str] = field(default_factory=list)
    fiducial_runs: List[str] = field(default_factory=list)
    pattern_path: Optional[Path] = None
    pattern_label: str = ""
    pattern_overlay: Optional[Overlay] = None
    fiducial_path: Optional[Path] = None
    fiducial_label: str = ""
    fiducial_overlay: Optional[Overlay] = None
    image_uri: Optional[str] = None
    image_size: Optional[Tuple[int, int]] = None
    coarse_uri: Optional[str] = None
    coarse_size: Optional[Tuple[int, int]] = None
    after_path: Optional[Path] = None
    after_uri: Optional[str] = None
    after_size: Optional[Tuple[int, int]] = None
    warnings: List[str] = field(default_factory=list)
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # v0.51.0 prepolish/polish


@dataclass
class ReportResult:
    path: Path
    positions: int
    milled: int                       # logged as milled + assumed (no record)
    warnings: List[str]
    assumed: int = 0
    map_paths: Dict[str, Path] = field(default_factory=dict)
    mosaic: Any = None                # mosaic.MosaicResult (v0.43.0)


def _short_names(names: List[str]) -> Dict[str, str]:
    """Per name: 'position_12' -> '12'; any other name is kept as it is."""
    from .map_export import short_label
    return {n: short_label(n) for n in names}


def _find_after_image(session: Path, name: str, site: Optional[Dict[str, Any]],
                      rec=None) -> Optional[Path]:
    """The (Rough Mill) after-mill image: the one the latest logged attempt recorded, else
    the one positions.json records, else the newest <name>_1-rough_<time>.bmp or
    <name>_after_<time>.bmp in <session>/images (e.g. a run made with --no-run-log)."""
    if site and site.get("after_image"):
        p = _resolve(session, site["after_image"])
        if p is not None and p.exists():
            return p
    if rec is not None:
        from ..mill_stages import latest_after_image
        return latest_after_image(rec, "rough", session)
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
    found = sorted((session / "images").glob(f"{safe}_after_*.bmp")) \
        if (session / "images").is_dir() else []
    found = [p for p in found if re.fullmatch(re.escape(safe) + r"_after_\d{8}_\d{6}\.bmp", p.name)]
    return found[-1] if found else None


def _stage_view(session: Path, runs, v, stage: str, W: int, H: int, ps_um,
                jpeg_quality: int) -> Optional[Dict[str, Any]]:
    """What the run logs and the images folder say about one later stage (prepolish /
    polish) at one position, or None if nothing does."""
    from ..mill_stages import STAGE_LABEL, latest_after_image
    label = STAGE_LABEL[stage]
    last_site = last_run = skip = None
    attempts = 0
    for r in runs:
        if r.get("kind") == "fiducial" or r.get("stage") != stage:
            continue
        for site in r.get("sites", []):
            if site.get("name") == v.name:
                attempts += 1
                last_site, last_run = site, r
        for sk in r.get("skipped", []) or []:
            if sk.get("name") == v.name:
                skip = f"skipped by run {r['_file']}: {sk.get('reason', '')}"
    after = None
    if last_site and last_site.get("after_image"):
        p = _resolve(session, last_site["after_image"])
        after = p if (p is not None and p.exists()) else None
    if after is None:
        after = latest_after_image(v.rec, stage, session)
    if last_site is None and after is None and skip is None:
        return None
    if last_site is not None:
        status = last_site.get("status") or "running"
        status = "interrupted" if status == "running" else status
    else:
        status = "assumed" if after is not None else "not_milled"
    sv: Dict[str, Any] = {"stage": stage, "label": label, "status": status,
                          "site": last_site, "run": last_run, "attempts": attempts,
                          "skip_note": skip if last_site is None and after is None else None,
                          "after_path": after, "uri": None, "size": None,
                          "pattern_path": None, "overlay": None}
    if after is not None:
        sv["uri"], sv["size"], w = _image_data_uri(after, quality=jpeg_quality)
        if w:
            v.warnings.append(f"{label} after-image {w}")
    pat = (last_site or {}).get("pattern") or {}
    if pat.get("path"):
        pp = _resolve(session, pat["path"])
        sv["pattern_path"] = pp
        if pp is not None and W and H:
            sv["overlay"] = load_overlay(pp, ps_um, W, H)
            v.warnings.extend(f"{label} pattern: {w}" for w in sv["overlay"].warnings)
            if pp.exists() and pat.get("sha256") and file_sha256(pp) != pat["sha256"]:
                v.warnings.append(f"{pp.name} has CHANGED since it was milled ({label}); the "
                                  f"current file is drawn")
    if last_site and last_site.get("after_image_warning"):
        v.warnings.append(f"{label}: {last_site['after_image_warning']}")
    return sv


# --------------------------------------------------------------------------- build
def build_report(positions_file, *, standard_pattern=None, out_path=None,
                 include_coarse: bool = True, jpeg_quality: int = 80,
                 title: Optional[str] = None, export_map: bool = True,
                 map_um_per_px: float = 1.0, mosaic: bool = False,   # opt-in since v0.43.2
                 mosaic_scale: float = 0.25, clock=time.time) -> ReportResult:
    from ..positions import PositionDatabase
    try:
        from .. import __version__ as pyfib_version
    except Exception:  # noqa: BLE001
        pyfib_version = "?"

    pf = Path(positions_file)
    if pf.is_dir():
        pf = pf / "positions.json"
    pf = pf.resolve()
    session = pf.parent
    db = PositionDatabase.load(pf)
    if standard_pattern:
        std, std_note = Path(standard_pattern).resolve(), "given with --pattern"
    elif DEFAULT_PATTERN.exists():
        std, std_note = DEFAULT_PATTERN, "pyFIB default"
    else:
        std, std_note = None, ""
    runs = load_run_logs(session)
    warnings: List[str] = []
    for r in runs:
        if r.get("_error"):
            warnings.append(f"run log {r['_file']} could not be read: {r['_error']}")
    runs = [r for r in runs if not r.get("_error")]

    names = sorted(db.positions.keys(), key=_natural_key)
    views: Dict[str, PositionView] = {n: PositionView(name=n, rec=db.positions[n]) for n in names}

    # --- what the run logs say happened (lamella runs vs fiducial runs)
    for r in runs:
        for s in r.get("sites", []):
            v = views.get(s.get("name"))
            if v is None:
                continue
            if r.get("kind") == "fiducial":
                if s.get("status") == "milled":
                    v.fiducial_runs.append(r["_file"])
                continue
            if r.get("stage") in ("prepolish", "polish"):     # v0.51.0: shown per stage
                continue
            v.attempts += 1
            v.last_site, v.last_run = s, r
            st = s.get("status") or "running"
            if st == "running":           # never closed: the process was killed mid-site
                st = "interrupted"
            v.status = st
            if st == "milled":
                v.milled_runs.append(r["_file"])
    # Positions a run skipped on purpose (v0.46.0 tilt limit) and never milled by any
    # logged run are NOT milled -- the run log says so, so "assumed milled" doesn't apply.
    for r in runs:
        if r.get("kind") == "fiducial" or r.get("stage") in ("prepolish", "polish"):
            continue
        for sk in r.get("skipped", []) or []:
            v = views.get(sk.get("name"))
            if v is not None and v.attempts == 0:
                v.status = "not_milled"
                v.skip_note = f"skipped by run {r['_file']}: {sk.get('reason', '')}"
    for r in runs:
        for s in r.get("sites", []):
            if s.get("name") not in views:
                warnings.append(f"run {r['_file']} mentions {s.get('name')!r}, which is not "
                                f"in positions.json (deleted or renamed?)")

    # --- fiducial files in the session that no position records
    fid_dir = session / "fiducials"
    recorded = {(int(v.rec.fiducial_number), str(v.rec.fiducial_side).lower())
                for v in views.values()
                if getattr(v.rec, "fiducial_number", None) and getattr(v.rec, "fiducial_side", None)}
    unassigned = []
    if fid_dir.is_dir():
        for p in sorted(fid_dir.glob("num_*_*.ely")):
            m = re.fullmatch(r"num_(\d+)_(left|right)\.ely", p.name)
            if m and (int(m.group(1)), m.group(2)) not in recorded:
                unassigned.append(p.name)

    # --- per position: images and overlays
    for v in views.values():
        rec = v.rec
        img = _resolve(session, rec.image_bmp)
        if img is None:
            v.warnings.append("no reference image recorded")
        else:
            v.image_uri, v.image_size, w = _image_data_uri(img, quality=jpeg_quality)
            if w:
                v.warnings.append(w)
        W = int(rec.image_width_px or (v.image_size[0] if v.image_size else 0) or 0)
        H = int(rec.image_height_px or (v.image_size[1] if v.image_size else 0) or 0)
        if v.image_size and (W, H) != tuple(v.image_size):
            v.warnings.append(f"image is {v.image_size[0]}x{v.image_size[1]} px but the record "
                              f"says {W}x{H}; overlay uses the record")
        ps_um = float(rec.fib_pixel_size_m) * 1e6 if rec.fib_pixel_size_m else None

        # Which pattern to draw.
        site_pat = (v.last_site or {}).get("pattern") if v.last_site else None
        if site_pat and site_pat.get("path"):
            p = _resolve(session, site_pat["path"])
            v.pattern_path = p
            v.pattern_label = (f"{site_pat.get('source', 'pattern')} pattern of the latest "
                               f"attempt ({STATUS_LABEL.get(v.status, v.status).lower()})")
            if p is not None and p.exists() and site_pat.get("sha256") \
                    and file_sha256(p) != site_pat["sha256"]:
                v.warnings.append(f"{p.name} has CHANGED since it was milled; the current file "
                                  f"is drawn")
        elif getattr(rec, "ely_pattern", None):
            v.pattern_path = _resolve(session, rec.ely_pattern)
            v.pattern_label = ("authored pattern (the latest attempt stopped before milling)"
                               if v.last_site else
                               "authored pattern (not milled: skipped)" if v.skip_note else
                               "authored pattern (assumed milled: no run log)")
        elif std is not None:
            v.pattern_path = std
            v.pattern_label = (f"standard pattern, {std_note} (the latest attempt stopped "
                               f"before milling)" if v.last_site else
                               f"standard pattern, {std_note} (not milled: skipped)"
                               if v.skip_note else
                               f"standard pattern, {std_note} (assumed milled: no run log)")
        if v.pattern_path is not None and W and H:
            v.pattern_overlay = load_overlay(v.pattern_path, ps_um, W, H)
            v.warnings.extend(v.pattern_overlay.warnings)

        # Fiducial milled here (recorded on the position).
        side = getattr(rec, "fiducial_side", None)
        num = getattr(rec, "fiducial_number", None)
        if side:
            if num:
                v.fiducial_path = fid_dir / f"num_{int(num):02d}_{str(side).lower()}.ely"
                v.fiducial_label = f"fiducial #{int(num)} ({side})"
            else:
                try:
                    from ..fiducial import fiducial_ely_path
                    v.fiducial_path = fiducial_ely_path(str(side))
                except Exception:  # noqa: BLE001
                    v.fiducial_path = None
                v.fiducial_label = f"square fiducial ({side})"
            if v.fiducial_path is not None and W and H:
                v.fiducial_overlay = load_overlay(v.fiducial_path, ps_um, W, H)
                v.warnings.extend(f"fiducial: {w}" for w in v.fiducial_overlay.warnings)

        # After-mill image (v0.41.0), same view as the reference: pattern drawn on both.
        v.after_path = _find_after_image(session, v.name, v.last_site, rec)
        if v.last_site and v.last_site.get("after_image_warning"):
            v.warnings.append(v.last_site["after_image_warning"])
        if v.after_path is not None:
            v.after_uri, v.after_size, w = _image_data_uri(v.after_path, quality=jpeg_quality)
            if w:
                v.warnings.append(f"after-mill {w}")
            elif v.after_size and W and H and tuple(v.after_size) != (W, H):
                v.warnings.append(f"after-mill image is {v.after_size[0]}x{v.after_size[1]} px, "
                                  f"the reference {W}x{H}; it is scaled to match")

        # Prepolish / Polish (v0.51.0): their after images, patterns and run records.
        for stg in ("prepolish", "polish"):
            sv = _stage_view(session, runs, v, stg, W, H, ps_um, jpeg_quality)
            if sv is not None:
                v.stages[stg] = sv

        if include_coarse and getattr(rec, "image_bmp_coarse", None):
            cimg = _resolve(session, rec.image_bmp_coarse)
            v.coarse_uri, v.coarse_size, w = _image_data_uri(cimg, max_width=512,
                                                             quality=jpeg_quality)
            if w:
                v.warnings.append(f"800x {w}")

    # One current -> colour map for the whole report (v0.42.1): the position overlays and
    # the standard-pattern drawing use the same colour for the same current.
    from .pattern_diagram import current_classes, pattern_currents
    all_currents = [sh.current_A for v in views.values() if v.pattern_overlay
                    for sh in v.pattern_overlay.shapes if sh.current_A]
    all_currents += [sh.current_A for v in views.values() for sv in v.stages.values()
                     if sv.get("overlay") is not None
                     for sh in sv["overlay"].shapes if sh.current_A]
    if std is not None:
        all_currents += pattern_currents(std)
    cmap = current_classes(all_currents)

    assumed = sum(1 for v in views.values() if v.status == "assumed")
    milled = assumed + sum(1 for v in views.values() if v.status == "milled")
    t_gen = clock()
    if out_path is None:
        out_path = session / f"report_{datetime.fromtimestamp(t_gen).strftime('%Y%m%d_%H%M%S')}.html"
    out_path = Path(out_path)

    # Positions map for pyCLEM (v0.41.0): <report>_positions_map.png/.tif/.json/.csv.
    map_paths: Dict[str, Path] = {}
    if export_map and views:
        try:
            from .map_export import write_positions_map
            rs = sorted({round(float(v.rec.stage_at.r_deg), 2) for v in views.values()})
            ts = sorted({round(float(v.rec.stage_at.t_deg), 2) for v in views.values()
                         if v.rec.stage_at.t_deg is not None})
            map_paths = write_positions_map(_stage_rows(views),
                out_path.with_name(out_path.stem + "_positions_map"),
                um_per_px=map_um_per_px, clock=clock,
                meta={"session": session.name, "positions_file": str(pf),
                      "stage_r_deg": rs, "stage_t_deg": ts,
                      "microscope_serial": db.microscope_serial})
            if len(rs) > 1:
                warnings.append(f"positions were captured at different stage rotations {rs}: "
                                f"their stage X/Y may not share one frame on the map")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"positions map for pyCLEM not written: {e}")
    # Stage Z (v0.45.0): only a warning when Z differs and the measured model can't apply.
    if views:
        try:
            from .map_export import apply_z_model
            _zxy, _zi = apply_z_model(_stage_rows(views))
            if _zi["unmodelled"]:
                warnings.append("positions at a different stage Z are placed by X/Y only on "
                                "the stage layout and map (" + _zi["reason"] + "): "
                                + ", ".join(_zi["unmodelled"]))
        except Exception:  # noqa: BLE001
            pass

    # 800x mosaic (v0.43.0): the positions' 800x frames placed by stage position.
    mosaic_res = None
    if mosaic:
        items = []
        for v in views.values():
            cp = _resolve(session, getattr(v.rec, "image_bmp_coarse", None))
            if cp is not None and cp.exists():
                items.append((v.name, v.rec, cp))
        if items:
            try:
                from .mosaic import build_mosaic
                from .map_export import short_label
                from .map_export import apply_z_model
                zxy, zi = apply_z_model(_stage_rows(views))
                xy_eff = ({n: (x * 1e-3, y * 1e-3) for n, (x, y) in zxy.items()}
                          if zi["applied"] else None)
                mosaic_res = build_mosaic(items, out_path.with_name(out_path.stem + "_mosaic_800x"),
                                          scale=mosaic_scale, label_of=short_label,
                                          xy_eff_m=xy_eff)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"800x mosaic not made: {e}")

    doc = _render(session=session, pf=pf, db=db, views=views, runs=runs, std=std,
                  std_note=std_note, unassigned=unassigned, warnings=warnings, title=title,
                  generated=_fmt_time(t_gen), version=pyfib_version, map_paths=map_paths,
                  cmap=cmap, mosaic_res=mosaic_res)
    out_path.write_text(doc, encoding="utf-8")
    return ReportResult(path=out_path, positions=len(views), milled=milled,
                        warnings=warnings, assumed=assumed, map_paths=map_paths,
                        mosaic=mosaic_res)


# --------------------------------------------------------------------------- render
CSS = """
:root{--bg:#f6f6f4;--surface:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--ink3:#7a7974;
--line:#e2e1dc;--accent:#0f8b8d;--good:#0ca30c;--crit:#d03b3b;--serious:#ec835a;
--neutral:#8a8983;--pat:#19c6d8;--fid:#fab219;--chip:#f0efeb}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121211;
--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--ink3:#9a998f;--line:#2e2e2b;
--accent:#3fb8ba;--chip:#262624}}
:root[data-theme="dark"]{--bg:#121211;--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;
--ink3:#9a998f;--line:#2e2e2b;--accent:#3fb8ba;--chip:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 "Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif}
main{max-width:1240px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:36px 0 12px}
h3{font-size:17px;margin:0}
.sub{color:var(--ink2);margin:0 0 16px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 24px;
background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.meta div span{display:block;color:var(--ink3);font-size:12px;text-transform:uppercase;
letter-spacing:.04em}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:10px 16px;min-width:130px}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.tile span{color:var(--ink2);font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:16px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--ink2);font-weight:600;font-size:12.5px}
td.num,th.num{text-align:right}
a{color:var(--accent)}
.badge{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;
padding:2px 9px;border-radius:999px;background:var(--chip);color:var(--ink)}
.badge i{width:9px;height:9px;border-radius:50%;display:inline-block}
.s-milled i{background:var(--good)} .s-error i{background:var(--crit)}
.s-stopped i,.s-interrupted i,.s-running i{background:var(--serious)}
.s-not_milled i{background:transparent;border:2px solid var(--neutral)}
.s-assumed i{background:transparent;border:2px solid var(--good)}
.pos,h2{scroll-margin-top:56px}
.badge.sm{font-size:12px;padding:1px 8px;width:104px}
.cell{display:inline-flex;align-items:center;gap:6px}
.cell i{width:9px;height:9px;border-radius:50%;display:inline-block}
td.dim{color:var(--ink3)}
table.tight th,table.tight td{padding:4px 8px}
dl.kv{grid-template-columns:auto 1fr;gap:6px 12px;font-size:13.5px}
.stl{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:0 10px;align-items:center;
margin:0 0 5px}
.stl .pn{overflow-wrap:anywhere} .stl .rs{font-variant-numeric:tabular-nums;color:var(--ink2);
white-space:nowrap}
.stl .sn{grid-column:2/-1;color:var(--ink3);font-size:12.5px}
.stl .sn1{color:var(--ink3);font-size:12.5px}
.stl.sth{margin:0 0 -4px;font-size:12px} .stl.sth .rs{color:var(--ink3);font-weight:600}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:16px}
.pcard h3{font-size:15px;margin:0} .pcard .note{margin:2px 0 8px}
.pcard figure{margin-bottom:6px}
.pos{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:16px;margin:18px 0;break-inside:avoid;page-break-inside:avoid}
.pos header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.pos header .when{color:var(--ink3);font-size:13px;margin-left:auto}
.grid{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(0,1fr);gap:18px}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
figure{margin:0} figcaption{color:var(--ink2);font-size:12.5px;margin-top:6px}
svg.fig{width:100%;height:auto;display:block;background:#000;border-radius:6px}
.fig-wrap .iv,.fig-wrap .pv{display:none}
.fig-wrap[data-view=before] .v-before,.fig-wrap[data-view=rough] .v-rough,
.fig-wrap[data-view=prepolish] .v-prepolish,.fig-wrap[data-view=polish] .v-polish{display:inline}
.hide-inset .inset{display:none}
.ba{display:inline-flex;margin-right:8px;vertical-align:middle}
.ba button,.tb-ba button{font:inherit;font-size:12.5px;padding:2px 10px;border:1px solid var(--line);border-radius:0;background:var(--surface);color:var(--ink);cursor:pointer}
.ba button:first-child,.tb-ba button:first-child{border-radius:6px 0 0 6px}
.ba button:last-child,.tb-ba button:last-child{border-radius:0 6px 6px 0;border-left:none}
.ba button+button,.tb-ba button+button{border-left:none}
.fig-wrap[data-view=before] .ba [data-show=before],.fig-wrap[data-view=rough] .ba [data-show=rough],
.fig-wrap[data-view=prepolish] .ba [data-show=prepolish],.fig-wrap[data-view=polish] .ba [data-show=polish]{background:var(--accent);color:#fff;border-color:var(--accent)}
.ov polygon{fill-opacity:.2;stroke-width:2;vector-effect:non-scaling-stroke}
.ov-pattern polygon{fill:var(--cc,#19c6d8);stroke:var(--cc,#19c6d8)}
.ov-fid polygon{fill:#a020f0;fill-opacity:.3;stroke:#a020f0;stroke-width:2.5}
.ov text{font-family:sans-serif}
.ov-pattern text{fill:var(--cc,#19c6d8)} .ov-fid text{fill:#a020f0}
.hide-ov .ov{display:none}
.key{display:inline-flex;align-items:center;gap:5px;margin-right:12px}
.key i{width:14px;height:10px;display:inline-block;border:2px solid;border-radius:2px}
dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0;font-size:13.5px}
dt{color:var(--ink3)} dd{margin:0;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.sect{grid-column:1/-1;margin-top:10px;font-weight:600;color:var(--ink2);font-size:12px;
text-transform:uppercase;letter-spacing:.04em;border-top:1px solid var(--line);padding-top:8px}
.warn{color:var(--crit);font-size:13px;margin:8px 0 0;padding-left:18px}
.note{color:var(--ink2);font-size:13px}
.toolbar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;
border-bottom:1px solid var(--line);display:flex;gap:18px;align-items:center;flex-wrap:wrap}
.toolbar label{font-size:14px;cursor:pointer}
.plot svg.chart{width:100%;height:auto;max-height:420px;display:block}
.two-up #plist{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 16px}
.two-up .pos .grid{grid-template-columns:1fr;gap:10px}
@media (max-width:900px){.two-up #plist{grid-template-columns:1fr}}
.key svg{width:16px;height:16px;flex:none}
.plot .gridl{stroke:var(--line);stroke-width:1}
.plot .axis text{fill:var(--ink3);font-size:11px}
.plot .lbl{fill:var(--ink2);font-size:11px}
.plot .pt{cursor:pointer}
svg.patdia{width:100%;max-width:1000px;height:auto;display:block;margin:0 auto 6px}
svg.mosaic{width:100%;height:auto;display:block;background:#111;border-radius:6px}
.sh{stroke:var(--ink2);stroke-width:.8;vector-effect:non-scaling-stroke}
.c0{--cc:#56B4E9}.c1{--cc:#009E73}.c2{--cc:#F0E442}.c3{--cc:#E69F00}.c4{--cc:#D55E00}
.c5{--cc:#CC79A7}.cn{--cc:#d9d9d9}
.sh{fill:var(--cc)}
.key i.sw{border-color:var(--cc);background:var(--cc)}
.patdia .ext{stroke:var(--ink3);stroke-width:.8;stroke-dasharray:3 3}
.patdia .dim{stroke:var(--ink2);stroke-width:1}
.patdia .lead{stroke:var(--ink3);stroke-width:.8}
.patdia .dl,.patdia .cl{fill:var(--ink);font-size:12.5px}
@media print{.toolbar{display:none}body{background:#fff}.pos,.card,.meta,.tile{border-color:#ccc}}
"""

JS = """
document.getElementById('ovtoggle').addEventListener('change',function(e){
  document.body.classList.toggle('hide-ov',!e.target.checked);});
document.getElementById('twoup').addEventListener('change',function(e){
  document.body.classList.toggle('two-up',e.target.checked);});
document.getElementById('insettoggle').addEventListener('change',function(e){
  document.body.classList.toggle('hide-inset',!e.target.checked);});
document.querySelectorAll('.ba button').forEach(function(b){
  b.addEventListener('click',function(){
    b.closest('.fig-wrap').dataset.view=b.dataset.show;});});
document.querySelectorAll('.tb-ba button').forEach(function(b){
  b.addEventListener('click',function(){
    document.querySelectorAll('.fig-wrap.has-after').forEach(function(f){
      if((' '+f.dataset.views+' ').indexOf(' '+b.dataset.show+' ')>=0){
        f.dataset.view=b.dataset.show;}});});});
"""


def _badge(status: str) -> str:
    return (f'<span class="badge s-{_esc(status)}"><i aria-hidden="true"></i>'
            f'{_esc(STATUS_LABEL.get(status, status))}</span>')


def _poly_points(pts) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def _overlay_svg(ov: Optional[Overlay], cls: str, cmap=None) -> str:
    """Overlay outlines; with cmap, each shape takes its probe current's colour."""
    if ov is None or not ov.shapes:
        return ""
    from .pattern_diagram import class_for
    parts = [f'<g class="ov {cls}">']
    for s in ov.shapes:
        c = f' class="{class_for(cmap, s.current_A)}"' if cmap is not None else ""
        if s.kind == "polygon":
            parts.append(f'<polygon{c} points="{_poly_points(s.points_px)}"/>')
        elif s.kind == "text" and s.points_px:
            x, y = s.points_px[0]
            parts.append(f'<text{c} x="{x:.1f}" y="{y:.1f}" font-size="{s.text_height_px:.1f}" '
                         f'text-anchor="middle" dominant-baseline="central">{_esc(s.text)}</text>')
    parts.append("</g>")
    return "".join(parts)


def _figure(v: PositionView, cmap=None) -> str:
    """Reference image (and after-mill image, switchable) with the pattern drawn on it,
    and the 800x image as an inset in the top-right corner."""
    rec = v.rec
    if not v.image_uri:
        return '<div class="card note">No reference image available.</div>'
    W = int(rec.image_width_px or v.image_size[0])
    H = int(rec.image_height_px or v.image_size[1])
    ps_um = float(rec.fib_pixel_size_m) * 1e6 if rec.fib_pixel_size_m else None
    has_after = bool(v.after_uri)
    # v0.51.0: one image per view -- before (capture), rough / prepolish / polish after
    # images -- each with the pattern of that stage drawn on it.
    views = ["before"] + (["rough"] if has_after else []) + [
        s for s in ("prepolish", "polish") if (v.stages.get(s) or {}).get("uri")]
    svg = [f'<svg class="fig" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
           f'role="img" aria-label="{_esc(v.name)} FIB image">',
           f'<image class="im-before iv v-before" x="0" y="0" width="{W}" height="{H}" '
           f'href="{v.image_uri}" preserveAspectRatio="none"/>']
    if has_after:
        svg.append(f'<image class="im-after iv v-rough" x="0" y="0" width="{W}" height="{H}" '
                   f'href="{v.after_uri}" preserveAspectRatio="none"/>')
    for stg in views[1 + int(has_after):]:
        svg.append(f'<image class="im-{stg} iv v-{stg}" x="0" y="0" width="{W}" height="{H}" '
                   f'href="{v.stages[stg]["uri"]}" preserveAspectRatio="none"/>')
    svg.append('<g class="pv v-before v-rough">'
               + _overlay_svg(v.pattern_overlay, "ov-pattern", cmap or {}) + '</g>')
    for stg in views[1 + int(has_after):]:
        svg.append(f'<g class="pv v-{stg}">'
                   + _overlay_svg(v.stages[stg].get("overlay"), "ov-pattern", cmap or {})
                   + '</g>')
    svg += [_overlay_svg(v.fiducial_overlay, "ov-fid")]
    c = 14                                           # FOV centre = pattern origin
    svg.append(f'<g class="ov" stroke="#ffffff" stroke-opacity=".75" stroke-width="1.2">'
               f'<line x1="{W/2-c}" y1="{H/2}" x2="{W/2+c}" y2="{H/2}" '
               f'vector-effect="non-scaling-stroke"/>'
               f'<line x1="{W/2}" y1="{H/2-c}" x2="{W/2}" y2="{H/2+c}" '
               f'vector-effect="non-scaling-stroke"/></g>')
    if ps_um:
        bar_um = _scale_bar_um(W * ps_um)
        bar_px = bar_um / ps_um
        x0, y0 = 24, H - 30
        svg.append(f'<rect x="{x0-8}" y="{y0-30}" width="{bar_px+16}" height="44" rx="4" '
                   f'fill="#000" fill-opacity=".55"/>'
                   f'<rect x="{x0}" y="{y0}" width="{bar_px:.1f}" height="6" fill="#fff"/>'
                   f'<text x="{x0 + bar_px/2:.1f}" y="{y0-8}" fill="#fff" font-size="20" '
                   f'text-anchor="middle" font-family="sans-serif">{bar_um:g} µm</text>')
    # 800x inset, top right, with the 2000x field marked.
    cmag = float(getattr(rec, "coarse_magnification_x", 0) or 0)
    if v.coarse_uri and v.coarse_size:
        cw, ch = v.coarse_size
        iw = 0.30 * W
        ih = iw * ch / cw
        ix, iy = W - iw - 10, 10
        svg.append(f'<g class="inset"><rect x="{ix-3:.1f}" y="{iy-3:.1f}" width="{iw+6:.1f}" '
                   f'height="{ih+6:.1f}" fill="#000" stroke="#fff" stroke-width="1.5" '
                   f'vector-effect="non-scaling-stroke"/>'
                   f'<image x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}" '
                   f'href="{v.coarse_uri}" preserveAspectRatio="none"/>')
        if cmag > 0 and rec.fib_magnification_x:
            fr = cmag / float(rec.fib_magnification_x)      # fine FOV / coarse FOV
            bw, bh = iw * fr, ih * fr
            svg.append(f'<rect x="{ix + (iw-bw)/2:.1f}" y="{iy + (ih-bh)/2:.1f}" '
                       f'width="{bw:.1f}" height="{bh:.1f}" fill="none" stroke="#fff" '
                       f'stroke-dasharray="5 4" stroke-width="1.3" '
                       f'vector-effect="non-scaling-stroke"/>')
        svg.append(f'<text x="{ix+8:.1f}" y="{iy+ih-8:.1f}" fill="#fff" font-size="18" '
                   f'font-family="sans-serif" stroke="#000" stroke-width="3" '
                   f'paint-order="stroke">{cmag:g}x</text></g>')
    svg.append("</svg>")

    # v0.51.1: the caption is only the view switch and the key of the pattern on show
    # (each key belongs to its view); the shared notes are said once above the positions.
    from .pattern_diagram import class_for, fmt_current

    def _key(ov, path, view_cls: str) -> str:
        if ov is None or not ov.shapes or path is None:
            return ""
        seen: Dict[float, str] = {}
        for sh in ov.shapes:                          # one key per current, highest first
            seen.setdefault(sh.current_A or 0.0, class_for(cmap or {}, sh.current_A))
        return (f'<span class="pv {view_cls}"><span class="key">{_esc(path.name)}</span>'
                + "".join(f'<span class="key"><i class="sw {seen[a]}"></i>'
                          f'{_esc(fmt_current(a or None))}</span>' for a in sorted(seen, reverse=True))
                + "</span>")
    keys = [_key(v.pattern_overlay, v.pattern_path, "v-before v-rough")]
    for stg in views[1 + int(has_after):]:
        sv = v.stages[stg]
        keys.append(_key(sv.get("overlay"), sv.get("pattern_path"), f"v-{stg}"))
    if v.fiducial_overlay is not None and v.fiducial_overlay.shapes:
        keys.append(f'<span class="key"><i style="border-color:#a020f0;'
                    f'background:rgba(160,32,240,.3)"></i>{_esc(v.fiducial_label)}</span>')
    switch = ""
    if len(views) > 1:
        switch = ('<span class="ba" role="group" aria-label="Before or after each stage">'
                  + "".join(f'<button type="button" data-show="{w}">{_esc(VIEW_LABEL[w])}'
                            f'</button>' for w in views) + '</span> ')
    cls = "fig-wrap has-after" if len(views) > 1 else "fig-wrap"
    return (f'<figure class="{cls}" data-view="{views[-1]}" data-views="{" ".join(views)}">'
            f'{"".join(svg)}<figcaption>{switch}{"".join(keys)}</figcaption></figure>')


def _refine_text(rf: Dict[str, Any]) -> str:
    txt = (f"aligned via {_esc(rf.get('method'))}" if rf.get("success") else "NOT refined")
    if rf.get("reference"):
        txt += f" to {_esc(rf.get('reference'))}"
    txt += (f" · drift {_pair(rf.get('initial_drift_um'))}"
            f" → residual {_f(rf.get('residual_um'), 2)} µm")
    sd = rf.get("stage_delta_um") or [0, 0]
    if sd and any(abs(x or 0) > 0 for x in sd):
        txt += f" · stage moved {_pair(sd)}"
    if rf.get("coarse_fallback_tried"):
        cs = rf.get("coarse_stage_delta_um") or [0, 0]
        txt += " · 800x fallback used" + (
            f" (stage {_pair(cs)})" if any(abs(x or 0) > 0 for x in cs) else "")
    if not rf.get("success") and rf.get("message"):
        txt += f" · {_esc(rf.get('message'))}"
    return txt


def _short(msg, n: int = 90) -> str:
    """A message without its exception class / '[tag] name:' prefix, at most n chars."""
    s = re.sub(r"^\w*(Error|Exception):\s*", "", str(msg or "")).strip()
    s = re.sub(r"^\[[\w-]+\]\s*[\w.-]+:\s*", "", s)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _stage_entries(v: PositionView) -> List[Dict[str, Any]]:
    """Rough (from the main view) + Prepolish / Polish (v.stages), in order."""
    site_pat = ((v.last_site or {}).get("pattern") or {})
    source = site_pat.get("source") or (
        "authored" if (v.pattern_path is not None and getattr(v.rec, "ely_pattern", None)
                       and Path(str(v.rec.ely_pattern).replace("\\", "/")).name
                       == v.pattern_path.name) else "standard")
    rough = {"stage": "rough", "label": "Rough", "status": v.status, "site": v.last_site,
             "run": v.last_run, "attempts": v.attempts, "skip_note": v.skip_note,
             "pattern_path": v.pattern_path, "after_path": v.after_path, "source": source,
             "milled_runs": v.milled_runs}
    return [rough] + [v.stages[s] for s in ("prepolish", "polish") if s in v.stages]


def _residual(site: Optional[Dict[str, Any]]) -> str:
    if not site:
        return ""
    rf = site.get("refine")
    if not rf:
        return "no refine"
    if not rf.get("success"):
        return "refine failed"
    out = f"{_f(rf.get('residual_um'), 2)} µm"
    sd = rf.get("stage_delta_um") or [0, 0]
    if rf.get("coarse_fallback_tried"):
        out += " · 800x"
    if any(abs(x or 0) > 0 for x in sd) or any(abs(x or 0) > 0 for x in
                                               (rf.get("coarse_stage_delta_um") or [0, 0])):
        out += " · stage"
    return out


def _stage_line(e: Dict[str, Any]) -> str:
    """One line per stage: status badge (stage name), pattern, refine residual; a short note
    underneath only when something needs saying. Run, time and after image on hover."""
    st = e["status"]
    site = e.get("site") or {}
    run = e.get("run") or {}
    pat = e["pattern_path"].name if e.get("pattern_path") is not None else "—"
    if e.get("source") == "authored":
        pat += " (authored)"
    notes, full = [], []
    if site and st in ("error", "stopped", "interrupted", "running") and site.get("message"):
        notes.append(_short(site["message"]))
        full.append(str(site["message"]))
    if not site and e.get("skip_note"):
        why = str(e["skip_note"]).split(": ", 1)[-1]
        notes.append(f"skipped: {why}")
        full.append(str(e["skip_note"]))
    if st == "assumed":
        notes.append("no run log")
    if e.get("attempts", 0) > 1:
        notes.append(f"{e['attempts']} attempts")
    if st != "milled" and e.get("milled_runs"):
        notes.append("milled earlier")
    af = site.get("autofocus") if site else None
    if af:
        notes.append(f"AF {_f(af.get('initial_voltage'), 0)}→{_f(af.get('best_voltage'), 0)} V"
                     if af.get("success") else "AF not converged")
    if run.get("kind") == "manual":
        notes.append("manual")
    tip = [STATUS_LABEL.get(st, st)]
    if run.get("_file"):
        tip.append(f"{run['_file']} · {_fmt_iso(site.get('started'))}")
    if e.get("after_path") is not None:
        tip.append(f"after image {e['after_path'].name}")
    tip += full
    pn = f'<span class="pn">{_esc(pat)}</span>'
    if e.get("pattern_path") is None and notes:          # nothing milled: say why, in place
        pn = f'<span class="pn sn1">{_esc(notes.pop(0))}</span>'
    return (f'<div class="stl" title="{_esc(" | ".join(tip))}">'
            f'<span class="badge sm s-{_esc(st)}"><i aria-hidden="true"></i>{_esc(e["label"])}</span>'
            f'{pn}<span class="rs">{_esc(_residual(site))}</span>'
            + (f'<span class="sn">{_esc("; ".join(notes))}</span>' if notes else "")
            + "</div>")


def _details(v: PositionView, session: Path) -> str:
    """v0.51.1 compact: one line for the stage position, one for FIB imaging, then the
    patterns Rough -> Prepolish -> Polish with their refine residuals."""
    rec = v.rec
    st = rec.stage_at
    t = f"{_f(st.t_deg, 2)}°" if st.t_deg is not None else "not recorded"
    rows: List[Tuple[str, str]] = [
        ("Stage", f"X {_f(st.x_m * 1e3, 4)} · Y {_f(st.y_m * 1e3, 4)} · Z {_f(st.z_m * 1e3, 4)} mm"
                  f" · T {t} · R {_f(st.r_deg, 1)}°")]
    # v0.51.2 (Evan): FIB = magnification + WD only; field, pixel size and objective
    # voltage on hover.
    tip = []
    if rec.fib_pixel_size_m and rec.image_width_px and rec.image_height_px:
        ps = float(rec.fib_pixel_size_m)
        tip += [f"field {_f(rec.image_width_px * ps * 1e6, 1)} × "
                f"{_f(rec.image_height_px * ps * 1e6, 1)} µm", f"{_f(ps * 1e9, 2)} nm/px"]
    tip.append(f"objective {_f(rec.fib_objective_potential_v, 1)} V")
    fib = [f'<span title="{_esc(" · ".join(tip))}">Mag {float(rec.fib_magnification_x):g}x</span>']
    if rec.fib_working_distance_m:
        fib.append(f"WD {_f(rec.fib_working_distance_m * 1e3, 3)} mm")
    if rec.fib_scan_rotation_deg is not None and abs(float(rec.fib_scan_rotation_deg)) > 0.05:
        fib.append(f"rotation {_f(rec.fib_scan_rotation_deg, 2)}°")
    rows.append(("FIB", " · ".join(fib)))
    rows.append(("", '<div class="stl sth"><span></span><span></span>'
                     '<span class="rs">Residual</span></div>'))      # v0.51.2 header
    rows.append(("Patterns", "".join(_stage_line(e) for e in _stage_entries(v))))
    if getattr(rec, "fiducial_side", None):
        rows.append(("Fiducial", _esc(v.fiducial_label)
                     + (" · milled" if v.fiducial_runs else "")))
    if rec.notes:
        rows.append(("Note", f'<span class="note">{_esc(rec.notes)}</span>'))
    out = ["<dl class='kv'>"]
    for k, val in rows:
        out.append(f"<dt>{_esc(k)}</dt><dd>{val if k not in ('Stage',) else _esc(val)}</dd>")
    out.append("</dl>")
    if v.warnings:
        out.append('<ul class="warn">' + "".join(f"<li>{_esc(w)}</li>" for w in v.warnings)
                   + "</ul>")
    return "".join(out)


def _marker(status: str, cx: float, cy: float) -> str:
    """Status marker: shape AND colour, so status never rests on colour alone."""
    if status == "milled":
        return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="var(--good)"/>'
    if status == "error":
        return (f'<path d="M{cx-5:.1f},{cy-5:.1f}L{cx+5:.1f},{cy+5:.1f}M{cx+5:.1f},'
                f'{cy-5:.1f}L{cx-5:.1f},{cy+5:.1f}" stroke="var(--crit)" stroke-width="2.5"/>')
    if status in ("stopped", "interrupted", "running"):
        return (f'<path d="M{cx:.1f},{cy-6:.1f}L{cx+6:.1f},{cy+5:.1f}L{cx-6:.1f},'
                f'{cy+5:.1f}Z" fill="var(--serious)"/>')
    if status == "assumed":
        return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--surface)" '
                f'stroke="var(--good)" stroke-width="2.5"/>')
    return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--surface)" '
            f'stroke="var(--neutral)" stroke-width="2"/>')


def _stage_rows(views: Dict[str, PositionView]) -> List[tuple]:
    """(name, x_mm, y_mm, z_mm, t_deg, r_deg) for map_export.apply_z_model."""
    out = []
    for v in views.values():
        st = v.rec.stage_at
        out.append((v.name, st.x_m * 1e3, st.y_m * 1e3,
                    None if st.z_m is None else st.z_m * 1e3, st.t_deg, st.r_deg))
    return out


def _stage_plot(views: Dict[str, PositionView]) -> str:
    from .map_export import apply_z_model
    zxy, zinfo = apply_z_model(_stage_rows(views))
    shifted = zinfo["shifted_um"] if zinfo["applied"] else {}
    rec_xy = {v.name: (v.rec.stage_at.x_m * 1e3, v.rec.stage_at.y_m * 1e3)
              for v in views.values()}
    zval = {v.name: v.rec.stage_at.z_m for v in views.values()}
    pts = [(v.name, *zxy[v.name], v.status) for v in views.values()]
    if not pts:
        return ""
    short = _short_names([p[0] for p in pts])
    xs = [p[1] for p in pts]; ys = [p[2] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 0.02)
    pad = 0.08 * span + 0.01
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    Wp, ml, mr, mt, mb = 900.0, 64.0, 20.0, 16.0, 40.0
    scale = (Wp - ml - mr) / (x1 - x0)
    Hp = mt + mb + (y1 - y0) * scale
    if Hp > 520:                                  # keep equal aspect; shrink width instead
        scale = (520 - mt - mb) / (y1 - y0)
        Hp = 520.0
        Wp = ml + mr + (x1 - x0) * scale

    from .map_export import STAGE_X_INCREASES_LEFT

    def X(x):                                      # stage X increases to the LEFT
        return ml + ((x1 - x) if STAGE_X_INCREASES_LEFT else (x - x0)) * scale

    from .map_export import STAGE_Y_INCREASES_DOWN

    def Y(y):                                      # stage Y increases DOWNWARD (v0.43.0)
        return mt + ((y - y0) if STAGE_Y_INCREASES_DOWN else (y1 - y)) * scale

    zref = f", at Z {zinfo['z_ref_mm']:.4f}" if shifted else ""
    ylab = (f"← Stage Y (mm{zref}) increases" if STAGE_Y_INCREASES_DOWN
            else f"Stage Y (mm{zref})")
    xlab = (f"← Stage X (mm{zref}) increases" if STAGE_X_INCREASES_LEFT
            else f"Stage X (mm{zref})")
    raw = (x1 - x0) / 6
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 5, 10)), key=lambda s: abs(s - raw))
    parts = [f'<svg class="chart" viewBox="0 0 {Wp:.0f} {Hp:.0f}" '
             f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Stage positions">']
    g = math.ceil(x0 / step) * step
    nd = max(0, -int(math.floor(math.log10(step))))
    while g <= x1:
        parts.append(f'<line class="gridl" x1="{X(g):.1f}" y1="{mt}" x2="{X(g):.1f}" '
                     f'y2="{Hp-mb:.1f}"/><g class="axis"><text x="{X(g):.1f}" '
                     f'y="{Hp-mb+16:.1f}" text-anchor="middle">{g:.{nd}f}</text></g>')
        g += step
    g = math.ceil(y0 / step) * step
    while g <= y1:
        parts.append(f'<line class="gridl" x1="{ml}" y1="{Y(g):.1f}" x2="{Wp-mr:.1f}" '
                     f'y2="{Y(g):.1f}"/><g class="axis"><text x="{ml-6}" y="{Y(g)+4:.1f}" '
                     f'text-anchor="end">{g:.{nd}f}</text></g>')
        g += step
    parts.append(f'<g class="axis"><text x="{(ml + Wp - mr)/2:.1f}" y="{Hp-6:.1f}" '
                 f'text-anchor="middle">'
                 f'{xlab}'
                 f'</text>'
                 f'<text transform="translate(14 {(mt + Hp - mb)/2:.1f}) rotate(-90)" '
                 f'text-anchor="middle">'
                 f'{ylab}</text></g>')
    for name, x, y, status in pts:
        cx, cy = X(x), Y(y)
        zt = "" if zval.get(name) is None else f", Z {zval[name] * 1e3:.4f} mm"
        if name in shifted:
            zt += f" (drawn at X {x:.4f}, Y {y:.4f} for its Z)"
        tip = (f"{name}: X {rec_xy[name][0]:.4f} mm, Y {rec_xy[name][1]:.4f} mm{zt}, "
               f"{STATUS_LABEL.get(status, status).lower()}")
        mark = _marker(status, cx, cy)
        parts.append(f'<a href="#pos-{_esc(name)}" class="pt"><title>{_esc(tip)}</title>'
                     f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="12" fill="transparent"/>{mark}'
                     f'<text class="lbl" x="{cx+8:.1f}" y="{cy-7:.1f}">{_esc(short[name])}'
                     f'</text></a>')
    parts.append("</svg>")
    groups = (("milled", ("milled",), "Milled"), ("error", ("error",), "Error"),
              ("stopped", ("stopped", "interrupted", "running"), "Stopped / interrupted"),
              ("assumed", ("assumed",), "Milled (assumed: no run log)"),
              ("not_milled", ("not_milled",), "Not milled"))
    legend = []
    for key, members, label in groups:
        if any(p[3] in members for p in pts):
            legend.append(f'<span class="key"><svg width="16" height="16" viewBox="0 0 16 16" '
                          f'aria-hidden="true">{_marker(key, 8, 8.5)}</svg>{label}</span>')
    return (f'<div class="card plot"><p class="note" style="margin-top:0">{"".join(legend)}</p>'
            f'{"".join(parts)}<p class="note">Stage coordinates at capture. Hover a point for '
            f'its coordinates; click to jump to it.</p></div>')


CELL_TEXT = {"error": "error", "stopped": "stopped", "interrupted": "stopped",
             "running": "stopped", "not_milled": "skipped", "assumed": "assumed"}


def _stage_cell(e: Optional[Dict[str, Any]]) -> str:
    """Summary cell: status mark + refine residual (milled) or a one-word state."""
    if e is None:
        return "<td class='dim'>—</td>"
    st = e["status"]
    txt = (_residual(e.get("site")) or "milled") if st == "milled" else CELL_TEXT.get(st, st)
    pat = e["pattern_path"].name if e.get("pattern_path") is not None else ""
    return (f"<td title='{_esc(STATUS_LABEL.get(st, st) + (' · ' + pat if pat else ''))}'>"
            f"<span class='cell s-{_esc(st)}'><i aria-hidden='true'></i>{_esc(txt)}</span></td>")


def _summary_table(views: Dict[str, PositionView]) -> str:
    """v0.51.1: one row per position; Rough / Prepolish / Polish columns show the status and
    refine residual (pattern on hover). Columns for stages never run are left out."""
    stages = ["rough"] + [s for s in ("prepolish", "polish")
                          if any(s in v.stages for v in views.values())]
    head = ("<tr><th>Position</th><th>Captured</th><th class='num'>X (mm)</th>"
            "<th class='num'>Y (mm)</th><th class='num'>Z (mm)</th><th class='num'>T (°)</th>"
            "<th class='num'>R (°)</th><th class='num'>Obj (V)</th><th>Fid.</th>"
            + "".join(f"<th>{_esc(VIEW_LABEL[s])}</th>" for s in stages) + "</tr>")
    rows = []
    dates = {_fmt_time(v.rec.captured_unix_s)[:10] for v in views.values()}
    same_day = len(dates) == 1
    if same_day:
        head = head.replace("<th>Captured</th>", f"<th>Captured ({_esc(next(iter(dates)))})</th>")
    for v in views.values():
        r, st = v.rec, v.rec.stage_at
        cap = _fmt_time(r.captured_unix_s)
        cap = cap[11:] if same_day else cap
        fid = ""
        if getattr(r, "fiducial_side", None):
            fid = (f"#{r.fiducial_number} " if getattr(r, "fiducial_number", None) else "") + str(r.fiducial_side)
        ents = {e["stage"]: e for e in _stage_entries(v)}
        rows.append(
            f"<tr><td><a href='#pos-{_esc(v.name)}'>{_esc(v.name)}</a></td>"
            f"<td>{_esc(cap)}</td>"
            f"<td class='num'>{_f(st.x_m * 1e3, 4)}</td><td class='num'>{_f(st.y_m * 1e3, 4)}</td>"
            f"<td class='num'>{_f(st.z_m * 1e3, 4)}</td>"
            f"<td class='num'>{_f(st.t_deg, 2) if st.t_deg is not None else '—'}</td>"
            f"<td class='num'>{_f(st.r_deg, 2)}</td>"
            f"<td class='num'>{_f(r.fib_objective_potential_v, 1)}</td><td>{_esc(fid)}</td>"
            + "".join(_stage_cell(ents.get(s)) for s in stages) + "</tr>")
    return f"<div class='card scroll'><table>{head}{''.join(rows)}</table></div>"


def _runs_table(runs: List[Dict[str, Any]]) -> str:
    if not runs:
        return ("<p class='note'>No run logs in this session's <code>runs</code> folder "
                "(pyFIB v0.40.0 or later writes one per armed batch).</p>")
    head = ("<tr><th>Started</th><th>Stage</th><th>Status</th><th class='num'>Milled</th>"
            "<th>File</th></tr>")
    rows = []
    for r in runs:
        sites = r.get("sites", [])
        n_ok = sum(1 for s in sites if s.get("status") == "milled")
        status = r.get("status")
        if status == "running":
            status = "ended without a final status (killed or crashed)"
        stage = ("fiducial" if r.get("kind") == "fiducial"
                 else VIEW_LABEL.get(r.get("stage") or "rough", r.get("stage")))
        if r.get("kind") == "manual":
            stage += " · manual"
        msg = r.get("message")
        rows.append(f"<tr><td>{_esc(_fmt_iso(r.get('started')))}</td><td>{_esc(stage)}</td>"
                    f"<td title='{_esc(msg or '')}'>{_esc(status)}"
                    f"{(' · ' + _esc(_short(msg, 60))) if msg else ''}</td>"
                    f"<td class='num'>{n_ok} / {len(r.get('positions_requested', []))}</td>"
                    f"<td title='{_esc(r.get('console_log') or '')}'>{_esc(r.get('_file'))}</td></tr>")
    return f"<div class='card scroll'><table class='tight'>{head}{''.join(rows)}</table></div>"


def _patterns_section(views: Dict[str, PositionView], std, std_note: str, cmap):
    """v0.51.1: every pattern this session used (Rough standard / authored, Prepolish,
    Polish), each with its name, where it was used, drawing and parts. (html, warnings)"""
    from .map_export import short_label
    from .pattern_diagram import pattern_card
    used: Dict[str, Dict[str, Any]] = {}

    def add(path, stage, source, name):
        if path is None:
            return
        k = str(Path(path).resolve())
        u = used.setdefault(k, {"path": Path(path), "stages": [], "sources": set(), "pos": []})
        if stage not in u["stages"]:
            u["stages"].append(stage)
        u["sources"].add(source)
        if name and name not in u["pos"]:
            u["pos"].append(name)
    for v in views.values():
        for e in _stage_entries(v):
            if e["status"] == "not_milled" and not e.get("site"):
                continue
            add(e.get("pattern_path"), e["stage"], e.get("source") or "stage", v.name)
    if std is not None:
        add(std, "rough", "standard", None)
    order = {"rough": 0, "prepolish": 1, "polish": 2}
    items = sorted(used.values(), key=lambda u: (min(order.get(s, 9) for s in u["stages"]),
                                                 "authored" in u["sources"], u["path"].name))
    cards, warns = [], []
    for u in items:
        stages = " + ".join(VIEW_LABEL.get(s, s) for s in u["stages"])
        src = " / ".join(sorted(u["sources"] - {"stage"}))
        pos = u["pos"]
        where = ("not used" if not pos else
                 f"{len(pos)} position{'s' if len(pos) != 1 else ''}: "
                 + ", ".join(short_label(n) for n in pos[:16]) + (" …" if len(pos) > 16 else ""))
        sub = " · ".join(x for x in (src + (f" ({std_note})" if (std is not None and Path(std).resolve()
                                                                  == u["path"].resolve() and std_note)
                                            else ""), where) if x)
        h, w = pattern_card(u["path"], cmap, title=f"{stages} · {u['path'].name}", subtitle=sub)
        cards.append(h)
        warns += w
    if not cards:
        return "", warns
    return f"<div class='pgrid'>{''.join(cards)}</div>", warns


def _mosaic_section(m, views) -> str:
    """The 800x mosaic with clickable position labels (v0.43.0)."""
    if m is None:
        return ""
    if not m.clean_jpeg:
        return ("<h2 id='mosaic'>800x mosaic</h2><div class='card'><ul class='warn'>"
                + "".join(f"<li>{_esc(w)}</li>" for w in m.warnings) + "</ul></div>")
    from .map_export import short_label
    uri = "data:image/jpeg;base64," + base64.b64encode(m.clean_jpeg).decode("ascii")
    fs = max(12.0, m.height / 22.0)
    parts = [f'<svg class="mosaic" viewBox="0 0 {m.width} {m.height}" '
             f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="800x mosaic">'
             f'<image x="0" y="0" width="{m.width}" height="{m.height}" href="{uri}"/>']
    for name, (cx, cy) in m.centres.items():
        st = STATUS_LABEL.get(views[name].status, "") if name in views else ""
        parts.append(f'<a href="#pos-{_esc(name)}"><title>{_esc(name)} · {_esc(st)}</title>'
                     f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{fs/3.2:.1f}" fill="#fff" '
                     f'stroke="#000" stroke-width="1.5"/>'
                     f'<text x="{cx + fs/2.4:.1f}" y="{cy - fs/3:.1f}" font-size="{fs:.1f}" '
                     f'fill="#fff" stroke="#000" stroke-width="3" paint-order="stroke" '
                     f'font-family="sans-serif">{_esc(short_label(name))}</text></a>')
    parts.append("</svg>")
    # v0.43.1 (Evan): no caption or warnings under the mosaic -- placement method, seam
    # figures and the Z warning are kept in <report>_mosaic_800x.json.
    return (f"<h2 id='mosaic'>800x mosaic</h2><div class='card'><figure>{''.join(parts)}"
            f"</figure></div>")


def _map_links(map_paths: Dict[str, Path]) -> str:
    if not map_paths:
        return ""
    links = " · ".join(f"<a href='{_esc(p.name)}' download>{k.upper()}</a>"
                       for k, p in map_paths.items())
    return f"<p class='note'><b>Positions map for pyCLEM:</b> {links}</p>"


def _render(*, session, pf, db, views, runs, std, unassigned, warnings, title,
            generated, version, std_note="", map_paths=None, cmap=None,
            mosaic_res=None) -> str:
    ents = [e for v in views.values() for e in _stage_entries(v)]

    def n(stage, *sts):
        return sum(1 for e in ents if e["stage"] == stage and e["status"] in sts)
    caps = [v.rec.captured_unix_s for v in views.values() if v.rec.captured_unix_s]
    ttl = title or f"Session report: {session.name}"
    tiles = [("Positions", len(views)), ("Rough milled", n("rough", "milled"))]
    if n("rough", "assumed"):
        tiles.append(("Assumed (no log)", n("rough", "assumed")))
    for s, lab in (("prepolish", "Prepolished"), ("polish", "Polished")):
        if any(e["stage"] == s for e in ents):
            tiles.append((lab, n(s, "milled", "assumed")))
    errs = sum(1 for e in ents if e["status"] == "error")
    stopped = sum(1 for e in ents if e["status"] in ("stopped", "interrupted", "running"))
    if errs:
        tiles.append(("Errors", errs))
    if stopped:
        tiles.append(("Stopped", stopped))
    if n("rough", "not_milled"):
        tiles.append(("Not milled", n("rough", "not_milled")))
    tiles.append(("Runs", len(runs)))
    any_after = any(v.after_uri for v in views.values())
    view_set = (["before"] + (["rough"] if any_after else [])
                + [s for s in ("prepolish", "polish")
                   if any((v.stages.get(s) or {}).get("uri") for v in views.values())])
    pattern_html, pw = _patterns_section(views, std, std_note, cmap)
    warnings = list(warnings) + pw
    any_inset = any(v.coarse_uri for v in views.values())
    notes = list(warnings)
    if unassigned:
        notes.append("Fiducial pattern files with no position recording them: "
                     + ", ".join(unassigned) + " (a position re-captured after its fiducial "
                     "was milled?).")
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_esc(ttl)}</title><style>{CSS}</style></head><body class='two-up'><main>",
        f"<h1>{_esc(ttl)}</h1>",
        f"<p class='sub'>Generated {_esc(generated)} by pyFIB {_esc(version)}</p>",
        "<div class='meta'>",
        f"<div title='{_esc(pf)}'><span>Session</span>{_esc(session.name)}</div>",
        f"<div><span>Microscope</span>{_esc(db.microscope_serial or 'not recorded')}</div>",
        f"<div><span>Captured</span>{_esc(_fmt_time(min(caps)) if caps else '')} to "
        f"{_esc(_fmt_time(max(caps))[11:] if caps else '')}</div>",
        "</div>",
        "<div class='tiles'>" + "".join(f"<div class='tile'><b>{n}</b><span>{_esc(k)}</span></div>"
                                         for k, n in tiles) + "</div>",
    ]
    if notes:
        parts.append("<div class='card'><b>Notes</b><ul class='warn'>"
                     + "".join(f"<li>{_esc(n)}</li>" for n in notes) + "</ul></div>")
    parts += [
        "<div class='toolbar'><label><input type='checkbox' id='ovtoggle' checked> "
        "Patterns</label>"
        + ("<label><input type='checkbox' id='insettoggle' checked> 800x inset</label>"
           if any_inset else "<input type='checkbox' id='insettoggle' hidden>")
        + "<label title='Positions two per row, details under the image'>"
          "<input type='checkbox' id='twoup' checked> 2 per row</label>"
        + ("<span class='tb-ba' role='group' aria-label='All images'>"
           + "".join(f"<button type='button' data-show='{w}'>All {VIEW_LABEL[w].lower()}"
                     f"</button>" for w in view_set) + "</span>"
           if len(view_set) > 1 else "")
        + "<a href='#summary'>Summary</a><a href='#layout'>Layout</a>"
        + ("<a href='#mosaic'>Mosaic</a>" if mosaic_res is not None and mosaic_res.clean_jpeg else "")
        + ("<a href='#pattern'>Patterns</a>" if pattern_html else "")
        + "<a href='#runs'>Runs</a><a href='#positions'>Positions</a></div>",
        "<h2 id='summary'>Summary</h2>", _summary_table(views),
        "<h2 id='layout'>Stage layout</h2>", _stage_plot(views), _map_links(map_paths or {}),
        _mosaic_section(mosaic_res, views),
        (f"<h2 id='pattern'>Patterns</h2>{pattern_html}" if pattern_html else ""),
        "<h2 id='runs'>Runs</h2>", _runs_table(runs),
        "<h2 id='positions'>Positions</h2>",
        "<div id='plist'>",
    ]
    for v in views.values():
        parts.append(f"<section class='pos' id='pos-{_esc(v.name)}'><header><h3>{_esc(v.name)}</h3>"
                     f"<span class='when'>captured "
                     f"{_esc(_fmt_time(v.rec.captured_unix_s))}</span></header>"
                     f"<div class='grid'><div>{_figure(v, cmap)}</div><div>{_details(v, session)}</div>"
                     f"</div></section>")
    parts.append(f"</div></main><script>{JS}</script></body></html>")
    return "".join(parts)
