# pyFIB Architecture

pyFIB is a Python control layer for automated cryo-FIB lamella milling (for cryoET)
on a ZEISS Crossbeam 550, driving the microscope through the SmartSEM Remote API
(CZEMApi ActiveX / COM via pywin32). It is Windows-only at runtime because the COM
layer is.

This document describes what each module does and the method by which the code —
in particular the autofocus fork — was developed.

---

## Package layout

```
pyfib/                      thin public wrapper (python -m pyfib)
  __init__.py               version string
  __main__.py               forwards to smartfib_executor

smartfib_executor/          the implementation
  smartsem_com.py           COM/ActiveX wrapper: the ONLY module that talks to the instrument
  positions.py              capture / recall named lamella sites (stage + FIB focus + BMP)
  runner.py                 mill one .ely pattern at one site; detect mill completion
  batch.py                  mill a set of saved positions sequentially
  report/                   session report (HTML) + per-run log (v0.40.0); reads files only
  after_image.py            optional after-mill FIB frame per site (v0.41.0; off by default)
  pattern_ely.py            parse SerialFIB .ely pattern XML (rects + polygons)
  cli.py                    argparse command surface (capture-position, mill-positions, ui, ...)
  gui.py                    Tk desktop UI (shells out to the CLI subcommands)
  __init__.py               version + accumulated changelog
  __main__.py               module entry point

  sem_autofocus/            SEM autofocus fork (v0.37.0) -- steps AP_WD, 4-8 mm window,
                            never SmartSEM's CMD_AUTO_FOCUS_*; decoupled
    guards.py / scope.py / search.py / sim.py / __main__.py / tests/

  autofocus/                FIB autofocus fork -- decoupled; nothing above imports it yet
    metrics.py              pure sharpness metrics + image preprocessing (numpy)
    sweep.py                pure voltage-sweep planning + parabola refinement
    search.py               coarse->refine orchestrator; dose budget; safety restore
    sources.py              ImageSource iface; simulator; real ScopeImageSource adapter
    __init__.py             public API of the fork
    __main__.py             standalone runner (--simulate and real modes)
    tests/                  offline test suites (no hardware, no COM, no display)
      test_autofocus.py       pure core: metrics, sweep, search, simulator convergence
      test_real_data.py       replays measured focus curves from real FIB images
      test_scope_source.py    the instrument path against a mock SmartSEM
      __main__.py             runs all three suites

  refine/                   position-refinement fork (roadmap #4) -- also decoupled
    registration.py         pure FFT phase cross-correlation (numpy)
    geometry.py             pixel<->metre + correction sign conventions
    refine.py               orchestrator with closed-loop verify (undo on non-improvement)
    sources.py              RefineScope iface; simulator; real ScopeRefineAdapter
    __init__.py / __main__.py   public API / standalone runner (--simulate, real modes)
    tests/                  offline suite (registration, geometry, closed-loop safety)

  milling_workflow.py       INTEGRATION: per-position autofocus + refine for milling
  probe/                    read-only API probe -- isolated diagnostic
    catalog.py              candidate mnemonics grouped by capability
    probe.py                read-only probing engine + report (ReadOnlySem guard)
    __init__.py / __main__.py   public API / runner (--simulate, --save)
    tests/                  offline suite (safety: reads only, commands never run)
```

---

## Core workflow modules

**`smartsem_com.py`** — the foundation. A wrapper around the ZEISS `CZEMApi.ocx`
exposing the `SmartSEM` class and the primitives everything else is built on:
`get_float` / `get_str` / `set_value` (read/write parameters such as `AP_MAG` or
`DP_FIB_IMAGING`), `execute` (fire `CMD_*` commands), `grab_bmp`, stage moves, and
the local-vs-remoting connection logic. It is the single chokepoint to the
instrument; by convention nothing else in the project talks COM directly.

**`positions.py`** — capture and recall of named lamella sites. Records stage
X/Y/Z/R/M, the FIB objective potential, and a reference BMP to JSON, and moves the
stage back to a saved site on recall.

