from __future__ import annotations

import argparse
import time
import sys
from pathlib import Path
from typing import Optional

from .batch import mill_positions_sequential, natural_position_sort
from .milling_workflow import AutofocusConfig, RefineConfig
from .pattern_ely import ELayoutPattern
from .positions import PositionDatabase, capture_position, recall_position
from .runner import PatternJob, PatternRunner
from .smartsem_com import SmartSEM


def _project_root() -> Path:
    """Return the pyFIB project root directory.

    This is used as the base directory for resolving relative file paths so that
    running pyFIB from unexpected working directories (e.g. SmartSEM install folder)
    does not redirect outputs into Program Files.
    """
    # smartfib_executor/cli.py -> smartfib_executor -> project root
    return Path(__file__).resolve().parent.parent


def _resolve_user_path(p: str, *, base: Optional[Path] = None) -> Path:
    r"""Resolve a user-supplied path with a safer default base directory.

    - Expands ~
    - If absolute, resolves as-is
    - If relative, resolves relative to *base* (defaults to project root)

    This keeps default outputs (positions.json, images/, logs) inside the pyFIB folder
    even if the process current working directory is something like
    C:\Program Files (x86)\Carl Zeiss\...
    """
    base_dir = base or _project_root()
    pp = Path(str(p)).expanduser()
    if not pp.is_absolute():
        pp = base_dir / pp
    try:
        return pp.resolve()
    except Exception:
        # resolve() can fail on some network paths; fall back to absolute.
        return pp.absolute()


def _add_common_connection_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--connect",
        choices=["local", "remoting"],
        default="remoting",
        help="How to connect to SmartSEM: local (Initialise) or remoting (InitialiseRemoting). Default: remoting.",
    )
    p.add_argument(
        "--machine",
        default="",
        help="Machine name for local Initialise(machine). Leave empty for local host.",
    )


def cmd_ui(args: argparse.Namespace) -> int:
    """Launch the pyFIB GUI (Qt by default; --tk for the legacy Tkinter UI).

      python -m pyfib ui            # Qt GUI (default)
      python -m pyfib ui --tk       # legacy Tkinter UI

    Either way the GUI runs the existing CLI commands as subprocesses.
    """
    positions_file = (
        str(_resolve_user_path(args.positions_file))
        if getattr(args, "positions_file", None)
        else None
    )

    # Default: the Qt GUI.
    if not getattr(args, "tk", False):
        try:
            from .gui_qt import main as qt_main
        except Exception as e:  # qtpy / PyQt5 not installed
            print(
                "The Qt GUI needs qtpy and PyQt5, which don't seem to be installed:\n"
                f"  {e}\n"
                "Install them (pip install qtpy PyQt5) or run the legacy UI with:\n"
                "  python -m pyfib ui --tk",
                file=sys.stderr,
            )
            return 2
        return qt_main(positions_file=positions_file)

    # Legacy Tkinter UI (--tk).
    from .gui import launch_gui
    try:
        timeout_s = int(float(args.timeout))
    except Exception:
        timeout_s = 3600
    launch_gui(positions_file=positions_file,
               ely_file=str(_resolve_user_path(args.ely)) if args.ely is not None else None,
               timeout_s=timeout_s)
    return 0
    return 0


def cmd_pattern_info(args: argparse.Namespace) -> int:
    pat = ELayoutPattern.from_file(_resolve_user_path(args.ely))
    info = pat.summary_dict()
    print(info)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    ely_local = _resolve_user_path(args.ely)

    # Determine microscope path only for macro method
    ely_microscope_path = None
    if args.load_method == "macro":
        if args.ely_microscope_path is not None:
            ely_microscope_path = args.ely_microscope_path
        else:
            # Assume the script runs on the microscope PC; use local path.
            ely_microscope_path = str(ely_local)

    job = PatternJob(
        ely_local_path=ely_local,
        load_method=args.load_method,
        # smartfib_drop
        smartfib_drop_dir=Path(args.smartfib_drop_dir),
        smartfib_drop_name=args.smartfib_drop_name,
        smartfib_load_delay_s=float(args.smartfib_load_delay_s),
        smartfib_prepare_delay_s=float(args.smartfib_prepare_delay_s),
        set_patterning_mode_fib=not args.no_set_patterning_mode,
        # macro
        ely_microscope_path=ely_microscope_path,
        sv_user_dev_slot=args.sv_slot,
        load_pattern_macro=args.load_macro,
        # optional
        pre_macro=args.pre_macro,
        post_macro=args.post_macro,
        ensure_fib_mode_mill=bool(args.mode_mill and (not args.no_mode_mill)),
        arm=args.arm,
        wait_strategy=args.wait,
        wait_timeout_s=float(args.timeout),
        sleep_s=float(args.sleep_s),
    )

    with SmartSEM() as sem:
        if args.connect == "local":
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        runner = PatternRunner(sem)
        runner.run(job)

    return 0


# ----------------------------
# Positions: capture/list/recall
# ----------------------------

def _load_or_new_db(path: Path) -> PositionDatabase:
    if path.exists():
        return PositionDatabase.load(path)
    return PositionDatabase()


