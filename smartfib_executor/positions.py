from __future__ import annotations

import json
import math
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from .smartsem_com import SmartSEM, SmartSEMError
from . import fov as _fov


SCHEMA_ID = "smartfib_executor.positions.v2"
SCHEMA_ID_V1 = "smartfib_executor.positions.v1"
ACCEPTED_SCHEMAS = (SCHEMA_ID, SCHEMA_ID_V1)


@dataclass(frozen=True)
class StageAt:
    """Stage 'actual' coordinates as read from AP_STAGE_AT_*.

    Units:
      - x_m, y_m, z_m, m_m are metres (SmartSEM analogue parameters are SI units).
      - r_deg is degrees (consistent with MoveStage() signature).
      - t_deg is degrees (optional; included for completeness).

    Note: Some SmartSEM documentation lists all stage AT/GOTO values as metres; however
    the API MoveStage() explicitly uses degrees for tilt/rotation.
    """

    x_m: float
    y_m: float
    z_m: float
    r_deg: float
    m_m: float
    t_deg: Optional[float] = None


@dataclass(frozen=True)
class PositionRecord:
    """A single named position snapshot used for later recall."""

    name: str
    captured_unix_s: float
    fib_magnification_x: float
    fib_objective_potential_v: float
    stage_at: StageAt
    image_bmp: Optional[str] = None
    notes: Optional[str] = None
    # FIB working distance (metres), recorded for reference only. Focus is still
    # driven by fib_objective_potential_v; this is stored alongside it.
    fib_working_distance_m: Optional[float] = None

    # --- v2 (elymaker integration) ---
    # Geometry: authoritative, computed by pyFIB so elymaker needs no calibration.
    image_width_px: Optional[int] = None      # actual grabbed BMP width  (px)
    image_height_px: Optional[int] = None     # actual grabbed BMP height (px)
    fov_width_m: Optional[float] = None        # field-of-view width  (metres)
    fov_height_m: Optional[float] = None       # field-of-view height (metres)
    fib_pixel_size_m: Optional[float] = None   # fov_width_m / image_width_px (metres/px)
    # Capture conditions (milling verifies it reproduces these; all expected ~0).
    fib_scan_rotation_deg: Optional[float] = None
    fib_beam_shift_x_m: Optional[float] = None
    fib_beam_shift_y_m: Optional[float] = None
    # Per-position pattern linkage (filled when a pattern is authored; else None).
    ely_pattern: Optional[str] = None
    # Logging/cross-check ONLY -- tied to the store/display grid; must NOT drive the
    # pixel->um transform (see the v0.9.1 store-vs-grab pixel-size lesson).
    ap_fib_pixel_size_m: Optional[float] = None
    # Fiducial: which side of the image ("left" | "right") a fiducial was milled on
    # at this position, or None. Recorded after a successful fiducial mill; drift
    # correction uses it to know where to look.
    fiducial_side: Optional[str] = None
    # Which NUMBERED fiducial (1-30) was milled on that side, or None (square/none).
    fiducial_number: Optional[int] = None
    # Coarse (lower-mag) reference for the two-stage refine: a second frame grabbed at
    # coarse_magnification_x right after the fine one. None if not captured.
    image_bmp_coarse: Optional[str] = None
    coarse_magnification_x: Optional[float] = None
    # v0.51.0 milling stages (mill_stages.py): the LATEST after-mill image of each stage,
    # {"rough"|"prepolish"|"polish": "images/<name>_<n>-<stage>_<time>.bmp"} relative to
    # the positions file. The next stage refines to it. Older files: None. Not hashed.
    stage_after_images: Optional[Dict[str, str]] = field(default=None, hash=False)


