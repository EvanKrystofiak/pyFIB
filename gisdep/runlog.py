"""Structured per-run logging -- a paper trail for every deposition / full run.

Wrap an operation in a RunLog context manager. It captures start/end timestamps, the
parameters, a state snapshot (WD, stage position + tilt, GIS needle/valve) at start and
end, the gisdep log lines emitted during the run, the result summary you set, and the
outcome (completed / aborted / error). On exit it writes a timestamped JSON file and
appends a one-line summary to runs.csv.

    with RunLog("deposition", {"port": 1, "dwell": 2}, api=api) as rl:
        res = deposit(api, 1, 2.0, ...)
        rl.set_result({"message": res.message, "completed": res.completed})
    print("logged to", rl.path)

Logging never raises: a write failure is logged and swallowed so it can't break a run.
The directory defaults to ./gisdep_runs (override via logdir= or $GISDEP_RUNLOG_DIR).
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

_log = logging.getLogger("gisdep.runlog")

# exception type names treated as a clean "aborted" (vs "error")
_ABORT_NAMES = {
    "FullRunAborted", "OperatorDeclined", "StageMoveDeclined", "NotSafeToStartError",
    "StageAborted", "GISAborted", "DepositionAborted",
}


class _ListHandler(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self._sink = sink

    def emit(self, record):
        try:
            self._sink.append(self.format(record))
        except Exception:
            pass


def _default_logdir() -> Path:
    return Path(os.environ.get("GISDEP_RUNLOG_DIR", "gisdep_runs"))


class RunLog:
    def __init__(self, operation, params=None, api=None, logdir=None):
        self.operation = operation
        self.params = dict(params or {})
        self.api = api
        self.logdir = Path(logdir) if logdir else _default_logdir()
        self.result = {}
        self.path = None
        self.record = None
        self._lines = []
        self._handler = None

    # --- lifecycle ---------------------------------------------------------
    def __enter__(self):
        self.started = datetime.now()
        self.run_id = self.started.strftime("%Y%m%d-%H%M%S") + f"-{self.operation}"
        self._state_start = self._snapshot()
        self._handler = _ListHandler(self._lines)
        self._handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self._logger = logging.getLogger("gisdep")
        self._prev_level = self._logger.level
        if self._prev_level == logging.NOTSET or self._prev_level > logging.INFO:
            self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        return self

    def set_result(self, d):
        self.result.update(d)

    def __exit__(self, exc_type, exc, tb):
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._logger.setLevel(self._prev_level)
        finished = datetime.now()
        if exc_type is None:
            outcome, error = "completed", None
        elif exc_type.__name__ in _ABORT_NAMES:
            outcome, error = "aborted", f"{exc_type.__name__}: {exc}"
        else:
            outcome, error = "error", f"{exc_type.__name__}: {exc}"

        self.record = {
            "run_id": self.run_id,
            "operation": self.operation,
            "started": self.started.isoformat(timespec="seconds"),
            "finished": finished.isoformat(timespec="seconds"),
            "duration_s": round((finished - self.started).total_seconds(), 2),
            "params": self.params,
            "outcome": outcome,
            "error": error,
            "state_start": self._state_start,
            "state_end": self._snapshot(),
            "result": self.result,
            "log": self._lines,
        }
        self._write(self.record)
        return False  # never suppress the exception

    # --- helpers -----------------------------------------------------------
    def _snapshot(self):
        if self.api is None:
            return {}
        snap = {}
        try:
            snap["wd_mm"] = round(self.api.get_analogue("AP_WD") * 1e3, 4)
        except Exception:
            pass
        try:
            p = self.api.get_stage_position()
            snap["stage"] = {
                "x_mm": round(p.x_m * 1e3, 4), "y_mm": round(p.y_m * 1e3, 4),
                "z_mm": round(p.z_m * 1e3, 4), "tilt_deg": round(p.tilt_deg, 3),
                "rot_deg": round(p.rot_deg, 3), "m_mm": round(p.m_m * 1e3, 4),
            }
        except Exception:
            pass
        try:
            from gisdep.gis import read_all
            r = read_all(self.api)
            snap["gis"] = {
                port: {"needle": rd.needle_meaning, "valve": rd.valve_meaning}
                for port, rd in r.items()
            }
        except Exception:
            pass
        return snap

    def _write(self, record):
        try:
            self.logdir.mkdir(parents=True, exist_ok=True)
            self.path = self.logdir / f"{self.run_id}.json"
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
            self._append_csv(record)
            _log.info("run logged: %s", self.path)
        except Exception as exc:  # logging must never break a run
            _log.warning("failed to write run log: %s", exc)
            self.path = None

    def _append_csv(self, record):
        csv_path = self.logdir / "runs.csv"
        new = not csv_path.exists()
        params = record["params"]
        row = {
            "run_id": record["run_id"], "operation": record["operation"],
            "started": record["started"], "duration_s": record["duration_s"],
            "outcome": record["outcome"],
            "grid": params.get("grid", ""), "port": params.get("port", ""),
            "dwell_s": params.get("dwell", params.get("dwell_s", "")),
            "wd_start_mm": record["state_start"].get("wd_mm", ""),
            "wd_end_mm": record["state_end"].get("wd_mm", ""),
            "error": record["error"] or "",
        }
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
