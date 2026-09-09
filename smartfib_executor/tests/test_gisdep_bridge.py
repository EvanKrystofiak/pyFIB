"""Prove the pyFIB<->gisdep adapter maps calls faithfully and translates errors.

gisdep itself is vendored VERBATIM and its own 236-test suite proves it still works
(run: pytest gisdep_tests). These tests cover only the new bridge code.
"""

from __future__ import annotations

from smartfib_executor.smartsem_com import StagePosition, SmartSEMError as PyErr
from smartfib_executor.gisdep_adapter import PyfibSmartSEM, bridged_api
from gisdep.api.interface import SmartSEMInterface, SmartSEMError as GisErr


class _FakePyfibSEM:
    """Minimal stand-in for pyFIB SmartSEM; records calls."""
    def __init__(self, *, raise_on=None):
        self.calls = []
        self._raise_on = raise_on or set()
        self.values = {"AP_WD": 5.0e-3, "DP_STAGE_IS": 0.0}

    def _maybe_raise(self, tag):
        if tag in self._raise_on:
            raise PyErr("boom", code=1002)

    def get_float(self, m):
        self.calls.append(("get_float", m)); self._maybe_raise("get_float")
        return self.values.get(m, 0.0)

    def get_digital(self, m):
        self.calls.append(("get_digital", m)); self._maybe_raise("get_float")
        return int(round(float(self.values.get(m, 0.0))))

    def get_str(self, m):
        self.calls.append(("get_str", m)); self._maybe_raise("get_str")
        return "Idle"

    def set_value(self, m, v):
        self.calls.append(("set_value", m, v)); self._maybe_raise("set_value")

    def execute(self, c):
        self.calls.append(("execute", c)); self._maybe_raise("execute")

    def move_stage(self, x, y, z, t, r, m):
        self.calls.append(("move_stage", x, y, z, t, r, m))

    def get_stage_position(self):
        return StagePosition(1e-3, 2e-3, 3e-3, 0.0, 12.0, 12.99e-3)

    def connect_remoting(self): self.calls.append(("connect_remoting",))
    def connect_local(self, machine=""): self.calls.append(("connect_local", machine))
    def close(self): self.calls.append(("close",))


def test_adapter_is_a_smartsem_interface():
    assert isinstance(PyfibSmartSEM(_FakePyfibSEM()), SmartSEMInterface)


def test_read_mapping():
    sem = _FakePyfibSEM()
    api = bridged_api(sem)
    assert api.get_analogue("AP_WD") == 5.0e-3
    assert ("get_float", "AP_WD") in sem.calls
    # digital: pyFIB get_float(state number) -> int
    sem.values["DP_STAGE_IS"] = 0.0
    assert api.get_digital("DP_STAGE_IS") == 0
    assert api.get_digital_text("DP_STAGE_IS") == "Idle"
    assert ("get_str", "DP_STAGE_IS") in sem.calls


def test_write_and_action_mapping():
    sem = _FakePyfibSEM()
    api = bridged_api(sem)
    api.set_analogue("AP_MANUALKV", 2000.0)
    api.set_digital("DP_SPOT", "On")
    api.execute("CMD_EHT_OFF")
    api.move_stage(1e-3, 2e-3, 3e-3, 0.0, 12.0, 12.99e-3)
    assert ("set_value", "AP_MANUALKV", 2000.0) in sem.calls
    assert ("set_value", "DP_SPOT", "On") in sem.calls
    assert ("execute", "CMD_EHT_OFF") in sem.calls
    assert ("move_stage", 1e-3, 2e-3, 3e-3, 0.0, 12.0, 12.99e-3) in sem.calls


def test_stage_position_field_rename():
    api = bridged_api(_FakePyfibSEM())
    sp = api.get_stage_position()
    # gisdep StagePosition uses m_m for the pyFIB master_z_m value
    assert sp.m_m == 12.99e-3 and sp.rot_deg == 12.0 and sp.z_m == 3e-3


def test_pyfib_error_is_translated_to_gisdep_error():
    api = bridged_api(_FakePyfibSEM(raise_on={"get_float"}))
    raised = False
    try:
        api.get_analogue("AP_WD")
    except GisErr as e:            # gisdep's SmartSEMError, not pyFIB's
        raised = True
        assert e.code == 1002
    except PyErr:
        raise AssertionError("pyFIB error leaked through un-translated")
    assert raised


def test_initialise_uses_selected_connect_mode():
    sem = _FakePyfibSEM()
    bridged_api(sem, connect="remoting").initialise()
    assert ("connect_remoting",) in sem.calls
    sem2 = _FakePyfibSEM()
    bridged_api(sem2, connect="local", machine="SEM1").initialise()
    assert ("connect_local", "SEM1") in sem2.calls


def test_gisdep_function_accepts_the_adapter():
    """A real gisdep entry point must accept the adapter as its api."""
    from gisdep.gis.probe import GISProbe   # read-only gisdep routine
    sem = _FakePyfibSEM()
    api = bridged_api(sem)
    report = GISProbe().probe(api)          # sweeps candidate mnemonics read-only
    # The adapter was used to read from the (fake) instrument.
    assert any(c[0] in ("get_float", "get_str") for c in sem.calls)
    assert report is not None


def test_bridge_reads_digital_via_forced_vt_r4_not_get_float():
    """REGRESSION: GIS needles read as transit(0) through the bridge because get_float
    short-circuited to a wrong numeric for these DPs. The bridge must use the reliable
    forced-VT_R4 get_digital instead."""
    from gisdep.gis.state import DP_GIS1_NEEDLE, NEEDLE_PARKED

    class _SplitReadSEM(_FakePyfibSEM):
        # get_float returns the BUGGY value (0) for the needle DP; get_digital returns
        # the TRUE state (1 = Parked) via the forced-VT_R4 path.
        def get_float(self, m):
            self.calls.append(("get_float", m))
            if m == DP_GIS1_NEEDLE:
                return 0.0            # the wrong value the buggy path produced
            return super().get_float(m)
        def get_digital(self, m):
            self.calls.append(("get_digital", m))
            if m == DP_GIS1_NEEDLE:
                return NEEDLE_PARKED  # 1 -- the real, reliably-read state
            return int(round(super().get_float(m)))

    sem = _SplitReadSEM()
    api = bridged_api(sem)
    assert api.get_digital(DP_GIS1_NEEDLE) == NEEDLE_PARKED   # not 0/transit
    # and it went through get_digital, not get_float, for this read
    assert ("get_digital", DP_GIS1_NEEDLE) in sem.calls


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