@dataclass
class PositionDatabase:
    """JSON-serializable container for multiple position records."""

    schema: str = SCHEMA_ID
    created_unix_s: float = field(default_factory=lambda: time.time())
    microscope_serial: Optional[str] = None
    positions: Dict[str, PositionRecord] = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> "PositionDatabase":
        data = json.loads(path.read_text(encoding="utf-8"))
        found = data.get("schema")
        if found not in ACCEPTED_SCHEMAS:
            raise ValueError(
                f"Unexpected schema: {found!r} (expected one of {ACCEPTED_SCHEMAS})")

        # Upgrade to the current schema in memory so save() writes v2. v1 files load
        # fine; their v2-only fields simply come through as None.
        db = PositionDatabase(
            schema=SCHEMA_ID,
            created_unix_s=float(data.get("created_unix_s", time.time())),
            microscope_serial=data.get("microscope_serial"),
        )

        def _f(rec, key):
            v = rec.get(key)
            return float(v) if v is not None else None

        def _i(rec, key):
            v = rec.get(key)
            return int(v) if v is not None else None

        def _stage_images(v):
            if not isinstance(v, dict):
                return None
            out = {str(k): str(p) for k, p in v.items() if p}
            return out or None

        pos_dict = data.get("positions", {})
        for name, rec in pos_dict.items():
            st = rec["stage_at"]
            stage_at = StageAt(
                x_m=float(st["x_m"]),
                y_m=float(st["y_m"]),
                z_m=float(st["z_m"]),
                r_deg=float(st["r_deg"]),
                m_m=float(st["m_m"]),
                t_deg=float(st["t_deg"]) if st.get("t_deg") is not None else None,
            )
            db.positions[name] = PositionRecord(
                name=str(rec["name"]),
                captured_unix_s=float(rec["captured_unix_s"]),
                fib_magnification_x=float(rec["fib_magnification_x"]),
                fib_objective_potential_v=float(rec["fib_objective_potential_v"]),
                stage_at=stage_at,
                image_bmp=rec.get("image_bmp"),
                notes=rec.get("notes"),
                fib_working_distance_m=_f(rec, "fib_working_distance_m"),
                # v2 fields (None on v1 files)
                image_width_px=_i(rec, "image_width_px"),
                image_height_px=_i(rec, "image_height_px"),
                fov_width_m=_f(rec, "fov_width_m"),
                fov_height_m=_f(rec, "fov_height_m"),
                fib_pixel_size_m=_f(rec, "fib_pixel_size_m"),
                fib_scan_rotation_deg=_f(rec, "fib_scan_rotation_deg"),
                fib_beam_shift_x_m=_f(rec, "fib_beam_shift_x_m"),
                fib_beam_shift_y_m=_f(rec, "fib_beam_shift_y_m"),
                ely_pattern=rec.get("ely_pattern"),
                ap_fib_pixel_size_m=_f(rec, "ap_fib_pixel_size_m"),
                fiducial_side=rec.get("fiducial_side"),
                fiducial_number=_i(rec, "fiducial_number"),
                image_bmp_coarse=rec.get("image_bmp_coarse"),
                coarse_magnification_x=_f(rec, "coarse_magnification_x"),
                stage_after_images=_stage_images(rec.get("stage_after_images")),
            )

        return db

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Convert dataclasses to plain dicts
        positions_out: Dict[str, Any] = {}
        for name, rec in self.positions.items():
            d = asdict(rec)
            positions_out[name] = d

        out = {
            "schema": self.schema,
            "created_unix_s": self.created_unix_s,
            "microscope_serial": self.microscope_serial,
            "positions": positions_out,
        }
        # v0.51.0: atomic (temp file + replace). Batches now write the file after every
        # staged site; a stop or crash mid-write must never truncate the session.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))

    def upsert(self, record: PositionRecord) -> None:
        self.positions[record.name] = record

    def get(self, name: str) -> PositionRecord:
        if name not in self.positions:
            raise KeyError(f"No such position: {name}")
        return self.positions[name]


# -------------------------
# Acquisition (record)
# -------------------------

