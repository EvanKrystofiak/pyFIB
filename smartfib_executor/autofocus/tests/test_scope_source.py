"""Offline validation of the REAL instrument path (ScopeImageSource).

FRAMESTORE SEMANTICS (learned the hard way on the instrument): SmartSEM's Grab()
reads the framestore and NEVER errors -- if the beam has not painted a complete
frame since the last unfreeze, it silently returns the PREVIOUS frame. The v0.5.2
mock synthesised a fresh image on every grab_bmp call, so it happily passed code
that grabbed instantly after unfreezing; on the real column that produced one
image repeated across the whole sweep and a flat focus curve. MockSmartSEM now
models the framestore and the ~1.3 s cycle time, so that bug fails here instead.

ScopeImageSource is the only part of the autofocus fork that talks to the
microscope, so it cannot be exercised on a dev machine. These tests substitute a
MockSmartSEM that mimics ``smartsem_com.SmartSEM`` -- same method names and
signatures -- and records every call. That catches the class of bug that would
otherwise only surface during a scarce instrument session: wrong mnemonics, a
missing freeze (ion dose!), bounds not clamped, or the column left at the wrong
voltage.

It does NOT prove the real COM layer behaves identically -- only that our call
sequence and logic are right.

Run:
    python -m smartfib_executor.autofocus.tests.test_scope_source
"""

from __future__ import annotations

import numpy as np

from .. import sources as _sources
from ..search import autofocus
from ..sources import ScopeImageSource, DEFAULT_BEAM_SHIFT_X_M

try:
    from PIL import Image, ImageFilter
    _HAVE_PIL = True
except Exception:
    _HAVE_PIL = False

TRUE_FOCUS = 16425.0