def cmd_capture_position(args: argparse.Namespace) -> int:
    pos_file = _resolve_user_path(args.positions_file)
    name = str(args.name)

    img_dir = Path(args.image_dir)
    if not img_dir.is_absolute():
        img_dir = pos_file.parent / img_dir
    img_dir.mkdir(parents=True, exist_ok=True)

    # Default image filename: <name>.bmp (or with timestamp if not overwriting)
    img_path = img_dir / f"{name}.bmp"
    if img_path.exists() and not args.overwrite_image:
        ts = time.strftime("%Y%m%d_%H%M%S")
        img_path = img_dir / f"{name}_{ts}.bmp"

    if not args.arm:
        print("[DRY RUN] Would do:")
        print("  - Ensure FIB imaging (request DP_FIB_IMAGING=1 and/or CMD_FIB_MODE_FIB)")
        print(f"  - Set AP_FIB_MAGNIFICATION = {float(args.fib_mag):.6g}")
        print(f"  - Grab BMP to: {img_path}")
        if not bool(args.no_freeze_after_grab):
            print("  - Freeze scanning (CMD_FREEZE_ZONE) after successful grab")
        print("  - Read AP_STAGE_AT_X/Y/Z/R/M and AP_FIB_OBJECTIVE_POTENTIAL")
        print(f"  - Save/update: {pos_file}")
        return 0

    db = _load_or_new_db(pos_file)

    with SmartSEM() as sem:
        if args.connect == "local":
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        # Try to store serial number for context
        if db.microscope_serial is None:
            try:
                db.microscope_serial = sem.get_str("SV_SERIAL_NUMBER")
            except Exception:
                db.microscope_serial = None

        rec = capture_position(
            sem,
            name,
            fib_magnification_x=float(args.fib_mag),
            image_bmp=img_path,
            grab_xoff=int(args.xoff),
            grab_yoff=int(args.yoff),
            grab_width=int(args.width),
            grab_height=int(args.height),
            grab_reduction=int(args.reduction),
            settle_s=float(args.settle_s),
            include_tilt=bool(args.include_tilt),
            unfreeze_before_grab=not bool(getattr(args, "no_unfreeze_before_grab", False)),
            freeze_after_grab=not bool(getattr(args, "no_freeze_after_grab", False)),
        )

    # Store relative image path if possible
    try:
        rec = rec.__class__(
            **{**rec.__dict__, "image_bmp": str(Path(rec.image_bmp).resolve().relative_to(pos_file.parent))}
        )
    except Exception:
        pass

    db.upsert(rec)
    db.save(pos_file)

    print(f"Captured {name} -> {pos_file}")
    print(f"  Image: {rec.image_bmp}")
    print(f"  Stage: X={rec.stage_at.x_m:.6e} m, Y={rec.stage_at.y_m:.6e} m, Z={rec.stage_at.z_m:.6e} m, R={rec.stage_at.r_deg:.6f} deg, M={rec.stage_at.m_m:.6e} m")
    if rec.stage_at.t_deg is not None:
        print(f"  Tilt:  T={rec.stage_at.t_deg:.6f} deg")
    print(f"  FIB objective potential: {rec.fib_objective_potential_v:.6f} V")

    return 0



def cmd_debug_fib_imaging(args: argparse.Namespace) -> int:
    """Debug FIB imaging switching by printing DP states before/after a request.

    This is intended to diagnose cases where capture-position times out waiting for
    DP_FIB_IMAGING to become FIB.

    Behavior:
      - Without --arm: prints a single snapshot and exits.
      - With --arm: runs a sequence of steps (Set DP_FIB_IMAGING numeric/string, Execute CMD_FIB_MODE_*)
        and logs key DP states over time.

    Exit code:
      0 = completed (may or may not reach target)
    """

    # Target mapping
    target = str(args.target).strip().lower()
    target_map = {
        'fib': (1, 'FIB', 'CMD_FIB_MODE_FIB'),
        'sem': (0, 'SEM', 'CMD_FIB_MODE_SEM'),
        'sem+fib': (3, 'SEM + FIB', 'CMD_FIB_MODE_SEM_FIB'),
        'semfib': (3, 'SEM + FIB', 'CMD_FIB_MODE_SEM_FIB'),
        'mill+sem': (4, 'Mill + SEM', 'CMD_FIB_MODE_MILL_SEM'),
        'millsem': (4, 'Mill + SEM', 'CMD_FIB_MODE_MILL_SEM'),
    }
    if target not in target_map:
        print('Unknown --target {!r}. Use: fib, sem, sem+fib, mill+sem'.format(target))
        return 2

    desired_num, desired_str, desired_cmd = target_map[target]

    steps = [s.strip().lower() for s in str(args.steps).split(',') if s.strip()]
    per_step_s = float(args.per_step_s)
    poll_s = float(args.poll_s)
    stop_if_target = bool(args.stop_if_target)

    log_file = str(args.log_file or '').strip()
    log_fh = open(log_file, 'w', encoding='utf-8') if log_file else None

    dps = [
        'DP_FIB_IMAGING',
        'DP_FIB_MODE',
        'DP_FIB_GUN_STATE',
        'DP_FIB_BLANKED',
        'DP_PATTERNING_MODE',
        'DP_AUTO_FUNCTION',
        'DP_FROZEN',
        'DP_EXT_SCAN_CONTROL',
        'DP_PANEL_DISABLE',
        'DP_STAGE_IS',
    ]

    def emit(line: str) -> None:
        print(line)
        if log_fh:
            log_fh.write(line + '\n')
            log_fh.flush()

    def safe_float(sem: SmartSEM, m: str):
        try:
            return sem.get_float(m), None
        except Exception as e:
            return None, str(e)

    def safe_str(sem: SmartSEM, m: str):
        try:
            return sem.get_str(m), None
        except Exception as e:
            return None, str(e)

    def fmt_num(v):
        if v is None:
            return 'NA'
        try:
            if abs(float(v) - round(float(v))) < 1e-6:
                return str(int(round(float(v))))
        except Exception:
            pass
        try:
            return '{:.6g}'.format(float(v))
        except Exception:
            return str(v)

    def fmt_str(v):
        if v is None:
            return 'NA'
        s = str(v)
        return s.replace('\r', ' ').replace('\n', ' ').strip()

    def snapshot_line(sem: SmartSEM, prefix: str, elapsed_s: float = 0.0) -> str:
        parts = [prefix]
        if elapsed_s is not None:
            parts.append('t={:.2f}s'.format(float(elapsed_s)))
        for m in dps:
            f, _ = safe_float(sem, m)
            s, _ = safe_str(sem, m)
            parts.append('{}={}\'{}\''.format(m, fmt_num(f), fmt_str(s)))
        return ' | '.join(parts)

    with SmartSEM() as sem:
        if args.connect == 'local':
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        emit('=== pyFIB debug-fib-imaging ===')
        emit('Python: {}'.format(sys.version.split()[0]))
        emit('Connect: {}'.format(args.connect))
        emit('Target: {}  (DP_FIB_IMAGING={} / {!r}, CMD={!r})'.format(target, desired_num, desired_str, desired_cmd))
        try:
            serial = sem.get_str('SV_SERIAL_NUMBER')
        except Exception:
            serial = None
        if serial:
            emit('SV_SERIAL_NUMBER: {}'.format(serial))

        emit(snapshot_line(sem, 'BEFORE', 0.0))

        if not bool(args.arm):
            emit('[DRY RUN] Not executing any Set/Execute steps. Re-run with --arm to perform steps.')
            if log_fh:
                log_fh.close()
            return 0

        # Execute steps
        reached = False
        for step in steps:
            step = step.strip()
            if not step:
                continue

            emit('--- STEP: {} ---'.format(step))

            if step == 'dp_num':
                try:
                    sem.set_value('DP_FIB_IMAGING', int(desired_num))
                    emit('Set(DP_FIB_IMAGING, {}) OK'.format(desired_num))
                except Exception as e:
                    emit('Set(DP_FIB_IMAGING, {}) FAILED: {}'.format(desired_num, e))

            elif step == 'dp_str':
                try:
                    sem.set_value('DP_FIB_IMAGING', desired_str)
                    emit('Set(DP_FIB_IMAGING, {!r}) OK'.format(desired_str))
                except Exception as e:
                    emit('Set(DP_FIB_IMAGING, {!r}) FAILED: {}'.format(desired_str, e))

            elif step == 'cmd':
                try:
                    sem.execute(desired_cmd)
                    emit('Execute({}) OK'.format(desired_cmd))
                except Exception as e:
                    emit('Execute({}) FAILED: {}'.format(desired_cmd, e))

            elif step == 'wait':
                emit('(wait only)')

            else:
                emit('Unknown step {!r} (valid: dp_num, dp_str, cmd, wait)'.format(step))

            # Observe after each step
            t0 = time.time()
            while True:
                elapsed = time.time() - t0
                emit(snapshot_line(sem, 'SAMPLE', elapsed))

                v, _ = safe_float(sem, 'DP_FIB_IMAGING')
                try:
                    if v is not None and int(round(float(v))) == int(desired_num):
                        reached = True
                        if stop_if_target:
                            emit('[STOP] Reached target DP_FIB_IMAGING={} during step {}.'.format(desired_num, step))
                            break
                except Exception:
                    pass

                if elapsed >= per_step_s:
                    break
                time.sleep(poll_s)

            if reached and stop_if_target:
                break

        emit(snapshot_line(sem, 'AFTER', 0.0))
        if reached:
            emit('[RESULT] DP_FIB_IMAGING reached target={}'.format(desired_num))
        else:
            emit('[RESULT] DP_FIB_IMAGING did NOT reach target={} within the observation window.'.format(desired_num))

    if log_fh:
        log_fh.close()

    return 0


