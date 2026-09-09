# GIS control-path probe protocol

Goal: determine how the GIS (needle insert/retract + precursor gas valve) is driven, so
the deposition sequence can be built against a real, confirmed path. Do this **with no
sample at risk** and, ideally, with the FIB/GIS in a safe state per your normal SOP.

## Step 1 — Read-only API sweep (safe)

Run the probe on the instrument. It only calls `Get()`; it cannot actuate anything.

```
python scripts/run_gis_probe.py --instrument
```

Record the output. Any line beginning `+` is a mnemonic the API recognises — a candidate
GIS parameter that the manual doesn't document. If you know other likely names, add them
to the `ANALOGUE_CANDIDATES` / `DIGITAL_CANDIDATES` lists in `gisdep/gis/probe.py` and
re-run.

> If nothing resolves (the likely outcome, matching the documented API), the API almost
> certainly has no GIS parameters and the macro path is the way forward.

## Step 2 — Check for commands by hand (do NOT auto-execute)

The probe prints candidate `CMD_` names. **Do not** try to execute them to test
existence — an unknown command could actuate hardware. Instead, open the SmartSEM
parameter / macro editor and look for:

- any command whose description mentions GIS, needle, injector, or gas;
- any existing **macro** (`MCF_`/`MCL_`) that inserts/retracts a needle or toggles a gas
  valve. Note its exact name and what it does.

## Step 3 — Report back what drives the GIS

For each action, note the mechanism (API command name, or macro name) and behaviour:

| Action | Mechanism (CMD_ / MCF_ / other) | Notes |
|--------|--------------------------------|-------|
| Insert needle, port 1 | | insertion time? readback? |
| Retract needle, port 1 | | |
| Open gas valve, port 1 | | |
| Close gas valve, port 1 | | |
| Insert needle, port 2 | | |
| Retract needle, port 2 | | |
| Open gas valve, port 2 | | |
| Close gas valve, port 2 | | |

Also useful:
- Is there any **position readback** (needle inserted/retracted sensor), or is it
  open-loop (fire-and-wait)?
- Roughly how long does an insert / retract take?
- Any **precursor per port** mapping (which chemistry on needle 1 vs 2)?
- Any prerequisite you check by hand today before inserting (vacuum ready, specific FIB
  mode, gun valve state)?

Each confirmed answer becomes a mock behaviour + regression test, and unblocks the
deposition state machine.

## Step 4 — Confirm the stage-tilt readback (safe, read-only)

gisdep gates GIS deposition on stage tilt (cryo window 29–31°; flat dev stage 0°) and
reads tilt via `GetStagePosition` (§4.10, documented as degrees). Confirm this on the
instrument so the gate is trustworthy:

1. With the **flat** stage at its home tilt, read the position and record the tilt
   value. Expected ~0.
2. With a stage **tilted to 30°** (or whatever your cryo value is), read again. Expected
   ~30 if the units are degrees; ~0.52 would indicate radians.

You can read it in Python (read-only):

```python
from gisdep.api.com import ComSmartSEM   # instrument only
api = ComSmartSEM(); api.initialise()
print(api.get_stage_position())          # note the tilt_deg field
api.close()
```

Report the two numbers. If they are ~0 and ~30, the degree convention is confirmed and
becomes a regression test. If they look like radians (or the `GetStagePosition` return
shape differs from what `gisdep/api/com.py` expects), we fix the backend and adjust the
gate accordingly.

## Step 5 — Capture the valve transit label (if it appears)

The GIS valve has a third state, integer `3`, seen live during open/close (`1 → 3 → 0`
on closing) and interpreted as "in transit or error." gisdep already handles it (the
backend polls for the target, so a transient `3` is fine and a persistent one trips the
timeout). One small thing still worth grabbing: the instrument's own TEXT label for
valve state `3`. In the verifier's initial snapshot the valve showed `text="Closed"`;
next time the valve sits at `3`, note the `text="..."` it reports so we can record the
real label (we currently only know the integer). Not required for correctness — the
logic keys off the integer — just nice to have for display/cross-check.