class FakeClock:
    """Virtual clock so tests exercise multi-second waits instantly."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += float(s)


class MockSmartSEMError(RuntimeError):
    """Mimics smartsem_com.SmartSEMError, carrying an API return code."""
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class MockSmartSEM:
    """Mimics smartsem_com.SmartSEM, including framestore/cycle-time semantics.

    Key realism: ``grab_bmp`` returns whatever is in the framestore. The
    framestore only refreshes once the beam has scanned for a full ``cycle_s``
    while live -- exactly like the real column. Grab never errors on stale data.
    """

    def __init__(self, true_focus: float = TRUE_FOCUS, start_v: float = 16500.0,
                 clock: "FakeClock" = None, cycle_s: float = 1.3):
        self.calls = []
        self.true_focus = true_focus
        self.v = start_v
        self.imaging = "SEM"          # column starts in SEM imaging
        self.scan_rate = 4            # DP_SCANRATE enum index
        self.mag = 1.0e3              # some other magnification to begin with
        self.beam_shift_x = 0.0       # beam starts on the area of interest
        self.beam_shift_history = []
        self.fail_on_shift_restore = False
        self.clock = clock or FakeClock()
        self.cycle_s = float(cycle_s)  # ~1.3 s measured on the real instrument
        self.frozen = True             # column starts frozen
        self.freeze_on = "End Frame"
        self._live_since = None        # when scanning last started
        self._pending_freeze_at = None
        self._framestore = None        # last COMPLETE frame (None = never scanned)
        self._framestore_v = None      # voltage that frame was taken at
        self.frames_painted = 0
        self.image_mode = "Full"          # DP_IMAGE_MODE
        self.red_raster = {}              # AP_RED_RASTER_*
        self.dosed_columns = set()        # screen x columns the beam has scanned
        self.rng = np.random.default_rng(0)
        t = self.rng.normal(0, 1, (768, 1024))
        if _HAVE_PIL:
            t = np.asarray(
                Image.fromarray((t * 40 + 128).astype(np.uint8))
                .filter(ImageFilter.GaussianBlur(4)),
                dtype=float,
            )
        self.tex = (t - t.mean()) / max(t.std(), 1e-9) * 9.2 + 80.0

    # -- internal framestore model ---------------------------------------
    def _render(self) -> np.ndarray:
        """Synthesise the frame the beam would paint at the CURRENT voltage."""
        blur = 0.3 + 0.02 * abs(self.v - self.true_focus)
        img = self.tex
        if _HAVE_PIL and blur > 0:
            img = np.asarray(
                Image.fromarray(img.astype(np.uint8))
                .filter(ImageFilter.GaussianBlur(blur)),
                dtype=float,
            )
        return np.clip(img + self.rng.normal(0, 10.4, img.shape), 0, 255)

    def _advance(self) -> None:
        """Refresh the framestore if a full frame has been painted while live."""
        if self.frozen or self._live_since is None:
            return
        if self.clock.time() - self._live_since >= self.cycle_s:
            # Record the dosed area: reduced raster confines the beam; full raster
            # scans everything. This is what makes "does cropping save dose?" testable.
            if self.image_mode == "Reduced" and self.red_raster:
                x0 = int(self.red_raster.get("AP_RED_RASTER_POSN_X", 0))
                w = int(self.red_raster.get("AP_RED_RASTER_W", 1024))
                self.dosed_columns.update(range(x0, x0 + w))
            else:
                self.dosed_columns.update(range(0, 1024))
            self._framestore = self._render()
            self._framestore_v = self.v
            self.frames_painted += 1
            self._live_since = self.clock.time()  # next frame starts now

    # -- SmartSEM API surface --------------------------------------------
    def execute(self, command: str) -> None:
        self.calls.append(("execute", command))
        if command == "CMD_FIB_MODE_FIB":
            self.imaging = "FIB"
        elif command == "CMD_MODE_REDUCED":
            self.image_mode = "Reduced"
        elif command == "CMD_MODE_NORMAL":
            self.image_mode = "Full"
        elif command == "CMD_UNFREEZE_ZONE":
            self._advance()
            self.frozen = False
            self._live_since = self.clock.time()
            self._pending_freeze_at = None
        elif command in ("CMD_FREEZE_ZONE", "CMD_FREEZE_ALL"):
            self._advance()
            if self.freeze_on == "End Frame" and not self.frozen:
                # Freeze lands at the end of the frame in progress.
                self._pending_freeze_at = (self._live_since or self.clock.time()) + self.cycle_s
            else:
                self.frozen = True  # immediate (may capture a partial frame)

        elif command.startswith("CMD_SCANRATE"):
            try:
                self.scan_rate = int(command.replace("CMD_SCANRATE", ""))
            except ValueError:
                pass

    def _settle_freeze(self) -> None:
        if self._pending_freeze_at is not None and self.clock.time() >= self._pending_freeze_at:
            self._advance()
            self.frozen = True
            self._pending_freeze_at = None

    def get_str(self, mnemonic: str) -> str:
        self.calls.append(("get_str", mnemonic))
        self._advance()
        self._settle_freeze()
        if mnemonic == "DP_FIB_IMAGING":
            return self.imaging
        if mnemonic == "DP_FROZEN":
            return "Frozen" if self.frozen else "Live"
        if mnemonic == "DP_IMAGE_MODE":
            return self.image_mode
        raise KeyError(mnemonic)

    def get_float(self, mnemonic: str) -> float:
        self.calls.append(("get_float", mnemonic))
        self._advance()
        self._settle_freeze()
        if mnemonic == "AP_FIB_OBJECTIVE_POTENTIAL":
            return self.v
        if mnemonic == "AP_FIB_MAGNIFICATION":
            return self.mag
        if mnemonic == "AP_FIB_BEAM_SHIFT_X":
            return self.beam_shift_x
        if mnemonic in ("AP_FIB_PIXEL_SIZE", "AP_PIXEL_SIZE"):
            return getattr(self, "pixel_size", 5.582e-8)
        if mnemonic in ("AP_FIB_SCAN_ROTATION", "AP_SCANROTATION"):
            return getattr(self, "scan_rotation", 0.0)
        if mnemonic == "AP_FIB_WORKING_DISTANCE":
            wd = getattr(self, "working_distance", None)
            if wd is None:
                raise KeyError(mnemonic)
            return wd
        if mnemonic == "DP_FIB_IMAGING":
            return 1.0 if self.imaging == "FIB" else 0.0
        if mnemonic == "DP_FROZEN":
            return 1.0 if self.frozen else 0.0
        if mnemonic == "DP_SCANRATE":
            return float(getattr(self, "scan_rate", 4))
        raise KeyError(mnemonic)

    def set_value(self, mnemonic: str, value) -> None:
        self.calls.append(("set_value", mnemonic, value))
        if mnemonic == "AP_FIB_OBJECTIVE_POTENTIAL":
            self._advance()
            self.v = float(value)
        elif mnemonic.startswith("AP_RED_RASTER_"):
            self.red_raster[mnemonic] = float(value)
        elif mnemonic == "DP_FREEZE_ON":
            self.freeze_on = "End Frame" if str(value) in ("End Frame", "0", "0.0") else "Command"
        elif mnemonic == "AP_FIB_MAGNIFICATION":
            self.mag = float(value)
        elif mnemonic == "DP_SCANRATE":
            # DP_SCANRATE is READ-ONLY on the real column: set_value fails with
            # rc=1019. Only CMD_SCANRATE# changes it. Model that faithfully.
            raise MockSmartSEMError("Set failed with rc=1019", code=1019)
        elif mnemonic == "AP_FIB_BEAM_SHIFT_X":
            if self.fail_on_shift_restore and float(value) == 0.0:
                raise RuntimeError("simulated COM failure restoring beam shift")
            self.beam_shift_x = float(value)
            self.beam_shift_history.append(float(value))

    def grab_bmp(self, filename: str, *, xoff=0, yoff=0, width=1024, height=768,
                 reduction=0) -> None:
        self.calls.append(("grab_bmp", xoff, yoff, width, height, reduction))
        self._advance()
        self._settle_freeze()
        # THE REAL BEHAVIOUR: Grab() reads the framestore and never errors. If no
        # complete frame has been painted, it returns the stale previous frame.
        if self._framestore is None:
            # Nothing ever scanned: the column hands back a blank/garbage buffer.
            img = np.zeros((768, 1024), dtype=float)
        else:
            img = self._framestore
        img = img[yoff:yoff + height, xoff:xoff + width]
        Image.fromarray(img.astype(np.uint8)).save(filename)


def _make(sem=None, **kw):
    """Build a ScopeImageSource whose sleeps advance the mock's virtual clock."""
    clock = FakeClock()
    sem = sem or MockSmartSEM(clock=clock)
    sem.clock = clock
    _sources.time = clock          # patch module-level time -> virtual
    kw.setdefault("bounds", (16000.0, 17000.0))
    kw.setdefault("roi", (0, 0, 512, 384))
    return sem, ScopeImageSource(sem, **kw), clock