**`runner.py`** — drives milling at a single site: loads an `.ely` pattern the
SerialFIB way, starts the mill, and (the genuinely hard part) reliably detects when
milling has actually finished before anything else moves.

**`batch.py`** — sits on top of `runner.py` to mill a whole set of saved positions
in sequence.

**`pattern_ely.py`** — parses the SerialFIB `.ely` pattern XML (rectangles and
polygons, each with its own milling current) into a summary and bounding box.

**`cli.py`** — the argparse command surface (`capture-position`, `mill-positions`,
`ui`, and so on).

**`gui.py`** — the Tk desktop UI. It runs work by shelling out to the CLI
subcommands as subprocesses rather than calling into the library in-process.

**`__init__.py`** — the version string and the changelog accumulated across
releases.

---

## The autofocus fork

Goal: before milling, set the FIB objective voltage to best focus by sweeping it
around the last known-good value, grabbing an image at each step, scoring image
sharpness, and choosing the peak.

It is deliberately **isolated** — nothing in the core workflow imports it — and
**layered** so that all the reasoning is testable without a microscope and only one
file touches hardware.

**`metrics.py`** — pure image math. Five sharpness scores (Tenengrad is the
default), each normalised by pixel count so ROI size does not skew comparisons.
Also the preprocessing that turned out to be essential on real FIB frames:
databar auto-detection and cropping, and a low-pass pre-smooth (a scale-invariant
fraction of image width) that lifts the focus signal out of the shot-noise
pedestal without moving the peak.

**`sweep.py`** — pure arithmetic. Plans the voltage sample points around the centre
and does parabola sub-step refinement, including detecting when the peak sits at a
sweep edge (unbracketed), so the caller can widen rather than trust an
extrapolation.

**`search.py`** — the orchestrator that drives an abstract image source: coarse
sweep, pick the peak, optional fine sweep + parabola, a frame/dose budget, a
low-contrast confidence guard, and the `try/finally` that guarantees the beam shift
and reduced raster are restored on every exit path (success, failure, exception,
Ctrl-C).

**`sources.py`** — the boundary to the outside world, and the only instrument-
touching file. It defines the `ImageSource` interface (`set_voltage`, `grab`,
`initial_voltage`, `bounds`), a `SimulatedImageSource` whose sharpness peaks at a
known voltage (for offline proof), and `ScopeImageSource` — the real adapter. The
adapter sets `AP_FIB_MAGNIFICATION`, parks the beam via `AP_FIB_BEAM_SHIFT_X`,
applies a reduced raster so the beam never scans the area of interest, runs the
frame-safe acquisition sequence, and holds the field-of-view and raster-side
geometry. It also carries the timing constants and the beam-shift sign convention,
both established on the instrument.

**`__main__.py`** — the standalone runner, with a hardware-free `--simulate` mode
and a real mode (plus `--dry-run`) exposing every knob.

**`tests/`** — three suites, run together with
`python -m smartfib_executor.autofocus.tests`:
`test_autofocus.py` (the pure core and simulator convergence),
`test_real_data.py` (replays real measured focus curves), and
`test_scope_source.py` (the instrument path against a mock `SmartSEM`).

---

## The SEM autofocus fork (v0.37.0)

Why: SmartSEM's `CMD_AUTO_FOCUS_COARSE/FINE` sometimes leaves `AP_CONTRAST` at 0 (bug
list #3). `smartfib_executor/sem_autofocus/` focuses the SEM itself: it steps `AP_WD` at
fixed contrast/brightness, grabs frame-safe SEM frames, and reuses the FIB autofocus
fork's pure pieces (`autofocus.metrics` sharpness, `autofocus.sweep` planning and
parabola refinement). Isolated: coincidence and GISDEP still use their own autofocus.

