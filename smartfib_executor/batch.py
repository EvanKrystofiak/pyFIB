from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable, List, Literal, Optional

from .positions import PositionDatabase, PositionRecord, recall_position
from .runner import MILLING_IMAGING_CODES, PatternJob, PatternRunner
from .smartsem_com import SmartSEM, SmartSEMError
from .milling_workflow import (
    AutofocusConfig, RefineConfig,
    run_autofocus_at_position, run_refine_at_position,
)


def natural_position_sort(names: Iterable[str]) -> List[str]:
    """Sort position names like position_1, position_2, ... in numeric order.

    Falls back to plain lexical order if a trailing integer can't be parsed.
    """

    def key(n: str):
        m = re.match(r"^(.*?)(\d+)$", n)
        if m:
            prefix, num = m.group(1), m.group(2)
            return (prefix, int(num))
        return (n, -1)

    return sorted(list(names), key=key)


def _safe_get_float_int(sem: SmartSEM, mnemonic: str) -> Optional[int]:
    try:
        return int(sem.get_float(mnemonic))
    except Exception:
        return None


def _safe_get_float(sem: SmartSEM, mnemonic: str) -> Optional[float]:
    try:
        return float(sem.get_float(mnemonic))
    except Exception:
        return None


# Scan rotation is expected to match capture (refine does not change it). Beyond this
# tolerance the pattern could land rotated, so we WARN (Phase D: warn, never refuse).
MILL_SCAN_ROT_TOL_DEG = 0.5


def _resolve_position_ely(rec, default_ely: Path, ref_base_dir=None):
    """Pick the .ely to mill for a position: the AUTHORED pattern (rec.ely_pattern) if
    it exists, otherwise the standard default. Returns (path, source, message) where
    source is 'authored' | 'standard' | 'authored_missing'."""
    authored = getattr(rec, "ely_pattern", None)
    if authored:
        ap = Path(authored)
        if not ap.is_absolute() and ref_base_dir:
            ap = Path(ref_base_dir) / ap
        if ap.exists():
            return ap, "authored", f"using authored pattern {authored}"
        return (Path(default_ely), "authored_missing",
                f"*** WARNING: authored pattern {authored} is missing -- falling back "
                f"to the standard pattern. ***")
    return Path(default_ely), "standard", "using the standard pattern"


def _report_ely_pattern(idx: int, total: int, rec, ref_base_dir=None) -> None:
    """If this position has a linked elymaker .ely, WARN when the file no longer
    matches its ready-marker (edited since it was marked ready). Warn only."""
    ely = getattr(rec, "ely_pattern", None)
    if not ely:
        return
    from pathlib import Path as _P
    p = _P(ely)
    if not p.is_absolute() and ref_base_dir:
        p = _P(ref_base_dir) / ely
    try:
        from .ely_link import verify_ely_against_marker
        ok = verify_ely_against_marker(str(p))
    except Exception:
        ok = None
    if ok is False:
        print(f"[{idx}/{total}] *** WARNING: the .ely for {rec.name} ({ely}) has "
              f"CHANGED since it was marked ready -- you may be milling a different "
              f"pattern than was authored. ***")
    elif ok is True:
        print(f"[{idx}/{total}] pattern {ely}: matches its ready-marker.")