def _restore_time():
    import time as _real_time
    _sources.time = _real_time


def _mnemonics(sem):
    return [c[1] for c in sem.calls if c[0] in ("execute", "set_value", "get_str",
                                                "get_float")]


def test_scope_source_reads_start_voltage_from_scope():
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16480.0))
        assert src.initial_voltage() == 16480.0
        assert ("get_float", "AP_FIB_OBJECTIVE_POTENTIAL") in sem.calls
    finally:
        _restore_time()


def test_scope_source_uses_correct_mnemonics():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make()
        src.set_voltage(16400.0)
        src.grab()
        used = set(_mnemonics(sem))
        assert "AP_FIB_OBJECTIVE_POTENTIAL" in used   # the focus parameter
        assert "CMD_FIB_MODE_FIB" in used             # ensure FIB imaging
        assert "DP_FIB_IMAGING" in used               # confirm it took
    finally:
        _restore_time()


def test_scope_source_clamps_to_safety_bounds():
    """Out-of-range requests must be clamped, never sent raw to the column."""
    try:
        sem, src, _ = _make(bounds=(16300.0, 16500.0))
        src.set_voltage(99999.0)
        src.set_voltage(-5000.0)
        sets = [c[2] for c in sem.calls
                if c[0] == "set_value" and c[1] == "AP_FIB_OBJECTIVE_POTENTIAL"]
        assert sets == [16500.0, 16300.0], sets
    finally:
        _restore_time()


def test_column_is_frozen_when_grab_happens():
    """Grab must read a FROZEN framestore, and scanning must stop afterwards.

    Freezing before the grab both stops dose and prevents catching a
    half-scanned frame.
    """
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make()
        src.set_voltage(16400.0)
        src.grab()
        seq = [c for c in sem.calls if c[0] in ("execute", "grab_bmp")]
        kinds = [c[1] if c[0] == "execute" else "GRAB" for c in seq]
        i = kinds.index("GRAB")
        # A freeze must PRECEDE the grab...
        assert any("FREEZE" in k and "UNFREEZE" not in k for k in kinds[:i]), kinds
        # ...and the column must end up frozen (no continued dose).
        assert sem.frozen, "column left scanning after grab"
    finally:
        _restore_time()


def test_dry_run_touches_no_hardware():
    try:
        sem, src, _ = _make(dry_run=True)
        sem.calls.clear()
        src.set_voltage(16400.0)
        src.grab()
        assert not any(c[0] in ("set_value", "execute", "grab_bmp")
                       for c in sem.calls), sem.calls
    finally:
        _restore_time()


def test_roi_is_passed_through_to_grab():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(roi=(64, 32, 512, 384))
        src.set_voltage(16400.0)
        img = src.grab()
        grab = [c for c in sem.calls if c[0] == "grab_bmp"][0]
        assert grab[1:] == (64, 32, 512, 384, 0), grab
        assert img.shape == (384, 512), img.shape
    finally:
        _restore_time()


