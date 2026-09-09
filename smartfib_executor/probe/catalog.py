"""Catalog of SmartSEM mnemonics to probe, organised by CAPABILITY.

The naming is the uncertain part: several mnemonics reported as working elsewhere
are absent from this instrument's 2007 reference tables, and at least one that IS
absent from those tables (DP_PATTERNING_MODE) demonstrably works here. So each
capability lists several candidate names in preference order and the probe reports
which one actually resolves on THIS machine.

Priorities reflect what pyFIB would actually do with the value:
    1  fixes something currently inferred, guessed, or unsafe
    2  improves robustness of an existing fork
    3  worth recording, nothing depends on it yet
    0  already in use -- probed only as a health check
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Candidate:
    mnemonic: str
    kind: str            # 'AP' | 'DP' | 'SV' | 'CMD'
    in_manual: bool      # appears in the 2007 reference tables
    note: str = ""


@dataclass(frozen=True)
class Capability:
    key: str
    title: str
    why: str             # what pyFIB would do with it
    priority: int
    candidates: Tuple[Candidate, ...]


CAPABILITIES: Tuple[Capability, ...] = (
    # ---------------------------------------------------------------- priority 1
    Capability(
        key="fib_pixel_size",
        title="FIB pixel size / field of view",
        why=("Replaces MAG_REF_WIDTH_M=0.1143, a constant reverse-engineered from a "
             "databar (measured 1.2% off, 3% X/Y spread). It scales EVERY refine "
             "correction and sets the autofocus reduced-raster geometry that keeps "
             "the beam off the lamella."),
        priority=1,
        candidates=(
            Candidate("AP_FIB_PIXEL_SIZE", "AP", False, "reported working elsewhere"),
            Candidate("AP_IMAGE_PIXEL_SIZE", "AP", False, "reported working elsewhere"),
            Candidate("AP_PIXEL_SIZE", "AP", True, "in the manual (read-only)"),
            Candidate("AP_WIDTH", "AP", True,
                      "field-of-view WIDTH in metres -- the quantity fov_width_m() "
                      "currently infers; even more direct than a pixel size"),
            Candidate("AP_HEIGHT", "AP", True, "field-of-view height, completes the pair"),
        ),
    ),
    Capability(
        key="store_resolution",
        title="Image store resolution",
        why=("Screen width/height are currently ASSUMED 1024x768. If the store is "
             "512x384 (better for focusing) the reduced-raster rectangle is computed "
             "from wrong dimensions and could land on the target."),
        priority=1,
        candidates=(
            Candidate("DP_IMAGE_STORE", "DP", True, "table exists in pyFIB, never read"),
        ),
    ),
    Capability(
        key="fib_probe_current",
        title="FIB probe current (imaging vs milling)",
        why=("pyFIB tells the operator to use a low probe current but never sets or "
             "records it. Autofocus spends ~11 frames and refine 2-3; a defined "
             "imaging current (set then restored) is real dose control."),
        priority=1,
        candidates=(
            Candidate("AP_FIB_PROBE_CURRENT", "AP", False, "reported working elsewhere"),
            Candidate("DP_FIB_IMAGE_PROBE", "DP", True, "imaging probe, 0-15 enum"),
            Candidate("DP_FIB_PROBE", "DP", True, "current probe, 0-15 enum"),
            Candidate("DP_FIB_MILL_PROBE", "DP", True, "milling probe, 0-15 enum"),
            Candidate("AP_IPROBE", "AP", True,
                      "probe current in AMPERES -- a continuous value rather than the "
                      "0-15 enum, so preferable if it applies to the ion column here"),
        ),
    ),
    Capability(
        key="scan_rotation",
        title="Scan rotation",
        why=("SILENT BREAKAGE RISK. Both forks assume image axes are aligned with the "
             "beam-shift axes. If scan rotation is non-zero the autofocus raster-side "
             "geometry (which keeps the beam off the lamella) and the refine correction "
             "axes both rotate out from under us, with no error raised. The beam-shift "
             "calibration measured only 2-3% crosstalk, consistent with ~0 rotation -- "
             "but nothing currently VERIFIES that, or notices if an operator changes it."),
        priority=1,
        candidates=(
            Candidate("AP_FIB_SCAN_ROTATION", "AP", True,
                      "FIB scan rotation -- the one that matters for ion imaging"),
            Candidate("AP_SCANROTATION", "AP", True, "SEM/general scan rotation, degrees"),
            Candidate("DP_SCAN_ROT", "DP", True, "is scan rotation On or Off"),
        ),
    ),
    Capability(
        key="fib_dwell_scan_speed",
        title="FIB dwell time / scan speed (SNR control)",
        why=("The real correlation/focus SNR is ~12 (vs ~150 simulated). Longer dwell "
             "per pixel raises signal directly. We need to know whether this column "
             "exposes a continuous DWELL TIME (seconds/pixel) or a SCAN SPEED enum "
             "(index -> preset), and the exact name/units, so refine and autofocus can "
             "set a high imaging dwell for the diagnostic frames and restore it after. "
             "Milling dwell is untouched."),
        priority=1,
        candidates=(
            # continuous dwell time (seconds per pixel)
            Candidate("AP_FIB_DWELL_TIME", "AP", False, "FIB dwell, s/px if present"),
            Candidate("AP_DWELL_TIME", "AP", True, "dwell time, s/px"),
            Candidate("AP_PIXEL_DWELL", "AP", False, "alt. dwell name"),
            # scan-speed enum (index -> preset speed)
            Candidate("DP_FIB_SCANRATE", "DP", False, "FIB scan speed index"),
            Candidate("DP_SCANRATE", "DP", True, "scan speed index 0-15"),
            Candidate("DP_SCAN_SPEED", "DP", False, "alt. scan speed name"),
            # frame integration / averaging (a different way to lift SNR)
            Candidate("DP_FIB_FRAME_AVERAGE", "DP", False, "FIB frame averaging"),
            Candidate("DP_FRAME_INT", "DP", True, "frame integration count"),
            Candidate("DP_FRAME_AVERAGING", "DP", False, "frame averaging count"),
            Candidate("DP_NOISE_REDUCTION", "DP", True, "None/Pixel Avg/Frame Avg/..."),
            # line-level settings sometimes carry the effective dwell
            Candidate("AP_LINE_TIME", "AP", True, "line time, s"),
            Candidate("AP_FRAME_TIME", "AP", True, "frame time, s"),
        ),
    ),
    # ---------------------------------------------------------------- priority 2
    Capability(
        key="auto_brightness_contrast",
        title="Auto brightness/contrast + completion status",
        why=("Real correlation SNR was ~18 vs ~150 in simulation. A quick B/C before "
             "autofocus/refine should raise real signal. The STATUS parameter is "
             "required to wait for completion properly."),
        priority=2,
        candidates=(
            Candidate("DP_AUTO_FN_STATUS", "DP", False, "completion status"),
            Candidate("DP_AUTO_FUNCTION", "DP", True, "0=Idle..10=ABC"),
            Candidate("DP_AUTO_VIDEO", "DP", True, "Off/B/C/BC"),
        ),
    ),
    Capability(
        key="detector",
        title="Detector channel / type",
        why=("Cross-correlation compares a stored reference against a fresh image. If "
             "the detector changed between capture and recall the correlation degrades. "
             "Record at capture, verify at recall."),
        priority=2,
        candidates=(
            Candidate("DP_DETECTOR_CHANNEL", "DP", True, "Signal A, 0-7"),
            Candidate("DP_DETECTOR_TYPE", "DP", True, "None/INLENS/SE2/..."),
        ),
    ),
    # ---------------------------------------------------------------- priority 3
    Capability(
        key="fib_eht",
        title="FIB beam energy",
        why="Worth recording with each position for reproducibility; nothing needs it yet.",
        priority=3,
        candidates=(
            Candidate("AP_FIB_EHT_TARGET", "AP", True),
            Candidate("AP_FIB_EHT_ACTUAL", "AP", True, "read-only"),
        ),
    ),
    Capability(
        key="sem_column_state",
        title="SEM column state (working distance, EHT)",
        why=("Worth recording alongside each captured position for reproducibility. "
             "AP_WD is the electron column's focus proxy, the SEM analogue of the FIB "
             "objective potential pyFIB already stores."),
        priority=3,
        candidates=(
            Candidate("AP_WD", "AP", True, "working distance, metres"),
            Candidate("AP_ACTUALKV", "AP", True, "EHT actual, volts (read-only)"),
            Candidate("AP_MANUALKV", "AP", True, "EHT target, volts"),
        ),
    ),
    Capability(
        key="sem_beam_shift",
        title="SEM (electron) beam shift",
        why="Only needed if we add SEM-side or correlative alignment. Not used today.",
        priority=3,
        candidates=(
            Candidate("AP_BEAMSHIFT_X", "AP", True),
            Candidate("AP_BEAMSHIFT_Y", "AP", True),
        ),
    ),
    Capability(
        key="fib_emission",
        title="FIB emission current",
        why="Source health; useful context in diagnostic snapshots.",
        priority=3,
        candidates=(
            Candidate("AP_FIB_EMISSION_CURRENT", "AP", True, "read-only"),
        ),
    ),
    # ---------------------------------------------------------------- priority 0
    Capability(
        key="in_use_health_check",
        title="Already used by pyFIB (health check)",
        why="These already drive pyFIB. Probing them confirms the connection is sane.",
        priority=0,
        candidates=(
            Candidate("AP_FIB_MAGNIFICATION", "AP", True),
            Candidate("AP_FIB_BEAM_SHIFT_X", "AP", True),
            Candidate("AP_FIB_BEAM_SHIFT_Y", "AP", True),
            Candidate("DP_PATTERNING_MODE", "DP", False,
                      "NOT in the manual, yet works here -- proof that undocumented "
                      "mnemonics exist and that names cannot be assumed"),
            Candidate("DP_FIB_IMAGING", "DP", True),
            Candidate("DP_FIB_MODE", "DP", True),
            Candidate("DP_FROZEN", "DP", True),
            Candidate("SV_SERIAL_NUMBER", "SV", True),
            Candidate("SV_VERSION", "SV", True, "software version -- explains naming"),
        ),
    ),
)


# Commands cannot be probed read-only: the only way to test a CMD_ is to RUN it,
# which acts on the instrument. They are listed so the report is complete, and are
# never executed.
NOT_PROBEABLE: Tuple[Candidate, ...] = (
    Candidate("CMD_QUICK_BC", "CMD", False,
              "would trigger auto brightness/contrast"),
    Candidate("CMD_BEAM_ON", "CMD", True,
              "ELECTRON gun EHT On -- deliberately not automated by pyFIB"),
    Candidate("CMD_BEAM_OFF", "CMD", True,
              "ELECTRON gun Beam Off -- deliberately not automated by pyFIB"),
)


def all_candidates() -> Tuple[Candidate, ...]:
    out = []
    for cap in CAPABILITIES:
        out.extend(cap.candidates)
    return tuple(out)