def _report_mill_conditions(idx: int, total: int, rec, sem: SmartSEM) -> None:
    """List the current FIB scan rotation + beam shift vs the recorded capture values.

    Informational only -- milling proceeds regardless (warn, never refuse). Scan
    rotation should match capture; a CHANGED beam shift is EXPECTED when refine ran
    (refine intentionally shifts the beam to land the pattern on the feature), so the
    beam-shift lines are reported without alarming.
    """
    cur_rot = _safe_get_float(sem, "AP_FIB_SCAN_ROTATION")
    cur_bx = _safe_get_float(sem, "AP_FIB_BEAM_SHIFT_X")
    cur_by = _safe_get_float(sem, "AP_FIB_BEAM_SHIFT_Y")

    def _fmt(v, unit, scale=1.0):
        return "n/a" if v is None else f"{v * scale:+.4f} {unit}"

    def _delta(cur, rec_v, unit, scale=1.0):
        if rec_v is None:
            return "(no recorded value)"
        if cur is None:
            return "(current unavailable)"
        return f"delta {(cur - rec_v) * scale:+.4f} {unit}"

    print(f"[{idx}/{total}] Mill-time conditions for {rec.name} "
          f"(warn only; milling proceeds):")
    print(f"    FIB scan rotation : now {_fmt(cur_rot, 'deg')}  |  "
          f"capture {_fmt(getattr(rec, 'fib_scan_rotation_deg', None), 'deg')}  |  "
          f"{_delta(cur_rot, getattr(rec, 'fib_scan_rotation_deg', None), 'deg')}")
    print(f"    FIB beam shift X  : now {_fmt(cur_bx, 'um', 1e6)}  |  "
          f"capture {_fmt(getattr(rec, 'fib_beam_shift_x_m', None), 'um', 1e6)}  |  "
          f"{_delta(cur_bx, getattr(rec, 'fib_beam_shift_x_m', None), 'um', 1e6)}")
    print(f"    FIB beam shift Y  : now {_fmt(cur_by, 'um', 1e6)}  |  "
          f"capture {_fmt(getattr(rec, 'fib_beam_shift_y_m', None), 'um', 1e6)}  |  "
          f"{_delta(cur_by, getattr(rec, 'fib_beam_shift_y_m', None), 'um', 1e6)}")

    rec_rot = getattr(rec, "fib_scan_rotation_deg", None)
    if cur_rot is not None and rec_rot is not None \
            and abs(cur_rot - rec_rot) > MILL_SCAN_ROT_TOL_DEG:
        print(f"    *** WARNING: scan rotation differs from capture by "
              f"{abs(cur_rot - rec_rot):.3f} deg (> {MILL_SCAN_ROT_TOL_DEG} deg). "
              f"The pattern may land rotated relative to the feature. ***")
    elif cur_rot is not None and rec_rot is None \
            and abs(cur_rot) > MILL_SCAN_ROT_TOL_DEG:
        print(f"    *** WARNING: scan rotation is {cur_rot:.3f} deg (no recorded "
              f"capture value to compare). ***")


def _safe_get_str(sem: SmartSEM, mnemonic: str) -> Optional[str]:
    try:
        s = str(sem.get_str(mnemonic)).strip()
        return s if s else None
    except Exception:
        return None


def _zero_fib_beam_shift(sem: SmartSEM, *, logger=print, verify: bool = True,
                         tol_m: float = 1e-9) -> bool:
    """Set FIB beam shift X and Y to 0. Best-effort; never raises.

    Returns True if both axes are confirmed ~0. A leftover beam shift would
    otherwise become the baseline for the NEXT site's refine (a cumulative offset)
    and skew the reduced-raster/park geometry, so this is called at batch start and
    after every position.
    """
    log = logger or (lambda m: None)
    ok = True
    for mn in ("AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"):
        try:
            sem.set_value(mn, 0.0)
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"[beam-shift] WARNING: could not zero {mn}: {e}")
    if verify:
        for mn in ("AP_FIB_BEAM_SHIFT_X", "AP_FIB_BEAM_SHIFT_Y"):
            try:
                v = float(sem.get_float(mn))
                if abs(v) > tol_m:
                    ok = False
                    log(f"[beam-shift] WARNING: {mn} reads {v:.3e} m after reset "
                        f"(expected 0). Check the column before the next site.")
            except Exception:
                pass
    return ok


def _is_milling_active(sem: SmartSEM) -> bool:
    """Conservative safety check for whether SmartSEM still indicates active milling.

    We use multiple DP_ signals and deliberately treat *unknown/unreadable* states
    as "active" to avoid moving the stage mid-pattern.

    Returns True when *any* of the following indicates activity:
      - DP_FIB_MODE in (2=Milling, 5=To Milling)
      - DP_FIB_IMAGING indicates Milling / Mill+SEM (string contains 'MILL' or code in MILLING_IMAGING_CODES)
      - DP_PATTERNING_MODE is not Off (0) (if available)

    Returns False only when we can read enough signals to be confident milling is not active.
    """

    mode = _safe_get_float_int(sem, 'DP_FIB_MODE')
    img_str = _safe_get_str(sem, 'DP_FIB_IMAGING')
    img_code = _safe_get_float_int(sem, 'DP_FIB_IMAGING')
    pat_code = _safe_get_float_int(sem, 'DP_PATTERNING_MODE')

    # Positive evidence of activity
    if mode in (2, 5):
        return True

    if img_str is not None and 'MILL' in img_str.upper():
        return True

    if img_code in MILLING_IMAGING_CODES:
        return True

    if pat_code is not None and pat_code != 0:
        return True

    # Conservative: if we cannot read key states reliably, do not move.
    if mode is None:
        return True

    if img_str is None and img_code is None:
        return True

    return False