def test_full_autofocus_over_mock_scope_converges_and_leaves_best_focus():
    """End-to-end: the real ScopeImageSource path, driven by the real search."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        res = autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad")
        assert res.success, res.message
        assert abs(res.best_voltage - TRUE_FOCUS) < 20.0, res.best_voltage
        # The column must be LEFT at the chosen focus.
        assert abs(sem.v - res.best_voltage) < 1e-6, (sem.v, res.best_voltage)
        # Dose sanity: one grab per measured frame, no runaway scanning.
        grabs = sum(1 for c in sem.calls if c[0] == "grab_bmp")
        assert grabs == res.n_frames, (grabs, res.n_frames)
    finally:
        _restore_time()


# ---------- regression: the real "only one image acquired" failure ---------- #
def test_each_sweep_point_yields_a_DISTINCT_fresh_frame():
    """THE bug seen on the instrument (v0.5.2 and earlier).

    Grabbing immediately after CMD_UNFREEZE_ZONE returned the previous frame, so
    every voltage produced an identical image and the focus curve was flat.
    Grab() never errors on stale data, so nothing surfaced until the real run.
    """
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        frames = []
        for v in (16300.0, 16400.0, 16500.0):
            src.set_voltage(v)
            frames.append(src.grab())
        # Distinct voltages must give measurably different frames.
        for i in range(len(frames) - 1):
            assert not np.allclose(frames[i], frames[i + 1]), (
                f"frames {i} and {i+1} are identical -- stale framestore "
                "(this is the 'only one image acquired' bug)"
            )
        # The mock must actually have painted a fresh frame per grab.
        assert sem.frames_painted >= 3, sem.frames_painted
    finally:
        _restore_time()


def test_frame_wait_covers_the_real_cycle_time():
    """Default frame wait must exceed the measured ~1.3 s cycle, with margin.

    3.0 s was confirmed on the instrument (down from the initial 5.0 s). The hard
    invariant is the margin over the frame cycle time -- drop below that and Grab()
    silently returns the previous frame again.
    """
    from ..sources import DEFAULT_FRAME_WAIT_S, DEFAULT_SETTLE_S

    CYCLE_S = 1.3  # measured at 2000x on the Crossbeam 550
    assert DEFAULT_FRAME_WAIT_S >= 3.0, DEFAULT_FRAME_WAIT_S    # instrument-confirmed
    assert DEFAULT_FRAME_WAIT_S > CYCLE_S * 2, "needs real margin over the cycle time"
    assert DEFAULT_SETTLE_S >= 1.0, DEFAULT_SETTLE_S            # lens settle


def test_mock_reproduces_the_stale_framestore(tmpfile=None):
    """Guard the guard: the MOCK must still model stale frames.

    This drives the mock the way the buggy v0.5.2 code did -- unfreeze then grab
    with no scanning time -- and asserts it hands back the SAME frame. If this
    ever fails, the mock has stopped modelling the framestore and the regression
    test above would silently stop protecting us.
    """
    if not _HAVE_PIL:
        return
    import tempfile, os

    clock = FakeClock()
    sem = MockSmartSEM(clock=clock)
    sem.imaging = "FIB"

    def buggy_grab_at(v):
        sem.set_value("AP_FIB_OBJECTIVE_POTENTIAL", v)
        sem.execute("CMD_UNFREEZE_ZONE")
        # NO WAIT -- exactly the v0.5.2 bug.
        fd, path = tempfile.mkstemp(suffix=".bmp")
        os.close(fd)
        try:
            sem.grab_bmp(path, xoff=0, yoff=0, width=512, height=384)
            with Image.open(path) as im:
                return np.asarray(im.convert("L"), dtype=float)
        finally:
            sem.execute("CMD_FREEZE_ZONE")
            os.remove(path)

    a = buggy_grab_at(16300.0)
    b = buggy_grab_at(16500.0)
    assert np.allclose(a, b), (
        "mock no longer models the stale framestore; the 'distinct frames' "
        "regression test above is no longer meaningful"
    )
    assert sem.frames_painted == 0, (
        f"no frame should complete without scan time, got {sem.frames_painted}"
    )


def test_frozen_poll_rescues_a_short_frame_wait():
    """Defence in depth: DP_FREEZE_ON=End Frame + DP_FROZEN poll still yields a
    complete frame even if frame_wait_s is set too low."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0), frame_wait_s=0.1)
        src.set_voltage(16300.0)
        a = src.grab()
        src.set_voltage(16500.0)
        b = src.grab()
        assert not np.allclose(a, b), "frozen-poll failed to guarantee a fresh frame"
    finally:
        _restore_time()


def test_freeze_on_end_frame_is_requested():
    """Mid-scan freezes yield half-old/half-new frames; we must ask for End Frame."""
    try:
        sem, src, _ = _make()
        assert any(c[0] == "set_value" and c[1] == "DP_FREEZE_ON" for c in sem.calls), \
            "DP_FREEZE_ON was never set to End Frame"
        assert sem.freeze_on == "End Frame", sem.freeze_on
    finally:
        _restore_time()


# ---------- prepare/restore: magnification + beam shift ---------- #
def test_prepare_sets_magnification_and_parks_beam_shift():
    try:
        sem, src, _ = _make()
        src.prepare()
        assert sem.mag == 2.00e03, sem.mag
        assert sem.beam_shift_x == DEFAULT_BEAM_SHIFT_X_M, sem.beam_shift_x
    finally:
        _restore_time()


def test_restore_returns_beam_shift_to_zero():
    try:
        sem, src, _ = _make()
        src.prepare()
        assert sem.beam_shift_x == DEFAULT_BEAM_SHIFT_X_M
        src.restore()
        assert sem.beam_shift_x == 0.0, sem.beam_shift_x
    finally:
        _restore_time()


