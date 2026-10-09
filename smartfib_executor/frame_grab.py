"""Frame-safe FIB grab for CORE code (capture-position).

WHY: SmartSEM's Grab() saves whatever is in the frame store at that instant. Right
after CMD_UNFREEZE_ZONE the store still holds the LAST FROZEN frame, and a new frame
takes seconds to scan -- so "unfreeze, then Grab() immediately" saves the previous
image (or a band of new lines over it). pyFIB freezes the scan itself (every capture
ends frozen; refine/autofocus leave it frozen), so capture #2 routinely saved capture
#1's image. The reference image is what refine correlates against, so a stale
reference points refine at the wrong site.

THE SEQUENCE (copied from the instrument-proven refine/autofocus adapters -- the
forks stay isolated, so this is the core copy; they can adopt it later):
  1. DP_FREEZE_ON = End Frame   (a freeze request completes the frame in progress)
  2. CMD_UNFREEZE_ZONE          (scan live)
  3. wait frame_wait_s          (minimum; at least one full frame at this scan rate)
  4. CMD_FREEZE_ZONE            (scan stops at the END of the current frame)
  5. poll DP_FROZEN until Frozen (the frame is complete)
  6. Grab()                     (from the frozen, fresh frame store)
The scan is left FROZEN (no further dose).
"""

from __future__ import annotations

import time
from typing import Callable, Optional

FRAME_WAIT_S = 3.0        # refine/autofocus default; the DP_FROZEN poll guarantees completion
FROZEN_TIMEOUT_S = 30.0   # refine/autofocus default


def is_frozen(sem) -> Optional[bool]:
    """True/False from DP_FROZEN (name first, number fallback); None if unreadable."""
    try:
        v = sem.get_str("DP_FROZEN").strip().lower()
        if v in ("frozen", "1", "yes"):
            return True
        if v in ("live", "0", "no"):
            return False
    except Exception:
        pass
    try:
        return int(round(float(sem.get_float("DP_FROZEN")))) == 1
    except Exception:
        return None


def wait_frozen(sem, timeout_s: float, poll_s: float = 0.2) -> Optional[bool]:
    """Block until DP_FROZEN reports Frozen. Returns True (confirmed), False (timed out)
    or None (DP_FROZEN unreadable -- rely on the frame_wait padding)."""
    deadline = time.time() + float(timeout_s)
    while True:
        st = is_frozen(sem)
        if st is None:
            return None
        if st:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_s)


def grab_fresh_frame(sem, path: str, *, xoff: int = 0, yoff: int = 0,
                     width: int = 1024, height: int = 768, reduction: int = 0,
                     frame_wait_s: float = FRAME_WAIT_S,
                     frozen_timeout_s: float = FROZEN_TIMEOUT_S,
                     log: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Scan a FRESH frame, freeze at its end, and Grab() it to `path`.

    Returns None on a confirmed-complete frame, or a WARNING string if completion
    could not be confirmed (the caller records it). Raises if Grab() fails; the scan
    is frozen best-effort before the exception propagates.
    """
    log = log or (lambda m: None)
    warning: Optional[str] = None
    try:
        sem.set_value("DP_FREEZE_ON", "End Frame")
    except Exception:
        try:
            sem.set_value("DP_FREEZE_ON", 0.0)
        except Exception:
            pass
    try:
        sem.execute("CMD_UNFREEZE_ZONE")
    except Exception:
        pass
    time.sleep(max(0.0, float(frame_wait_s)))
    try:
        sem.execute("CMD_FREEZE_ZONE")
    except Exception:
        try:
            sem.execute("CMD_FREEZE_ALL")
        except Exception:
            pass
    st = wait_frozen(sem, frozen_timeout_s)
    if st is False:
        warning = (f"WARNING: DP_FROZEN did not report Frozen within "
                   f"{float(frozen_timeout_s):g} s; the reference frame may be "
                   f"incomplete or stale -- check the image.")
        log(warning)
    elif st is None:
        log("[capture] DP_FROZEN unreadable; relying on the frame-wait padding.")
    sem.grab_bmp(str(path), xoff=int(xoff), yoff=int(yoff), width=int(width),
                 height=int(height), reduction=int(reduction))
    return warning


# ----------------------------- scan rate (core) ----------------------------- #
# DP_SCANRATE is read-only on the instrument; CMD_SCANRATE<n> sets it (confirmed).
def read_scan_rate(sem) -> Optional[int]:
    try:
        return int(round(float(sem.get_float("DP_SCANRATE"))))
    except Exception:
        return None


def set_scan_rate(sem, rate: int) -> bool:
    try:
        sem.execute(f"CMD_SCANRATE{int(rate)}")
        return True
    except Exception:
        return False