**`guards.py`** — the 4-8 mm WD window and every other limit, `frame_sanity` (blank /
black / saturated), and `GuardedSem`, an allow-list wrapper: set only `AP_WD` (window
re-checked at the set), `AP_MAG`, `DP_FREEZE_ON`; execute only `CMD_FIB_MODE_SEM`,
freeze/unfreeze and `CMD_SCANRATE<n>`. The stage, contrast/brightness and
`CMD_AUTO_FOCUS_*` are unreachable.

**`scope.py`** — `SemFocusScope`, every SmartSEM call: read-only `read_state`; `prepare`
(SEM imaging confirmed by the state NAME, End-Frame freeze, optional mag / scan rate,
baselines); `set_wd` (read-back and stage-Z checks); frame-safe `grab` (wait from
`AP_FRAME_TIME`, `DP_FROZEN`); `check_unchanged`; `restore` (mag, scan rate). Since
v0.37.1 it scans only a centred reduced-raster box (default half width x half height,
FIB autofocus's `AP_RED_RASTER_*` + `CMD_MODE_REDUCED` mechanism) and grabs only that box;
`restore` returns to `CMD_MODE_NORMAL` and the operator's box values.

**`search.py`** — `sem_autofocus()`: read-only preflight (the dry run), a 10 um probe
step, coarse sweep, fine sweep around an interior peak, acceptance guards, verification
frame. Any abort returns `AP_WD` to its start value (try/finally, also on Ctrl-C).

**`sim.py`** — `SimulatedSem`, a fake SmartSEM with fault knobs (contrast -> 0,
read-back offset, stale framestore, never frozen, Z following WD, featureless, ...).

**`__main__.py`** — `python -m smartfib_executor.sem_autofocus` (dry run by default,
`--arm`, `--simulate`). Tests: `python -m smartfib_executor.sem_autofocus.tests`.

---

## GUI Tools tab (v0.44.0)

`gui_qt/tools_page.py`, nav entry "tools" between Mill and Log. Buttons only build argv
for existing commands and launch them with `MainWindow.run_module` (Log tab output):
report (`smartfib_executor.report`, files only, no ARM gate), SEM autofocus
(`smartfib_executor.sem_autofocus`; armed -> confirm dialog, `--arm --yes`), FIB autofocus
(`smartfib_executor.autofocus`; unarmed `--dry-run`, armed -> confirm dialog; settings
mirror `milling_workflow.AutofocusConfig`, bounds from the last capture +/- 400 V).

## Session report and run log (v0.40.0)

`smartfib_executor/report/` -- files in, one HTML file out; it never connects to SmartSEM.

- `runlog.py` -- `RunLog`, written by every ARMED `mill-positions` run to
  `<session>/runs/run_<time>.json` (+ `.log`, a console copy via a stdout/stderr tee).
  Saved (atomic replace) after every step. The batch calls it only through a local
  `_log()` guard, and every public `RunLog` method catches its own errors, so a logging
  failure cannot change the milling sequence (tested with a log that raises on every call).
- `overlay.py` -- `.ely` -> image pixels with elymaker's `reader` and
  `gui.coords.ImageCalibration`; rect corners follow elymaker's `RectItem` (anchor corner,
  local (0,0)..(w,-h) in the y-down scene, Qt rotation about the anchor). Instrument check:
  the numbered-fiducial `.ely` files land on the milled digits in the position_6/7 images
  (tests/data holds the crops and the measured offsets).
- `html_report.py` -- per position: pattern from the latest logged attempt, else the
  authored pattern, else `--pattern`; images embedded as JPEG; inline-SVG overlays with a
  show/hide toggle; stage-layout plot in stage coordinates (equal scale, labelled as not
  the SEM orientation).
- v0.41.0: no run-log record = assumed milled; bundled `assets/patterns/default.ely` is
  the standard pattern where nothing else applies; 800x image as a top-right inset;
  before/after switch when an after-mill image exists.