def _recover_grab_file(sem, img_target: Path) -> Optional[str]:
    """If Grab() saved into SmartSEM's default folder (SV_IMAGE_PATH) instead of the
    requested path, move/copy it to img_target. Returns a warning string or None."""
    if img_target.exists():
        return None
    try:
        sv_image_path = sem.get_str("SV_IMAGE_PATH").strip()
    except Exception:
        sv_image_path = ""
    if not sv_image_path:
        return None
    candidate = Path(sv_image_path) / img_target.name
    if not candidate.exists():
        return None
    try:
        shutil.move(str(candidate), str(img_target))
    except Exception:
        try:
            shutil.copy2(str(candidate), str(img_target))
        except Exception as e:
            return (f"WARNING: Grab saved to {candidate} but could not be copied to "
                    f"{img_target}: {e}")
    return None


def capture_position(
    sem: SmartSEM,
    name: str,
    *,
    fib_magnification_x: float = 2.000e3,
    image_bmp: Optional[Path] = None,
    grab_xoff: int = 0,
    grab_yoff: int = 0,
    grab_width: int = 1024,
    grab_height: int = 768,
    grab_reduction: int = 0,
    settle_s: float = 0.25,
    ensure_fib_image: bool = True,
    fib_image_timeout_s: float = 15.0,
    include_tilt: bool = True,             # default ON: recall restores T, refine's tilt model
    fiducial_side: Optional[str] = None,   # "left" | "right" if a fiducial was milled first
    fiducial_number: Optional[int] = None, # numbered fiducial (1-30) milled on that side
    unfreeze_before_grab: bool = True,
    freeze_after_grab: bool = True,
    frame_wait_s: float = 3.0,        # frame-safe grab: minimum live scan before freezing
    frozen_timeout_s: float = 30.0,   # frame-safe grab: max wait for DP_FROZEN
    coarse_magnification_x: Optional[float] = None,   # e.g. 800: also grab a coarse ref
    coarse_scan_rate: Optional[int] = 5,              # scan rate for the coarse frame
) -> PositionRecord:
    """Set FIB magnification, grab a BMP, and record stage + objective potential.

    This is designed for building a lookup table of objective potential vs. site.

    Parameters
    ----------
    sem:
        Connected SmartSEM instance.
    name:
        Name like "position_1".
    fib_magnification_x:
        Value written to AP_FIB_MAGNIFICATION.
    image_bmp:
        Where to save the grabbed BMP. If None, no grab is performed.
    settle_s:
        Small delay after setting magnification before reading/Grabbing.
    include_tilt:
        If True, also read AP_STAGE_AT_T and store it as t_deg.

    Returns
    -------
    PositionRecord
    """

    # Optional warnings collected during capture (e.g. Freeze failures, file relocation).
    notes: Optional[str] = None

    # 0) Ensure we are grabbing a FIB image (not SEM)
    #
    # The SmartSEM Remote API exposes explicit FIB imaging mode commands:
    #   CMD_FIB_MODE_FIB  -> FIB imaging
    # and the digital state DP_FIB_IMAGING indicates which imaging mode is active.
    # (DP_FIB_IMAGING: 0=SEM, 1=FIB, 2=Milling, 3=SEM+FIB, ...)
    #
    # We do this first so that the subsequent Grab() captures the FIB image.
    if ensure_fib_image:
        # IMPORTANT:
        # - DP_FIB_IMAGING is frequently *not settable* via Set() ("failed to set digital state").
        # - On some installations, requesting VT_R4 for a DP_ can yield 0 even when the
        #   state *string* is "FIB".
        #
        # The most reliable sequence is:
        #   Execute("CMD_FIB_MODE_FIB")  -> request FIB imaging
        #   then Grab()                 -> save current FIB frame to a BMP
        #
        # Here we:
        #   1) execute CMD_FIB_MODE_FIB if we're not already in "FIB"
        #   2) wait for DP_FIB_IMAGING to report "FIB" (string-first), with a numeric fallback.

        def _fib_imaging_str() -> Optional[str]:
            try:
                return sem.get_str("DP_FIB_IMAGING").strip()
            except Exception:
                return None

        cur_s = _fib_imaging_str()
        if cur_s is None or cur_s.upper() != "FIB":
            sem.execute("CMD_FIB_MODE_FIB")

        t0 = time.time()
        last_s: Optional[str] = cur_s
        last_num: Optional[float] = None

        while True:
            last_s = _fib_imaging_str()
            if last_s is not None and last_s.upper() == "FIB":
                break

            # Numeric fallback (best-effort)
            try:
                last_num = float(sem.get_float("DP_FIB_IMAGING"))
                if int(last_num) == 1:
                    break
            except Exception:
                pass

            if time.time() - t0 > float(fib_image_timeout_s):
                raise SmartSEMError(
                    "Timeout waiting for DP_FIB_IMAGING=FIB after CMD_FIB_MODE_FIB "
                    f"(last_str={last_s!r}, last_num={last_num!r})."
                )
            time.sleep(0.25)

    # 1) Set FIB magnification
    sem.set_value("AP_FIB_MAGNIFICATION", float(fib_magnification_x))

    if settle_s > 0:
        time.sleep(float(settle_s))

    # 2) Grab image (optional)
    image_rel: Optional[str] = None
    if image_bmp is not None:
        img_target = Path(image_bmp)
        img_target.parent.mkdir(parents=True, exist_ok=True)

        # FRAME-SAFE GRAB. Grab() saves the frame store as it is at that instant; right
        # after an unfreeze the store still holds the LAST FROZEN frame, so the old
        # "unfreeze, Grab() immediately" saved the previous position's image whenever
        # the scan was frozen going in (every capture ends frozen; refine/autofocus
        # leave it frozen). Now: freeze-on End Frame -> unfreeze -> scan >= one frame ->
        # freeze (completes the frame) -> wait DP_FROZEN -> Grab() -- the sequence the
        # refine/autofocus adapters use on the instrument.
        #   unfreeze_before_grab=False keeps the old explicit meaning: grab the frame
        #   currently on screen, as-is (e.g. a frame the operator froze by hand).
        from .frame_grab import grab_fresh_frame
        grab_kw = dict(xoff=grab_xoff, yoff=grab_yoff, width=grab_width,
                       height=grab_height, reduction=grab_reduction)
        try:
            if unfreeze_before_grab:
                w = grab_fresh_frame(sem, str(img_target), frame_wait_s=frame_wait_s,
                                     frozen_timeout_s=frozen_timeout_s, log=print,
                                     **grab_kw)
                if w:
                    notes = (notes + "\n" + w) if notes else w
            else:
                sem.grab_bmp(str(img_target), **grab_kw)
        except Exception:
            # Best-effort: stop scanning even if the grab fails, to avoid continued dose.
            try:
                sem.execute("CMD_FREEZE_ZONE")
            except Exception:
                try:
                    sem.execute("CMD_FREEZE_ALL")
                except Exception:
                    pass
            raise
        image_rel = str(img_target)
        # The frame-safe grab leaves the scan FROZEN (no further dose). Honour the
        # options: freeze_after_grab=False -> resume live scanning; the as-is path
        # (unfreeze_before_grab=False) freezes afterwards as before.
        if not freeze_after_grab:
            try:
                sem.execute("CMD_UNFREEZE_ZONE")
            except Exception:
                pass
        elif not unfreeze_before_grab:
            try:
                sem.execute("CMD_FREEZE_ZONE")
            except Exception as e:
                try:
                    sem.execute("CMD_FREEZE_ALL")
                except Exception:
                    pass
                notes = f"WARNING: Freeze failed after grab (CMD_FREEZE_ZONE): {e}"

        # Some SmartSEM installs ignore the directory of the Grab() filename and save
        # into SV_IMAGE_PATH; recover the file from there if needed.
        w2 = _recover_grab_file(sem, img_target)
        if w2:
            notes = (notes + "\n" if notes else "") + w2

    # 2b) COARSE reference (optional, two-stage refine). Taken AFTER the fine frame so
    #     the fine capture above is unchanged; the FIB is returned to the fine mag (and
    #     the original scan rate) afterwards. A coarse failure does not lose the fine
    #     capture: it is recorded as a WARNING and the coarse fields stay None.
    image_coarse_rel: Optional[str] = None
    coarse_mag_rec: Optional[float] = None
    if image_bmp is not None and coarse_magnification_x:
        from .frame_grab import grab_fresh_frame, read_scan_rate, set_scan_rate
        coarse_target = Path(image_bmp).with_name(Path(image_bmp).stem + "_coarse.bmp")
        prev_rate = read_scan_rate(sem) if coarse_scan_rate is not None else None
        try:
            sem.set_value("AP_FIB_MAGNIFICATION", float(coarse_magnification_x))
            if settle_s > 0:
                time.sleep(float(settle_s))
            if coarse_scan_rate is not None:
                set_scan_rate(sem, int(coarse_scan_rate))
            w = grab_fresh_frame(sem, str(coarse_target), frame_wait_s=frame_wait_s,
                                 frozen_timeout_s=frozen_timeout_s, log=print,
                                 xoff=grab_xoff, yoff=grab_yoff, width=grab_width,
                                 height=grab_height, reduction=grab_reduction)
            _recover_grab_file(sem, coarse_target)
            if not coarse_target.exists():
                raise RuntimeError(f"coarse grab produced no file at {coarse_target}")
            image_coarse_rel = str(coarse_target)
            coarse_mag_rec = float(coarse_magnification_x)
            if w:
                notes = (notes + "\n" if notes else "") + "[coarse] " + w
        except Exception as e:  # noqa: BLE001
            msg = f"WARNING: coarse reference at {coarse_magnification_x:g}x FAILED: {e}"
            print(msg)
            notes = (notes + "\n" if notes else "") + msg
        finally:
            if prev_rate is not None:
                set_scan_rate(sem, int(prev_rate))
            try:
                sem.set_value("AP_FIB_MAGNIFICATION", float(fib_magnification_x))
            except Exception:
                pass

    # 3) Read stage position and FIB objective potential
    x = sem.get_float("AP_STAGE_AT_X")
    y = sem.get_float("AP_STAGE_AT_Y")
    z = sem.get_float("AP_STAGE_AT_Z")
    r = sem.get_float("AP_STAGE_AT_R")
    m = sem.get_float("AP_STAGE_AT_M")

    t: Optional[float] = None
    if include_tilt:
        try:
            t = sem.get_float("AP_STAGE_AT_T")
        except Exception:
            t = None

    obj_v = sem.get_float("AP_FIB_OBJECTIVE_POTENTIAL")

    # FIB working distance -- recorded for reference only (focus stays on the
    # objective potential above). Best-effort: this mnemonic is not confirmed on
    # every instrument, so a failure just records None rather than aborting capture.
    try:
        fib_wd = float(sem.get_float("AP_FIB_WORKING_DISTANCE"))
    except Exception:
        fib_wd = None

    # --- v2 geometry + capture conditions (elymaker integration) ---------------
    # Defensive throughout: any failed readback records None (+ a note), never
    # aborts the capture.
    def _try_float(mnemonic):
        try:
            return float(sem.get_float(mnemonic))
        except Exception:
            return None

    v2_notes = []
    # Image dimensions: the geometry actually grabbed. Only meaningful if we grabbed.
    if image_rel is not None:
        image_width_px = int(grab_width)
        image_height_px = int(grab_height)
    else:
        image_width_px = None
        image_height_px = None

    # FOV width from magnification (shared source of truth).
    try:
        fov_w = _fov.fov_width_m(float(fib_magnification_x))
    except Exception:
        fov_w = None
        v2_notes.append("fov_width_m could not be computed")

    # FOV height: prefer AP_HEIGHT if it reads sensibly, else square-pixel derivation.
    ap_height = _try_float("AP_HEIGHT")
    if ap_height is not None and ap_height > 0:
        fov_h = ap_height
    elif fov_w is not None and image_width_px and image_height_px:
        fov_h = fov_w * (image_height_px / image_width_px)  # square-pixel assumption
    else:
        fov_h = None

    # Authoritative sample-plane pixel size (self-consistent with the grabbed width).
    if fov_w is not None and image_width_px:
        fib_px = fov_w / image_width_px
    else:
        fib_px = None

    # Capture conditions (milling verifies it reproduces these; all expected ~0).
    scan_rot = _try_float("AP_FIB_SCAN_ROTATION")
    bshift_x = _try_float("AP_FIB_BEAM_SHIFT_X")
    bshift_y = _try_float("AP_FIB_BEAM_SHIFT_Y")
    for label, val in (("AP_FIB_SCAN_ROTATION", scan_rot),
                       ("AP_FIB_BEAM_SHIFT_X", bshift_x),
                       ("AP_FIB_BEAM_SHIFT_Y", bshift_y)):
        if val is None:
            v2_notes.append(f"{label} unavailable")

    # Logging/cross-check ONLY -- store-grid-tied; must not drive the transform.
    ap_fib_px = _try_float("AP_FIB_PIXEL_SIZE")

    if v2_notes:
        note_str = "v2: " + "; ".join(v2_notes)
        notes = (notes + "\n" + note_str) if notes else note_str

    return PositionRecord(
        name=name,
        captured_unix_s=time.time(),
        fib_magnification_x=float(fib_magnification_x),
        fib_objective_potential_v=float(obj_v),
        stage_at=StageAt(x_m=float(x), y_m=float(y), z_m=float(z), r_deg=float(r), m_m=float(m), t_deg=t),
        image_bmp=image_rel,
        notes=notes,
        fib_working_distance_m=fib_wd,
        # v2 fields
        image_width_px=image_width_px,
        image_height_px=image_height_px,
        fov_width_m=fov_w,
        fov_height_m=fov_h,
        fib_pixel_size_m=fib_px,
        fib_scan_rotation_deg=scan_rot,
        fib_beam_shift_x_m=bshift_x,
        fib_beam_shift_y_m=bshift_y,
        ely_pattern=None,
        ap_fib_pixel_size_m=ap_fib_px,
        fiducial_side=(str(fiducial_side) if fiducial_side else None),
        fiducial_number=(int(fiducial_number) if fiducial_number is not None else None),
        image_bmp_coarse=image_coarse_rel,
        coarse_magnification_x=coarse_mag_rec,
    )


