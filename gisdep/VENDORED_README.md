# gisdep — GIS deposition control tool (v0.16.1)

> **pyFIB local patches (not yet upstream):**
> * v0.36.1 — `stage/cryo.py`: `go_to_grid` skips the CLEM SAFE detour when the stage
>   is already at / beside the requested grid (X/Y within 0.3 mm, Z within 1 mm, T/R
>   within 0.1 deg); otherwise unchanged. Patch: `gisdep_skip_clem_when_at_grid.patch`.

Standalone tool for cryo GIS (gas-injection-system) deposition on the ZEISS Crossbeam,
developed separately from pyFIB (mirroring the elymaker pattern) for eventual
integration. Python 3.9.13, Windows for instrument use; fully testable offline via a
faithful mock.

> **Nothing in v0.1.0 inserts a needle or opens a gas valve.** This release delivers the
> autofocus + working-distance-gate core, prerequisite checks, and a read-only probe to
> hunt for a hidden GIS API path. The actuation seam is stubbed. The deposition sequence
> is the next deliverable, once the GIS control path is confirmed.

## Cryo stage positioning — STEP 1 (VALIDATED LIVE)

`gisdep/stage/cryo.py` (`CryoPositioner`) drives the cryostage to a grid position for GIS
deposition — the first step, before autofocus / WD / deposition. Every move goes **via
CLEM SAFE** with the ordering that's safest for big moves:

1. To CLEM SAFE: **Z first, then X,Y,T,R** — **M is never moved**.
2. **Verify** CLEM SAFE by reading back the stage position.
3. To the chosen grid: **X,Y,T,R first, then Z** (Z, the approach, done last).
4. Verify the grid position.

Both GIS needles must be Parked and the stage Idle, or it refuses. In the GUI it's the
**Go to Grid 1 / Grid 2** buttons (Step 1); on the instrument use the CLI:

```
python scripts/go_to_cryo.py --grid 1                                 # MOCK
python scripts/go_to_cryo.py --grid 1 \
    --instrument --i-understand-this-moves-the-stage                  # live, confirmed
```

**Coordinates are now the real values** (`DEFAULT_WAYPOINTS` in `gisdep/stage/cryo.py`;
Grid 1/2 + CLEM SAFE, from the provided files). **Validated live**: a full go-to-Grid-1
executed the choreography and verified every axis exactly, with M already at 12.99 mm and
never moved, and the 10 µm / 0.1° verify tolerances adequate. They were recorded at
M = 12.99 mm and gisdep never moves M — a non-blocking warning fires if the current M
differs. The mock reproduces the full choreography (tests assert Z is isolated from
lateral/tilt moves, that it always routes via CLEM SAFE, and that M never moves).

## GUI

```
pip install PyQt5 qtpy
python -m gisdep.gui              # mock, full interactive (no instrument)
python -m gisdep.gui --monitor    # read-only monitor UI on the mock (offline preview)
python -m gisdep.gui --instrument # read-only LIVE instrument monitor (COM)
```

Uses the **elymaker dark theme** (`gisdep/gui/fib_dark_theme.py` + `.qss`; teal accent on
`#232833`, `apply_theme()` = Fusion + dark palette + stylesheet). The line-art follows the
GIS readbacks: needle Parked + valve Closed (ready), needle Inserted + valve Closed,
needle Inserted + valve Open (glowing tip = depositing).

**Full run.** The **Full Run** card (grid → port → dwell → *Run full sequence*) drives the
whole workflow: cryo position (via CLEM SAFE) → autofocus → step Z to 5.5 mm → **autofocus
fine to confirm the WD** → deposition. The individual Step 1/2/3 controls remain for
granular use. On the mock everything runs automatically and animates.

**Arm for live actuation.** On a real instrument the GUI starts as a **read-only monitor**
(badge: MONITOR). Ticking **Arm live actuation** (with a warning confirm) enables live
control (badge turns red: ARMED) — and every actuation step then pops a confirmation
dialog, matching the CLI's per-step prompts. The whole workflow is also available headless
via `scripts/run_full.py`. (The armed-instrument GUI path is new; do the first live full
run supervised.)

- **Live state** — needle/valve pills for both ports (green safe, blue inserted, red open,
  amber transit), stage Z, working distance.
