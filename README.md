# pyFIB (ZEISS Crossbeam / SmartSEM Remote API)

pyFIB is a minimal Python interface for two practical tasks on a ZEISS Crossbeam
running SmartSEM / SmartFIB (via the SmartSEM Remote API / CZEMApi ActiveX control):

1) **Execute a SmartFIB milling pattern** (`.ely`) at the **current stage location**.
2) **Capture named positions** (`position_1`, `position_2`, …):
   - set **FIB magnification**
   - **grab an image**
   - record **stage position** (X, Y, Z, R, M)
   - record **FIB objective potential**

The position records are stored in a **JSON file** so you can recall them later.

## Scope

Included now:
- Parse `.ely` pattern files and print a summary
- Load an `.ely` into SmartFIB using the **SerialFIB-style** mechanism:
  - copy to SmartFIB API Drop folder as `ApiLayout.ely`
  - execute `CMD_SMARTFIB_LOAD_ELY`
  - execute `CMD_SMARTFIB_PREPARE_EXPOSURE`
- Start milling with `CMD_FIB_START_MILLING`
- Capture named positions into a JSON file
- Recall a position and mill (`goto-and-mill`)
- **Sequential multi-position milling** (`mill-positions`): mill at position_1, then move to position_2, etc.

Not included yet (planned later):
- Closed-loop drift correction

## Installation

Windows + Python + SmartSEM Remote API:

Recommended: Python **3.9+** (your validated environment).

```bat
pip install -r requirements.txt
```

## UI (SerialFIB-style wrapper)

This project includes a small **desktop UI** to run the same commands you
validated on the command line (capture-position, mill-positions) and to
browse/preview BMP images + position metadata.

Launch (UI window title: **pyFIB_UI**):

```bat
python -m pyfib ui --ely examples\Py_Cross.ely --timeout 3600
```

**Session safety (positions file):** when launched *without* `--positions-file`, the UI
starts a **fresh, empty, timestamped session file** under
`positions\session_<YYYYMMDD_HHMMSS>\positions.json` and does **not** load any previous
file. This prevents an old `positions.json` from being adopted silently and driving the
stage to stale coordinates (a collision risk). To reuse earlier positions, use the
**Session > Load Previous…** button in the Positions tab — the session then operates on
that file directly, and you are warned that its coordinates predate the current sample and
stage baseline. **New Session** starts a fresh empty file at any time. Passing an explicit
`--positions-file C:\temp\positions.json` still opens that specific file.

**Tools tab** (Qt UI, v0.44.0; between Mill and Log): **Make report** (the session report
for the positions file in the top bar; reads files only), **SEM autofocus** and **FIB
autofocus** at the current position. Unarmed, the autofocus buttons only check and plan
(FIB: dry run); armed, each asks for confirmation first.

Notes:
- The UI is **dry-run** unless you enable the global **ARM** safety switch.
- BMP preview requires **Pillow** (installed by requirements.txt).
- If your Python build does not include Tkinter, install a Python distribution
  that includes Tkinter (most standard Windows installers do).

Theme:
- The UI uses a **black + gold** theme with subtle colored section boxes (Project / Navigation / Active tab).
- Milling UI includes per-position checkbox selection, reordering, a queue/progress panel, and a live readout.

## Pattern execution

Print pattern info:

```bat
python -m pyfib pattern-info examples\Py_Cross.ely
```

Dry-run (no instrument actions):

```bat
python -m pyfib run examples\Py_Cross.ely
```

Armed run (actually loads pattern + starts milling):

```bat
python -m pyfib run examples\Py_Cross.ely --arm
```

## Position capture (stage + objective potential)

### Capture a position

This does:
- `Set(AP_FIB_MAGNIFICATION, 2.000e3)`
- `Grab(...)` to a BMP
- reads `AP_STAGE_AT_X/Y/Z/R/M` and `AP_FIB_OBJECTIVE_POTENTIAL`
- stores everything in a JSON file (`positions.json` by default)

Dry-run:

```bat
python -m pyfib capture-position position_1
```

Armed:

```bat
python -m pyfib capture-position position_1 --arm --positions-file C:\temp\positions.json
```

By default, images go to `<positions-file folder>\images\position_1.bmp`.

### List stored positions

```bat
python -m pyfib list-positions --positions-file C:\temp\positions.json
```