def _wait_not_milling_stable(
    sem: SmartSEM,
    *,
    stable_s: float = 5.0,
    timeout_s: float = 600.0,
    poll_s: float = 0.5,
) -> None:
    """Wait until SmartSEM indicates milling is inactive for stable_s seconds."""

    t0 = time.time()
    stable_t0: Optional[float] = None

    while True:
        active = _is_milling_active(sem)

        if not active:
            if stable_t0 is None:
                stable_t0 = time.time()
            elif (time.time() - stable_t0) >= float(stable_s):
                return
        else:
            stable_t0 = None

        if timeout_s > 0 and (time.time() - t0) > float(timeout_s):
            raise SmartSEMError(
                f"Timeout waiting for milling to become inactive before stage move (needed stable {stable_s:.0f}s)."
            )

        time.sleep(float(poll_s))




def mill_positions_sequential(
    sem: SmartSEM,
    *,
    db: PositionDatabase,
    position_names: Iterable[str],
    ely_local_path: Path,
    move_method: Literal["ap_goto", "movestage", "none"] = "ap_goto",
    wait_stage_idle: bool = True,
    stage_timeout_s: float = 120.0,
    skip_objective: bool = False,
    set_mag: bool = False,
    settle_s: float = 0.25,
    load_method: Literal["smartfib_drop", "none"] = "smartfib_drop",
    smartfib_drop_dir: Path = Path(r"C:\\ProgramData\\Carl Zeiss\\SmartFIB\\API\\Drop"),
    smartfib_drop_name: str = "ApiLayout.ely",
    smartfib_load_delay_s: float = 1.0,
    smartfib_prepare_delay_s: float = 1.0,
    set_patterning_mode_fib: bool = True,
    ensure_fib_mode_mill: bool = False,
    wait_strategy: Literal["none", "mode_change", "blanked", "auto", "sleep"] = "mode_change",
    wait_timeout_s: float = 5400.0,
    done_confirm_s: float = 30.0,
    sleep_s: float = 0.0,
    continue_on_error: bool = False,
    # --- per-position autofocus / refine integration ---
    autofocus_names: Optional[Iterable[str]] = None,
    refine_names: Optional[Iterable[str]] = None,
    autofocus_config: Optional[AutofocusConfig] = None,
    refine_config: Optional[RefineConfig] = None,
    ref_base_dir: Optional[Path] = None,
    dry_run_focus_refine: bool = False,
    reset_beam_shift_between_positions: bool = True,
    # Injection hooks (tests replace these to check ordering without hardware).
    _autofocus_fn=run_autofocus_at_position,
    _refine_fn=run_refine_at_position,
) -> None:
    """Recall each named position and mill the same .ely pattern at each site.

    This function assumes:
    - The stage movement and objective potential recall are sufficient to relocate to each site.
    - The milling layout is defined by the .ely file (loaded via SmartFIB drop or already loaded).

    Parameters match the CLI for 'mill-positions'.
    """

    runner = PatternRunner(sem)

    af_set = {str(n) for n in (autofocus_names or [])}
    rf_set = {str(n) for n in (refine_names or [])}
    af_cfg = autofocus_config or AutofocusConfig()
    rf_cfg = refine_config or RefineConfig()

    names = list(position_names)
    total = len(names)
    if total == 0:
        print("No positions to mill.")
        return

    # Start from a known-zero FIB beam shift so the FIRST site's refine is not
    # baselined on a leftover shift (which may already be high before we begin).
    if reset_beam_shift_between_positions and not dry_run_focus_refine:
        print("[beam-shift] zeroing FIB beam shift X/Y before the first site.")
        _zero_fib_beam_shift(sem, logger=print)

    for idx, name in enumerate(names, start=1):
        rec: PositionRecord = db.get(name)
        print("\n" + "=" * 72)
        print(f"[{idx}/{total}] Milling at {rec.name}")
        print(
            f"Stage target: X={rec.stage_at.x_m:.6e} m, Y={rec.stage_at.y_m:.6e} m, Z={rec.stage_at.z_m:.6e} m, "
            f"R={rec.stage_at.r_deg:.6f} deg, M={rec.stage_at.m_m:.6e} m"
        )
        print(f"Objective potential: {rec.fib_objective_potential_v:.6f} V")
        print("=" * 72)

        try:
            # Safety interlock: never move the stage if SmartSEM indicates milling is still active.
            #
            # This protects against false-positive "milling finished" detections that could
            # otherwise trigger a stage move mid-pattern (high risk for sample motion / damage).
            # If SmartSEM still indicates milling, wait here rather than moving the stage.
            # This prevents mid-pattern stage motion if the milling-complete detection was wrong.
            if _is_milling_active(sem):
                print(
                    "[safety] SmartSEM still indicates milling activity; waiting for it to become inactive before stage move..."
                )
                _wait_not_milling_stable(
                    sem,
                    stable_s=10.0,
                    timeout_s=float(wait_timeout_s) if float(wait_timeout_s) > 0 else 0.0,
                    poll_s=0.5,
                )

            # 1) Move stage first (if requested)
            if move_method != "none":
                recall_position(
                    sem,
                    rec,
                    move_method=move_method,
                    set_objective=False,  # apply objective after stage motion
                    set_magnification=bool(set_mag),
                    wait_stage_idle=bool(wait_stage_idle),
                    stage_timeout_s=float(stage_timeout_s),
                )

            # 2) Apply objective potential at the site
            if not skip_objective:
                sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", float(rec.fib_objective_potential_v))

            if settle_s and float(settle_s) > 0:
                time.sleep(float(settle_s))

            # 2a) Autofocus (optional, per position). Uses the saved objective
            #     voltage as the expected focus and sweeps above/below; leaves the
            #     column at best focus. Runs BEFORE refine so refine images in focus.
            if name in af_set:
                print(f"[{idx}/{total}] Autofocus at {rec.name}")
                _autofocus_fn(sem, rec, af_cfg, dry_run=bool(dry_run_focus_refine),
                              logger=print)
                print(f"[{idx}/{total}] Autofocus done at {rec.name}")

            # 2b) Refine (optional, per position). Cross-correlates the live image
            #     against the reference BMP and nulls the residual offset with beam
            #     shift; the correction stays applied so milling lands on target.
            if name in rf_set:
                print(f"[{idx}/{total}] Refine at {rec.name}")
                _refine_fn(sem, rec, rf_cfg, ref_base_dir=ref_base_dir,
                           dry_run=bool(dry_run_focus_refine), logger=print)
                print(f"[{idx}/{total}] Refine done at {rec.name}")

            # 3) Run milling. DEFAULT: the standard pattern (ely_local_path). OVERRIDE:
            #    if this position has an authored pattern (rec.ely_pattern) and the file
            #    exists, mill THAT instead.
            per_pos_ely, _src, _msg = _resolve_position_ely(
                rec, Path(ely_local_path), ref_base_dir)
            print(f"[{idx}/{total}] {rec.name}: {_msg}")

            job = PatternJob(
                ely_local_path=per_pos_ely,
                load_method=load_method,
                smartfib_drop_dir=Path(smartfib_drop_dir),
                smartfib_drop_name=str(smartfib_drop_name),
                smartfib_load_delay_s=float(smartfib_load_delay_s),
                smartfib_prepare_delay_s=float(smartfib_prepare_delay_s),
                set_patterning_mode_fib=bool(set_patterning_mode_fib),
                ensure_fib_mode_mill=bool(ensure_fib_mode_mill),
                arm=True,
                wait_strategy=wait_strategy,
                wait_timeout_s=float(wait_timeout_s),
                done_confirm_s=float(done_confirm_s),
                sleep_s=float(sleep_s),
            )

            # Phase D: report FIB scan rotation + beam shift vs the recorded capture
            # conditions (warn only -- milling proceeds regardless).
            _report_mill_conditions(idx, total, rec, sem)
            _report_ely_pattern(idx, total, rec, ref_base_dir)

            runner.run(job)
            print(f"[{idx}/{total}] Completed milling at {rec.name}")

        except KeyboardInterrupt:
            # PatternRunner already attempts abort milling on Ctrl+C; just re-raise
            raise

        except Exception as e:
            print(f"Error at {rec.name}: {e}")
            if not continue_on_error:
                raise
            print("Continuing to next position due to --continue-on-error...")

        finally:
            # Return FIB beam shift to zero after milling completes (or after an
            # error) so any refine correction applied for THIS site does not carry
            # into the next one. Runs on success, handled/again-raised errors, and
            # Ctrl+C, leaving the column ready for the next site.
            if reset_beam_shift_between_positions and not dry_run_focus_refine:
                if _zero_fib_beam_shift(sem, logger=print):
                    print(f"[{idx}/{total}] FIB beam shift X/Y reset to 0 "
                          f"after {rec.name}.")