- **Workflow — WD check + stage** — *Check working distance* (read-only, or *with
  autofocus*) shows an ideal / too-close / safe-suboptimal verdict; then *Move −Z / +Z*
  nudges (safety-gated: needles must be parked, Z limits [20, 40] mm) let you adjust.
- **Run deposition** — runs the real `deposit()` in a worker thread against the mock, so
  the images animate through insert → open → dwell → close → retract.
- **Simulate** — flip needle/valve state instantly to step through every image.

The three artworks are normalised to identical dimensions so they display in perfect
registration. GIS2 reuses the GIS1 art (captioned GIS2) until dedicated art is added.
Long operations run off the UI thread; the mock is thread-safe. See `docs/gui_preview.png`.

Theme note: this is a clean modern light theme (chosen to suit the white line-art). It's
an approximation of the elymaker look — drop in elymaker's stylesheet to match exactly.

## Why no GIS actuation yet

The SmartSEM Remote API in the manual (sections 6.1.8 / 6.2.10 / 6.4.8) exposes **no**
gas-injection control: no needle insert/retract, no precursor gas valve. The only valve
command in the whole API is `CMD_FIB_GUN_VALVE_OPEN/_CLOSE`, which is the **ion-source
column valve — not a GIS gas valve**, and must never be used for gas injection.

So the GIS path is unresolved. Two candidates:
1. **A hidden API mnemonic** — use the read-only probe (below) to check.
2. **A SmartSEM macro** driven via `Execute("MCF_…")` — the confirmed fallback.

## What's in this release (tested offline, 174 core tests (+15 GUI smoke tests when PyQt5 is installed) — 216 total here)

| Area | Module | Status |
|------|--------|--------|
| SEM autofocus (coarse + fine) with sustained-idle confirmation | `gisdep/focus/autofocus.py` | tested vs mock; **instrument-untested** |
| Working-distance gate (asymmetric safety classification) | `gisdep/focus/wd_gate.py` | tested; **thresholds are assumptions** |
| Stage-tilt gate (cryo 29–31° vs flat-stage-dev 0°) | `gisdep/prereq/tilt.py`, `checks.py` | tested; **degree convention unconfirmed** |
| Prerequisite checks (stage idle, tilt, chamber ack) | `gisdep/prereq/checks.py` | tested vs mock |
| `prepare_for_deposition` orchestration (no GIS) | `gisdep/orchestrate/prepare.py` | tested vs mock |
| Safety-gated stage movement (nudge-and-verify) | `gisdep/stage/mover.py` | **Z sign/scale + WD:Z coupling confirmed on instrument** |
| **GIS needle/valve readbacks + interpretation** | `gisdep/gis/state.py` | **confirmed live on scope** |
| **Live GIS state monitor (snapshot + watch)** | `gisdep/gis/verify.py`, `scripts/verify_gis_state.py` | tested; strictly read-only |
| Faithful Remote-API mock | `gisdep/api/mock.py` | premature-Idle quirk; models GIS readbacks |
| Read-only GIS API probe | `gisdep/gis/probe.py` | confirmed names first, then guesses |
| GIS actuation seam | `gisdep/gis/backend.py` | **scaffold only** (Stub + Mock) |
| Real COM backend | `gisdep/api/com.py` | init logic tested; **marshalling instrument-untested** |

## Prerequisites → autofocus → WD gate (`prepare_for_deposition`)

`gisdep/orchestrate/prepare.py` runs the go/no-go check before any GIS work: prerequisites
(chamber ack, stage idle, tilt) → autofocus → WD gate. It never touches the GIS.

```
python scripts/run_prepare.py                              # MOCK, read-only
python scripts/run_prepare.py --instrument --tilt flat     # live, read-only (no actuation)
python scripts/run_prepare.py --instrument --tilt flat --autofocus   # + autofocus
```

Default is a **read-only** check: reads stage-idle, tilt, and the *current* WD and
classifies it against the 5.5 mm gate — nothing is actuated (a good first instrument
run). Add `--autofocus` to also run `CMD_AUTO_FOCUS_COARSE/FINE` (real actuation,
instrument-untested from gisdep). The tilt gate defaults to cryo (29–31°); pass
`--tilt flat` for the flat dev stage or it will correctly block. Chamber-scope position
is confirmed via prompt (or `--assume-chamber-ok`).



