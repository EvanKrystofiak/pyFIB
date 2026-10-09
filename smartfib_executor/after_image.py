"""After-mill image (v0.41.0): one fresh FIB frame of a site once its milling is done.

Off by default (mill-positions --after-image, or the Mill tab box). Called by the
batch right after milling completes and BEFORE the beam shift is reset, so the frame
is in the same beam-shifted view the pattern was milled in and the report can draw
the pattern on it.

The sequence is the capture tab's, which the instrument already runs after every
"Mill fiducial here" (mill, then capture):
  1. only if SmartSEM no longer indicates milling (otherwise skip -- never interfere);
  2. CMD_FIB_MODE_FIB, wait for DP_FIB_IMAGING = "FIB";
  3. AP_FIB_MAGNIFICATION = the position's capture magnification;
  4. frame_grab.grab_fresh_frame: End Frame freeze -> unfreeze -> scan one frame ->
     freeze -> wait DP_FROZEN -> Grab();  the scan is left FROZEN (no further dose).
Dose: one FIB frame at the current scan rate, the same as a capture.

It NEVER raises: any problem freezes the scan (best effort), returns a warning, and
the batch carries on -- the site was already milled.

v0.51.0: with ``stage`` (rough | prepolish | polish) the file is named
<name>_<n>-<stage>_<time>.bmp (mill_stages.after_image_path); without it, the
pre-v0.51.0 <name>_after_<time>.bmp.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple


def after_image_path(images_dir, name: str, t: Optional[float] = None) -> Path:
    stamp = datetime.fromtimestamp(time.time() if t is None else t).strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))
    return Path(images_dir) / f"{safe}_after_{stamp}.bmp"


def _freeze(sem) -> None:
    try:
        sem.execute("CMD_FREEZE_ZONE")
    except Exception:  # noqa: BLE001
        try:
            sem.execute("CMD_FREEZE_ALL")
        except Exception:  # noqa: BLE001
            pass


def grab_after_mill_image(sem, rec, images_dir, *,
                          milling_active: Callable[[], bool],
                          fib_image_timeout_s: float = 15.0,
                          settle_s: float = 0.25,
                          frame_wait_s: float = 3.0,
                          frozen_timeout_s: float = 30.0,
                          clock: Callable[[], float] = time.time,
                          sleep: Callable[[float], None] = time.sleep,
                          log: Callable[[str], None] = print,
                          stage: Optional[str] = None) -> Tuple[Optional[Path], Optional[str]]:
    """Grab the after-mill frame. Returns (path or None, warning or None). Never raises."""
    name = getattr(rec, "name", "?")
    try:
        if milling_active():
            return None, (f"after-mill image for {name} skipped: SmartSEM still indicates "
                          f"milling.")
    except Exception as e:  # noqa: BLE001
        return None, f"after-mill image for {name} skipped: milling state unreadable ({e})."

    try:
        if stage:
            from .mill_stages import after_image_path as _stage_path
            target = _stage_path(images_dir, name, stage, clock())
        else:
            target = after_image_path(images_dir, name, clock())
    except Exception as e:  # noqa: BLE001
        return None, f"after-mill image for {name} skipped: {e}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)

        def _fib_str() -> Optional[str]:
            try:
                return str(sem.get_str("DP_FIB_IMAGING")).strip()
            except Exception:  # noqa: BLE001
                return None

        cur = _fib_str()
        if cur is None or cur.upper() != "FIB":
            sem.execute("CMD_FIB_MODE_FIB")
        t0 = clock()
        while True:
            s = _fib_str()
            if s is not None and s.upper() == "FIB":
                break
            try:
                if int(float(sem.get_float("DP_FIB_IMAGING"))) == 1:
                    break
            except Exception:  # noqa: BLE001
                pass
            if clock() - t0 > float(fib_image_timeout_s):
                _freeze(sem)
                return None, (f"after-mill image for {name} skipped: FIB imaging did not "
                              f"come on within {fib_image_timeout_s:g} s (last {s!r}).")
            sleep(0.25)

        sem.set_value("AP_FIB_MAGNIFICATION", float(rec.fib_magnification_x))
        if settle_s > 0:
            sleep(float(settle_s))

        from .frame_grab import grab_fresh_frame
        w = int(getattr(rec, "image_width_px", None) or 1024)
        h = int(getattr(rec, "image_height_px", None) or 768)
        warn = grab_fresh_frame(sem, str(target), width=w, height=h,
                                frame_wait_s=frame_wait_s, frozen_timeout_s=frozen_timeout_s,
                                log=log)
        try:
            from .positions import _recover_grab_file
            w2 = _recover_grab_file(sem, target)
            if w2:
                warn = (warn + " " if warn else "") + w2
        except Exception:  # noqa: BLE001
            pass
        if not target.exists():
            return None, f"after-mill image for {name}: Grab() produced no file at {target}."
        if stage and warn and "incomplete or stale" in warn:
            # v0.51.0: a staged after image is the NEXT stage's refine reference; an
            # unconfirmed frame is kept for inspection but renamed so it is never used.
            sus = target.with_name(target.stem + "_suspect.bmp")
            try:
                target.replace(sus)
            except Exception:  # noqa: BLE001
                sus = target
            return None, (f"{warn} Saved as {sus.name}, NOT used as the next stage's "
                          f"reference -- record the after image again.")
        return target, warn
    except Exception as e:  # noqa: BLE001
        _freeze(sem)
        return None, f"after-mill image for {name} FAILED ({type(e).__name__}: {e}); scan frozen."
