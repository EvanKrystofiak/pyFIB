"""Shared SEM abstraction for pyFIB -- the reconciled interface + one mock.

We ADOPT gisdep's cleaner API design as pyFIB's canonical SEM abstraction:
  * ``SmartSEMInterface`` -- the typed contract (analogue vs digital vs digital-text),
  * ``PyfibMockSEM`` -- ONE mock that pyFIB's forks can share, replacing the ~6
    hand-rolled fakes that each re-modelled stage / WD / EHT / valves slightly
    differently.

``PyfibMockSEM`` EXTENDS gisdep's already-rich ``MockSmartSEM`` (which models the
WD<->Z coupling and the premature-Idle autofocus behaviour), adding the extra reads
and commands pyFIB touches (EHT, DP_FROZEN, scan rate, FIB/column valves, the
AP_STAGE_AT_* reads, image grab). It also exposes a pyFIB-legacy compat surface
(get_float / get_str / set_value / grab_bmp), so a fork can move to the shared mock
BEFORE its own calls are migrated to the analogue/digital interface -- letting the
reconciliation proceed one fork at a time with tests green throughout.

pyFIB's own ``SmartSEM`` now implements the interface too (see smartsem_com.py), so
the real connection and the mock share one contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

# Canonical interface: adopt gisdep's (single source of truth now that gisdep is
# vendored into pyFIB). This module is the ONE seam that imports it, so if the
# interface is ever relocated, only this file changes.
from gisdep.api.interface import (  # noqa: F401
    SmartSEMInterface, StagePosition, SmartSEMError,
)
from gisdep.api.mock import MockSmartSEM


class PyfibMockSEM(MockSmartSEM):
    """One shared mock for pyFIB forks -- gisdep's mock + pyFIB's extra mnemonics.

    Adds (beyond gisdep's MockSmartSEM):
      reads   : AP_ACTUALKV, AP_STAGE_AT_{X,Y,Z,T,R,M}, DP_FROZEN, DP_SCANRATE,
                DP_FIB_GUN_VALVE, DP_COLUMN_CHAMBER_VALVE
      writes  : records AP_MANUALKV / AP_IPROBE (and any AP_*), DP_* passthrough
      actions : CMD_EHT_OFF, CMD_CLOSE_COL_CHAMBER_VALVE, CMD_FIB_GUN_VALVE_{CLOSE,
                OPEN}, CMD_UNFREEZE_ZONE, CMD_FREEZE_{ZONE,ALL}, CMD_FIB_MODE_*,
                CMD_SCANRATE<n>
      image   : grab_bmp (writes a small BMP)
      compat  : get_float / get_str / set_value (pyFIB-legacy names)
    Inherits from gisdep: the WD<->Z coupling, premature-Idle DP_AUTO_FUNCTION,
    DP_STAGE_IS, stage soft limits, and GIS valve/needle modelling.
    """

    def __init__(self, *, eht_v: float = 2000.0, frozen: bool = True,
                 scan_rate: int = 4, fib_valve: int = 1, column_valve: int = 1,
                 fib_probe_idx: int = 6, fib_probe_a: float = 5.0e-11,
                 **kwargs):
        super().__init__(**kwargs)
        self.eht_v = float(eht_v)
        self.frozen = bool(frozen)
        self.scan_rate = int(scan_rate)
        self.fib_valve = int(fib_valve)         # 0 closed, 1 open, 3 transit
        self.column_valve = int(column_valve)
        self.fib_probe_idx = int(fib_probe_idx)
        self.fib_probe_a = float(fib_probe_a)
        self.grabbed = []                        # paths grab_bmp wrote
        self.sets = {}                           # params explicitly set via set_*
        # Ensure a connection so the inherited _require_init() guard passes.
        try:
            self.initialise()
        except Exception:
            pass

    # --- reads (override; fall through to gisdep's mock) ---
    def get_analogue(self, param: str) -> float:
        if param == "AP_ACTUALKV":
            return self.eht_v
        if param == "AP_FIB_PROBE_CURRENT":
            return self.fib_probe_a
        if param.startswith("AP_STAGE_AT_"):
            axis = param.split("_")[-1].lower()
            sp = self.get_stage_position()
            return {"x": sp.x_m, "y": sp.y_m, "z": sp.z_m,
                    "t": sp.tilt_deg, "r": sp.rot_deg, "m": sp.m_m}[axis]
        return super().get_analogue(param)

    def get_digital(self, param: str) -> int:
        if param == "DP_FROZEN":
            return 1 if self.frozen else 0
        if param == "DP_SCANRATE":
            return self.scan_rate
        if param == "DP_FIB_GUN_VALVE":
            return self.fib_valve
        if param == "DP_COLUMN_CHAMBER_VALVE":
            return self.column_valve
        if param == "DP_FIB_PROBE":
            return self.fib_probe_idx
        return super().get_digital(param)

    def get_digital_text(self, param: str) -> str:
        if param == "DP_FROZEN":
            return "Frozen" if self.frozen else "Live"
        return super().get_digital_text(param)

    # --- writes ---
    def set_analogue(self, param: str, value: float) -> None:
        self.sets[param] = float(value)
        try:
            super().set_analogue(param, value)
        except SmartSEMError:
            # pyFIB sets targets gisdep's mock doesn't model (AP_MANUALKV, AP_IPROBE
            # ...). Record them so tests can assert.
            self._ap[param] = float(value)

    def set_digital(self, param, value) -> None:
        self.sets[param] = value
        try:
            super().set_digital(param, value)
        except Exception:
            self._dp[param] = value

    # --- actions ---
    def execute(self, command: str) -> None:
        if command == "CMD_EHT_OFF":
            self.eht_v = 0.0; self.executed.append(command); return
        if command == "CMD_CLOSE_COL_CHAMBER_VALVE":
            self.column_valve = 0; self.executed.append(command); return
        if command == "CMD_FIB_GUN_VALVE_CLOSE":
            self.fib_valve = 0; self.executed.append(command); return
        if command == "CMD_FIB_GUN_VALVE_OPEN":
            self.fib_valve = 1; self.executed.append(command); return
        if command == "CMD_UNFREEZE_ZONE":
            self.frozen = False; self.executed.append(command); return
        if command in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            self.frozen = True; self.executed.append(command); return
        if command.startswith("CMD_FIB_MODE_"):
            self.executed.append(command); return
        if command.startswith("CMD_SCANRATE"):
            try:
                self.scan_rate = int(command.replace("CMD_SCANRATE", ""))
            except ValueError:
                pass
            self.executed.append(command); return
        return super().execute(command)

    # --- stage: snap instantly (pyFIB forks expect immediate moves) ---
    def move_stage(self, x, y, z, t, r, m) -> None:
        dz = float(z) - self._stage.z_m
        self._stage = StagePosition(x_m=float(x), y_m=float(y), z_m=float(z),
                                    tilt_deg=float(t), rot_deg=float(r), m_m=float(m))
        self._stage_move = None                       # no in-flight move -> idle now
        if "AP_WD" in self._ap:                        # dWD/dZ coupling, applied now
            self._ap["AP_WD"] += dz * getattr(self, "_wd_per_z", -1.0)
        self.stage_moves.append((x, y, z, t, r, m))

    # --- image grab ---
    def grab_bmp(self, path, *, xoff=0, yoff=0, width=64, height=48, reduction=0):
        self.grabbed.append(str(path))
        try:
            from PIL import Image
            Image.new("L", (max(1, width), max(1, height)), 128).save(str(path))
        except Exception:
            try:
                Path(str(path)).write_bytes(b"BM")
            except Exception:
                pass

    # --- pyFIB-legacy compat surface (so un-migrated forks can use this mock) ---
    def get_float(self, mnemonic: str) -> float:
        if mnemonic.startswith("AP_"):
            return self.get_analogue(mnemonic)
        return float(self.get_digital(mnemonic))

    def get_str(self, mnemonic: str) -> str:
        return self.get_digital_text(mnemonic)

    def set_value(self, mnemonic: str, value) -> None:
        if mnemonic.startswith("AP_"):
            self.set_analogue(mnemonic, float(value))
        else:
            self.set_digital(mnemonic, value)
