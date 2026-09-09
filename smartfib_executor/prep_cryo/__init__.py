"""Prep CryoFIBSEM: ready the FIB for sample insertion (isolated, dry-run-first).

    python -m smartfib_executor.prep_cryo --positions-file positions.json           # dry-run
    python -m smartfib_executor.prep_cryo --positions-file positions.json --arm      # execute
    python -m smartfib_executor.prep_cryo --simulate                                 # offline demo

Turns EHT off (waits for off), closes the column valve, closes the FIB gun valve if
open (FIB stays on), sets the SEM to 2.00 keV / 20 pA, moves the stage to Cryo
Insertion via CLEM SAFE with the required axis ordering, and reports the FIB probe.
Dry-run unless --arm.
"""
from __future__ import annotations

from .steps import (run_prep_cryo, PrepResult, SEM_TARGET_KV_V,
                    SEM_TARGET_CURRENT_A, CLEM_SAFE_NAME, CRYO_INSERTION_NAME)
from .scope import PrepScope, SimulatedPrepSem

__all__ = ["run_prep_cryo", "PrepResult", "PrepScope", "SimulatedPrepSem",
           "SEM_TARGET_KV_V", "SEM_TARGET_CURRENT_A", "CLEM_SAFE_NAME",
           "CRYO_INSERTION_NAME"]
