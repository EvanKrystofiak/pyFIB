from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .pattern_ely import ELayoutPattern
from .smartsem_com import SmartSEM, SmartSEMError


def _sv_user_dev_name(slot: int) -> str:
    if slot not in (1, 2, 3, 4, 5):
        raise ValueError("SV_USER_DEV slot must be 1..5")
    return f"SV_USER_DEV_{slot}"


FIB_MODE_NAME_TO_CODE = {
    # DP_FIB_MODE: 0 Imaging, 1 Remote, 2 Milling, 3 Deposition, 4 To Imaging,
    # 5 To Milling, 6 To Deposition, 7 To Remote
    "Imaging": 0,
    "Remote": 1,
    "Milling": 2,
    "Deposition": 3,
    "To Imaging": 4,
    "To Milling": 5,
    "To Deposition": 6,
    "To Remote": 7,
}

FIB_IMAGING_NAME_TO_CODE = {
    # DP_FIB_IMAGING:
    #   0 SEM, 1 FIB, 2 Milling, 3 SEM+FIB, 4 Mill+SEM,
    #   5 Drift SEM, 6 Drift FIB, 7 None, 8 Ext, 9 Ext+SEM
    #
    # NOTE: Some installations return these as strings (preferred) and some as
    # numeric codes. Some can also return mismatched numeric codes (e.g. 0) while
    # the string still reflects the correct state.
    "SEM": 0,
    "FIB": 1,
    "MILLING": 2,
    "SEM+FIB": 3,
    "SEMFIB": 3,
    "MILL+SEM": 4,
    "MILLSEM": 4,
    "DRIFTSEM": 5,
    "DRIFTFIB": 6,
    "NONE": 7,
    "EXT": 8,
    "EXT+SEM": 9,
    "EXTSEM": 9,
}


MILLING_IMAGING_CODES = {2, 4}  # Milling, Mill+SEM



@dataclass
class PatternJob:
    """All inputs needed to execute a SmartFIB milling pattern at current stage location."""

    # Local path (on the computer running Python) to the .ely file
    ely_local_path: Path

    # How to load the pattern into SmartFIB before starting milling.
    #
    # - smartfib_drop: SerialFIB-style. Copy file to SmartFIB API drop folder as
    #   ApiLayout.ely, then execute:
    #       CMD_SMARTFIB_LOAD_ELY
    #       CMD_SMARTFIB_PREPARE_EXPOSURE
    #
    # - macro: legacy. Set SV_USER_DEV_N and run a SmartSEM macro to load.
    #
    # - none: assume pattern is already loaded/armed in SmartFIB.
    load_method: Literal["smartfib_drop", "macro", "none"] = "smartfib_drop"

    # ----- smartfib_drop settings -----
    smartfib_drop_dir: Path = Path(r"C:\ProgramData\Carl Zeiss\SmartFIB\API\Drop")
    smartfib_drop_name: str = "ApiLayout.ely"
    smartfib_load_cmd: str = "CMD_SMARTFIB_LOAD_ELY"
    smartfib_prepare_cmd: str = "CMD_SMARTFIB_PREPARE_EXPOSURE"
    smartfib_load_delay_s: float = 1.0
    smartfib_prepare_delay_s: float = 1.0
    # Some SmartFIB installations expose a DP_PATTERNING_MODE state. SerialFIB sets it
    # to "FIB" after starting milling.
    set_patterning_mode_fib: bool = True
    patterning_mode_state: str = "DP_PATTERNING_MODE"
    patterning_mode_value: str = "FIB"

    # ----- macro settings (legacy) -----
    # Path *as seen by SmartSEM/SmartFIB* on the microscope PC.
    ely_microscope_path: Optional[str] = None
    # Communicate the pattern path to SmartSEM macros via SV_USER_DEV_N
    sv_user_dev_slot: int = 1
    # Macro that loads the pattern path from SV_USER_DEV_N into SmartFIB
    load_pattern_macro: Optional[str] = None

    # Optional macros
    pre_macro: Optional[str] = None
    post_macro: Optional[str] = None

    # Milling behavior
    # NOTE: SerialFIB does *not* call CMD_FIB_MODE_MILL; it starts milling directly after
    # loading/preparing the SmartFIB layout. On some systems CMD_FIB_MODE_MILL can hang.
    # Therefore the default here is False; enable only if you know you need it.
    ensure_fib_mode_mill: bool = False
    arm: bool = False  # require explicit arming for any machine-affecting action

    # Waiting strategy after CMD_FIB_START_MILLING
    #
    # - mode_change: SerialFIB-like, waits for DP_FIB_MODE to enter then leave Milling.
    # - blanked: waits for DP_FIB_BLANKED to become stably "Yes" (beam blanked)
    # - auto: returns when either mode_change triggers OR DP_FIB_BLANKED becomes stably "Yes"
    # - sleep: just sleep a fixed time (no feedback)
    # - none: do not wait
    wait_strategy: Literal["none", "mode_change", "blanked", "auto", "sleep"] = "mode_change"

    # How long to wait (seconds). Use 0 for "no timeout" (wait indefinitely).
    wait_timeout_s: float = 5400.0

    # Poll interval while waiting.
    wait_poll_s: float = 1.0

    # Print a status line at this interval while waiting.
    wait_status_every_s: float = 15.0

    # Used by blanked/auto strategies: how long DP_FIB_BLANKED must remain "Yes" to
    # be considered finished (avoids false positives during brief inter-exposure blanks).
    blanked_stable_s: float = 10.0

    # Completion confirmation: once a "done" signature is detected, require it to
    # remain true for this many seconds before declaring completion.
    #
    # This protects against transient DP_FIB_MODE / DP_FIB_BLANKED changes that can
    # otherwise cause false-positive "milling finished" detections during long runs.
    done_confirm_s: float = 30.0

    # used when wait_strategy == 'sleep'
    sleep_s: float = 0.0


