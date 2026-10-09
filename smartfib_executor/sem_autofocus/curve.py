"""SEM focus-curve measurement (v0.47.0) -- data for the two-stage SEM autofocus.

Evan, 2026-10-05: autofocus worked but its range was small (+/-60 um) and it was slow
(scan speed 8, ~2 min). Proposed: a very coarse pass (0.2 mm steps, scan speed 4,
reduced window) then a fine pass (scan speed 5). Before building it we MEASURE:

  * coarse passes at LOW mag (default 250x and 500x, speeds 4 and 5): WD start
    +/-1 mm in 100 um steps -- is the in-focus band wide enough there for 200 um steps?
  * fine passes at 2000x (speeds 4, 5, 6): WD start +/-150 um in 15 um steps -- which
    scan speed still gives a clean curve?

It only measures. It never applies a "best" WD: every pass ends back at the starting
WD (verified by read-back), and mag, scan speed and the raster are restored after each
pass. Every frame is saved, with curve.csv (all metrics per frame, written as it goes),
a contact sheet per pass, curves.png and summary.json, so the run can be reviewed later.

Same guards as SEM autofocus (GuardedSem allow-list: only AP_WD inside 4-8 mm, AP_MAG,
the reduced-raster box, freeze/unfreeze, scan speed; no stage, no contrast/brightness,
no SmartSEM autofocus). Every WD set is read back, stage Z must not move, frames must
be fresh, and contrast/brightness/mag/imaging must stay put -- any of those stops the
whole run and puts the WD back.

    python -m smartfib_executor.sem_autofocus.curve            # DRY RUN: checks + plan + time
    python -m smartfib_executor.sem_autofocus.curve --arm      # measure (asks to confirm)
    python -m smartfib_executor.sem_autofocus.curve --simulate # offline demo
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..autofocus import metrics as _metrics
from . import guards as G
from .scope import (DEFAULT_FRAME_WAIT_S, DEFAULT_SETTLE_S, FakeClock, SemAutofocusAbort,
                    SemFocusScope)
from .search import preflight

FIRST_SETTLE_S = 2.0          # extra settle after the jump to a pass's first WD
BAD_FRAMES_MAX = 3            # consecutive blank/saturated frames that end a pass
BOX_TIME_FACTOR_HALF = 5.5 / 20.9   # measured 2026-10-01: 0.5 box vs full frame, speed 8
METRIC_COLS = sorted(_metrics.METRICS)


@dataclass
class CurvePass:
    label: str
    mag: Optional[float]
    scan_rate: int
    half_range_m: float
    step_m: float
    raster_frac: Optional[float] = G.RASTER_FRAC_DEFAULT


@dataclass
class PassResult:
    label: str
    mag: Optional[float]
    scan_rate: int
    step_um: float
    half_range_um: float
    n_frames: int = 0
    frame_time_s: Optional[float] = None
    grab_s_median: Optional[float] = None
    error: Optional[str] = None
    bad_frames: int = 0
    stats: Dict[str, Dict[str, Optional[float]]] = field(default_factory=dict)
    contact_sheet: Optional[str] = None


@dataclass
class CurveRunResult:
    out_dir: str
    wd_start_m: Optional[float]
    passes: List[PassResult] = field(default_factory=list)
    stopped: Optional[str] = None
    wd_end_m: Optional[float] = None
    restore_failed: bool = False
    csv_path: Optional[str] = None
    plot_path: Optional[str] = None


def plan_wds(wd0: float, half_range_m: float, step_m: float,
             window: Tuple[float, float]) -> List[float]:
    """wd0 + k*step (k = -n..n), ascending, inside the window (never widened)."""
    if step_m <= 0 or half_range_m <= 0:
        raise ValueError("step and half-range must be > 0")
    n = int(math.floor(half_range_m / step_m + 1e-9))
    lo, hi = window
    return [round(wd0 + k * step_m, 9) for k in range(-n, n + 1)
            if lo <= wd0 + k * step_m <= hi]


def curve_stats(wds: List[float], scores: List[float], wd0: float) -> Dict[str, Optional[float]]:
    """Peak, contrast and in-focus band width of one curve (measured, no fit)."""
    if len(scores) < 3:
        return {"peak_wd_mm": None, "peak_offset_um": None, "contrast": None,
                "half_band_um": None, "edge_peak": None}
    s = np.asarray(scores, float); w = np.asarray(wds, float)
    i = int(np.argmax(s)); smax, smin = float(s.max()), float(s.min())
    contrast = (smax - smin) / smax if smax > 0 else 0.0
    half = smin + 0.5 * (smax - smin)
    # walk out from the peak while the score stays above half -> band edges
    a = i
    while a > 0 and s[a - 1] >= half:
        a -= 1
    b = i
    while b < len(s) - 1 and s[b + 1] >= half:
        b += 1
    band = (w[b] - w[a]) / 2.0 + (w[1] - w[0]) / 2.0 if len(w) > 1 else None
    return {"peak_wd_mm": round(float(w[i]) * 1e3, 5),
            "peak_offset_um": round((float(w[i]) - wd0) * 1e6, 1),
            "contrast": round(contrast, 3),
            "half_band_um": None if band is None else round(band * 1e6, 1),
            "edge_peak": bool(i == 0 or i == len(s) - 1)}


def estimate_frame_s(ft_now_s: Optional[float], rate_now: Optional[int], rate: int,
                     raster_frac: Optional[float]) -> Optional[float]:
    """Frame time at another scan speed: x2 per speed step (measured 2026-10-01), and the
    box factor measured for the 0.5 box (scaled by area for other boxes)."""
    if not ft_now_s or not rate_now:
        return None
    ft = ft_now_s * (2.0 ** (int(rate) - int(rate_now)))
    if raster_frac is not None and raster_frac < 1.0:
        ft *= BOX_TIME_FACTOR_HALF * (raster_frac / 0.5) ** 2
    return ft


def estimate_point_s(ft: Optional[float], settle_s: float, frame_wait_s: float) -> float:
    if not ft:
        return settle_s + 2.0 + 1.5
    return settle_s + max(frame_wait_s, 1.2 * ft + 0.3) + ft + 0.8


def default_passes(*, low_mags=(250.0, 500.0), low_speeds=(4, 5), low_half_um=1000.0,
                   low_step_um=100.0, fine_mag=2000.0, fine_speeds=(4, 5, 6),
                   fine_half_um=150.0, fine_step_um=15.0,
                   raster_frac: Optional[float] = G.RASTER_FRAC_DEFAULT) -> List[CurvePass]:
    out = []
    for m in low_mags:
        for s in low_speeds:
            out.append(CurvePass(f"coarse_{m:g}x_s{s}", float(m), int(s), low_half_um * 1e-6,
                                 low_step_um * 1e-6, raster_frac))
    if fine_mag:
        for s in fine_speeds:
            out.append(CurvePass(f"fine_{fine_mag:g}x_s{s}", float(fine_mag), int(s),
                                 fine_half_um * 1e-6, fine_step_um * 1e-6, raster_frac))
    return out


# --------------------------------------------------------------------------- run
def measure_curves(target, passes: List[CurvePass], out_dir: str, *,
                   settle_s: float = DEFAULT_SETTLE_S, frame_wait_s: float = DEFAULT_FRAME_WAIT_S,
                   wd_tol_m: float = G.WD_READBACK_TOL_M, first_settle_s: float = FIRST_SETTLE_S,
                   clock=None, logger: Callable[[str], None] = print) -> CurveRunResult:
    """Armed measurement. ``target`` is the SmartSEM connection (or the simulator)."""
    os.makedirs(out_dir, exist_ok=True)
    log = logger
    probe = SemFocusScope(target, magnification=None, scan_rate=None, raster_frac=None,
                          clock=clock, logger=log)
    wd0 = probe.read_wd()
    res = CurveRunResult(out_dir=out_dir, wd_start_m=wd0)
    if wd0 is None or not G.wd_in_window(wd0):
        res.stopped = f"start WD {G.mm(wd0)} is unreadable or outside 4-8 mm; nothing changed"
        return res
    res.csv_path = os.path.join(out_dir, "curve.csv")
    cols = (["pass", "mag", "scan_rate", "kind", "index", "wd_set_mm", "wd_read_mm",
             "offset_um", "live_to_frozen_s", "grab_s", "mean", "std", "bad_frame", "file"]
            + METRIC_COLS + ["tenengrad_smooth3"])
    fh = open(res.csv_path, "w", newline="", encoding="utf-8")
    wr = csv.DictWriter(fh, fieldnames=cols)
    wr.writeheader()
    per_pass_rows: Dict[str, List[dict]] = {}
    try:
        for p in passes:
            pr = PassResult(label=p.label, mag=p.mag, scan_rate=p.scan_rate,
                            step_um=round(p.step_m * 1e6, 3),
                            half_range_um=round(p.half_range_m * 1e6, 3))
            res.passes.append(pr)
            rows: List[dict] = []
            per_pass_rows[p.label] = rows
            pdir = os.path.join(out_dir, p.label)
            os.makedirs(pdir, exist_ok=True)
            scope = SemFocusScope(target, magnification=p.mag, scan_rate=p.scan_rate,
                                  settle_s=settle_s, frame_wait_s=frame_wait_s,
                                  wd_tol_m=wd_tol_m, raster_frac=p.raster_frac,
                                  clock=clock, logger=log)
            plan = plan_wds(wd0, p.half_range_m, p.step_m, scope.window)
            log(f"\n[curve] === {p.label}: mag {p.mag:g}x, scan speed {p.scan_rate}, "
                f"{len(plan)} WDs {G.mm(plan[0])} .. {G.mm(plan[-1])} "
                f"({p.step_m * 1e6:g} um steps) ===")
            prev = {"img": None}
            stop_run = None
            try:
                scope.prepare()
                pr.frame_time_s = scope.read_state().get("frame_time_s")
                wd_now = scope.read_wd()
                if wd_now is None or abs(wd_now - wd0) > scope.wd_tol_m:
                    raise SemAutofocusAbort(f"AP_WD reads {G.mm(wd_now)} after preparing the "
                                            f"pass, not the start {G.mm(wd0)}")
                bad_run = 0

                def shot(kind: str, idx: int, wd: float) -> None:
                    nonlocal bad_run
                    rb = scope.set_wd(wd)
                    fname = f"{kind}_{idx:02d}_wd{wd * 1e3:.4f}mm.bmp"
                    img = scope.grab(os.path.join(pdir, fname))
                    pr.n_frames += 1
                    if prev["img"] is not None and prev["img"].shape == img.shape and \
                            np.array_equal(prev["img"], img):
                        raise SemAutofocusAbort(f"frame at WD {G.mm(wd)} is identical to the "
                                                f"previous one (the scan did not refresh)")
                    prev["img"] = img
                    why = scope.check_unchanged()
                    if why:
                        raise _StopRun(why)
                    bad = G.frame_sanity(img, crop_bar=scope.frame_has_databar)
                    bad_run = bad_run + 1 if bad else 0
                    pr.bad_frames += 1 if bad else 0
                    crop = scope.frame_has_databar
                    ps = _metrics.DEFAULT_PRESMOOTH_FRAC * scope.presmooth_scale
                    sc = _metrics.all_scores(img, crop_bar=crop, presmooth_frac=ps)
                    sc3 = _metrics.sharpness(img, "tenengrad", crop_bar=crop,
                                             presmooth_frac=3 * ps)
                    lf, gs = scope.frame_log[-1] if scope.frame_log else (None, None)
                    row = {"pass": p.label, "mag": p.mag, "scan_rate": p.scan_rate,
                           "kind": kind, "index": idx, "wd_set_mm": round(wd * 1e3, 6),
                           "wd_read_mm": round(rb * 1e3, 6),
                           "offset_um": round((wd - wd0) * 1e6, 2),
                           "live_to_frozen_s": lf, "grab_s": gs,
                           "mean": round(float(img.mean()), 2),
                           "std": round(float(img.std()), 2), "bad_frame": bad or "",
                           "file": f"{p.label}/{fname}",      # portable (v0.48.0)
                           "tenengrad_smooth3": sc3}
                    row.update(sc)
                    rows.append(row)
                    wr.writerow(row); fh.flush()
                    log(f"[curve] {p.label} {kind} {idx:2d}: WD {G.mm(wd)} "
                        f"({(wd - wd0) * 1e6:+7.1f} um) tenengrad {sc['tenengrad']:.4g}"
                        + (f"  [{bad}]" if bad else ""))
                    if bad_run >= BAD_FRAMES_MAX:
                        raise SemAutofocusAbort(f"{bad_run} unusable frames in a row ({bad})")

                scope.set_wd(plan[0])
                if clock is not None:
                    clock.sleep(first_settle_s)
                else:
                    time.sleep(first_settle_s)
                shot("settle", 0, plan[0])            # throwaway after the big jump
                for i, wd in enumerate(plan, start=1):
                    shot("sweep", i, wd)
                shot("return", 0, wd0)                # repeatability at the start WD
            except _StopRun as e:
                pr.error = str(e)
                stop_run = str(e)
            except SemAutofocusAbort as e:
                pr.error = str(e)
                stop_run = str(e)        # a guard (read-back, stage Z, fresh frame): stop all
            except Exception as e:  # noqa: BLE001
                pr.error = f"{type(e).__name__}: {e}"
                stop_run = pr.error
            finally:
                try:
                    scope.set_wd(wd0)
                except Exception as e:  # noqa: BLE001
                    log(f"[curve] *** WARNING: could not confirm WD back at {G.mm(wd0)}: {e!r}")
                    res.restore_failed = True
                scope.restore()
                gl = [g for _l, g in scope.frame_log if g is not None]
                pr.grab_s_median = round(float(np.median(gl)), 3) if gl else None
                sw = [r for r in rows if r["kind"] == "sweep"]
                for m in METRIC_COLS + ["tenengrad_smooth3"]:
                    pr.stats[m] = curve_stats([r["wd_set_mm"] * 1e-3 for r in sw],
                                              [float(r[m]) for r in sw], wd0)
                ret = [r for r in rows if r["kind"] == "return"]
                at0 = [r for r in sw if abs(r["offset_um"]) < 1e-6]
                if ret and at0 and float(at0[0]["tenengrad"]) > 0:
                    pr.stats["tenengrad"]["return_ratio"] = round(
                        float(ret[0]["tenengrad"]) / float(at0[0]["tenengrad"]), 3)
                pr.contact_sheet = _contact_sheet(out_dir, p.label, rows)
                st = pr.stats.get("tenengrad", {})
                log(f"[curve] {p.label}: {pr.n_frames} frames, frame {pr.frame_time_s}, grab "
                    f"median {pr.grab_s_median} s; tenengrad peak {st.get('peak_offset_um')} um, "
                    f"contrast {st.get('contrast')}, half band {st.get('half_band_um')} um"
                    + (f"; STOPPED: {pr.error}" if pr.error else ""))
            if stop_run:
                res.stopped = f"{p.label}: {stop_run}"
                break
    finally:
        fh.close()
        res.wd_end_m = probe.read_wd()
        if res.wd_end_m is None or abs(res.wd_end_m - wd0) > wd_tol_m:
            res.restore_failed = True
        res.plot_path = _curves_plot(out_dir, per_pass_rows, wd0)
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(asdict(res), f, indent=1, default=str)
    return res


class _StopRun(SemAutofocusAbort):
    """Someone/something changed contrast, brightness, mag, stage Z or imaging."""


# --------------------------------------------------------------------------- pictures
def _contact_sheet(out_dir: str, label: str, rows: List[dict]) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return None
    rows = [r for r in rows if r["kind"] in ("sweep", "return")]
    if not rows:
        return None
    tw, cols = 192, 7
    tiles = []
    smax = max(float(r["tenengrad"]) for r in rows) or 1.0
    for r in rows:
        try:
            with Image.open(os.path.join(out_dir, *r["file"].replace("\\", "/").split("/"))) as im:
                g = im.convert("L")
                th = max(1, int(round(g.height * tw / g.width)))
                tiles.append((r, g.resize((tw, th))))
        except Exception:  # noqa: BLE001
            continue
    if not tiles:
        return None
    th = max(t.height for _r, t in tiles)
    lab = 30
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = Image.new("L", (cols * tw, rows_n * (th + lab) + 24), 255)
    d = ImageDraw.Draw(sheet)
    d.text((4, 4), f"{label}  (score = tenengrad / pass max)", fill=0)
    best = max(range(len(tiles)), key=lambda k: float(tiles[k][0]["tenengrad"]))
    for k, (r, t) in enumerate(tiles):
        x, y = (k % cols) * tw, 24 + (k // cols) * (th + lab)
        sheet.paste(t, (x, y))
        tag = "return " if r["kind"] == "return" else ""
        d.text((x + 3, y + th + 2), f"{tag}{float(r['offset_um']):+.0f} um", fill=0)
        d.text((x + 3, y + th + 15), f"{float(r['tenengrad']) / smax:.2f}"
               + ("  <- max" if k == best else ""), fill=0)
    path = os.path.join(out_dir, f"sheet_{label}.png")
    sheet.save(path)
    return path


def _curves_plot(out_dir: str, per_pass: Dict[str, List[dict]], wd0: float) -> Optional[str]:
    """Normalised tenengrad vs WD offset, one panel per offset scale (coarse / fine)."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return None
    groups: Dict[str, List[Tuple[str, List[dict]]]] = {}
    for lab, rows in per_pass.items():
        sw = [r for r in rows if r["kind"] == "sweep"]
        if len(sw) >= 2:
            groups.setdefault(lab.split("_")[0], []).append((lab, sw))
    if not groups:
        return None
    colours = [(0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167), (230, 159, 0),
               (86, 180, 233), (0, 0, 0)]
    W, Hp, m = 900, 360, 60
    img = Image.new("RGB", (W, Hp * len(groups)), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for gi, (gname, curves) in enumerate(sorted(groups.items())):
        y0 = gi * Hp
        xs = [float(r["offset_um"]) for _l, sw in curves for r in sw]
        xmin, xmax = min(xs), max(xs)
        if xmax <= xmin:
            xmax = xmin + 1

        def X(v):
            return m + (v - xmin) / (xmax - xmin) * (W - 2 * m)

        def Y(v):
            return y0 + Hp - m + (-(v) * (Hp - 2 * m))
        d.rectangle((m, y0 + m, W - m, y0 + Hp - m), outline=(120, 120, 120))
        d.text((m, y0 + 10), f"{gname}: tenengrad / pass max vs WD offset from start (um)",
               fill=(0, 0, 0))
        for v in (0.0, 0.5, 1.0):
            d.text((10, Y(v) - 6), f"{v:.1f}", fill=(0, 0, 0))
        for v in (xmin, 0.0, xmax):
            d.text((X(v) - 14, y0 + Hp - m + 6), f"{v:+.0f}", fill=(0, 0, 0))
        for ci, (lab, sw) in enumerate(curves):
            col = colours[ci % len(colours)]
            s = [float(r["tenengrad"]) for r in sw]
            smax = max(s) or 1.0
            pts = [(X(float(r["offset_um"])), Y(v / smax)) for r, v in zip(sw, s)]
            d.line(pts, fill=col, width=2)
            for x, y in pts:
                d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=col)
            d.text((W - m - 170, y0 + m + 6 + 14 * ci), lab, fill=col)
    path = os.path.join(out_dir, "curves.png")
    img.save(path)
    return path