### Recall a position later

This project provides a *best-effort* recall function.

```bat
python -m pyfib goto-position position_1 --positions-file C:\temp\positions.json --arm --set-objective
```

Stage movement can be requested by:
- `--move-method movestage` (calls `MoveStage(x,y,z,t,r,m)`) -- the DEFAULT since v0.38.0.
  Tilt is the recorded tilt if the position has one, otherwise the current tilt (unchanged);
  if neither is known the recall refuses.
- `--move-method ap_goto` (sets `AP_STAGE_GOTO_*` targets)

On the Crossbeam 550 (2026-10-01) `AP_STAGE_GOTO_*` did not move the stage for a 14 um
recall, while `MoveStage()` did; that is why `movestage` is the default.

### Recall a position and then mill a pattern

This is the workflow you requested: **recall stage position + set FIB objective potential**, then
load/prepare/start a SmartFIB pattern via:

- `CMD_SMARTFIB_LOAD_ELY`
- `CMD_SMARTFIB_PREPARE_EXPOSURE`
- `CMD_FIB_START_MILLING`

Dry-run:

```bat
python -m pyfib goto-and-mill position_1 examples\Py_Cross.ely --positions-file C:\temp\positions.json
```

Armed:

```bat
python -m pyfib goto-and-mill position_1 examples\Py_Cross.ely --positions-file C:\temp\positions.json --arm
```

### Mill all positions sequentially

This runs the same pattern at multiple stored positions in a single run:

Dry-run (prints the planned order):

```bat
python -m pyfib mill-positions examples\Py_Cross.ely --positions-file C:\temp\positions.json
```

Armed (moves stage + sets objective + mills at each position, waiting for each milling run to finish):

```bat
python -m pyfib mill-positions examples\Py_Cross.ely --positions-file C:\temp\positions.json --arm
```

If you only want specific positions (in an explicit order):

```bat
python -m pyfib mill-positions examples\Py_Cross.ely position_3 position_1 --positions-file C:\temp\positions.json --arm
```

By default it stops on the first error. To continue past failures:

```bat
python -m pyfib mill-positions examples\Py_Cross.ely --positions-file C:\temp\positions.json --arm --continue-on-error
```

### Milling stages: Rough Mill -> Prepolish -> Polish (v0.51.0)

Rough mill every lamella, then prepolish them, then polish them -- each as a batch or
site by site. `--stage` picks the stage's pattern rule, refine reference and after-image
name (images\<pos>_1-rough_<t>.bmp, _2-prepolish_, _3-polish_; the latest of each is
recorded in positions.json):

```
python -m pyfib mill-positions rough.ely --positions-file S\positions.json --stage rough --refine-all --arm
python -m pyfib mill-positions prepolish.ely --positions-file S\positions.json --stage prepolish --refine-all --arm
python -m pyfib mill-positions polish.ely --positions-file S\positions.json --stage polish --refine-all --arm
```

Prepolish/Polish mill the given pattern at every position (never the authored rough one)
and refine to the previous stage's latest after image; a position without it is listed
and not milled (OK with `--accept-reference-skips NAMES`, otherwise nothing moves).

One position by hand (dry run without `--arm`):

```
python -m pyfib site goto position_12 --stage prepolish --positions-file S\positions.json --arm
python -m pyfib site align position_12 --stage prepolish --positions-file S\positions.json --arm
python -m pyfib site push position_12 --stage prepolish --ely prepolish.ely --positions-file S\positions.json --arm
python -m pyfib site after-image position_12 --stage prepolish --positions-file S\positions.json --arm
```

`push` loads + prepares the pattern in SmartFIB and never starts milling: check it and
start milling in SmartFIB, then record the after image.

### About `CMD_FIB_MODE_MILL`

Some systems expose `CMD_FIB_MODE_MILL`, but in practice it is **not usually required** to run a
SmartFIB pattern: SerialFIB starts milling without it.

On some microscopes/instrument states, calling `CMD_FIB_MODE_MILL` can **hang** the control/UI.
Therefore this project **does not call it by default**.

If you know you need it, you can enable it explicitly:

```bat
python -m pyfib goto-and-mill position_1 examples\Py_Cross.ely --positions-file C:\temp\positions.json --arm --mode-mill
```

