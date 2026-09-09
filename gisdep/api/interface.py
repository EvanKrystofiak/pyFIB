"""Abstract SmartSEM Remote API interface.

Both the faithful offline mock (gisdep.api.mock) and the real pywin32/COM backend
(gisdep.api.com) implement this. Keeping the surface small and explicit lets the
autofocus / gate / probe logic be written and tested with zero COM dependency.

Return-code semantics follow the SmartSEM Remote API manual (section 7). A non-zero
return code from a Get/Set/Execute is surfaced as a SmartSEMError carrying that code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Union


@dataclass
class StagePosition:
    """Stage position as returned by the Remote API GetStagePosition method (§4.10).

    Linear axes are in metres; tilt and rotation are in DEGREES per the manual's
    GetStagePosition documentation. The degree convention is not yet instrument-
    confirmed for gisdep -- verify with the flat stage (expect ~0) and a tilted stage
    (expect ~30) and turn the result into a regression test.
    """

    x_m: float
    y_m: float
    z_m: float
    tilt_deg: float
    rot_deg: float
    m_m: float


class SmartSEMError(Exception):
    """Raised when a Remote API call returns a non-zero error code.

    ``code`` is the numeric API_E_* value from manual section 7 (e.g. 1000 =
    API_E_GET_TRANSLATE_FAIL, 1019 = API_E_NOT_INITIALISED).
    """

    def __init__(self, code: int, message: str = "", param: str = "") -> None:
        self.code = int(code)
        self.param = param
        detail = message or ""
        if param:
            detail = f"{detail} (param={param})".strip()
        super().__init__(f"[rc={self.code}] {detail}".strip())


class SmartSEMInterface(ABC):
    """Minimal typed wrapper over the CZEMApi ActiveX control."""

    @abstractmethod
    def initialise(self) -> None:
        """Initialise the connection. Raises SmartSEMError on failure."""

    @abstractmethod
    def close(self) -> None:
        """ClosingControl(). Safe to call more than once."""

    # --- reads -------------------------------------------------------------
    @abstractmethod
    def get_analogue(self, param: str) -> float:
        """Get an AP_* parameter as a float in SI units (e.g. AP_WD in metres)."""

    @abstractmethod
    def get_digital(self, param: str) -> int:
        """Get a DP_* parameter as its integer state value (e.g. DP_STAGE_IS -> 0/1)."""

    @abstractmethod
    def get_digital_text(self, param: str) -> str:
        """Get a DP_* parameter as its state name (e.g. 'Idle')."""

    @abstractmethod
    def get_stage_position(self) -> "StagePosition":
        """GetStagePosition (§4.10): x/y/z/m in metres, tilt/rotation in degrees."""

    @abstractmethod
    def move_stage(
        self, x: float, y: float, z: float, t: float, r: float, m: float
    ) -> None:
        """MoveStage (§4.11): ABSOLUTE move, all axes. x/y/z/m metres, t/r degrees.

        Non-blocking on the instrument -- poll DP_STAGE_IS (and/or the position) to
        detect completion. Callers should use gisdep.stage.StageMover rather than this
        raw method, which has no safety checks.
        """

    # --- writes ------------------------------------------------------------
    @abstractmethod
    def set_analogue(self, param: str, value: float) -> None:
        """Set an AP_* parameter (SI units). Subject to instrument limit checks."""

    @abstractmethod
    def set_digital(self, param: str, value: Union[int, str]) -> None:
        """Set a DP_* parameter by state number or state name."""

    # --- actions -----------------------------------------------------------
    @abstractmethod
    def execute(self, command: str) -> None:
        """Execute a CMD_* command (or MCF_/MCL_ macro). Raises on failure."""
