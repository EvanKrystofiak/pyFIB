# pyFIB Handoff Guide

## Project summary
pyFIB is a Windows-only Python control and UI layer for a ZEISS Crossbeam 550 using the SmartSEM Remote API and SmartFIB pattern execution.

## Current stable behavior
- Capture a FIB image at the current stage location.
- Store stage position and FIB objective potential to a JSON position database.
- Mill a pre-made SmartFIB layout (.ely) at one or more saved positions.
- Sequential milling uses conservative completion detection.
- UI: dark black/gold themed desktop app with left navigation, queue view, live status, and editable confirm-done timeout.

## Important runtime facts discovered during testing
- Python target for the main machine: 3.9.13.
- SmartSEM FIB imaging values observed on the instrument:
  - DP_FIB_IMAGING = 0 -> SEM
  - DP_FIB_IMAGING = 1 -> FIB
  - DP_FIB_IMAGING = 3 -> SEM+FIB
  - DP_FIB_IMAGING = 4 -> Mill+SEM
- During milling, observed good signals were:
  - DP_FIB_MODE = 2 while milling
  - DP_FIB_MODE = 0 when done
  - DP_FIB_IMAGING = 4 while milling
  - DP_FIB_IMAGING = 0 when done
  - DP_PATTERNING_MODE = 3 while milling
  - DP_PATTERNING_MODE = 0 when done
- Brief blanked transitions during probe changes should be ignored.

## Milling completion logic intent
Completion should be conservative. The current logic should not advance to the next position until the done signature is stable for the configured confirmation window.

## Recommended next steps for another LLM
1. Keep the existing CLI names and UI wiring stable.
2. Preserve the pyfib module name as the public entry point.
3. Be careful with relative paths: the project should resolve relative paths from the pyFIB project directory.
4. If debugging milling completion, inspect:
   - DP_FIB_MODE
   - DP_FIB_IMAGING
   - DP_PATTERNING_MODE
   - DP_FIB_BLANKED
5. If capture behaves oddly, remember that `CMD_FIB_MODE_FIB` followed by `Grab` is the working FIB-image path on this instrument.

## Useful files
- `smartfib_executor/cli.py` - CLI entry points
- `smartfib_executor/gui.py` - Tk UI
- `smartfib_executor/smartsem_com.py` - COM wrapper
- `smartfib_executor/positions.py` - capture / store / recall logic
- `smartfib_executor/batch.py` - sequential milling logic
- `smartfib_executor/runner.py` - SmartFIB layout loading and milling execution
- `smartfib_executor/pattern_ely.py` - .ely parsing helpers

## Notes for future changes
- The UI should remain visually consistent with the black/gold theme.
- The Milling tab has a confirm-done box defaulting to 30 seconds.
- Keep ARM as a global safety toggle.
