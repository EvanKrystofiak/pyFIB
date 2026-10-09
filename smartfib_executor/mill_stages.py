"""Milling stages (v0.51.0): Rough Mill -> Prepolish -> Polish.

Evan, 2026-10-07: rough mill every lamella, go back and prepolish them, then polish
them, documenting each step. Each stage can run as a batch (automatic) or site by
site (manual: Go to -> Align -> Push pattern -> mill in SmartFIB -> Record after image).

What each stage uses
--------------------
  stage       pattern                                   refine reference
  rough       the standard .ely, or the position's      the capture image (position_N.bmp)
              authored pattern if it has one
  prepolish   the prepolish .ely for EVERY position     the latest Rough Mill after image
              (authored rough patterns are never used)
  polish      the polish .ely for EVERY position        the latest Prepolish after image

The after image of a stage is grabbed at the capture magnification in the view the
pattern was milled in (before the beam shift is reset), so its centre is the pattern
centre; refining the next stage to it puts the next pattern on the same lamella.

After-image names (one file per grab; re-runs keep the history):

    images/<position>_1-rough_<YYYYmmdd_HHMMSS>.bmp
    images/<position>_2-prepolish_<YYYYmmdd_HHMMSS>.bmp
    images/<position>_3-polish_<YYYYmmdd_HHMMSS>.bmp

positions.json records the LATEST one per stage (PositionRecord.stage_after_images,
paths relative to the session folder). The capture images (position_N.bmp,
position_N_coarse.bmp) are unchanged. Rough after images made before v0.51.0
(<position>_after_<time>.bmp) still count as Rough Mill after images.

Nothing in this module talks to the microscope.
"""

from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

STAGES = ("rough", "prepolish", "polish")
STAGE_NUMBER = {"rough": 1, "prepolish": 2, "polish": 3}
STAGE_LABEL = {"rough": "Rough Mill", "prepolish": "Prepolish", "polish": "Polish"}
PREVIOUS_STAGE = {"rough": None, "prepolish": "rough", "polish": "prepolish"}
# Stages that mill ONE pattern at every position (never the authored rough pattern).
FORCED_PATTERN_STAGES = ("prepolish", "polish")

_PATTERNS_DIR = Path(__file__).resolve().parent / "assets" / "patterns"
# Default stage patterns: Evan will supply these. Until the files exist the tabs start
# empty and the pattern must be chosen.
DEFAULT_STAGE_PATTERNS = {
    "prepolish": _PATTERNS_DIR / "prepolish.ely",
    "polish": _PATTERNS_DIR / "polish.ely",
}

_STAMP_RE = r"\d{8}_\d{6}"
AFTER_RE = re.compile(
    r"^(?P<name>.+)_(?P<num>[123])-(?P<stage>rough|prepolish|polish)_(?P<stamp>" + _STAMP_RE
    + r")\.bmp$")


class StageReferenceError(RuntimeError):
    """A staged batch was asked to mill a position whose previous-stage after image is
    missing, and skipping it was not OKed. Raised before ANY motion."""


def check_stage(stage: Optional[str]) -> Optional[str]:
    if stage is None:
        return None
    s = str(stage).strip().lower()
    if s not in STAGES:
        raise ValueError(f"unknown milling stage {stage!r} (expected one of {STAGES})")
    return s


def stage_tag(stage: str) -> str:
    """'prepolish' -> '2-prepolish'."""
    s = check_stage(stage)
    return f"{STAGE_NUMBER[s]}-{s}"


def safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))


def default_stage_pattern(stage: str) -> Optional[Path]:
    p = DEFAULT_STAGE_PATTERNS.get(check_stage(stage))
    return p if (p is not None and p.exists()) else None


def after_image_path(images_dir, name: str, stage: str, t: Optional[float] = None) -> Path:
    """images/<name>_<n>-<stage>_<YYYYmmdd_HHMMSS>.bmp (never overwrites: a clash gets _2)."""
    stamp = datetime.fromtimestamp(time.time() if t is None else t).strftime("%Y%m%d_%H%M%S")
    base = Path(images_dir) / f"{safe_name(name)}_{stage_tag(stage)}_{stamp}.bmp"
    p, k = base, 2
    while p.exists():
        p = base.with_name(f"{base.stem}_{k}.bmp")
        k += 1
    return p


def stamp_of(path) -> Optional[str]:
    """'YYYY-mm-dd HH:MM:SS' from an after-image name, or None."""
    m = re.search(r"_(\d{8})_(\d{6})(?:_\d+)?\.bmp$", Path(path).name)
    if not m:
        return None
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"


def stage_image_history(images_dir, name: str, stage: str) -> List[Path]:
    """Every after image of this position and stage on disk, oldest first. For rough this
    includes the pre-v0.51.0 <name>_after_<time>.bmp files."""
    d = Path(images_dir)
    if not d.is_dir():
        return []
    s = check_stage(stage)
    safe = safe_name(name)
    pats = [re.compile(re.escape(safe) + "_" + re.escape(stage_tag(s)) + "_(" + _STAMP_RE
                       + r")(?:_\d+)?\.bmp$")]
    if s == "rough":
        pats.append(re.compile(re.escape(safe) + "_after_(" + _STAMP_RE + r")\.bmp$"))
    found = []
    for p in d.glob(f"{safe}_*.bmp"):
        for rx in pats:
            m = rx.fullmatch(p.name)
            if m:
                k = re.search(r"_\d{8}_\d{6}_(\d+)\.bmp$", p.name)
                found.append((m.group(1), int(k.group(1)) if k else 1, p))
                break
    found.sort(key=lambda t: (t[0], t[1]))
    return [p for _s, _k, p in found]


