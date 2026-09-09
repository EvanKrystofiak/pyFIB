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

__version__ = "0.26.1"
