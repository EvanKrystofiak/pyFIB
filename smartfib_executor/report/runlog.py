"""Run log for mill batches: what actually happened at each site.

Every ARMED ``mill-positions`` run writes

    <session>/runs/run_<YYYYmmdd_HHMMSS>.json   what happened, site by site
    <session>/runs/run_<YYYYmmdd_HHMMSS>.log    a copy of the console output

The JSON is saved after every step (atomic replace), so a stop or a crash keeps the
sites already done; a run killed outright is left with status "running", which the
report shows as "ended without a final status".

SAFETY: the log must never change what the microscope does. Every public method
catches its own errors; the first failed write prints ONE warning and milling
carries on. Nothing in this module talks to SmartSEM.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "pyfib.runlog.v1"
RUNS_DIRNAME = "runs"


def _now_iso(t: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(time.time() if t is None else t).astimezone()
    return dt.isoformat(timespec="seconds")


def _um(v) -> Optional[float]:
    try:
        f = float(v) * 1e6
        return round(f, 4) if math.isfinite(f) else None
    except Exception:  # noqa: BLE001
        return None


def _um_pair(p) -> Optional[List[Optional[float]]]:
    try:
        return [_um(p[0]), _um(p[1])]
    except Exception:  # noqa: BLE001
        return None


def _fnum(v, nd=4) -> Optional[float]:
    try:
        f = float(v)
        return round(f, nd) if math.isfinite(f) else None
    except Exception:  # noqa: BLE001
        return None


def file_sha256(path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return None


def refine_summary(res) -> Optional[Dict[str, Any]]:
    """Plain-JSON summary of a RefineResult (None if refine didn't run)."""
    if res is None:
        return None
    return {
        "success": bool(getattr(res, "success", False)),
        "method": getattr(res, "method", None),
        "initial_drift_um": [_um(getattr(res, "initial_drift_x_m", None)),
                             _um(getattr(res, "initial_drift_y_m", None))],
        "residual_um": _um(getattr(res, "residual_drift_m", None)),
        "beam_shift_delta_um": _um_pair(getattr(res, "applied_beam_shift_delta_m", (0, 0))),
        "stage_delta_um": _um_pair(getattr(res, "applied_stage_delta_m", (0, 0))),
        "coarse_fallback_tried": bool(getattr(res, "coarse_fallback_tried", False)),
        "coarse_stage_delta_um": _um_pair(getattr(res, "coarse_stage_delta_m", (0, 0))),
        "snr": _fnum(getattr(res, "snr", None), 2),
        "low_confidence": bool(getattr(res, "low_confidence", False)),
        "stage_refused": bool(getattr(res, "stage_refused", False)),
        "stage_required_but_disabled": bool(getattr(res, "stage_required_but_disabled", False)),
        "frames": getattr(res, "frames", None),
        "message": str(getattr(res, "message", "") or ""),
    }


def autofocus_summary(res) -> Optional[Dict[str, Any]]:
    """Plain-JSON summary of an AutofocusResult (None if autofocus didn't run)."""
    if res is None:
        return None
    return {
        "success": bool(getattr(res, "success", False)),
        "initial_voltage": _fnum(getattr(res, "initial_voltage", None), 2),
        "best_voltage": _fnum(getattr(res, "best_voltage", None), 2),
        "frames": getattr(res, "n_frames", None),
        "low_confidence": bool(getattr(res, "low_confidence", False)),
        "message": str(getattr(res, "message", "") or ""),
    }


class _Tee:
    """Writes to the real stream first, then (best effort) to the console copy."""

    def __init__(self, stream, sink, on_error):
        self._stream = stream
        self._sink = sink
        self._on_error = on_error

    def write(self, s):
        n = self._stream.write(s)
        if self._sink is not None:
            try:
                self._sink.write(s)
                if "\n" in s:
                    self._sink.flush()
            except Exception as e:  # noqa: BLE001
                self._sink = None
                self._on_error(f"console copy stopped: {e}")
        return n

    def flush(self):
        self._stream.flush()
        if self._sink is not None:
            try:
                self._sink.flush()
            except Exception:  # noqa: BLE001
                pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


class RunLog:
    """Structured record of one batch run. Never raises from its public methods."""

    def __init__(self, session_dir, *, kind: str = "mill",
                 stage: Optional[str] = None,
                 positions_file: Optional[str] = None,
                 standard_pattern: Optional[str] = None,
                 positions: Optional[List[str]] = None,
                 options: Optional[Dict[str, Any]] = None,
                 microscope_serial: Optional[str] = None,
                 clock: Callable[[], float] = time.time,
                 printer: Callable[[str], None] = print):
        self._clock = clock
        self._print = printer
        self._warned = False
        self._site: Optional[Dict[str, Any]] = None
        self._sink = None
        self._old_streams = None
        self.session_dir = Path(session_dir)
        self.path: Optional[Path] = None
        self.console_path: Optional[Path] = None
        try:
            from .. import __version__ as pyfib_version
        except Exception:  # noqa: BLE001
            pyfib_version = None
        t0 = self._clock()
        self.data: Dict[str, Any] = {
            "schema": SCHEMA,
            "kind": kind,
            "stage": stage,                 # v0.51.0: rough | prepolish | polish | None
            "status": "running",
            "message": None,
            "started": _now_iso(t0),
            "started_unix_s": t0,
            "finished": None,
            "pyfib_version": pyfib_version,
            "host": platform.node(),
            "microscope_serial": microscope_serial,
            "positions_file": positions_file,
            "standard_pattern": self._rel(standard_pattern) if standard_pattern else None,
            "standard_pattern_sha256": file_sha256(standard_pattern) if standard_pattern else None,
            "positions_requested": list(positions or []),
            "options": dict(options or {}),
            "argv": list(sys.argv),
            "sites": [],
        }
        try:
            runs = self.session_dir / RUNS_DIRNAME
            runs.mkdir(parents=True, exist_ok=True)
            stem = "run_" + datetime.fromtimestamp(t0).strftime("%Y%m%d_%H%M%S")
            p = runs / f"{stem}.json"
            k = 2
            while p.exists():
                p = runs / f"{stem}_{k}.json"
                k += 1
            self.path = p
            self.console_path = p.with_suffix(".log")
            self._save()
        except Exception as e:  # noqa: BLE001
            self._warn(f"could not create the run log: {e}")

    # ------------------------------------------------------------------ helpers
    def _warn(self, msg: str) -> None:
        if not self._warned:
            self._warned = True
            try:
                self._print(f"*** WARNING: run log: {msg} -- milling continues; this run "
                            f"may be missing from the report. ***")
            except Exception:  # noqa: BLE001
                pass

    def _rel(self, p) -> Optional[str]:
        if p is None:
            return None
        try:
            return os.path.relpath(str(Path(p).resolve()), str(self.session_dir.resolve()))
        except Exception:  # noqa: BLE001
            return str(p)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
            os.replace(str(tmp), str(self.path))
        except Exception as e:  # noqa: BLE001
            self._warn(f"could not write {self.path}: {e}")

    def _guard(fn):  # noqa: N805 -- decorator: public methods never raise
        def wrapper(self, *a, **k):
            try:
                return fn(self, *a, **k)
            except Exception as e:  # noqa: BLE001
                self._warn(f"{fn.__name__} failed: {e}")
                return None
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    # ------------------------------------------------------------------ console
    @_guard
    def start_console(self) -> None:
        """Copy everything printed (stdout + stderr) to run_<...>.log."""
        if self.console_path is None or self._old_streams is not None:
            return
        self._sink = open(self.console_path, "w", encoding="utf-8", errors="replace")
        self._old_streams = (sys.stdout, sys.stderr)
        sys.stdout = _Tee(sys.stdout, self._sink, self._warn)
        sys.stderr = _Tee(sys.stderr, self._sink, self._warn)
        self.data["console_log"] = self.console_path.name
        self._save()

    @_guard
    def stop_console(self) -> None:
        if self._old_streams is not None:
            try:
                sys.stdout.flush(); sys.stderr.flush()
            except Exception:  # noqa: BLE001
                pass
            sys.stdout, sys.stderr = self._old_streams
            self._old_streams = None
        if self._sink is not None:
            try:
                self._sink.close()
            finally:
                self._sink = None

    # ------------------------------------------------------------------ sites
    @_guard
    def site_start(self, name: str, index: int, total: int, rec=None) -> None:
        t = self._clock()
        site: Dict[str, Any] = {"name": str(name), "index": int(index), "total": int(total),
                                "started": _now_iso(t), "started_unix_s": t,
                                "finished": None, "status": "running", "message": None}
        st = getattr(rec, "stage_at", None)
        if st is not None:
            def _mm(v):
                return None if v is None else _fnum(float(v) * 1e3, 5)
            site["stage_target"] = {
                "x_mm": _mm(getattr(st, "x_m", None)),
                "y_mm": _mm(getattr(st, "y_m", None)),
                "z_mm": _mm(getattr(st, "z_m", None)),
                "t_deg": _fnum(getattr(st, "t_deg", None), 3),
                "r_deg": _fnum(getattr(st, "r_deg", None), 3),
            }
        if rec is not None:
            site["objective_v"] = _fnum(getattr(rec, "fib_objective_potential_v", None), 2)
        self.data["sites"].append(site)
        self._site = site
        self._save()

    @_guard
    def site_set(self, **fields) -> None:
        if self._site is None:
            return
        self._site.update(fields)
        self._save()

    @_guard
    def site_autofocus(self, res) -> None:
        self.site_set(autofocus=autofocus_summary(res))

    @_guard
    def site_refine(self, res) -> None:
        self.site_set(refine=refine_summary(res))

    @_guard
    def site_pattern(self, path, source: str, message: str) -> None:
        self.site_set(pattern={"path": self._rel(path), "source": source,
                               "sha256": file_sha256(path), "message": message})

    @_guard
    def site_after_image(self, path, warning: Optional[str] = None) -> None:
        self.site_set(after_image=self._rel(path) if path is not None else None,
                      after_image_warning=warning)

    @_guard
    def site_end(self, status: str, message: Optional[str] = None) -> None:
        if self._site is None:
            return
        t = self._clock()
        self._site.update(status=str(status), message=message, finished=_now_iso(t),
                          finished_unix_s=t,
                          duration_s=_fnum(t - float(self._site.get("started_unix_s", t)), 1))
        self._site = None
        self._save()

    @_guard
    def tilt_check(self, live_deg: float, limit_deg: float, skipped) -> None:
        """v0.46.0 batch tilt limit: the live tilt at the start and the positions NOT
        milled because of it ({name: reason}); the report shows those as not milled."""
        skipped = dict(skipped or {})
        self.data["tilt_check"] = {"live_deg": _fnum(live_deg, 4), "limit_deg": float(limit_deg)}
        self._add_skipped(skipped)
        self._save()

    @_guard
    def stage_check(self, stage: str, skipped) -> None:
        """v0.51.0 milling stage of this run (rough | prepolish | polish) and the positions
        NOT milled because the previous stage's after image is missing ({name: reason})."""
        self.data["stage"] = str(stage)
        self._add_skipped(dict(skipped or {}))
        self._save()

    def _add_skipped(self, skipped) -> None:
        cur = [d for d in (self.data.get("skipped") or []) if d.get("name") not in skipped]
        self.data["skipped"] = cur + [{"name": str(n), "reason": str(r)}
                                      for n, r in skipped.items()]

    # ------------------------------------------------------------------ run
    @_guard
    def finish(self, status: str, message: Optional[str] = None) -> None:
        """Close the run: "completed" | "stopped" | "error". Also ends the console copy."""
        if self._site is not None:                  # a site was cut off mid-way
            self.site_end("stopped" if status == "stopped" else "error",
                          message or f"run ended ({status})")
        t = self._clock()
        self.data.update(status=str(status), message=message, finished=_now_iso(t),
                         finished_unix_s=t)
        self._save()
        self.stop_console()
        if self.path is not None and not self._warned:
            self._print(f"Run log: {self.path}")

    del _guard


def load_run_logs(session_dir) -> List[Dict[str, Any]]:
    """All run logs under <session>/runs, oldest first. Unreadable files are reported
    as {"_error": ...} entries rather than raising."""
    out: List[Dict[str, Any]] = []
    runs = Path(session_dir) / RUNS_DIRNAME
    if not runs.is_dir():
        return out
    for p in sorted(runs.glob("run_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("schema") != SCHEMA:
                out.append({"_file": p.name, "_error": f"unknown schema {d.get('schema')!r}"})
                continue
            d["_file"] = p.name
            out.append(d)
        except Exception as e:  # noqa: BLE001
            out.append({"_file": p.name, "_error": str(e)})
    out.sort(key=lambda d: d.get("started_unix_s") or 0)
    return out
