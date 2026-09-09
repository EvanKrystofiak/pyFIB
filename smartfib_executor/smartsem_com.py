from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Tuple

try:
    import pythoncom
    import pywintypes
    import win32com.client
except Exception as e:  # pragma: no cover
    # Allow importing this module on non-Windows systems for pattern parsing etc.
    pythoncom = None  # type: ignore
    pywintypes = None  # type: ignore
    win32com = None  # type: ignore
    _IMPORT_ERROR = e
else:
    _IMPORT_ERROR = None


# CLSID of the CZ EM API (CZEMApi) ActiveX control.
CZEMAPI_CLSID = "{71BD42C4-EBD3-11D0-AB3A-444553540000}"


class SmartSEMError(RuntimeError):
    """Raised when the SmartSEM Remote API returns a non-zero error code."""

    def __init__(self, message: str, code: Optional[int] = None, last_error: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.last_error = last_error


@dataclass(frozen=True)
class StagePosition:
    x_m: float
    y_m: float
    z_m: float
    tilt_deg: float
    rot_deg: float
    master_z_m: float


class SmartSEM:
    """Thin wrapper over the SmartSEM CZEMApi ActiveX (COM) control.

    Notes:
    - Windows only (pywin32).
    - Supports local Initialise("") and remoting InitialiseRemoting/InitialiseRemote.
    - Get/Set use VARIANT types; helper methods here request explicit float/string types.
    """

    def __init__(self, suppress_remoting_popups: bool = True):
        if _IMPORT_ERROR is not None:
            raise RuntimeError(
                "pywin32/pythoncom not available. This package must run on Windows with pywin32 installed."
            ) from _IMPORT_ERROR

        assert pythoncom is not None
        assert win32com is not None

        # SmartSEM OCX expects STA.
        pythoncom.CoInitialize()

        self.api = win32com.client.Dispatch(CZEMAPI_CLSID)

        # Optional: prevent popups when remoting connection is not ready.
        self._suppress_remoting_popups = suppress_remoting_popups
        self._connected = False
        self._mode: Optional[str] = None

    def close(self) -> None:
        if self._connected:
            try:
                self._call_ok(self.api.ClosingControl)
            except Exception:
                pass
        self._connected = False
        self._mode = None

    def __enter__(self) -> "SmartSEM":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------
    # Connection
    # ----------------------------
    def connect_local(self, machine: str = "") -> None:
        """Connect to a local EM Server using Initialise(machine)."""
        self._call_ok(self.api.Initialise, machine)
        self._connected = True
        self._mode = "local"

    def connect_remoting(self) -> None:
        """Connect via remoting using InitialiseRemoting / InitialiseRemote."""
        init = None
        if hasattr(self.api, "InitialiseRemoting"):
            init = getattr(self.api, "InitialiseRemoting")
        elif hasattr(self.api, "InitialiseRemote"):
            init = getattr(self.api, "InitialiseRemote")
        else:
            raise SmartSEMError("CZEMApi has no InitialiseRemoting/InitialiseRemote method")

        # IMPORTANT: On some SmartSEM/SmartFIB installations the OCX requires
        # SetSuppressRemotingConnectionErrors() to be called *before* InitialiseRemoting().
        # Calling it after InitialiseRemoting() can raise an error like:
        # "You cannot call SetSuppressRemotingConnectionErrors() after calling InitialiseRemoting(),
        #  the flag must be set first".
        if self._suppress_remoting_popups and hasattr(self.api, "SetSuppressRemotingConnectionErrors"):
            try:
                self._call_ok(self.api.SetSuppressRemotingConnectionErrors)
            except Exception:
                # Not fatal; depends on API version.
                pass

        self._call_ok(init)

        self._connected = True
        self._mode = "remoting"

    # ----------------------------
    # Low-level call helpers
    # ----------------------------
    def _split_rc_outs(self, res: Any) -> Tuple[int, Tuple[Any, ...]]:
        # Some COM calls return just rc (int).
        if isinstance(res, (int, float)):
            return int(res), tuple()
        # Some wrappers return (rc, out1, out2, ...)
        if isinstance(res, tuple) and res and isinstance(res[0], (int, float)):
            return int(res[0]), tuple(res[1:])
        # If we got only an out value, treat as success.
        return 0, (res,)

    def _get_last_error(self) -> Optional[str]:
        try:
            # Signature: GetLastError(VARIANT* Error). Like Get(), win32com may return
            # the [out] string in the RETURN (as (rc, error)) rather than mutating the
            # passed VARIANT -- handle both so error detail isn't lost.
            v = win32com.client.VARIANT(pythoncom.VT_BSTR, "")  # type: ignore[attr-defined]
            res = self.api.GetLastError(v)
            if isinstance(res, tuple):
                rc = res[0] if res else 0
                err = res[1] if len(res) > 1 else getattr(v, "value", "")
            else:
                rc = res
                err = getattr(v, "value", "")
            if int(rc) == 0 and err:
                return str(err)
        except Exception:
            return None
        return None

    def _call_ok(self, func, *args) -> Tuple[Any, ...]:
        try:
            res = func(*args)
        except pywintypes.com_error as e:  # type: ignore[attr-defined]
            raise SmartSEMError(f"COM error calling {getattr(func, '__name__', str(func))}: {e}") from e

        rc, outs = self._split_rc_outs(res)
        if rc != 0:
            raise SmartSEMError(
                f"{getattr(func, '__name__', 'COM call')} failed with rc={rc}",
                code=rc,
                last_error=self._get_last_error(),
            )
        return outs

    # ----------------------------
    # Core API surface
    # ----------------------------
    def execute(self, command: str) -> None:
        """Execute CMD_... or macro MCF_/MCL_..."""
        self._call_ok(self.api.Execute, command)

    def _get_r4(self, mnemonic: str) -> float:
        """Read a parameter as a number, forcing VT_R4 and taking the RETURNED value.

        Tries a plain-float prime first (`Get(param, 0.0)` -> use the return), exactly
        like gisdep's ComSmartSEM; falls back to an explicit VT_R4 VARIANT. The value
        comes from the return tuple, never the passed VARIANT's .value (which stays
        stale -- the bug behind DP_AUTO_FUNCTION / GIS needles reading 0).
        """
        try:
            outs = self._call_ok(self.api.Get, mnemonic, 0.0)
            if outs:
                return float(outs[0])
        except (TypeError, ValueError):
            pass
        v = win32com.client.VARIANT(pythoncom.VT_R4, 0.0)  # type: ignore[attr-defined]
        outs = self._call_ok(self.api.Get, mnemonic, v)
        if outs:
            return float(outs[0])
        return float(getattr(v, "value", v))

    def get_float(self, mnemonic: str) -> float:
        """Get value requesting VT_R4 (float) when possible."""
        # Try the python-friendly calling convention first.
        try:
            outs = self._call_ok(self.api.Get, mnemonic)
            if outs:
                v = outs[0]
                if isinstance(v, (int, float)):
                    return float(v)
                # fallthrough for strings etc
        except TypeError:
            pass
        return self._get_r4(mnemonic)

    def get_str(self, mnemonic: str) -> str:
        """Get value requesting VT_BSTR (string) when possible."""
        try:
            outs = self._call_ok(self.api.Get, mnemonic)
            if outs:
                return str(outs[0])
        except TypeError:
            pass

        v = win32com.client.VARIANT(pythoncom.VT_BSTR, "")  # type: ignore[attr-defined]
        outs = self._call_ok(self.api.Get, mnemonic, v)
        if outs:
            return str(outs[0])
        return str(getattr(v, "value", v))

    def set_value(self, mnemonic: str, value: Any) -> None:
        """Set AP_/DP_/SV_ values.

        For analogue parameters, pass float in SI units (e.g., metres).
        For DP_ states, pass a state string (e.g., "Off") or a numeric code.
        For SV_ strings, pass python str.
        """
        self._call_ok(self.api.Set, mnemonic, value)

    # ----------------------------
    # Stage helpers
    # ----------------------------
    def get_stage_position(self) -> StagePosition:
        """Read stage position using GetStagePosition()."""
        # Signature: GetStagePosition(VARIANT* x, VARIANT* y, VARIANT* z, VARIANT* t, VARIANT* r, VARIANT* m)
        v = [win32com.client.VARIANT(pythoncom.VT_R4, 0.0) for _ in range(6)]  # type: ignore[attr-defined]
        self._call_ok(self.api.GetStagePosition, *v)
        vals = [float(getattr(x, "value", x)) for x in v]
        return StagePosition(vals[0], vals[1], vals[2], vals[3], vals[4], vals[5])

    def move_stage(self, x_m: float, y_m: float, z_m: float, t_deg: float, r_deg: float, m_m: float) -> None:
        """Move stage using MoveStage(x,y,z,t,r,m). Non-blocking."""
        self._call_ok(self.api.MoveStage, float(x_m), float(y_m), float(z_m), float(t_deg), float(r_deg), float(m_m))

    # --- gisdep SmartSEMInterface surface (delegates to the methods above) -----
    # Lets pyFIB's proven connection be used wherever a gisdep SmartSEMInterface is
    # expected, and lets pyFIB code migrate to the clearer analogue/digital split.
    def initialise(self) -> None:
        """Open the connection (remoting, falling back to local)."""
        try:
            self.connect_remoting()
        except Exception:
            self.connect_local()

    def get_analogue(self, param: str) -> float:
        """AP_* as a float in SI units."""
        return float(self.get_float(param))

    def get_digital(self, param: str) -> int:
        """DP_* as its integer state value (forced VT_R4, value from the return)."""
        return int(round(self._get_r4(param)))

    def get_digital_text(self, param: str) -> str:
        """DP_* as its state name."""
        return str(self.get_str(param))

    def set_analogue(self, param: str, value: float) -> None:
        self.set_value(param, float(value))

    def set_digital(self, param, value) -> None:
        self.set_value(param, value)

    def _get_stage_is_code(self) -> Optional[int]:
        """Best-effort getter for DP_STAGE_IS as an integer (0=Idle, 1=Busy)."""
        try:
            return int(self.get_float("DP_STAGE_IS"))
        except Exception:
            pass
        try:
            s = self.get_str("DP_STAGE_IS").strip()
            if s.isdigit():
                return int(s)
            s_low = s.lower()
            if s_low == "idle":
                return 0
            if s_low == "busy":
                return 1
        except Exception:
            return None
        return None

    def wait_stage_idle(self, timeout_s: float = 120.0, poll_s: float = 0.25) -> None:
        """Wait for DP_STAGE_IS to become Idle (0)."""
        t0 = time.time()
        while True:
            code = self._get_stage_is_code()
            if code == 0:
                return
            if time.time() - t0 > timeout_s:
                raise SmartSEMError(f"Timeout waiting for stage idle (DP_STAGE_IS). Got {code!r}.")
            time.sleep(poll_s)

    # ----------------------------
    # Imaging helper
    # ----------------------------
    def grab_bmp(
        self,
        filename: str,
        *,
        xoff: int = 0,
        yoff: int = 0,
        width: int = 1024,
        height: int = 768,
        reduction: int = 0,
    ) -> None:
        """Grab an image and save as BMP using Grab()."""
        self._call_ok(
            self.api.Grab,
            int(xoff),
            int(yoff),
            int(width),
            int(height),
            int(reduction),
            str(filename),
        )

    # ----------------------------
    # Convenience waits
    # ----------------------------
    def wait_for_fib_mode(self, desired_state_num: int, timeout_s: float = 60.0, poll_s: float = 0.25) -> None:
        """Wait until DP_FIB_MODE equals desired_state_num."""
        t0 = time.time()
        while True:
            cur = int(self.get_float("DP_FIB_MODE"))
            if cur == desired_state_num:
                return
            if time.time() - t0 > timeout_s:
                raise SmartSEMError(f"Timeout waiting for DP_FIB_MODE == {desired_state_num} (got {cur})")
            time.sleep(poll_s)