def _stamp_unix(path) -> Optional[float]:
    m = re.search(r"_(\d{8}_\d{6})(?:_\d+)?\.bmp$", Path(path).name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").timestamp()
    except Exception:  # noqa: BLE001
        return None


def _resolve(base_dir, p) -> Optional[Path]:
    if not p:
        return None
    q = Path(str(p).replace("\\", "/"))
    if not q.is_absolute() and base_dir is not None:
        q = Path(base_dir) / q
    return q


def latest_after_image(rec, stage: str, base_dir) -> Optional[Path]:
    """The latest after image of `stage` for this position: the one positions.json records
    (if the file is there), else the newest matching file in <session>/images."""
    s = check_stage(stage)
    recorded = (getattr(rec, "stage_after_images", None) or {}).get(s)
    p = _resolve(base_dir, recorded)
    if p is not None and p.exists():
        return p
    hist = stage_image_history(Path(base_dir) / "images" if base_dir is not None else "images",
                               rec.name, s)
    # Files older than the position's capture belong to whatever had this name before a
    # re-capture (possibly another site): never use them (v0.51.0 review).
    cap = float(getattr(rec, "captured_unix_s", 0.0) or 0.0)
    hist = [h for h in hist if (_stamp_unix(h) or 0.0) >= cap - 2.0]
    return hist[-1] if hist else None


def reference_for_stage(rec, stage: str, base_dir) -> Tuple[Optional[Path], str, Optional[str]]:
    """(reference image, description, reason-it-is-missing).

    rough -> the capture image; prepolish -> latest Rough Mill after image;
    polish -> latest Prepolish after image."""
    s = check_stage(stage)
    prev = PREVIOUS_STAGE[s]
    if prev is None:
        p = _resolve(base_dir, getattr(rec, "image_bmp", None))
        if p is None:
            return None, "capture image", "no capture image recorded"
        if not p.exists():
            return None, "capture image", f"capture image missing ({p.name})"
        return p, "capture image", None
    p = latest_after_image(rec, prev, base_dir)
    label = f"{STAGE_LABEL[prev]} after image"
    if p is None:
        return None, label, f"no {label}"
    return p, f"{label} {p.name}", None


@dataclass
class StagePlan:
    stage: str
    references: Dict[str, Path] = field(default_factory=dict)
    missing: Dict[str, str] = field(default_factory=dict)     # name -> reason

    @property
    def allowed(self) -> List[str]:
        return list(self.references)

    def lines(self) -> List[str]:
        out = []
        for n, p in self.references.items():
            out.append(f"[stage] {n}: refine reference = {p.name}")
        for n, why in self.missing.items():
            out.append(f"[stage] {n}: will NOT be milled ({why})")
        return out


def plan_stage(db, names: Iterable[str], stage: str, base_dir) -> StagePlan:
    """Which positions have the reference this stage needs. Order kept."""
    s = check_stage(stage)
    plan = StagePlan(stage=s)
    for n in names:
        ref, _label, why = reference_for_stage(db.get(n), s, base_dir)
        if ref is None:
            plan.missing[str(n)] = why or "no reference"
        else:
            plan.references[str(n)] = ref
    return plan


def _rel(path, base_dir) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(Path(base_dir).resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return p.as_posix()


def with_after_image(rec, stage: str, path, base_dir):
    """Copy of `rec` with the stage's latest after image set (relative path)."""
    s = check_stage(stage)
    m = dict(getattr(rec, "stage_after_images", None) or {})
    m[s] = _rel(path, base_dir)
    return dataclasses.replace(rec, stage_after_images=m)


def record_after_image(positions_file, name: str, stage: str, path, *, db=None) -> str:
    """Record `path` as the latest `stage` after image of `name` in positions.json.

    Re-reads the file and changes only that one field, so nothing else written to the
    file meanwhile is lost; also updates `db` (the caller's in-memory copy) if given.
    Returns the relative path stored."""
    from .positions import PositionDatabase
    pf = Path(positions_file)
    disk = PositionDatabase.load(pf)
    new = with_after_image(disk.get(name), stage, path, pf.parent)
    disk.positions[name] = new
    disk.save(pf)
    if db is not None and name in getattr(db, "positions", {}):
        db.positions[name] = with_after_image(db.positions[name], stage, path, pf.parent)
    return new.stage_after_images[check_stage(stage)]


SAME_SITE_M = 50e-6     # re-capture within this (X, Y and Z) = the same site


def carry_over(old_rec, new_rec):
    """Re-capturing the SAME site keeps its stage after images (still images of that
    lamella). If the name was re-used for a different place (> 50 um away in X, Y or Z)
    they are dropped, so the next stage can never align to another site's image.
    Returns new_rec with them, or new_rec unchanged."""
    m = getattr(old_rec, "stage_after_images", None) if old_rec is not None else None
    if not m:
        return new_rec
    a, b = old_rec.stage_at, new_rec.stage_at
    if max(abs(a.x_m - b.x_m), abs(a.y_m - b.y_m), abs(a.z_m - b.z_m)) > SAME_SITE_M:
        return new_rec
    return dataclasses.replace(new_rec, stage_after_images=dict(m))


def resolve_stage_pattern(rec, stage: Optional[str], given_ely, base_dir=None):
    """(path, source, message) for the .ely to mill / push at this position.

    prepolish / polish: always the given stage pattern (authored rough patterns are
    bypassed). rough / no stage: the authored pattern if it exists, else the given one."""
    s = check_stage(stage)
    if s in FORCED_PATTERN_STAGES:
        return (Path(given_ely), "stage",
                f"using the {STAGE_LABEL[s]} pattern {Path(given_ely).name} "
                f"(authored rough patterns are not used in {STAGE_LABEL[s]})")
    from .batch import _resolve_position_ely
    return _resolve_position_ely(rec, Path(given_ely), base_dir, force_pattern=False)