def test_beam_shift_is_parked_BEFORE_any_imaging():
    """The shift must land before the first grab, or we sputter the real target."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make()
        src.prepare()
        src.set_voltage(16400.0)
        src.grab()
        idx_shift = next(i for i, c in enumerate(sem.calls)
                         if c[0] == "set_value" and c[1] == "AP_FIB_BEAM_SHIFT_X")
        idx_grab = next(i for i, c in enumerate(sem.calls) if c[0] == "grab_bmp")
        assert idx_shift < idx_grab, "beam shift applied after imaging had started"
    finally:
        _restore_time()


def test_full_autofocus_parks_and_returns_beam_shift():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        res = autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad")
        assert res.success, res.message
        assert sem.beam_shift_x == 0.0, "beam shift not returned home"
        assert DEFAULT_BEAM_SHIFT_X_M in sem.beam_shift_history, \
            "beam was never parked off target"
        assert sem.mag == 2.00e03, sem.mag
    finally:
        _restore_time()


def test_beam_shift_returned_even_when_sweep_RAISES():
    """SAFETY-CRITICAL: an exception mid-sweep must not leave the beam shifted."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))

        boom = RuntimeError("grab exploded")

        def exploding_grab():
            raise boom

        src.grab = exploding_grab
        try:
            autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad")
        except RuntimeError as e:
            assert e is boom
        else:
            raise AssertionError("expected the exception to propagate")
        assert sem.beam_shift_x == 0.0, (
            "beam left shifted after an exception -- subsequent milling would be "
            "displaced by the shift"
        )
    finally:
        _restore_time()


def test_beam_shift_returned_on_keyboard_interrupt():
    """Ctrl-C during a sweep must still put the beam back."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))

        def interrupting_grab():
            raise KeyboardInterrupt()

        src.grab = interrupting_grab
        try:
            autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad")
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("expected KeyboardInterrupt to propagate")
        assert sem.beam_shift_x == 0.0, "beam left shifted after Ctrl-C"
    finally:
        _restore_time()


def test_beam_shift_returned_when_focus_fails_low_confidence():
    """A failed focus run must still return the beam."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        # Flat signal -> low confidence -> autofocus returns success=False.
        src.grab = lambda: np.full((384, 512), 100.0)
        res = autofocus(src, half_range=150.0, coarse_points=7, metric="tenengrad")
        assert not res.success
        assert sem.beam_shift_x == 0.0, "beam left shifted after a failed focus"
    finally:
        _restore_time()


def test_restore_never_raises_and_warns_loudly_on_failure():
    """If the restore COM call fails we must not raise -- but must warn hard."""
    try:
        msgs = []
        sem, src, _ = _make(logger=msgs.append)
        src.prepare()
        sem.fail_on_shift_restore = True
        src.restore()  # must not raise
        assert any("WARNING" in m and "BEAM_SHIFT" in m for m in msgs), msgs
        assert any("BEFORE MILLING" in m for m in msgs), msgs
    finally:
        _restore_time()


def test_restore_is_idempotent_and_safe_without_prepare():
    try:
        sem, src, _ = _make()
        src.restore()   # never prepared -> no-op, no crash
        src.prepare()
        src.restore()
        src.restore()   # double restore -> harmless
        assert sem.beam_shift_x == 0.0
    finally:
        _restore_time()


def test_context_manager_restores_on_exception():
    try:
        sem, src, _ = _make()
        try:
            with src:
                assert sem.beam_shift_x == DEFAULT_BEAM_SHIFT_X_M
                raise ValueError("boom")
        except ValueError:
            pass
        assert sem.beam_shift_x == 0.0
    finally:
        _restore_time()


def test_dry_run_prepare_restore_touches_nothing():
    try:
        sem, src, _ = _make(dry_run=True)
        sem.calls.clear()
        src.prepare()
        src.restore()
        assert not any(c[0] == "set_value" for c in sem.calls), sem.calls
    finally:
        _restore_time()


def test_beam_shift_can_be_disabled():
    try:
        sem, src, _ = _make(beam_shift_x_m=None, fib_magnification=None)
        src.prepare()
        assert not any(c[0] == "set_value" and c[1] in
                       ("AP_FIB_BEAM_SHIFT_X", "AP_FIB_MAGNIFICATION")
                       for c in sem.calls), sem.calls
    finally:
        _restore_time()


# ---------------- field-of-view geometry ---------------- #
def test_fov_matches_the_measured_databar():
    """FOV model must reproduce the operator's own 2000x calibration (~57 um)."""
    from ..sources import fov_width_m

    fov = fov_width_m(2000.0)
    # databar: Image Pixel Size = 55.82 nm over 1024 px -> 57.16 um
    assert abs(fov - 1024 * 55.82e-9) < 1.5e-6, fov


