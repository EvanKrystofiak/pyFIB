"""pyFIB: SmartFIB pattern executor for ZEISS Crossbeam / SmartSEM Remote API.

This package started as a minimal interface to execute a pre-defined SmartFIB milling
pattern (.ely) at the *current stage location*.

It now also includes a small "position capture" module to:
- set FIB magnification,
- grab an image,
- record stage coordinates and FIB objective potential,
- store/reload named positions (position_1, position_2, ...).

Stage movement and drift correction are still intentionally light-weight / opt-in.

New in pyFIB v0.4.0:
- includes a simple Tkinter desktop UI (python -m pyfib ui) to:
  * capture positions
  * mill positions
  * browse positions + preview/open BMP images
- keeps v0.2.5 improvements (long timeout + auto/blanked wait strategies)

UI theme:
- Black + gold UI (ttk theme + custom styles) (ttk theme + custom styles).

New in pyFIB v0.51.2 (report, Evan 2026-10-07):
- Position details: "Residual" header over the residual column of the pattern lines;
  Stage line labelled "X ... · Y ... · Z ... mm · T ... · R ..."; FIB line = "Mag 2000x ·
  WD 13.451 mm" only (field, pixel size and objective voltage on hover; scan rotation still
  added when it is not 0).
- Removed the note above the positions (+ = pattern origin, inset, views, hover) and the
  sentence under the Patterns heading.

New in pyFIB v0.51.1 (compact session report, Evan 2026-10-07: less text):
- Per position: "Stage" = X, Y, Z mm · T · R on ONE line; "FIB" = mag · field · objective
  · WD on one line (pixel size on hover); "Patterns" = Rough -> Prepolish -> Polish, one
  line each: status badge, pattern name ("(authored)" when it is), refine residual (with
  "800x" / "stage" when those were used); a short reason only when not milled (error /
  skipped / no run log). Run file, start time, after image and the full message on hover.
  Removed: the Position / FIB imaging / Patterns / Milling record headings, the source
  sentences, "At milling", after-image file rows, the header status badge.
- Image caption: only the view buttons and the key of the pattern ON SHOW (each stage's
  own currents); the shared notes (+ = pattern origin, inset, views) are said once above
  the positions.
- Patterns section after the stage layout: EVERY pattern used, each with its name, stage,
  source and positions ("Rough · default.ely", "Prepolish · <file>", authored ones), the
  dimensioned drawing and a compact parts table (Part x2 = mirror pair, Current, Depth,
  W x H, Dose, Recipe; probe name on hover). Replaces "Standard pattern".
- Summary: Rough / Prepolish / Polish columns = status mark + refine residual (pattern on
  hover); unused stage columns left out; Pattern / Status / Stages / Refine residual columns
  removed. Tiles: Positions, Rough milled, Prepolished, Polished, Errors (all stages), Runs
  (Assumed / Stopped / Not milled only when non-zero). Header: session name (path on
  hover), microscope, capture span.
- Runs table: Started, Stage (manual marked), Status, Milled n / requested, File.
- Positions two per row by default ("2 per row" toggle in the toolbar); stage layout plot
  capped at 420 px high. Demo session (8 positions, 3 stages): page 12 742 -> 5 478 px
  (two per row; 7 885 one per row), visible text 17 018 -> 5 924 characters.
- Tests updated to the compact markup (report 13/13, stage tests 11/11).

New in pyFIB v0.51.0 (milling stages: Rough Mill -> Prepolish -> Polish):
- Evan 2026-10-07: rough mill every lamella, then prepolish them, then polish them, each
  stage automatic (batch) or manual (site by site), documenting every step.
- Patterns: Rough Mill = standard pattern, a position's authored pattern overrides it (as
  before). Prepolish / Polish = their OWN pattern at EVERY position -- authored rough
  patterns are never milled there (fixes the v0.50.0 hazard of the new tabs milling the
  rough pattern). Default prepolish/polish .ely files: assets/patterns/prepolish.ely and
  polish.ely prefill the tabs once Evan supplies them; until then choose them.
- Refine reference: Rough Mill -> capture image; Prepolish -> the latest Rough Mill after
  image; Polish -> the latest Prepolish after image (800x fallback: the capture 800x).
  run_refine_at_position(reference_path=...).
- After images: ON by default in the stage tabs (any mill-positions --stage; off with
  --no-after-image), named images/<pos>_1-rough_<t>.bmp, <pos>_2-prepolish_<t>.bmp,
  <pos>_3-polish_<t>.bmp (never overwritten; history kept). positions.json records the
  latest per stage (PositionRecord.stage_after_images; schema still v2, older files load;
  re-capture keeps it). Pre-v0.51.0 <pos>_after_<t>.bmp counts as a Rough Mill image.
- Missing reference: a Prepolish/Polish batch never recalls a position without the
  previous stage's after image. They are listed up front (GUI dialog / terminal prompt /
  dry run) and need an OK (--accept-reference-skips); otherwise it refuses BEFORE any
  motion (rc 3), like the tilt check. Skips are in the run log and the report.
- Manual, per selected position, in all three tabs (CLI: site goto|align|push|after-image
  NAME --stage S, dry run without --arm, confirm dialog when armed):
    Go to: refuse if milling; zero beam shift; MoveStage (recorded tilt; no batch tilt
      limit -- single confirmed move); objective voltage; FIB magnification.
    Align: refine to the stage reference; beam-shift correction left on.
    Push pattern: drop + CMD_SMARTFIB_LOAD_ELY + CMD_SMARTFIB_PREPARE_EXPOSURE, NO
      CMD_FIB_START_MILLING (PatternJob.start_milling=False); warns if the stage is
      > 150 um from the position.
    Record after image: stage-named frame, recorded in positions.json and in a run log
      (kind "manual") with the last Align/Push of that position and stage.
  The buttons keep the tab on screen (run_cli(show_log=False)); output still in the Log.
- GUI: "Align to" and "After image" columns; Refine ticked by default in Prepolish/Polish;
  ticks survive a tab switch; status "Skipped (no ... image)".
- Report: Rough status/pattern/after image ignore Prepolish/Polish runs; each position's
  figure switches Before / Rough / Prepolish / Polish (each after image with that stage's
  pattern); per-stage rows in the details; "Stages" column; run kind shows the stage.
- Run log: "stage" field; stage_check(stage, skipped); skips from the tilt and stage
  checks are merged.
- Safety review (independent pass before delivery), all fixed:
    * manual buttons watch SmartSEM for 10 s (Go to; Align that may move the stage) or
      5 s (Push, Align, Record) and refuse on ANY milling reading -- the state can read
      idle for a moment between exposures; `site --stage` is REQUIRED (a default of
      rough could push the authored rough pattern onto a prepolished lamella);
    * Prepolish/Polish never mill a site whose refine failed (enforced in the batch;
      GUI box locked on); unrefined sites are warned in the dry run and need an extra OK
      in the GUI; they need --load-method smartfib_drop (else refused, nothing moved);
    * re-capturing a NAME at another place (> 50 um) drops its after images, and image
      files older than the capture are never a reference;
    * an unconfirmed (stale) staged frame is kept as *_suspect.bmp, never a reference;
    * positions.json is now written atomically (temp + replace); Positions-tab
      "Refresh patterns" re-reads the file first so it cannot drop after images.
- Tests: tests/test_mill_stages.py (11), GUI 31/31 (+2), report markup updated.

New in pyFIB v0.50.0 (Qt GUI: milling stages):
- The Mill tab is now ROUGH MILL, followed by two new tabs, PREPOLISH and POLISH (Evan,
  2026-10-07). Order: ... Positions, Rough Mill, Prepolish, Polish, Tools, Log.
- All three are the same MillPage (title/stage), so for now each has exactly the former
  Mill tab's options: standard pattern, wait, move method, imaging scan rate, continue on
  error, refine may move stage, skip site if refine fails, after-mill image, per-position
  Mill / AF / Refine ticks, the v0.46.0 tilt check before an armed batch. Each tab keeps
  its own selections and options. They will be specialised stage by stage next.
- Internals: NAV key "mill" kept for Rough Mill; new keys "prepolish", "polish";
  MainWindow.mill_pages() lists the three; each reloads on show and on New Session.
  Tools-tab text now says "milling tabs". Command line unchanged (mill-positions).
- Test: gui_qt test_three_milling_tabs_rough_prepolish_polish (order, labels, titles,
  independent selections). GUI 29/29.

New in pyFIB v0.49.0 (SEM autofocus, from the first two-stage run on 2026-10-06):
- First instrument run (v0.48.0, Tools tab): started 1.78 mm out of focus at 66x (WD
  7.932 mm, at the 8 mm limit); coarse extended 3x to a clean peak (contrast 0.84) at
  6.140 mm; fine -> 6.1544 mm, verify 96%; 23 frames, 55 s; all read-backs exact; mag,
  scan speed and raster restored.
- The scan is now left PAUSED (frozen) at the end of every SEM autofocus run, success or
  failure (Evan: it kept rastering after hand-back). The freeze is sent LAST, after the
  raster, scan speed and mag are restored (those can restart scanning); with freeze on
  End Frame it stops at the end of the frame in progress (confirmed for up to 3 s).
  Shared by two-stage, --single-stage and sem_autofocus.curve (scope.restore()).
- Two-stage goes straight from the coarse mag (500x) to the fine mag on one scope
  (SemFocusScope.retarget) instead of 66x -> 500x -> 66x -> 2000x -> 66x; the operator's
  mag / speed / full frame come back once, at the end.
- Fine-stage curve-contrast floor 0.3 -> 0.2 (--fine-min-contrast): the +/-90 um fine
  window at speed 4 spans only 0.41-0.53 on the 2026-10-05 data and 0.43 on this run,
  so 0.3 could reject a clean peak on a sparser area; noise alone gives ~0.1. The coarse
  stage keeps 0.3 (--min-contrast); the 60% verify frame is unchanged.
- The start/plan lines are logged once (they were printed by the plan and again by the run).
- --jump-settle-s (default 2 s, as measured) for the settle after a >= 0.3 mm WD jump;
  --settle-s (0.5 s per WD step) unchanged until measured: see
  test_scripts/SEM_AUTOFOCUS_TWO_STAGE_TEST.txt step 6.
- Tests: test_two_stage.py +3 (11): mag order 500 -> 2000 -> operator only; scan left
  paused on a simulator that restarts scanning on mag / speed / raster changes (and the
  old order fails there); fine floor 0.2 accepts a 0.2-0.3 replayed curve that 0.3
  rejects; plan logged once.

New in pyFIB v0.48.0 (two-stage SEM autofocus, from the measured focus curves):
- python -m smartfib_executor.sem_autofocus (and Tools -> SEM autofocus) is now
  TWO-STAGE (sem_autofocus/two_stage.py; Evan, 2026-10-05):
  * coarse at 500x, scan speed 4, centred box 0.35: WD start +/-600 um in 200 um steps
    (7 frames); if the sharpest frame is at an edge it extends 3 steps that way, again
    as needed, inside 4-8 mm (the window limit itself is tried last; <= 21 frames);
  * fine at --sem-mag (default: the current mag), speed 4, box 0.35: coarse result
    +/-90 um in 30 um steps (7 frames) + 2 halfway frames around the best + 1 verify.
  * Designed from sem_af_curve_20261005_162406 (true focus 6.942-6.945 mm): 200 um
    coarse steps at 500x land within ~30 um; 30 um fine steps at speed 4 within ~2 um
    (speeds 5/6 gave the same focus); box 0.35 is as fast as smaller boxes with twice
    the features of 0.25 (0.18 broke the coarse estimate); no throwaway frame needed
    after a big WD jump (2 s settle kept, as measured).
  * Replayed on the measured curves from 6 starting offsets (one needing an extension):
    focus found at -46.3...-47.5 um (truth -45...-50), 17-18 frames. Estimated ~30 s on
    the instrument (was ~2 min for +/-60 um).
  * Every single-stage guard applies (preflight, allow-list, window, read-back, 10 um
    probe, stage Z / contrast / brightness / mag / imaging unchanged, fresh frames,
    bracketed peak with contrast >= 0.3 in BOTH stages, verify frame); any failure puts
    the WD back. Mag, scan speed and box are restored after each stage, including a
    stage that fails half-way through preparing.
  * --single-stage keeps the previous search (+/-60 um, 10 um steps, speed 8, box 0.5).
    New options: --coarse-mag/-speed/-half-range-um/-step-um, --fine-speed/-half-range-um/
    -step-um. Outputs add coarse_curve.csv; frames are af2_<n>_<stage>_...bmp.
- Faster frames for both modes (scope.py): minimum live wait 0.5 -> 0.25 s (the
  1.2 x frame time + 0.3 s rule still applies) and DP_FROZEN polled every 0.05 s
  (was 0.2 s / 0.1 s; ~0.15 s of each measured 1.45 s grab was polling).
- Fix: sem_autofocus.curve wrote Windows paths into curve.csv; now "pass/file.bmp".
- Tests: sem_autofocus/tests/test_two_stage.py (8, incl. the measured-curve replay; data
  tests/data/af_curve_20261005.json). Instrument: test_scripts/SEM_AUTOFOCUS_TWO_STAGE_TEST.txt.

New in pyFIB v0.47.0 (SEM autofocus: focus-curve measurement):
- NEW python -m smartfib_executor.sem_autofocus.curve (Evan, 2026-10-05): data for a fast
  two-stage SEM autofocus (coarse at low mag / fast scan, then fine at 2000x). Default
  passes: coarse at 250x and 500x, scan speeds 4 and 5, WD start +/-1 mm in 100 um steps;
  fine at 2000x, speeds 4, 5, 6, +/-150 um in 15 um steps; centred 0.5 box. About 8-10 min.
- MEASURES ONLY: never applies a "best" WD. Each pass ends back at the starting WD
  (verified), and mag, scan speed and the raster box are restored after every pass.
  Same guards as SEM autofocus (GuardedSem allow-list, WD 4-8 mm, read-back, stage Z
  unchanged, fresh frames, contrast/brightness/mag/imaging unchanged -> any trip stops
  the run and puts the WD back). Dry run by default (read-only checks, plan, time
  estimate); --arm asks to confirm (--yes skips).
- Records everything as it goes: every frame (BMP) per pass, curve.csv (all metrics +
  a 3x-smoothed tenengrad, timings, WD set/read per frame; written frame by frame),
  sheet_<pass>.png contact sheets, curves.png (normalised curves), summary.json (peak,
  curve contrast, half band width, return-frame repeatability per pass and metric).
- Tests: sem_autofocus/tests/test_curve.py (4, in python -m
  smartfib_executor.sem_autofocus.tests). Instrument: test_scripts/SEM_FOCUS_CURVE_TEST.txt.

New in pyFIB v0.46.0 (mill batches: 2 deg tilt limit; bug 2):
- Evan, 2026-10-05: tilt must be recorded; the danger is recalling tilts far apart. For
  MILL BATCHES ONLY (setup, GISDEP, goto-position and every other stage move unchanged):
  a position is milled only if its recorded tilt is within 2 deg of the stage's current
  tilt AND no single recall in the batch changes tilt by more than 2 deg (milling order).
  A position with no recorded tilt is fine (its recall holds the current tilt). Large
  tilt changes stay manual / CLEM SAFE. New module smartfib_executor/tilt_guard.py.
- Up front, before ANY motion (even the beam-shift zero): the live tilt (AP_STAGE_AT_T)
  is read, the plan is printed (mill / SKIP per position with the reason) and every
  position that will NOT be milled must be OKed:
  * Mill tab, ARM on: Start first runs a read-only `mill-positions ... --tilt-check`,
    then (only if something is out of range) asks "These positions ... will NOT be
    milled ... Mill the other N?". Yes -> armed run with --accept-tilt-skips <those>;
    No -> nothing runs. Skipped rows show "Skipped (tilt)". Unarmed Start is unchanged.
  * CLI: `--tilt-check` (read-only, works without --arm, prints a TILT-CHECK line);
    armed runs ask y/N at a terminal, otherwise refuse (exit 3, nothing moved) unless
    `--accept-tilt-skips NAMES` covers every out-of-range position.
  * An unreadable tilt refuses the batch (exit 2). --move-method none: no check.
- The batch itself enforces the rule (refuses if an out-of-range position wasn't OKed)
  and re-reads the live tilt right before EVERY recall; if it moved outside the limit
  (vs the recorded tilt or the batch's starting tilt) that site errors without moving.
- Run log: "tilt_check" (live tilt, limit) and "skipped" [{name, reason}]. The report
  shows such positions as "Not milled" with the reason instead of "assumed milled".
- Dry run lists each position's recorded tilt (T=...) and the tilt-limit rule.
- Tests: tests/test_tilt_guard.py (6, now in python -m smartfib_executor.tests), GUI
  smoke test for the armed Start flow (28/28). Instrument check:
  test_scripts/TILT_LIMIT_TEST.txt.

New in pyFIB v0.45.0 (report: stage Z on the map, stage layout and mosaic):
- Positions captured at a different stage Z are now drawn where they really are (Evan,
  2026-10-05: "include Z"). At the milling tilt the FIB meets the grid at a few degrees,
  so a Z change moves the site under the FIB like a Y move (plus a little X). The map,
  the stage layout and the (opt-in) mosaic draw such positions at the equivalent stage
  X/Y at the median Z: map_x/y = stage_x/y + (+0.047 / -1.13) * (stage_z - z_ref).
- Measured, not derived, on session_20261001_170335 (tilt 1.97 deg, R 12 deg; positions
  5-7 captured 204 um higher than 8-25): 11 overlaps of the 800x FIB images across the Z
  step give Y -1.130 (-1.124...-1.139) and X +0.047; the lamellae of 5-7 on Evan's 55x
  SEM overview (pyCLEM overlay) give Y -1.104 and X +0.049. X/Y alone put 5-7 ~1.1 mm
  from their lamellae; with Z, within 2 um in X and 24-27 um along the grid in Y.
- Applied only at the tilt/rotation where it was measured (1.97 +/- 1 deg, R 12 +/- 1 deg)
  and only when Z differs by more than 1 um; otherwise positions are drawn by X/Y and the
  report warns (Z differs, no model there). Same-Z sessions are unchanged.
- Map schema pyfib.positions_map.v4: JSON "z_model" block (factors, z_ref, shifted
  positions, reason); rows and CSV gain stage_z_mm, map_x_mm, map_y_mm; px/py use map_x/y.
  Stage-layout hover shows the recorded X/Y/Z and "drawn at X.., Y.. for its Z"; the axis
  labels name the reference Z when anything moved. Mosaic JSON: placed_x/y_mm.
- Tests: report/tests/test_z_model.py (5; runs in python -m smartfib_executor.report.tests)
  with the measurements in tests/data/z_model_20261001.json. Wrong factors (Y 0 or -1.0,
  X 0 or -0.047) fail the instrument tests.
- Not done (Evan): no FIB focus estimate from stage Y.

New in pyFIB v0.44.0 (Qt GUI: Tools tab):
- NEW Tools tab, between Mill and Log (Evan, 2026-10-05). Each button launches an existing
  command through the GUI's subprocess worker (output in the Log tab):
  * Make report   -> python -m smartfib_executor.report <session positions.json>
                     [--pattern P] [--mosaic]. Reads files only, so it runs armed or not;
                     opens the finished report in the browser ("Open when done").
  * SEM autofocus -> python -m smartfib_executor.sem_autofocus --sem-mag M. Not armed:
                     read-only checks + plan. Armed: confirmed in a dialog, then --arm
                     --yes, results saved into the session folder.
  * FIB autofocus -> python -m smartfib_executor.autofocus --vmin A --vmax B ... with the
                     Mill tab's autofocus settings (AutofocusConfig: +/-150 V, 7 points,
                     beam parked 12 um, raster side auto, scan rate 8 via CMD). Bounds
                     default to the last captured position's objective +/- 400 V
                     ("From last position"). Not armed: --dry-run (verified read-only:
                     every set/execute/grab in the fork's dry-run path is skipped).
                     Armed: confirmed in a dialog first. Refuses bad bounds.
- New module gui_qt/tools_page.py; app.py nav gains "tools". No engine or CLI changes.
- Tests: GUI smoke +6 (tab order, report args/open, needs a positions file, SEM/FIB
  argv unarmed vs armed + confirm + No, and both argv lists parse with their CLIs).

New in pyFIB v0.43.2 (report: 800x mosaic off by default):
- The 800x mosaic is now OPT-IN (Evan, 2026-10-04: not happy with it; keep the code):
  python -m smartfib_executor.report <session> --mosaic   (replaces --no-mosaic).
  build_report(mosaic=False) by default. Code, tests and the overlap-fit finding (bug 13)
  are unchanged.

New in pyFIB v0.43.1 (report: less text):
- Removed, at Evan's request (2026-10-04): the caption and the warning list under the 800x
  mosaic (method, scales, seam figures and the different-Z warning remain in
  <report>_mosaic_800x.json and the console line), the orientation/labels sentences under
  the stage layout, and the description after the pyCLEM map links (links kept).

New in pyFIB v0.43.0 (report: 800x mosaic):
- NEW: the report shows the positions' 800x reference frames as one mosaic, placed by
  stage position (Evan, 2026-10-04: an aid, not full resolution; default 1/4,
  --mosaic-scale). Shown as the FIB sees them: larger stage X to the LEFT, larger stage
  Y DOWN (refine's measured stage signs), foreshortened in Y. Position labels are
  clickable. Also written next to the report: <report>_mosaic_800x.png (labelled) +
  .json (tile centres, matrix, seam figures). --no-mosaic leaves it out.
- Placement: refine's measured stage model first; where tiles overlap, phase correlation
  measures each seam, and with >= 4 confident same-Z pairs the stage->image matrix is
  refitted (least squares), re-checked, and used if it lines the seams up better. The
  caption says which, with the seam error. Positions at another stage Z are placed by X/Y
  only and flagged. New module report/mosaic.py; tests (5, synthetic tiles cut with a
  known matrix; a wrong correction sign fails them).
- CHANGE (Evan, 2026-10-04): the stage-layout plot and the pyCLEM positions map now draw
  stage Y increasing DOWNWARD, matching the 800x mosaic (X was already mirrored in
  v0.42.0), so all three share one orientation. report/map_export.STAGE_Y_INCREASES_DOWN;
  map sidecar schema pyfib.positions_map.v3: py = (stage_y_mm - y_top_mm) * 1000 / um_per_px.
  The Y direction is refine's measured STAGE_SIGN_Y = +1, confirmed by the mosaic overlaps.
- First real run (session_20261001_170335, 21 tiles, 800x, tilt 1.97 deg, R 12): refine's
  model (X 0.912, Y 0.583) left 4.6 um seams that grew with distance; the fit from 34
  overlaps gave X 0.985, Y 0.613, seams 0.92 um median, 1.9 um worst. positions 5-7
  (+204 um Z) flagged.

New in pyFIB v0.42.2 (report: fiducial overlay in purple):
- The fiducial drawn on position images is now solid purple (#a020f0, 2.5 px outline,
  30 % fill) instead of white dashed, which was too hard to see (Evan, 2026-10-03). Purple
  is not one of the current colours used for default.ely. Key under the image matches.

New in pyFIB v0.42.1 (report: overlays coloured by probe current):
- The pattern drawn on each position image now colours every shape by its probe current
  (Evan, 2026-10-03), with a key under the image (e.g. 700 / 300 / 100 / 50 pA). The SAME
  colour per current is used in the standard-pattern drawing, which is the legend:
  one current->colour map per report (report/pattern_diagram.current_classes), lowest
  current first, Okabe-Ito colours (colour-blind safe: worst adjacent CVD dE 13.1;
  bright enough on grey FIB images). For default.ely: 50 pA blue, 100 pA green, 300 pA
  yellow, 700 pA orange. Fiducial outlines are now white dashed (no fill) so they never
  look like a milling current (purple since v0.42.2). Instrument images unchanged.
- Tests: +2 (colour order/sharing; overlay colours match the drawing).

New in pyFIB v0.42.0 (report: stage layout mirrored in X; dimensioned pattern drawing):
- FIX (Evan, 2026-10-03): the stage-layout plot and the pyCLEM positions map now draw stage
  X increasing to the LEFT, as the Crossbeam images show it -- positions 5-7 (smallest X)
  sit on the RIGHT of the SEM overview. Same sign as refine's STAGE_SIGN_X = -1 (measured
  2026-09-22). One constant, report/map_export.STAGE_X_INCREASES_LEFT, drives both. Map
  sidecar schema pyfib.positions_map.v2: px = (x_left_mm - stage_x_mm) * 1000 / um_per_px.
  Instrument images are unchanged. Y is still drawn increasing upward.
- NEW: after the stage layout, the report draws the standard pattern (default.ely, or
  --pattern) to scale with: current + depth of each part on the left, widths above,
  heights on the right, and the centre gap (lamella). Mirror-symmetric top/bottom pairs are
  labelled once (width/current on the top shape, height on the bottom one). A table lists
  each part (current, probe, depth, width, height, dose factor, recipe). For default.ely:
  700/300/100/50 pA, 12 um deep, 16.5/15/13.5/12 um wide, 2.65/1.3/0.7/0.34 um high,
  gap 0.275 um. New module report/pattern_diagram.py.
- Tests: report/tests/test_pattern_diagram.py (7); stage-layout orientation test; map test
  updated for the mirrored X.

New in pyFIB v0.41.0 (report: assumed-milled, default pattern, 800x inset, before/after,
pyCLEM map; NEW optional after-mill image):
- Report (Evan, 2026-10-03):
  * A position with no run-log record is ASSUMED MILLED ("Milled (assumed)", hollow green
    ring). Tiles show logged and assumed separately.
  * Default pattern bundled: smartfib_executor/assets/patterns/default.ely (Evan's default
    lamella pattern). Drawn on positions with no run-log record and no authored pattern
    unless --pattern names another file.
  * The 800x image is now an INSET in the top-right corner of the 2000x image (30 % width,
    dashed box = the 2000x field); toolbar box hides the insets.
  * After-mill image: when one exists, the figure switches Before / After milling (per
    position, or all at once from the toolbar), with the pattern drawn on both. Found
    from the run log, else the newest <images>/<name>_after_<time>.bmp.
  * Labels are per name: position_N shows as N, any other name in full.
  * Positions map for pyCLEM, written next to the report:
    <report>_positions_map.png / .tif / .json / .csv -- black 8-bit image, a white dot +
    label per position at a fixed scale (default 1 um/px, --map-um-per-px), stage X right
    / Y up; the TIF carries ImageJ-style micron calibration; JSON/CSV give each
    position's pixel coordinates and the exact stage<->pixel transform. --no-map skips it.
- NEW after-mill image (OFF by default): mill-positions --after-image, Mill tab box
  "After-mill image". After milling a site and BEFORE the beam-shift reset, grab ONE fresh
  FIB frame at the capture magnification into <session>/images/<name>_after_<time>.bmp,
  using the capture tab's proven sequence (CMD_FIB_MODE_FIB -> mag -> End Frame freeze ->
  unfreeze -> freeze -> DP_FROZEN -> Grab; left frozen). Nothing is sent unless SmartSEM
  shows milling inactive; any failure freezes the scan, prints a warning and never turns
  the milled site into an error. Recorded in the run log (after_image). New module
  after_image.py; tests/test_after_image.py (7, added to the core runner).
- Tests: report 10 (+3), core +7 (after image), GUI smoke: the Mill tab box defaults off.

New in pyFIB v0.40.0 (session report + run log):
- NEW smartfib_executor/report -- one self-contained HTML report per session:
      python -m smartfib_executor.report <positions.json | session folder>
             [--pattern STANDARD.ely] [--out FILE.html] [--no-coarse] [--quality 80]
  Writes <session>/report_<time>.html. Reads files only: it never connects to SmartSEM
  and the report is the only file it writes.
  * Summary table, stage-layout plot (stage coordinates at capture; NOT the SEM image
    orientation), runs table, and one section per position: the reference FIB image with
    the pattern (cyan) and any recorded fiducial (amber) drawn on it, the image centre
    (pattern origin), a scale bar, the 800x image with the 2000x field marked, then
    coordinates, imaging conditions, pattern and the run-log record.
  * Which pattern is drawn: the one the latest logged attempt milled; else the
    position's authored pattern; else --pattern. A pattern file that changed since it was
    milled, or is missing, is flagged. Fiducial files that no position records are listed.
  * The overlay uses elymaker's own reader and ImageCalibration (rect anchor + rotation
    exactly as elymaker's RectItem; cross-checked against Qt in the tests). Verified on
    instrument data (session_20261001_170335): the num_06 / num_07 fiducial .ely files land
    on the milled "6" and "7"; best-match offset +0.50/+0.17 um and +0.33/+0.28 um
    (milled features right of / below the drawn outline). Regression test pins it.
- NEW run log: every ARMED mill-positions run writes <session>/runs/run_<time>.json and
  run_<time>.log (a copy of the console). Saved after every step, so a stop keeps what
  was done; a run killed outright stays "running" (the report says "ended without a
  final status"). Per site: start/end, stage target, recall, autofocus, refine (drift,
  residual, beam-shift and stage moves, 800x fallback), the pattern actually milled with
  its SHA-256, beam shift and scan rotation at milling, fiducial recorded, outcome
  (milled / error / stopped). Fiducial mills from the Positions tab are kind "fiducial".
  --no-run-log skips it; dry runs never write one.
  SAFETY: no new microscope reads or commands. Every call into the log goes through a
  guard in the batch, and RunLog methods never raise; a test proves that a log failing
  on every call leaves the batch's sequence of operations identical.
- batch.py: mill_positions_sequential(run_log=None); _report_mill_conditions returns the
  values it already read. milling_workflow.py: the refine result is annotated with
  coarse_fallback_tried / coarse_stage_delta_m (annotation only). cli.py: armed
  mill-positions creates the run log; new --no-run-log.
- Tests: python -m smartfib_executor.report.tests (overlay 7, run log 12, report 7).
- Seen in the 2026-10-01 session, NOT changed (bug list #11, #12): re-capturing a position
  replaces its record and drops its fiducial (position_6 has a milled "6" but records no
  fiducial); fov_height_m holds AP_HEIGHT (107.18 um), not the FIB field height (42.86 um)
  -- nothing uses it, and the report computes the field from pixels.

New in pyFIB v0.39.0 (UI: stage refine on by default; 800x capture + refine fallback;
SEM autofocus tuned from the instrument):
- From the 2026-10-01 tests (stage refine at 12 deg validated in the offset test and a
  mill batch; the 800x coarse pass validated in the CLI: 30+30 um -> 0.05 um).
- UI, Mill tab: "Refine may move stage (X/Y, <= 100 um)" is now TICKED by default (Evan).
  The CLI is unchanged: mill-positions still needs --refine-enable-stage.
- UI, Capture tab: NEW "Also capture 800x image (refine fallback)", ticked by default ->
  capture-position --coarse-mag 800 (one extra FIB frame at 800x, scan speed 5).
- capture-position: the 800x image path is now stored RELATIVE to positions.json (like
  the 2000x image; it was absolute) and printed in the summary ("Coarse image (800x):"),
  or "NOT captured" if the 800x grab failed (bug list #8).
- Mill batches: 800x FALLBACK in refine. Refine runs at 2000x first. Only if 2000x cannot
  LOCATE the site (low confidence / aliasing guard / correction not converging) and the
  position has an 800x image: beam shift put back to its pre-refine value, then the
  validated two-stage refine (800x coarse pass -> 2000x fine pass). Stage moves only if
  "Refine may move stage" / --refine-enable-stage, and never a second stage move at one
  site (if the 2000x pass kept one, the 800x pass is beam-shift only). No fallback when
  2000x located the site but was not allowed / able to correct it (stage disabled, or a
  stage guard refused). Log lines: "retrying with the 800x image", "800x fallback OK /
  FAILED", "STAGE moved (...) in the 800x pass". mill-positions
  --no-refine-coarse-fallback turns it off. RefineConfig: coarse_fallback,
  coarse_scan_rate (5), coarse_tolerance_m (1 um), coarse_magnification_default (800).
- SEM autofocus, from the instrument runs (bug list #3):
    * scan speed 8 is the default (restored afterwards; --scan-rate -1 leaves it). At 5
      the curve was too flat (0.24, rejected); at 8 it was 0.71.
    * sweep +/-60 um in 13 steps (10 um) by default; the 2 kV focus peak is a sharp cusp.
    * the fine sweep now samples halfway between coarse points (+/- one coarse step);
      before, its points landed ~0.5 um from coarse points (repeats).
    * DP_IMAGE_MODE reads 'Full' in SEM mode even with the reduced raster on (measured:
      5.5 s vs 20.9 s per frame at speed 8): a log note now, not a result warning.
    * the time estimate scales with scan speed (AP_FRAME_TIME doubles per step, measured).
    * Confirmed on the instrument: DP_FIB_IMAGING "SEM", DP_AUTO_VIDEO "Off",
      DP_NOISE_REDUCTION "Pixel Avg.", DP_FIB_MODE "Imaging", DP_OPERATING_MODE "Normal",
      DP_IMAGE_MODE "Full", DP_SPOT / DP_LINE_SCAN "Off", DP_IMAGE_STORE "1024 * 768",
      AP_FRAME_TIME in ms, AP_WD read-back within 1 nm. Box vs full frame: 0.8 um apart.
- Tests: workflow 25 (+7: fallback runs when 2000x cannot locate, with the 800x image,
  enable_stage and the 100 um cap, beam shift put back first; respects the stage
  opt-out; never chains a second stage move; no fallback when 2000x succeeds / located
  but stage disabled / stage refused; none without an 800x image or when disabled;
  failure follows the abort policy; CLI flag), gui_qt 21 (stage box ticked by default;
  capture sends --coarse-mag 800 by default), sem_autofocus 56 (+4: the instrument's
  state names pass preflight; scan speed 8 + 10 um steps; fine points mid-way; estimate
  scales with speed). All other suites unchanged.

New in pyFIB v0.38.0 (recall: MoveStage is the default stage movement):
- Instrument finding 2026-10-01 (bug list #7): a mill-batch recall 14 um away in X with
  the old default --move-method ap_goto (AP_STAGE_GOTO_*) did not move the stage at all
  (SmartSEM still read X = 43.505 mm; target 43.5186 mm), and the 0.3 mm arrival check let
  it through. With --move-method movestage (MoveStage, the call refine, setup_move, prep
  and GISDEP already use) the same recall moved, and stage refine then worked end to end.
- DEFAULT is now movestage everywhere recall happens: recall_position, mill-positions /
  mill_positions_sequential, goto-position, goto-and-mill, the Qt Mill tab "Move:" box
  (movestage listed first) and the legacy Tk UI. ap_goto and none remain selectable.
- SAFETY (movestage recall): MoveStage needs a tilt. It uses the recorded tilt if the
  position has one, otherwise the CURRENT tilt read live (so tilt does not change, same
  as ap_goto). The old fallback commanded tilt 0.0 if AP_STAGE_AT_T could not be read --
  a swing from the milling tilt to 0 deg. It now REFUSES (SmartSEMError, nothing moves).
- Unchanged: which axes recall drives (X, Y, Z, R, M, and T only if recorded -- bug list
  #2), the arrival wait (still the 0.3 mm tolerance of bug list #4), all other motion.
- Tests: recall 7 (+4: movestage is the default in recall / batch / all three CLI
  commands; the 2026-10-01 finding reproduced -- a GOTO deadband leaves a 14 um recall
  unmoved while the default moves it; current tilt held / recorded tilt used; unknown
  tilt refuses and moves nothing), gui_qt 21 (Mill tab sends --move-method movestage).

New in pyFIB v0.37.1 (SEM autofocus: reduced raster -> faster):
- Operator request: focus does not need the full frame. SEM autofocus now scans only a
  centred box (default half the width and height = 1/4 of the area, 512x384 at (256,192)
  on the 1024x768 screen grid) using the reduced-raster mechanism FIB autofocus already
  runs on the instrument: AP_RED_RASTER_POSN_X/Y + W/H, CMD_MODE_REDUCED, and
  CMD_MODE_NORMAL plus the operator's original box values on EVERY exit (success, abort,
  error, Ctrl-C). Only the box is grabbed (the rest of the framestore is stale).
- Faster: frame wait is now 1.2 x AP_FRAME_TIME + 0.3 s read AFTER the box is on (floor
  0.5 s; 2 s if unreadable) instead of a 2 s floor. On the simulator a run takes ~40% of
  the full-frame time; the 0.5 s WD settle per point now dominates.
- Metric kept at the validated PHYSICAL smoothing scale (presmooth fraction x screen
  width / box width); no databar crop inside the box.
- Reduced raster is only used when DP_IMAGE_STORE is 1024 * 768 (where raster and grab
  coordinates coincide, as in FIB autofocus); otherwise the full frame is scanned and
  the run says so. If DP_IMAGE_MODE does not read Reduced after the command, the run
  continues (the frame is still fresh, just slower) and reports it.
- Default minimum curve contrast 0.15 -> 0.3: a featureless image's noise "curve"
  reached 0.07 (full frame), 0.12 (half box) and 0.17 (third box) on the simulator; real
  SEM curves there are ~0.97. Lower with --min-contrast only if a measured curve needs it.
- CLI: --raster-frac F (0.1-1, default 0.5), --full-frame. GuardedSem additionally allows
  AP_RED_RASTER_* (range-checked to the screen) and CMD_MODE_REDUCED / CMD_MODE_NORMAL.
- Tests: sem_autofocus 52 (+8: box used for every frame and only the box grabbed, then
  full frame + original box values restored; restored on abort / edge reject / Ctrl-C;
  faster than full frame; store not 1024x768 -> full frame; reduced mode not confirmed
  -> warning, still correct; --full-frame; limits; a featureless image never passes with
  a third, half or full box). Each new guard's test checked to fail without it.

New in pyFIB v0.37.0 (SEM autofocus fork: pyFIB's own SEM focus, never SmartSEM's):
- Bug list #3 (2026-09-25): SmartSEM's CMD_AUTO_FOCUS_COARSE/FINE sometimes leaves
  AP_CONTRAST at 0. NEW fork smartfib_executor/sem_autofocus steps the SEM focus (AP_WD)
  itself at FIXED contrast/brightness, grabs frame-safe SEM frames, scores them with the
  validated FIB-autofocus metrics (tenengrad + presmooth, databar cropped) and sweep maths
  (coarse 9 x 25 um -> fine around the peak -> parabola), then verifies with one more frame.
- Operator window: WD 4-8 mm. Readings/targets outside it are treated as nonsense: the run
  refuses if the start WD is outside, every sweep point is clamped inside, GuardedSem
  refuses any AP_WD set outside it, and an edge peak at the window limit is rejected.
- Guards. Before anything changes (the dry run stops here): SEM beam on; imaging is
  SEM, FIB or SEM + FIB (anything else, or an empty read, refused) and DP_FIB_MODE not
  milling/deposition/switching; contrast not ~0; auto BC (DP_AUTO_VIDEO) off; Noise
  Reduction Line Avg or Pixel Avg (frame/continuous averaging mixes WD steps); normal
  full-frame scan (no split/reduced/spot/line scan); sweep <= +/-1 mm with >= 3 points.
  Core values (WD, kV, imaging, contrast, brightness, stage Z) must be readable; the
  secondary mode reads only warn if unreadable. During the run (abort -> WD back to the
  start): WD unchanged by the switch to SEM imaging, the scan PROVEN to go Live after
  each unfreeze and Frozen again (DP_FROZEN name, number fallback; unreadable = abort),
  AP_WD read-back within 2 um, stage Z unchanged (2 um) after every WD set, a 10 um
  probe step first (no image) so a bad read-back or a Z/WD link is caught on a tiny move,
  frames not blank/black/saturated and not identical to the previous one (stale
  framestore), contrast/brightness/mag/stage Z/SEM mode unchanged. Before accepting:
  peak bracketed (never extrapolated), curve contrast >= 0.15, result inside the window
  and the swept range, verification frame >= 60% of the sweep peak.
- Structural safety: GuardedSem allow-list. It can set only AP_WD, AP_MAG (only with
  --sem-mag; restored after) and DP_FREEZE_ON, and execute only CMD_FIB_MODE_SEM,
  freeze/unfreeze and CMD_SCANRATE<n> (only with --scan-rate; restored after). It cannot
  move the stage, set contrast/brightness, or run CMD_AUTO_FOCUS_*. A caller may narrow
  the 4-8 mm window, never widen it. Mag / scan rate are not changed unless their current
  value could be read back (so they can be restored).
- Frame timing from AP_FRAME_TIME (wait >= 1.2 x frame + 0.3 s) plus End-Frame freeze and
  DP_FROZEN; store size from DP_IMAGE_STORE. Frames are grabbed to an absolute temp file
  (the proven autofocus path) and copied into the run folder. The measured live->frozen
  time per frame is reported (confirms AP_FRAME_TIME units). Left in SEM imaging, frozen
  (FIB imaging is not switched back on, so no FIB dose).
- CLI (dry run by default): python -m smartfib_executor.sem_autofocus [--arm] [--simulate]
  [--sem-mag] [--scan-rate] [--half-range-um] [--points]. Exit 0 focus set / dry run
  OK, 1 rejected (WD restored), 2 refused, 3 WD restore NOT confirmed. Armed runs save frames,
  curve.csv and results.json (incl. every AP_WD set/read-back pair) to
  sem_autofocus_<time>/. test_scripts/SEM_AUTOFOCUS_TEST.txt: instrument test steps.
- Isolated like the other forks: coincidence and GISDEP still use their own autofocus
  until this is proven on the instrument. INSTRUMENT-UNTESTED: DP_FIB_IMAGING reads
  "SEM", the AP_WD read-back tolerance, AP_FRAME_TIME units, DP_AUTO_VIDEO names.
- Reviewed by a separate agent before release; its findings are fixed above (window
  widening, unknown imaging states, stale-frame paths, noise reduction/scan modes,
  unrestorable mag/scan rate, exit code for an unconfirmed restore, parameter limits).
- Tests: sem_autofocus 44 (convergence both sides; dry run changes nothing; window never
  left; refusals: WD outside / contrast 0 / auto BC / beam off / milling / unreadable
  guards / bad settings; mid-run: contrast -> 0, contrast nudged, mag changed, stale
  frames, never frozen, read-back off (caught on the probe, before any image), stage
  following WD (caught on the probe); acceptance: edge peak, window-limit edge, flat curve,
  focus that does not reproduce; COM error and Ctrl-C still restore WD; the allow-list
  blocks CMD_AUTO_FOCUS_*, contrast/brightness, stage moves; CLI; +14 for the review
  fixes: window never widened, unknown imaging / busy FIB / bad scan settings refused,
  unreadable secondary modes only warn, failed unfreeze / never Live / DP_FROZEN
  unreadable abort before scoring, numeric DP_FROZEN fallback, mag / scan rate left alone
  if not restorable, WD moved by the mode switch, parameter limits, saved frames are
  copies of fresh temp grabs). Each guard's test was
  checked to fail with that guard removed. All other suites unchanged and green.

New in pyFIB v0.36.1 (SAFETY: Prep waits for EHT truly off; GISDEP skips needless detour):
- PREP CryoFIBSEM (bug list #2, reported 2026-09-25): the column valve could be closed
  before the HT was off. The old wait polled only AP_ACTUALKV <= 50 V, which dropped
  while the EHT was still switching off. Now BOTH must hold, continuously for 3 s,
  before ANY valve command:
    * DP_RUNUPSTATE (Beam State, API manual 6.2.3) reads "EHT Off" (state 10);
    * AP_ACTUALKV <= 50 V.
  Timeout 180 s (was 120). If the Beam State cannot be read at all, prep ABORTS before
  valves/stage ("cannot confirm EHT off"). A numeric read of 0 ("Shutdown") never counts
  as off (the stale-VARIANT lesson). The log prints every Beam State seen, with the
  voltage and time -- this confirms the state names on the instrument.
  NOT YET INSTRUMENT-CONFIRMED: the state names come from the manual.
- GISDEP (bug list #4): go_to_grid no longer detours via CLEM SAFE when the stage is
  already at / beside the requested grid: X/Y within 0.3 mm (setup_move's rule), T/R
  within 0.1 deg (no swing), Z within 1 mm (one WD-step cap, so a previous run's WD
  adjustment still counts). Exactly there -> no move; beside -> direct move to the
  waypoint (X,Y,T,R first, then Z), still behind the operator confirmation and the
  GIS-parked / idle gates. Everything else still goes via CLEM SAFE. Local patch to
  vendored gisdep 0.16.1: gisdep/gisdep_skip_clem_when_at_grid.patch (see
  gisdep/VENDORED_README.md).
- Shared mock (instrument.py) now models DP_RUNUPSTATE.
- Tests: prep 16 (+6: valve waits for Beam State while kV already reads low -- PROVEN to
  fail on the old code; the 3 s hold restarts on a flicker; state off but kV high keeps
  waiting then aborts; unreadable Beam State aborts before valves; numeric 0 never
  counts; dry run describes both conditions). gisdep 246 (+10: no move when at grid;
  direct after a WD adjust, Z last; rotation wrap; tilt/rotation/Z/X out of range ->
  via CLEM; other grid -> via CLEM; direct move still confirms; direct move still
  refuses with a GIS needle inserted). All suites green; elymaker 244.

New in pyFIB v0.36.0 (stage refine available in mill batches, opt-in; test tools):
- From the 2026-09-25 test day: in T13 one site failed to refine because its drift
  exceeded the 10 um beam-shift window and the milling pipeline always ran refine with
  enable_stage=False (stage correction existed only in the standalone refine CLI).
- NEW mill-positions --refine-enable-stage and a Mill tab checkbox "Refine may move
  stage (X/Y, <= 100 um)". DEFAULT OFF. When on, refine may make ONE X/Y stage move
  (measured signs + tilt model; <= 100 um/axis, refused above; undone unless the drift
  halves; only at R 12 and tilt -1..31 deg, refused otherwise), then finish with beam
  shift. The batch log now prints "STAGE moved (dx, dy) um" when that happens.
  RefineConfig gains stage_max_m (100 um), passed to refine_position.
- NEW Mill tab checkbox "Skip site if refine fails" (= --refine-abort-on-fail, which
  already existed on the CLI): with "Continue on error" a site whose refine failed is
  skipped instead of milled at the recalled position.
- NEW python -m smartfib_executor.refine.offset_position <pf> <name> --dx-um 14: FILE-ONLY
  helper that copies a captured position with a known stage offset (keeps its reference
  image, clears any authored pattern, marks it TEST DECOY, backs up positions.json,
  refuses > 30 um). Recalling the copy lands off target, so refine's stage path can be
  tested inside a real batch without moving the stage by hand.
- test_scripts/STAGE_REFINE_TEST.txt: step-by-step test (Part A: offset_test CLI;
  Part B: decoy + mill batch). test_scripts/api_check.py: the read-only API check from
  the serialFIB diagnosis (bug list #1), now shipped.
- Tests: workflow 18 (+3: stage off by default in the pipeline; opt-in reaches
  refine_position with the 100 um cap and logs STAGE moved; CLI flag sets enable_stage),
  gui_qt 21 (+1: Mill tab checkboxes -> flags, only when refine is selected),
  offset_test 13 (+2: decoy offset/fields/backup; refusals). All suites green; gisdep
  236 + elymaker 244.

New in pyFIB v0.35.2 (no .bat files):
- Removed test_scripts/*.bat (Windows flags .bat files as a security risk). They only
  wrapped python commands; test_scripts/README.txt now lists the plain commands:
      python -m smartfib_executor.refine.offset_test                # T06 dry run
      python -m smartfib_executor.refine.offset_test --arm          # T06 plan check
      python -m smartfib_executor.refine.offset_test --arm --live   # T06 live
  No code changes.

New in pyFIB v0.35.1 (T06 test script: automatic stage-offset injection for refine):
- NEW `python -m smartfib_executor.refine.offset_test` (commands in test_scripts/README.txt): moving
  the stage ~15 um by hand is impractical, so the script does T06 end to end, per axis:
  grab a reference at 2000x -> move the stage a KNOWN offset (X/Y only, Z/T/R/M held,
  arrival confirmed to 1 um and steady) -> refine --plan-only -> CHECK the planned stage
  move reverses the offset (sign + within 1 um) -> with --live and only if the plan
  passed, run the real correction and require residual <= 0.3 um -> restore stage AND
  beam shift. A final frame is correlated with the first reference (return check).
  Default offsets come from the tilt model so the IMAGE moves ~13 um (past the 10 um
  beam window, inside the ~17 um aliasing guard): tilt 12 -> X 14.3 / Y 18.5 um.
  Guards before any motion: tilt/R inside the calibrated model, offset <= 30 um.
  Dry run by default (read-only); --arm asks for confirmation. Stops at the first
  failure; a failed restore prints the coordinates to return to by hand. Saves
  results.json + PNG frames to refine_offset_test_<time>/. Also does T07:
  --axes x --offset-x-um 30 must come back "no confident measurement" (aliasing guard).
- FIX (refine adapter): move_stage_xy waited for arrival with wait_stage_arrival's
  DEFAULT 0.3 mm tolerance. Refine's X/Y moves are <= 100 um, so the start position
  already counted as "arrived" -- with the premature Idle, the next frame could be
  grabbed mid-travel. Now 1 um (stage lands within ~3 nm on 20 um steps). Affects
  refine's live stage correction and --calibrate-stage.
- NOT CHANGED, SUGGESTION: recall_position (positions.py) uses the same 0.3 mm
  tolerance. Recalling between two sites less than 0.3 mm apart could return before
  the stage moves; autofocus's first frames then take the hit. Outside the refine area
  -- left as-is pending your OK.
- Tests: offset_test 11 (default offsets = 13 um image drift at 2/12/30 deg; plan-only
  pass + restore + return check; live both axes; WRONG-SIGN stage stops before live and
  restores; refused before any motion/imaging outside the model or above 30 um; small
  offset flagged as not exercising the stage path; T07 refused by the aliasing guard;
  loud restore failure; mid-axis error recorded + restored; CLI writes results/images);
  calibrate-stage 16 (+1: adapter arrival tolerance 1 um). All suites green; gisdep 236 +
  elymaker 244.

New in pyFIB v0.35.0 (ELYMaker lamella designer: rounded INNER corners, per stage):
- Lamella dialog: a "Round inner" checkbox on each stage row, plus one "Corner radius
  (x box height)" setting (default 0.5). Off by default -- with nothing ticked the
  output is byte-for-byte the same RECT lamella as before.
- A ticked stage's two boxes get their two INNER corners (facing the lamella)
  rounded; the outer corners stay square. Radius = frac x box height, CAPPED at
  h_step/2 (1 um by default) so a curve never reaches past the end of the next finer,
  narrower stage (which would leave unmilled material inside the trench); also capped
  at the geometric limit. Default spec: Rough 1 1.0 um (capped from 1.33), Rough 2
  0.65, Fine 1 0.35, Fine 2 0.135, Polish 0.10. Side trenches stay square.
- A rounded box is written as a POLYGON (8 points per corner) with
  edge_sel_marked_edge set to its STRAIGHT INNER EDGE, computed from the geometry in
  elymaker's convention (edge N runs vertex N-1 -> vertex N) -- so it still mills
  TOWARD the lamella, like the rect. The designer's "mill toward" reads bottom (top
  box) / top (bottom box), as for the rects.
- NOT INSTRUMENT-CONFIRMED: the marked-edge index is confirmed on the instrument for
  RECTs only (edge 3 = bottom). No real SmartFIB file with a POLYGON marked edge has
  been milled yet. Load one rounded lamella in SmartFIB and check the scan direction
  arrow points toward the lamella before milling a real site.
- Tests: elymaker 244 (+6: default unchanged; rounded stages are polygons marked on
  the straight inner edge of the right length; only inner corners rounded; auto radius
  + cap; writer/reader round trip keeps polygons and marked edges; dialog per-stage
  checkbox + radius field). pyFIB core files unchanged; all suites green; gisdep 236.

New in pyFIB v0.34.0 (Capture tab: numbered fiducials, auto-incrementing names, tilt by default):
- NUMBERED FIDUCIALS 1-30 from elynumbers v0.3.0 (loop-free seven-segment polygons,
  0.9 um bridges, verified one connected unmilled region). Shipped as
  assets/fiducial_numbers/polygons_um.json (+ manifest, contact sheet). Each number is
  written as a POLYGON .ely THROUGH ELYMAKER (LayoutBuilder.polygon + write_ely --
  never a hand-rolled ELY) into <session>/fiducials/num_NN_<side>.ely, centred on the
  side's prototype square (left -21.07 / right +21.10 um) with the prototype's own
  exposure (30kV:300pA, Vitriuos_Ice.epm, depth 5 um, frame 76.3 um). Every number,
  both sides, fits at 2000x with >= 2 um margin (widest two-digit: +3.6 um).
  `python -m smartfib_executor.fiducial --write-all DIR` writes all 60 for inspection.
- CAPTURE TAB: Fiducial row = kind (number | square) + side + # (1-30) + "Mill fiducial
  here" (number mode writes the file, fit-checks it, runs `run --fib-mag`). "Record on
  capture" shows what will be recorded ("records #7 on the left").
- AUTO-INCREMENT: after a SUCCESSFUL, ARMED capture the name advances (position_1 ->
  position_2; keeps zero-padding; skips names already in the session) and, if a
  numbered fiducial was recorded, the number advances. A dry run or a failed capture
  advances nothing. "Record on capture" is cleared after each capture, so a fiducial is
  never carried to the next position. On opening the tab, a name or number already used
  in the session is bumped to the next free one. Re-capturing an existing name asks
  before overwriting.
- TILT RECORDED BY DEFAULT: capture_position(include_tilt=True); CLI --include-tilt is
  now the default (--no-include-tilt to opt out); the UI box is ticked. Consequence:
  RECALL now restores the recorded tilt for newly captured positions (ap_goto sets
  AP_STAGE_GOTO_T and the arrival wait checks it).
- PositionRecord gains fiducial_number; CLI capture-position --fiducial-number N; the
  Positions tab "Fid" column shows e.g. "left #7".
- ORIENTATION NOT YET INSTRUMENT-CONFIRMED: numbers read correctly in elymaker's +Y-up
  frame, but a vertical flip turns a seven-segment 2 into 5 (and 25 into 52). Mill one
  test number on a sacrificial area and confirm it reads upright in the FIB image.
- Tests: fiducial 11 (+5: round-trip vs the source polygons with offset and prototype
  exposure, all 60 fit at 2000x, range check, --write-all = 60 files, capture records
  the number and tilt by default); gui_qt 20 (+2: next-name rules; mill #1 -> capture ->
  counters advance only on armed success, pending fiducial cleared, overwrite prompt).
  All suites green; gisdep 236 + elymaker 238.

New in pyFIB v0.33.0 (two-stage refine: coarse 800x pass, then the unchanged 2000x pass -- CLI only):
- CAPTURE: `capture-position --coarse-mag 800 [--coarse-scan-rate 5]` also grabs a
  COARSE reference (<name>_coarse.bmp) right AFTER the fine one, frame-safe, at scan
  rate 5, then restores the fine mag and the original scan rate. Off by default; the
  fine capture is unchanged either way. New PositionRecord fields image_bmp_coarse and
  coarse_magnification_x (v2-additive; older files load with None). A coarse failure
  keeps the fine capture and records a WARNING.
- REFINE: `refine --coarse-reference <name>_coarse.bmp [--coarse-magnification 800]
  [--coarse-scan-rate 5] [--coarse-tolerance-um 1]` runs a COARSE pass first: measure
  at 800x (143 x 107 um field; ~47 um X / ~36 um Y of drift measurable), use the stage
  if --enable-stage and the drift exceeds the beam-shift window (refine-only limits:
  X/Y, <= 100 um, one move, undone unless the drift halves, tilt/R guards), converge
  to 1 um with beam shift, then run the FINE pass at 2000x exactly as before (beam
  shift only). The FIB is left at the fine mag even if the coarse pass fails. Works
  with --plan-only and, in --simulate, with --coarse.
- WHY COARSE FIRST, NOT AS A FALLBACK: at 2000x an out-of-reach drift does NOT fail
  cleanly -- phase correlation wraps it to the wrong side with a confident peak (the
  simulator measured a true +30 um as -27 um at snr 87). So the fine pass cannot be
  relied on to detect a large drift.
- THE ONE CHANGE TO THE 2000x PATH (a guard only): refine_position refuses a measured
  drift beyond 40% of the field ("may be aliased ... use a coarse pass") instead of
  acting on it. Normal drifts are unaffected. The real adapter's grab also accepts an
  optional per-call scan rate (default = its existing rate) and gains
  ensure_magnification(); neither changes fine-pass behaviour.
- SIMULATOR renders at any mag by cropping/downscaling a larger canvas whose centre is
  exactly the existing scene, so the 2000x rendering is bit-identical to before; grabs
  log (mag, scan rate) and ensure_magnification() calls for tests.
- NOT DONE (by design): no UI, and mill-positions is untouched. STAGE CALIBRATION AT
  800x is still to be MEASURED: the tilt model's constant factors (~0.91 / 0.95) may
  fold in a pixel-size term that is mag-dependent -- run `refine --calibrate-stage
  --magnification 800` once and compare with the 2000x values before trusting coarse
  stage moves on the column.
- Tests: refine 51 (+7: 800x renders consistently with 2000x; fine-only refuses the
  aliased 30 um; two-stage recovers 30 um via stage then fine to < 0.3 um, coarse at
  rate 5 and fine untouched, mag restored; small drift is beam-only; stage-disabled
  stops before the fine pass; plan-only moves nothing; mag restored even if the coarse
  grab raises), capture 7 (+3: coarse grabbed after fine with mags [2000, 800, 2000]
  and scan rate 5 then restored; coarse failure keeps the fine capture and warns; no
  coarse by default). Pipeline/UI files byte-identical. All suites green; gisdep 236 +
  elymaker 238.

New in pyFIB v0.32.3 (FIX: capture could save the PREVIOUS image instead of a new one):
- BUG: capture-position did CMD_UNFREEZE_ZONE and then Grab() IMMEDIATELY. Grab()
  saves the frame store as-is, and right after an unfreeze the store still holds the
  LAST FROZEN frame (a new frame takes seconds to scan). Whenever the scan was frozen
  going into capture -- which pyFIB itself causes: every capture ends frozen, and
  refine/autofocus leave it frozen -- the saved BMP was the previous frame (or a band
  of new lines over it). Typical: capture A -> move stage -> capture B saved A's image.
  It "worked sometimes" only when the scan happened to be live beforehand.
  Consequences: refine correlated against a reference of the WRONG site; the record's
  stage coordinates (read live) disagreed with its image; a stale frame could be at the
  previous mag while the record claimed the new one (wrong pixel size / FOV for
  ELYMaker); and the capture after "Mill fiducial here" could save a pre-milling frame,
  defeating the fiducial-before-capture fix.
- FIX: capture now uses the frame-safe grab the refine/autofocus adapters use on the
  instrument: DP_FREEZE_ON = End Frame -> unfreeze -> scan >= one frame (default 3 s)
  -> freeze (completes the frame) -> wait DP_FROZEN -> Grab(). Scan left FROZEN. If
  DP_FROZEN never confirms, the capture still completes but records a WARNING in the
  position's notes. New core module smartfib_executor/frame_grab.py holds the sequence
  (a core copy -- forks stay isolated; refine/autofocus/coincidence can adopt it later).
  Capture takes about one frame longer. New capture flags --frame-wait-s (3.0) and
  --frozen-timeout-s (30). --no-unfreeze-before-grab keeps its explicit meaning: grab
  the frame currently on screen, as-is.
- Tests: test_capture_fresh_frame (4) with a frame-store mock -- the second capture
  saves the new site (the OLD code is shown to save site A's image as B, reproducing
  the report), the as-is flag grabs what is displayed, freeze_after_grab=False leaves
  the scan live, an unconfirmed frame is recorded as a warning. All suites green;
  gisdep 236 + elymaker 238.

New in pyFIB v0.32.2 (stage calibration complete; refine --plan-only):
- CLEAN 12-deg REPEAT with the v0.32.1 probe (tilt 12.04, R 12.00): Y 0.707 vs model
  0.705 (+0.3%) and within 0.3% of the first 12-deg run; X 0.924 (confirms the earlier
  0.980 was contaminated); return-to-start offsets 0.09 um (X) / 0.03 um (Y) -- no
  drift this time, so the 1.2 um jump was a one-off, and the stage returns to start
  within ~0.1 um (the undo in stage correction is trustworthy). Signs X -1 / Y +1 and
  rotation ~0.8 deg as before. The tilt model is validated over the milling range;
  stage calibration is COMPLETE. Constants unchanged. Bundled + locked as a fixture.
- NEW `refine --plan-only`: grabs REAL frames, measures the drift, runs every guard
  (tilt range, rotation, 100 um cap) and prints what refine WOULD do -- the beam-shift
  or stage delta, or why it would refuse -- then stops. Moves NOTHING (no beam shift,
  no stage); no confirmation prompt needed. The safe first step on the instrument.
- CORRECTION: the v0.32.0 notes said --dry-run shows the decision without moving. In
  real mode --dry-run grabs NO images (the adapter returns a blank frame), so there is
  nothing to measure. Use --plan-only instead.
- Tests: refine 44 (+2: plan-only moves neither stage nor beam in the beam, stage and
  both refused branches, while still measuring; reports the stage move it would make),
  calibrate-stage 15 (+1: clean 12-deg repeat locked). Pipeline files unchanged. All
  suites green; gisdep 236 + elymaker 238.

New in pyFIB v0.32.1 (12-deg calibration validates the tilt model; probe drift check):
- OUT-OF-SAMPLE VALIDATION: the tilt model was fitted on the 2 and 30 deg runs only.
  The 12.044 deg run (2026-09-22, R 12) measured Y = 0.709; the model had predicted
  0.705 (+0.6%). The model now covers the 0-12 and 30 deg milling tilts on measured
  evidence. Constants unchanged (not refitted, so the prediction stays a real test).
- The 12-deg run's X (0.980) was CONTAMINATED, not a real scale change: the Y step
  also moved the image +25 px in X (vs ~5 px in the other runs) -- an image offset of
  ~1.2 um appeared after the baseline and inflated both measurements. Removing it gives
  X ~0.92, consistent with 0.902 / 0.922; STAGE_SCALE_X stays 0.912. (Even if it were
  real, a 7% X error only leaves a 7% residual for beam shift to finish.)
- PROBE FIX (no extra stage motion): after each axis the probe already returns to the
  start position; it now grabs a RETURN frame there. Its offset from the baseline
  measures drift / settling jumps / backlash during that axis; above 0.5 um the axis is
  flagged ("SCALE may be contaminated -- repeat; the sign is still valid") and a
  QUALITY line is printed. The return frame becomes a FRESH baseline for the next axis,
  so an offset on one axis can no longer carry into the next (the cause of the 25 px Y
  cross-talk). Costs one extra FIB frame per axis.
- Tests: calibrate-stage 14 -- the 12-deg file bundled and locked as an out-of-sample
  validation; a simulator that reproduces the run (a one-time +22 px jump after the
  baseline) proves X is flagged while Y stays clean (cross-talk < 2 px, scale within
  2%); a clean run reports no drift. refine.py, geometry.py and the pipeline are
  unchanged. All suites green; gisdep 236 + elymaker 238.

New in pyFIB v0.32.0 (refine stage correction, Stage 2b: tilt model + guarded CLI path):
- TILT MODEL from two instrument calibrations (2026-09-22, 2000x, R 12, 20 um):
    tilt 30.0: scale X 0.902, Y 0.865      tilt 1.97: scale X 0.922, Y 0.584
  Y (the axis foreshortened by the FIB viewing angle) follows
      scale_Y = 0.946 * cos(53.9 deg - tilt),   scale_X ~ 0.912 (constant, +/-1.1%)
  The 53.9 deg was SOLVED from the ratio of the two runs, not assumed; the Crossbeam
  FIB column is at 54 deg. The model reproduces both runs to 0.1% in Y. A single
  fixed calibration fails across tilts (30-deg values at 2 deg leave 32% of the
  drift; 2-deg values at 30 deg overshoot 48%), so the correction reads the LIVE
  tilt. Milling tilts 0-12 and 30 deg; the model predicts Y 0.70 at 12 deg -- one
  calibration at ~12 deg would validate the interpolated range.
- refine stage correction (enable_stage=True only), REFINE-ONLY limits:
    * guards: live tilt within [-1, 31] deg (model range) and live R within 1 deg of
      the calibration R (12). R is fixed for cryo-milling and is NOT modelled; the
      guard exists because an earlier session milled at R 316.4 -- if a session's
      fixed R differs, re-run --calibrate-stage at that R;
    * X/Y ONLY (Z/T/R/M held from the live read-back);
    * a required move above 100 um on either axis is REFUSED (no partial move);
    * ONE stage move per refine; drift must drop below 50% of before, else UNDONE;
    * the residual is handed to beam shift (method "stage+beam_shift").
  stage_correction() now REQUIRES tilt_deg (keyword-only) so a stale tilt can never
  be used silently.
- CLI: `refine --enable-stage [--stage-max-um 100]`; in real mode it now asks for
  confirmation before running (the stage may move). Simulator models the measured
  signs and the tilt model.
- --calibrate-stage records tilt AND rotation; its scale warning now compares against
  the TILT MODEL (foreshortening is expected) instead of the nominal pixel size.
- PIPELINE UNTOUCHED: mill-positions keeps enable_stage=False; batch, milling_workflow,
  positions, smartsem_com, cli, setup_move, prep_cryo, coincidence are byte-identical
  to v0.31.0. Pipeline opt-in is Stage 3, after `refine --enable-stage` is proven live.
- FUTURE (documented, not built): at 2000x correlation can measure only ~19 um (X) /
  ~14 um (Y) of drift, so stage correction serves a thin band just above the beam-shift
  window. Larger drifts would need a low-mag reference captured with each position,
  and a stage calibration at that magnification.
- Tests: refine 42 (+7 stage-path: stage->beam handoff under 15% under-travel, undo of
  a wrong-handed response, refusal on tilt range / rotation / cap / unreadable tilt,
  convergence at 2, 12, 30 deg); calibrate-stage 11 (both calibration files bundled and
  locked: signs, precision, model reproduces both, fitted angle ~54, guards). All
  suites green; gisdep 236 + elymaker 238.

New in pyFIB v0.31.0 (refine stage correction, Stage 2a: MEASURED signs + scale baked in):
- The stage sign convention is now MEASURED on the instrument (2026-09-22, 2000x,
  20 um steps, stage tilt 30 deg) and baked into refine/geometry.py:
    STAGE_SIGN_X = -1 (unchanged)   STAGE_SIGN_Y = +1  (the placeholder had -1: WRONG)
  Had the stage path been enabled on the placeholder, every Y correction would have
  moved the wrong way and doubled the error -- the reason the probe was built first.
  Rotation hint +0.7 deg (~1.2% cross-talk): no rotation term needed. Stage precision
  excellent: achieved 19.9974 um of 20.0 commanded (0.01%).
- KEY FINDING -- stage->image SCALE: a 20 um stage step moved the image only 323 px
  (X) / 310 px (Y), not the 358 px the nominal 55.8 nm/px predicts (response_raw
  0.902 / 0.865). The image pixel size is right; at 30 deg tilt a physical stage
  displacement projects onto the FIB image FORESHORTENED, differently in X and Y. A
  correction that converts image drift to metres with the nominal pixel size would
  UNDER-correct by 10-15%. geometry.stage_correction now divides by the measured
  per-axis scale (STAGE_SCALE_X/Y). These are TILT-DEPENDENT and valid only at the
  calibration tilt (STAGE_CALIBRATION_TILT_DEG = 30); the calibration file now records
  tilt_deg so a mismatch can be detected rather than silently applied.
- The refine simulator now models the REAL instrument (measured signs AND scale), so
  the enabled stage path converges in tests exactly as it should on the column.
- Regression tests locked to the actual calibration file (bundled as
  refine/tests/refine_stage_calibration_2026-09-22.json): signs, scale, precision,
  rotation; and that stage_correction scales UP to beat foreshortening and uses the
  measured Y sign. refine 35 + 9. All suites green; gisdep 236 + elymaker 238.
- NEXT (Stage 2b, needs the tilt question answered): the capped (100 um, X/Y-only,
  refuse-above-cap) correction that hands back to beam shift, with a tilt check
  against the calibration; standalone `refine --enable-stage` on the instrument;
  then opt-in (default OFF) in mill-positions.

New in pyFIB v0.30.1 (ELYMaker probe colours: option B, categorical Okabe-Ito):
- Probe colouring is now CATEGORICAL: one fixed, colourblind-safe Okabe-Ito colour
  per library probe (ordered by current, arranged so neighbouring probes alternate
  warm/cool). The old log-hue ramp put adjacent stages on adjacent hues (100 pA green
  vs 50 pA cyan-green; 1.5 nA and 700 pA both red) -- the exact pairs drawn nested
  together. Now every adjacent lamella pair is >= 120 deg apart in hue. Deterministic
  (700 pA is always the same colour); unknown currents map to the nearest library
  probe on a log scale; no-probe stays grey. Four extra colours beyond the strict
  Okabe-Ito 7 keep all 11 library probes unique (the common 20 pA..1.5 nA range stays
  fully Okabe-Ito).
- Fill alpha in probe mode raised 110 -> 170 and the edge to an opaque 2 px, so
  nested fine boxes no longer BLEND into the coarse boxes beneath them.
- The canvas legend is now driven by the probe library (one entry per probe, e.g.
  "50pA ref"), so it can never drift from the colours on the canvas.
- Tests: the four ramp-era tests that pinned specific hues (red at 700 pA, blue floor,
  magenta above, monotonic) are rewritten to the categorical contract (distinct,
  deterministic); +2 new (neighbour hue gap >= 60, unique palette, nearest-match,
  opacity/edge in probe mode). elymaker 238; all suites green.
- Upstream: elymaker_option_b_colours.patch (vs original elymaker 0.9.1) covers
  gui/items.py, gui/canvas.py, gui/lamella_dialog.py, gui/main_window.py, probes.py,
  lamella.py -- i.e. everything since 0.9.1: probe fix, per-stage overlaps, optional
  stages, default colour mode, and option B.

New in pyFIB v0.30.0 (ELYMaker pattern designer: optional stages, colour default, overlap fix):
- Lamella dialog: an "On" checkbox per stage. Fine 1, Fine 2 and Polish can be
  switched off (left out of the pattern); Rough 1 / Rough 2 are locked on. Disabled
  stages are omitted from the built lamella, and the remaining stages still nest
  correctly (the innermost enabled stage defines the final thickness).
- BUG FIX: the dialog rebuilt each stage as LamellaStage(name, probe, height) and so
  DROPPED the per-stage overlap_um added in v0.26.3 -- GUI-authored lamellae were
  silently falling back to the global 0.15 um instead of the measured rough 0.35 /
  fine 0.15. The dialog now carries the per-stage overlap through.
- Colour by probe current is now the DEFAULT (was colour by depth), in both the item
  colour mode and the View menu check state.
- Colour PROPOSAL (not yet applied -- see elymaker_probe_colour_options.png): the
  current scheme is a continuous log-hue ramp at fill alpha 110. Two structural
  problems: adjacent stages get ADJACENT hues (100 pA green vs 50 pA cyan-green;
  1.5 nA vs 700 pA both red), and semi-transparent fills BLEND where fine boxes lie
  on top of coarse ones. Recommended fix: a categorical, colourblind-safe palette
  (Okabe-Ito) assigned per probe, fill alpha ~170, opaque 2 px edge.
- Tests: elymaker +2 (dialog checkboxes + overlap preserved; default colour mode).
  elymaker 236; all suites green.

New in pyFIB v0.29.1 (FIX: refine stage adapter crashed; silent-tilt hazard closed):
- BUG: `refine --calibrate-stage` crashed with "'StagePosition' object has no
  attribute 'r_deg'". The refine adapter's move_stage_xy used p.r_deg / p.m_m /
  t_deg, but StagePosition's fields are rot_deg / master_z_m / tilt_deg. The real
  adapter's move_stage_xy had NEVER been exercised (stage correction was disabled;
  the sim overrides it), so the probe was its first real caller. The stage did NOT
  move: the AttributeError fired while building the move_stage arguments, before the
  call -- although the log line printed the intended move first, which was
  misleading. The finally-restore then hit the same bug.
- The HAZARD in it: `getattr(p, "t_deg", 0.0)` silently defaulted TILT to 0.0
  (wrong field name, defaulted instead of raising). Had r_deg not crashed first, a
  tilted stage would have been COMMANDED to tilt 0 -- violating the "hold other
  axes" guarantee. Fixed: held axes now come strictly from the real read-back with
  NO defaults; a missing field raises rather than substituting zero.
- move_stage_xy logs only after the full command is built (never claims a move that
  did not happen). calibrate_stage's restore is now robust: a restore failure logs
  *** RESTORE FAILED ... return MANUALLY to X=..., Y=... *** instead of raising a
  second silent exception that could leave the stage displaced.
- CLI: --calibrate-stage no longer demands --reference (it grabs its own baseline).
  Correct real-mode invocation:
      python -m smartfib_executor.refine --calibrate-stage --magnification 2000 \
          --stage-test-step-um 20 --save-calibration
  (the earlier note that said --fib-mag was wrong; the flag is --magnification.)
- Tests: test_calibrate_stage now 7 -- a REAL-adapter move_stage_xy test on a TILTED
  stage asserting X/Y move and tilt/rot/master-z are held (proven to reproduce the
  reported AttributeError against the old code), and a restore-failure test asserting
  the loud manual-restore message. All suites green; gisdep 236 + elymaker 234.
- recall_position, setup_move, prep_cryo, coincidence, smartsem_com: UNCHANGED.

New in pyFIB v0.29.0 (refine stage correction, Stage 1: MEASURE the stage sign first):
- CONTEXT: refine's stage-correction path exists but is disabled because its
  stage<->image signs (geometry.STAGE_SIGN_X/Y = -1, rotation 0) are PLACEHOLDERS,
  marked UNVERIFIED -- a wrong sign in a feedback loop doubles the error. It also had
  NO magnitude cap. Before refine ever drives the stage, the sign must be measured on
  the instrument, as the beam-shift sign was.
- NEW refine/calibrate_stage.py + `refine --calibrate-stage`: steps the stage a known
  amount on X then Y (X/Y ONLY; other axes held by the adapter), waits for arrival,
  reads the ACHIEVED step from the stage read-back (mechanical stages have error --
  we measure what happened, not what was asked), cross-correlates against a baseline,
  and reports per axis: sign, achieved step, measured pixel size vs nominal (a scale
  cross-check), cross-axis motion (rotation hint) and SNR. Then restores the start
  position (always, incl. on error). Result: STAGE_SIGN_X/Y = -response, mirroring
  BEAM_SIGN = -R. --save-calibration writes refine_stage_calibration.json.
      python -m smartfib_executor.refine --simulate --calibrate-stage       # offline
      python -m smartfib_executor.refine --calibrate-stage --dry-run        # plan only
      python -m smartfib_executor.refine --calibrate-stage --fib-mag 2000 \
          --stage-test-step-um 20 --save-calibration                        # real (confirms)
  Default step 20 um (operator: start there, go higher if noisy; the step is a flag).
- REFINE-ONLY STAGE LIMITS, introduced here: REFINE_STAGE_MAX_M = 100 um per axis,
  X/Y only. The probe REFUSES a test step above the cap (no move). These limits apply
  to refine's correction ONLY. recall_position, setup_move, prep_cryo, coincidence,
  goto-position and smartsem_com are byte-for-byte UNCHANGED (verified against the
  previous release).
- Tests (test_calibrate_stage, 5): recovers the sim's convention and restores the
  stage; MEASURES a flipped stage as (+1,+1) (proves it measures, not assumes); uses the
  ACHIEVED step under an 85%-travel mechanical error; refuses a step above the cap
  without moving; restores the stage even when a grab fails. refine suite 35+5. All
  suites green; gisdep 236 + elymaker 234 intact.
- NOT YET (Stage 2/3, after the sign is measured live): bake the measured sign into
  geometry as a MEASURED constant with a regression test; the capped, X/Y-only,
  refuse-above-100um stage correction that hands back to beam shift for the fine
  residual; standalone `refine --enable-stage` on the instrument; only then an opt-in
  (default OFF) --refine-enable-stage on mill-positions.

New in pyFIB v0.28.2 (FIX: milling timed out at the first position -- stage read returned zeros):
- BUG (introduced in v0.27.0): "Timeout waiting for stage arrival (x_m off by 0.0485;
  DP_STAGE_IS=0)" against a target X of 0.04849654 m -- i.e. the read-back X was
  ~0.0. get_stage_position() called GetStagePosition(), DISCARDED the return value,
  and read back the passed VARIANTs' .value, which stay at their initial 0.0 because
  win32com returns the [out] values in the return tuple and does not mutate the
  VARIANTs -- the same stale-VARIANT bug as get_float/get_digital/get_str. It returned
  six zeros. v0.26.1 was unaffected only because its idle-only recall wait never read
  the position; the v0.27.0 arrival wait was the first mill-path caller and hit it at
  once. (The stage HAD moved; the check just could not see where it was. NOT a
  tolerance issue: the miss equalled the whole target coordinate.)
- FIX: get_stage_position() now reads the six AP_STAGE_AT_* values via get_float --
  the path PROVEN live (prep_cryo and setup_move confirmed real arrival coordinates
  with it). GetStagePosition is kept only as a fallback and now captures its RETURNED
  tuple. Also included: the fiducial-before-capture flow (Capture-tab "Mill fiducial
  here" at the capture mag via `run --fib-mag`, and capture-position --fiducial-side)
  -- refine registers against the reference captured at capture time, so a fiducial
  must exist BEFORE the reference is taken.
- Regression tests model the exact instrument behaviour (GetStagePosition leaving the
  VARIANTs stale while AP_STAGE_AT_* hold the real position): get_stage_position
  returns the real coordinates, the arrival wait confirms at the real position, and
  the fallback captures the returned tuple. The old read is shown to yield 0.0 ->
  "x off by 0.04849654" -- byte-identical to the reported error. All suites green;
  gisdep 236 + elymaker 234 intact.

New in pyFIB v0.28.1 (prototype fiducial patterns shipped + FOV fit check):
- The operator's prototype fiducials are now INCLUDED as
  smartfib_executor/assets/fiducial_left.ely and fiducial_right.ely (to be swapped
  later). Each is a 14x14 um square, 30kV:300pA, Vitriuos_Ice.epm, dose 5, z 5 um:
  left spans x = -28.1 .. -14.1 um, right spans x = +14.1 .. +28.1 um -- i.e. each
  file encodes its own offset, exactly the convention v0.28.0 was built on. No code
  change was needed to use them.
- FOV FIT CHECK: the prototypes reach |x| = 28.1 um, while the FIB FOV half-width at
  2000x is only 28.6 um (+0.5 um margin) -- at 2200x and above they fall OUTSIDE the
  field and would be clipped or not milled. fiducial.fiducial_fit(side, mag) reads
  the .ely extent (via the vendored elymaker reader) and compares it to the FOV at the
  position's recorded magnification. The Positions-tab "Mill fiducial" now WARNS and
  asks for confirmation when the fiducial will not fit at that position's mag;
  declining does not run.
- The mill-positions dry-run summary now states the forced-pattern override and the
  fiducial_side that will be recorded.
- Tests: test_fiducial now 5 (prototypes present and offset by side; fit flags 2500x,
  passes 2000x/1500x). GUI verified: no warning at 2000x, warning at 2500x, declined
  = not run. All suites green; gisdep 236 + elymaker 234 intact.

New in pyFIB v0.28.0 (roadmap #5: fiducial milling, left or right of the image):
- POSITIONS tab gains a "Fiducial" control: pick a side (left / right), optionally
  autofocus and/or refine first, and "Mill fiducial" at the selected position.
  ARM-gated behind a confirmation; unarmed = the mill CLI's dry-run.
- It reuses the ENTIRE validated mill path (recall with the arrival wait -> mag ->
  [autofocus] -> [refine] -> interlocks -> mill) via mill-positions with two new
  flags: --force-pattern mills the GIVEN .ely even if the position has an authored
  lamella pattern linked (a fiducial must never be replaced by the lamella pattern);
  --record-fiducial {left,right} records fiducial_side on the position and saves.
- FIDUCIAL FILES: one .ely per side, expected at
      smartfib_executor/assets/fiducial_left.ely
      smartfib_executor/assets/fiducial_right.ely
  Each is authored on the FOV-centred convention (+X right) so the file ITSELF
  encodes the left/right offset -- pyFIB picks the file, it does not compute offsets.
  The files are NOT shipped yet (operator will supply them); the UI/CLI refuses with a
  message naming the expected path if a side's file is missing.
- PositionRecord gains fiducial_side (None | "left" | "right"), shown in a new "Fid"
  column, so drift correction (#6) knows where a fiducial exists. v2-additive; old
  files load with None.
- Tests: test_fiducial (3) -- force-pattern bypasses an authored lamella, side->file
  mapping (+ bad side rejected), fiducial_side persists; GUI flow verified offscreen
  (argv, autofocus/refine flags, ARM, and the missing-file refusal). All suites green;
  gisdep 236 + elymaker 234 intact.

New in pyFIB v0.27.0 (SAFETY: core milling recall waits for ARRIVAL; failed axis sets warn):
- #1 ROOT FIX at the source: SmartSEM gains wait_stage_arrival(x,y,z,t,r,m) -- waits
  until DP_STAGE_IS is Idle AND the read-back position is within tolerance of the
  commanded target (0.3 mm / 0.05 deg). MoveStage is non-blocking and DP_STAGE_IS reads
  Idle *before* the stage starts moving, so the old idle-only wait_stage_idle() could
  return mid-travel. This was fixed piecemeal in prep_cryo / setup_move / coincidence
  but never in the primitive -- and recall_position (the move between EVERY position in
  a mill batch) still used the idle-only wait, so with autofocus and refine off,
  milling could start while the stage was still travelling. recall_position (both
  ap_goto and movestage) and refine's X/Y stage correction now wait for arrival.
  wait_stage_idle() is kept (with a caution) for callers that only need "not busy".
- #3 WARNING: in ap_goto recall, a FAILED optional axis set (AP_STAGE_GOTO_R/M/T) was
  silently swallowed -- a lamella could be milled at the wrong rotation/tilt with no
  message. It now prints a loud *** WARNING *** naming the axis and value, and that
  axis is excluded from the arrival check (it was never commanded), so the recall
  does not time out purely because of a known-unset optional axis. A GENUINE
  non-arrival on a commanded axis raises a timeout that names the axis still off.
- refine's stage correction no longer swallows a wait timeout silently; it warns.
- The shared PyfibMockSEM gains wait_stage_idle / wait_stage_arrival so forks can
  drive recall_position against it.
- Regression tests (test_recall_arrival, 3) use a stage that reads Idle before moving
  and lags the command: recall must not return until arrival (PROVEN to fail under the
  old idle-only wait), a failed R set warns and recall still completes, and genuine
  non-arrival times out naming the axis. All suites green; gisdep 236 + elymaker 234.

New in pyFIB v0.26.5 (Mill tab: explicit All/None selection, no inverting toggles):
- BUG: "Toggle all" on the Mill tab unchecked Mill and checked Autofocus/Refine. It
  was a literal per-cell INVERT, and the columns start in different states (Mill on,
  AF/Refine off), so inverting everything produced exactly that.
- FIX: the three ambiguous "Toggle ..." buttons are replaced by explicit per-column
  All / None buttons (Mill, Autofocus, Refine) that set a FIXED state regardless of
  what was there. No combined "select everything" button (per-column only).
- Autofocus / Refine "All" only checks rows that are selected for milling, because
  autofocus/refine at an un-milled site never runs (the mill argv builder drops
  them) -- so a checkbox there would look on while doing nothing. "None" clears the
  column regardless.
- Regression test: from the defaults, All on every column yields all-checked (the
  exact broken case); None clears; AF All respects the Mill selection. gui_qt smoke
  now 17. All suites green; gisdep 236 + elymaker 234 intact.

New in pyFIB v0.26.4 (FIX: FIB mag not restored before autofocus after milling):
- BUG: after milling position 1 and moving to position 2, the FIB was at a LOWER
  (post-milling) magnification and the autofocus imaged at that wrong mag; refine
  looked right. Root cause was ORDER: the autofocus source's prepare() set
  AP_FIB_MAGNIFICATION before the column was in FIB imaging mode (the mode switch
  happened later, in grab()). SmartFIB keeps separate imaging/milling mags, so a set
  issued in post-milling mode does not stick, and the imaging mag came up at the
  lowered value. It was also set once and never READ BACK, so nothing caught it. The
  pixel-size read in prepare() was therefore also taken at the wrong mag.
- FIX (autofocus AND refine): ensure FIB imaging mode FIRST, then set the mag and
  READ IT BACK to verify (retry once), and do this before EVERY image, not just at
  prepare -- milling / mode switches can change the mag between frames. A confirmed
  mismatch after retry refuses to grab ("refusing to grab at the wrong mag"); if the
  readback is unavailable (some mocks / instruments) it sets and proceeds with a note,
  matching the pre-fix behaviour rather than blocking.
- Regression tests reproduce the observed failure with a mock whose mag was lowered by
  milling and only accepts a set once in imaging mode: autofocus prepare restores it,
  grab re-asserts it, a stuck mag refuses; refine likewise. autofocus suite +3,
  refine 35. All suites green; gisdep 236 + elymaker 234 intact.
- Also silenced invalid-escape SyntaxWarnings in this changelog (backslashes in the
  D:\\FIB Recipes path).

New in pyFIB v0.26.3 (elymaker: CORRECT 50 pA probe + per-stage overlaps -- supersedes v0.26.2):
- v0.26.2's "recipe-only" 50 pA change was WRONG and is reverted. A SmartFIB-corrected
  before/after (Bad50pAprobe.ely -> Corrected.ely) is the ground truth: the 50 pA probe
  IS written inline, and SmartFIB names it "30kV:50pA ref" (trailing " ref") with a
  45 nm spot (4.5e-8 m) -- not "30kV:50pA" / 50 nm (elymaker's estimate).
  * probes.py: canonical entry "30kV:50pA ref" @ 4.5e-8; the plain key "30kV:50pA" is
    kept as an alias that resolves to the same Probe (so existing references still
    work and the written .ely carries the real name); the alias is excluded from the
    by-current listing so the dropdown does not show it twice.
  * writer.py: recipe-skip reverted (inline PROBE written for every probe again).
- PER-STAGE OVERLAPS (measured from Corrected.ely): the lamella gap at every stage was
  unchanged; the operator extended the ROUGH stages outward so they overlap their
  coarser neighbour more -- 1.5nA->700pA 0.15->0.38, 700pA->300pA 0.15->0.35 -- while
  the FINE transitions (300->100, 100->50) stayed at 0.15. elymaker had one global
  overlap_um=0.15. LamellaStage now has overlap_um (its overlap INTO its outer/coarser
  neighbour; None -> the spec's global), and the defaults are rough 0.35 / fine 0.15.
  The placement loop applies the FINER stage's overlap to each transition. Default
  Fine 2 stage now references "30kV:50pA ref".
- Verified: a default lamella reproduces the corrected overlaps exactly (700->300 =
  0.350; 300->100, 100->50, 50->20 = 0.150) and writes "30kV:50pA ref".
- Regression (elymaker_tests/test_probe_50pa_and_overlaps.py, 6) is locked against
  Corrected.ely itself; the earlier wrong test/fixture is removed. elymaker 234;
  gisdep 236; all pyFIB suites green.
- The upstream patch is regenerated (elymaker_probe_overlap_fix.patch) and supersedes
  the earlier elymaker_50pA_probe_fix.patch, which must NOT be applied.

New in pyFIB v0.26.2 (elymaker FIX: the 50 pA probe is recipe-defined, not "30kV:50pA"):
- A real instrument .ely (Krystofiak_lamella_fine2.ely, the lamella "Fine 2" stage)
  shows the 50 pA probe is NOT identified by an inline PROBE name at all: each shape
  carries only <EXPOSURE version="2.1" file="D:\\FIB Recipes\\Vitriuos_Ice.epm"> with
  no <PROBE> child. The .epm recipe IS the probe. elymaker had been writing an
  INVENTED inline <PROBE name="30kV:50pA"/> (its 50 pA entry was estimated -- no
  example file had one), which SmartFIB does not recognise -> wrong probe.
- FIX (applied to the vendored elymaker; identical patch for upstream):
  * probes.py: 30kV:50pA is now type "recipe" (recipe-defined). Other probes, which
    were seeded verbatim from real files, are unchanged.
  * writer.py: for a type-"recipe" probe the EXPOSURE is written with the recipe file
    only and the inline <PROBE> is omitted -- byte-for-byte the form of the real file.
  elymaker's reader/writer already round-tripped the real file correctly (no probe
  injected); only NEW 50 pA authoring was wrong.
- Regression test (elymaker_tests/test_probe_50pa_recipe_only.py) locks this against
  the real fine2 file: authoring 50 pA emits recipe-only, 700 pA still inline, and the
  real file round-trips unchanged. elymaker now 232 tests; all suites green.
- NOTE for upstream elymaker: apply the same two edits (probes.py _RECIPE_DEFINED +
  writer.py skip PROBE for type "recipe"). The remaining ESTIMATED spot diameters
  (3nA, 7nA, 30nA) are display-only and unaffected; whether any OTHER probe is also
  recipe-defined on this instrument can be confirmed the same way (a real .ely with no
  <PROBE> child).

New in pyFIB v0.26.1 (per-position pattern: authored overrides the standard):
- Milling now picks the .ely PER POSITION: the standard pattern (the mill-positions
  ely argument / Mill-tab picker) is the DEFAULT, but if a position has an authored
  pattern linked (rec.ely_pattern) and the file exists, pyFIB mills THAT instead. The
  log states which was used per position; a linked-but-missing authored file WARNS and
  falls back to the standard pattern.
- Resolution is a small tested helper (_resolve_position_ely): authored / standard /
  authored_missing. Mill-tab label clarified to "Standard pattern (.ely)" with a hint
  that authored patterns override per position.
- Tests: test_ely_link now 7 (adds the override matrix). All pyFIB suites green;
  gisdep 236 + elymaker 228 intact.

New in pyFIB v0.26.0 (ELYMaker integration -- Phase 2: per-position authoring round trip):
- NEW smartfib_executor/ely_link.py -- the integration seam (like gisdep_adapter):
  builds a handoff job from a v2 position, picks up ready-markers, verifies hashes.
  Core does not import elymaker except through here.
- AUTHOR FROM POSITIONS: the Positions tab gains "Author pattern..." -- select a
  position, and pyFIB builds a HandoffJob from its v2 geometry (image, fib_pixel_size_m,
  fov_width_m) and opens the ELYMaker tab preloaded over that image at the right pixel
  size, Save -> <session>/patterns/<name>.ely, target_site = the position. Refuses on a
  v1 record that lacks fib_pixel_size_m (asks you to re-capture).
- PATTERN OUTPUT: authored .ely (+ ready-marker) land in <session>/patterns/ beside the
  session's positions.json.
- EXPLICIT PICKUP (option b -- no filesystem watcher): "Refresh patterns" (and on
  loading Positions) reads the *.ely.ready.json markers, matches target_site to a
  position, verifies the content hash, sets ely_pattern, and saves. Reports linked /
  stale (edited since marked ready) / unmatched.
- MILL-TIME WARN: before milling a position, if its linked .ely no longer matches its
  ready-marker hash, pyFIB WARNS ("...has CHANGED since it was marked ready...") and
  proceeds -- consistent with the scan-rotation/beam-shift checks.
- Tests: new test_ely_link (6) -- build job, refuse v1, link from marker, stale
  detection, verify-without-marker, unmatched marker; plus a GUI check that Author
  pattern wires Positions -> ELYMaker. All pyFIB suites green; gisdep 236 + elymaker
  228 intact.
- The ELYMaker tab remains available for free-form authoring independent of the
  per-position flow.

New in pyFIB v0.25.0 (ELYMaker integration -- Phase 1: vendor + embedded tab):
- elymaker (v0.9.1) is VENDORED VERBATIM at the distribution root (elymaker/),
  alongside smartfib_executor/ and gisdep/. Its 228 tests pass unmodified inside
  pyFIB (pytest elymaker_tests). elymaker is a PURE authoring tool -- it does not touch
  the instrument, so no bridge/adapter is needed (unlike gisdep).
- NEW "ELYMaker" tab in the left nav (after GISDEP, before Capture): embeds elymaker's
  ACTUAL MainWindow (canvas, docks, tools) -- the full authoring UI, built lazily on
  first open. The tab exposes open_handoff(...) so pyFIB can preload a position's FIB
  image + geometry, and the window's send_to_smartfib()/ready-marker path is available.
- The handoff contract is already implemented on elymaker's side: HandoffJob
  (elymaker.handoff.v1) maps directly onto pyFIB's v2 position fields (image,
  fib_pixel_size_m, fov_width_m, image dims, site); write_ready_marker/read_ready_marker
  (elymaker.ready.v1) publish an atomic <name>.ely.ready.json with a sha-256 content
  hash and target_site -- "ready", NOT "mill".
- Per-position handoff WIRING (build a job from a selected position, pick up the
  ready-marker to set the position's ely_pattern, and the mill-time hash check) is
  intentionally NOT wired yet -- it touches the milling workflow and depends on
  operator decisions (see the integration questions).
- Tests: gui_qt smoke asserts the elymaker tab embeds elymaker's real MainWindow with
  its handoff hooks. All pyFIB suites green; gisdep 236 + elymaker 228 intact.

New in pyFIB v0.24.2 (audit: stale-VARIANT bug swept from the rest of the codebase):
- Audited every COM read. VARIANT usage is confined to smartsem_com.py, and all forks
  read through SmartSEM's get_float/get_digital/get_str/get_stage_position -- no raw
  Get() calls anywhere else. Findings:
  * get_float / get_digital / get_str: fixed in v0.24.0-.1 (capture the RETURN value).
  * get_stage_position: uses the v.value read-back too, BUT is confirmed working on the
    instrument (GetStagePosition's six [out] VARIANTs are mutated, unlike Get()); left
    untouched deliberately -- it is a validated, safety-critical read.
  * _get_last_error: had the same latent pattern (affecting only error-detail strings);
    hardened to handle both COM conventions so failures aren't reported blank.
- No behaviour change to the working paths. All suites green; gisdep's 236 intact.

New in pyFIB v0.24.1 (FIX: same stale-VARIANT read bug in get_float / get_str):
- Coincidence refused with "DP_AUTO_FUNCTION stayed Idle 30s; the auto routine never
  started" even though coarse autofocus ran. Cause: the SAME bug fixed in get_digital
  (v0.24.0) also lived in get_float and get_str -- their VT_R4/VT_BSTR fallback read
  back the passed VARIANT's stale .value instead of the value RETURNED by the call.
  Coincidence reads DP_AUTO_FUNCTION via get_float, so it always saw 0 = Idle.
- FIX: unified all number reads through one helper (_get_r4) that primes VT_R4 and
  takes the RETURNED value (like gisdep's proven read); get_float falls back to it and
  get_digital uses it directly. get_str's fallback likewise now takes the returned
  string. So every DP read -- via get_float, get_digital, or get_str -- returns the
  real state number/name, not a stale 0/"".
- Tests: new test_smartsem_reads models the real win32com semantics (single-arg Get ->
  state NAME; primed Get -> number in the RETURN tuple, VARIANT left stale) and proves
  get_float/get_digital/get_str now return the true value. All suites green; gisdep 236
  intact.

New in pyFIB v0.24.0 (real fix for GIS needle reads + single ARM):
- ROOT-CAUSE FIX: GIS needles still read as transit in v0.23.1 because that
  get_digital forced VT_R4 but then read back the INPUT VARIANT's .value (a stale
  0.0). win32com returns the [out] param in the RETURN tuple, not by mutating the
  passed VARIANT. get_digital now captures the returned value -- `Get(param, 0.0)` ->
  use the result -- exactly like gisdep's proven ComSmartSEM.get_digital and pyFIB's
  own working get_str path. With DP_GIS_LOCATION = 1 in the API, the tab will now read
  Parked. (Confirmed by the operator that the API holds 1; WD and stage already read
  correctly, which is why only the digital read was implicated.)
- SINGLE ARM: the embedded GISDEP tab no longer has its own ARM button. pyFIB's global
  ARM toggle (top bar) now governs the gisdep tab too -- gisdep's arm state is synced
  from it and its redundant ARM button is hidden (its status badge still shows the
  state). One ARM control for the whole app.
- All pyFIB suites green; gisdep's 236 intact.

New in pyFIB v0.23.1 (FIX: GIS needles read as "transit" through the bridge):
- The GISDEP tab reported both GIS1 and GIS2 needles in transit when they were parked.
  Cause: the bridge read digital params via pyFIB's get_float, whose "try single-arg
  Get first, return if numeric" path is unreliable for DIGITAL parameters -- the
  SmartSEM API defaults a DP with no variant priming to the state NAME, and for the GIS
  needle DPs get_float could hand back a numeric 0, which the bridge reported as
  needle state 0 = transit.
- FIX: pyFIB's SmartSEM.get_digital now forces VT_R4 (primes the value with a float),
  exactly like gisdep's own ComSmartSEM.get_digital -- the proven read path that
  returns the state NUMBER. The bridge adapter now reads all digitals through
  SmartSEM.get_digital, not get_float. pyFIB's own forks are unchanged (they were not
  affected).
- Regression test reproduces the split: a fake whose get_float returns the buggy 0 for
  the needle DP but whose get_digital returns the true 1 (Parked) -- the bridge now
  reports Parked. All pyFIB suites green; gisdep's 236 intact.

New in pyFIB v0.23.0 (gisdep's real UI restored as its own tab):
- The full gisdep GUI is back -- embedded as a dedicated "GISDEP" tab in the left nav,
  BETWEEN Setup and Capture. It is gisdep's actual GisdepWindow, unchanged: the
  chamber-view line-art graphics (cryo_normal / insert / on / start / blocked), the
  LIVE STATE grid, the header ARM / STOP controls, and the full-run / cryo / workflow /
  deposition cards with their buttons -- exactly as before, not a stripped-down panel.
- Built lazily the first time the tab is opened (so pyFIB startup doesn't connect), and
  driven INLINE as gisdep's GUI always was. Connection: tries pyFIB's SmartSEM via the
  bridge adapter (one instrument connection); falls back to gisdep's MockSmartSEM when
  there's no instrument, so the full UI still renders for offline exploration.
- The interim 3-button GIS panel that had replaced gisdep's UI on the Setup page is
  removed (the real tab supersedes it). Setup now has: Prep CryoFIBSEM, Move to
  position, and Coincidence. The gis_run CLI entry remains for scripted use.
- Tests: gui_qt smoke now asserts the gisdep tab embeds the real GisdepWindow with its
  controls, and that the tab sits between Setup and Capture. All pyFIB suites green;
  gisdep's 236 intact.

New in pyFIB v0.22.0 (gisdep Phase 3: full run enabled + waypoint agreement locked):
- KEY FINDING: gisdep's built-in cryo waypoints are IDENTICAL to pyFIB's
  setup_positions.json (same instrument-measured CLEM_SAFE / GRID1 / GRID2). The
  Phase-2 "coordinate mismatch" worry was unfounded -- there was nothing to reconcile.
  gisdep's cryo choreography also deliberately never moves M (GIS positioning), while
  pyFIB's setup_move does (general cryo positioning): different, both validated.
- FULL RUN is now enabled in the Setup GIS panel: pick a grid (GRID1/GRID2, from
  gisdep's validated waypoints) + target WD, and Full run does gisdep's cryo
  positioning (via CLEM SAFE) -> autofocus + set WD -> deposition, all on pyFIB's
  connection via the bridge. ARM-gated behind an explicit confirmation that spells out
  the stage move + deposition. gis_run already supported full-run; the panel now
  drives it.
- REGRESSION LOCK: new test asserts gisdep's DEFAULT_WAYPOINTS == pyFIB's
  setup_positions.json (CLEM SAFE / GRID1 / GRID2) within 1 um / 0.001 deg, so the two
  coordinate sources can never silently drift apart.
- Tests: new test_waypoint_agreement (1); gui_qt smoke updated for full-run. All pyFIB
  suites green AND gisdep's 236 intact.

REMAINING gisdep-integration items (reported, not yet done):
  1. Single source of truth for waypoints: coordinates now live in BOTH gisdep/config.py
     and setup_positions.json (kept in lock-step by the new test). A future cleanup
     could derive one from the other; low priority since the test guards drift.
  2. API/mock reconciliation (the other Phase 3 track): migrate the remaining forks'
     bespoke mocks to the shared PyfibMockSEM (coincidence, setup_move, refine,
     autofocus) -- prep_cryo done. Then optionally migrate their calls to the analogue/
     digital interface, then retire gisdep's own api/com.py so there is ONE connection
     class. Pure cleanup; no new capability.
  3. Retire the standalone gisdep GUI path in favour of the in-app panel (optional).
  4. Full-run is instrument-UNTESTED end-to-end through pyFIB's bridge -- validate
     probe -> deposit -> full run on a sacrificial run before routine use.

New in pyFIB v0.21.0 (gisdep Phase 2: GIS deposition in the Setup panel):
- NEW smartfib_executor/gis_run.py runs the VENDORED gisdep workflow on pyFIB's
  connection via the bridge adapter, streaming gisdep's logs to stdout:
      python -m smartfib_executor.gis_run probe                       # read-only
      python -m smartfib_executor.gis_run deposit --port 1 --dwell 20 [--arm]
  Dry-run (read-only GIS probe + plan) unless --arm; gisdep's own stage-idle and tilt
  gates still apply underneath.
- GUI Setup page: the "Load GISDEP" placeholder is now a real GIS panel -- port +
  dwell, a read-only Probe button, and an ARM-gated + confirmed Deposit button, output
  to the Log. One app, one connection.
- SAFETY DECISION: gisdep's "full run" (which does its OWN cryo stage positioning from
  gisdep's waypoints) is deliberately NOT exposed yet -- its coordinates are not the
  instrument-validated ones in pyFIB's setup_positions.json, so driving the stage from
  them would be a collision risk. Deposit runs at the CURRENT position (position first
  with 'Move to position'). Full run comes in Phase 3, once gisdep's positioner uses
  pyFIB's setup fixtures.
- Tests: new test_gis_run (2); gui_qt smoke 16 (adds the GIS panel argv). All pyFIB
  suites green AND gisdep's 236 intact.

New in pyFIB v0.20.2 (FIX: stage moves proceeded before the stage arrived):
- Prep CryoFIBSEM aborted with "not at CLEM SAFE: X off by 1.64 mm" even though the
  microscope did reach CLEM SAFE after the script ended. Cause: MoveStage is
  NON-BLOCKING and DP_STAGE_IS can read Idle *before* the stage starts moving, so the
  per-phase wait (which only checked DP_STAGE_IS == Idle) returned mid-travel and the
  next phase / arrival check ran against a stale position.
- FIX (the working gisdep convention): each phased stage move now waits until the
  stage is Idle AND the read-back position has actually REACHED the commanded target
  (within the arrival tolerance), not just Idle. Applied in prep_cryo, setup_move, and
  the coincidence Z move (all shared the idle-only pattern).
- Regression test reproduces the exact failure: a mock stage that reports Idle for
  several polls before reflecting the move -- proven to FAIL under idle-only (same
  "CLEM SAFE arrival not confirmed" error) and PASS with the arrival wait. prep_cryo
  now 10 tests. All suites green; gisdep's 236 intact.

New in pyFIB v0.20.1 (Qt GUI is now the default):
- The GUI launcher now opens the Qt GUI by DEFAULT. The legacy Tkinter UI is still
  available via --tk. If qtpy/PyQt5 are not installed, the launcher prints a clear
  message pointing to `pip install qtpy PyQt5` or `--tk`.
      python -m pyfib ui                 # Qt GUI (default)
      python -m pyfib ui --tk            # legacy Tkinter UI
      python -m smartfib_executor.gui_qt # Qt GUI directly (equivalent)
- An explicit --positions-file preloads that session; otherwise the Qt GUI starts in a
  fresh timestamped session (Load Previous... in the top bar opens an old one).
- No engine changes. All suites green.

New in pyFIB v0.20.0 (API reconciliation -- Phase 3 start: shared interface + mock):
- We ADOPTED gisdep's cleaner SEM abstraction as pyFIB's canonical one:
  * pyFIB's own SmartSEM now IMPLEMENTS the interface -- it gained get_analogue /
    get_digital / get_digital_text / set_analogue / set_digital / initialise
    (delegating to its proven get_float / get_str / set_value). Purely additive; the
    battle-tested COM connection is unchanged and now satisfies SmartSEMInterface.
  * NEW smartfib_executor/instrument.py is the single seam: it re-exports the
    canonical SmartSEMInterface / StagePosition / SmartSEMError, and provides
    PyfibMockSEM -- ONE shared mock that EXTENDS gisdep's rich MockSmartSEM (WD<->Z
    coupling, premature-Idle autofocus, stage soft limits, GIS states) and adds the
    reads/commands pyFIB touches (AP_ACTUALKV, AP_STAGE_AT_*, DP_FROZEN, DP_SCANRATE,
    FIB/column valves, FIB probe, grab_bmp), with instant stage moves and a
    pyFIB-legacy compat surface (get_float/get_str/set_value) so forks can adopt it
    before their calls are migrated.
- TEMPLATE MIGRATION: prep_cryo's bespoke ~50-line SimulatedPrepSem is DELETED and
  replaced by a thin subclass of the shared PyfibMockSEM (just the dict/list views its
  tests read). prep_cryo's 9 tests + --simulate CLI pass unchanged. This is the pattern
  for collapsing pyFIB's ~6 hand-rolled mocks into the one shared mock, one fork at a
  time, tests green throughout.
  (I migrated prep_cryo first, not coincidence: coincidence turned out higher-risk --
  its autofocus timing model needs a careful test remap -- so it stays untouched/green
  and is the next migration.)
- Tests: new test_instrument (6). Nothing broke: ALL pyFIB suites green AND gisdep's
  236 still pass unmodified.
- Next: migrate the remaining forks' mocks (coincidence, setup_move, refine, autofocus)
  to PyfibMockSEM one at a time; then optionally migrate their CALLS to the analogue/
  digital interface; then retire gisdep's own com.py in favour of pyFIB's SmartSEM.

New in pyFIB v0.19.0 (gisdep integration -- Phase 1: vendor + bridge):
- The full gisdep package (v0.16.1) is VENDORED VERBATIM at the distribution root
  (gisdep/), alongside smartfib_executor/. Its imports (from gisdep.x), its GIS
  deposition subsystem, and its whole test suite are UNCHANGED -- gisdep's own 236
  tests pass unmodified inside the pyFIB tree (pytest gisdep_tests). This brings the
  one capability pyFIB lacked: GIS deposition.
- ONE new bridge: smartfib_executor/gisdep_adapter.py presents pyFIB's SmartSEM AS a
  gisdep SmartSEMInterface (get_analogue<->get_float, get_digital<->int(get_float),
  get_digital_text<->get_str, set_analogue/set_digital<->set_value, execute,
  move_stage, get_stage_position), and TRANSLATES pyFIB's SmartSEMError into gisdep's
  so gisdep's own error handling keeps working. So gisdep code runs on pyFIB's single
  COM connection without being modified. Use bridged_api(sem).
- Isolation preserved: pyFIB core does NOT import gisdep (only the opt-in adapter
  does). Neither side is broken -- all pyFIB suites green AND gisdep's 236 green.
- Tests: new test_gisdep_bridge (7) -- call mapping, error translation, StagePosition
  field rename, connect-mode, and a real gisdep routine (GISProbe) accepting the
  adapter.
- Phase 1 deliberately does NOT wire the GUI or de-duplicate the overlapping modules
  (clock, autofocus, stage moves appear in both for now, each inside its own package
  namespace). Next: Phase 2 (Setup-page GIS deposition panel driving the vendored gis/
  via the adapter) and Phase 3 (reconcile the duplicates one at a time).
- NOTE: gisdep is vendored as its own top-level package; run its tests with
  "pytest gisdep_tests". pytest is only needed for gisdep's suite, not pyFIB's.

New in pyFIB v0.18.0 (Setup move-to-position via CLEM SAFE):
- SHIPPED FIXTURES: smartfib_executor/assets/setup_positions.json holds the six setup
  positions (CLEM SAFE, Cryo Insertion, Cryo Removal, Grid1 Milling, Grid1/Grid2 GIS
  Normal), values in metres (from the mm coordinate files). Insertion before Removal.
- NEW isolated module smartfib_executor/setup_move: move the stage to a setup fixture
  ALWAYS VIA CLEM SAFE, with the choreography Z/XYTR/M to CLEM SAFE, then M/XYTR/Z to
  the destination. Each waypoint is CONFIRMED on arrival (axes read back within 0.3 mm
  on X/Y/Z/M and 0.05 deg on T/R) before proceeding -- not just "idle". The CLEM SAFE
  pass is SKIPPED only when already within 0.3 mm of CLEM SAFE on X/Y/Z AND exact on
  T/R/M. Dry-run unless --arm.
      python -m smartfib_executor.setup_move --list
      python -m smartfib_executor.setup_move "Cryo Insertion"          # dry-run
      python -m smartfib_executor.setup_move "Cryo Insertion" --arm     # execute
  This is DELIBERATELY SEPARATE from milling recall (goto-position), which does NOT
  detour via CLEM SAFE (those recalls do not change tilt/Z dangerously).
- prep_cryo now reads CLEM SAFE + Cryo Insertion from the shipped setup_positions.json
  by default, and CONFIRMS arrival at CLEM SAFE (which is T=0 -- the state prep_cryo
  needs) and at Cryo Insertion before proceeding; it aborts if either is not confirmed.
- GUI Setup page: "Move to position" now lists the six fixtures and runs setup_move
  (via CLEM SAFE, confirmed); the goto-position options were removed. "Prep CryoFIBSEM"
  uses the shipped fixtures (no session file needed). Both ARM-gated + confirmed.
- Tests: setup_move 9 (axis order, skip-near-CLEM incl. exact-T/R/M, arrival-not-
  confirmed refuses, dest==CLEM SAFE, dry-run moves nothing); prep_cryo 9; gui_qt 15.
  All suites green. setup_move isolated: core does not import it.
- INSTRUMENT-UNTESTED end-to-end (dry-run first). Confirm the fixture coordinates and
  the via-CLEM choreography on a dry-run before arming.

New in pyFIB v0.17.2 (Prep CryoFIBSEM: operator-verified mnemonics + valve fix):
- All Prep CryoFIBSEM mnemonics are now CONFIRMED on the instrument (operator-tested):
  CMD_EHT_OFF, AP_ACTUALKV, CMD_CLOSE_COL_CHAMBER_VALVE + DP_COLUMN_CHAMBER_VALVE,
  DP_FIB_GUN_VALVE + CMD_FIB_GUN_VALVE_CLOSE, AP_MANUALKV, AP_IPROBE, DP_FIB_PROBE.
- IMPORTANT valve-logic fix: valves have a TRANSIT/unknown state (like the GIS valves:
  0 closed, 1 open, e.g. 3 transit). The FIB gun valve is now closed UNLESS the read is
  confirmed 0 (closed) -- so OPEN or TRANSIT both get closed. (Previously "not open"
  was treated as closed, which could skip a mid-transit valve.) Uses the confirmed
  DP_FIB_GUN_VALVE read; CMD_FIB_GUN_VALVE_CLOSE closes (CMD_FIB_GUN_VALVE_OPEN, which
  OPENS, is never used).
- Column chamber valve state is now read back after closing and reported.
- AP_MANUALKV = 2000 V (Zeiss scientific form 2.00e+03) and AP_IPROBE = 20e-12 (the
  number; it also has a "20 pA" text form) -- both confirmed correct.
- Sim now models valve states as ints incl. transit. Tests: prep_cryo 9 (added: a
  transit valve state gets closed). All suites green.

New in pyFIB v0.17.1 (Qt GUI: bigger + per-session directories):
- BIGGER UI. A new theme.scale_up() bumps the application font (11pt) and appends an
  override stylesheet for larger controls (min-heights, padding, prominent buttons),
  ON TOP of the shared fib_dark_theme (which is left untouched, so elymaker/gisdep are
  unaffected). Window 1360x900, wider nav, bigger titles/section headers, taller table
  rows. Much more usable.
- PER-SESSION positions directory, matching the old convention:
      <project_root>/positions/session_<YYYYMMDD_HHMMSS>/positions.json
      .../images/<name>.bmp
  * On launch pyFIB starts in a FRESH session (unique timestamped folder; created on
    the first capture, not before).
  * Top bar gains "New Session" (fresh folder) and "Load Previous…" (open an old
    positions.json -- still fully supported), with the session folder name shown.
  * Capture now defaults its image dir to <session>/images, so BMP grabs group with
    the session's positions.json automatically.
  * Starting a new session clears the stale table/dropdown data.
- Tests: gui_qt smoke now 15 (adds session-path convention, fresh-session-on-startup +
  New Session, capture image-dir defaults into the session). Skips cleanly without Qt.
- Engine/CLI untouched; tkinter GUI unchanged. All suites green.

New in pyFIB v0.17.0 (Prep CryoFIBSEM):
- NEW isolated module smartfib_executor/prep_cryo: readies the FIB for sample
  insertion. DRY-RUN by default; --arm to execute; --simulate for offline demo.
      python -m smartfib_executor.prep_cryo --positions-file positions.json          # dry-run
      python -m smartfib_executor.prep_cryo --positions-file positions.json --arm     # execute
  Sequence, in order:
    1. SEM EHT off (CMD_EHT_OFF) -> WAIT until AP_ACTUALKV ~ 0 -> close column chamber
       valve (CMD_CLOSE_COL_CHAMBER_VALVE). If EHT never confirms off, ABORTS before
       touching valves or the stage.
    2. Close the FIB gun valve IF open (CMD_FIB_GUN_VALVE_CLOSE); the FIB gun is left
       ON. Valve OPEN-state read is unconfirmed -> if unreadable, close is issued
       anyway (harmless if already closed).
    3. Set SEM accelerating voltage 2.00 keV (AP_MANUALKV=2000) and probe current
       20 pA (AP_IPROBE=2e-11).
    4. Stage: move to "Cryo Insertion" VIA "CLEM SAFE" with the required axis order --
       to CLEM SAFE: Z, then X/Y/R/T, then M ; to Cryo Insertion: M, then X/Y/R/T,
       then Z. M moves ARE allowed here. Each phase waits for the stage to go idle.
    5. Read and report the FIB probe (DP_FIB_PROBE + AP_FIB_PROBE_CURRENT).
  Requires the two named waypoints in the positions file; refuses if either is
  missing.
- GUI: the Setup page's "Prep CryoFIBSEM" button now runs this module -- unarmed =
  dry-run, armed = --arm behind a confirmation dialog that spells out the actions.
- Tests: prep_cryo 8 -- dry-run touches nothing; armed command order + states; ends
  at Cryo Insertion with M moved; the stage AXIS CHOREOGRAPHY (Z-first to CLEM SAFE,
  M-first/Z-last to Cryo) verified move-by-move; EHT-off failure aborts before
  valves/stage; missing waypoint refuses; FIB-valve already-closed / unreadable paths.
- MNEMONICS FLAGGED / INSTRUMENT-UNTESTED: CMD_EHT_OFF, AP_ACTUALKV wait, column
  valve close + state, FIB gun valve close (+ its unconfirmed OPEN-state read),
  AP_MANUALKV / AP_IPROBE, and the stage moves. Confirm on the instrument via a
  DRY-RUN first; nothing executes without --arm.
- Isolated: core does not import it. All other suites green.

New in pyFIB v0.16.0 (Qt GUI re-skin -- Phase 4: Setup page; feature parity):
- SETUP page, in the intended cryo order:
    1. Prep CryoFIBSEM -- a button (its action is TBD; placeholder that logs a note
       until its purpose is provided).
    2. Move to position -- a gisdep-style stage recall: pick a saved position and run
       `goto-position` with options (move method, set objective, set magnification,
       wait for stage idle). ARM-gated and CONFIRMED before moving.
    3. GISDEP -- launcher placeholder (info dialog; integrates as it matures).
    4. Coincidence -- Measure / Measure & Adjust Z wired to the coincidence module
       (armed = real, unarmed = --simulate), with the SEM + FIB overview images shown
       SIDE BY SIDE via QPixmap (parses the SEM_IMAGE:/FIB_IMAGE: markers).
- Worker/window: added build_module_argv + run_module so the GUI can launch standalone
  modules (the coincidence tool) as well as CLI subcommands; run_cli/run_module share a
  run_argv core. A confirm() dialog helper gates stage-moving actions.
- Verified offscreen: four sections present and ordered, position dropdown populates,
  the goto-position argv carries the chosen options and gates --arm, coincidence runs
  as a module with --simulate/--dry-run, and the image markers parse. gui_qt smoke
  suite now 12; skips cleanly without Qt.
- The Qt GUI now covers Setup / Capture / Positions / Mill / Log -- parity with the
  tkinter GUI's actions (plus move-to-position). The tkinter GUI remains in place and
  unchanged; the engine and CLI are untouched. All offline suites green.

New in pyFIB v0.15.2 (Qt GUI re-skin -- Phase 3: Mill page + nav change):
- NAV: "Tools" renamed to "Setup" and moved to the TOP; it is now the default landing
  page. Setup is reserved for the coincidence tool and a gisdep-style "move to
  position" (stage navigation) -- placeholder for now, filled in Phase 4.
- MILL page: a queue table with per-position CHECKBOXES for Mill / Autofocus / Refine,
  a pattern (.ely) picker, and options (wait strategy, move method, imaging scan rate,
  continue-on-error), plus Toggle-all / Toggle-autofocus / Toggle-refine helpers.
  "Start milling" assembles the mill-positions argv from the checked rows + options,
  gating --arm on the shared ARM toggle, and streams to the Log. The status column
  updates LIVE per position (Milling / Autofocus / Refine / Done / Error) by parsing
  the streamed CLI output.
- run_cli gained on_line / on_done hooks so pages can react to streamed output; used by
  the Mill page for live status. Subprocess model unchanged.
- Verified offscreen: nav order (setup first + default), queue loads, argv assembly
  excludes unchecked rows and maps autofocus/refine correctly, --arm gated by the
  toggle, and live status parses names (incl. stripping the trailing colon on the
  Error line). gui_qt smoke suite now 10; skips cleanly without Qt.
- tkinter GUI still in place and unchanged. Engine/CLI untouched. All offline suites
  green.
- Next: Phase 4 -- Setup page (coincidence tool with side-by-side SEM/FIB images, and
  the move-to-position stage navigation).

New in pyFIB v0.15.1 (Qt GUI re-skin -- Phase 2: Capture + Positions + ARM):
- Shared TOP BAR with the ARM safety toggle (prominent, red when armed) and the
  positions-file path (with Browse). ARM decides whether instrument-acting commands
  pass --arm (armed = real) or run the CLI's dry-run (unarmed = plan only).
- CAPTURE page: name / FIB mag / grab W x H / image dir / tilt, and a Capture button
  that shells out to `capture-position` (subprocess model unchanged) with --arm gated
  by the toggle. Output streams to the Log page.
- POSITIONS page: a table of saved positions loaded IN-PROCESS via
  PositionDatabase.load() (reading JSON is not an instrument action) -- shows name,
  mag, objective V, X/Y/Z, and the v2 geometry (pixel size, FOV width, ELY linkage).
  A reference-image preview loads the BMP via QPixmap (native BMP; no PIL needed).
  Auto-refreshes when the page is shown.
- Verified for real offscreen (QT_QPA_PLATFORM=offscreen): table populates the v2
  fields, BMP preview renders, and the Capture argv correctly gates --arm on the
  toggle. gui_qt smoke suite now 7 (adds capture-argv + positions-table); skips
  cleanly where Qt is absent.
- The tkinter GUI is still in place and unchanged. Engine/CLI untouched. All offline
  suites green.
- Next: Phase 3 -- the Mill page (queue table with per-position autofocus/refine
  checkboxes, wired to `mill-positions`).

New in pyFIB v0.15.0 (Qt GUI re-skin -- Phase 1 scaffold):
- NEW smartfib_executor/gui_qt/: a Qt (qtpy/PyQt5) front-end that matches elymaker /
  gisdep by reusing the shared fib_dark_theme asset. Runs with:
      python -m smartfib_executor.gui_qt
- Phase 1 is the SHELL only: shared theme applied, a left nav that switches a stacked
  set of pages (Capture / Positions / Mill / Tools / Log), and a Log page fed LIVE by a
  subprocess worker. Capture/Positions/Mill/Tools are placeholders filled in later.
- SUBPROCESS MODEL KEPT (as agreed): the Qt GUI shells out to the existing CLI
  (python -u -m smartfib_executor.cli ...) on a QThread and streams stdout as Qt
  signals -- exactly like the tkinter GUI. The engine, CLI, and all safety logic are
  untouched; this is a re-skin, not a rewrite. A Run > Self-check menu action proves
  the argv build + streaming + exit handling end to end.
- The existing tkinter GUI (smartfib_executor.gui) is LEFT IN PLACE and unchanged; the
  Qt GUI grows alongside it until it reaches parity.
- Verified for real: with PyQt5 installed the window builds offscreen
  (QT_QPA_PLATFORM=offscreen) -- all five pages present, theme applied (teal #17c3b2
  accent), nav switches pages + checks the right button, log appends. A Qt GUI is thus
  headless-smoke-testable, unlike the tkinter one. Smoke tests SKIP cleanly where Qt is
  absent (this sandbox), so they do not block the offline suites.
- Dependency: qtpy + PyQt5 for the Qt GUI (already required by elymaker / gisdep).

New in pyFIB v0.14.1 (elymaker integration, Phases C + D):
- Phase C: capture_position() now POPULATES the v2 fields, defensively:
    * image_width_px / image_height_px from the grab geometry actually used;
    * fov_width_m from the shared fov module; fib_pixel_size_m = fov_width_m /
      image_width_px (derived from the GRABBED width -> store-independent);
    * fov_height_m from AP_HEIGHT when it reads sensibly, else the square-pixel
      derivation (fov_width_m * height/width);
    * fib_scan_rotation_deg, fib_beam_shift_x/y_m, and the logging-only
      ap_fib_pixel_size_m from readbacks;
    * ely_pattern left None.
  Any failed readback records None and appends a "v2: ... unavailable" note -- the
  capture never aborts.
- Phase D: mill-time condition report (WARN, never refuse). Before each
  runner.run(job), pyFIB now prints the current FIB scan rotation and beam shift X/Y
  alongside the recorded capture values and their deltas. It only raises a WARNING
  when scan rotation drifts from capture beyond 0.5 deg (that would land the pattern
  rotated). A CHANGED beam shift is reported WITHOUT alarming, because refine
  intentionally leaves a beam shift to place the pattern -- so the interlock does not
  fight the refine feature. v1 records (no recorded values) degrade to listing the
  current values with "(no recorded value)". Milling always proceeds.
- Tests: test_positions_schema now 12 (added capture population + AP_HEIGHT fallback +
  defensive-None, and the mill report: lists values, no false alarm on refine shift,
  warns on rotation drift, handles v1). All suites green.
- Remaining: Phase E is a one-time BMP y-orientation check on the instrument (I cannot
  do it offline).

New in pyFIB v0.14.0 (elymaker integration, Phases A + B):
- SHARED FOV MODULE (Phase B): smartfib_executor/fov.py is now the single source of
  truth for MAG_REF_WIDTH_M / fov_width_m / pixel_size_m. refine/geometry.py re-exports
  it (its public API is unchanged), and core capture uses it -- so core no longer
  needs to reach into a fork, and the future AP_WIDTH change moves everything at once.
- POSITIONS SCHEMA v2 (Phase A): PositionRecord gains ten fields for elymaker so the
  handoff needs no calibration knowledge:
    geometry   : image_width_px, image_height_px, fov_width_m, fov_height_m,
                 fib_pixel_size_m
    conditions : fib_scan_rotation_deg, fib_beam_shift_x_m, fib_beam_shift_y_m
    linkage    : ely_pattern (relative path to the per-position .ely; None until authored)
    logging    : ap_fib_pixel_size_m (raw AP_FIB_PIXEL_SIZE, cross-check ONLY -- tied to
                 the store grid, must not drive the transform)
  All default to None so nothing else is affected.
- BACKWARD COMPATIBLE LOAD: load() accepts BOTH v1 and v2 files. v1 files load with the
  new fields = None and are upgraded to v2 in memory, so the next save() writes v2.
  save() writes v2. Unknown schemas are still rejected.
- Not yet done (next on the list): Phase C (populate the fields in capture_position),
  Phase D (mill-time interlock: verify scan rotation / beam shift reproduce capture --
  needs the refuse-vs-warn decision and care around refine's intentional beam shift),
  Phase E (one-time BMP y-orientation check on the instrument).
- Tests: new test_positions_schema (6) -- v2 round-trip, v1 loads with None, v1->v2
  upgrade on save, unknown-schema rejection, shared-fov agreement. All suites green.

New in pyFIB v0.13.0:
- Coincidence tool: the 0-tilt rule is REMOVED. It no longer refuses on a tilted
  stage (coincidence at 5 mm WD is tilt-independent); tilt is still read and logged
  for context. The Z-move safety is unchanged -- the per-move cap (<=0.5 mm) and hard
  bounds [20, 40] mm remain the pole-touch protection, and move_z re-validates them.
- Coincidence tool now IMAGES after reaching the correct WD: it grabs an SEM overview
  image, then (after that completes) a FIB overview image, and prints their paths
  (SEM_IMAGE:/FIB_IMAGE:). New coincidence/imaging.py (capture_sem_then_fib): switches
  the imaging beam, frame-safe grab, no stage motion; timing via the injected clock.
  Only runs on an armed adjust that reached the target WD -- never on measure/dry-run.
- FocusSem gains a SAFE imaging surface (grab_bmp + the SEM/FIB mode-switch and
  freeze commands). None of these move the stage, so the guarantee is intact: the
  ONLY motion the branch can produce is the validated Z move. Stage moves, set_value,
  and arbitrary commands stay blocked.
- UI: the Tools tab shows the SEM and FIB overview images SIDE BY SIDE after an armed
  "Measure & Adjust Z" run. The GUI passes --image-dir, parses the SEM_IMAGE:/
  FIB_IMAGE: markers, and displays both via Pillow (falls back to showing the paths
  if Pillow is absent).
- CoincidenceResult gains sem_image_path / fib_image_path. CLI: --image-dir.
- Tests: coincidence 33 (tilt now allowed; move_z allowed while tilted; SEM-before-FIB
  capture order; adjust captures images; dry-run does not). All suites green.
- INSTRUMENT-UNTESTED: DP_AUTO_FUNCTION transitions, the live Z move, and now the
  SEM/FIB mode switch + grab timing. The GUI cannot run here (no display); the image
  pipeline is verified by running the exact commands and parsing their output.

New in pyFIB v0.12.1:
- NEW "Tools" tab in the UI (between Mill and Log).
- It loads the COINCIDENCE tool with buttons:
    * "Measure Working Distance" -> runs the coincidence routine report-only
      (--dry-run): autofocus + read WD + recommend a Z move, never moving the stage.
    * "Measure & Adjust Z (<= 0.5 mm)" -> measures and, for a correction within the
      0.5 mm cap, moves Z toward 5 mm. ARMED runs pop a confirmation dialog first;
      all code-level guards (<=0.5 mm, Z in [20,40] mm, WD 3-8 mm, tilt 0, beam on)
      still apply, so an unsafe move is refused regardless.
  Safety model: UNARMED runs are SIMULATED (offline, harmless); ARMED runs hit the
  instrument. Measuring never moves the stage. Output streams to the Log tab.
- Placeholder launchers "Load ELY Maker" and "Load GISDEP" (not functional yet) --
  they show an info dialog; wired for when those tools integrate.
- SubprocessWorker gained optional stdin input, used to satisfy the coincidence
  tool's own confirmation prompt from the GUI dialog.
- UI only; no change to the milling/refine/autofocus engines. All suites green
  (coincidence 29, autofocus 54, refine 34, workflow 15, probe 13).
- NOTE: the GUI cannot be run in this environment (no display / tkinter), so the
  Tools tab is verified by compile + by running the exact commands it issues; give
  it a quick visual check on the instrument PC.

New in pyFIB v0.12.0:
- SEM autofocus in the coincidence branch now uses gisdep's proven method (ported so
  the two fold together cleanly at integration). New coincidence/autofocus.py +
  coincidence/clock.py:
    * Two-phase confirmation: first wait for DP_AUTO_FUNCTION to LEAVE Idle (proof the
      routine actually started, own startup timeout, raises AutoFunctionNeverStarted
      if require_started and it never starts) THEN wait for sustained Idle (10 s;
      any bounce to active resets the timer).
    * Injected Clock (RealClock / FakeClock) so the 10 s windows are exercised
      exactly but run INSTANTLY in tests.
    * Abort support (duck-typed .is_set()) that issues CMD_ABORT_AUTO -- ready for a
      GUI cancel button.
    * Distinct errors: AutoFunctionNeverStarted / AutoFunctionTimeout /
      AutoFocusAborted; AutoFocusConfig + AutoFocusResult (WD before/after).
  measure_and_recommend()/run_coincidence() gained clock= and abort= (default
  RealClock); the --stable-idle-s CLI flag still maps to the sustained-Idle window.
- UI THEME matched to elymaker / gisdep: pyFIB's ttk palette is retuned to the shared
  fib_dark_theme colours (teal #17c3b2 accent on dark slate #232833), replacing the
  old gold-on-black. Constant names are unchanged so the layout is untouched; only
  colours move. The canonical fib_dark_theme.py (+README) is bundled under
  smartfib_executor/assets/ as the shared palette source (and for any future Qt UI).
  NOTE: pyFIB's GUI is tkinter, so the Qt .qss cannot apply directly -- the palette
  was ported into the ttk styles instead; the two apps now read as one family.
- Tests: coincidence 29 (added FakeClock autofocus tests: sustained-Idle completes,
  never-started raises, flicker resets the timer, abort issues CMD_ABORT_AUTO + raises,
  overall timeout raises). All other suites unchanged and green.
- INSTRUMENT-UNTESTED: exact DP_AUTO_FUNCTION transitions during CMD_AUTO_FOCUS_* and
  the live Z move; the ported routine is the direct fix for the reported early
  completion.

New in pyFIB v0.11.2:
- Coincidence autofocus now guards against a PREMATURE / FLICKERING Idle. Instead of
  accepting the first DP_AUTO_FUNCTION == Idle (which the instrument can report while
  the autofocus is still running, so the focus had "not enough time to complete"),
  the waiter requires Idle to hold CONTINUOUSLY for 10 s. Any "busy" reading during
  that window resets the 10 s timer, so it only proceeds once the focus has genuinely
  settled. Polls at 0.25 s to catch brief busy flickers.
- If DP_AUTO_FUNCTION is momentarily observed busy, the min-elapsed backstop is not
  needed; if it is NEVER observed busy, a min-elapsed floor still applies so an
  instant Idle is not trusted. Overall wait is capped (240 s) and a stable-Idle
  timeout raises -> the caller refuses to read WD or move (safe).
- Configurable: run_coincidence(..., stable_idle_s=10.0) and CLI --stable-idle-s.
- Tests: coincidence 26 (added: a flicker Idle-busy-Idle is not accepted early;
  never-stable status times out). Suite kept fast by passing stable_idle_s=0.0.
- Still INSTRUMENT-UNTESTED on the exact DP_AUTO_FUNCTION transitions; the 10 s
  sustained-Idle requirement is the direct fix for the reported early completion.

New in pyFIB v0.11.1:
- Coincidence branch corrected per the operator: pyFIB now MOVES Z itself when the
  correction is <= 0.5 mm, and only RECOMMENDS (does not move) when it is larger.
  Decision:
    * WD already at target        -> coincident; nothing to do.
    * WD outside [3, 8] mm         -> focus suspect; refuse, no move.
    * target Z outside [20, 40] mm -> refuse; DO NOT MOVE.
    * |correction| > 0.5 mm         -> pyFIB will NOT move; recommend the move to the
                                       operator (by hand), then re-run.
    * |correction| <= 0.5 mm & safe -> pyFIB moves Z after an interactive
                                       confirmation, waits for the stage, then
                                       re-autofocuses to verify (reports residual).
- The move is Z-ONLY and goes through FocusSem.move_z, the SINGLE motion path, which
  RE-VALIDATES the per-move cap and hard bounds at the moment of motion (reads the
  live Z, refuses if the target would exceed 0.5 mm, leave [20, 40] mm, or if tilted)
  and re-commands every other axis to its current value. Any other motion/set is
  still blocked structurally.
- CLI: interactive prompt (type 'yes') before any move; --dry-run reports/recommends
  without moving even for a small correction. --simulate for offline demo.
- Tests: coincidence 24 (added: small move performed+verified when confirmed; not
  moved when declined; large move recommended-not-moved; allow_move=False never
  moves; move_z re-validates cap and hard bounds; move changes only Z).
- INSTRUMENT-UNTESTED: DP_AUTO_FUNCTION transitions and CMD_AUTO_FOCUS_* settling;
  and the live Z-only move + DP_STAGE_IS idle wait. First real run: dry-run first,
  then confirm a small move on a safe Z.

New in pyFIB v0.11.0:
- NEW isolated branch smartfib_executor/coincidence/: measure the SEM working
  distance and RECOMMEND a Z move to reach the 5 mm FIB/SEM coincidence point.
      python -m smartfib_executor.coincidence --simulate    # offline demo
      python -m smartfib_executor.coincidence               # against the instrument
- It NEVER moves the stage. The workflow autofocuses the SEM
  (CMD_AUTO_FOCUS_COARSE then FINE, polling DP_AUTO_FUNCTION to Idle), reads AP_WD,
  and prints the recommended Z change for the operator to make by hand. After the
  move, re-run to verify.
- No-stage-motion is STRUCTURAL, not just a promise: FocusSem exposes only reads and
  the two autofocus commands; move_stage, set_value, and any other command raise
  CoincidenceSafetyError. A test asserts a full run issues nothing else.
- Geometry (confirmed): +Z is closer to the pole and SHORTENS WD, so
  delta_Z = WD_meas - 5 mm. WD 5.3 mm -> +0.3 mm (toward pole); WD 4.7 mm -> -0.3 mm.
- Safety checks (all refuse-and-report, never auto-trim, never move):
    * SEM beam must be on (AP_ACTUALKV > 100 V);
    * stage tilt must be ~0 (refuses otherwise, for now);
    * measured WD must be within [3, 8] mm or focus is treated as suspect;
    * recommended move must be <= 0.5 mm (per-move cap);
    * target Z must stay within the hard bounds [20, 40] mm -- 40 never crossed.
  All limits are named constants in coincidence/limits.py (the operator may tighten
  40 mm / 0.5 mm later). AP_STAGE_HIGH_Z/LOW_Z are left untouched.
- Coincidence at WD = 5 mm is reported (tilt-independent); no extra motion.
- Isolated: nothing in core imports it. 17 offline tests (guard, sign, clamps,
  refusals, verify-after-move).
- INSTRUMENT-UNTESTED: that DP_AUTO_FUNCTION reads 1/Focus during CMD_AUTO_FOCUS_*
  and returns to 0/Idle when done, and that the commands settle before Idle. The
  poll guards against a premature Idle, but the exact transition needs a real run.

New in pyFIB v0.10.6:
- Autofocus at grazing FIB incidence: the top and bottom of the focus box sit at a
  different defocus than the centre (the focus plane tilts across the box), which
  biases the sharpness metric. Two independent, configurable fixes:
  * EXCLUDE TOP/BOTTOM FROM ANALYSIS (analysis_height_frac): score only the central
    fraction of ROWS. Pure metric change -- does not alter what the beam scans. E.g.
    0.5 scores the central half; 1.0 (default) keeps every row. Applied in
    metrics.preprocess via the new crop_central_rows(), consistently across all sweep
    frames.
  * SHORTER BOX (raster_h_scale): reduce the reduced-raster height so the beam scans
    a shorter vertical strip -- less defocus spread AND less dose. Exposes the raster
    height that was previously fixed at 0.60 of the field.
- CLI: standalone autofocus --analysis-height-frac / --raster-h-scale; mill-positions
  --af-analysis-height-frac / --af-raster-h-scale. Both plumb through AutofocusConfig.
- Defaults preserve current behaviour (analysis_height_frac=1.0, box height 0.60).
- Tests: +4 (central-row crop keeps the middle band; a frame sharp only in the centre
  scores higher when cropped to it; raster height shortens & stays centred). Totals:
  autofocus 54, refine 34, workflow 15, probe 13.

New in pyFIB v0.10.5:
- CONFIRMED on the instrument: DP_SCANRATE is READ-ONLY (set_value fails with
  rc=1019, matching the manual's "RO" note). It is changed only via CMD_SCANRATE#.
- The default scan_rate_method is now "cmd" everywhere (adapters, AutofocusConfig,
  RefineConfig, and all CLI defaults), so raising the imaging dwell just works
  without the failed-set noise. 'set' and 'set_then_cmd' remain available.
- Verify logic softened: because DP_SCANRATE is read-only it can read 0 / be
  unreadable mid-mode, so a 0/None read after issuing CMD_SCANRATE# is reported as
  "could not confirm" rather than a false WARNING. A genuine wrong value (a valid
  1-15 that differs) still warns.
- Log messages corrected for the 'cmd' path ("used CMD_SCANRATE8." instead of
  implying a failed direct set).
- Faithful mocks: the autofocus MockSmartSEM and the refine grab fake now model
  DP_SCANRATE as read-only (set raises rc=1019; only CMD_SCANRATE# works), per the
  project rule that mocks mirror instrument behaviour. The refine adapter test now
  captures the scan rate AT GRAB TIME, proving it was raised to 8 for the frame and
  restored to 4 afterwards -- via CMD, not a direct set.
- Tests: refine 34, autofocus 50, workflow 15, probe 13 -- all green.

New in pyFIB v0.10.4:
- IMAGING DWELL / SCAN RATE for SNR. The probe confirmed DP_SCANRATE is the scan-speed
  enum on this column (currently 4; 8 gives good SNR). Autofocus and refine now raise
  DP_SCANRATE for their diagnostic frames and RESTORE the original afterward (milling
  dwell is untouched). Default imaging_scan_rate = 8.
- Per the request, the default method is a DIRECT set ("set"): set_value("DP_SCANRATE").
  It is VERIFIED by read-back and logs exactly what happened, so the first armed run
  shows either "DP_SCANRATE now 8 (was 4)" or a clear WARNING that direct set did not
  take. If it does not take, switch to --scan-rate-method cmd (CMD_SCANRATE#) or
  set_then_cmd (try set, fall back to cmd) -- no code change needed.
- REFINE GRAB MADE FRAME-SAFE. At a higher scan rate the frame is SLOWER, so the old
  fixed-sleep grab could capture a half-scanned frame. The refine adapter now uses the
  same end-of-frame freeze + DP_FROZEN poll as autofocus (freeze at end of frame, wait
  until DP_FROZEN, then grab), with a configurable frozen timeout. This is required for
  the higher dwell to actually help rather than hand back an incomplete frame.
- Autofocus raises the scan rate once in prepare() and restores it in restore(), so the
  whole sweep runs at high dwell and the rate is put back even if the sweep raises.
- CLI: mill-positions --imaging-scan-rate (default 8, -1 to leave as-is) and
  --scan-rate-method; standalone refine/autofocus --scan-rate / --scan-rate-method.
- DOSE NOTE: higher dwell = more dose per frame. Refine takes ~2-3 frames; autofocus
  ~11. Autofocus frames are on the parked throwaway area; refine frames are on/near the
  target. Acceptable for alignment, but worth watching on a real lamella.
- Tests: refine 34 (+2 adapter: scan-rate restore + frame-safe grab, incl. ignored-
  direct-set -> cmd fallback), autofocus 50 (+4 scan-rate set/restore/warn/fallback).
- INSTRUMENT-UNTESTED end-to-end; the scan-rate mechanism is validated offline against
  mocks. First armed run: watch the [scanrate] log line to confirm the direct set took.

New in pyFIB v0.10.3:
- Probe catalog gains a PRIORITY 1 capability, "FIB dwell time / scan speed (SNR
  control)", to find how to raise the imaging dwell -- the real fix for the ~12
  correlation SNR. Candidates cover the three possibilities so the instrument tells
  us which applies and its units:
    * continuous dwell: AP_FIB_DWELL_TIME / AP_DWELL_TIME / AP_PIXEL_DWELL (s/px)
    * scan-speed enum:  DP_FIB_SCANRATE / DP_SCANRATE / DP_SCAN_SPEED (index)
    * frame integ/avg:  DP_FIB_FRAME_AVERAGE / DP_FRAME_INT / DP_FRAME_AVERAGING /
                        DP_NOISE_REDUCTION
    * line/frame time:  AP_LINE_TIME / AP_FRAME_TIME
  Catalog-only change: the probe remains READ-ONLY by construction (no writes, no
  commands executed). No behavior change anywhere else. 48 candidates / 12
  capabilities.

New in pyFIB v0.10.2:
- Recalibration (uploaded) CONFIRMS the beam-shift sign is unchanged: response R=+1
  on both axes -> correction sign -1/-1, crosstalk 2.6% (no scan rotation). So the
  reported "sign issue" was NOT the sign constant.
- ROOT CAUSE of the apparent sign issue: the closed-loop verify undid a correction
  whenever the residual merely "did not improve" and printed "possible sign error".
  At the new SNR (~12, down from ~18) registration noise can make a CORRECT
  correction look no-better, so refine falsely blamed the sign and undid good work.
  FIX: a wrong sign roughly DOUBLES the error, so refine now only undoes / flags a
  sign error when the residual clearly INCREASES (>1.25x). If it neither reaches
  tolerance nor clearly worsens, it is the correlation noise floor -- the correction
  is KEPT and reported honestly, without implicating the sign. A genuinely wrong
  sign is still caught and undone (tested).
- The milling integration now LOADS refine_calibration.json (via the same loader the
  standalone tool uses) and passes the measured signs into refine_position; it no
  longer relies only on the built-in default. Configurable via RefineConfig.
  calibration_file.
- Shipped reference calibration (data/instrument_calibration_crossbeam550.json)
  updated to the latest measurement; the pinned-defaults test still holds (-1/-1).
- NOTE for later: measured pixel size came out ~4.6% larger than the 0.1143 constant
  (57-59 nm vs 55.8 nm at 2000x), and X/Y differ ~3%. This is the AP_WIDTH item --
  reading the live field-of-view would remove the constant. Sign-independent; does
  not affect direction, only correction magnitude (~5%).
- Tests: refine 32 (added noise-floor vs wrong-sign discrimination). Others unchanged.

New in pyFIB v0.10.1:
- FIB beam shift is now zeroed between positions so a refine correction never
  accumulates across sites. Refine applies its correction as an INCREMENT on the
  current beam shift, so a leftover shift from a prior site (or one that was already
  high before the batch) would baseline the next site's refine and skew the park /
  reduced-raster geometry. Now AP_FIB_BEAM_SHIFT_X and _Y are set to 0:
    * once before the first site (so the first refine starts clean even if the shift
      was already high), and
    * after every site, in a finally -- so it runs after milling completes AND after
      an error or Ctrl+C, leaving the column ready for the next site.
  The reset is verified by read-back and warns if either axis does not return to 0.
  The refine correction still persists through THAT site's milling (so the pattern
  lands on target); it is only cleared once the site is done.
  Disable with mill-positions --no-reset-beam-shift (default: on).
- Tests: workflow suite now 15 (added: zeroed at start + after each site, zeroed even
  when a position errors, and the disable flag). autofocus 46, refine 30, probe 13.

New in pyFIB v0.10.0:
- INTEGRATION: the autofocus and refine forks are now wired into the milling
  workflow and the UI, with a per-position choice for each. New module
  smartfib_executor/milling_workflow.py holds the thin integration layer.
- Per-position workflow (serial -- the next position does not start until the
  previous mill completes):
      recall (stage + saved objective voltage)
        -> autofocus  (if checked)  -- sweeps the SAVED objective voltage above and
                                       below; leaves the column at best focus
        -> refine     (if checked)  -- cross-correlates the live FIB image against the
                                       reference BMP and nulls the offset with beam
                                       shift, which stays applied so milling lands on
                                       target
        -> mill
- UI (mill queue) has two new checkbox columns, "Autofocus" and "Refine", toggled
  per position. The mill subprocess now also runs with -u so the queue status and
  the autofocus/refine progress update live (fixes the earlier block-buffering lag).
- CLI (mill-positions): --autofocus NAME[,NAME...] / --autofocus-all,
  --refine NAME[,NAME...] / --refine-all, plus --af-half-range, --af-coarse-points,
  --af-abort-on-fail, --refine-abort-on-fail. The dry-run plan shows [AUTOFOCUS]/
  [REFINE] tags per position.
- Failure policy (default): if autofocus or refine cannot proceed confidently, log
  and fall back to the recalled position / saved voltage (no worse than not using
  the feature). Pass the abort flags to make a failure stop the position instead.
- Safety preserved: autofocus still parks the beam + uses the measured safe raster
  side; refine keeps stage correction OFF and the closed-loop verify ON; focus is
  driven by the FIB objective voltage. The refine drift scaling remains the v0.9.1
  image-consistent fix.
- Tests: new workflow suite (12) asserts the recall->autofocus->refine->mill order,
  per-position independence, and strict seriality, plus dry-run/failure policy.
  Totals: workflow 12, autofocus 46, refine 30, probe 13.
- INSTRUMENT-UNTESTED end-to-end: the forks are validated offline and the ordering
  is tested, but this full wiring has not yet run on the microscope. Recommend a
  dry-run (mill-positions without --arm) and a single-position armed trial first.

New in pyFIB v0.9.1 (FIX for a v0.9.0 regression):
- v0.9.0 broke position refinement: corrections (both FIB beam shift AND stage) were
  scaled far too small, so recall/refine no longer reached the target. Root cause: the
  drift px->metre conversion was switched to the live AP_FIB_PIXEL_SIZE, which is tied
  to the STORE/display pixel grid. The refine adapter grabs on a fixed grid (1024 wide)
  and cross-correlates on THAT grid, so when the store width differs from the grabbed
  width every correction was scaled by grab_width/store_width (e.g. a 3072 store with a
  1024 grab -> 3x too small). The probe was taken at store 1024, where the two grids
  coincide, so the bug did not show there.
- FIX: refinement again derives pixel size from the ACTUAL correlated image width
  (fov_width_m(mag)/reg.shape[1]) -- the self-consistent v0.8.1 method -- for BOTH beam
  and stage corrections. Verified numerically identical to v0.8.1. Stage RECALL itself
  (positions.recall_position) was never changed and continues to use the stored stage
  coordinates directly.
- The live AP_FIB_PIXEL_SIZE reading is retained ONLY as a cross-check: refine now logs
  a note when the live (store-grid) pixel size disagrees with the correlated-image
  pixel size by >10% -- the signal that would have caught this immediately.
- The same store-tied value had been threaded into the autofocus reduced-raster
  geometry; reverted so the raster side is computed on the SCREEN grid (screen_size),
  as before. Live pixel size there is likewise a cross-check only.
- Regression tests added: a scope reporting a 3x-wrong (store-tied) pixel size must
  still converge; proven to FAIL against the v0.9.0 scaling and PASS after the fix.
- Kept from v0.9.0 (unaffected, additive): scan-rotation guard and AP_FIB_WORKING_
  DISTANCE recording. Focus is still driven by the FIB objective voltage.
  Tests: autofocus 46, refine 30, probe 13.

New in pyFIB v0.9.0 (built on the confirmed probe results):
- LIVE PIXEL SIZE. Both forks now read AP_FIB_PIXEL_SIZE from the instrument and use
  it directly, falling back to the MAG_REF_WIDTH_M=0.1143 constant (labelled as such)
  only if it is unavailable. This replaces an inferred constant with ground truth in
  two safety-relevant paths: the refine drift px->metre conversion (scales every
  correction) and the autofocus reduced-raster geometry (keeps the beam off the
  lamella). On the probe machine the live value matched the constant to 0.03%, but it
  now self-corrects for store resolution and any recalibration.
- SCAN-ROTATION GUARD. The probe showed DP_SCAN_ROT=On (enabled) with
  AP_FIB_SCAN_ROTATION=0 -- i.e. rotation is currently zero but could be changed
  without notice, silently breaking the raster-side and correction-axis geometry.
  Both forks now read AP_FIB_SCAN_ROTATION and, if |rotation| exceeds a threshold
  (default 0.5 deg), WARN by default or REFUSE if strict_scan_rotation=True.
  autofocus: strict guard raises before any imaging; refine: strict guard refuses to
  correct. At 0 deg (the current state) both are silent.
- FIB WORKING DISTANCE RECORDED. capture_position() now reads AP_FIB_WORKING_DISTANCE
  (best-effort) into PositionRecord.fib_working_distance_m, and autofocus stamps it on
  AutofocusResult.working_distance_m. Recorded for reference only -- FOCUS IS STILL
  DRIVEN BY THE FIB OBJECTIVE VOLTAGE; working distance does not change the mechanism.
  Backward compatible: positions files written by older pyFIB load with WD = None.
- Note: AP_FIB_WORKING_DISTANCE was NOT in the probe run (which tested the SEM AP_WD),
  so it is read defensively -- a failure records None rather than aborting.
- Tests: autofocus 46, refine 29, probe 13. Core positions round-trip verified.

New in pyFIB v0.8.1:
- Probe catalog extended with the operator's requested mnemonics (none were present):
  AP_WD, AP_IPROBE, AP_ACTUALKV, AP_MANUALKV, AP_WIDTH, AP_SCANROTATION -- plus three
  siblings that belong with them: AP_HEIGHT, AP_FIB_SCAN_ROTATION and DP_SCAN_ROT.
  36 candidates across 11 capabilities.
- Two of these are more consequential than they look:
  * AP_WIDTH gives the FIELD OF VIEW WIDTH in metres directly -- exactly the quantity
    fov_width_m() currently infers from MAG_REF_WIDTH_M=0.1143. If AP_WIDTH and a
    pixel size BOTH read, they cross-check each other (pixel_size * image_width
    should equal AP_WIDTH), which would settle the 1.2% discrepancy the beam-shift
    calibration turned up.
  * SCAN ROTATION is a silent-breakage risk, so it is now its own PRIORITY 1
    capability. Both forks assume the image axes are aligned with the beam-shift
    axes. If scan rotation is non-zero, the autofocus raster-side geometry (which
    keeps the beam off the lamella) and the refine correction axes both rotate out
    from under us with NO error raised. The beam-shift calibration measured only
    2-3% crosstalk, consistent with ~0 rotation -- but nothing verifies that today,
    or notices if it is changed. AP_FIB_SCAN_ROTATION is preferred over
    AP_SCANROTATION for ion imaging.
- AP_IPROBE added to the probe-current candidates: amperes rather than the 0-15 enum,
  so preferable if it applies to the ion column here.
- AP_WD/AP_ACTUALKV/AP_MANUALKV grouped as "SEM column state" (priority 3, record
  with each position). AP_WD is the electron column's focus proxy -- the SEM analogue
  of the FIB objective potential pyFIB already stores.

New in pyFIB v0.8.0:
- NEW read-only diagnostic: smartfib_executor.probe. Answers "which SmartSEM
  mnemonics actually exist on THIS instrument?" before any code is built on them.
      python -m smartfib_executor.probe --save probe_results.json
      python -m smartfib_executor.probe --simulate      (offline demo)
- SAFE BY CONSTRUCTION: it calls only get_float/get_str, enforced by ReadOnlySem,
  which raises if anything reaches set_value/execute/move_stage/grab_bmp. CMD_*
  cannot be tested without running it, so commands are reported as un-probeable and
  NEVER executed (notably CMD_BEAM_ON/OFF, which are ELECTRON gun commands that
  pyFIB deliberately does not automate). Tests assert a full run issues nothing but
  reads.
- Organised by CAPABILITY, not by name, because the naming is the uncertain part:
  each capability lists candidate mnemonics in preference order and the report says
  which one resolved. Distinguishes "name not recognised" (err 1000) from "name is
  right but unreadable now" (1001/1002/1025) -- the exact question when a mnemonic
  works on one machine and not another.
- Priorities encode what pyFIB would actually do with each value:
    P1 pixel size      -- would replace MAG_REF_WIDTH_M=0.1143, a constant reverse-
                          engineered from a databar (measured 1.2% off, 3% X/Y
                          spread). It scales every refine correction AND sets the
                          autofocus reduced-raster geometry that protects the lamella.
    P1 DP_IMAGE_STORE  -- screen size is currently ASSUMED 1024x768; if the store is
                          512x384 the raster rectangle is computed wrong.
    P1 probe current   -- pyFIB advises a low imaging current but never sets/records it.
    P2 auto B/C, detector channel.  P3 FIB EHT, SEM beam shift, emission current.
    P0 health check on the mnemonics pyFIB already depends on.
- Also corrects a stale note: AP_FIB_MAGNIFICATION, AP_FIB_BEAM_SHIFT_X/Y,
  SV_SERIAL_NUMBER and DP_PATTERNING_MODE are ALREADY used by pyFIB; DP_IMAGE_STORE
  and DP_AUTO_FUNCTION are only mentioned, never queried.
- Isolated: nothing imports it. 13 tests pass.

New in pyFIB v0.7.2:
- Beam-shift convention MEASURED on this instrument and set as the default.
  calibrate.py applied +2 um and the IMAGE moved:
      X: +36.8 px -> RIGHT  (R_x = +1, snr 18.1, crosstalk 2.0%)
      Y: +35.7 px -> DOWN   (R_y = +1, snr 19.0, crosstalk 2.7%)
  With correction_sign = -R:  BEAM_SIGN_X = BEAM_SIGN_Y = -1.
- This REVERSES the v0.7.1 change. v0.7.1 had inferred +1 from a verbal description
  of which way the beam moves; the measurement says otherwise, and the measurement
  wins. (v0.7.0's original -1 was right after all.) Exactly why calibrate.py exists.
- The raw calibration is shipped at
  smartfib_executor/refine/data/instrument_calibration_crossbeam550.json and two
  tests PIN geometry.BEAM_SIGN_* to it, so the defaults cannot drift from the
  measurement without a new one.
- CONVENTION CONFLICT RESOLVED: the measurement says the safe raster side for a
  positive X park is the LEFT -- which is exactly what the autofocus fork already
  does. Autofocus needs NO change, and its v0.6.1 burn fix is now independently
  confirmed by measurement rather than by inference.
- Scale check: measured pixel size 54.28 nm (X) / 56.04 nm (Y), mean 55.16 nm vs the
  nominal 55.81 nm -- agreement to ~1.2%, i.e. the magnification -> pixel-size
  calibration (MAG_REF_WIDTH_M) is sound and is left unchanged. Crosstalk 2-3% means
  no significant scan rotation to model.
- Simulator response flipped to R=+1 to match the instrument. 24 refine tests pass.

New in pyFIB v0.7.1:
- FIX from the instrument: refine's X correction moved the WRONG way. The beam-shift
  correction signs are now +1 on both axes (were -1).
  Derivation, from two operator observations:
    * +AP_FIB_BEAM_SHIFT_Y moves the BEAM DOWN relative to the sample, so a fixed
      feature ends up ABOVE the new field centre -> the IMAGE moves UP -> response
      R_y = -1;
    * X behaved the same way (the -1 correction pushed the wrong direction) -> R_x = -1.
  With correction_sign = -R, both axes are +1.
- NEW: calibrate.py -- MEASURE the convention instead of asserting it. Applies a known
  beam shift on each axis, cross-correlates before/after, and reports:
    * the SIGN  (which way the image actually moves for a positive shift)
    * the SCALE (pixels per metre -> an independent check of the magnification ->
      pixel-size calibration; agreed to 0.2% in simulation)
    * the safe raster side for the AUTOFOCUS fork -- one run resolves both modules
  It refuses to exceed the 15 um beam-shift limit, flags a shift that did not take
  ("image barely moved") rather than inventing a sign, flags large cross-axis motion
  (scan rotation), and always restores the original beam shift.
      python -m smartfib_executor.refine --calibrate --save-calibration
  A saved refine_calibration.json is then used automatically by later refine runs
  (--ignore-calibration to opt out).
- !! CONVENTION CONFLICT TO RESOLVE ON THE INSTRUMENT !!
  The autofocus fork (v0.6.1) encodes R_x = +1 (+X park -> target appears RIGHT ->
  raster LEFT). The observations above imply R_x = -1 (-> raster RIGHT). Both cannot
  be true, and the autofocus raster side is what keeps the beam off the lamella.
  Run --calibrate before the next autofocus run; its output states the safe side
  directly. The two modules are independent, so this does not affect refine.
- Simulator now models the instrument response (R = -1) by default and accepts either
  handedness, so tests reflect the real column. 22 refine tests pass.

New in pyFIB v0.7.0:
- Position refinement fork (roadmap #4): smartfib_executor.refine. Isolated -- nothing
  in core or the autofocus fork imports it. After a stage recall it cross-correlates
  the reference BMP stored by positions.py (image_bmp) with a fresh FIB image at the
  stored magnification (fib_magnification_x), then pulls the field back onto the
  original target:
    * drift <= 10 um  -> FIB beam shift (AP_FIB_BEAM_SHIFT_X/Y)
    * drift  > 10 um  -> stage move (X/Y only) -- code WRITTEN but DISABLED by default
                         (enable_stage=False) pending sign/rotation calibration.
  Layers: registration (FFT phase cross-correlation, subpixel), geometry (pixel<->metre
  + correction signs in one place), refine (orchestrator), sources (RefineScope iface +
  SimulatedRefineScope + real ScopeRefineAdapter, instrument-untested).
  Try it:  python -m smartfib_executor.refine --simulate --drift-um 5
  Tests:   python -m smartfib_executor.refine.tests   (22 pass)
- SAFETY -- closed-loop verify: a position correction is a feedback loop, so a wrong
  sign doubles the error. Every correction is followed by a fresh image + re-correlation;
  if the residual does not shrink, the last correction is UNDONE and refinement reports
  failure. This measures the real residual and does not depend on the assumed sign.
  (Caught and reverted a deliberately-wrong sign in testing.)
- Beam-shift sign uses the convention measured in v0.6.1; Y is assumed and guarded by
  the closed-loop check. Databar is auto-cropped before correlating (static text would
  otherwise pin the shift at zero). Confidence gate on correlation SNR.

New in pyFIB v0.6.2:
- Added ARCHITECTURE.md: what each module does + how the autofocus fork was developed.
- Removed in-code comments that directly referenced the SmartSEM Remote API *manual*
  (e.g. "as documented in the ... manual", section numbers, "signature in manual").
  The technical notes those comments carried (parameter value tables, method
  signatures) are kept; only the document references were removed. References to the
  SmartSEM Remote API itself (the software the code drives) are unchanged.
- No functional change; 62 tests still pass.

New in pyFIB v0.6.1:
- CRITICAL FIX -- v0.6.0's --raster-side auto BURNED THE SAMPLE. The beam-shift sign
  convention was inverted. MEASURED ON THE INSTRUMENT: a POSITIVE
  AP_FIB_BEAM_SHIFT_X makes the area of interest appear to the RIGHT of centre, so
  the LEFT side is the safe one to raster. v0.6.0 assumed the opposite and chose the
  right side for a +12 um park -- straight over the target.
      target_screen_x = screen_w/2 + shift_px      (was: - shift_px)
      +shift -> target RIGHT -> scan LEFT          (was: scan right)
  Do NOT revert this without a fresh instrument observation.
- Note on why the v0.6.0 "safety" warning did not help: it was computed from the SAME
  wrong convention, so it cheerfully reported the target as outside the raster while
  the beam sat on it. A check derived from the assumption it is meant to test is not a
  check. The regression is now pinned by an END-TO-END test that asserts the beam
  never doses the target's screen column (MockSmartSEM records every dosed column);
  re-introducing the old sign makes it fail.
- Reduced raster shrunk per operator request: width -10% (DEFAULT_RASTER_W_SCALE=0.90),
  height -40% (DEFAULT_RASTER_H_SCALE=0.60). At 1024x768 with a half-field band this
  gives a 461x461 rectangle, centred vertically, anchored to the OUTER edge of the
  field so the narrowing widens the clearance rather than eating into it.
  Target clearance at a 12 um park improves from ~12.0 um to ~14.8 um.
  Tunable: --raster-fraction, or an explicit --reduced-raster X Y W H.

New in pyFIB v0.6.0:
- Beam park raised to 12 um (AP_FIB_BEAM_SHIFT_X = 1.20e-05). Instrument maximum is
  15 um (BEAM_SHIFT_X_MAX_M); we deliberately stay off the rail.
- IMPORTANT CORRECTION: Grab()'s xoff/yoff/width/height only CROP the framestore --
  the beam still rasters the WHOLE field, so cropping the grab saves NO dose. (Our
  own stale-frame bug proved Grab merely reads a framestore that refreshes on its own
  scan cycle.) Cropping cannot "image only the left side to avoid burning".
- NEW: reduced raster -- the mechanism that genuinely shrinks the burned area.
  Sets AP_RED_RASTER_POSN_X/Y + AP_RED_RASTER_W/H then CMD_MODE_REDUCED, verifies
  DP_IMAGE_MODE really reads Reduced, and restores CMD_MODE_NORMAL on every exit path
  (success, failure, exception, Ctrl-C) alongside the beam shift. The grab ROI defaults
  to the rastered rectangle, since only that region is refreshed.
      --raster-side {left,right,auto}   --raster-fraction F
      --reduced-raster X Y W H          --screen-width/--screen-height
- WHY THIS MATTERS: at 2000x the field is ~57 um wide (half-width 28.6 um), but the
  column's beam shift maxes at 15 um. So beam shift ALONE can NEVER take the area of
  interest out of the scanned field at this magnification -- reduced raster (or a
  stage move, or higher mag) is the only way. With a 12 um park + a half-field raster
  on the far side, the target sits ~12 um outside the scanned region.
- SIGN CONVENTION: pyFIB assumes +AP_FIB_BEAM_SHIFT_X moves the field +X, so the
  target appears at screen x = w/2 - shift_px (x~297 px for +12 um at 2000x, i.e. in
  the LEFT half -> scan the RIGHT). --raster-side auto picks the far side from the
  shift sign. If the raster would cover the target, pyFIB prints a loud WARNING rather
  than silently burning it. VERIFY THE SIGN ONCE against a real image.
  Helpers: fov_width_m(), target_screen_x(), safe_raster_side(), raster_for_side().
- MockSmartSEM now models reduced raster + tracks which screen columns the beam
  actually dosed, so "does cropping save dose?" is a testable assertion, not a belief.
  59 tests pass: python -m smartfib_executor.autofocus.tests

New in pyFIB v0.5.4:
- Autofocus now images on a THROWAWAY area, per the confirmed-good instrument run:
  * AP_FIB_MAGNIFICATION is set to 2.00e+03 before focusing (same mnemonic/value the
    main workflow uses, and the conditions the focus metric was calibrated at).
  * AP_FIB_BEAM_SHIFT_X is parked to 1.00e-05 m BEFORE any imaging, and returned to
    0.00e+00 m afterwards.
  * The return is SAFETY-CRITICAL (a shift left applied would displace subsequent
    imaging AND milling), so autofocus() brackets the whole sweep in try/finally:
    the shift is returned on success, failure, exception and Ctrl-C. restore() never
    raises, reads the value back to confirm, and shouts if it cannot.
  * Flags: --fib-mag, --beam-shift-x, --beam-shift-return, --no-beam-shift.
- --frame-wait default lowered 5.0 -> 3.0 s (confirmed sufficient on the instrument;
  still > 2x the ~1.3 s cycle time, and the DP_FROZEN poll remains as a backstop).
- NOTE (geometry, please read): at 2000x the FIB field of view is ~57 um wide
  (half-width ~28.6 um). This is measured from the operator's own databar
  (Image Pixel Size = 55.82 nm over 1024 px) and confirmed independently from the
  10 um scale bar at BOTH store resolutions. FIB imaging rasters the WHOLE field,
  so a 10 um beam shift does NOT take the area of interest out of the scanned area
  -- it only moves it off-centre, and it is still dosed. pyFIB now prints an
  explicit NOTE when the requested shift is smaller than half the field, with the
  numbers, and leaves the decision to the operator. To actually clear the field at
  2000x you need a shift > ~28.6 um (or a stage move, or higher magnification).
  New helper: sources.fov_width_m(magnification).

New in pyFIB v0.5.3:
- FIX (found on the instrument): autofocus swept too fast and only ONE image was
  really acquired. grab() called Grab() immediately after CMD_UNFREEZE_ZONE, but the
  framestore needs a full frame cycle (~1.3 s at 2000x) to refresh -- and Grab()
  never errors on stale data, it just returns the PREVIOUS frame. Every sweep point
  therefore scored an identical image and the focus curve was flat.
  The grab sequence is now:
      set voltage -> settle (1.0 s) -> ensure FIB imaging -> CMD_UNFREEZE_ZONE
      -> scan for frame_wait_s (5.0 s) -> CMD_FREEZE_ZONE (at end of frame)
      -> poll DP_FROZEN -> Grab() from the stable frozen framestore
  Freezing BEFORE the grab also avoids capturing a half-scanned frame.
- DP_FREEZE_ON is now set to "End Frame" so a freeze lands on a frame boundary.
- New timing knobs (defaults from the measured instrument):
      --settle       1.0 s  (after changing objective voltage)
      --frame-wait   5.0 s  (scanning time before freeze+grab; must exceed cycle time)
      --no-freeze-on-end-frame  (escape hatch; not recommended)
  Real-mode output now reports total FIB exposure time up front.
- MockSmartSEM now models the framestore + cycle time, so this class of bug fails
  offline instead of on the microscope. New regression tests assert that each sweep
  point yields a DISTINCT fresh frame, that the column is frozen when Grab() runs,
  and that the mock itself still reproduces staleness. Verified: re-introducing the
  old sequence makes these tests fail with "frames are identical -- stale framestore".

KNOWN ISSUE (main workflow, NOT yet fixed -- ask before changing):
  positions.py capture_position has the SAME pattern: CMD_UNFREEZE_ZONE then Grab()
  with no wait (its settle_s applies to the STAGE, before the unfreeze). Saved
  position reference BMPs are therefore likely to be the previous frame rather than
  the position just moved to.

New in pyFIB v0.5.2:
- FIX (would have failed on the instrument): the autofocus real-mode runner imported
  a non-existent class name (SmartSemCom) instead of smartsem_com.SmartSEM. It would
  have died on connect with a misleading "pywin32 missing?" message, wasting a session.
- New offline validation of the instrument path: tests/test_scope_source.py runs the
  real ScopeImageSource against a MockSmartSEM that mimics smartsem_com.SmartSEM and
  records every COM call. Verifies mnemonics, bounds clamping, dry-run, ROI passthrough,
  that every grab is followed by a FREEZE (ion dose), and that a full autofocus converges
  and leaves the column at the chosen focus.
- Run every suite with one command:  python -m smartfib_executor.autofocus.tests

New in pyFIB v0.5.1:
- Autofocus validated against REAL Crossbeam 550 FIB data (2000x, Signal A = SESI,
  AP_FIB_OBJECTIVE_POTENTIAL 16200-16564 V). Three changes came out of that:
  * Preprocessing before scoring (metrics.preprocess): auto-detect and crop the
    SmartSEM databar (static text otherwise dilutes the score), then low-pass
    filter. Shot noise adds a focus-independent pedestal to gradient metrics; on
    real frames raw peak contrast was only 0.087 (slow dwell) / 0.021 (fast dwell).
    Pre-smoothing lifts these to 0.46 / 0.25 WITHOUT moving the peak.
    presmooth_frac is a fraction of image WIDTH (scale-invariant); default 0.004.
  * min_contrast default raised 0.05 -> 0.15 (the old value would have accepted
    noise on the fast-dwell set and only barely passed the good one).
  * Voltage scale defaults recalibrated to the real instrument: focus ~16.4 kV,
    sweep half-range 150 V (was a meaningless 6 V).
- Finding: for focusing, LOWER resolution + SLOWER dwell (512x384) beats higher
  resolution + fast dwell (1024x768): ~1.8x peak contrast and ~3.7x less noise.
  Focus metrics are SNR-limited, not resolution-limited, at this magnification.
- New regression tests replay the measured real focus curves
  (tests/data/real_fib_curves.json): python -m smartfib_executor.autofocus.tests.test_real_data

New in pyFIB v0.5.0:
- FIB autofocus fork module: smartfib_executor.autofocus (standalone, NOT yet wired
  into the mill/positions/UI workflow). Sweeps AP_FIB_OBJECTIVE_POTENTIAL around the
  last best-known focus, grabs an image per voltage, scores sharpness, and sets the
  optimum (coarse sweep -> parabola refine, with dose/frame budget and a confidence
  guard). Layers: metrics / sweep / search (all offline-tested via a simulator) and
  sources (ScopeImageSource is the only instrument-touching part; instrument-untested).
  Try it: python -m smartfib_executor.autofocus --simulate --true-focus 30 --start 24
  Tests: python -m smartfib_executor.autofocus.tests.test_autofocus

New in pyFIB v0.4.14:
- Session safety: the UI launches a fresh, empty, timestamped positions file
  (positions/session_<timestamp>/positions.json) instead of silently adopting an
  existing positions.json. Loading a previous file is now an explicit, warned action
  ("Session > Load Previous..." in the Positions tab). CLI defaults are unchanged.
"""

from .batch import mill_positions_sequential, natural_position_sort
from .pattern_ely import ELayoutPattern
from .positions import PositionDatabase, PositionRecord, StageAt, capture_position, recall_position
from .runner import PatternJob, PatternRunner
from .smartsem_com import SmartSEM, SmartSEMError

__all__ = [
    "SmartSEM",
    "SmartSEMError",
    "ELayoutPattern",
    "PatternJob",
    "PatternRunner",
    "PositionDatabase",
    "PositionRecord",
    "StageAt",
    "capture_position",
    "recall_position",
    "natural_position_sort",
    "mill_positions_sequential",
]

__version__ = "0.51.2"
