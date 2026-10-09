"""SEM access for the Prep CryoFIBSEM sequence.

Safety model (same as the coincidence tool):
  * DRY-RUN BY DEFAULT. Every mutating operation (execute a command, set a value,
    move the stage) is only LOGGED unless the caller passes armed=True. Reads always
    happen. So a plain run prints exactly what it would do and touches nothing.
  * --simulate uses SimulatedPrepSem (no hardware) for offline demo/tests.

MNEMONICS -- now CONFIRMED on this instrument (operator-verified):
  * SEM EHT off        : CMD_EHT_OFF
  * SEM EHT actual     : AP_ACTUALKV        (must be <= 50 V ...)
  * SEM Beam State     : DP_RUNUPSTATE      (... AND read "EHT Off", both held 3 s,
                         before any valve; from the API manual 6.2.3 -- the log prints
                         every state seen, to confirm the names on the instrument)
  * column valve close : CMD_CLOSE_COL_CHAMBER_VALVE ; state DP_COLUMN_CHAMBER_VALVE
                         (0 closed, 1 open, transit/unknown otherwise)
  * FIB gun valve      : DP_FIB_GUN_VALVE (0 closed, 1 open, transit otherwise);
                         CMD_FIB_GUN_VALVE_CLOSE closes it. CMD_FIB_GUN_VALVE_OPEN
                         OPENS it and is never used here. We CLOSE unless the read is
                         confirmed 0, so open OR transit both get closed.
  * SEM EHT target     : AP_MANUALKV        (2000 V; Zeiss shows 2.00e+03)
  * SEM probe current  : AP_IPROBE          (set the NUMBER 20e-12; text form "20 pA")
  * FIB probe (report) : DP_FIB_PROBE (enum) and AP_FIB_PROBE_CURRENT (amperes)
"""

from __future__ import annotations

from typing import Callable, Optional

from ..instrument import PyfibMockSEM


class PrepScope:
    """Thin wrapper that gates mutating ops on ``armed`` and logs everything."""

    def __init__(self, sem, *, armed: bool = False,
                 logger: Optional[Callable[[str], None]] = None):
        self._sem = sem
        self.armed = bool(armed)
        self._log = logger or (lambda m: None)

    # --- reads (always real) ---
    def get_float(self, mnemonic: str) -> Optional[float]:
        try:
            return float(self._sem.get_float(mnemonic))
        except Exception:
            return None

    def get_str(self, mnemonic: str) -> Optional[str]:
        try:
            v = self._sem.get_str(mnemonic)
            return str(v).strip() if v is not None else None
        except Exception:
            return None

    # --- mutations (gated on armed) ---
    def execute(self, command: str) -> None:
        if not self.armed:
            self._log(f"    [dry-run] would execute {command}")
            return
        self._log(f"    execute {command}")
        self._sem.execute(command)

    def set_value(self, mnemonic: str, value: float) -> None:
        if not self.armed:
            self._log(f"    [dry-run] would set {mnemonic} = {value:g}")
            return
        self._log(f"    set {mnemonic} = {value:g}")
        self._sem.set_value(mnemonic, value)

    def move_stage(self, x, y, z, t, r, m, *, note: str = "") -> None:
        desc = (f"x={x * 1e3:.4f} y={y * 1e3:.4f} z={z * 1e3:.4f} mm, "
                f"t={t:.3f} r={r:.3f} deg, m={m * 1e3:.4f} mm")
        if not self.armed:
            self._log(f"    [dry-run] would move stage ({note}): {desc}")
            return
        self._log(f"    move stage ({note}): {desc}")
        self._sem.move_stage(x, y, z, t, r, m)


class SimulatedPrepSem(PyfibMockSEM):
    """Offline fake for prep_cryo -- now the SHARED PyfibMockSEM.

    The bespoke re-implementation is gone; this is a thin subclass that adds the
    dict/list views prep_cryo's tests read (``stage``, ``commands``, ``set_values``),
    translating from PyfibMockSEM's canonical state. Same constructor kwargs
    (eht_v / column_valve / fib_valve / fib_probe_idx / fib_probe_a).
    """

    @property
    def stage(self) -> dict:
        sp = self.get_stage_position()
        return {"x": sp.x_m, "y": sp.y_m, "z": sp.z_m,
                "t": sp.tilt_deg, "r": sp.rot_deg, "m": sp.m_m}

    @property
    def commands(self) -> list:
        return self.executed

    @property
    def set_values(self) -> dict:
        return self.sets