def test_warns_when_park_shift_does_not_clear_the_field():
    """10 um at 2000x leaves the target inside the 57 um scanned field."""
    msgs = []
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=1.0e-5,
                            logger=msgs.append)
        src.prepare()
        joined = " ".join(msgs)
        assert "does NOT clear the field of view" in joined, joined
        assert "still being scanned" in joined, joined
    finally:
        _restore_time()


def test_no_warning_when_shift_clears_the_field():
    msgs = []
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=4.0e-5,
                            logger=msgs.append)
        src.prepare()
        joined = " ".join(msgs)
        assert "does NOT clear" not in joined, joined
        assert "outside the scanned area" in joined, joined
    finally:
        _restore_time()


# ---------------- reduced raster / dose ---------------- #
def test_grab_roi_alone_does_NOT_reduce_dose():
    """The core correction: cropping Grab() saves no dose.

    Grab() reads the framestore; the beam still rasters the whole field. Only a
    reduced raster shrinks the burned area.
    """
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(roi=(0, 0, 256, 192), beam_shift_x_m=None,
                            fib_magnification=None)
        src.prepare()
        src.set_voltage(16400.0)
        src.grab()
        assert len(sem.dosed_columns) == 1024, (
            "cropping the grab must NOT be mistaken for dose reduction; the beam "
            f"scanned {len(sem.dosed_columns)} columns"
        )
    finally:
        _restore_time()


def test_reduced_raster_actually_shrinks_the_dosed_area():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(reduced_raster=(512, 0, 512, 768), roi=(512, 0, 512, 768))
        src.prepare()
        src.set_voltage(16400.0)
        src.grab()
        assert sem.image_mode == "Reduced", sem.image_mode
        assert min(sem.dosed_columns) >= 512, min(sem.dosed_columns)
        assert len(sem.dosed_columns) == 512, len(sem.dosed_columns)
    finally:
        _restore_time()


def test_reduced_raster_sets_all_four_mnemonics():
    try:
        sem, src, _ = _make(reduced_raster=(512, 0, 512, 768))
        src.prepare()
        for m in ("AP_RED_RASTER_POSN_X", "AP_RED_RASTER_POSN_Y",
                  "AP_RED_RASTER_W", "AP_RED_RASTER_H"):
            assert m in sem.red_raster, f"{m} never set"
        assert sem.red_raster["AP_RED_RASTER_POSN_X"] == 512.0
        assert sem.red_raster["AP_RED_RASTER_W"] == 512.0
    finally:
        _restore_time()


def test_full_raster_is_restored():
    """A reduced raster left applied would silently constrain later imaging."""
    try:
        sem, src, _ = _make(reduced_raster=(512, 0, 512, 768))
        src.prepare()
        assert sem.image_mode == "Reduced"
        src.restore()
        assert sem.image_mode == "Full", sem.image_mode
    finally:
        _restore_time()


def test_full_raster_restored_even_when_sweep_raises():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(reduced_raster=(512, 0, 512, 768), roi=(512, 0, 512, 768))

        def boom(*a, **k):
            raise RuntimeError("sweep exploded")

        src.grab = boom
        try:
            autofocus(src, half_range=150.0, coarse_points=5, metric="tenengrad")
        except RuntimeError:
            pass
        assert sem.image_mode == "Full", "reduced raster left applied after a crash"
        assert sem.beam_shift_x == 0.0, "beam shift left applied after a crash"
    finally:
        _restore_time()


def test_auto_side_picks_the_side_away_from_the_target():
    """MEASURED convention (v0.6.1): +shift -> target appears RIGHT -> scan LEFT.

    pyFIB <= 0.6.0 had this backwards and burned the sample. Regression-locked.
    """
    from ..sources import safe_raster_side, target_screen_x

    assert safe_raster_side(+1.2e-5, 2000.0) == "left"
    assert safe_raster_side(-1.2e-5, 2000.0) == "right"
    # +shift must put the target to the RIGHT of centre (x > 512 on a 1024 screen).
    assert target_screen_x(+1.2e-5, 2000.0) > 512.0
    assert target_screen_x(-1.2e-5, 2000.0) < 512.0


def test_warns_when_raster_covers_the_target():
    """The wrong side must be impossible to miss."""
    msgs = []
    try:
        # +12 um shift puts the target at x~727 -> scanning the RIGHT half hits it.
        sem, src, _ = _make(beam_shift_x_m=1.2e-5, fib_magnification=2000.0,
                            reduced_raster=(512, 0, 512, 768), logger=msgs.append)
        src.prepare()
        joined = " ".join(msgs)
        assert "INSIDE the scanned raster" in joined, joined
    finally:
        _restore_time()


def test_no_warning_when_raster_avoids_the_target():
    msgs = []
    try:
        sem, src, _ = _make(beam_shift_x_m=1.2e-5, fib_magnification=2000.0,
                            reduced_raster=(0, 0, 512, 768), logger=msgs.append)
        src.prepare()
        joined = " ".join(msgs)
        assert "INSIDE the scanned raster" not in joined, joined
        assert "outside the scanned raster" in joined, joined
    finally:
        _restore_time()


