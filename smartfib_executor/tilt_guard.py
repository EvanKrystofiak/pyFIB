"""Batch tilt limit (v0.46.0).

Evan, 2026-10-05: tilt must be recorded, but recalling tilts that are far apart is
dangerous. Rule for MILL BATCHES ONLY (setup, GISDEP, goto-position and every other
stage move are unchanged):

  * A position is milled only if its recorded tilt is within BATCH_TILT_LIMIT_DEG (2 deg)
    of the tilt the stage is at when the batch starts, AND no single recall in the
    batch changes tilt by more than 2 deg (checked in milling order, from the live tilt).
  * Positions outside that are reported up front and NOT milled. The batch runs only
    after the user OKs that list (the GUI asks in a dialog; the CLI asks at a terminal or
    takes --accept-tilt-skips NAMES). Large tilt changes stay manual / CLEM SAFE.
  * A position with no recorded tilt is fine: its recall holds the current tilt.

Everything here is pure except read_live_tilt (one read, AP_STAGE_AT_T).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

BATCH_TILT_LIMIT_DEG = 2.0
_EPS = 1e-6


class TiltLimitError(RuntimeError):
    """A batch would recall a tilt outside the limit and nobody OKed skipping it."""


@dataclass
class TiltPlan:
    live_deg: float
    limit_deg: float
    # (name, recorded tilt or None, allowed, reason)
    rows: List[Tuple[str, Optional[float], bool, str]] = field(default_factory=list)

    @property
    def allowed(self) -> List[str]:
        return [n for n, _t, ok, _r in self.rows if ok]

    @property
    def excluded(self) -> Dict[str, str]:
        return {n: r for n, _t, ok, r in self.rows if not ok}

    def table_lines(self) -> List[str]:
        out = [f"[tilt] current stage tilt {self.live_deg:.2f} deg; batch limit "
               f"{self.limit_deg:g} deg (recorded tilt vs current, and per recall)"]
        for n, t, ok, r in self.rows:
            ts = "   --  " if t is None else f"{t:7.2f}"
            out.append(f"[tilt]   {n:<24} {ts} deg  {'mill' if ok else 'SKIP'}"
                       + ("" if ok and not r else f"  ({r})"))
        ex = self.excluded
        if ex:
            out.append(f"[tilt] {len(ex)} position(s) will NOT be milled: {', '.join(ex)}")
        return out

    def machine_line(self) -> str:
        """One parseable line for the GUI."""
        return (f"TILT-CHECK: live={self.live_deg:.4f} limit={self.limit_deg:g} "
                f"allowed={','.join(self.allowed)} excluded={','.join(self.excluded)}")


def plan_batch_tilts(names: Iterable[str], recorded: Dict[str, Optional[float]],
                     live_deg: float, limit_deg: float = BATCH_TILT_LIMIT_DEG) -> TiltPlan:
    """Which positions a batch may recall, in milling order (module docstring)."""
    live = float(live_deg)
    plan = TiltPlan(live_deg=live, limit_deg=float(limit_deg))
    cur = live
    for n in names:
        t = recorded.get(n)
        if t is None:
            plan.rows.append((n, None, True, "no recorded tilt: recall holds the current tilt"))
            continue
        t = float(t)
        if abs(t - live) > limit_deg + _EPS:
            plan.rows.append((n, t, False, f"{abs(t - live):.2f} deg from the current "
                                          f"{live:.2f} deg"))
        elif abs(t - cur) > limit_deg + _EPS:
            plan.rows.append((n, t, False, f"{abs(t - cur):.2f} deg from the previous "
                                          f"site's {cur:.2f} deg"))
        else:
            plan.rows.append((n, t, True, ""))
            cur = t
    return plan


def parse_machine_line(line: str) -> Optional[dict]:
    """Inverse of TiltPlan.machine_line (None if the line isn't one)."""
    s = line.strip()
    if not s.startswith("TILT-CHECK:"):
        return None
    out: dict = {}
    for part in s[len("TILT-CHECK:"):].split():
        k, _, v = part.partition("=")
        if k in ("live", "limit"):
            out[k] = float(v)
        elif k in ("allowed", "excluded"):
            out[k] = [x for x in v.split(",") if x]
    for k in ("allowed", "excluded"):
        out.setdefault(k, [])
    return out if "live" in out else None


def read_live_tilt(sem) -> float:
    """Current stage tilt (deg) from AP_STAGE_AT_T; raises if it can't be read."""
    try:
        t = float(sem.get_float("AP_STAGE_AT_T"))
    except Exception as e:  # noqa: BLE001
        raise TiltLimitError(f"current stage tilt (AP_STAGE_AT_T) could not be read ({e!r}); "
                             f"the batch tilt check needs it, so nothing will move") from e
    if not math.isfinite(t):
        raise TiltLimitError(f"current stage tilt reads {t!r}; nothing will move")
    return t
