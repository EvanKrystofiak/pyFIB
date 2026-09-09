"""GIS control backend -- SCAFFOLD ONLY for v0.1.0.

The Remote API in the manual exposes NO gas-injection-system control (no needle
insert/retract, no precursor gas valve). This module defines the seam that a real
backend will implement once the control path is chosen:

  * a SmartSEM macro backend driven via api.execute("MCF_..."), or
  * a separate GIS controller (its own COM object / DLL / serial link).

Nothing here actuates hardware. StubGISBackend refuses everything; MockGISBackend
tracks state in memory for future deposition-sequence tests. The deposition state
machine (which will enforce: valve opens only while the needle is confirmed inserted;
valve closes before retract; and always-safe teardown on any exit) is the NEXT
deliverable and is intentionally not built yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple


class GISBackend(ABC):
    """Actuation seam for a single-or-multi-port GIS."""

    @abstractmethod
    def insert_needle(self, port: int) -> None: ...

    @abstractmethod
    def retract_needle(self, port: int) -> None: ...

    @abstractmethod
    def open_valve(self, port: int) -> None: ...

    @abstractmethod
    def close_valve(self, port: int) -> None: ...

    def needle_inserted(self, port: int) -> Optional[bool]:
        """True/False if a readback exists, else None (unknown / open-loop)."""
        return None

    def valve_open(self, port: int) -> Optional[bool]:
        return None


class StubGISBackend(GISBackend):
    """Placeholder that refuses all actuation. Used wherever a backend is required
    structurally but must not do anything (default until a real path exists)."""

    def _refuse(self, what: str, port: int) -> None:
        raise NotImplementedError(
            f"GIS control path not configured: {what}(port={port}). "
            "Resolve the API/macro path before enabling deposition."
        )

    def insert_needle(self, port: int) -> None:
        self._refuse("insert_needle", port)

    def retract_needle(self, port: int) -> None:
        self._refuse("retract_needle", port)

    def open_valve(self, port: int) -> None:
        self._refuse("open_valve", port)

    def close_valve(self, port: int) -> None:
        self._refuse("close_valve", port)


class MockGISBackend(GISBackend):
    """In-memory GIS with a call log, for offline tests of a future sequence.

    Models simple readbacks and records the exact order of operations so a test can
    later assert the safety invariants. It does NOT itself enforce those invariants --
    enforcement is the orchestrator's job.
    """

    def __init__(self, ports: Tuple[int, ...] = (1, 2)) -> None:
        self.ports = ports
        self._needle: Dict[int, bool] = {p: False for p in ports}
        self._valve: Dict[int, bool] = {p: False for p in ports}
        self.calls: List[Tuple[str, int]] = []

    def _validate(self, port: int) -> None:
        if port not in self.ports:
            raise ValueError(f"unknown GIS port {port}; known: {self.ports}")

    def insert_needle(self, port: int) -> None:
        self._validate(port)
        self.calls.append(("insert_needle", port))
        self._needle[port] = True

    def retract_needle(self, port: int) -> None:
        self._validate(port)
        self.calls.append(("retract_needle", port))
        self._needle[port] = False

    def open_valve(self, port: int) -> None:
        self._validate(port)
        self.calls.append(("open_valve", port))
        self._valve[port] = True

    def close_valve(self, port: int) -> None:
        self._validate(port)
        self.calls.append(("close_valve", port))
        self._valve[port] = False

    def needle_inserted(self, port: int) -> Optional[bool]:
        self._validate(port)
        return self._needle[port]

    def valve_open(self, port: int) -> Optional[bool]:
        self._validate(port)
        return self._valve[port]
