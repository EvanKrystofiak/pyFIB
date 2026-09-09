"""Real CZEMApi ActiveX backend via pywin32/COM.

!!! INSTRUMENT-UNTESTED marshalling !!!
The Get/Set/Execute/GetStagePosition VARIANT handling has NOT been exercised against a
microscope from within gisdep and must be reconciled with pyFIB's proven COM layer
before a live run. win32com is imported lazily so the rest of gisdep imports and tests
cleanly on non-Windows machines.

Initialisation (fixed): on a 64-bit process the control rejects Initialise() with
"Initialise() is not supported for 64bit processes. Please use InitialiseRemoting()".
gisdep is 64-bit Python, so the default init_mode is "remoting". The control's remoting
init method name is inconsistent in the manual (section title "InitialiseRemoting" vs
the C++ signature "InitialiseRemote(void)"), so we try both and use whichever exists.

Remoting prerequisites (one-time, per the manual): RemoteRelay.exe running on the EM
Server PC, and RConfigure run with client/remote IPs set to "localhost" when the client
is on the same PC as the server.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

from gisdep.api.interface import SmartSEMInterface, SmartSEMError, StagePosition


def _load_win32():
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
        import pywintypes  # type: ignore
    except Exception as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "pywin32 is required for the COM backend and is only available on the "
            "Windows microscope PC. Use MockSmartSEM for offline work."
        ) from exc
    return win32com.client, pythoncom, pywintypes


# Remoting init method names to try, in order (manual is inconsistent).
_REMOTING_INIT_NAMES = ("InitialiseRemoting", "InitialiseRemote")


class ComSmartSEM(SmartSEMInterface):
    """Thin COM wrapper.

    ``init_mode``:
      * "remoting" (default) -- required for 64-bit processes; uses the .NET Remoting
        go-between (Interceptor/RemoteRelay).
      * "local" -- direct Initialise(machine); only works from a 32-bit process on the
        EM Server PC.

    ``dispatch_factory`` is a test seam: when provided, it is called instead of
    win32com.Dispatch to obtain the control object, so the init-selection logic can be
    unit-tested without COM.
    """

    PROG_ID = "CZ.EMApiCtrl.1"  # ProgID for the CZ EM API control; verify on the PC.

    def __init__(
        self,
        machine: str = "",
        init_mode: str = "remoting",
        dispatch_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        if init_mode not in ("remoting", "local"):
            raise ValueError("init_mode must be 'remoting' or 'local'")
        self._machine = machine
        self._init_mode = init_mode
        self._dispatch_factory = dispatch_factory
        self._api = None
        self._init_method_used: Optional[str] = None

    # --- lifecycle ---------------------------------------------------------
    def initialise(self) -> None:
        if self._dispatch_factory is not None:
            self._api = self._dispatch_factory()
        else:  # pragma: no cover - requires pywin32
            win32com_client, _, _ = _load_win32()
            self._api = win32com_client.Dispatch(self.PROG_ID)

        if self._init_mode == "local":
            rc = self._api.Initialise(self._machine)
            if rc:
                raise SmartSEMError(
                    rc, "Initialise failed (use init_mode='remoting' on 64-bit)"
                )
            self._init_method_used = "Initialise"
            return

        rc, used = self._remoting_init()
        self._init_method_used = used
        if rc:
            raise SmartSEMError(rc, f"{used} failed")

    def _remoting_init(self):
        """Call whichever remoting init method the control exposes.

        Returns (rc, method_name). A missing/failed method name is skipped; a method
        that exists and returns a non-zero rc is a real error and is returned as-is
        (we do NOT fall through to the other name on a genuine rc failure).
        """
        errors = []
        for name in _REMOTING_INIT_NAMES:
            try:
                method = getattr(self._api, name)
            except AttributeError as exc:
                errors.append(f"{name}: {exc}")
                continue
            try:
                rc = method()
            except Exception as exc:  # unknown-name com_error, etc. -> try next name
                errors.append(f"{name}: {exc}")
                continue
            return (rc or 0), name
        raise RuntimeError(
            "no usable remoting init method on the CZEMApi control ("
            + "; ".join(errors) + ")"
        )

    def close(self) -> None:
        if self._api is not None:
            try:
                self._api.ClosingControl()
            finally:
                self._api = None

    def _check(self, rc: int, what: str, param: str = "") -> None:
        if rc:
            raise SmartSEMError(rc, what, param)

    # --- reads -------------------------------------------------------------
    def get_analogue(self, param: str) -> float:
        # NOTE: Get takes an [in][out] VARIANT. The exact pywin32 marshalling of the
        # out value must be verified against pyFIB's working implementation.
        rc, value = self._api.Get(param, 0.0)
        self._check(rc, "Get(analogue)", param)
        return float(value)

    def get_digital(self, param: str) -> int:
        rc, value = self._api.Get(param, 0.0)
        self._check(rc, "Get(digital)", param)
        return int(round(float(value)))

    def get_digital_text(self, param: str) -> str:
        rc, value = self._api.Get(param, "")
        self._check(rc, "Get(digital text)", param)
        return str(value)

    def get_stage_position(self) -> StagePosition:
        # GetStagePosition has six [out] VARIANT params and a long return. The exact
        # pywin32 return shape MUST be verified against pyFIB's working code -- most
        # likely (rc, x, y, z, t, r, m). Tilt/rotation are DEGREES per manual 4.10.
        result = self._api.GetStagePosition()
        try:
            rc, x, y, z, t, r, m = result
        except (TypeError, ValueError) as exc:  # pragma: no cover - shape unknown here
            raise SmartSEMError(
                1017,
                "unexpected GetStagePosition return shape; reconcile with pyFIB "
                f"(got {result!r})",
            ) from exc
        self._check(rc, "GetStagePosition")
        return StagePosition(
            x_m=float(x), y_m=float(y), z_m=float(z),
            tilt_deg=float(t), rot_deg=float(r), m_m=float(m),
        )

    def move_stage(self, x, y, z, t, r, m) -> None:
        # MoveStage (§4.11) is non-blocking; the caller polls DP_STAGE_IS / position.
        rc = self._api.MoveStage(
            float(x), float(y), float(z), float(t), float(r), float(m)
        )
        self._check(rc, "MoveStage")

    # --- writes ------------------------------------------------------------
    def set_analogue(self, param: str, value: float) -> None:
        rc = self._api.Set(param, float(value))
        self._check(rc, "Set(analogue)", param)

    def set_digital(self, param: str, value: Union[int, str]) -> None:
        rc = self._api.Set(param, value)
        self._check(rc, "Set(digital)", param)

    def execute(self, command: str) -> None:
        rc = self._api.Execute(command)
        self._check(rc, "Execute", command)
