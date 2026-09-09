"""Faithful offline mock of the SmartSEM Remote API.

No simulator exists, so this mock is the sole means of testing gisdep offline. It is
deliberately faithful to two confirmed instrument behaviours that matter to autofocus:

  1. DP_AUTO_FUNCTION reports Idle *prematurely* -- right after a focus command is
     issued there is a brief window during which the auto routine has not yet started
     and the parameter still reads Idle. Only after that does it read Focus, and only
     later does it settle back to Idle. This is why the routine needs a sustained-idle
     confirmation window rather than trusting the first Idle it sees.

  2. Unknown mnemonics fail with API_E_GET_TRANSLATE_FAIL (rc=1000). This lets the
     read-only GIS probe report "no such parameter" faithfully -- and, importantly,
     the mock knows about NO GIS mnemonics, matching the documented API.

Timing is driven by the injected clock, so with a FakeClock the auto cycle plays out
in virtual time.
"""

from __future__ import annotations

import functools
import threading
from typing import Dict, Optional, Union

from gisdep.api import params as P
from gisdep.api.interface import SmartSEMInterface, SmartSEMError, StagePosition
from gisdep.clock import Clock, RealClock

# Error codes (manual section 7)
API_E_GET_TRANSLATE_FAIL = 1000
API_E_SET_TRANSLATE_FAIL = 1004
API_E_SET_STATE_FAIL = 1005
API_E_EXEC_TRANSLATE_FAIL = 1011
API_E_NOT_INITIALISED = 1019

# --- GIS readbacks (instrument-side model) --------------------------------
# The mock owns the instrument-facing strings/labels; the tool's interpretation lives
# in gisdep.gis.state. A drift-guard test asserts these mnemonics match that module.
# param -> (port, field), field in {"valve", "needle"}.
_MOCK_GIS_PARAM_MAP = {
    "DP_GIS_VALVE_STATE1": (1, "valve"),
    "DP_GIS_2ND_VALVE_STATE": (2, "valve"),
    "DP_GIS_LOCATION": (1, "needle"),
    "DP_GIS_2ND_STAGE_LOCATION": (2, "needle"),
}
# Instrument-style text labels (as reported live on the scope). Note both needle ports
# report "Gas 1" when inserted, per the measured values.
_MOCK_VALVE_TEXT = {0: "Closed", 1: "Open"}
_MOCK_NEEDLE_TEXT = {0: "Unknown", 1: "Parked", 2: "Gas 1"}

# How each GIS command drives a readback. command -> (port, field, target).
# During a transition the readback shows the field's TRANSIT value (needle 0 "Unknown",
# valve 3 "Transit/Unknown") -- both confirmed observable live on the scope -- before
# settling to the target.
_MOCK_GIS_COMMAND_MAP = {
    "CMD_GIS_GOTO_GAS1": (1, "needle", 2),
    "CMD_GIS_GOTO_PARKED": (1, "needle", 1),
    "CMD_GIS_OPEN_CHANNEL1": (1, "valve", 1),
    "CMD_GIS_CLOSE_CHANNEL": (1, "valve", 0),
    "CMD_GIS_2ND_INSERT": (2, "needle", 2),
    "CMD_GIS_2ND_RETRACT": (2, "needle", 1),
    "CMD_GIS_2ND_OPEN_VALVE": (2, "valve", 1),
    "CMD_GIS_2ND_CLOSE_VALVE": (2, "valve", 0),
}
# Per-field transit value shown while a transition is in flight.
_MOCK_TRANSIT = {"needle": 0, "valve": 3}
# Nominal transition durations (s). Needle values MEASURED live (insert ~2-3 s, retract
# ~2 s); valve values not yet measured live. Valve close dwells at transit value 3.
_MOCK_GIS_DUR = {
    ("needle", 2): 2.5,    # insert (measured ~2.1-3.4 s)
    ("needle", 1): 2.0,    # retract (measured ~1.8-2.1 s)
    ("valve", 1): 0.5,     # open
    ("valve", 0): 1.0,     # close (via transit 3)
}


class MockAutoConfig:
    """Timing of the simulated auto routine, in seconds of clock time.

    ``latency`` is the premature-idle window (reads Idle although a command was just
    issued); ``active`` is how long DP_AUTO_FUNCTION then reads Focus.
    """

    def __init__(self, latency: float = 0.4, active: float = 2.5) -> None:
        self.latency = float(latency)
        self.active = float(active)