- `map_export.py` (v0.41.0) -- the positions as a calibrated image for pyCLEM
  (`<report>_positions_map.png/.tif/.json/.csv`): fixed um/px, exact transform in the
  JSON. pyCLEM registers it to the SEM overview with landmarks. Since v0.42.0 stage X
  increases to the LEFT (`STAGE_X_INCREASES_LEFT`, shared with the stage-layout plot), as
  the Crossbeam images show it (Evan; matches refine's STAGE_SIGN_X = -1), and since
  v0.43.0 stage Y increases DOWNWARD (`STAGE_Y_INCREASES_DOWN`), matching the 800x mosaic.
  Since v0.45.0 it includes stage Z: `apply_z_model` draws positions at another Z at the
  equivalent stage X/Y at the median Z (`Z_TO_STAGE_X` +0.047, `Z_TO_STAGE_Y` -1.13 stage
  um per Z um, measured at tilt 1.97 deg / R 12 deg from 800x overlaps and the SEM
  overview; applied only there). The stage layout and mosaic use the same function.
- `mosaic.py` (v0.43.0; opt-in `--mosaic` since v0.43.2) -- the 800x frames placed by stage position in the FIB view
  (stage X left, Y down): refine's stage model, refitted from phase-correlated overlaps
  when >= 4 confident same-Z pairs exist and only if it improves the seams; downsampled
  composite + labelled PNG/JSON.
- `pattern_diagram.py` (v0.42.0) -- the standard pattern drawn to scale after the stage
  layout: current + depth (left), widths (top), heights (right), centre gap; mirror pairs
  labelled once; parts table. Since v0.42.1 its colours (one per probe current,
  `current_classes`) are shared with the position-image overlays, so it is their legend.
- Next (not built): position names on the low-mag SEM overview inside the report
  (SmartSEM TIFF metadata + pyCLEM landmark fit, points only, per-landmark residuals).

## After-mill image (v0.41.0)

`after_image.grab_after_mill_image`, called by the batch after `runner.run` and before
the beam-shift reset when `after_image=True` (`mill-positions --after-image`, Mill tab
box; default off). Skips unless `_is_milling_active` is False; then the capture
sequence (CMD_FIB_MODE_FIB, capture mag, `frame_grab.grab_fresh_frame`, left frozen).
Never raises; failures freeze the scan and return a warning; the site stays "milled".

## Refine in mill batches: 800x fallback (v0.39.0)

`milling_workflow.run_refine_at_position` runs the 2000x refine first. If it cannot
LOCATE the site (low confidence / aliasing guard / correction not converging) and the
position has an 800x image (`capture-position --coarse-mag 800`, ticked by default in the
Capture tab), it puts the beam shift back to its pre-refine value and runs
`refine.refine_two_stage`: coarse pass at 800x (stage only if enabled, and only if the
2000x pass did not already keep a stage move -- one stage move per site), then 2000x. No
fallback when 2000x located the site but was not allowed / able to correct it.
`mill-positions --no-refine-coarse-fallback` turns it off.

---

## The position-refinement fork

Goal (roadmap #4): after a stage recall, correct the small residual positioning
error so milling lands on the intended target. It cross-correlates the reference
BMP that `positions.py` stored (`image_bmp`) against a fresh FIB image grabbed at
the stored magnification (`fib_magnification_x`), then pulls the field back —
FIB beam shift for drift within 10 um, or a stage move (X/Y only) above that. Like
autofocus it is isolated (nothing else imports it) and layered so the reasoning is
provable offline.

**`registration.py`** — pure FFT phase cross-correlation returning a sub-pixel
(dy, dx) translation, with the databar cropped and a Hann window applied first so
static overlay and edge-wrap do not bias the result. The sign convention is pinned
by tests, not assumed.

As of v0.9.0 both forks prefer the instrument's live `AP_FIB_PIXEL_SIZE` over the constant, and guard against non-zero scan rotation (warn, or refuse if strict). Positions and autofocus also record `AP_FIB_WORKING_DISTANCE` for reference, while focus stays driven by the FIB objective voltage.

**`geometry.py`** — the magnification→pixel-size calibration and the pixel→metre
and correction-sign conventions, all in one tested place. The beam-shift X sign is
the one measured during the autofocus work; the stage signs and rotation are
uncalibrated placeholders.

**`refine.py`** — the orchestrator: measure the drift, route to beam shift (≤10 um)
or stage (>10 um, disabled by default), apply the correction, and — the safety
core — re-image and re-correlate to confirm the residual shrank, undoing the last
step if it did not.

**`sources.py`** — the `RefineScope` interface (grab at a magnification, get/set
beam shift, get/move stage X/Y), a `SimulatedRefineScope` that models a displaced
feature so the closed loop can be proven offline (including that a wrong sign is
rejected), and `ScopeRefineAdapter`, the instrument-untested real adapter. The
stage path exists but is only reached when explicitly enabled.

**`calibrate.py`** — measures the beam-shift convention rather than assuming it.
It applies a known shift on each axis, cross-correlates before against after, and
reports which way the image actually moved (the sign), how far per metre (the
scale, which independently checks the magnification→pixel-size calibration), and
the safe raster side implied for the autofocus fork. The result can be saved to
`refine_calibration.json` and is then used automatically. This exists because
beam-shift signs were wrong three separate times, each discovered on the
instrument — the durable fix is to measure them.

## Imaging dwell / scan rate (v0.10.4)

Refine and autofocus raise `DP_SCANRATE` (the scan-speed enum, confirmed by the
probe) for their diagnostic frames to lift the correlation/focus SNR, then restore
the original value; milling dwell is untouched. The change is applied by read-back-
verified set (default `method="set"`, with `cmd`/`set_then_cmd` fallbacks) so the
log states plainly whether a direct set took. Because a higher scan rate makes each
frame slower, the refine grab was upgraded to the same end-of-frame freeze +
`DP_FROZEN` poll autofocus already used, so a slow frame is captured complete.

## Integration: autofocus + refine in the mill loop (v0.10.0)

`milling_workflow.py` wires the two forks into `batch.mill_positions_sequential`.
For each recalled position the workflow runs, in order, optional autofocus then
optional refine then milling, chosen per position (checkboxes in the UI mill queue,
`--autofocus`/`--refine` on the CLI). Autofocus sweeps the saved objective voltage
above and below and leaves best focus; refine cross-correlates against the reference
BMP and nulls the offset with beam shift, which stays applied so milling lands on
target. Positions are processed strictly serially. The heavy logic stays in the
already-tested forks; this layer is thin and its ordering/seriality are covered by
an offline test suite that injects fakes for the fork calls.

## The read-only API probe

`smartfib_executor/probe/` answers a question that keeps recurring: *which
mnemonics actually exist on this instrument?* Names reported as working elsewhere
are often absent from the 2007 reference tables, and at least one that is absent
from those tables (`DP_PATTERNING_MODE`) demonstrably works here — so neither the
manual nor another project's notes can be trusted as the final word.

**`catalog.py`** lists candidates grouped by *capability* rather than by name, each
with several alternative mnemonics in preference order, a priority reflecting what
pyFIB would do with the value, and a flag for whether it appears in the manual.

**`probe.py`** does the probing and the reporting. It is read-only *by
construction*: `ReadOnlySem` exposes only `get_float`/`get_str` and raises on any
attempt to reach `set_value`, `execute`, `move_stage` or `grab_bmp`, and tests
assert that a full run issues nothing else. Commands can only be tested by running
them, so `CMD_*` entries are reported and never executed. Failures are classified
into "name not recognised" versus "name resolved but unreadable right now", which
is the distinction that matters when a mnemonic works on one machine and not
another.

## How the autofocus fork was developed

The core modules were inherited; the autofocus fork is the part with a development
story, and one method held throughout.

**Read before writing.** The starting point was reading the existing code and the
SmartSEM API reference, so the fork reused the project's proven COM patterns
instead of inventing calls.

**Design around the binding constraint.** Instrument time is scarce and there is no
simulator, so the architecture pushed as much correctness as possible into code
that could be proven *offline*: pure metrics, pure sweep math, and a search driven
through an abstract `ImageSource`, leaving a single thin adapter as the only part
that needs real hardware. A simulator whose sharpness peaks at a known voltage let
the whole search be validated before it touched the column.

**Let the instrument teach, and encode every lesson as a test.** Reality arrived in
stages, and each one exposed something the offline model had wrong:

- Real FIB images showed the metrics being swamped by shot noise, which produced
  the preprocessing layer and the finding that a slower dwell beats higher
  resolution for focusing.
- The first live run exposed a stale-frame bug: grabbing before the framestore had
  refreshed, so every sweep point scored the same image. That produced the
  unfreeze -> scan -> freeze -> grab sequence and its timing.
- The beam-park run showed that beam shift alone cannot clear the field of view at
  2000x (the field is wider than the shift range), which is why a reduced raster is
  required to protect the sample.
- The reduced-raster run showed the beam-shift sign convention had been assumed
  backwards, which dosed the sample.

Each finding became a regression test that would have caught it — and the mock had
to keep getting more honest to make that possible. That was the recurring
failure-and-fix: the mock first synthesised a fresh image on every grab, so it
passed the stale-frame bug, and was made to model the framestore and cycle time; it
did not track dose, so it passed the burn, and was made to record which screen
columns the beam actually scanned. Each fix was proven by re-introducing the old
bug and watching the new test fail.

**A check must not depend on the assumption it is meant to test.** The sign-
convention episode sharpened this. The original "safety" warning was computed from
the same wrong convention it was supposed to guard against, so it reported the
target as safe while the beam sat on it. The durable fix was an end-to-end test
asserting the beam never doses the target's screen column — a property that does
not depend on the reasoning under test.

**Release discipline.** Every change ships as a full package with the version
bumped, each release is verified end-to-end before hand-off, and any
instrument-untested code is labelled as such so the remaining risk is always
explicit. The `ScopeImageSource` adapter is the current example: its logic and call
sequence are validated against the mock, but the real COM behaviour (settle times,
whether reduced-raster mode engages) is confirmed on the microscope.

## Batch tilt limit (v0.46.0)

`tilt_guard.py` (pure, plus one AP_STAGE_AT_T read): `plan_batch_tilts(names, recorded,
live, 2.0)` walks the batch in milling order; a position is allowed if its recorded tilt
is within 2 deg of the live tilt at the start and of the previous allowed site (no
recorded tilt = holds the current tilt = allowed). Used in three places, all before
motion: `mill-positions --tilt-check` (read-only, prints `TILT-CHECK: live=.. limit=..
allowed=.. excluded=..` for the GUI), the armed CLI pre-check (prompt at a TTY, else
`--accept-tilt-skips` or refuse with exit 3), and `batch.mill_positions_sequential`
itself (`tilt_skip_ok`; raises TiltLimitError if an out-of-range position was not OKed;
re-reads the live tilt before every recall). Mill batches only: `recall_position`,
setup, GISDEP and goto are unchanged. Skips go to the run log (`tilt_check`, `skipped`)
and the report shows them as Not milled.

## SEM focus-curve measurement (v0.47.0)

`sem_autofocus/curve.py` reuses `SemFocusScope` (GuardedSem, verified WD sets, mag /
scan-speed / box set and restore, fresh-frame grabs) and `search.preflight`. One scope
per pass (mag, speed, box); each pass: settle frame, ascending WD sweep, return frame at
the start WD; then WD back and `scope.restore()`. Writes frames, `curve.csv` (row per
frame, flushed), contact sheets, `curves.png`, `summary.json`. Measures only; the data
will set the coarse mag/step and the fine scan speed of the two-stage SEM autofocus.

## Two-stage SEM autofocus (v0.48.0)

`sem_autofocus/two_stage.py` is the CLI default: read-only `search.preflight`, then a
coarse `SemFocusScope` (500x, speed 4, box 0.35; WD +/-600 um in 200 um steps, extended
3 steps at an edge until bracketed, window limit tried last) -> `scope.restore()` ->
a fine scope (working mag, speed 4, box 0.35; +/-90 um in 30 um steps around the coarse
parabola, 2 halfway points, verify). Peaks via `autofocus.sweep.refine_peak` in both
stages. The active scope is registered before `prepare()`, so a half-done prepare is
restored; a WD set is counted before it is sent, so a set that raised is still undone.
`search.sem_autofocus` (single stage) is unchanged and reachable with --single-stage.
v0.49.0: one scope for both stages (`SemFocusScope.retarget` switches 500x -> fine mag
and keeps the operator's originals for `restore()`); `restore()` ends with `_pause_scan()`
(freeze sent after raster / speed / mag are back); fine-stage contrast floor 0.2.

## Milling stages in the Qt GUI (v0.50.0)

`gui_qt/app.py` NAV: `mill` ("Rough Mill"), `prepolish`, `polish`, then `tools`, `log`.
All three pages are `pages.MillPage(window, title, stage)` with identical options for now
(each builds its own `mill-positions` argv, with the v0.46.0 tilt check when armed).
`MainWindow.mill_pages()` returns them in order.


## Milling stages: Rough Mill -> Prepolish -> Polish (v0.51.0)

`mill_stages.py` (no microscope I/O) holds the stage rules; `batch.py`, `site_ops.py`,
`cli.py`, the GUI and the report all go through it.

| stage | pattern | refine reference | after image |
|---|---|---|---|
| rough | standard, authored overrides | capture image | `<pos>_1-rough_<t>.bmp` |
| prepolish | prepolish .ely at every position | latest rough after image | `<pos>_2-prepolish_<t>.bmp` |
| polish | polish .ely at every position | latest prepolish after image | `<pos>_3-polish_<t>.bmp` |

* `PositionRecord.stage_after_images` = latest per stage (relative, `/`-separated);
  `record_after_image()` re-reads positions.json and changes only that field.
  `latest_after_image()` falls back to the newest matching file (rough also accepts the
  pre-v0.51.0 `<pos>_after_<t>.bmp`). Re-capture keeps the map (`carry_over`).
* Batch (`mill_positions_sequential(stage=..., reference_skip_ok=...)`): the reference
  plan is made before the tilt plan and before any motion; a missing reference not in
  `reference_skip_ok` raises `StageReferenceError` (nothing moved). Refine gets
  `reference_path`; the after image is named by stage and recorded.
* Manual (`site_ops.py`, CLI `site`, `--stage` required): every action first samples
  `_is_milling_active` for a window (10 s before possible stage motion, else 5 s) and
  refuses on any milling reading;
  goto zeroes the beam shift before MoveStage; push = `PatternJob(start_milling=False)`
  (load + prepare, never START); after-image writes a one-site run log (kind "manual")
  that carries the last align/push of that position + stage from
  `runs/manual_state.json`.
* Report: the main status/pattern/after image come from rough runs only (stage None or
  "rough"); `_stage_view()` adds Prepolish/Polish; the figure has one image + pattern
  overlay per view (`data-view` on `.fig-wrap`).
* Prepolish/Polish: `abort_on_failure` forced on for refine (a failed refine never
  mills); `load_method` must be `smartfib_drop`. `carry_over` keeps the map only for a
  re-capture within 50 um; disk fallback ignores files older than the capture; stale
  staged frames are renamed `*_suspect.bmp`. `PositionDatabase.save` is atomic.

## Compact report (v0.51.1)

`html_report._stage_entries(v)` gives Rough (from the main view) + Prepolish/Polish
(`v.stages`) as one list; `_stage_line` renders each as badge + pattern + residual (`_residual`)
with a short note only when not milled, details in the `title`; `_stage_cell` is the same in
the summary table. `_patterns_section` collects every pattern used (per stage, standard /
authored) and renders `pattern_diagram.pattern_card` (drawing + `parts_table_compact`).
Figure keys are per view (`pv v-<view>` spans, shown by the same `data-view` CSS as the
images). `#plist` + `body.two-up` = two positions per row (default; toolbar toggle).
