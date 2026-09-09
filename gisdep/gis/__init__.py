"""GIS control seam (scaffold), read-only API probe, and live state readbacks.

No module here actuates a needle or gas valve.
"""

from gisdep.gis.backend import GISBackend, StubGISBackend, MockGISBackend
from gisdep.gis.probe import GISProbe, ProbeReport, ProbeEntry
from gisdep.gis.state import (
    GIS1,
    GIS2,
    GISPortParams,
    GISPortReading,
    read_port,
    read_all,
    VALVE_CLOSED,
    VALVE_OPEN,
    VALVE_TRANSIT,
    NEEDLE_UNKNOWN,
    NEEDLE_PARKED,
    NEEDLE_INSERTED,
    DP_GIS1_VALVE,
    DP_GIS2_VALVE,
    DP_GIS1_NEEDLE,
    DP_GIS2_NEEDLE,
)
from gisdep.gis.verify import GISStateMonitor, GISChange, snapshot_text
from gisdep.gis.commands import (
    GISPortCommands,
    GIS1_COMMANDS,
    GIS2_COMMANDS,
    GIS_COMMANDS,
    commands_for,
)
from gisdep.gis.actuate import (
    CommandGISBackend,
    GISBackendConfig,
    GISActuationError,
    GISConfirmTimeout,
    MutualExclusionError,
)
from gisdep.gis.deposition import (
    deposit,
    DepositionResult,
    DepositionError,
    NotSafeToStartError,
    CloseFailure,
    OperatorDeclined,
    DWELL_MIN_S,
    DWELL_MAX_S,
)

__all__ = [
    "GISBackend",
    "StubGISBackend",
    "MockGISBackend",
    "GISProbe",
    "ProbeReport",
    "ProbeEntry",
    "GIS1",
    "GIS2",
    "GISPortParams",
    "GISPortReading",
    "read_port",
    "read_all",
    "VALVE_CLOSED",
    "VALVE_OPEN",
    "VALVE_TRANSIT",
    "NEEDLE_UNKNOWN",
    "NEEDLE_PARKED",
    "NEEDLE_INSERTED",
    "DP_GIS1_VALVE",
    "DP_GIS2_VALVE",
    "DP_GIS1_NEEDLE",
    "DP_GIS2_NEEDLE",
    "GISStateMonitor",
    "GISChange",
    "snapshot_text",
    "GISPortCommands",
    "GIS1_COMMANDS",
    "GIS2_COMMANDS",
    "GIS_COMMANDS",
    "commands_for",
    "CommandGISBackend",
    "GISBackendConfig",
    "GISActuationError",
    "GISConfirmTimeout",
    "MutualExclusionError",
    "deposit",
    "DepositionResult",
    "DepositionError",
    "NotSafeToStartError",
    "CloseFailure",
    "OperatorDeclined",
    "DWELL_MIN_S",
    "DWELL_MAX_S",
]
