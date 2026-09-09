"""Bridge: run the vendored gisdep code on pyFIB's SmartSEM connection.

gisdep talks to the instrument through its ``gisdep.api.SmartSEMInterface`` (an ABC
with get_analogue / get_digital / get_digital_text / set_analogue / set_digital /
execute / move_stage / get_stage_position). pyFIB has its own concrete
``smartfib_executor.smartsem_com.SmartSEM`` (get_float / get_str / set_value / ...).

This adapter presents a pyFIB ``SmartSEM`` AS a gisdep ``SmartSEMInterface``, so the
GIS deposition / orchestration code runs on pyFIB's single COM connection WITHOUT
being modified. It also translates pyFIB's ``SmartSEMError`` into gisdep's, so
gisdep's own ``except SmartSEMError`` handling keeps working.

Phase 1: neither codebase is changed -- gisdep is vendored verbatim, and this is the
only new code. The GUI wiring and de-duplication of the overlapping modules come in
later phases.
"""

from __future__ import annotations

from typing import Union

from gisdep.api.interface import SmartSEMInterface, StagePosition, SmartSEMError
from .smartsem_com import SmartSEM, SmartSEMError as _PyfibError


def _translate(exc: Exception) -> SmartSEMError:
    code = getattr(exc, "code", None)
    try:
        code = int(code) if code is not None else 0
    except (TypeError, ValueError):
        code = 0
    return SmartSEMError(code, str(exc))


class PyfibSmartSEM(SmartSEMInterface):
    """gisdep SmartSEMInterface backed by a pyFIB SmartSEM connection."""

    def __init__(self, sem: SmartSEM, *, connect: str = "remoting",
                 machine: str = ""):
        self._sem = sem
        self._connect_mode = connect
        self._machine = machine

    # --- lifecycle ---
    def initialise(self) -> None:
        try:
            if self._connect_mode == "local":
                self._sem.connect_local(self._machine)
            else:
                self._sem.connect_remoting()
        except _PyfibError as e:
            raise _translate(e)

    def close(self) -> None:
        try:
            self._sem.close()
        except _PyfibError:
            pass  # close is best-effort / idempotent

    # --- reads ---
    def get_analogue(self, param: str) -> float:
        try:
            return float(self._sem.get_float(param))
        except _PyfibError as e:
            raise _translate(e)

    def get_digital(self, param: str) -> int:
        try:
            # Use SmartSEM.get_digital (forced VT_R4), NOT get_float -- get_float's
            # single-arg path can return an unreliable value for some DPs (this is
            # what made GIS needles read as transit=0 through the bridge).
            return int(self._sem.get_digital(param))
        except _PyfibError as e:
            raise _translate(e)

    def get_digital_text(self, param: str) -> str:
        try:
            return str(self._sem.get_str(param))
        except _PyfibError as e:
            raise _translate(e)

    def get_stage_position(self) -> StagePosition:
        try:
            p = self._sem.get_stage_position()
        except _PyfibError as e:
            raise _translate(e)
        # pyFIB uses master_z_m; gisdep uses m_m -- same value, different name.
        return StagePosition(
            x_m=p.x_m, y_m=p.y_m, z_m=p.z_m,
            tilt_deg=p.tilt_deg, rot_deg=p.rot_deg, m_m=p.master_z_m,
        )

    # --- writes ---
    def set_analogue(self, param: str, value: float) -> None:
        try:
            self._sem.set_value(param, float(value))
        except _PyfibError as e:
            raise _translate(e)

    def set_digital(self, param: str, value: Union[int, str]) -> None:
        try:
            self._sem.set_value(param, value)
        except _PyfibError as e:
            raise _translate(e)

    # --- actions ---
    def move_stage(self, x: float, y: float, z: float, t: float, r: float,
                   m: float) -> None:
        try:
            self._sem.move_stage(x, y, z, t, r, m)   # same axis order (x,y,z,t,r,m)
        except _PyfibError as e:
            raise _translate(e)

    def execute(self, command: str) -> None:
        try:
            self._sem.execute(command)
        except _PyfibError as e:
            raise _translate(e)


def bridged_api(sem: SmartSEM, *, connect: str = "remoting",
                machine: str = "") -> SmartSEMInterface:
    """Wrap a pyFIB SmartSEM so gisdep code can use it. Does not connect yet --
    call ``.initialise()`` (as gisdep does) to open the connection."""
    return PyfibSmartSEM(sem, connect=connect, machine=machine)