# -------------------------
# Recall
# -------------------------


def recall_position(
    sem: SmartSEM,
    record: PositionRecord,
    *,
    move_method: Literal["ap_goto", "movestage", "none"] = "movestage",
    set_objective: bool = True,
    set_magnification: bool = False,
    wait_stage_idle: bool = True,
    stage_timeout_s: float = 120.0,
) -> None:
    """Recall a previously captured position.

    - Optionally set AP_FIB_OBJECTIVE_POTENTIAL to recorded value.
    - Optionally set AP_FIB_MAGNIFICATION.
    - Optionally move the stage.

    Stage movement options:
    - movestage (DEFAULT since v0.38.0): call the API MoveStage(x,y,z,t,r,m) directly.
              Tilt: the recorded tilt if the position has one, otherwise the CURRENT
              tilt read live (so tilt does not change); refuses if neither is known.
    - ap_goto: set AP_STAGE_GOTO_* targets (X/Y/Z and best-effort R/M/T if present).
              INSTRUMENT FINDING 2026-10-01: a recall 14 um away in X did not move the
              stage at all with ap_goto (still at 43.505 mm, target 43.5186 mm), and the
              0.3 mm arrival check let it pass. MoveStage moved it correctly.
    - none: do not move stage.

    Note: Whether setting AP_STAGE_GOTO_* immediately initiates motion depends on
    microscope configuration. MoveStage() is generally the most explicit option.
    """

    if set_magnification:
        sem.set_value("AP_FIB_MAGNIFICATION", float(record.fib_magnification_x))

    if set_objective:
        sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", float(record.fib_objective_potential_v))

    if move_method == "none":
        return

    st = record.stage_at

    if move_method == "ap_goto":
        # Required axes
        sem.set_value("AP_STAGE_GOTO_X", float(st.x_m))
        sem.set_value("AP_STAGE_GOTO_Y", float(st.y_m))
        sem.set_value("AP_STAGE_GOTO_Z", float(st.z_m))

        # Best-effort extras. A FAILED set here is NOT silent any more: it is
        # reported loudly, because milling a cryo lamella at the wrong rotation /
        # tilt with no warning is exactly the failure this used to hide. The axis is
        # then excluded from the arrival check (it was never commanded), so the
        # recall does not time out purely because of a known-unset optional axis.
        commanded = {"r_deg": None, "m_m": None, "t_deg": None}
        for key, mnemonic, val in (
            ("r_deg", "AP_STAGE_GOTO_R", st.r_deg),
            ("m_m", "AP_STAGE_GOTO_M", st.m_m),
            ("t_deg", "AP_STAGE_GOTO_T", st.t_deg),
        ):
            if val is None:
                continue
            try:
                sem.set_value(mnemonic, float(val))
                commanded[key] = float(val)
            except Exception as e:
                print(f"*** WARNING: recall '{record.name}': could not set {mnemonic} = "
                      f"{val} ({e}). This axis is NOT being moved -- the stage will "
                      f"stay at its current {mnemonic.split('_')[-1]}; verify the "
                      f"orientation before milling. ***")

        if wait_stage_idle:
            # Wait for ARRIVAL, not merely Idle: DP_STAGE_IS reads Idle before the
            # stage starts moving, so an idle-only wait returned mid-travel.
            sem.wait_stage_arrival(
                x_m=float(st.x_m), y_m=float(st.y_m), z_m=float(st.z_m),
                t_deg=commanded["t_deg"], r_deg=commanded["r_deg"],
                m_m=commanded["m_m"], timeout_s=stage_timeout_s)
        return

    if move_method == "movestage":
        # MoveStage needs a tilt. Recorded tilt if the position has one; otherwise HOLD
        # the current tilt, read live. If it cannot be read, REFUSE: the old fallback of
        # 0.0 would have swung the stage from the milling tilt to 0 deg during a recall.
        t = st.t_deg
        if t is None:
            try:
                t = float(sem.get_float("AP_STAGE_AT_T"))
            except Exception as e:
                raise SmartSEMError(
                    f"recall '{record.name}': the position has no recorded tilt and the "
                    f"current tilt (AP_STAGE_AT_T) could not be read ({e!r}); refusing to "
                    f"move rather than guess a tilt.")
            if not math.isfinite(t):
                raise SmartSEMError(
                    f"recall '{record.name}': current tilt reads {t!r}; refusing to move.")

        sem.move_stage(float(st.x_m), float(st.y_m), float(st.z_m), float(t), float(st.r_deg), float(st.m_m))
        if wait_stage_idle:
            # Wait for ARRIVAL (idle + read-back at target), not merely Idle.
            sem.wait_stage_arrival(
                x_m=float(st.x_m), y_m=float(st.y_m), z_m=float(st.z_m),
                t_deg=float(t), r_deg=float(st.r_deg), m_m=float(st.m_m),
                timeout_s=stage_timeout_s)
        return

    raise ValueError(f"Unknown move_method: {move_method}")
