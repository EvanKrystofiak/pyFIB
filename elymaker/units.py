"""Number and unit-suffix formatting to match native SmartFIB output.

SmartFIB writes floats in C++ ``%g`` style (6 significant figures) and, on the
build that produced the example files, pads scientific exponents to 3 digits
with an explicit sign (e.g. ``1.5e-008``, ``7e-010``, ``6e-005``).

Correctness of the .ely file does NOT depend on reproducing that exact string
(SmartFIB parses floats), but matching it keeps generated files visually native
and makes round-trip diffs against real files clean.

Suffixed physical quantities are stored internally in SI (metres, amps,
degrees) and rendered with their unit suffix on write:
    z_extent -> " m",  angle/a1/a2/area_fast -> " deg",
    current  -> " A",  diameter              -> " m"
Bare geometry numbers (x, y, width, cx, r1, points, frame_size) are in
micrometres and carry no suffix.
"""

from __future__ import annotations

import re

_SCI = re.compile(r"^([+-]?[0-9.]+)[eE]([+-]?)(\d+)$")


def fmt_num(value: float) -> str:
    """Format a float the way SmartFIB does: ``%g`` with 3-digit exponents.

    >>> fmt_num(1.5e-8)
    '1.5e-008'
    >>> fmt_num(7e-10)
    '7e-010'
    >>> fmt_num(617.797)
    '617.797'
    >>> fmt_num(0.0)
    '0'
    >>> fmt_num(-6.79084)
    '-6.79084'
    >>> fmt_num(180.0)
    '180'
    """
    s = "%g" % (value,)
    m = _SCI.match(s)
    if not m:
        return s
    mant, sign, digits = m.group(1), m.group(2), m.group(3)
    if sign == "":
        sign = "+"
    return "%se%s%03d" % (mant, sign, int(digits))


def parse_suffixed(raw: str) -> float:
    """Parse ``"1.5e-008 m"`` / ``"180 deg"`` / ``"7e-010 A"`` -> float.

    The unit suffix is discarded; the numeric value is returned in the file's
    native unit (SI for suffixed quantities). A bare number is also accepted.
    """
    token = raw.strip().split()[0]
    return float(token)


def fmt_suffixed(value: float, unit: str) -> str:
    """Render a value with its unit suffix, e.g. ``fmt_suffixed(180, "deg")``."""
    return "%s %s" % (fmt_num(value), unit)
