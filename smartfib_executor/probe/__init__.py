"""Read-only probe: which SmartSEM mnemonics exist on THIS instrument.

Standalone diagnostic. Never writes to the microscope -- see probe.ReadOnlySem.

    python -m smartfib_executor.probe                 # against the real instrument
    python -m smartfib_executor.probe --simulate      # offline demo
"""

from __future__ import annotations

from .catalog import CAPABILITIES, NOT_PROBEABLE, Candidate, Capability
from .probe import (run_probe, probe_one, format_report, ProbeReport,
                    ProbeResult, CapabilityResult, ReadOnlySem, ReadOnlyViolation,
                    PRESENT, ABSENT, UNAVAILABLE, UNKNOWN)

__all__ = [
    "CAPABILITIES", "NOT_PROBEABLE", "Candidate", "Capability",
    "run_probe", "probe_one", "format_report", "ProbeReport", "ProbeResult",
    "CapabilityResult", "ReadOnlySem", "ReadOnlyViolation",
    "PRESENT", "ABSENT", "UNAVAILABLE", "UNKNOWN",
]