def cmd_list_positions(args: argparse.Namespace) -> int:
    pos_file = _resolve_user_path(args.positions_file)
    db = PositionDatabase.load(pos_file)

    names = sorted(db.positions.keys())
    if not names:
        print("No positions in file.")
        return 0

    print(f"Positions in {pos_file}:")
    for n in names:
        rec = db.positions[n]
        print(
            f"- {n}: X={rec.stage_at.x_m:.3e} m, Y={rec.stage_at.y_m:.3e} m, Z={rec.stage_at.z_m:.3e} m, R={rec.stage_at.r_deg:.3f} deg, M={rec.stage_at.m_m:.3e} m, Obj={rec.fib_objective_potential_v:.3f} V"
        )
    return 0


def cmd_goto_position(args: argparse.Namespace) -> int:
    pos_file = _resolve_user_path(args.positions_file)
    db = PositionDatabase.load(pos_file)
    rec = db.get(str(args.name))

    if not args.arm:
        print("[DRY RUN] Would do:")
        if args.set_objective:
            print(f"  - Set AP_FIB_OBJECTIVE_POTENTIAL = {rec.fib_objective_potential_v:.6f} V")
        if args.set_mag:
            print(f"  - Set AP_FIB_MAGNIFICATION = {rec.fib_magnification_x:.6g}")
        if args.move_method != "none":
            print(f"  - Move stage via {args.move_method} to:")
            print(
                f"      X={rec.stage_at.x_m:.6e} m, Y={rec.stage_at.y_m:.6e} m, Z={rec.stage_at.z_m:.6e} m, R={rec.stage_at.r_deg:.6f} deg, M={rec.stage_at.m_m:.6e} m"
            )
        print("  - (Optionally wait for DP_STAGE_IS idle)")
        return 0

    with SmartSEM() as sem:
        if args.connect == "local":
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        recall_position(
            sem,
            rec,
            move_method=args.move_method,
            set_objective=bool(args.set_objective),
            set_magnification=bool(args.set_mag),
            wait_stage_idle=bool(args.wait_stage_idle),
            stage_timeout_s=float(args.stage_timeout_s),
        )

    print(f"Recalled {rec.name}")
    return 0


