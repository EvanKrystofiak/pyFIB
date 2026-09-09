"""Read-only probe to discover whether the Remote API exposes any hidden GIS
parameters -- mirrors the DriftProbe pattern (measure on the instrument; never derive).

SAFETY: This probe is strictly READ-ONLY. It only calls Get() on candidate AP_/DP_
mnemonics and records which ones resolve. It NEVER calls Set() and NEVER calls
Execute(), because:
  * Set/Execute could actuate hardware (that's the whole risk we're trying to map).
  * Command (CMD_) mnemonics cannot be safely discovered by calling them -- executing
    an unknown command to "see if it exists" is exactly what we must not do. Candidate
    command names are therefore listed for you to check by hand in the SmartSEM
    parameter/macro editor, not exercised here.

A mnemonic that returns cleanly is PRESENT. One that raises SmartSEMError with
rc=1000 (API_E_GET_TRANSLATE_FAIL) is ABSENT. Other error codes are reported verbatim
(e.g. a parameter that exists but is invalid in the current mode).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from gisdep.api.interface import SmartSEMInterface, SmartSEMError

API_E_GET_TRANSLATE_FAIL = 1000

# Candidate ANALOGUE mnemonics (safe to Get). Zeiss-style guesses -- extend freely.
ANALOGUE_CANDIDATES = [
    "AP_GIS_TEMP",
    "AP_GIS_TEMPERATURE",
    "AP_GIS_1_TEMP",
    "AP_GIS_2_TEMP",
    "AP_GIS_NEEDLE_POS",
    "AP_NEEDLE_POSITION",
    "AP_GIS_PRESSURE",
    "AP_GIS_FLOW",
]

# Digital mnemonics CONFIRMED present on the instrument (measured). Kept first so a
# probe run re-confirms them, and imported from the single source of truth.
from gisdep.gis.state import (
    DP_GIS1_VALVE,
    DP_GIS2_VALVE,
    DP_GIS1_NEEDLE,
    DP_GIS2_NEEDLE,
)

CONFIRMED_DIGITAL = [DP_GIS1_NEEDLE, DP_GIS1_VALVE, DP_GIS2_NEEDLE, DP_GIS2_VALVE]

# Candidate DIGITAL mnemonics (safe to Get). Confirmed names first, then guesses.
DIGITAL_CANDIDATES = CONFIRMED_DIGITAL + [
    "DP_GIS",
    "DP_GIS_1",
    "DP_GIS_2",
    "DP_GIS_STATE",
    "DP_GIS_NEEDLE",
    "DP_GIS_NEEDLE_1",
    "DP_GIS_NEEDLE_2",
    "DP_GIS_NEEDLE_STATE",
    "DP_GIS_INSERTED",
    "DP_GIS_VALVE",
    "DP_GIS_VALVE_1",
    "DP_GIS_VALVE_2",
    "DP_GIS_PORT",
    "DP_GIS_HEATER",
    "DP_GAS_INJECTION",
    "DP_NEEDLE",
    "DP_NEEDLE_1",
    "DP_NEEDLE_2",
]

# Candidate COMMAND mnemonics -- FOR MANUAL INSPECTION ONLY. Never executed here.
COMMAND_CANDIDATES_FOR_MANUAL_CHECK = [
    "CMD_GIS_INSERT",
    "CMD_GIS_RETRACT",
    "CMD_GIS_NEEDLE_IN",
    "CMD_GIS_NEEDLE_OUT",
    "CMD_GIS_1_INSERT",
    "CMD_GIS_1_RETRACT",
    "CMD_GIS_2_INSERT",
    "CMD_GIS_2_RETRACT",
    "CMD_GIS_VALVE_OPEN",
    "CMD_GIS_VALVE_CLOSE",
    "CMD_GAS_ON",
    "CMD_GAS_OFF",
    "CMD_NEEDLE_INSERT",
    "CMD_NEEDLE_RETRACT",
]


@dataclass
class ProbeEntry:
    mnemonic: str
    kind: str            # "analogue" | "digital"
    present: bool
    value: Optional[float] = None
    error_code: Optional[int] = None
    error: Optional[str] = None


@dataclass
class ProbeReport:
    entries: List[ProbeEntry] = field(default_factory=list)
    command_candidates: List[str] = field(default_factory=list)

    @property
    def present(self) -> List[ProbeEntry]:
        return [e for e in self.entries if e.present]

    def summary(self) -> str:
        lines = ["GIS Remote-API probe (READ-ONLY)"]
        found = self.present
        if found:
            lines.append("PRESENT parameters:")
            for e in found:
                v = "" if e.value is None else f" value={e.value}"
                lines.append(f"  + {e.mnemonic} ({e.kind}){v}")
        else:
            lines.append("No candidate GIS parameters resolved (as expected from the")
            lines.append("documented API). Try a SmartSEM macro path instead.")
        lines.append("")
        lines.append("Command mnemonics to check BY HAND in the SmartSEM editor")
        lines.append("(never auto-executed):")
        for c in self.command_candidates:
            lines.append(f"  ? {c}")
        return "\n".join(lines)


class GISProbe:
    """Runs the read-only candidate sweep against a live (or mock) API."""

    def __init__(
        self,
        analogue: Optional[List[str]] = None,
        digital: Optional[List[str]] = None,
        commands: Optional[List[str]] = None,
    ) -> None:
        self.analogue = analogue if analogue is not None else list(ANALOGUE_CANDIDATES)
        self.digital = digital if digital is not None else list(DIGITAL_CANDIDATES)
        self.commands = (
            commands if commands is not None
            else list(COMMAND_CANDIDATES_FOR_MANUAL_CHECK)
        )

    def probe(self, api: SmartSEMInterface) -> ProbeReport:
        report = ProbeReport(command_candidates=list(self.commands))
        for m in self.analogue:
            report.entries.append(self._probe_one(api, m, "analogue"))
        for m in self.digital:
            report.entries.append(self._probe_one(api, m, "digital"))
        return report

    def _probe_one(self, api: SmartSEMInterface, mnemonic: str, kind: str) -> ProbeEntry:
        try:
            if kind == "analogue":
                val = api.get_analogue(mnemonic)
            else:
                val = float(api.get_digital(mnemonic))
            return ProbeEntry(mnemonic=mnemonic, kind=kind, present=True, value=val)
        except SmartSEMError as exc:
            present = exc.code != API_E_GET_TRANSLATE_FAIL
            return ProbeEntry(
                mnemonic=mnemonic,
                kind=kind,
                present=present,
                error_code=exc.code,
                error=str(exc),
            )
