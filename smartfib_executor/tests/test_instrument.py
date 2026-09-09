"""Tests for the reconciled SEM foundation (interface + shared PyfibMockSEM)."""

from __future__ import annotations

from smartfib_executor.instrument import PyfibMockSEM, SmartSEMInterface
from smartfib_executor.smartsem_com import SmartSEM


def test_smartsem_implements_the_interface_surface():
    for m in ("get_analogue", "get_digital", "get_digital_text", "set_analogue",
              "set_digital", "execute", "move_stage", "get_stage_position",
              "initialise", "close"):
        assert callable(getattr(SmartSEM, m, None)), m


def test_mock_is_a_smartsem_interface_with_both_surfaces():
    m = PyfibMockSEM(eht_v=2000.0, wd_m=0.0053, stage_z_m=0.030)
    assert isinstance(m, SmartSEMInterface)
    # interface surface
    assert m.get_analogue("AP_ACTUALKV") == 2000.0
    assert m.get_digital("DP_FROZEN") == 1
    assert m.get_digital_text("DP_FROZEN") == "Frozen"
    # pyFIB-legacy compat surface
    assert m.get_float("AP_ACTUALKV") == 2000.0
    assert m.get_float("DP_FROZEN") == 1.0
    assert m.get_str("DP_FROZEN") == "Frozen"


def test_mock_covers_pyfib_mnemonics_and_commands():
    m = PyfibMockSEM(fib_valve=1, column_valve=1, scan_rate=4,
                     fib_probe_idx=6, fib_probe_a=5e-11)
    m.execute("CMD_EHT_OFF"); assert m.get_analogue("AP_ACTUALKV") == 0.0
    m.execute("CMD_FIB_GUN_VALVE_CLOSE"); assert m.get_digital("DP_FIB_GUN_VALVE") == 0
    m.execute("CMD_CLOSE_COL_CHAMBER_VALVE"); assert m.get_digital("DP_COLUMN_CHAMBER_VALVE") == 0
    m.execute("CMD_SCANRATE8"); assert m.get_digital("DP_SCANRATE") == 8
    assert m.get_digital("DP_FIB_PROBE") == 6
    assert abs(m.get_analogue("AP_FIB_PROBE_CURRENT") - 5e-11) < 1e-18


def test_mock_wd_z_coupling_is_instant():
    m = PyfibMockSEM(wd_m=0.0053, stage_z_m=0.030)
    wd0 = m.get_analogue("AP_WD")
    m.move_stage(0, 0, 0.031, 0, 0, 0)          # +1 mm Z
    assert abs(m.get_analogue("AP_WD") - (wd0 - 1e-3)) < 1e-9   # -1 mm WD
    assert m.get_analogue("AP_STAGE_AT_Z") == 0.031
    assert m.get_digital("DP_STAGE_IS") == 0     # idle immediately after the snap


def test_mock_records_explicit_sets_only():
    m = PyfibMockSEM()
    assert m.sets == {}                          # pre-populated state is NOT "sets"
    m.set_analogue("AP_MANUALKV", 2000.0)
    m.set_analogue("AP_IPROBE", 20e-12)
    assert m.sets == {"AP_MANUALKV": 2000.0, "AP_IPROBE": 20e-12}


def test_grab_writes_a_file():
    import tempfile, os
    m = PyfibMockSEM()
    p = os.path.join(tempfile.mkdtemp(), "x.bmp")
    m.grab_bmp(p, width=32, height=24)
    assert os.path.exists(p) and p in m.grabbed


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
