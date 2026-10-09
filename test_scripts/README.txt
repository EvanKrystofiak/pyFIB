Instrument test helpers. Run from a command prompt in the pyFIB folder
(the one containing smartfib_executor\).

STAGE_REFINE_TEST.txt   step-by-step stage refine test (Part A: CLI, Part B: in a batch)
SEM_AUTOFOCUS_TEST.txt  pyFIB SEM autofocus test (dry run, then 3 armed checks)
COARSE_REFINE_TEST.txt  800x coarse refine test (scale check, 30 um decoy, plan, live)
REPORT_TEST.txt         session report, run log + after-mill image check
TOOLS_TAB_TEST.txt      UI Tools tab: report, SEM autofocus, FIB autofocus
TILT_LIMIT_TEST.txt     mill batches: 2 deg tilt limit, read-only check, skip OK
SEM_FOCUS_CURVE_TEST.txt  SEM focus curves at low mag / 2000x and several scan speeds
SEM_AUTOFOCUS_TWO_STAGE_TEST.txt  two-stage SEM autofocus: speed, accuracy, far starts
MILL_STAGES_TEST.txt    Rough Mill / Prepolish / Polish: stage patterns, after images, manual buttons, push without milling
api_check.py            read-only API check:  python test_scripts\api_check.py

T06  refine stage correction  -- run in this order, stop if any step says FAIL:

  1) Dry run: guards + planned offsets. Nothing moves, nothing is imaged.
       python -m smartfib_executor.refine.offset_test

  2) Plan check: inject a known offset (X then Y), check refine's PLAN reverses it,
     restore stage + beam shift. Refine itself moves nothing. Asks to confirm.
       python -m smartfib_executor.refine.offset_test --arm

  3) Live: the plan check, then (only if the plan passed) the real correction.
     Asks to confirm.
       python -m smartfib_executor.refine.offset_test --arm --live

  Results + images: refine_offset_test_<time>\ in the pyFIB folder. Bring back
  results.json and the PNGs.

  Options (add to any command):
       --axes x                one axis only
       --offset-x-um 16        choose the X offset (same for --offset-y-um)

T07  aliasing guard -- must come back "no confident measurement":
       python -m smartfib_executor.refine.offset_test --arm --axes x --offset-x-um 30

Offline rehearsal (no microscope):
       python -m smartfib_executor.refine.offset_test --simulate --live
