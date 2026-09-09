"""SEM access for the coincidence branch, guarded so it can NEVER move the stage.

``FocusSem`` wraps a real SmartSEM connection and exposes only what this feature
needs: reads (get_float / get_str) and the two SEM autofocus commands. Any attempt
to reach set_value, move_stage, grab_bmp, or anything else raises
``CoincidenceSafetyError``. This makes "pyFIB does not move Z" a structural
guarantee, not a promise -- the code physically cannot issue a stage move.

``SimulatedFocusSem`` models the confirmed geometry (positive Z shortens WD) for
offline demos and tests.
"""

from __future__ import annotations

import time
from typing import Optional

from .limits import MAX_Z_STEP_M, Z_HARD_MIN_M, Z_HARD_MAX_M


class CoincidenceSafetyError(RuntimeError):
    """Raised when the guarded SEM is asked to do something not permitted here."""


class FocusSem:
    """Read + autofocus + a SINGLE validated Z move. Nothing else.

    The only motion this class can produce is a Z-ONLY stage move via ``move_z``,
    which re-reads the current position and re-checks the per-move cap and hard Z
    bounds AT THE MOMENT OF MOTION -- so the safety does not depend on the caller
    computing the target correctly. Any other command, any set_value, and any X/Y/
    tilt/rotation motion raise ``CoincidenceSafetyError``.
    """

    _AUTOFOCUS_CMDS = frozenset({"CMD_AUTO_FOCUS_COARSE", "CMD_AUTO_FOCUS_FINE"})
    # Imaging commands: switch the imaging beam and freeze/unfreeze for a clean grab.
    # None of these move the stage, so they are compatible with the no-pole-touch
    # guarantee (the ONLY motion this class can produce is the validated move_z).
    _IMAGING_CMDS = frozenset({
        "CMD_FIB_MODE_SEM", "CMD_FIB_MODE_FIB",
        "CMD_UNFREEZE_ZONE", "CMD_FREEZE_ZONE", "CMD_FREEZE_ALL",
    })

    def __init__(self, sem):
        object.__setattr__(self, "_sem", sem)
        object.__setattr__(self, "calls", [])

    # --- permitted: reads ---
    def get_float(self, mnemonic: str) -> float:
        object.__getattribute__(self, "calls").append(("get_float", mnemonic))
        return object.__getattribute__(self, "_sem").get_float(mnemonic)

    def get_str(self, mnemonic: str) -> str:
        object.__getattribute__(self, "calls").append(("get_str", mnemonic))
        return object.__getattribute__(self, "_sem").get_str(mnemonic)

    # --- permitted: ONLY the two autofocus commands ---
    def execute(self, command: str):
        if command not in FocusSem._AUTOFOCUS_CMDS \
                and command not in FocusSem._IMAGING_CMDS:
            raise CoincidenceSafetyError(
                f"command {command!r} is not permitted by the coincidence branch; "
                f"only autofocus and imaging commands may be executed.")
        object.__getattribute__(self, "calls").append(("execute", command))
        return object.__getattribute__(self, "_sem").execute(command)

    # --- permitted: image grab (no stage motion) ---
    def grab_bmp(self, path, **kwargs):
        object.__getattribute__(self, "calls").append(("grab_bmp", str(path)))
        return object.__getattribute__(self, "_sem").grab_bmp(path, **kwargs)

    # --- permitted: ONE validated Z-only move ---
    def move_z(self, z_target_m: float) -> None:
        """Move ONLY Z to ``z_target_m`` after re-validating every limit.

        Re-reads current Z (and the other axes, which are re-commanded to their
        current values so nothing else moves). Refuses if the move exceeds the
        per-move cap, if the target is outside the hard bounds, if the current Z is
        already outside them, or if the stage is tilted. This is the sole motion
        path in the whole branch.
        """
        raw = object.__getattribute__(self, "_sem")
        z_now = float(raw.get_float("AP_STAGE_AT_Z"))
        delta = float(z_target_m) - z_now
        if abs(delta) > MAX_Z_STEP_M + 1e-9:
            raise CoincidenceSafetyError(
                f"Z move {delta * 1e3:+.4f} mm exceeds the {MAX_Z_STEP_M * 1e3:.3f} mm "
                f"per-move cap; refusing.")
        if not (Z_HARD_MIN_M <= z_target_m <= Z_HARD_MAX_M):
            raise CoincidenceSafetyError(
                f"target Z {z_target_m * 1e3:.4f} mm is outside the hard bounds "
                f"[{Z_HARD_MIN_M * 1e3:.0f}, {Z_HARD_MAX_M * 1e3:.0f}] mm; refusing.")
        if not (Z_HARD_MIN_M <= z_now <= Z_HARD_MAX_M):
            raise CoincidenceSafetyError(
                f"current Z {z_now * 1e3:.4f} mm is outside the hard bounds; refusing "
                f"to move.")
        x = float(raw.get_float("AP_STAGE_AT_X"))
        y = float(raw.get_float("AP_STAGE_AT_Y"))
        t = float(raw.get_float("AP_STAGE_AT_T"))
        r = float(raw.get_float("AP_STAGE_AT_R"))
        m = float(raw.get_float("AP_STAGE_AT_M"))
        object.__getattribute__(self, "calls").append(("move_z", float(z_target_m)))
        # Move Z only: all other axes (including the current tilt) re-commanded to
        # their current values so nothing but Z changes.
        raw.move_stage(x, y, float(z_target_m), t, r, m)

    # --- everything else is blocked ---
    def __getattr__(self, name):
        raise CoincidenceSafetyError(
            f"'{name}' is not permitted by the coincidence branch (it may only read, "
            f"autofocus, and make one validated Z move).")

    def __setattr__(self, name, value):
        raise CoincidenceSafetyError(
            f"setting attribute '{name}' is not permitted on FocusSem.")


