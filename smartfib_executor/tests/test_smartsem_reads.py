"""Regression: digital reads must capture the RETURNED value, not the stale VARIANT.

Models the real win32com behaviour that caused DP_AUTO_FUNCTION (and the GIS needles)
to read as 0/Idle: a single-arg Get returns the state NAME for a digital, and a
two-arg (primed) Get returns (rc, number) in the RETURN tuple while the VARIANT passed
in stays at its initial value. The read methods must use the return tuple.
"""
from __future__ import annotations

from smartfib_executor.smartsem_com import SmartSEM

# state number the instrument really holds for these DPs
_DP_VALUES = {"DP_AUTO_FUNCTION": 1, "DP_GIS_LOCATION": 1, "DP_STAGE_IS": 0}
_DP_NAMES = {"DP_AUTO_FUNCTION": "Focus", "DP_GIS_LOCATION": "Parked",
             "DP_STAGE_IS": "Idle"}


class _FakeApi:
    """Mimics the CZEMApi OCX Get semantics that broke the naive readback."""
    def Get(self, mnemonic, prime=None):
        if prime is None:
            # unprimed digital -> state NAME string; return (rc, value)
            return (0, _DP_NAMES.get(mnemonic, "0"))
        # primed (VT_R4 or float) -> state NUMBER, returned in the tuple; the passed
        # VARIANT object (if any) is deliberately NOT mutated (stays at its init value).
        return (0, float(_DP_VALUES.get(mnemonic, 0)))


def _sem_with_fake():
    sem = SmartSEM.__new__(SmartSEM)   # bypass __init__/connection
    sem.api = _FakeApi()
    return sem


def test_get_float_on_digital_returns_number_not_stale_zero():
    sem = _sem_with_fake()
    # DP_AUTO_FUNCTION really = 1 (Focus). The old code returned a stale 0.
    assert sem.get_float("DP_AUTO_FUNCTION") == 1.0
    assert sem.get_digital("DP_AUTO_FUNCTION") == 1


def test_get_digital_reads_needle_parked():
    sem = _sem_with_fake()
    assert sem.get_digital("DP_GIS_LOCATION") == 1     # Parked, not transit(0)


def test_get_str_on_digital_returns_name():
    sem = _sem_with_fake()
    assert sem.get_str("DP_AUTO_FUNCTION") == "Focus"


def test_idle_still_reads_zero():
    sem = _sem_with_fake()
    assert sem.get_digital("DP_STAGE_IS") == 0         # genuinely Idle


# ---- stage position: the v0.28.1 "x off by the whole target" bug ---- #
_REAL = {"AP_STAGE_AT_X": 0.04849654, "AP_STAGE_AT_Y": 0.04902275, "AP_STAGE_AT_Z": 0.03898821,
         "AP_STAGE_AT_T": 0.0, "AP_STAGE_AT_R": 316.434235, "AP_STAGE_AT_M": 0.005052}


class _StageApi(_FakeApi):
    """AP_STAGE_AT_* reads return the real position; GetStagePosition returns the
    values in the tuple but leaves the passed VARIANTs at their initial 0.0."""
    def Get(self, mnemonic, prime=None):
        if mnemonic in _REAL:
            return (0, _REAL[mnemonic])
        return super().Get(mnemonic, prime)
    def GetStagePosition(self, *variants):
        return (0,) + tuple(_REAL.values())     # returned; VARIANTs NOT mutated


def test_get_stage_position_reads_real_coordinates_not_stale_zeros():
    sem = _sem_with_fake(); sem.api = _StageApi()
    p = sem.get_stage_position()
    assert abs(p.x_m - 0.04849654) < 1e-12        # was 0.0 -> "x off by 0.0485"
    assert abs(p.rot_deg - 316.434235) < 1e-9
    assert abs(p.master_z_m - 0.005052) < 1e-12


def test_arrival_wait_confirms_at_real_position():
    """The exact failing recall: stage idle at the target; arrival must confirm."""
    sem = _sem_with_fake(); sem.api = _StageApi()
    sem._get_stage_is_code = lambda: 0
    import time as _t
    _sleep = _t.sleep; _t.sleep = lambda s: None
    try:
        sem.wait_stage_arrival(x_m=0.04849654, y_m=0.04902275, z_m=0.03898821,
                               r_deg=316.434235, m_m=0.005052, timeout_s=2.0)
    finally:
        _t.sleep = _sleep


def test_getstageposition_fallback_captures_returned_tuple():
    class _NoAP(_StageApi):
        def Get(self, mnemonic, prime=None):
            if mnemonic in _REAL:
                raise RuntimeError("AP_STAGE_AT_* unavailable")
            return super().Get(mnemonic, prime)
    sem = _sem_with_fake(); sem.api = _NoAP()
    # _call_ok needs to run the real path; give it a minimal error hook
    sem._get_last_error = lambda: None
    p = sem.get_stage_position()
    assert abs(p.x_m - 0.04849654) < 1e-12       # from the RETURN, not stale v.value


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
