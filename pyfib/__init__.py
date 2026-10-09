"""pyFIB project.

This package is a thin compatibility wrapper around the internal implementation.

Primary goal:
- Provide a stable module name (pyfib) for launching CLI/UI on the main control PC.

Implementation note:
- The underlying implementation currently lives in the `smartfib_executor` package.
  This keeps backwards compatibility with earlier automation scripts.
"""

__all__ = ["__version__"]

# Keep version in one place.
__version__ = "0.51.2"