def cmd_goto_and_mill(args: argparse.Namespace) -> int:
    """Recall a stored position (stage + objective potential) and then run a milling pattern."""

    pos_file = _resolve_user_path(args.positions_file)
    db = PositionDatabase.load(pos_file)
    rec = db.get(str(args.name))

    ely_local = _resolve_user_path(args.ely)

    if not args.arm:
        print("[DRY RUN] Would do:")
        if args.move_method != "none":
            print(f"  - Move stage via {args.move_method} to:")
            print(
                f"      X={rec.stage_at.x_m:.6e} m, Y={rec.stage_at.y_m:.6e} m, Z={rec.stage_at.z_m:.6e} m, R={rec.stage_at.r_deg:.6f} deg, M={rec.stage_at.m_m:.6e} m"
            )
            if args.wait_stage_idle:
                print("  - Wait for DP_STAGE_IS idle")
            else:
                print("  - (Not waiting for DP_STAGE_IS idle)")
        if not args.skip_objective:
            print(f"  - Set AP_FIB_OBJECTIVE_POTENTIAL = {rec.fib_objective_potential_v:.6f} V")
        if args.set_mag:
            print(f"  - Set AP_FIB_MAGNIFICATION = {rec.fib_magnification_x:.6g}")

        print("  - Then mill pattern using SmartFIB load/prepare/start:")
        print(f"      Pattern: {ely_local}")
        if args.load_method == "smartfib_drop":
            tgt = Path(args.smartfib_drop_dir) / args.smartfib_drop_name
            print(f"      - Copy .ely to: {tgt}")
            print("      - Execute: CMD_SMARTFIB_LOAD_ELY")
            print(f"      - Sleep {float(args.smartfib_load_delay_s):.2f}s")
            print("      - Execute: CMD_SMARTFIB_PREPARE_EXPOSURE")
            print(f"      - Sleep {float(args.smartfib_prepare_delay_s):.2f}s")
        else:
            print("      - (Assume pattern already present/loaded in SmartFIB)")
        if args.mode_mill and (not args.no_mode_mill):
            print("      - Execute: CMD_FIB_MODE_MILL")
        print("      - Execute: CMD_FIB_START_MILLING")
        if not args.no_set_patterning_mode:
            print("      - Try set DP_PATTERNING_MODE = 'FIB'")
        if args.wait != "none":
            print(f"      - Wait: {args.wait}")
        return 0

    # ---- ARMED ----
    with SmartSEM() as sem:
        if args.connect == "local":
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        # 1) Move stage first (if requested)
        if args.move_method != "none":
            recall_position(
                sem,
                rec,
                move_method=args.move_method,
                set_objective=False,  # apply objective after stage motion
                set_magnification=bool(args.set_mag),
                wait_stage_idle=bool(args.wait_stage_idle),
                stage_timeout_s=float(args.stage_timeout_s),
            )

        # 2) Apply objective potential after stage is at the site
        if not args.skip_objective:
            sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", float(rec.fib_objective_potential_v))

        if args.settle_s and float(args.settle_s) > 0:
            time.sleep(float(args.settle_s))

        # 3) Run milling pattern
        job = PatternJob(
            ely_local_path=ely_local,
            load_method=args.load_method,
            smartfib_drop_dir=Path(args.smartfib_drop_dir),
            smartfib_drop_name=args.smartfib_drop_name,
            smartfib_load_delay_s=float(args.smartfib_load_delay_s),
            smartfib_prepare_delay_s=float(args.smartfib_prepare_delay_s),
            set_patterning_mode_fib=not args.no_set_patterning_mode,
            ensure_fib_mode_mill=bool(args.mode_mill and (not args.no_mode_mill)),
            arm=True,
            wait_strategy=args.wait,
            wait_timeout_s=float(args.timeout),
            sleep_s=float(args.sleep_s),
        )

        runner = PatternRunner(sem)
        runner.run(job)

    print(f"Recalled {rec.name} and started milling.")
    return 0




def cmd_mill_positions(args: argparse.Namespace) -> int:
    """Mill the same SmartFIB pattern at multiple stored positions sequentially.

    Order:
      - If position names are provided: mill in that order.
      - Otherwise: mill all positions in the positions file in natural order
        (position_1, position_2, ...).
    """

    pos_file = _resolve_user_path(args.positions_file)
    db = PositionDatabase.load(pos_file)

    ely_local = _resolve_user_path(args.ely)

    # Determine which positions to mill
    if args.names and len(args.names) > 0:
        names = [str(n) for n in args.names]
        # validate
        for n in names:
            _ = db.get(n)
    else:
        names = natural_position_sort(db.positions.keys())

    if not names:
        print('No positions to mill.')
        return 0

    # Per-position autofocus / refine selection.
    def _resolve_flag_set(csv, use_all):
        if use_all:
            return set(names)
        if not csv:
            return set()
        picked = {s.strip() for s in str(csv).split(",") if s.strip()}
        for n in picked:
            _ = db.get(n)  # validate
        return picked

    af_set = _resolve_flag_set(getattr(args, "autofocus", None),
                               getattr(args, "autofocus_all", False))
    rf_set = _resolve_flag_set(getattr(args, "refine", None),
                               getattr(args, "refine_all", False))

    if not args.arm:
        print('[DRY RUN] Would do:')
        print(f'  - Pattern: {ely_local}')
        print(f'  - Positions file: {pos_file}')
        print('  - Sequence:')
        for i, n in enumerate(names, start=1):
            rec = db.positions[n]
            extra = []
            if n in af_set:
                extra.append("AUTOFOCUS")
            if n in rf_set:
                extra.append("REFINE")
            tag = ("  [" + "+".join(extra) + "]") if extra else ""
            print(f'    {i}. {n}: X={rec.stage_at.x_m:.3e} m, Y={rec.stage_at.y_m:.3e} m, Z={rec.stage_at.z_m:.3e} m, R={rec.stage_at.r_deg:.3f} deg, M={rec.stage_at.m_m:.3e} m, Obj={rec.fib_objective_potential_v:.3f} V{tag}')
        print('  - For each position: move stage -> set objective -> [autofocus] -> [refine] -> load/prepare -> start milling -> wait -> next')
        if args.mode_mill and (not args.no_mode_mill):
            print('  - (Would also force CMD_FIB_MODE_MILL before each milling run)')
        return 0

    # ---- ARMED ----
    with SmartSEM() as sem:
        if args.connect == 'local':
            sem.connect_local(args.machine)
        else:
            sem.connect_remoting()

        mill_positions_sequential(
            sem,
            db=db,
            position_names=names,
            ely_local_path=ely_local,
            move_method=args.move_method,
            wait_stage_idle=bool(args.wait_stage_idle),
            stage_timeout_s=float(args.stage_timeout_s),
            skip_objective=bool(args.skip_objective),
            set_mag=bool(args.set_mag),
            settle_s=float(args.settle_s),
            load_method=args.load_method,
            smartfib_drop_dir=Path(args.smartfib_drop_dir),
            smartfib_drop_name=args.smartfib_drop_name,
            smartfib_load_delay_s=float(args.smartfib_load_delay_s),
            smartfib_prepare_delay_s=float(args.smartfib_prepare_delay_s),
            set_patterning_mode_fib=not args.no_set_patterning_mode,
            ensure_fib_mode_mill=bool(args.mode_mill and (not args.no_mode_mill)),
            wait_strategy=args.wait,
            wait_timeout_s=float(args.timeout),
            done_confirm_s=float(args.done_confirm_s),
            sleep_s=float(args.sleep_s),
            continue_on_error=bool(args.continue_on_error),
            autofocus_names=af_set,
            refine_names=rf_set,
            autofocus_config=AutofocusConfig(
                half_range_v=float(getattr(args, "af_half_range", 150.0)),
                coarse_points=int(getattr(args, "af_coarse_points", 7)),
                abort_on_failure=bool(getattr(args, "af_abort_on_fail", False)),
                imaging_scan_rate=(None if int(getattr(args, "imaging_scan_rate", 8)) < 0
                                   else int(getattr(args, "imaging_scan_rate", 8))),
                scan_rate_method=getattr(args, "scan_rate_method", "set"),
                analysis_height_frac=float(getattr(args, "af_analysis_height_frac", 1.0)),
                raster_h_scale=getattr(args, "af_raster_h_scale", None),
            ),
            refine_config=RefineConfig(
                abort_on_failure=bool(getattr(args, "refine_abort_on_fail", False)),
                imaging_scan_rate=(None if int(getattr(args, "imaging_scan_rate", 8)) < 0
                                   else int(getattr(args, "imaging_scan_rate", 8))),
                scan_rate_method=getattr(args, "scan_rate_method", "set"),
            ),
            ref_base_dir=pos_file.parent,
            reset_beam_shift_between_positions=not bool(getattr(args, "no_reset_beam_shift", False)),
        )

    print('Batch milling completed.')
    return 0