# --------------------------------------------------------------------------- CLI
def _floats(s: str) -> List[float]:
    return [float(x) for x in str(s).split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m smartfib_executor.sem_autofocus.curve",
        description="Measure SEM focus curves (sharpness vs WD) at several mags and scan "
                    "speeds. Measures only: the WD always ends where it started.")
    p.add_argument("--arm", action="store_true", help="Actually step the WD and image.")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    p.add_argument("--simulate", action="store_true", help="Run on the built-in simulator.")
    p.add_argument("--low-mags", default="250,500", help="Coarse-pass mags (default 250,500).")
    p.add_argument("--low-speeds", default="4,5", help="Coarse-pass scan speeds (default 4,5).")
    p.add_argument("--low-half-range-um", type=float, default=1000.0)
    p.add_argument("--low-step-um", type=float, default=100.0)
    p.add_argument("--fine-mag", type=float, default=2000.0, help="0 = no fine passes.")
    p.add_argument("--fine-speeds", default="4,5,6", help="Fine-pass scan speeds (default 4,5,6).")
    p.add_argument("--fine-half-range-um", type=float, default=150.0)
    p.add_argument("--fine-step-um", type=float, default=15.0)
    p.add_argument("--raster-frac", type=float, default=G.RASTER_FRAC_DEFAULT,
                   help="Centred box, fraction of width/height (default 0.5).")
    p.add_argument("--full-frame", action="store_true", help="Scan the whole frame instead.")
    p.add_argument("--settle-s", type=float, default=DEFAULT_SETTLE_S)
    p.add_argument("--frame-wait-s", type=float, default=DEFAULT_FRAME_WAIT_S)
    p.add_argument("--wd-tol-um", type=float, default=G.WD_READBACK_TOL_M * 1e6)
    p.add_argument("--out", default=None, help="Output folder (default sem_af_curve_<time>).")
    p.add_argument("--sim-true-wd-mm", type=float, default=5.03)
    p.add_argument("--sim-start-wd-mm", type=float, default=5.00)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    for name, half in (("low", a.low_half_range_um), ("fine", a.fine_half_range_um)):
        if not 0 < half <= G.HALF_RANGE_MAX_M * 1e6:
            print(f"ERROR: --{name}-half-range-um must be in (0, {G.HALF_RANGE_MAX_M * 1e6:.0f}]")
            return 2
    frac = None if (a.full_frame or a.raster_frac >= 1.0) else a.raster_frac
    passes = default_passes(low_mags=_floats(a.low_mags), low_speeds=[int(x) for x in _floats(a.low_speeds)],
                            low_half_um=a.low_half_range_um, low_step_um=a.low_step_um,
                            fine_mag=a.fine_mag, fine_speeds=[int(x) for x in _floats(a.fine_speeds)],
                            fine_half_um=a.fine_half_range_um, fine_step_um=a.fine_step_um,
                            raster_frac=frac)
    for p in passes:
        if not 1 <= p.scan_rate <= 15:
            print(f"ERROR: scan speed {p.scan_rate} is outside 1-15")
            return 2
    sem, clock = None, None
    if a.simulate:
        from .sim import SimulatedSem
        target = SimulatedSem(true_wd_m=a.sim_true_wd_mm * 1e-3, wd_m=a.sim_start_wd_mm * 1e-3,
                              blur_per_um=0.02)
        clock = FakeClock()
    else:
        try:
            from ..smartsem_com import SmartSEM
            sem = SmartSEM()
            try:
                sem.connect_remoting()
            except Exception:
                sem.connect_local()
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: could not connect to SmartSEM: {e!r}", file=sys.stderr)
            return 2
        target = sem
    try:
        probe = SemFocusScope(target, magnification=None, scan_rate=None, raster_frac=None,
                              clock=clock, logger=print)
        warns: List[str] = []
        reasons, st, _plan = preflight(probe, half_range_m=max(p.half_range_m for p in passes),
                                       coarse_points=3, max_frames=10 ** 6, warnings=warns)
        wd0 = st.get("wd_m")
        rate_now = None if st.get("scan_rate") is None else int(round(st["scan_rate"]))
        print("SEM focus-curve measurement (measures only; the WD always ends at its start)")
        print(f"  start: WD {G.mm(wd0)}, mag {st.get('mag')}, scan speed {rate_now}, "
              f"frame {st.get('frame_time_s')} s, contrast {st.get('contrast_pct')} %")
        total = 0.0
        n_total = 0
        for p in passes:
            n = len(plan_wds(wd0, p.half_range_m, p.step_m, probe.window)) if wd0 else 0
            ft = estimate_frame_s(st.get("frame_time_s"), rate_now, p.scan_rate, p.raster_frac)
            t = (n + 2) * estimate_point_s(ft, a.settle_s, a.frame_wait_s) + FIRST_SETTLE_S + 3
            total += t; n_total += n + 2
            print(f"  {p.label:<20} {n:3d} WDs, +/-{p.half_range_m * 1e6:g} um in "
                  f"{p.step_m * 1e6:g} um steps, frame ~{'%.2f s' % ft if ft else '?'}, "
                  f"~{t / 60:.1f} min")
        print(f"  total: {n_total} frames, ~{total / 60:.0f} min "
              f"({'box ' + str(frac) if frac else 'full frame'})")
        for w in warns:
            print(f"  note: {w}")
        if reasons:
            print("REFUSED (nothing changed):")
            for r in reasons:
                print(f"  - {r}")
            return 2
        if not (a.arm or a.simulate):
            print("Nothing was changed. Add --arm to measure.")
            return 0
        if not a.simulate and not a.yes:
            print("This steps the SEM focus (AP_WD) and changes SEM mag and scan speed for each "
                  "pass (restored after each). Contrast, brightness and the stage are never "
                  "touched. The WD ends where it started.")
            try:
                ans = input("Proceed? [yes/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                print("Cancelled; nothing changed.")
                return 2
        out_dir = os.path.abspath(a.out or time.strftime("sem_af_curve_%Y%m%d_%H%M%S"))
        res = measure_curves(target, passes, out_dir, settle_s=a.settle_s,
                             frame_wait_s=a.frame_wait_s, wd_tol_m=a.wd_tol_um * 1e-6,
                             clock=clock, logger=print)
        print("\n=== focus curves (tenengrad) ===")
        for pr in res.passes:
            stt = pr.stats.get("tenengrad", {})
            print(f"  {pr.label:<20} peak {stt.get('peak_offset_um')} um, contrast "
                  f"{stt.get('contrast')}, half band {stt.get('half_band_um')} um, return "
                  f"{stt.get('return_ratio')}, frame {pr.frame_time_s} s, grab "
                  f"{pr.grab_s_median} s" + (f"  STOPPED: {pr.error}" if pr.error else ""))
        print(f"WD at the end: {G.mm(res.wd_end_m)} (start {G.mm(res.wd_start_m)})")
        print(f"Saved to {res.out_dir}  (zip this folder and send it back)")
        if res.restore_failed:
            print("*** WARNING: the WD could not be confirmed back at its start value. "
                  "Check SEM focus by hand. ***")
            return 3
        return 1 if res.stopped else 0
    finally:
        if sem is not None:
            try:
                sem.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