By default, the command waits for the stage to become idle before milling. To skip that (not recommended):

```bat
python -m pyfib goto-and-mill position_1 examples\Py_Cross.ely --positions-file C:\temp\positions.json --arm --no-wait-stage-idle
```

By default, this uses the SerialFIB-style SmartFIB “drop folder” mechanism to load the .ely
into SmartFIB before starting milling.

## Session report

After a session (or at any time), make an HTML summary:

```bat
python -m smartfib_executor.report positions\session_20261001_170335\positions.json --pattern C:\path\to\standard_lamella.ely
```

It writes `report_<time>.html` into the session folder: every position's image with its
pattern drawn on it, coordinates, a stage-layout plot and, for batches milled with v0.40.0
or later, what happened at each site (from `<session>\runs\run_<time>.json`, which every
armed `mill-positions` run now writes). The report reads files only and never connects
to the microscope. `--pattern` is only needed for positions with no milling record and no
authored pattern; without it, positions with no record get pyFIB's default pattern
(`smartfib_executor\assets\patterns\default.ely`) and are shown as "Milled (assumed)".

The report also writes `report_<time>_positions_map.png/.tif/.json/.csv` for pyCLEM: the
positions as dots on a calibrated image (1 um per pixel by default), with stage X
increasing to the left and Y downward, as in the Crossbeam images and the 800x mosaic.
Positions captured at a different stage Z are drawn where they sit on the grid (measured
Z correction, tilt ~2 deg and R 12 deg only; elsewhere the report warns). After the stage layout it draws the
standard pattern with the current, depth, width and height of each part. Add `--mosaic`
for a quarter-size mosaic of the 800x images placed by stage position (off by default).

To get before/after pictures, tick **After-mill image** in any milling tab (Rough Mill, Prepolish, Polish) (or add
`--after-image` to `mill-positions`): one FIB frame per site after milling, saved as
`images\<name>_after_<time>.bmp`. It is off by default (one frame of ion dose per site).

## Safety

This software can start milling and change beam parameters.

- **Default is dry-run**. You must pass `--arm` to execute instrument-affecting actions.
- You are responsible for verifying that your microscope is in a safe state before running.
- **Mill batches: 2 deg tilt limit (v0.46.0).** A batch mills only positions whose
  recorded tilt is within 2 deg of the current stage tilt (and of the previous site).
  Others are listed before any motion and are not milled; the batch starts only after
  you OK that list (milling-tab dialog, terminal prompt, or `--accept-tilt-skips NAMES`).
  `mill-positions ... --tilt-check` shows the list read-only. Other stage moves (setup,
  GISDEP, goto) are not limited. Large tilt changes: by hand or via CLEM SAFE.

## Waiting for milling to finish

By default, the executor waits for milling completion using `DP_FIB_MODE` (the `mode_change` strategy),
similar to how SerialFIB waits for patterns to finish.

- Default timeout is **5400 s (90 minutes)**.
- If your pattern can take longer, set a longer timeout, e.g. `--timeout 7200`,
  or disable the timeout entirely with `--timeout 0` (wait indefinitely; use Ctrl+C to abort).

If your SmartFIB/SmartSEM installation keeps `DP_FIB_MODE` in **"Milling"** even after the
pattern finishes, try:

- `--wait auto` (finishes when either `DP_FIB_MODE` changes OR `DP_FIB_BLANKED` becomes stably "Yes")
- `--wait blanked` (finishes when `DP_FIB_BLANKED` becomes stably "Yes")

## Debugging imaging mode switching (DP_FIB_IMAGING)

If `capture-position` times out waiting for `DP_FIB_IMAGING` to become `FIB`,
use the debug command to log the *numeric* and *string* representations of key
DP states before/after requesting FIB imaging.

Snapshot only (no instrument actions):

```bat
python -m pyfib debug-fib-imaging
```

Run the default request sequence (Set numeric, Set string, Execute command) and log for ~15 s per step:

```bat
python -m pyfib debug-fib-imaging --target fib --arm --per-step-s 15 --poll-s 0.5
```

Try *only* the command (no DP sets):

```bat
python -m pyfib debug-fib-imaging --target fib --steps cmd --arm
```

Write a log file for later review:

```bat
python -m pyfib debug-fib-imaging --target fib --arm --log-file fib_debug.txt
```
