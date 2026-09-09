"""gisdep -- standalone GIS deposition control tool for ZEISS Crossbeam / SmartSEM.

This is an early, largely instrument-UNTESTED tool developed separately from pyFIB
(mirroring the elymaker pattern) with the intent of eventual integration.

v0.1.0 scope: SEM autofocus (coarse + fine), working-distance gate, prerequisite
checks (stage idle), and a read-only probe to discover whether the Remote API exposes
any hidden GIS mnemonics. The GIS control path itself (needle insert/retract + gas
valve) is deliberately NOT wired to any real actuation yet -- see gisdep.gis.

Nothing in this package inserts a needle or opens a gas valve. The deposition
sequence is the next deliverable, once the GIS control path is confirmed.
"""

from gisdep.version import __version__

__all__ = ["__version__"]
