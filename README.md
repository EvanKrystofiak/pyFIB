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
- `--move-method ap_goto` (sets `AP_STAGE_GOTO_*` targets)
- `--move-method movestage` (calls `MoveStage(x,y,z,t,r,m)`)

Whether `AP_STAGE_GOTO_*` immediately triggers motion depends on your microscope configuration.
`MoveStage()` is the most explicit method.

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

## Safety

This software can start milling and change beam parameters.

- **Default is dry-run**. You must pass `--arm` to execute instrument-affecting actions.
- You are responsible for verifying that your microscope is in a safe state before running.

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
