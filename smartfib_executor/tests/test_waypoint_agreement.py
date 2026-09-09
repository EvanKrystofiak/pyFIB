"""Lock the agreement between gisdep's waypoints and pyFIB's setup positions.

They are the SAME instrument-validated coordinates (measured once). This test fails
if either source drifts, so 'full run' (which uses gisdep's waypoints) and the Setup
'Move to position' (which uses setup_positions.json) can't silently diverge.
"""
from __future__ import annotations

from gisdep.stage.cryo import DEFAULT_WAYPOINTS
from smartfib_executor.setup_move import load_setup_db

# gisdep name -> pyFIB setup_positions.json name (same physical position)
PAIRS = {
    "CLEM_SAFE": "CLEM SAFE",
    "GRID1": "Grid1 GIS Normal",
    "GRID2": "Grid2 GIS Normal",
}
TOL_M = 1e-6      # 1 um
TOL_DEG = 1e-3


def test_gisdep_waypoints_match_pyfib_setup_positions():
    db = load_setup_db()
    for gname, pname in PAIRS.items():
        assert gname in DEFAULT_WAYPOINTS, f"gisdep missing {gname}"
        assert pname in db.positions, f"pyFIB setup missing {pname}"
        w = DEFAULT_WAYPOINTS[gname]
        st = db.positions[pname].stage_at
        assert abs(w.x_m - st.x_m) <= TOL_M, f"{gname} X"
        assert abs(w.y_m - st.y_m) <= TOL_M, f"{gname} Y"
        assert abs(w.z_m - st.z_m) <= TOL_M, f"{gname} Z"
        assert abs(w.tilt_deg - st.t_deg) <= TOL_DEG, f"{gname} tilt"
        assert abs(w.rot_deg - st.r_deg) <= TOL_DEG, f"{gname} rot"


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