def test_default_beam_shift_is_12um_and_under_the_instrument_limit():
    from ..sources import DEFAULT_BEAM_SHIFT_X_M, BEAM_SHIFT_X_MAX_M

    assert DEFAULT_BEAM_SHIFT_X_M == 1.20e-05
    assert DEFAULT_BEAM_SHIFT_X_M < BEAM_SHIFT_X_MAX_M, "must not sit on the rail"


def test_raster_dimensions_are_reduced_10pct_W_and_40pct_H():
    """Operator-specified: shrink the rastered rectangle to cut dose."""
    from ..sources import raster_for_side, DEFAULT_RASTER_W_SCALE, DEFAULT_RASTER_H_SCALE

    assert DEFAULT_RASTER_W_SCALE == 0.90   # W -10%
    assert DEFAULT_RASTER_H_SCALE == 0.60   # H -40%
    x, y, w, h = raster_for_side("left", (1024, 768), 0.5)
    assert w == round(1024 * 0.5 * 0.90), w      # 461
    assert h == round(768 * 0.60), h             # 461
    assert x == 0, "left band must stay anchored to the outer edge"
    assert y == (768 - h) // 2, "height should be centred vertically"


def test_width_shrinks_AWAY_from_the_target():
    """Narrowing W must widen the clearance, never narrow it."""
    from ..sources import raster_for_side, target_screen_x, fov_width_m

    tx = target_screen_x(1.2e-5, 2000.0)                  # ~727 px, to the RIGHT
    px_um = fov_width_m(2000.0) / 1024 * 1e6
    wide = raster_for_side("left", (1024, 768), 0.5, w_scale=1.0)
    narrow = raster_for_side("left", (1024, 768), 0.5)     # default 0.9
    m_wide = (tx - (wide[0] + wide[2])) * px_um
    m_narrow = (tx - (narrow[0] + narrow[2])) * px_um
    assert m_narrow > m_wide, (m_narrow, m_wide)


def test_auto_raster_never_doses_the_target_column_end_to_end():
    """The property that actually matters: the beam must never touch the target.

    Drives a full autofocus with the real defaults and checks the mock's record of
    which screen columns were dosed. This is the test that would have caught the
    v0.6.0 burn.
    """
    if not _HAVE_PIL:
        return
    try:
        from ..sources import target_screen_x, DEFAULT_BEAM_SHIFT_X_M

        sem, src, _ = _make(MockSmartSEM(start_v=16500.0),
                            fib_magnification=2000.0,
                            beam_shift_x_m=DEFAULT_BEAM_SHIFT_X_M,
                            raster_side="auto", roi=None)
        res = autofocus(src, half_range=150.0, coarse_points=5, metric="tenengrad")
        tx = int(round(target_screen_x(DEFAULT_BEAM_SHIFT_X_M, 2000.0)))
        assert sem.dosed_columns, "nothing was scanned at all"
        assert tx not in sem.dosed_columns, (
            f"THE BEAM DOSED THE AREA OF INTEREST at column {tx}; "
            f"dosed range = [{min(sem.dosed_columns)}..{max(sem.dosed_columns)}]"
        )
        # And the safe side really is the left, per the measured convention.
        assert max(sem.dosed_columns) < tx, (max(sem.dosed_columns), tx)
        assert res.n_frames > 0
    finally:
        _restore_time()


# ---------------- v0.9.0: live pixel size, scan-rotation guard, WD ---------------- #
def test_raster_geometry_is_screen_consistent_not_store_tied():
    """REGRESSION (v0.9.0): raster side must be computed on the SCREEN grid.

    v0.9.0 threaded the store-tied live pixel size into the raster-side calc, which
    would mis-scale the geometry when store resolution != screen_size. The safe side
    must depend only on the beam-shift sign and the screen grid, so a wildly wrong
    live pixel size must NOT change it.
    """
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=1.2e-5,
                            raster_side="auto")
        sem.pixel_size = 5.582e-8 / 3.0   # store-tied, 3x too small
        src.prepare()
        rect = src._resolve_raster()
        # +12 um park -> target on the RIGHT -> safe side LEFT -> raster anchored at x=0
        assert rect is not None and rect[0] == 0, rect
    finally:
        _restore_time()


def test_pixel_size_absent_does_not_break_prepare():
    class NoPx(MockSmartSEM):
        def get_float(self, m):
            if m in ("AP_FIB_PIXEL_SIZE", "AP_PIXEL_SIZE"):
                raise KeyError(m)
            return super().get_float(m)
    try:
        sem, src, _ = _make(NoPx(), fib_magnification=2000.0, beam_shift_x_m=1.2e-5,
                            raster_side="auto")
        src.prepare()
        assert src._pixel_size_m is None  # graceful; geometry still uses screen grid
        assert src._resolve_raster()[0] == 0
    finally:
        _restore_time()


