from __future__ import annotations

import json
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
        path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")

    def upsert(self, record: PositionRecord) -> None:
        self.positions[record.name] = record

    def get(self, name: str) -> PositionRecord:
        if name not in self.positions:
            raise KeyError(f"No such position: {name}")
        return self.positions[name]


# -------------------------
# Acquisition (record)
# -------------------------

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
    include_tilt: bool = False,
    unfreeze_before_grab: bool = True,
    freeze_after_grab: bool = True,
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

        # If a previous operation (or a prior pyFIB capture) froze the scan,
        # explicitly unfreeze so we capture a *fresh* frame at this position.
        if unfreeze_before_grab:
            try:
                sem.execute("CMD_UNFREEZE_ZONE")
            except Exception:
                pass

        try:
            sem.grab_bmp(
                str(img_target),
                xoff=grab_xoff,
                yoff=grab_yoff,
                width=grab_width,
                height=grab_height,
                reduction=grab_reduction,
            )
        except Exception:
            # Best-effort: stop scanning even if the grab fails, to avoid continued dose.
            if freeze_after_grab:
                try:
                    sem.execute("CMD_FREEZE_ZONE")
                except Exception:
                    try:
                        sem.execute("CMD_FREEZE_ALL")
                    except Exception:
                        pass
            raise
        image_rel = str(img_target)
        # After a successful grab, freeze scanning to avoid continued exposure/dose.
        # (SmartSEM provides Freeze/Unfreeze scanning commands.)
        if freeze_after_grab:
            try:
                sem.execute("CMD_FREEZE_ZONE")
            except Exception as e:
                # Best-effort fallback: try freezing all zones.
                try:
                    sem.execute("CMD_FREEZE_ALL")
                except Exception:
                    pass

                # Do not fail the capture if freezing is not permitted/valid;
                # instead record a note so the operator can review it later.
                notes = f"WARNING: Freeze failed after grab (CMD_FREEZE_ZONE): {e}"

        # Some SmartSEM installs appear to ignore directory components of the Grab()
        # filename and instead save into SmartSEM's default image folder (SV_IMAGE_PATH).
        # If the file did not appear where we requested, look for it there and
        # copy/move it into the desired destination.
        if not img_target.exists():
            try:
                sv_image_path = sem.get_str("SV_IMAGE_PATH").strip()
            except Exception:
                sv_image_path = ""

            if sv_image_path:
                candidate = Path(sv_image_path) / img_target.name
                if candidate.exists():
                    try:
                        shutil.move(str(candidate), str(img_target))
                    except Exception:
                        # If we cannot delete from the source folder (permissions), copy instead.
                        try:
                            shutil.copy2(str(candidate), str(img_target))
                        except Exception as e:
                            notes = (notes + "\n" if notes else "") + (
                                f"WARNING: Grab saved to {candidate} but could not be copied to {img_target}: {e}"
                            )

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
    )


# -------------------------
# Recall
# -------------------------


def recall_position(
    sem: SmartSEM,
    record: PositionRecord,
    *,
    move_method: Literal["ap_goto", "movestage", "none"] = "ap_goto",
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
    - ap_goto: set AP_STAGE_GOTO_* targets (X/Y/Z and best-effort R/M/T if present)
              and then optionally wait for DP_STAGE_IS to become idle.
    - movestage: call the API MoveStage(x,y,z,t,r,m) directly.
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

        # Best-effort extras
        for mnemonic, val in (
            ("AP_STAGE_GOTO_R", st.r_deg),
            ("AP_STAGE_GOTO_M", st.m_m),
            ("AP_STAGE_GOTO_T", st.t_deg),
        ):
            if val is None:
                continue
            try:
                sem.set_value(mnemonic, float(val))
            except Exception:
                # Some stages/versions may not support the axis or privilege may block it.
                pass

        if wait_stage_idle:
            sem.wait_stage_idle(timeout_s=stage_timeout_s)
        return

    if move_method == "movestage":
        # MoveStage requires tilt; if not in record, use current tilt (best-effort)
        t = st.t_deg
        if t is None:
            try:
                t = sem.get_float("AP_STAGE_AT_T")
            except Exception:
                t = 0.0

        sem.move_stage(float(st.x_m), float(st.y_m), float(st.z_m), float(t), float(st.r_deg), float(st.m_m))
        if wait_stage_idle:
            sem.wait_stage_idle(timeout_s=stage_timeout_s)
        return

    raise ValueError(f"Unknown move_method: {move_method}")