class PatternRunner:
    def __init__(self, sem: SmartSEM):
        self.sem = sem

    # ----------------------------
    # Internal helpers
    # ----------------------------
    def _smartfib_drop_target(self, job: PatternJob) -> Path:
        return job.smartfib_drop_dir / job.smartfib_drop_name

    def _copy_to_smartfib_drop(self, job: PatternJob) -> None:
        target = self._smartfib_drop_target(job)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job.ely_local_path, target)

    def _set_pattern_path_for_macro(self, job: PatternJob) -> None:
        if not job.ely_microscope_path:
            raise ValueError("ely_microscope_path must be set when load_method='macro'")
        sv_name = _sv_user_dev_name(job.sv_user_dev_slot)
        self.sem.set_value(sv_name, job.ely_microscope_path)

    def _get_fib_mode_code(self) -> Optional[int]:
        """Best-effort getter for DP_FIB_MODE as an integer code."""
        try:
            # Many installations return numeric codes.
            return int(self.sem.get_float("DP_FIB_MODE"))
        except Exception:
            pass
        try:
            # Some wrappers return the state name (e.g. "Milling").
            s = self.sem.get_str("DP_FIB_MODE").strip()
            if s.isdigit():
                return int(s)
            return FIB_MODE_NAME_TO_CODE.get(s)
        except Exception:
            return None


    def _get_fib_imaging_code(self) -> Optional[int]:
        """Best-effort getter for DP_FIB_IMAGING as an integer code.

        We prefer the *string* state (GetStr) when available because some
        installations can return an incorrect numeric code for DP_ states.

        Returns
        -------
        int | None
            0..9, or None if unavailable.
        """

        # String-first (preferred)
        try:
            s = self.sem.get_str("DP_FIB_IMAGING").strip()
            if s:
                s_norm = (
                    s.upper()
                    .replace(" ", "")
                    .replace("_", "")
                    .replace("-", "")
                )
                # Fast-path: numeric string
                if s_norm.isdigit():
                    return int(s_norm)
                # Known exact labels
                code = FIB_IMAGING_NAME_TO_CODE.get(s_norm)
                if code is not None:
                    return int(code)

                # Heuristics for vendor/locale variations
                if "MILL" in s_norm and "SEM" in s_norm:
                    return 4
                if "MILL" in s_norm:
                    return 2
                if "SEM" in s_norm and "FIB" in s_norm:
                    return 3
                if s_norm.startswith("FIB"):
                    return 1
                if s_norm.startswith("SEM"):
                    return 0
        except Exception:
            pass

        # Numeric fallback
        try:
            return int(self.sem.get_float("DP_FIB_IMAGING"))
        except Exception:
            return None

    def _get_fib_blanked(self) -> Optional[bool]:
        """Best-effort getter for DP_FIB_BLANKED.

        Returns:
            True  -> blanked ("Yes")
            False -> unblanked ("No")
            None  -> unknown / not supported / failed read
        """
        try:
            code = int(self.sem.get_float("DP_FIB_BLANKED"))
            if code in (0, 1):
                return bool(code)
        except Exception:
            pass
        try:
            s = self.sem.get_str("DP_FIB_BLANKED").strip().lower()
            if s in ("yes", "y", "1", "true"):
                return True
            if s in ("no", "n", "0", "false"):
                return False
        except Exception:
            return None
        return None


    def _get_patterning_mode_code(self) -> Optional[int]:
        """Best-effort getter for DP_PATTERNING_MODE as an integer code.

        This state is not always present on all installations, but when available it
        provides another useful signal for whether SmartFIB is actively patterning.

        Observed values (Crossbeam / SmartFIB):
          - 0: Off
          - 3: FIB + SEM

        Returns
        -------
        int | None
            Best-effort numeric code, or None if unavailable.
        """

        # Prefer string state when available (some DP_ numeric reads can be unreliable).
        try:
            s = self.sem.get_str('DP_PATTERNING_MODE').strip()
            if s:
                s_norm = (
                    s.upper()
                    .replace(' ', '')
                    .replace('_', '')
                    .replace('-', '')
                )
                if s_norm.isdigit():
                    return int(s_norm)
                if s_norm in ('OFF',):
                    return 0
                # Heuristics for common labels
                if 'FIB' in s_norm and 'SEM' in s_norm:
                    return 3
                if 'FIB' in s_norm:
                    return 1
                if 'SEM' in s_norm:
                    return 2
        except Exception:
            pass

        # Numeric fallback
        try:
            return int(self.sem.get_float('DP_PATTERNING_MODE'))
        except Exception:
            return None

    def _print_wait_status(self, elapsed_s: float, fib_mode: Optional[int], fib_blanked: Optional[bool]) -> None:
        fb = "?" if fib_blanked is None else ("Yes" if fib_blanked else "No")
        fm = "?" if fib_mode is None else str(fib_mode)
        fi_code = self._get_fib_imaging_code()
        fi = "?" if fi_code is None else str(fi_code)
        pm_code = self._get_patterning_mode_code()
        pm = "?" if pm_code is None else str(pm_code)
        print(
            f"  [wait] {elapsed_s:6.1f}s elapsed | DP_FIB_MODE={fm} | DP_FIB_IMAGING={fi} | DP_PATTERNING_MODE={pm} | DP_FIB_BLANKED={fb}"
        )

    def _time_exceeded(self, t0: float, timeout_s: float) -> bool:
        if timeout_s <= 0:
            return False
        return (time.time() - t0) > float(timeout_s)

    def _confirm_done_stable(
        self,
        *,
        t0: float,
        timeout_s: float,
        confirm_s: float,
        poll_s: float,
        status_every_s: float,
        is_done_fn,
    ) -> bool:
        """Return True if the completion signature remains stable for confirm_s seconds.

        This is used to avoid false-positive completion when DP_FIB_MODE/DP_FIB_BLANKED
        briefly indicate "done" during long milling runs.
        """

        confirm_s = float(confirm_s)
        if confirm_s <= 0:
            return True

        print(f"  [confirm] Potential completion detected; confirming for {confirm_s:.0f}s...")
        t_confirm = time.time()
        last_status = -1e9

        while True:
            # Abort confirmation if completion signature is no longer present.
            if not bool(is_done_fn()):
                print("  [confirm] Completion not stable; continuing to wait.")
                return False

            elapsed_confirm = time.time() - t_confirm
            if elapsed_confirm - last_status >= float(status_every_s):
                elapsed_total = time.time() - t0
                self._print_wait_status(elapsed_total, self._get_fib_mode_code(), self._get_fib_blanked())
                last_status = elapsed_confirm

            if elapsed_confirm >= confirm_s:
                print("  [confirm] Completion confirmed.")
                return True

            if self._time_exceeded(t0, timeout_s):
                raise SmartSEMError(
                    "Timeout waiting for milling to finish (timed out during completion confirmation)."
                )

            time.sleep(float(poll_s))


    def _milling_started_signature(self) -> bool:
        """Return True when SmartSEM provides evidence that milling has started."""
        mode = self._get_fib_mode_code()
        imaging = self._get_fib_imaging_code()
        patterning = self._get_patterning_mode_code()

        if mode in (2, 5):
            return True
        if imaging in MILLING_IMAGING_CODES:
            return True
        # Patterning mode is optional but strong evidence when available.
        if patterning is not None and patterning != 0:
            return True
        return False

    def _milling_done_signature(self) -> bool:
        """Conservative 'milling is finished' signature.

        Based on observed Crossbeam/SmartFIB behavior:
        - During milling: DP_FIB_MODE==2 and DP_FIB_IMAGING==4 (Mill+SEM)
        - Brief probe-change blanks can flip DP_FIB_BLANKED without the job being done
        - When the pattern finishes: DP_FIB_MODE returns to Imaging (0) and DP_FIB_IMAGING
          returns to a non-milling state (often SEM=0). DP_PATTERNING_MODE (if available)
          tends to return to Off (0).

        IMPORTANT: DP_FIB_BLANKED is *not* reliable across installations.
        In your latest log sample, milling was clearly finished (DP_FIB_IMAGING=0 and
        DP_PATTERNING_MODE=0) while DP_FIB_BLANKED remained "No" for minutes.
        Therefore we **do not require** DP_FIB_BLANKED to be "Yes".

        Instead, we treat milling as finished when SmartSEM indicates:
          - DP_PATTERNING_MODE is Off (0), if available
          - DP_FIB_IMAGING is not a milling imaging state (2=Milling, 4=Mill+SEM)
          - DP_FIB_MODE is not in Milling/To Milling if it is readable

        A separate confirmation window (done_confirm_s, default 30s) is used to avoid
        false positives during brief probe-change transitions.
        """
        mode = self._get_fib_mode_code()
        imaging = self._get_fib_imaging_code()
        patterning = self._get_patterning_mode_code()

        # If ANY strong signal says we are still milling -> not done.
        if patterning is not None and patterning != 0:
            return False
        if imaging is not None and imaging in MILLING_IMAGING_CODES:
            return False
        if mode is not None and mode in (2, 5):
            return False

        # If we can't read any of the relevant states, be conservative.
        if mode is None and imaging is None and patterning is None:
            return False

        # If imaging is unreadable (brief probe change), be conservative unless we have
        # explicit non-milling evidence from BOTH mode and patterning.
        if imaging is None:
            if (mode is not None and mode == 0) and (patterning is None or patterning == 0):
                return True
            return False

        # Imaging is readable and is NOT a milling imaging state.
        # If patterning exists, it must be Off (0).
        if patterning is not None and patterning != 0:
            return False

        # Mode can legitimately stay at Imaging (0) when --no-mode-mill is used.
        # We only treat explicit Milling/To Milling as "still active" (handled above).
        return True

    def _wait_milling_done_by_mode(
        self,
        *,
        timeout_s: float,
        poll_s: float = 1.0,
        status_every_s: float = 15.0,
        done_confirm_s: float = 30.0,
    ) -> None:
        """Wait for a full milling cycle using DP_FIB_MODE.

        DP_FIB_MODE is documented as a state machine with transitional states
        (e.g. "To Milling").

        Two-phase wait (SerialFIB-like):
          1) Wait for milling to *start* (enter To Milling or Milling)
          2) Wait for milling to *finish* (leave To Milling/Milling)

        NOTE: Some installations may remain in DP_FIB_MODE==Milling even after
        pattern completion (UI still in milling mode). For those cases, use the
        'auto' or 'blanked' wait strategies.
        """

        start_timeout_s = 60.0 if timeout_s <= 0 else min(60.0, float(timeout_s))
        t0 = time.time()
        last_status = 0.0

        # Phase 1: wait for start (enter 5=To Milling or 2=Milling)
        while True:
            mode = self._get_fib_mode_code()
            imaging = self._get_fib_imaging_code()
            elapsed = time.time() - t0
            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, self._get_fib_blanked())
                last_status = elapsed

            if (mode in (2, 5)) or (imaging in MILLING_IMAGING_CODES):
                break
            if elapsed > start_timeout_s:
                raise SmartSEMError(
                    "Timeout waiting for milling to start (DP_FIB_MODE never entered 'To Milling'/'Milling' and DP_FIB_IMAGING never indicated Milling)."
                )
            time.sleep(poll_s)

        # Phase 2: wait for a conservative "done" signature
        while True:
            mode = self._get_fib_mode_code()
            imaging = self._get_fib_imaging_code()
            blanked = self._get_fib_blanked()
            patterning = self._get_patterning_mode_code()
            elapsed = time.time() - t0
            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, blanked)
                last_status = elapsed

            if self._milling_done_signature():
                if self._confirm_done_stable(
                    t0=t0,
                    timeout_s=timeout_s,
                    confirm_s=done_confirm_s,
                    poll_s=poll_s,
                    status_every_s=status_every_s,
                    is_done_fn=self._milling_done_signature,
                ):
                    return

            if self._time_exceeded(t0, timeout_s):
                raise SmartSEMError(
                    'Timeout waiting for milling to finish (did not observe stable Imaging-mode completion signature).' 
                )

            time.sleep(poll_s)

    def _wait_milling_done_by_blanked(
        self,
        *,
        timeout_s: float,
        poll_s: float = 1.0,
        status_every_s: float = 15.0,
        blanked_stable_s: float = 10.0,
        done_confirm_s: float = 30.0,
    ) -> None:
        """Wait for milling to finish using DP_FIB_BLANKED.

        Strategy:
          - Wait until we observe evidence milling has started (DP_FIB_MODE enters Milling/To Milling
            OR DP_FIB_BLANKED becomes "No").
          - After start, consider milling finished when DP_FIB_BLANKED has been continuously "Yes"
            for >= blanked_stable_s seconds.

        This can help on systems where DP_FIB_MODE stays "Milling" even when patterning is done.
        """

        t0 = time.time()
        last_status = 0.0

        started = False
        saw_unblank = False
        blanked_since: Optional[float] = None

        # Phase 1: wait for a "started" signature
        start_timeout_s = 60.0 if timeout_s <= 0 else min(60.0, float(timeout_s))
        while True:
            mode = self._get_fib_mode_code()
            imaging = self._get_fib_imaging_code()
            blanked = self._get_fib_blanked()
            patterning = self._get_patterning_mode_code()
            elapsed = time.time() - t0

            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, blanked)
                last_status = elapsed

            # Do NOT use "blanked is False" as a start trigger: the beam can be unblanked
            # during normal imaging when no pattern is running.
            if mode in (2, 5) or (imaging in MILLING_IMAGING_CODES) or (patterning is not None and patterning != 0):
                started = True
                break

            if elapsed > start_timeout_s:
                raise SmartSEMError(
                    "Timeout waiting for milling to start (no DP_FIB_MODE/DP_FIB_IMAGING/DP_PATTERNING_MODE milling signature)."
                )
            time.sleep(poll_s)

        # Phase 2: wait for completion.
        # Even though this strategy is named "blanked", we first check the unified
        # completion signature (patterning off + not in milling imaging states) because
        # DP_FIB_BLANKED is not reliable on all installations.
        while started:
            mode = self._get_fib_mode_code()
            imaging = self._get_fib_imaging_code()
            blanked = self._get_fib_blanked()
            patterning = self._get_patterning_mode_code()
            elapsed = time.time() - t0

            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, blanked)
                last_status = elapsed

            if self._milling_done_signature():
                if self._confirm_done_stable(
                    t0=t0,
                    timeout_s=timeout_s,
                    confirm_s=done_confirm_s,
                    poll_s=poll_s,
                    status_every_s=status_every_s,
                    is_done_fn=self._milling_done_signature,
                ):
                    return

            if blanked is False:
                saw_unblank = True
                blanked_since = None

            # We only treat DP_FIB_BLANKED==Yes as a completion hint if we also have
            # independent evidence that we are *not* still in a milling imaging mode.
            not_milling_hint = False
            if imaging is not None and imaging not in MILLING_IMAGING_CODES:
                not_milling_hint = True
            if not not_milling_hint and mode is not None and mode == 0:
                not_milling_hint = True
            if not not_milling_hint and patterning is not None and patterning == 0:
                not_milling_hint = True

            if saw_unblank and blanked is True and not_milling_hint:
                if blanked_since is None:
                    blanked_since = time.time()
                elif (time.time() - blanked_since) >= float(blanked_stable_s):
                    # Confirm stability before declaring completion.
                    def _done() -> bool:
                        b = self._get_fib_blanked()
                        img = self._get_fib_imaging_code()
                        pm = self._get_patterning_mode_code()
                        m = self._get_fib_mode_code()

                        if b is not True:
                            return False

                        # If imaging is readable, it must not be a milling imaging state.
                        if img is not None and img in MILLING_IMAGING_CODES:
                            return False

                        # If patterning mode exists, require Off.
                        if pm is not None and pm != 0:
                            return False

                        # If both mode and imaging are unreadable, be conservative.
                        if m is None and img is None:
                            return False

                        # Otherwise: blanked stable + (not milling imaging) is our best proxy.
                        return True

                    if self._confirm_done_stable(
                        t0=t0,
                        timeout_s=timeout_s,
                        confirm_s=done_confirm_s,
                        poll_s=poll_s,
                        status_every_s=status_every_s,
                        is_done_fn=_done,
                    ):
                        return
            else:
                # Unknown or not blanked -> reset
                blanked_since = None

            if self._time_exceeded(t0, timeout_s):
                raise SmartSEMError(
                    "Timeout waiting for milling to finish (DP_FIB_BLANKED did not remain 'Yes' long enough)."
                )
            time.sleep(poll_s)

    def _wait_milling_done_auto(
        self,
        *,
        timeout_s: float,
        poll_s: float = 1.0,
        status_every_s: float = 15.0,
        blanked_stable_s: float = 10.0,
        done_confirm_s: float = 30.0,
    ) -> None:
        """Wait for milling completion using the most robust signal available.

        Completion condition is the first of:
          - DP_FIB_MODE leaves To Milling / Milling (mode-based)
          - DP_FIB_BLANKED becomes stably Yes (beam blanked) after having been unblanked

        If DP_FIB_BLANKED cannot be read, this behaves like mode_change.
        """

        t0 = time.time()
        last_status = 0.0

        # First: wait for evidence milling has started.
        start_timeout_s = 60.0 if timeout_s <= 0 else min(60.0, float(timeout_s))
        while True:
            mode = self._get_fib_mode_code()
            blanked = self._get_fib_blanked()
            elapsed = time.time() - t0

            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, blanked)
                last_status = elapsed

            if self._milling_started_signature():
                break

            if elapsed > start_timeout_s:
                raise SmartSEMError(
                    "Timeout waiting for milling to start (no DP_FIB_MODE/DP_FIB_IMAGING/DP_PATTERNING_MODE milling signature)."
                )

            time.sleep(poll_s)

        # Second: wait for a conservative completion signature.
        while True:
            mode = self._get_fib_mode_code()
            blanked = self._get_fib_blanked()
            elapsed = time.time() - t0

            if elapsed - last_status >= status_every_s:
                self._print_wait_status(elapsed, mode, blanked)
                last_status = elapsed

            if self._milling_done_signature():
                if self._confirm_done_stable(
                    t0=t0,
                    timeout_s=timeout_s,
                    confirm_s=done_confirm_s,
                    poll_s=poll_s,
                    status_every_s=status_every_s,
                    is_done_fn=self._milling_done_signature,
                ):
                    return

            if self._time_exceeded(t0, timeout_s):
                raise SmartSEMError('Timeout waiting for milling to finish (no stable completion signature detected).')

            time.sleep(poll_s)

    # ----------------------------
    # Public API
    # ----------------------------
    def run(self, job: PatternJob) -> None:
        # Parse & log pattern info (no microscope interaction)
        pattern = ELayoutPattern.from_file(job.ely_local_path)
        info = pattern.summary_dict()

        print("=== Pattern summary ===")
        print(f"Name: {info['name']}")
        print(f"ELayout version: {info['version']}")
        print(f"#Rect elements: {info['rect_count']}")
        print(f"Bounding box (um): {info['bbox_um']}")
        print(f"Exposure files: {info['unique_exposure_files']}")
        print(f"Probe currents (A): {info['unique_probe_currents_A']}")
        print("=======================")

        # From here onward, we are talking to the instrument.
        # Require explicit arming for any commands/macros.
        if not job.arm:
            print("[DRY RUN] Not armed. Would do the following:")

            if job.load_method == "smartfib_drop":
                target = self._smartfib_drop_target(job)
                print(f"  - Copy .ely to SmartFIB drop: {target}")
                print(f"  - Execute: {job.smartfib_load_cmd}")
                print(f"  - Sleep {job.smartfib_load_delay_s:.2f}s")
                print(f"  - Execute: {job.smartfib_prepare_cmd}")
                print(f"  - Sleep {job.smartfib_prepare_delay_s:.2f}s")
            elif job.load_method == "macro":
                print(
                    f"  - Set {_sv_user_dev_name(job.sv_user_dev_slot)} = {job.ely_microscope_path}"
                )
                if job.load_pattern_macro:
                    print(f"  - Execute macro: {job.load_pattern_macro}")
                else:
                    print("  - (No load macro specified; assumes pattern already loaded)")
            elif job.load_method == "none":
                print("  - (Assume pattern already loaded in SmartFIB)")
            else:
                raise ValueError(f"Unknown load_method: {job.load_method}")

            if job.pre_macro:
                print(f"  - Execute pre-macro: {job.pre_macro}")
            if job.ensure_fib_mode_mill:
                print("  - Execute: CMD_FIB_MODE_MILL")
            print("  - Execute: CMD_FIB_START_MILLING")
            if job.set_patterning_mode_fib:
                print(f"  - Try set {job.patterning_mode_state} = {job.patterning_mode_value}")
            if job.wait_strategy != "none":
                print(
                    f"  - Wait strategy: {job.wait_strategy} (timeout={job.wait_timeout_s}s, poll={job.wait_poll_s}s)"
                )
            if job.post_macro:
                print(f"  - Execute post-macro: {job.post_macro}")
            return

        # ARMED execution
        print("[ARMED] Executing pattern at current stage location.")

        # 1) Load pattern
        if job.load_method == "smartfib_drop":
            target = self._smartfib_drop_target(job)
            print(f"Copying .ely to SmartFIB drop folder: {target}")
            self._copy_to_smartfib_drop(job)

            print(f"Loading layout in SmartFIB ({job.smartfib_load_cmd})")
            self.sem.execute(job.smartfib_load_cmd)
            time.sleep(float(job.smartfib_load_delay_s))

            print(f"Preparing exposure ({job.smartfib_prepare_cmd})")
            self.sem.execute(job.smartfib_prepare_cmd)
            time.sleep(float(job.smartfib_prepare_delay_s))

        elif job.load_method == "macro":
            self._set_pattern_path_for_macro(job)
            if job.load_pattern_macro:
                print(f"Executing load macro: {job.load_pattern_macro}")
                self.sem.execute(job.load_pattern_macro)
            else:
                print("No load macro specified; assuming pattern is already loaded.")

        elif job.load_method == "none":
            print("Assuming pattern is already loaded in SmartFIB.")

        else:
            raise ValueError(f"Unknown load_method: {job.load_method}")

        # 2) Optional pre-macro
        if job.pre_macro:
            print(f"Executing pre macro: {job.pre_macro}")
            self.sem.execute(job.pre_macro)

        # 3) Optional: switch to milling mode
        if job.ensure_fib_mode_mill:
            print("Switching to FIB milling mode (CMD_FIB_MODE_MILL)")
            self.sem.execute("CMD_FIB_MODE_MILL")
            # Best-effort wait
            try:
                t_mode = time.time()
                while time.time() - t_mode < 60:
                    if self._get_fib_mode_code() == 2:
                        break
                    time.sleep(0.25)
            except Exception:
                pass

        # 4) Start milling
        print("Starting milling (CMD_FIB_START_MILLING)")
        self.sem.execute("CMD_FIB_START_MILLING")

        # SerialFIB sets DP_PATTERNING_MODE to "FIB" after starting.
        if job.set_patterning_mode_fib:
            try:
                self.sem.set_value(job.patterning_mode_state, job.patterning_mode_value)
            except Exception as e:
                print(
                    f"Warning: failed to set {job.patterning_mode_state}={job.patterning_mode_value}: {e}"
                )

        # 5) Wait / abort handling
        try:
            if job.wait_strategy == "none":
                return

            if job.wait_strategy == "sleep":
                if job.sleep_s <= 0:
                    raise ValueError("sleep_s must be > 0 when wait_strategy == 'sleep'")
                print(f"Sleeping {job.sleep_s:.1f} s (no status feedback)")
                time.sleep(job.sleep_s)
                return

            print("Waiting for milling to finish...")
            if job.wait_strategy == "mode_change":
                self._wait_milling_done_by_mode(
                    timeout_s=job.wait_timeout_s,
                    poll_s=job.wait_poll_s,
                    status_every_s=job.wait_status_every_s,
                    done_confirm_s=job.done_confirm_s,
                )
                print("Milling finished (DP_FIB_MODE changed).")
                return

            if job.wait_strategy == "blanked":
                self._wait_milling_done_by_blanked(
                    timeout_s=job.wait_timeout_s,
                    poll_s=job.wait_poll_s,
                    status_every_s=job.wait_status_every_s,
                    blanked_stable_s=job.blanked_stable_s,
                    done_confirm_s=job.done_confirm_s,
                )
                print("Milling finished (DP_FIB_BLANKED stable).")
                return

            if job.wait_strategy == "auto":
                self._wait_milling_done_auto(
                    timeout_s=job.wait_timeout_s,
                    poll_s=job.wait_poll_s,
                    status_every_s=job.wait_status_every_s,
                    blanked_stable_s=job.blanked_stable_s,
                    done_confirm_s=job.done_confirm_s,
                )
                print("Milling finished.")
                return

            raise ValueError(f"Unknown wait_strategy: {job.wait_strategy}")

        except KeyboardInterrupt:
            print("\nKeyboardInterrupt: aborting milling (CMD_FIB_ABORT_MILLING)...")
            try:
                self.sem.execute("CMD_FIB_ABORT_MILLING")
            except Exception as e:
                print(f"Abort command failed: {e}")
            raise

        finally:
            if job.post_macro:
                print(f"Executing post macro: {job.post_macro}")
                try:
                    self.sem.execute(job.post_macro)
                except Exception as e:
                    print(f"Post macro failed: {e}")
