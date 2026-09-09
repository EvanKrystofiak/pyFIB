"""Verbatim GIS command-name registry -- confirmed on the instrument.

DATA ONLY. This module executes NOTHING. It stores the exact Execute() command strings
for each GIS port and pairs them with the confirmed readback parameters from
gisdep.gis.state, so the deposition backend can look commands up per port instead of
generating them.

Why a registry instead of a format string: the two ports share no naming convention.
Every field differs -- position-named vs action-named (GOTO_GAS1 vs INSERT), and CHANNEL
vs VALVE. (Casing is uniform: all mnemonics are uppercase.) These are measured strings;
storing them verbatim (and pinning them with tests) is the only safe option.

    field           GIS1                      GIS2
    insert needle   CMD_GIS_GOTO_GAS1         CMD_GIS_2ND_INSERT
    retract needle  CMD_GIS_GOTO_PARKED       CMD_GIS_2ND_RETRACT
    open valve      CMD_GIS_OPEN_CHANNEL1     CMD_GIS_2ND_OPEN_VALVE
    close valve     CMD_GIS_CLOSE_CHANNEL     CMD_GIS_2ND_CLOSE_VALVE

All Crossbeam CMD_ commands are ALL-UPPERCASE (an instrument rule) -- a test enforces
this on the registry. The DP_ readback params are ALSO all-uppercase (same rule); they
live in gisdep.gis.state and are enforced there.
"""

from __future__ import annotations

from dataclasses import dataclass

from gisdep.gis.state import (
    GISPortParams,
    GIS1 as _GIS1_READBACKS,
    GIS2 as _GIS2_READBACKS,
)


@dataclass(frozen=True)
class GISPortCommands:
    """Exact Execute() command strings for one GIS port, plus its readbacks.

    Nominal mechanical timings (seconds) are current ASSUMPTIONS to be measured on the
    instrument and turned into regression tests. They are used only as timeout bounds
    for closed-loop polling, never as the mechanism -- the readbacks decide completion.
    """

    port: int
    insert_needle: str
    retract_needle: str
    open_valve: str
    close_valve: str
    readbacks: GISPortParams
    # Nominal needle timings (seconds). MEASURED live per port (readback-confirmed
    # insert/retract, no gas): GIS1 ~2.1/2.1 s, GIS2 ~3.4/1.8 s. Valve timings not yet
    # measured live. Used as reference; the backend's timeouts have generous margin.
    nominal_insert_s: float = 5.0
    nominal_retract_s: float = 10.0
    nominal_open_s: float = 0.5
    nominal_close_s: float = 0.5


GIS1_COMMANDS = GISPortCommands(
    port=1,
    insert_needle="CMD_GIS_GOTO_GAS1",
    retract_needle="CMD_GIS_GOTO_PARKED",
    open_valve="CMD_GIS_OPEN_CHANNEL1",
    close_valve="CMD_GIS_CLOSE_CHANNEL",
    readbacks=_GIS1_READBACKS,
    nominal_insert_s=2.1,    # measured live
    nominal_retract_s=2.1,   # measured live
)

GIS2_COMMANDS = GISPortCommands(
    port=2,
    insert_needle="CMD_GIS_2ND_INSERT",
    retract_needle="CMD_GIS_2ND_RETRACT",
    open_valve="CMD_GIS_2ND_OPEN_VALVE",
    close_valve="CMD_GIS_2ND_CLOSE_VALVE",
    readbacks=_GIS2_READBACKS,
    nominal_insert_s=3.4,    # measured live
    nominal_retract_s=1.8,   # measured live
)

# All fully confirmed. (No UNCONFIRMED entries remain as of v0.4.0.)
GIS_COMMANDS = {1: GIS1_COMMANDS, 2: GIS2_COMMANDS}


def commands_for(port: int) -> GISPortCommands:
    if port not in GIS_COMMANDS:
        raise ValueError(f"unknown GIS port {port}; known: {sorted(GIS_COMMANDS)}")
    return GIS_COMMANDS[port]
