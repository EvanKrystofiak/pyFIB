"""GIS needle/valve state readbacks -- confirmed working live on the scope.

These four digital parameters were measured on the instrument (not derived). DP read
mnemonics are ALL-UPPERCASE (an instrument rule, same as the CMD_ commands) -- a test
enforces this. The naming is still inconsistent across ports ("2ND" vs "STATE1" vs
"STAGE_LOCATION"), so the exact strings are stored verbatim otherwise. All decisions key
off the integer value, never the (localisable) text label; the label is read only for
display and cross-checking.

Measured value meanings:

  Valve  (DP_GIS_VALVE_STATE1 / DP_GIS_2ND_VALVE_STATE):
      0 = Closed
      1 = Open
      3 = Transit/Unknown (in transit or error) -- observed live on the scope during
          valve open/close, most clearly as 1 -> 3 -> 0 while closing. Value 2 has not
          been observed for the valve and is left unmapped.

  Needle (DP_GIS_LOCATION / DP_GIS_2ND_STAGE_LOCATION):
      0 = Unknown   (in transit, or an error)
      1 = Parked    (retracted)
      2 = Inserted  (at gas position; instrument text reads "Gas 1" on BOTH ports)

Both the valve's 3 and the needle's 0 are transient during motion; if they persist past
the expected transition time they indicate an error. The closed-loop backend handles
both the same way -- it polls for the target value, so a transient transit reading is
simply "not there yet", and a persistent one trips the confirmation timeout.

Note: "DP_GIS_2ND_STAGE_LOCATION" contains the word STAGE but refers to the GIS second
needle, NOT the microscope stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from gisdep.api.interface import SmartSEMInterface, SmartSEMError

API_E_GET_TRANSLATE_FAIL = 1000

# --- exact mnemonics (verbatim, measured) ---------------------------------
DP_GIS1_VALVE = "DP_GIS_VALVE_STATE1"
DP_GIS2_VALVE = "DP_GIS_2ND_VALVE_STATE"
DP_GIS1_NEEDLE = "DP_GIS_LOCATION"
DP_GIS2_NEEDLE = "DP_GIS_2ND_STAGE_LOCATION"

# --- value constants -------------------------------------------------------
VALVE_CLOSED = 0
VALVE_OPEN = 1
VALVE_TRANSIT = 3    # in transit or error (observed live during open/close)

NEEDLE_UNKNOWN = 0   # in transit or error
NEEDLE_PARKED = 1    # retracted / parked
NEEDLE_INSERTED = 2  # at gas position

# Our interpretation (from the integer).
VALVE_MEANING = {
    VALVE_CLOSED: "Closed",
    VALVE_OPEN: "Open",
    VALVE_TRANSIT: "Transit",
}
NEEDLE_MEANING = {
    NEEDLE_UNKNOWN: "Transit",           # value 0 -- in transit (or, rarely, an error)
    NEEDLE_PARKED: "Parked (retracted)",
    NEEDLE_INSERTED: "Inserted (at gas)",
}

# Mapping used by the mock so param names never drift from this module.
# param -> (port, field) where field is "valve" or "needle".
GIS_PARAM_MAP: Dict[str, Tuple[int, str]] = {
    DP_GIS1_VALVE: (1, "valve"),
    DP_GIS2_VALVE: (2, "valve"),
    DP_GIS1_NEEDLE: (1, "needle"),
    DP_GIS2_NEEDLE: (2, "needle"),
}


@dataclass(frozen=True)
class GISPortParams:
    """Which readback parameters belong to a given GIS port."""

    port: int
    valve_param: str
    needle_param: str


GIS1 = GISPortParams(port=1, valve_param=DP_GIS1_VALVE, needle_param=DP_GIS1_NEEDLE)
GIS2 = GISPortParams(port=2, valve_param=DP_GIS2_VALVE, needle_param=DP_GIS2_NEEDLE)
DEFAULT_PORTS = (GIS1, GIS2)


def interpret_valve(raw: Optional[int]) -> str:
    if raw is None:
        return "?"
    return VALVE_MEANING.get(raw, f"UNEXPECTED({raw})")


def interpret_needle(raw: Optional[int]) -> str:
    if raw is None:
        return "?"
    return NEEDLE_MEANING.get(raw, f"UNEXPECTED({raw})")


@dataclass
class _FieldRead:
    raw: Optional[int]
    instrument_text: Optional[str]
    present: bool
    error: Optional[str]


def _read_field(api: SmartSEMInterface, param: str, read_text: bool) -> _FieldRead:
    try:
        raw = api.get_digital(param)
    except SmartSEMError as exc:
        # rc=1000 => the parameter name doesn't translate => absent.
        return _FieldRead(None, None, exc.code != API_E_GET_TRANSLATE_FAIL, str(exc))
    text = None
    if read_text:
        try:
            text = api.get_digital_text(param)
        except SmartSEMError:
            text = None
    return _FieldRead(raw, text, True, None)


@dataclass
class GISPortReading:
    port: int
    valve_raw: Optional[int]
    valve_meaning: str
    valve_instrument_text: Optional[str]
    valve_present: bool
    valve_error: Optional[str]
    needle_raw: Optional[int]
    needle_meaning: str
    needle_instrument_text: Optional[str]
    needle_present: bool
    needle_error: Optional[str]

    @property
    def valve_open(self) -> bool:
        return self.valve_raw == VALVE_OPEN

    @property
    def valve_closed(self) -> bool:
        return self.valve_raw == VALVE_CLOSED

    @property
    def valve_in_transit(self) -> bool:
        return self.valve_raw == VALVE_TRANSIT

    @property
    def needle_inserted(self) -> bool:
        return self.needle_raw == NEEDLE_INSERTED

    @property
    def needle_parked(self) -> bool:
        return self.needle_raw == NEEDLE_PARKED

    @property
    def is_safe(self) -> bool:
        """Needle parked AND valve closed -- the state we require before/after work."""
        return self.needle_parked and self.valve_closed

    def line(self) -> str:
        def fmt(raw, meaning, text, present):
            if not present:
                return "ABSENT"
            t = f' / text="{text}"' if text is not None else ""
            return f"{raw} -> {meaning}{t}"
        return (
            f"GIS{self.port}  "
            f"needle: {fmt(self.needle_raw, self.needle_meaning, self.needle_instrument_text, self.needle_present)}  |  "
            f"valve: {fmt(self.valve_raw, self.valve_meaning, self.valve_instrument_text, self.valve_present)}"
        )


def read_port(
    api: SmartSEMInterface, port: GISPortParams, read_text: bool = True
) -> GISPortReading:
    v = _read_field(api, port.valve_param, read_text)
    n = _read_field(api, port.needle_param, read_text)
    return GISPortReading(
        port=port.port,
        valve_raw=v.raw,
        valve_meaning=interpret_valve(v.raw) if v.present else "?",
        valve_instrument_text=v.instrument_text,
        valve_present=v.present,
        valve_error=v.error,
        needle_raw=n.raw,
        needle_meaning=interpret_needle(n.raw) if n.present else "?",
        needle_instrument_text=n.instrument_text,
        needle_present=n.present,
        needle_error=n.error,
    )


def read_all(
    api: SmartSEMInterface, ports=DEFAULT_PORTS, read_text: bool = True
) -> Dict[int, GISPortReading]:
    return {p.port: read_port(api, p, read_text) for p in ports}