Four digital parameters, measured working on the scope. All-uppercase (instrument rule,
enforced by a test); the rest of the naming is inconsistent across ports and stored
verbatim:

| Port | Needle | Valve |
|------|--------|-------|
| GIS1 | `DP_GIS_LOCATION` | `DP_GIS_VALVE_STATE1` |
| GIS2 | `DP_GIS_2ND_STAGE_LOCATION` | `DP_GIS_2ND_VALVE_STATE` |

Needle: `0` = Unknown (transit/error), `1` = Parked, `2` = Inserted (text "Gas 1" on
both ports). Valve: `0` = Closed, `1` = Open, `3` = Transit/Unknown (in transit or
error) — **found live** during valve motion, most clearly `1 → 3 → 0` while closing
(value `2` unobserved for the valve). **All logic keys off the integer**, never
the localisable text. `DP_GIS_2ND_STAGE_LOCATION` says STAGE but means the GIS second
*needle*, not the microscope stage.

## GIS commands (confirmed; data-only registry)

Verbatim Execute() command strings, stored in `gisdep/gis/commands.py` and pinned by
tests. The two ports share **no** naming convention, so the registry holds exact strings
per port rather than deriving them:

| Action | GIS1 | GIS2 |
|--------|------|------|
| Insert needle | `CMD_GIS_GOTO_GAS1` | `CMD_GIS_2ND_INSERT` |
| Retract needle | `CMD_GIS_GOTO_PARKED` | `CMD_GIS_2ND_RETRACT` |
| Open valve | `CMD_GIS_OPEN_CHANNEL1` | `CMD_GIS_2ND_OPEN_VALVE` |
| Close valve | `CMD_GIS_CLOSE_CHANNEL` | `CMD_GIS_2ND_CLOSE_VALVE` |

This module **executes nothing** — it is the command-name analog of `state.py`, kept so
the confirmed strings can't drift between sessions. Nominal mechanical timings (~5 s
insert, ~10 s retract, ~0.5 s open, near-instant close) are recorded on each entry as
**assumptions**, to be used only as closed-loop timeout bounds and confirmed on the
instrument. Both CMD_ commands and DP_ read mnemonics are all-uppercase (each enforced by
a test).

## Stage movement (INSTRUMENT-UNTESTED)

`gisdep/stage/mover.py` (`StageMover`) is the only path that moves the stage. It wraps
`MoveStage` with safety gates and verifies every move by readback:

- refuses unless **both GIS needles are Parked** and the stage is **Idle**;
- **caps** the single-move delta (1 mm / 2° default) and checks the target against
  `AP_STAGE_HIGH/LOW_*` (skips the limit check with a warning if those don't resolve);
- confirms completion by **idle AND arrival-at-target** (so premature-idle can't end the
  wait early), then **verifies** the achieved delta — command a known move, measure the
  actual change, fail if they disagree. This measures the true sign/scale/units.

`nudge_and_verify(axis, delta)` does a round trip (out then back), leaving the stage
where it started.

**Confirmed on the instrument** (dev-stage nudge, +200 µm Z): Z sign is correct
(+command → +motion), scale is metres to ~nm accuracy, and working distance tracks Z with
**dWD/dZ = −1** (+Z shortens WD 1:1). The mock models this coupling, so Z nudges change the
WD readout offline too. (Measured far from the 5.5 mm cryo config — the slope should
transfer, but confirm near 5.5 mm on the cryo stage.)

The test script is **Z-only** (Z is what drives working distance /
coincidence; +Z shortens WD) and reports the WD change alongside the Z move:

```
python scripts/test_stage_move.py --um 20                             # MOCK
python scripts/test_stage_move.py --um 20 \
    --instrument --i-understand-this-moves-the-stage                  # live, confirmed
```

Z carries **hard limits of [20, 40] mm** (from the coincidence work), enforced in
addition to the instrument's `AP_STAGE_HIGH/LOW_Z`. **Confirm these are right for your
stage before a live run** — override with `--zmin-mm` / `--zmax-mm`. The `--instrument`
path needs the acknowledgement flag and confirms each move.

**Go to a target WD.** Because dWD/dZ = −1 is confirmed, `StageMover.move_to_wd(target)`
takes ONE bounded Z step toward a target working distance (clamped to the max single move
and the Z limits; run again to continue if capped). In the GUI it's the "→ 5.5 mm WD"
button; on the instrument use the CLI:

```
python scripts/go_to_wd.py --target-mm 5.5                            # MOCK
python scripts/go_to_wd.py --target-mm 5.5 \
    --instrument --i-understand-this-moves-the-stage                  # live, confirmed
``` It reports requested vs.
measured microns, whether the Z sign matched, whether it returned to start, and the WD
change (so a first live run measures the +Z→WD relationship directly). Start small
(20 µm), with GIS parked, under supervision.

## GIS validation harness (no gas) — run before any deposition

`scripts/test_gis_needle.py` validates the GIS commands in risk order without ever opening
a valve. **Validated live on both ports:** Stage A (safe commands translate/execute) and
Stage B (needle in/out, readback-confirmed) both passed. **Measured needle times:** GIS1
insert 2.1 s / retract 2.1 s; GIS2 insert 3.4 s / retract 1.8 s — much faster than the
earlier 5 s / 10 s guesses, so the backend timeouts are tightened to 10 s (needle) with
generous margin.

- **Stage A (default) — safe-command check.** From a safe state (needle Parked, valve
  Closed), it `Execute()`s the safe-direction commands (close valve, retract needle) — the
  state the hardware is already in, so harmless no-ops — and confirms rc=0.
- **Stage B (`--needle-cycle`) — needle in/out, still no gas.** Inserts then retracts,
  confirming each via the readback and timing it. Mutual exclusion (other needle Parked)
  is enforced.

```
python scripts/test_gis_needle.py --port 1                        # MOCK, Stage A
python scripts/test_gis_needle.py --port 1 --needle-cycle         # MOCK, A + B
python scripts/test_gis_needle.py --port 1 --instrument \
    --i-understand-this-actuates-hardware                         # live, Stage A first
```

Still unvalidated live: the **valve** open/close under gas and the full `deposit()`
sequence (the next step: a short deposition on a sacrificial spot).

## Autofocus + delta-Z move — `test_focus_wd.py`

`scripts/test_focus_wd.py` runs SEM autofocus (coarse + fine), reads the resulting WD,
then takes ONE bounded Z step toward the target WD (default 5.5 mm) via dWD/dZ = −1 —
the "focus and set working distance" step. Reports WD before autofocus, after autofocus,
and after the Z move. `--no-autofocus` does the Z step alone.

```
python scripts/test_focus_wd.py --target-mm 5.5                    # MOCK
python scripts/test_focus_wd.py --target-mm 5.5 \
    --instrument --i-understand-this-actuates-hardware            # live, confirmed
```

## Closed-loop deposition sequence (VALIDATED LIVE)

`gisdep/gis/actuate.py` (`CommandGISBackend`) executes each GIS command and **confirms it
via the live readback** rather than a blind delay; needle times are now the measured
values. `gisdep/gis/deposition.py` (`deposit`) runs the full guarded sequence for one
port. **Validated live** — a short GIS1 deposition (2 s dwell, per-step confirmation) ran
insert → open → dwell → close → retract, each readback-confirmed:

```
preconditions → insert needle → open valve → dwell → close valve → retract needle
```

Enforced invariants (all tested offline against fault-injecting mocks):

- **Dwell clamped to 2–30 s** — out-of-range is rejected before anything actuates.
- **Never both needles inserted** — the sequence refuses to start unless the other
  port's needle reads Parked, and the backend re-checks the interlock at insert time.
- **Valve opens only** once the needle is confirmed Inserted.
- **Needle retracts only** once the valve is confirmed Closed — **except** on a close
  failure, where policy is to **retract the failed needle anyway and raise a loud alarm**
  (valve may still read Open; gas may be flowing — the alarm says so).
- **Safe teardown** on any abort/fault: close valve, then retract (retract even if close
  can't be confirmed).
- No −2 mm Z clearance move (WD is verified at 5.5 mm instead).

This path executes real commands and is **tested offline only**. Live use requires: the
readbacks verified live, a needle-only staged test (insert→retract, no gas), and the
prerequisite gates satisfied. Run it via:

```
python scripts/run_deposition.py --port 1 --dwell 5           # MOCK demo (safe)
python scripts/run_deposition.py --port 1 --dwell 5 \
    --instrument --i-understand-this-actuates-hardware        # live, per-step confirm
```

The `--instrument` path requires the explicit acknowledgement flag and prompts for
confirmation before each mechanical step (disable only with `--no-confirm`).

### Verifying them live

```
python scripts/verify_gis_state.py                        # one snapshot, MOCK (safe)
python scripts/verify_gis_state.py --instrument           # one snapshot, live
python scripts/verify_gis_state.py --instrument --watch   # poll + print every change
```

Strictly read-only (only `get_digital` / `get_digital_text`; never sets, never executes)
so it's safe to run while you drive the GIS by hand in SmartFIB. In watch mode, insert /
open / close / park each port and confirm every transition is printed and matches. It
also reads the instrument's own text label next to our integer interpretation, so you
can cross-check the mapping.

**COM initialisation (64-bit fix):** gisdep runs on 64-bit Python, where the control
rejects `Initialise()` and requires the remoting init. `ComSmartSEM` therefore defaults
to `init_mode="remoting"` and calls whichever remoting method the control exposes
(`InitialiseRemoting` or `InitialiseRemote` — the manual names both). This needs
`RemoteRelay.exe` running on the EM Server PC, and a one-time `RConfigure` with the
client/remote IPs set to `localhost` when running on the microscope PC. (`init_mode=
"local"` keeps the old direct `Initialise()` path for a 32-bit process, if ever needed.)

## Autofocus behaviour

Confirmed instrument fact carried over from pyFIB: `DP_AUTO_FUNCTION` can read **Idle
prematurely** — right after a focus command, before the routine actually starts. So the
routine (1) issues the command, (2) waits for `DP_AUTO_FUNCTION` to *leave* Idle (proof
it started), then (3) waits for it to *return* to Idle and stay Idle for a sustained
window (default 10 s) before declaring completion. The mock reproduces this quirk, so
the test `test_premature_idle_does_not_short_circuit` is a genuine regression guard.

Coarse then fine run as two separate confirmed cycles. An optional `abort` object
(anything with `.is_set()`, e.g. a `threading.Event`) lets a GUI cancel mid-run; on
abort the routine issues `CMD_ABORT_AUTO`.

## Working-distance gate (ASSUMPTIONS — confirm on instrument)

Physical picture from Evan: ideal cryo deposition at **WD = 5.5 mm**; the needle inserts
to the equivalent of ~5.0 mm (the extra 0.5 mm is safety clearance); larger WD is safer
but deposits differently; smaller WD eats the clearance → collision risk.

The gate is therefore **asymmetric**:

- WD **below** the floor (default 5.5 mm) → `TOO_CLOSE` → **insertion BLOCKED**.
- WD at ~5.5 mm → `IDEAL` → proceed.
- WD **above** ~5.5 mm → `SAFE_SUBOPTIMAL` → proceed with a warning (deposition differs).

`WDGateConfig` defaults — **all guesses pending your numbers**:
`target_m=0.0055`, `floor_m=0.0055`, `ideal_tol_m=0.0001` (0.1 mm band above target),
`measure_tol_m=2e-5` (20 µm slack on the floor comparison). Please confirm the tolerance
you want; each confirmed value becomes a regression test.

## Prerequisites

- **Chamber-scope position** — operator confirms visually; represented as a boolean the
  GUI must supply. gisdep never assumes it.
- **Stage idle** — `DP_STAGE_IS == Idle`, blocking.
- **Stage tilt** — blocking. Real cryo GIS requires ~30° (window **29–31°**); the current
  development stage is flat at **0°**. These are two explicit modes:
  - `CRYO_GIS_TILT` = 29–31° (the **default** — a missing choice fails safe)
  - `FLAT_STAGE_DEV_TILT` = 0° ± 1° (dev only; **not valid for real cryo**)

  The active mode name is always printed in the check detail so you can't miss that the
  permissive flat window is in force. Pass the mode via `tilt_cfg=` to
  `run_prerequisites` / `prepare_for_deposition`. Tilt is read via `GetStagePosition`
  (degrees, §4.10); the degree convention is **not yet instrument-confirmed** — if the
  readback were radians, a 30° stage would read ~0.52 and get blocked, so the gate fails
  safe against that ambiguity (a flat stage reads ~0 either way). Confirm on the
  instrument and turn it into a regression test — see `PROBE_PROTOCOL.md`.
- **FIB gun-valve closed** — *future, stubbed, non-blocking*. The API documents no
  readback for the gun-valve position, so the correct parameter must be found on the
  instrument before this can gate anything.

## Read-only GIS probe

```
python scripts/run_gis_probe.py               # mock (safe, finds nothing — expected)
python scripts/run_gis_probe.py --instrument  # live, READ-ONLY (COM path untested)
```

Strictly read-only: it only calls `Get()` on candidate `AP_`/`DP_` mnemonics and reports
which resolve (present) vs. `rc=1000` (absent). It **never** sets or executes anything.
Candidate `CMD_` names are printed for you to check by hand in the SmartSEM parameter/
macro editor — an unknown command is never executed "to see if it exists." Extend the
candidate lists in `gisdep/gis/probe.py` as you like. See `PROBE_PROTOCOL.md`.

## Running the tests

```
./run_tests.sh          # or: python -m pytest -q
```

## Open items / next steps

1. **Resolve the GIS control path** (hidden API vs. macro). If macro: provide the exact
   `MCF_`/`MCL_` names and what each does (insert/retract per port; valve open/close).
2. **Confirm WD-gate tolerances** (`ideal_tol_m`, `measure_tol_m`, and whether the floor
   should differ from the target).
2a. **Confirm the stage-tilt readback** (degrees vs radians; that the flat stage reads
   ~0 and a cryo stage reads ~30) and the exact cryo window if it isn't 29–31°.
3. Build the **deposition state machine** against `GISBackend`, enforcing: valve opens
   only while the needle is confirmed inserted; valve closes before retract; valve
   window clamped to **2–30 s**; and always-safe teardown (close valve → retract needle)
   on success, error, abort, or exception — with a confirm-before-each-step mode for
   early instrument runs.
4. Add the **GUI** (PyQt5 via qtpy, matching elymaker) once the sequence is settled.
5. **Reconcile `gisdep/api/com.py`** with pyFIB's proven COM layer before any live run.

## Run logging & Stop (v0.15.0)

**Run logging.** Every deposition and full run is recorded to a timestamped JSON file in
`gisdep_runs/` (override with `$GISDEP_RUNLOG_DIR`), plus a one-line summary appended to
`runs.csv`. Each record captures: parameters (grid/port/dwell), a state snapshot at start
and end (WD, stage X/Y/Z/T/R/M, GIS needle/valve), the result, the outcome
(completed/aborted/error), and the gisdep log lines from the run. Use `gisdep.runlog.RunLog`
as a context manager; the GUI wraps runs automatically and prints the log path.

**Stop.** A red **■ STOP** button in the header aborts the running operation and runs safe
teardown — it interrupts the dwell promptly and, for a GIS deposition, closes the valve and
retracts the needle (teardown ignores the abort so it always completes). Abort is threaded
through the stage moves, autofocus, cryo positioning, and the deposition dwell/polls, so it
works at any point in a run. The button is enabled only while an operation is running.

## Config file (v0.16.0)

Site constants — stage waypoints, Z limits, WD target, tilt window, default dwell,
recorded master-z — live in an editable JSON file so nothing is hard-coded. Copy
`gisdep_config.example.json` to `gisdep_config.json` (in the working dir, or point
`$GISDEP_CONFIG` at it) and edit the numbers (mm / degrees). Anything you omit falls back
to the built-in defaults (the real, confirmed values); a malformed file logs a warning and
uses defaults — config never breaks a run. Add grids by adding entries under `waypoints`.
The cryo waypoints and tilt window are read from here automatically; the GUI also reads the
Z limits, WD target, and default dwell.

Needle/valve transit states now display simply as **"Transit"** (the common case) rather
than "Unknown (transit/error)".

## Chamber images: start & blocked (v0.16.1)

Two more chamber states: **GIS_Start** (`cryo_start.jpg`) is shown just after GISdep loads —
the ready position with the X / Z movement hints ("GIS loaded — ready to position"). It
clears the moment you take any action. **GIS_Insertion_Not_Possible** (`cryo_blocked.jpg`,
the red X) is shown when a needle insertion is refused — mutual exclusion (other needle in),
stage not idle, or an unconfirmed/out-of-window tilt — with a red "Needle insertion not
possible" caption. It clears on the next action.