# ----------------------------
# Parser
# ----------------------------


def build_parser(prog_name: str = "pyfib") -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=prog_name,
        description=(
            "Execute a SmartFIB (.ely) milling pattern at the current stage location via SmartSEM Remote API, "
            "and/or capture named positions (stage + FIB objective potential) for later recall."  # noqa: E501
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- pattern-info
    p_info = sub.add_parser("pattern-info", help="Print a summary of an .ely pattern file.")
    p_info.add_argument("ely", help="Path to .ely file.")
    p_info.set_defaults(func=cmd_pattern_info)

    # ---- run
    p_run = sub.add_parser("run", help="Run milling for the given .ely pattern.")
    p_run.add_argument("ely", help="Local path to the .ely file (on this PC).")

    p_run.add_argument(
        "--load-method",
        choices=["smartfib_drop", "macro", "none"],
        default="smartfib_drop",
        help=(
            "How to load the pattern into SmartFIB before milling. "
            "smartfib_drop=copy to SmartFIB API Drop as ApiLayout.ely and execute CMD_SMARTFIB_LOAD_ELY + CMD_SMARTFIB_PREPARE_EXPOSURE. "
            "macro=use a SmartSEM macro reading SV_USER_DEV_N. none=assume pattern already loaded."
        ),
    )

    # smartfib_drop options (SerialFIB-style)
    p_run.add_argument(
        "--smartfib-drop-dir",
        default=r"C:\\ProgramData\\Carl Zeiss\\SmartFIB\\API\\Drop",
        help=(
            "SmartFIB API drop directory on the microscope PC. "
            "Default: C:\\ProgramData\\Carl Zeiss\\SmartFIB\\API\\Drop"
        ),
    )
    p_run.add_argument(
        "--smartfib-drop-name",
        default="ApiLayout.ely",
        help="Filename SmartFIB expects in the drop folder. Default: ApiLayout.ely",
    )
    p_run.add_argument(
        "--smartfib-load-delay-s",
        default=1.0,
        help="Seconds to wait after CMD_SMARTFIB_LOAD_ELY. Default: 1.0",
    )
    p_run.add_argument(
        "--smartfib-prepare-delay-s",
        default=1.0,
        help="Seconds to wait after CMD_SMARTFIB_PREPARE_EXPOSURE. Default: 1.0",
    )
    p_run.add_argument(
        "--no-set-patterning-mode",
        action="store_true",
        help="Do not attempt to set DP_PATTERNING_MODE='FIB' after starting milling.",
    )

    # macro options (legacy)
    p_run.add_argument(
        "--ely-microscope-path",
        default=None,
        help=(
            "(macro method) Path to the .ely file as seen by SmartSEM/SmartFIB on the microscope PC. "
            "If omitted, uses the local path (assumes this script runs on the microscope PC)."
        ),
    )
    p_run.add_argument(
        "--sv-slot",
        type=int,
        default=1,
        help="(macro method) Which SV_USER_DEV_N slot to write the pattern path into (1..5). Default: 1.",
    )
    p_run.add_argument(
        "--load-macro",
        default=None,
        help=(
            "(macro method) Macro (MCF_/MCL_) that loads the .ely file path from SV_USER_DEV_N into SmartFIB. "
            "Optional; omit if pattern is already loaded."
        ),
    )

    p_run.add_argument("--pre-macro", default=None, help="Optional macro executed before starting milling.")
    p_run.add_argument("--post-macro", default=None, help="Optional macro executed after milling (even on error).")

    p_run.add_argument(
        "--arm",
        action="store_true",
        help="Actually execute commands on the instrument. Without this flag, the command is a dry-run.",
    )
    p_run.add_argument(
        "--mode-mill",
        action="store_true",
        help=(
            "Force CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING. "
            "Not usually required (SerialFIB starts milling without it) and can hang on some systems."
        ),
    )
    # Kept for backward compatibility with earlier versions where CMD_FIB_MODE_MILL
    # was the default.
    p_run.add_argument(
        "--no-mode-mill",
        action="store_true",
        help="Disable CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING (default).",
    )
    p_run.add_argument(
        "--wait",
        choices=["none", "mode_change", "blanked", "auto", "sleep"],
        default="mode_change",
        help=(
            "How to wait after CMD_FIB_START_MILLING. 'auto' uses DP_FIB_MODE + DP_FIB_BLANKED; 'mode_change' watches DP_FIB_MODE; "
            "'blanked' watches DP_FIB_BLANKED; 'sleep' waits fixed seconds; 'none' returns immediately."
        ),
    )
    p_run.add_argument("--timeout", default=5400, help="Timeout (s) used for --wait. Use 0 for no timeout.")
    p_run.add_argument("--sleep-s", default=0, help="Seconds to sleep when --wait sleep.")

    _add_common_connection_args(p_run)

    p_run.set_defaults(func=cmd_run)

    # ---- capture-position
    p_cap = sub.add_parser(
        "capture-position",
        help=(
            "Set FIB magnification, grab a BMP, and record stage X/Y/Z/R/M + FIB objective potential into a JSON file."
        ),
    )
    p_cap.add_argument("name", help='Position name like "position_1".')
    p_cap.add_argument(
        "--positions-file",
        default="positions.json",
        help="JSON file to create/update. Default: positions.json (relative paths resolve to the pyFIB folder by default).",
    )
    p_cap.add_argument(
        "--fib-mag",
        type=float,
        default=2.000e3,
        help="FIB magnification to set before grabbing/recording. Default: 2000.",
    )
    p_cap.add_argument(
        "--image-dir",
        default="images",
        help=(
            "Directory for BMP grabs. Relative paths are relative to the positions-file folder. "
            "Default: images (inside the pyFIB folder by default)"
        ),
    )
    p_cap.add_argument(
        "--no-unfreeze-before-grab",
        action="store_true",
        help="By default pyFIB executes CMD_UNFREEZE_ZONE before grabbing to ensure a fresh frame. Use this flag to disable that behavior.",
    )
    p_cap.add_argument(
        "--no-freeze-after-grab",
        action="store_true",
        help="After a successful Grab(), pyFIB freezes scanning with CMD_FREEZE_ZONE to avoid continued dose. Use this flag to disable that behavior.",
    )
    p_cap.add_argument(
        "--overwrite-image",
        action="store_true",
        help="Overwrite <image-dir>/<name>.bmp if it exists. Otherwise append timestamp.",
    )
    p_cap.add_argument("--xoff", type=int, default=0)
    p_cap.add_argument("--yoff", type=int, default=0)
    p_cap.add_argument("--width", type=int, default=1024)
    p_cap.add_argument("--height", type=int, default=768)
    p_cap.add_argument("--reduction", type=int, default=0)
    p_cap.add_argument(
        "--settle-s",
        type=float,
        default=0.25,
        help="Seconds to wait after setting magnification before grabbing/recording. Default: 0.25",
    )
    p_cap.add_argument(
        "--include-tilt",
        action="store_true",
        help="Also record AP_STAGE_AT_T (tilt) for completeness.",
    )
    p_cap.add_argument(
        "--arm",
        action="store_true",
        help="Actually execute on the instrument. Without this flag, this is a dry-run.",
    )
    _add_common_connection_args(p_cap)
    p_cap.set_defaults(func=cmd_capture_position)

    # ---- debug-fib-imaging
    p_dbg = sub.add_parser(
        'debug-fib-imaging',
        help='Debug FIB/SEM imaging mode switching by logging DP_FIB_IMAGING and related DP states.',
    )
    p_dbg.add_argument(
        '--target',
        default='fib',
        help='Target imaging mode: fib, sem, sem+fib, mill+sem. Default: fib.',
    )
    p_dbg.add_argument(
        '--steps',
        default='dp_num,dp_str,cmd',
        help='Comma-separated steps to try: dp_num, dp_str, cmd, wait. Default: dp_num,dp_str,cmd.',
    )
    p_dbg.add_argument(
        '--per-step-s',
        type=float,
        default=15.0,
        help='Seconds to observe/log after each step. Default: 15.',
    )
    p_dbg.add_argument(
        '--poll-s',
        type=float,
        default=0.5,
        help='Polling interval (seconds) for logging DP states. Default: 0.5.',
    )
    p_dbg.add_argument(
        '--stop-if-target',
        action='store_true',
        help='Stop early if DP_FIB_IMAGING reaches the target state.',
    )
    p_dbg.add_argument(
        '--log-file',
        default='',
        help='Optional path to write the debug log (text).',
    )
    p_dbg.add_argument(
        '--arm',
        action='store_true',
        help='Actually execute the requested steps. Without this flag, prints current state only.',
    )
    _add_common_connection_args(p_dbg)
    p_dbg.set_defaults(func=cmd_debug_fib_imaging)


    # ---- list-positions
    p_ls = sub.add_parser("list-positions", help="List positions stored in a positions JSON file.")
    p_ls.add_argument("--positions-file", default="positions.json", help="Path to positions JSON file.")
    p_ls.set_defaults(func=cmd_list_positions)

    # ---- goto-position
    p_go = sub.add_parser(
        "goto-position",
        help="Recall a stored position: optionally set FIB objective potential and move stage.",
    )
    p_go.add_argument("name", help="Position name to recall (e.g. position_1)")
    p_go.add_argument("--positions-file", default="positions.json", help="Path to positions JSON file.")
    p_go.add_argument(
        "--move-method",
        choices=["ap_goto", "movestage", "none"],
        default="ap_goto",
        help=(
            "Stage recall method. ap_goto sets AP_STAGE_GOTO_* targets. movestage calls MoveStage(). none does not move stage."
        ),
    )
    p_go.add_argument(
        "--set-objective",
        action="store_true",
        help="Set AP_FIB_OBJECTIVE_POTENTIAL to the recorded value.",
    )
    p_go.add_argument(
        "--set-mag",
        action="store_true",
        help="Set AP_FIB_MAGNIFICATION to the recorded magnification.",
    )
    p_go.add_argument(
        "--wait-stage-idle",
        action="store_true",
        help="After requesting a move, wait for DP_STAGE_IS to become Idle.",
    )
    p_go.add_argument(
        "--stage-timeout-s",
        type=float,
        default=120.0,
        help="Timeout used for --wait-stage-idle. Default: 120",
    )
    p_go.add_argument(
        "--arm",
        action="store_true",
        help="Actually execute on the instrument. Without this flag, this is a dry-run.",
    )
    _add_common_connection_args(p_go)
    p_go.set_defaults(func=cmd_goto_position)

    # ---- goto-and-mill
    p_gm = sub.add_parser(
        "goto-and-mill",
        help=(
            "Recall a stored position (stage + objective potential) and then load/prepare/start a SmartFIB .ely milling pattern."
        ),
    )
    p_gm.add_argument("name", help="Position name to recall (e.g. position_1)")
    p_gm.add_argument("ely", help="Local path to the .ely file to run after recall.")
    p_gm.add_argument("--positions-file", default="positions.json", help="Path to positions JSON file.")

    p_gm.add_argument(
        "--move-method",
        choices=["ap_goto", "movestage", "none"],
        default="ap_goto",
        help="Stage recall method before milling. Default: ap_goto",
    )
    p_gm.add_argument(
        "--no-wait-stage-idle",
        dest="wait_stage_idle",
        action="store_false",
        help="Do not wait for DP_STAGE_IS to become Idle before milling.",
    )
    p_gm.set_defaults(wait_stage_idle=True)
    p_gm.add_argument(
        "--stage-timeout-s",
        type=float,
        default=120.0,
        help="Timeout used for --wait-stage-idle. Default: 120",
    )
    p_gm.add_argument(
        "--skip-objective",
        action="store_true",
        help="Do not set AP_FIB_OBJECTIVE_POTENTIAL before milling.",
    )
    p_gm.add_argument(
        "--set-mag",
        action="store_true",
        help="Also set AP_FIB_MAGNIFICATION to the recorded magnification before milling.",
    )
    p_gm.add_argument(
        "--settle-s",
        type=float,
        default=0.25,
        help="Seconds to wait after recall/setting objective before loading the pattern. Default: 0.25",
    )

    # Pattern loading method (SerialFIB-style by default)
    p_gm.add_argument(
        "--load-method",
        choices=["smartfib_drop", "none"],
        default="smartfib_drop",
        help=(
            "How to load the pattern into SmartFIB. smartfib_drop copies the .ely to the SmartFIB API Drop folder and calls "
            "CMD_SMARTFIB_LOAD_ELY + CMD_SMARTFIB_PREPARE_EXPOSURE. none assumes the pattern is already loaded."
        ),
    )
    p_gm.add_argument(
        "--smartfib-drop-dir",
        default=r"C:\ProgramData\Carl Zeiss\SmartFIB\API\Drop",
        help="SmartFIB API drop directory. Default: C:\\ProgramData\\Carl Zeiss\\SmartFIB\\API\\Drop",
    )
    p_gm.add_argument(
        "--smartfib-drop-name",
        default="ApiLayout.ely",
        help="Filename SmartFIB expects in the drop folder. Default: ApiLayout.ely",
    )
    p_gm.add_argument(
        "--smartfib-load-delay-s",
        default=1.0,
        help="Seconds to wait after CMD_SMARTFIB_LOAD_ELY. Default: 1.0",
    )
    p_gm.add_argument(
        "--smartfib-prepare-delay-s",
        default=1.0,
        help="Seconds to wait after CMD_SMARTFIB_PREPARE_EXPOSURE. Default: 1.0",
    )
    p_gm.add_argument(
        "--no-set-patterning-mode",
        action="store_true",
        help="Do not attempt to set DP_PATTERNING_MODE='FIB' after starting milling.",
    )
    p_gm.add_argument(
        "--mode-mill",
        action="store_true",
        help=(
            "Force CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING. "
            "Not usually required (SerialFIB starts milling without it) and can hang on some systems."
        ),
    )
    p_gm.add_argument(
        "--no-mode-mill",
        action="store_true",
        help="Disable CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING (default).",
    )
    p_gm.add_argument(
        "--wait",
        choices=["none", "mode_change", "blanked", "auto", "sleep"],
        default="mode_change",
        help="How to wait after CMD_FIB_START_MILLING. Default: mode_change. Use auto/blanked if DP_FIB_MODE does not change.",
    )
    p_gm.add_argument("--timeout", default=5400, help="Timeout (s) used for --wait. Use 0 for no timeout.")
    p_gm.add_argument("--sleep-s", default=0, help="Seconds to sleep when --wait sleep.")

    p_gm.add_argument(
        "--arm",
        action="store_true",
        help="Actually execute on the instrument. Without this flag, this is a dry-run.",
    )
    _add_common_connection_args(p_gm)

    # ---- mill-positions
    p_mp = sub.add_parser(
        'mill-positions',
        help=(
            'Recall multiple stored positions sequentially and mill the same SmartFIB .ely pattern at each. '
            'If no position names are provided, mills all positions in natural order.'
        ),
    )
    p_mp.add_argument('ely', help='Local path to the .ely file to run at each position.')
    p_mp.add_argument(
        'names',
        nargs='*',
        help='Optional list of position names (e.g. position_1 position_2). If omitted, mills all.'
    )
    p_mp.add_argument('--positions-file', default='positions.json', help='Path to positions JSON file.')

    p_mp.add_argument(
        '--move-method',
        choices=['ap_goto', 'movestage', 'none'],
        default='ap_goto',
        help='Stage recall method before milling each position. Default: ap_goto',
    )
    p_mp.add_argument(
        '--no-wait-stage-idle',
        dest='wait_stage_idle',
        action='store_false',
        help='Do not wait for DP_STAGE_IS to become Idle before milling each position.',
    )
    p_mp.set_defaults(wait_stage_idle=True)
    p_mp.add_argument(
        '--stage-timeout-s',
        type=float,
        default=120.0,
        help='Timeout used for waiting on stage idle. Default: 120',
    )
    p_mp.add_argument(
        '--skip-objective',
        action='store_true',
        help='Do not set AP_FIB_OBJECTIVE_POTENTIAL before milling each position.',
    )
    p_mp.add_argument(
        '--set-mag',
        action='store_true',
        help='Also set AP_FIB_MAGNIFICATION to the recorded magnification before milling each position.',
    )
    p_mp.add_argument(
        '--settle-s',
        type=float,
        default=0.25,
        help='Seconds to wait after recall/setting objective before loading the pattern. Default: 0.25',
    )

    p_mp.add_argument(
        '--load-method',
        choices=['smartfib_drop', 'none'],
        default='smartfib_drop',
        help=(
            'How to load the pattern into SmartFIB for each position. smartfib_drop copies the .ely to the SmartFIB API Drop folder and calls '
            'CMD_SMARTFIB_LOAD_ELY + CMD_SMARTFIB_PREPARE_EXPOSURE. none assumes the pattern is already loaded.'
        ),
    )
    p_mp.add_argument(
        '--smartfib-drop-dir',
        default=r'C:\ProgramData\Carl Zeiss\SmartFIB\API\Drop',
        help=r"SmartFIB API drop directory. Default: C:\ProgramData\Carl Zeiss\SmartFIB\API\Drop",
    )
    p_mp.add_argument(
        '--smartfib-drop-name',
        default='ApiLayout.ely',
        help='Filename SmartFIB expects in the drop folder. Default: ApiLayout.ely',
    )
    p_mp.add_argument('--smartfib-load-delay-s', default=1.0, help='Seconds to wait after CMD_SMARTFIB_LOAD_ELY. Default: 1.0')
    p_mp.add_argument('--smartfib-prepare-delay-s', default=1.0, help='Seconds to wait after CMD_SMARTFIB_PREPARE_EXPOSURE. Default: 1.0')
    p_mp.add_argument(
        '--no-set-patterning-mode',
        action='store_true',
        help="Do not attempt to set DP_PATTERNING_MODE='FIB' after starting milling.",
    )
    p_mp.add_argument(
        '--mode-mill',
        action='store_true',
        help=(
            'Force CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING. Not usually required and can hang on some systems.'
        ),
    )
    p_mp.add_argument(
        '--no-mode-mill',
        action='store_true',
        help='Disable CMD_FIB_MODE_MILL before CMD_FIB_START_MILLING (default).',
    )
    p_mp.add_argument(
        '--wait',
        choices=['none', 'mode_change', 'blanked', 'auto', 'sleep'],
        default='mode_change',
        help='How to wait after CMD_FIB_START_MILLING for each position. Default: mode_change. Use auto/blanked if DP_FIB_MODE does not change.',
    )
    p_mp.add_argument('--timeout', default=5400, help='Timeout (s) used for --wait. Use 0 for no timeout.')
    p_mp.add_argument(
        '--done-confirm-s',
        type=float,
        default=30.0,
        help=(
            'After a "milling finished" signature is detected, require it to remain stable for this many seconds '
            'before proceeding to the next position. Helps avoid false-positive completion. Default: 30.'
        ),
    )
    p_mp.add_argument('--sleep-s', default=0, help='Seconds to sleep when --wait sleep.')

    p_mp.add_argument(
        '--continue-on-error',
        action='store_true',
        help='Continue to the next position if one position fails. Default: stop on first error.',
    )

    # --- per-position autofocus / refine ---
    p_mp.add_argument(
        '--autofocus', default=None, metavar='NAME[,NAME...]',
        help='Comma-separated position names to AUTOFOCUS before milling (sweeps the '
             'saved objective voltage above/below). Leaves the column at best focus.',
    )
    p_mp.add_argument('--autofocus-all', action='store_true',
                      help='Autofocus at every milled position.')
    p_mp.add_argument(
        '--refine', default=None, metavar='NAME[,NAME...]',
        help='Comma-separated position names to REFINE (cross-correlate the live FIB '
             'image against the reference BMP and null the offset with beam shift). '
             'Runs AFTER autofocus for a position.',
    )
    p_mp.add_argument('--refine-all', action='store_true',
                      help='Refine at every milled position.')
    p_mp.add_argument('--af-half-range', type=float, default=150.0,
                      help='Autofocus sweep half-range in volts (default 150).')
    p_mp.add_argument('--af-coarse-points', type=int, default=7,
                      help='Autofocus coarse sweep point count (default 7).')
    p_mp.add_argument('--af-abort-on-fail', action='store_true',
                      help='Abort the position if autofocus does not converge '
                           '(default: warn and mill at the recalled voltage).')
    p_mp.add_argument('--refine-abort-on-fail', action='store_true',
                      help='Abort the position if refine does not converge '
                           '(default: warn and mill at the recalled position).')
    p_mp.add_argument('--no-reset-beam-shift', action='store_true',
                      help='Do NOT zero AP_FIB_BEAM_SHIFT_X/Y between positions. By '
                           'default the FIB beam shift is reset to 0 before the first '
                           'site and after every site, so a refine correction never '
                           'carries into the next position.')
    p_mp.add_argument('--imaging-scan-rate', type=int, default=8,
                      help='DP_SCANRATE for autofocus/refine frames (higher = more '
                           'dwell = better SNR). Use -1 to leave the scan rate as-is. '
                           'Milling dwell is unaffected.')
    p_mp.add_argument('--scan-rate-method', choices=('set', 'cmd', 'set_then_cmd'),
                      default='cmd',
                      help="How to change DP_SCANRATE (which is READ-ONLY on this "
                           "column): 'cmd' (CMD_SCANRATE#, default), 'set' (direct -- "
                           "fails here with rc=1019), or 'set_then_cmd'.")
    p_mp.add_argument('--af-analysis-height-frac', type=float, default=1.0,
                      help='Autofocus: score only this central fraction of ROWS '
                           '(0<f<=1). Use e.g. 0.5 at grazing FIB incidence so the '
                           'out-of-focus top/bottom of the box do not bias focus.')
    p_mp.add_argument('--af-raster-h-scale', type=float, default=None,
                      help='Autofocus: reduced-raster box height as a fraction of the '
                           'field. Lower = SHORTER box (less defocus spread and dose).')

    p_mp.add_argument(
        '--arm',
        action='store_true',
        help='Actually execute on the instrument. Without this flag, this is a dry-run.',
    )

    _add_common_connection_args(p_mp)
    p_mp.set_defaults(func=cmd_mill_positions)

    # ---- ui
    p_ui = sub.add_parser(
        'ui',
        help='Launch a simple desktop UI for capture-position / mill-positions and browsing BMPs.',
    )
    p_ui.add_argument(
        '--positions-file',
        default=None,
        help='Positions JSON path. If omitted, the UI starts a FRESH timestamped session '
             'file so an old positions.json is never loaded automatically (safer for stage '
             'collisions). Pass a path explicitly to open a specific file.',
    )
    p_ui.add_argument('--ely', default='', help='Initial .ely pattern path (optional).')
    p_ui.add_argument('--timeout', default=3600, help='Initial timeout seconds for milling (editable in UI).')
    p_ui.add_argument('--tk', action='store_true',
                      help='Launch the legacy Tkinter UI instead of the default Qt GUI.')
    p_ui.set_defaults(func=cmd_ui)

    p_gm.set_defaults(func=cmd_goto_and_mill)

    return p


def main(argv=None, prog_name: str = "pyfib") -> int:
    p = build_parser(prog_name=prog_name)
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