def test_scan_rotation_warns_but_proceeds_by_default():
    msgs = []
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=1.2e-5,
                            raster_side="auto", logger=msgs.append)
        sem.scan_rotation = 5.0
        src.prepare()
        assert any("scan rotation" in m.lower() for m in msgs), msgs
    finally:
        _restore_time()


def test_scan_rotation_strict_refuses_to_prepare():
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=1.2e-5,
                            raster_side="auto", strict_scan_rotation=True)
        sem.scan_rotation = 5.0
        raised = False
        try:
            src.prepare()
        except RuntimeError as e:
            raised = "refused" in str(e).lower()
        assert raised, "strict scan-rotation guard did not refuse"
    finally:
        _restore_time()


def test_zero_scan_rotation_is_silent():
    msgs = []
    try:
        sem, src, _ = _make(fib_magnification=2000.0, beam_shift_x_m=1.2e-5,
                            raster_side="auto", logger=msgs.append)
        sem.scan_rotation = 0.0
        src.prepare()
        assert not any("scan rotation" in m.lower() for m in msgs)
    finally:
        _restore_time()


def test_working_distance_recorded_in_result():
    """WD is stamped on the result; focus still driven by the voltage sweep."""
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        sem.working_distance = 4.98e-3
        res = autofocus(src, half_range=150.0, coarse_points=5, metric="tenengrad")
        assert res.working_distance_m == 4.98e-3
        # and the actual focus mechanism was still the objective voltage
        assert any(c[0] == "set_value" and c[1] == "AP_FIB_OBJECTIVE_POTENTIAL"
                   for c in sem.calls)
    finally:
        _restore_time()


def test_working_distance_absent_is_none_not_error():
    if not _HAVE_PIL:
        return
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0))
        # working_distance attribute defaults to None -> get_float raises -> None
        res = autofocus(src, half_range=150.0, coarse_points=5, metric="tenengrad")
        assert res.working_distance_m is None
        assert res.success
    finally:
        _restore_time()


# ----------------- imaging scan rate (dwell/SNR) set + restore ----------------- #
def test_scan_rate_raised_in_prepare_restored_in_restore():
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0),
                            imaging_scan_rate=8, raster_side="auto",
                            beam_shift_x_m=1.2e-5, fib_magnification=2000.0)
        assert sem.scan_rate == 4  # starting value in the mock
        src.prepare()
        assert sem.scan_rate == 8, "prepare() should raise DP_SCANRATE to 8"
        src.restore()
        assert sem.scan_rate == 4, "restore() should put DP_SCANRATE back to 4"
    finally:
        _restore_time()


def test_scan_rate_none_leaves_it_untouched():
    try:
        sem, src, _ = _make(MockSmartSEM(start_v=16500.0),
                            imaging_scan_rate=None, raster_side="auto",
                            beam_shift_x_m=1.2e-5, fib_magnification=2000.0)
        src.prepare()
        assert sem.scan_rate == 4
        assert not any(c[0] == "set_value" and c[1] == "DP_SCANRATE" for c in sem.calls)
    finally:
        _restore_time()


def test_scan_rate_direct_set_ignored_warns_then_cmd_fallback():
    """Model a column where set_value(DP_SCANRATE) is a no-op; 'set_then_cmd' recovers."""
    try:
        sem = MockSmartSEM(start_v=16500.0)
        sem.ignore_direct_scanrate = True
        msgs = []
        sem2, src, _ = _make(sem, imaging_scan_rate=8, scan_rate_method="set_then_cmd",
                             raster_side="auto", beam_shift_x_m=1.2e-5,
                             fib_magnification=2000.0, logger=msgs.append)
        src.prepare()
        # set_value ignored -> falls back to CMD_SCANRATE8 -> rate becomes 8
        assert sem.scan_rate == 8, sem.scan_rate
        assert any("CMD_SCANRATE8" in str(c) for c in sem.calls)
    finally:
        _restore_time()


def test_scan_rate_set_only_ignored_warns_and_does_not_cmd():
    """With method='set' (the operator's first test), an ignored set must WARN."""
    try:
        sem = MockSmartSEM(start_v=16500.0)
        sem.ignore_direct_scanrate = True
        msgs = []
        _, src, _ = _make(sem, imaging_scan_rate=8, scan_rate_method="set",
                          raster_side="auto", beam_shift_x_m=1.2e-5,
                          fib_magnification=2000.0, logger=msgs.append)
        src.prepare()
        assert sem.scan_rate == 4  # unchanged
        assert any("WARNING" in m and "DP_SCANRATE" in m for m in msgs), msgs
        assert not any("CMD_SCANRATE" in str(c) for c in sem.calls)
    finally:
        _restore_time()


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed"
          + ("" if _HAVE_PIL else "  (some skipped: Pillow unavailable)"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
