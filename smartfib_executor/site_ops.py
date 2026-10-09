"""Manual milling at one position (v0.51.0): Go to -> Align -> Push pattern -> Record.

The Rough Mill, Prepolish and Polish tabs each have these four buttons for the
selected position (CLI: ``site goto|align|push|after-image NAME --stage S``):

  goto         Zero the FIB beam shift, MoveStage to the recorded position (tilt included;
               no batch tilt limit -- a single move, confirmed in the GUI), then the
               recorded objective voltage and FIB magnification.
  align        Refine to this stage's reference (capture image / previous stage's latest
               after image). The beam-shift correction is LEFT ON for milling.
  push         Load + prepare this stage's pattern in SmartFIB (drop folder,
               CMD_SMARTFIB_LOAD_ELY, CMD_SMARTFIB_PREPARE_EXPOSURE). Milling is NOT
               started: the operator checks it and starts it in SmartFIB.
  after-image  One FIB frame at the capture magnification, named
               images/<name>_<n>-<stage>_<time>.bmp, recorded in positions.json as the
               stage's latest after image, and written to a run log (kind "manual") with
               the last align / push of this position and stage, so the report documents
               manual milling like a batch. The beam shift is left as it is (Go to zeroes
               it before the next move).

Every action first watches SmartSEM for a few seconds (10 s before anything that can
move the stage, 5 s otherwise) and refuses if ANY reading in that window indicates
milling (unreadable state counts as milling, as in the batch): the milling state can
read inactive for a moment between exposures / during probe changes, so one reading is
not enough. Nothing here starts milling.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Callable, Optional

from .mill_stages import (STAGE_LABEL, check_stage, record_after_image,
                          reference_for_stage, resolve_stage_pattern)

PUSH_WARN_DISTANCE_M = 150e-6      # push: warn if the stage is further than this from the site
MANUAL_STATE = "manual_state.json"  # <session>/runs/: last align / push per position + stage
GUARD_MOTION_S = 10.0               # not-milling window before goto / align that may move stage
GUARD_S = 5.0                       # ... before push / align (beam shift only) / after image
GUARD_POLL_S = 0.5


class SiteRefused(RuntimeError):
    pass


def _milling_guard(sem, what: str, window_s: float = GUARD_S, *, log=print,
                   clock=time.monotonic, sleep=time.sleep) -> None:
    """Refuse unless SmartSEM reads NOT milling for the whole window (read-only)."""
    from .batch import _is_milling_active
    window_s = max(0.0, float(window_s))
    if window_s > 0:
        log(f"[site] checking that SmartSEM is not milling ({window_s:g} s) ...")
    t0 = clock()
    while True:
        if _is_milling_active(sem):
            raise SiteRefused(f"{what} refused: SmartSEM indicates milling (or the milling "
                              f"state cannot be read). Nothing was changed.")
        if clock() - t0 >= window_s:
            return
        sleep(GUARD_POLL_S)


# ----------------------------------------------------------------- manual state file
def _state_path(session_dir) -> Path:
    return Path(session_dir) / "runs" / MANUAL_STATE


def _load_state(session_dir) -> dict:
    try:
        return json.loads(_state_path(session_dir).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_state(session_dir, state: dict, log) -> None:
    try:
        p = _state_path(session_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(p))
    except Exception as e:  # noqa: BLE001
        log(f"*** WARNING: could not note this step for the report ({e}); the instrument "
            f"action itself is unaffected. ***")


def _note(session_dir, name: str, stage: str, key: str, value: dict, log) -> None:
    st = _load_state(session_dir)
    st.setdefault(f"{name}|{stage}", {})[key] = dict(value, time=time.time())
    _save_state(session_dir, st, log)


# ----------------------------------------------------------------- actions
def site_goto(sem, rec, *, move_method: str = "movestage", set_mag: bool = True,
              stage_timeout_s: float = 120.0, log: Callable[[str], None] = print,
              guard_s: Optional[float] = None) -> None:
    from .batch import _zero_fib_beam_shift
    from .positions import recall_position
    _milling_guard(sem, f"Go to {rec.name}",
                   GUARD_MOTION_S if guard_s is None else guard_s, log=log)
    t_rec = rec.stage_at.t_deg
    try:
        t_now = float(sem.get_float("AP_STAGE_AT_T"))
    except Exception:  # noqa: BLE001
        t_now = float("nan")
    if t_rec is not None and math.isfinite(t_now):
        log(f"[goto] tilt {t_now:.2f} -> {float(t_rec):.2f} deg "
            f"(change {abs(float(t_rec) - t_now):.2f} deg)")
    log("[goto] zeroing FIB beam shift X/Y (no correction carried from the last site).")
    _zero_fib_beam_shift(sem, logger=log)
    if move_method != "none":
        log(f"[goto] moving the stage to {rec.name} ({move_method}).")
    recall_position(sem, rec, move_method=move_method, set_objective=False,
                    set_magnification=False, wait_stage_idle=True,
                    stage_timeout_s=float(stage_timeout_s))
    sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", float(rec.fib_objective_potential_v))
    log(f"[goto] objective {rec.fib_objective_potential_v:.1f} V")
    if set_mag:
        sem.set_value("AP_FIB_MAGNIFICATION", float(rec.fib_magnification_x))
        log(f"[goto] FIB magnification {rec.fib_magnification_x:g}x")


def site_align(sem, rec, stage: str, session_dir, cfg, *,
               log: Callable[[str], None] = print, _refine_fn=None,
               guard_s: Optional[float] = None):
    """Refine to the stage reference. Returns the RefineResult (beam shift left on)."""
    from .milling_workflow import run_refine_at_position
    from .report.runlog import refine_summary
    s = check_stage(stage)
    ref, label, why = reference_for_stage(rec, s, session_dir)
    if ref is None:
        raise SiteRefused(f"Align {rec.name} ({STAGE_LABEL[s]}): {why}. Nothing was changed.")
    if guard_s is None:
        guard_s = GUARD_MOTION_S if getattr(cfg, "enable_stage", False) else GUARD_S
    _milling_guard(sem, f"Align {rec.name}", guard_s, log=log)
    log(f"[align] {rec.name} ({STAGE_LABEL[s]}): aligning to the {label}")
    fn = _refine_fn or run_refine_at_position
    res = fn(sem, rec, cfg, ref_base_dir=Path(session_dir), dry_run=False, logger=log,
             reference_path=ref)
    summ = refine_summary(res) or {"success": False, "message": "refine did not run"}
    summ["reference"] = ref.name
    _note(session_dir, rec.name, s, "align", summ, log)
    return res


def site_push(sem, rec, stage: str, ely, session_dir, *, drop_dir, drop_name: str,
              load_delay_s: float = 1.0, prepare_delay_s: float = 1.0,
              log: Callable[[str], None] = print, _runner_cls=None,
              guard_s: Optional[float] = None) -> Path:
    """Load + prepare the stage pattern in SmartFIB. Milling is NOT started."""
    from .report.runlog import file_sha256
    from .runner import PatternJob, PatternRunner
    s = check_stage(stage)
    path, source, msg = resolve_stage_pattern(rec, s, ely, session_dir)
    if not Path(path).exists():
        raise SiteRefused(f"Push for {rec.name}: pattern not found: {path}")
    _milling_guard(sem, f"Push pattern for {rec.name}", GUARD_S if guard_s is None else guard_s,
                   log=log)
    log(f"[push] {rec.name} ({STAGE_LABEL[s]}): {msg}")
    try:
        x, y = float(sem.get_float("AP_STAGE_AT_X")), float(sem.get_float("AP_STAGE_AT_Y"))
        d = math.hypot(x - rec.stage_at.x_m, y - rec.stage_at.y_m)
        if d > PUSH_WARN_DISTANCE_M:
            log(f"*** WARNING: the stage is {d * 1e6:.0f} um from {rec.name}'s recorded "
                f"position -- use Go to first if this is not the right site. ***")
    except Exception:  # noqa: BLE001
        pass
    job = PatternJob(ely_local_path=Path(path), load_method="smartfib_drop",
                     smartfib_drop_dir=Path(drop_dir), smartfib_drop_name=str(drop_name),
                     smartfib_load_delay_s=float(load_delay_s),
                     smartfib_prepare_delay_s=float(prepare_delay_s),
                     arm=True, start_milling=False, wait_strategy="none")
    (_runner_cls or PatternRunner)(sem).run(job)
    _note(session_dir, rec.name, s, "push",
          {"path": str(Path(path)), "source": source, "message": msg,
           "sha256": file_sha256(path)}, log)
    return Path(path)


def site_after_image(sem, db, rec, stage: str, positions_file, *,
                     log: Callable[[str], None] = print, run_log: bool = True,
                     _grab_fn=None, guard_s: Optional[float] = None):
    """Grab + record this stage's after image. Returns (path or None, warning or None)."""
    from .after_image import grab_after_mill_image
    from .batch import _is_milling_active
    s = check_stage(stage)
    pf = Path(positions_file)
    session = pf.parent
    _milling_guard(sem, f"Record after image for {rec.name}",
                   GUARD_S if guard_s is None else guard_s, log=log)
    grab = _grab_fn or grab_after_mill_image
    path, warn = grab(sem, rec, session / "images",
                      milling_active=lambda: _is_milling_active(sem), log=log, stage=s)
    if path is not None:
        try:
            rel = record_after_image(pf, rec.name, s, path, db=db)
            log(f"[after-image] {rec.name}: {STAGE_LABEL[s]} after image {rel} "
                f"(recorded in positions.json)")
        except Exception as e:  # noqa: BLE001
            warn = ((warn + " ") if warn else "") + (
                f"{path.name} was saved but could not be recorded in positions.json ({e}); "
                f"the next stage still finds it in the images folder.")
        log("[after-image] beam shift left as it is; Go to zeroes it before the next move.")
    if warn:
        log(f"*** WARNING: {warn} ***")
    if run_log:
        _manual_run_log(session, pf, db, rec, s, path, warn, log)
    return path, warn


