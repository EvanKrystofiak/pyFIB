"""Tests for the gis_run entry (dispatch + arg building) and its adapter path."""
from __future__ import annotations

from smartfib_executor.gis_run import build_parser
from smartfib_executor.instrument import PyfibMockSEM
from smartfib_executor.gisdep_adapter import PyfibSmartSEM


def test_parser_modes_and_defaults():
    for mode in ("probe", "deposit", "full-run"):
        ns = build_parser().parse_args([mode])
        assert ns.mode == mode and ns.port == 1 and ns.dwell == 20.0
    ns = build_parser().parse_args(["deposit", "--port", "2", "--dwell", "30", "--arm"])
    assert ns.port == 2 and ns.dwell == 30.0 and ns.arm is True


def test_probe_runs_through_adapter_over_shared_mock():
    from gisdep.gis.probe import GISProbe
    sem = PyfibMockSEM()
    api = PyfibSmartSEM(sem)
    report = GISProbe().probe(api)          # gisdep read-only routine via the bridge
    assert report is not None


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