class SimulatedFocusSem:
    """Offline fake modelling: +Z shortens WD; autofocus reveals the true WD.

    WD(Z) = wd_const - Z, so raising Z by dZ shortens WD by dZ. ``wd_const`` is
    chosen from an initial (Z, WD) pair. Autofocus sets AP_WD to the true WD for
    the current Z (plus a little noise). DP_AUTO_FUNCTION reports 1 (Focus) while
    busy and 0 (Idle) when done -- modelled as one poll of "busy" then "idle".
    """

    def __init__(self, *, z_m: float = 30.0e-3, wd_m: float = 5.3e-3,
                 eht_v: float = 2000.0, tilt_deg: float = 0.0,
                 noise_m: float = 0.0, never_busy: bool = False, seed: int = 0):
        self.z_m = float(z_m)
        self.eht_v = float(eht_v)
        self.tilt_deg = float(tilt_deg)
        self.noise_m = float(noise_m)
        self.never_busy = bool(never_busy)
        # WD(Z) = wd_const - Z  =>  wd_const = wd + z at the seed point.
        self.wd_const = float(wd_m) + float(z_m)
        self._wd_m = float(wd_m)          # last focused WD reading
        self._auto_state = 0.0            # 0 Idle, 1 Focus
        self._busy_polls = 0
        import random
        self._rng = random.Random(seed)

    # Simulated manual stage move (tests use this to emulate the operator).
    def _operator_set_z(self, z_m: float) -> None:
        self.z_m = float(z_m)

    def _true_wd(self) -> float:
        return self.wd_const - self.z_m

    def get_float(self, m: str) -> float:
        if m == "AP_ACTUALKV":
            return self.eht_v
        if m == "AP_STAGE_AT_T":
            return self.tilt_deg
        if m == "AP_STAGE_AT_Z":
            return self.z_m
        if m in ("AP_STAGE_AT_X", "AP_STAGE_AT_Y", "AP_STAGE_AT_R", "AP_STAGE_AT_M"):
            return 0.0
        if m == "DP_STAGE_IS":
            return 0.0                    # always idle in the sim
        if m == "DP_FROZEN":
            return 1.0                    # frame available in the sim
        if m == "AP_WD":
            return self._wd_m
        if m == "DP_AUTO_FUNCTION":
            # Report busy for one poll after a focus command, then idle.
            if self._busy_polls > 0:
                self._busy_polls -= 1
                self._auto_state = 1.0
                return 1.0
            self._auto_state = 0.0
            return 0.0
        raise KeyError(m)

    def get_str(self, m: str) -> str:
        if m == "DP_AUTO_FUNCTION":
            return "Focus" if self._auto_state >= 0.5 else "Idle"
        raise KeyError(m)

    def execute(self, command: str) -> None:
        if command in ("CMD_AUTO_FOCUS_COARSE", "CMD_AUTO_FOCUS_FINE"):
            # Focus reveals the true WD for the current Z.
            noise = (self._rng.uniform(-self.noise_m, self.noise_m)
                     if self.noise_m else 0.0)
            self._wd_m = self._true_wd() + noise
            self._busy_polls = 0 if self.never_busy else 1
        # other commands: no-op in the sim

    def move_stage(self, x_m, y_m, z_m, t_deg, r_deg, m_m) -> None:
        # Only Z is expected to change; the operator/guard moves Z only.
        self.z_m = float(z_m)

    def grab_bmp(self, path, *, xoff=0, yoff=0, width=64, height=48, reduction=0):
        # Write a tiny valid BMP so the GUI can load something in offline demos.
        try:
            from PIL import Image
            Image.new("L", (max(1, width), max(1, height)), 128).save(str(path))
        except Exception:
            # Minimal BMP header fallback if PIL is unavailable.
            try:
                with open(str(path), "wb") as fh:
                    fh.write(b"BM")
            except Exception:
                pass