def _synchronized(method):
    """Serialise a mock method under self._lock so a GUI polling thread and a
    deposition/stage worker thread can share one mock safely."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class MockSmartSEM(SmartSEMInterface):
    def __init__(
        self,
        clock: Optional[Clock] = None,
        wd_m: float = 0.0055,
        stage_state: int = P.STAGE_IDLE,
        fib_gun_state: int = P.FIB_GUN_OFF,
        auto_config: Optional[MockAutoConfig] = None,
        stage_tilt_deg: float = 0.0,
        stage_z_m: float = 0.030,
        gis: Optional[Dict[int, Dict[str, int]]] = None,
    ) -> None:
        self.clock = clock or RealClock()
        self.auto_cfg = auto_config or MockAutoConfig()

        self._lock = threading.RLock()
        self._initialised = False
        self._closed = False

        # GIS needle/valve state per port. Default: safe (parked + closed).
        self._gis: Dict[int, Dict[str, int]] = gis or {
            1: {"needle": 1, "valve": 0},
            2: {"needle": 1, "valve": 0},
        }
        # In-flight GIS transitions keyed by (port, field) and commands marked "stuck"
        # (a transition that never completes -- for fault-injection tests).
        self._gis_transition: Dict[tuple, dict] = {}
        self._gis_stuck_cmds: set = set()

        # stage position (metres / degrees). Flat stage (tilt 0); Z mid-range within the
        # [20, 40] mm hard Z limits so Z nudges land in range.
        self._stage = StagePosition(
            x_m=0.0, y_m=0.0, z_m=float(stage_z_m),
            tilt_deg=float(stage_tilt_deg), rot_deg=0.0, m_m=0.0,
        )

        # analogue store (SI units). Stage soft limits included so the mover's limit
        # check is testable; real values come from the instrument. Z limits mirror the
        # [20, 40] mm hard limits.
        self._ap: Dict[str, float] = {
            P.AP_WD: float(wd_m),
            "AP_STAGE_HIGH_X": 0.05, "AP_STAGE_LOW_X": -0.05,
            "AP_STAGE_HIGH_Y": 0.10, "AP_STAGE_LOW_Y": -0.10,
            "AP_STAGE_HIGH_Z": 0.040, "AP_STAGE_LOW_Z": 0.020,
        }
        # in-flight stage move (non-blocking model)
        self._stage_move: Optional[dict] = None
        self._stage_move_dur: float = 2.0
        # ordered log of MoveStage targets (x,y,z,t,r,m) -- lets tests assert move order
        self.stage_moves: list = []
        # Confirmed on the instrument (2025 dev-stage nudge, +200 um Z -> -200 um WD):
        # dWD/dZ = -1 (i.e. +Z shortens WD 1:1). The mock adjusts AP_WD when Z moves.
        self._wd_per_z: float = -1.0
        # digital store (state numbers)
        self._dp: Dict[str, int] = {
            P.DP_STAGE_IS: int(stage_state),
            P.DP_FIB_GUN_STATE: int(fib_gun_state),
        }

        # auto-function simulation
        self._auto_cmd_time: Optional[float] = None
        self._auto_kind: Optional[str] = None  # last focus command issued

        # call log for test assertions
        self.executed: list[str] = []

    # --- lifecycle ---------------------------------------------------------
    def initialise(self) -> None:
        self._initialised = True
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def _require_init(self) -> None:
        if not self._initialised or self._closed:
            raise SmartSEMError(API_E_NOT_INITIALISED, "control not initialised")

    # --- auto-function model ----------------------------------------------
    def _auto_function_now(self) -> int:
        if self._auto_cmd_time is None:
            return P.AF_IDLE
        dt = self.clock.now() - self._auto_cmd_time
        if dt < self.auto_cfg.latency:
            return P.AF_IDLE            # premature-idle window
        if dt < self.auto_cfg.latency + self.auto_cfg.active:
            return P.AF_FOCUS
        return P.AF_IDLE               # completed

    # --- reads -------------------------------------------------------------
    @_synchronized
    def get_analogue(self, param: str) -> float:
        self._require_init()
        if param in self._ap:
            return self._ap[param]
        raise SmartSEMError(API_E_GET_TRANSLATE_FAIL, "unknown analogue param", param)

    @_synchronized
    def get_digital(self, param: str) -> int:
        self._require_init()
        if param == P.DP_AUTO_FUNCTION:
            return self._auto_function_now()
        if param == P.DP_STAGE_IS:
            return self._stage_is_now()
        if param in _MOCK_GIS_PARAM_MAP:
            port, field = _MOCK_GIS_PARAM_MAP[param]
            return self._gis_value_now(port, field)
        if param in self._dp:
            return self._dp[param]
        raise SmartSEMError(API_E_GET_TRANSLATE_FAIL, "unknown digital param", param)

    @_synchronized
    def get_stage_position(self) -> StagePosition:
        self._require_init()
        self._advance_stage()
        # return a copy so callers can't mutate mock state
        return StagePosition(
            x_m=self._stage.x_m, y_m=self._stage.y_m, z_m=self._stage.z_m,
            tilt_deg=self._stage.tilt_deg, rot_deg=self._stage.rot_deg,
            m_m=self._stage.m_m,
        )

    @_synchronized
    def move_stage(self, x, y, z, t, r, m) -> None:
        self._require_init()
        self.stage_moves.append((float(x), float(y), float(z), float(t), float(r), float(m)))
        target = StagePosition(
            x_m=float(x), y_m=float(y), z_m=float(z),
            tilt_deg=float(t), rot_deg=float(r), m_m=float(m),
        )
        self._stage_move = {"start": self.clock.now(), "dur": self._stage_move_dur,
                            "target": target}

    def _advance_stage(self) -> None:
        mv = self._stage_move
        if mv is not None and self.clock.now() - mv["start"] >= mv["dur"]:
            old_z = self._stage.z_m
            self._stage = mv["target"]
            # confirmed instrument coupling: WD tracks Z with dWD/dZ = -1
            dz = self._stage.z_m - old_z
            if P.AP_WD in self._ap:
                self._ap[P.AP_WD] += dz * self._wd_per_z
            self._stage_move = None

    def _stage_is_now(self) -> int:
        self._advance_stage()
        if self._stage_move is not None:
            return P.STAGE_BUSY
        return self._dp[P.DP_STAGE_IS]

    @_synchronized
    def get_digital_text(self, param: str) -> str:
        val = self.get_digital(param)
        if param == P.DP_AUTO_FUNCTION:
            return P.AUTO_FUNCTION_NAMES.get(val, str(val))
        if param == P.DP_STAGE_IS:
            return P.STAGE_IS_NAMES.get(val, str(val))
        if param == P.DP_FIB_GUN_STATE:
            return P.FIB_GUN_STATE_NAMES.get(val, str(val))
        if param in _MOCK_GIS_PARAM_MAP:
            _, field = _MOCK_GIS_PARAM_MAP[param]
            table = _MOCK_VALVE_TEXT if field == "valve" else _MOCK_NEEDLE_TEXT
            return table.get(val, str(val))
        return str(val)

    # --- writes ------------------------------------------------------------
    @_synchronized
    def set_analogue(self, param: str, value: float) -> None:
        self._require_init()
        # AP_WD is read-only on the instrument; the mock allows setting it so tests
        # can position the working distance. Production code never sets AP_WD.
        self._ap[param] = float(value)

    @_synchronized
    def set_digital(self, param: str, value: Union[int, str]) -> None:
        self._require_init()
        if param not in self._dp and param != P.DP_AUTO_FUNCTION:
            raise SmartSEMError(API_E_SET_TRANSLATE_FAIL, "unknown digital param", param)
        if isinstance(value, str):
            raise SmartSEMError(API_E_SET_STATE_FAIL, "mock: set by name unsupported", param)
        self._dp[param] = int(value)

    # --- GIS transition model ---------------------------------------------
    def _gis_value_now(self, port: int, field: str) -> int:
        key = (port, field)
        tr = self._gis_transition.get(key)
        if tr is None:
            return self._gis[port][field]
        transit = _MOCK_TRANSIT[field]
        if tr["stuck"]:
            return transit  # never completes (fault injection) -> stuck in transit/error
        if self.clock.now() - tr["start"] >= tr["dur"]:
            self._gis[port][field] = tr["target"]
            del self._gis_transition[key]
            return tr["target"]
        return transit

    def _start_gis_transition(self, command: str) -> None:
        port, field, target = _MOCK_GIS_COMMAND_MAP[command]
        key = (port, field)
        # A new command overrides any in-flight transition (e.g. retract cancelling an
        # insert). If, after cancelling, we are already at the target, it's a no-op.
        self._gis_transition.pop(key, None)
        if self._gis[port][field] == target:
            return
        self._gis_transition[key] = {
            "start": self.clock.now(),
            "dur": _MOCK_GIS_DUR[(field, target)],
            "target": target,
            "from": self._gis[port][field],
            "stuck": command in self._gis_stuck_cmds,
        }

    # --- actions -----------------------------------------------------------
    @_synchronized
    def execute(self, command: str) -> None:
        self._require_init()
        self.executed.append(command)
        if command in (P.CMD_AUTO_FOCUS_COARSE, P.CMD_AUTO_FOCUS_FINE):
            self._auto_cmd_time = self.clock.now()
            self._auto_kind = command
            return
        if command == P.CMD_ABORT_AUTO:
            self._auto_cmd_time = None
            self._auto_kind = None
            return
        if command in _MOCK_GIS_COMMAND_MAP:
            self._start_gis_transition(command)
            return
        raise SmartSEMError(API_E_EXEC_TRANSLATE_FAIL, "unknown command", command)

    # --- test helpers ------------------------------------------------------
    @_synchronized
    def set_stage_busy(self, busy: bool) -> None:
        self._dp[P.DP_STAGE_IS] = P.STAGE_BUSY if busy else P.STAGE_IDLE

    @_synchronized
    def set_stage_tilt(self, tilt_deg: float) -> None:
        self._stage.tilt_deg = float(tilt_deg)

    @_synchronized
    def set_gis_needle(self, port: int, value: int) -> None:
        self._gis[port]["needle"] = int(value)
        self._gis_transition.pop((port, "needle"), None)

    @_synchronized
    def set_gis_valve(self, port: int, value: int) -> None:
        self._gis[port]["valve"] = int(value)
        self._gis_transition.pop((port, "valve"), None)

    @_synchronized
    def set_gis_command_stuck(self, command: str, stuck: bool = True) -> None:
        """Fault injection: mark a GIS command whose transition never completes."""
        if stuck:
            self._gis_stuck_cmds.add(command)
        else:
            self._gis_stuck_cmds.discard(command)
