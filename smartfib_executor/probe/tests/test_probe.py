"""Offline tests for the read-only mnemonic probe.

The most important assertions here are the SAFETY ones: this tool runs against a
live instrument with a sample loaded, so "it only reads" must be enforced and
proven, not merely intended.

Run:
    python -m smartfib_executor.probe.tests.test_probe
"""

from __future__ import annotations

from ..catalog import CAPABILITIES, NOT_PROBEABLE, all_candidates
from ..probe import (ABSENT, PRESENT, UNAVAILABLE, ProbeReport, ReadOnlySem,
                     ReadOnlyViolation, format_report, probe_one, run_probe)


class _Err(RuntimeError):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class FakeSem:
    """Minimal fake instrument; records every call it receives."""

    def __init__(self, present=None, unavailable=None):
        self.present = present if present is not None else {
            "AP_PIXEL_SIZE": ("= 55.82 nm", 5.582e-8),
            "SV_SERIAL_NUMBER": ("1550XB-01-02", None),
        }
        self.unavailable = unavailable or set()
        self.calls = []

    def get_str(self, m):
        self.calls.append(("get_str", m))
        if m in self.present:
            return self.present[m][0]
        if m in self.unavailable:
            raise _Err("digital read failed", code=1002)
        raise _Err("failed to translate parameter into an id", code=1000)

    def get_float(self, m):
        self.calls.append(("get_float", m))
        if m in self.present:
            v = self.present[m][1]
            if v is None:
                raise _Err("not analogue nor digital", code=1003)
            return v
        if m in self.unavailable:
            raise _Err("analogue read failed", code=1001)
        raise _Err("failed to translate parameter into an id", code=1000)

    # Methods the probe must NEVER touch.
    def set_value(self, *a, **k):
        raise AssertionError("set_value called -- probe is supposed to be read-only")

    def execute(self, *a, **k):
        raise AssertionError("execute called -- probe is supposed to be read-only")

    def move_stage(self, *a, **k):
        raise AssertionError("move_stage called -- probe is supposed to be read-only")

    def grab_bmp(self, *a, **k):
        raise AssertionError("grab_bmp called -- probe is supposed to be read-only")


# ----------------------------- SAFETY ----------------------------- #
def test_readonly_wrapper_blocks_writes():
    ro = ReadOnlySem(FakeSem())
    for forbidden in ("set_value", "execute", "move_stage", "grab_bmp",
                      "connect_local", "close"):
        try:
            getattr(ro, forbidden)
            raise AssertionError(f"{forbidden} was not blocked")
        except ReadOnlyViolation:
            pass


def test_readonly_wrapper_blocks_attribute_writes():
    ro = ReadOnlySem(FakeSem())
    try:
        ro.anything = 1
        raise AssertionError("attribute assignment was not blocked")
    except ReadOnlyViolation:
        pass


def test_readonly_wrapper_allows_reads():
    sem = FakeSem()
    ro = ReadOnlySem(sem)
    assert ro.get_str("AP_PIXEL_SIZE") == "= 55.82 nm"
    assert ro.get_float("AP_PIXEL_SIZE") == 5.582e-8


def test_full_run_touches_only_read_methods():
    """End-to-end: a whole probe must issue nothing but get_str/get_float."""
    sem = FakeSem()
    run_probe(sem)
    kinds = {c[0] for c in sem.calls}
    assert kinds <= {"get_str", "get_float"}, kinds
    assert sem.calls, "probe made no calls at all"


def test_commands_are_never_executed():
    """CMD_* must be reported, never run."""
    sem = FakeSem()
    report = run_probe(sem)
    probed = {c[1] for c in sem.calls}
    for cand in NOT_PROBEABLE:
        assert cand.mnemonic not in probed, f"{cand.mnemonic} was probed!"
    listed = {d["mnemonic"] for d in report.not_probeable}
    assert {c.mnemonic for c in NOT_PROBEABLE} == listed


# --------------------------- CLASSIFICATION --------------------------- #
def test_absent_vs_unavailable_are_distinguished():
    """'wrong name' and 'right name, not readable now' must not be conflated."""
    sem = FakeSem(present={"AP_PIXEL_SIZE": ("= 55.8 nm", 5.58e-8)},
                  unavailable={"AP_FIB_PIXEL_SIZE"})
    ro = ReadOnlySem(sem)
    cands = {c.mnemonic: c for c in all_candidates()}

    hit = probe_one(ro, cands["AP_PIXEL_SIZE"])
    assert hit.status == PRESENT, hit

    unavail = probe_one(ro, cands["AP_FIB_PIXEL_SIZE"])
    assert unavail.status == UNAVAILABLE, unavail
    assert unavail.error_code in (1001, 1002)

    missing = probe_one(ro, cands["AP_IMAGE_PIXEL_SIZE"])
    assert missing.status == ABSENT, missing
    assert missing.error_code == 1000


def test_string_form_captures_units():
    """AP_ string reads carry units -- essential for interpreting a pixel size."""
    sem = FakeSem(present={"AP_PIXEL_SIZE": ("= 55.82 nm", 5.582e-8)})
    r = probe_one(ReadOnlySem(sem), {c.mnemonic: c for c in all_candidates()}["AP_PIXEL_SIZE"])
    assert r.string_value and "nm" in r.string_value
    assert r.float_value == 5.582e-8


def test_string_only_parameter_still_counts_as_present():
    """SV_ has no float form; a 1003 on the float must not mark it absent."""
    sem = FakeSem(present={"SV_SERIAL_NUMBER": ("1550XB-01-02", None)})
    r = probe_one(ReadOnlySem(sem),
                  {c.mnemonic: c for c in all_candidates()}["SV_SERIAL_NUMBER"])
    assert r.status == PRESENT and r.string_value == "1550XB-01-02"


# ------------------------------ REPORTING ------------------------------ #
def test_capability_picks_first_working_candidate():
    sem = FakeSem(present={"AP_PIXEL_SIZE": ("= 55.8 nm", 5.58e-8)})
    report = run_probe(sem)
    cap = next(c for c in report.capabilities if c.key == "fib_pixel_size")
    assert cap.working is not None
    assert cap.working.mnemonic == "AP_PIXEL_SIZE"


def test_capability_reports_unavailable_when_nothing_resolves():
    report = run_probe(FakeSem(present={}))
    cap = next(c for c in report.capabilities if c.key == "fib_pixel_size")
    assert cap.working is None


def test_health_check_failure_is_surfaced():
    """pyFIB already depends on these; a failure must be shouted about."""
    text = format_report(run_probe(FakeSem(present={})))
    assert "health check FAILED" in text


def test_report_is_json_serialisable(tmp=None):
    import json, tempfile, os
    report = run_probe(FakeSem())
    d = tempfile.mkdtemp()
    path = os.path.join(d, "probe.json")
    report.save(path)
    back = json.loads(open(path).read())
    assert "capabilities" in back and back["capabilities"]
    assert back["serial"] == "1550XB-01-02"


def test_catalog_is_well_formed():
    seen = set()
    for cap in CAPABILITIES:
        assert cap.candidates, f"{cap.key} has no candidates"
        assert cap.priority in (0, 1, 2, 3)
        for c in cap.candidates:
            assert c.kind in ("AP", "DP", "SV")
            assert c.mnemonic not in seen, f"duplicate candidate {c.mnemonic}"
            seen.add(c.mnemonic)
    for c in NOT_PROBEABLE:
        assert c.kind == "CMD"


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
