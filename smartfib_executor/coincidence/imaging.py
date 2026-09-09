"""Capture an SEM overview image and then a FIB overview image for coincidence.

After the working distance is set to the 5 mm coincidence point, this grabs one SEM
image and then one FIB image so the operator can eyeball that the two beams are
coincident. It performs NO stage motion -- it only switches the imaging beam,
freezes a frame, and grabs a BMP (all behind the FocusSem guard).

Timing goes through the injected Clock so tests run instantly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from .clock import Clock, RealClock

# Conservative frame settle so the framestore holds a complete fresh frame before we
# freeze + grab. This is an overview image, not a correlation frame.
DEFAULT_MODE_SETTLE_S = 1.5
DEFAULT_FRAME_WAIT_S = 3.0
DEFAULT_FROZEN_TIMEOUT_S = 20.0
DEFAULT_POLL_S = 0.25
DEFAULT_GRAB_SIZE = (1024, 768)


@dataclass
class ImagePair:
    sem_path: Optional[str] = None
    fib_path: Optional[str] = None


def _is_frozen(sem) -> Optional[bool]:
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


def _wait_frozen(sem, clock: Clock, timeout_s: float, poll_s: float) -> None:
    t0 = clock.now()
    while clock.now() - t0 < timeout_s:
        st = _is_frozen(sem)
        if st is None or st:
            return
        clock.sleep(poll_s)


def _grab_one(sem, out_path: Path, *, mode_cmd: str, clock: Clock,
              mode_settle_s: float, frame_wait_s: float, frozen_timeout_s: float,
              poll_s: float, grab_size: Tuple[int, int],
              logger: Callable[[str], None]) -> str:
    logger(f"[coincidence] imaging: {mode_cmd} then grab -> {out_path.name}")
    sem.execute(mode_cmd)                       # switch imaging beam (SEM or FIB)
    clock.sleep(mode_settle_s)                  # let the mode switch settle
    try:
        sem.execute("CMD_UNFREEZE_ZONE")
    except Exception:
        pass
    clock.sleep(frame_wait_s)                   # scan at least one fresh frame
    try:
        sem.execute("CMD_FREEZE_ZONE")
    except Exception:
        try:
            sem.execute("CMD_FREEZE_ALL")
        except Exception:
            pass
    _wait_frozen(sem, clock, frozen_timeout_s, poll_s)
    w, h = grab_size
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sem.grab_bmp(str(out_path), xoff=0, yoff=0, width=w, height=h, reduction=0)
    return str(out_path)


def capture_sem_then_fib(sem, out_dir, *, clock: Optional[Clock] = None,
                         mode_settle_s: float = DEFAULT_MODE_SETTLE_S,
                         frame_wait_s: float = DEFAULT_FRAME_WAIT_S,
                         frozen_timeout_s: float = DEFAULT_FROZEN_TIMEOUT_S,
                         poll_s: float = DEFAULT_POLL_S,
                         grab_size: Tuple[int, int] = DEFAULT_GRAB_SIZE,
                         prefix: str = "coincidence",
                         logger: Optional[Callable[[str], None]] = None) -> ImagePair:
    """Grab an SEM image, then (after it completes) a FIB image. Returns their paths.

    No stage motion. SEM first, FIB second, as requested.
    """
    clock = clock or RealClock()
    log = logger or (lambda m: None)
    out_dir = Path(out_dir)

    sem_path = _grab_one(
        sem, out_dir / f"{prefix}_sem.bmp", mode_cmd="CMD_FIB_MODE_SEM", clock=clock,
        mode_settle_s=mode_settle_s, frame_wait_s=frame_wait_s,
        frozen_timeout_s=frozen_timeout_s, poll_s=poll_s, grab_size=grab_size,
        logger=log)
    fib_path = _grab_one(
        sem, out_dir / f"{prefix}_fib.bmp", mode_cmd="CMD_FIB_MODE_FIB", clock=clock,
        mode_settle_s=mode_settle_s, frame_wait_s=frame_wait_s,
        frozen_timeout_s=frozen_timeout_s, poll_s=poll_s, grab_size=grab_size,
        logger=log)

    # Emit machine-parseable markers so a GUI can find and display the images.
    log(f"SEM_IMAGE: {sem_path}")
    log(f"FIB_IMAGE: {fib_path}")
    return ImagePair(sem_path=sem_path, fib_path=fib_path)
