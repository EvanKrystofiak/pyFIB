"""Mnemonics and state tables used by gisdep, transcribed from the SmartSEM Remote
API manual (section 6). Only what this tool touches is listed here.

Every value here that affects a decision should ultimately be backed by an
instrument observation -- per the project principle "measure signs/conventions on
the instrument, don't derive them." Items still awaiting confirmation are flagged.
"""

from __future__ import annotations

# --- analogue parameters (SI units) ---------------------------------------
AP_WD = "AP_WD"                    # Working distance, metres  (manual 6.1.2)

# Stage tilt. NOTE: the manual's §6.1.6 table lists AP_STAGE_AT_* units as "Metres",
# which is a copy-paste error for the angular axes -- GetStagePosition (§4.10) is the
# authoritative source and documents tilt/rotation in DEGREES. gisdep reads tilt via
# get_stage_position() rather than this parameter to avoid the ambiguity. Kept here
# for reference / future probing.
AP_STAGE_AT_T = "AP_STAGE_AT_T"    # Stage tilt (degrees per GetStagePosition)

# --- digital parameters ----------------------------------------------------
DP_AUTO_FUNCTION = "DP_AUTO_FUNCTION"   # RO, current auto routine (manual 6.2.4)
DP_STAGE_IS = "DP_STAGE_IS"             # RO, 0 Idle / 1 Busy    (manual 6.2.8)
DP_FIB_GUN_STATE = "DP_FIB_GUN_STATE"   # RO                     (manual 6.2.10)

# --- commands --------------------------------------------------------------
CMD_AUTO_FOCUS_COARSE = "CMD_AUTO_FOCUS_COARSE"  # Coarse Focus (manual 6.4.4)
CMD_AUTO_FOCUS_FINE = "CMD_AUTO_FOCUS_FINE"      # Fine Focus   (manual 6.4.4)
CMD_ABORT_AUTO = "CMD_ABORT_AUTO"                # Cancel Auto  (manual 6.4.4)

# --- DP_AUTO_FUNCTION state values (manual 6.2.4) --------------------------
AF_IDLE = 0
AF_FOCUS = 1
AF_GUN_ALIGN = 2
AF_STIGMATION = 3
AF_SATURATE = 4
AF_FIND_PEAK = 5
AF_FIND_MEAN = 6
AF_FIND_IMAGE = 7
AF_FOCUS_SCAN = 8
AF_FOCUS_STIG = 9
AF_ABC = 10

AUTO_FUNCTION_NAMES = {
    AF_IDLE: "Idle",
    AF_FOCUS: "Focus",
    AF_GUN_ALIGN: "Gun Align",
    AF_STIGMATION: "Stigmation",
    AF_SATURATE: "Saturate",
    AF_FIND_PEAK: "Find Peak",
    AF_FIND_MEAN: "Find Mean",
    AF_FIND_IMAGE: "Find Image",
    AF_FOCUS_SCAN: "Focus Scan",
    AF_FOCUS_STIG: "Focus+Stig",
    AF_ABC: "ABC",
}

# --- DP_STAGE_IS state values (manual 6.2.8) ------------------------------
STAGE_IDLE = 0
STAGE_BUSY = 1
STAGE_IS_NAMES = {STAGE_IDLE: "Idle", STAGE_BUSY: "Busy"}

# --- DP_FIB_GUN_STATE state values (manual 6.2.10) ------------------------
# NOTE: there is NO documented digital parameter that reports the FIB *gun valve*
# open/closed position -- only CMD_FIB_GUN_VALVE_OPEN / _CLOSE exist. The future
# "FIB valve closed" prerequisite therefore has no confirmed readback yet and must
# be resolved by probing the instrument. (See gisdep/gis/probe.py.)
FIB_GUN_OFF = 0
FIB_GUN_ON = 1
FIB_GUN_RAMPING = 2
FIB_GUN_ERROR = 3
FIB_GUN_VAC_NOT_READY = 4
FIB_GUN_STATE_NAMES = {
    FIB_GUN_OFF: "Off",
    FIB_GUN_ON: "On",
    FIB_GUN_RAMPING: "Ramping",
    FIB_GUN_ERROR: "Error",
    FIB_GUN_VAC_NOT_READY: "Vac Not Ready",
}
