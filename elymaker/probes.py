"""Standard FIB probe library, seeded verbatim from the example .ely files.

Each entry maps a probe *name* (as SmartFIB writes it) to its beam current (A)
and nominal spot diameter (m). These are the ``type="specific"`` probes that
appeared across the real files. The values are exactly as authored on that
instrument -- treat them as that instrument's probe table, not universal
constants. If your column reports different currents/diameters for a named
probe, override via :func:`register_probe` before authoring.

INSTRUMENT NOTE: these currents/diameters were read from files, not measured on
your Crossbeam this session. Confirm they match your current probe table.
"""

from __future__ import annotations

from typing import Dict

from .model import Probe

# name -> (current_A, diameter_m)
_SEED = {
    "30kV:10pA": (1e-11, 2.5e-8),
    "30kV:20pA": (2e-11, 3.5e-8),
    # 50 pA: SmartFIB names this probe "30kV:50pA ref" (trailing " ref") with a 45 nm
    # spot -- CONFIRMED from a SmartFIB-corrected .ely (Corrected.ely). The earlier
    # "30kV:50pA" / 50 nm entry was an estimate that SmartFIB did not recognise.
    "30kV:50pA ref": (5e-11, 4.5e-8),
    "30kV:100pA": (1e-10, 7e-8),
    "30kV:300pA": (3e-10, 1.75e-7),
    "30kV:700pA": (7e-10, 2.1e-7),
    "30kV:1.5nA": (1.5e-9, 2.4e-7),
    "30kV:3nA": (3e-9, 3.4e-7),        # diameter ESTIMATED (interpolated)
    "30kV:7nA": (7e-9, 5.1e-7),        # diameter ESTIMATED (interpolated)
    "30kV:15nA": (1.5e-8, 7.5e-7),
    "30kV:30nA": (3e-8, 1.05e-6),      # diameter ESTIMATED (extrapolated)
}
# NOTE: the four probes marked ESTIMATED have interpolated/extrapolated spot
# diameters (the currents are exact). Diameter is display/metadata; the current
# identifies the probe and drives milling. Replace with real SmartFIB values
# (or values read from a real .ely PROBE line) when available.

PROBES: Dict[str, Probe] = {
    name: Probe(name=name, type_="specific", current_A=cur, diameter_m=dia)
    for name, (cur, dia) in _SEED.items()
}
# Alias: the plain key still resolves, but the Probe it returns carries the REAL
# SmartFIB name ("30kV:50pA ref") so the written .ely is correct.
PROBES["30kV:50pA"] = PROBES["30kV:50pA ref"]
_ALIASES = {"30kV:50pA"}   # resolvable keys that are not distinct probes


def get_probe(name: str) -> Probe:
    """Return a copy of the named standard probe.

    Raises KeyError with the list of known names if not found.
    """
    try:
        p = PROBES[name]
    except KeyError:
        raise KeyError(
            "Unknown probe %r. Known probes: %s"
            % (name, ", ".join(sorted(PROBES)))
        )
    return Probe(name=p.name, type_=p.type_, current_A=p.current_A, diameter_m=p.diameter_m)


def register_probe(name: str, current_A: float, diameter_m: float, type_: str = "specific") -> Probe:
    """Add or override a probe in the library and return it."""
    p = Probe(name=name, type_=type_, current_A=current_A, diameter_m=diameter_m)
    PROBES[name] = p
    return p


def probe_names_by_current() -> list:
    """Probe names ordered by beam current ascending (pA -> nA).

    Sorting by the physical current (not the name string) keeps the list in a
    sensible ramp -- 10pA, 20pA, 100pA, ... 15nA -- rather than the misleading
    alphabetical order where '15nA' sorts near '100pA'.
    """
    return sorted((n for n in PROBES if n not in _ALIASES),
                  key=lambda n: PROBES[n].current_A)
