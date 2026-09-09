"""Read-only probe: which SmartSEM mnemonics actually exist on THIS instrument.

SAFETY -- this tool never writes. It calls only ``get_float`` and ``get_str``, and
that is enforced structurally by :class:`ReadOnlySem`, which raises if anything
tries to reach ``set_value``, ``execute``, ``move_stage``, ``grab_bmp`` or any
other non-read method. Commands (``CMD_*``) cannot be probed at all without
running them, so they are reported as un-probeable and never executed.

It is therefore safe to run with a sample loaded, though there is no reason not to
use a throwaway area anyway.

Error classification is the useful part. The API distinguishes:
    1000 GET_TRANSLATE_FAIL  -- the NAME is not recognised (mnemonic absent)
    1001 GET_AP_FAIL         -- name resolved as analogue, read failed
    1002 GET_DP_FAIL         -- name resolved as digital, read failed
    1003 GET_BAD_PARAMETER   -- neither analogue nor digital
    1025 NOT_LICENSED        -- present but not licensed
so we can tell "wrong name" from "right name, not available right now" -- which
is exactly the question when a mnemonic works on one machine and not another.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .catalog import CAPABILITIES, NOT_PROBEABLE, Candidate, Capability

# Error codes worth naming (from the API error table).
ERR_TRANSLATE_FAIL = 1000
ERR_AP_FAIL = 1001
ERR_DP_FAIL = 1002
ERR_BAD_PARAMETER = 1003
ERR_NOT_INITIALISED = 1019
ERR_NOT_LICENSED = 1025

_ERR_NAMES = {
    ERR_TRANSLATE_FAIL: "name not recognised",
    ERR_AP_FAIL: "analogue read failed",
    ERR_DP_FAIL: "digital read failed",
    ERR_BAD_PARAMETER: "not analogue or digital",
    ERR_NOT_INITIALISED: "API not initialised",
    ERR_NOT_LICENSED: "not licensed",
}

# Probe outcomes.
PRESENT = "present"        # read succeeded
UNAVAILABLE = "unavailable"  # name resolved, value could not be read right now
ABSENT = "absent"          # name not recognised
UNKNOWN = "unknown"        # failed for an unclassifiable reason


class ReadOnlyViolation(RuntimeError):
    """Raised if the probe ever attempts a non-read operation."""


class ReadOnlySem:
    """Wrapper exposing ONLY the read methods of a SmartSEM connection."""

    _ALLOWED = frozenset({"get_float", "get_str"})

    def __init__(self, sem):
        object.__setattr__(self, "_sem", sem)
        object.__setattr__(self, "calls", [])

    def __getattr__(self, name):
        if name not in ReadOnlySem._ALLOWED:
            raise ReadOnlyViolation(
                f"probe attempted a non-read operation: {name!r}. This tool is "
                f"read-only by construction."
            )
        target = getattr(object.__getattribute__(self, "_sem"), name)

        def _wrapped(*a, **kw):
            object.__getattribute__(self, "calls").append((name,) + tuple(a))
            return target(*a, **kw)

        return _wrapped

    def __setattr__(self, name, value):
        raise ReadOnlyViolation(f"probe attempted to set attribute {name!r}")


@dataclass
class ProbeResult:
    mnemonic: str
    kind: str
    status: str
    float_value: Optional[float] = None
    string_value: Optional[str] = None
    error_code: Optional[int] = None
    error_text: str = ""
    in_manual: bool = True
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PRESENT

    def display(self) -> str:
        if self.status == PRESENT:
            if self.string_value and self.float_value is not None:
                return f"{self.string_value}   (float {self.float_value:g})"
            if self.string_value:
                return str(self.string_value)
            if self.float_value is not None:
                return f"{self.float_value:g}"
            return "(read ok, empty)"
        detail = _ERR_NAMES.get(self.error_code or -1, self.error_text or "?")
        code = f" [{self.error_code}]" if self.error_code is not None else ""
        return f"-- {detail}{code}"


@dataclass
class CapabilityResult:
    key: str
    title: str
    why: str
    priority: int
    results: List[ProbeResult] = field(default_factory=list)

    @property
    def working(self) -> Optional[ProbeResult]:
        for r in self.results:
            if r.ok:
                return r
        return None


@dataclass
class ProbeReport:
    capabilities: List[CapabilityResult] = field(default_factory=list)
    not_probeable: List[Dict] = field(default_factory=list)
    created_unix_s: float = field(default_factory=time.time)
    serial: Optional[str] = None
    software_version: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "created_unix_s": self.created_unix_s,
            "serial": self.serial,
            "software_version": self.software_version,
            "capabilities": [
                {
                    "key": c.key, "title": c.title, "priority": c.priority,
                    "why": c.why,
                    "working_mnemonic": c.working.mnemonic if c.working else None,
                    "results": [asdict(r) for r in c.results],
                }
                for c in self.capabilities
            ],
            "not_probeable": self.not_probeable,
        }

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


def _classify(exc: Exception) -> tuple:
    """Map an exception to (status, code, text)."""
    code = getattr(exc, "code", None)
    try:
        code = int(code) if code is not None else None
    except (TypeError, ValueError):
        code = None
    text = str(exc)
    if code == ERR_TRANSLATE_FAIL:
        return ABSENT, code, text
    if code in (ERR_AP_FAIL, ERR_DP_FAIL, ERR_NOT_LICENSED):
        return UNAVAILABLE, code, text
    if code == ERR_BAD_PARAMETER:
        return ABSENT, code, text
    # Fall back to the message when no code is available.
    low = text.lower()
    if "translate" in low or "unrecognis" in low or "unrecogniz" in low:
        return ABSENT, code, text
    return UNKNOWN, code, text


def probe_one(sem: ReadOnlySem, cand: Candidate) -> ProbeResult:
    """Probe a single mnemonic with reads only.

    For AP_ the string form also reveals the UNITS (the API returns scaled strings
    like "= 10 mm"), which is exactly what we need to interpret a pixel size.
    For DP_ the string form gives the state NAME and the float gives its number.
    """
    res = ProbeResult(mnemonic=cand.mnemonic, kind=cand.kind, status=UNKNOWN,
                      in_manual=cand.in_manual, note=cand.note)

    got_any = False
    first_exc: Optional[Exception] = None

    # String form first: works for AP (scaled+units), DP (state name) and SV.
    try:
        sv = sem.get_str(cand.mnemonic)
        res.string_value = str(sv).strip() if sv is not None else None
        got_any = True
    except Exception as e:  # noqa: BLE001 - classified below
        first_exc = e

    if cand.kind in ("AP", "DP"):
        try:
            fv = sem.get_float(cand.mnemonic)
            res.float_value = float(fv)
            got_any = True
        except Exception as e:  # noqa: BLE001
            if first_exc is None:
                first_exc = e

    if got_any:
        res.status = PRESENT
        return res

    status, code, text = _classify(first_exc) if first_exc else (UNKNOWN, None, "")
    res.status, res.error_code, res.error_text = status, code, text
    return res


def run_probe(sem, *, logger: Optional[Callable[[str], None]] = None) -> ProbeReport:
    """Probe every catalogued mnemonic. Read-only by construction."""
    log = logger or (lambda m: None)
    ro = ReadOnlySem(sem)
    report = ProbeReport()

    # Machine identity first: the software version explains naming differences.
    for mn, attr in (("SV_SERIAL_NUMBER", "serial"), ("SV_VERSION", "software_version")):
        try:
            setattr(report, attr, str(ro.get_str(mn)).strip())
        except Exception:
            pass
    if report.serial or report.software_version:
        log(f"[probe] machine: serial={report.serial!r} "
            f"software={report.software_version!r}")

    for cap in CAPABILITIES:
        cr = CapabilityResult(key=cap.key, title=cap.title, why=cap.why,
                              priority=cap.priority)
        for cand in cap.candidates:
            r = probe_one(ro, cand)
            cr.results.append(r)
            log(f"[probe] {r.status.upper():12s} {cand.mnemonic:28s} {r.display()}")
        report.capabilities.append(cr)

    report.not_probeable = [
        {"mnemonic": c.mnemonic, "kind": c.kind, "in_manual": c.in_manual,
         "note": c.note,
         "reason": "commands cannot be tested without executing them"}
        for c in NOT_PROBEABLE
    ]
    return report


def format_report(report: ProbeReport) -> str:
    """Human-readable summary, ordered by priority."""
    L: List[str] = []
    L.append("=" * 78)
    L.append("pyFIB SmartSEM mnemonic probe (READ-ONLY)")
    if report.serial or report.software_version:
        L.append(f"  machine : {report.serial or '?'}")
        L.append(f"  software: {report.software_version or '?'}")
    L.append("=" * 78)

    labels = {1: "PRIORITY 1 - fixes something currently inferred or unsafe",
              2: "PRIORITY 2 - improves robustness",
              3: "PRIORITY 3 - worth recording",
              0: "ALREADY IN USE - health check"}

    for pri in (1, 2, 3, 0):
        caps = [c for c in report.capabilities if c.priority == pri]
        if not caps:
            continue
        L.append("")
        L.append(labels[pri])
        L.append("-" * 78)
        for c in caps:
            w = c.working
            head = f"  {c.title}"
            L.append(head)
            if pri != 0:
                L.append(f"     why: {c.why}")
            for r in c.results:
                mark = "OK " if r.ok else "   "
                doc = "" if r.in_manual else "  (not in manual)"
                L.append(f"     {mark}{r.mnemonic:28s} {r.display()}{doc}")
            if pri != 0:
                if w:
                    L.append(f"     => USE {w.mnemonic}")
                else:
                    L.append(f"     => none of these resolved; capability unavailable")

    L.append("")
    L.append("NOT PROBEABLE (would require executing a command)")
    L.append("-" * 78)
    for c in report.not_probeable:
        L.append(f"     {c['mnemonic']:28s} {c['note']}")

    # Headline conclusions.
    L.append("")
    L.append("SUMMARY")
    L.append("-" * 78)
    for c in report.capabilities:
        if c.priority == 0:
            continue
        w = c.working
        L.append(f"  [{'x' if w else ' '}] {c.title}"
                 + (f"  -> {w.mnemonic}" if w else "  -> unavailable"))
    health = next((c for c in report.capabilities if c.priority == 0), None)
    if health:
        bad = [r.mnemonic for r in health.results if not r.ok]
        L.append("")
        if bad:
            L.append(f"  !! health check FAILED for: {', '.join(bad)}")
            L.append("     pyFIB already depends on these -- investigate before milling.")
        else:
            L.append("  health check: all mnemonics pyFIB already uses read OK.")
    return "\n".join(L)
