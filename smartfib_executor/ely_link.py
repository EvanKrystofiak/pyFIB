"""Link authored elymaker patterns to pyFIB positions.

The round trip:
  1. Author from Positions -> build a handoff job from a position's v2 geometry and
     open elymaker preloaded (image + pixel size + FOV), Save -> <session>/patterns/
     <name>.ely, target_site = position name.
  2. elymaker writes the .ely + an atomic <name>.ely.ready.json marker (content hash +
     target_site), meaning "ready", NOT "mill".
  3. Refresh patterns (explicit) -> read the markers, match target_site to a position,
     verify the .ely hash, set the position's ely_pattern.
  4. Mill time -> warn if the .ely's current hash no longer matches the marker (the
     pattern changed since it was marked ready).

This is the integration seam (like gisdep_adapter): pyFIB core does not import elymaker
except through here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


def session_dir_for(positions_file: str) -> Path:
    return Path(positions_file).resolve().parent


def patterns_dir_for(positions_file: str) -> Path:
    return session_dir_for(positions_file) / "patterns"


def ely_out_path(positions_file: str, position_name: str) -> Path:
    """Where a position's authored .ely lives: <session>/patterns/<name>.ely."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in position_name)
    return patterns_dir_for(positions_file) / f"{safe}.ely"


def build_handoff_kwargs(rec, positions_file: str) -> dict:
    """open_handoff(...) kwargs from a v2 position record. Raises ValueError if the
    position lacks the geometry elymaker needs (image + pixel size)."""
    base = session_dir_for(positions_file)
    if not rec.image_bmp:
        raise ValueError(f"position '{rec.name}' has no reference image to author over.")
    if rec.fib_pixel_size_m is None:
        raise ValueError(
            f"position '{rec.name}' has no fib_pixel_size_m (v1 record?) -- re-capture "
            f"it so elymaker can place the pattern to scale.")
    img = Path(rec.image_bmp)
    if not img.is_absolute():
        img = base / img
    out = ely_out_path(positions_file, rec.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Preload the existing pattern for re-editing, if one is already linked.
    load = None
    if rec.ely_pattern:
        p = Path(rec.ely_pattern)
        if not p.is_absolute():
            p = base / p
        if p.exists():
            load = str(p)
    return dict(
        image_path=str(img),
        pixel_size_um=float(rec.fib_pixel_size_m) * 1e6,
        out_path=str(out),
        load_path=load,
        fov_width_um=(float(rec.fov_width_m) * 1e6 if rec.fov_width_m else None),
        site=rec.name,
    )


@dataclass
class MarkerLink:
    site: str
    ely_abs: str
    ely_rel: str
    hash_ok: bool
    marker_path: str


def scan_ready_markers(positions_file: str) -> List[MarkerLink]:
    """Read every ready-marker under <session>/patterns and return the links found.

    hash_ok reflects whether the .ely on disk still matches the marker's content hash
    (i.e. the pattern hasn't been edited since it was marked ready)."""
    from elymaker.handoff import read_ready_marker, ely_content_hash  # vendored
    base = session_dir_for(positions_file)
    pdir = patterns_dir_for(positions_file)
    out: List[MarkerLink] = []
    if not pdir.exists():
        return out
    for mp in sorted(pdir.glob("*.ely.ready.json")):
        try:
            m = read_ready_marker(str(mp))
        except Exception:
            continue
        ely_abs = m.get("ely")
        site = m.get("target_site")
        if not ely_abs or not site or not os.path.exists(ely_abs):
            continue
        try:
            hash_ok = ely_content_hash(ely_abs) == m.get("content_hash")
        except Exception:
            hash_ok = False
        try:
            rel = os.path.relpath(ely_abs, base)
        except Exception:
            rel = ely_abs
        out.append(MarkerLink(site=str(site), ely_abs=ely_abs, ely_rel=rel,
                              hash_ok=hash_ok, marker_path=str(mp)))
    return out


def link_patterns_to_positions(db, positions_file: str) -> Dict[str, object]:
    """Set ely_pattern on each position that has a matching ready-marker.

    Returns a summary: linked names, unmatched sites, and any stale (hash-mismatch)
    links (still linked, but flagged). The caller saves the db.
    """
    import dataclasses
    linked: List[str] = []
    stale: List[str] = []
    unmatched: List[str] = []
    for link in scan_ready_markers(positions_file):
        if link.site not in db.positions:
            unmatched.append(link.site)
            continue
        rec = db.positions[link.site]
        # PositionRecord may be frozen -> replace() to set ely_pattern.
        try:
            db.positions[link.site] = dataclasses.replace(rec, ely_pattern=link.ely_rel)
        except Exception:
            setattr(rec, "ely_pattern", link.ely_rel)
        linked.append(link.site)
        if not link.hash_ok:
            stale.append(link.site)
    return {"linked": linked, "stale": stale, "unmatched": unmatched}


def verify_ely_against_marker(ely_path: str) -> Optional[bool]:
    """At mill time: True if the .ely still matches its ready-marker's hash, False if it
    drifted, None if there's no marker to check against."""
    from elymaker.handoff import read_ready_marker, ely_content_hash, marker_path_for
    mp = marker_path_for(os.path.abspath(ely_path))
    if not os.path.exists(mp):
        return None
    try:
        m = read_ready_marker(mp)
        return ely_content_hash(os.path.abspath(ely_path)) == m.get("content_hash")
    except Exception:
        return None