def _manual_run_log(session, pf, db, rec, stage, path, warn, log) -> None:
    """A one-site run log (kind 'manual') so the report shows this manual step."""
    try:
        from .report.runlog import RunLog
        st = _load_state(session)
        key = f"{rec.name}|{stage}"
        # A failed grab keeps the align/push notes for the retry.
        entry = st.pop(key, {}) if path is not None else dict(st.get(key, {}))
        push = entry.get("push") or {}
        rl = RunLog(session, kind="manual", stage=stage, positions_file=str(pf),
                    standard_pattern=push.get("path"), positions=[rec.name],
                    microscope_serial=getattr(db, "microscope_serial", None),
                    options={"action": "site after-image", "manual": True}, printer=log)
        rl.stage_check(stage, {})
        rl.site_start(rec.name, 1, 1, rec)
        if entry.get("align"):
            rl.site_set(refine=entry["align"])
        if push.get("path"):
            rl.site_pattern(push["path"], push.get("source", "stage"),
                            push.get("message", "") + " (pushed; milled by hand in SmartFIB)")
        rl.site_after_image(path, warn)
        if path is not None:
            rl.site_end("milled", "manual: milled in SmartFIB; after image recorded")
        else:
            rl.site_end("error", f"manual: after image not taken ({warn})")
        rl.finish("completed")
        _save_state(session, st, log)
    except Exception as e:  # noqa: BLE001
        log(f"*** WARNING: manual run log not written ({e}); the after image is unaffected. ***")
