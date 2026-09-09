"""Offline tests for positions schema v2 + backward-compatible load (Phase A/B)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..positions import (PositionDatabase, PositionRecord, StageAt,
                         SCHEMA_ID, SCHEMA_ID_V1)
from .. import fov as _fov


def _tmp() -> Path:
    return Path(tempfile.mkdtemp())


def _v2_record():
    return PositionRecord(
        name="p", captured_unix_s=2.0, fib_magnification_x=2000.0,
        fib_objective_potential_v=16400.0,
        stage_at=StageAt(1e-3, 2e-3, 3e-3, 0.0, 0.0, None),
        image_bmp="images/p.bmp",
        image_width_px=1024, image_height_px=768,
        fov_width_m=5.715e-5, fov_height_m=4.286e-5, fib_pixel_size_m=5.58e-8,
        fib_scan_rotation_deg=0.0, fib_beam_shift_x_m=0.0, fib_beam_shift_y_m=0.0,
        ely_pattern="patterns/p.ely", ap_fib_pixel_size_m=9.77e-8,
    )


def test_save_writes_v2_schema():
    d = _tmp(); p = d / "db.json"
    db = PositionDatabase(); db.positions["p"] = _v2_record(); db.save(p)
    assert json.load(open(p))["schema"] == SCHEMA_ID


def test_v2_round_trip_preserves_all_new_fields():
    d = _tmp(); p = d / "db.json"
    db = PositionDatabase(); db.positions["p"] = _v2_record(); db.save(p)
    r = PositionDatabase.load(p).positions["p"]
    assert r.image_width_px == 1024 and r.image_height_px == 768
    assert abs(r.fov_width_m - 5.715e-5) < 1e-12
    assert abs(r.fib_pixel_size_m - 5.58e-8) < 1e-12
    assert r.fib_scan_rotation_deg == 0.0
    assert r.ely_pattern == "patterns/p.ely"
    assert abs(r.ap_fib_pixel_size_m - 9.77e-8) < 1e-12


def test_v1_file_loads_with_new_fields_none():
    d = _tmp(); p = d / "v1.json"
    v1 = {"schema": SCHEMA_ID_V1, "created_unix_s": 1.0, "microscope_serial": "X",
          "positions": {"p1": {
              "name": "p1", "captured_unix_s": 1.0, "fib_magnification_x": 2000.0,
              "fib_objective_potential_v": 16400.0, "image_bmp": "a.bmp",
              "notes": None,
              "stage_at": {"x_m": 1e-3, "y_m": 2e-3, "z_m": 3e-3,
                           "r_deg": 0.0, "m_m": 0.0, "t_deg": None}}}}
    json.dump(v1, open(p, "w"))
    db = PositionDatabase.load(p)
    r = db.positions["p1"]
    # legacy fields intact
    assert r.fib_magnification_x == 2000.0 and r.image_bmp == "a.bmp"
    # v2 fields default to None
    for fname in ("image_width_px", "image_height_px", "fov_width_m", "fov_height_m",
                  "fib_pixel_size_m", "fib_scan_rotation_deg", "fib_beam_shift_x_m",
                  "fib_beam_shift_y_m", "ely_pattern", "ap_fib_pixel_size_m"):
        assert getattr(r, fname) is None, fname


def test_v1_upgrades_to_v2_in_memory_and_on_save():
    d = _tmp(); p = d / "v1.json"; q = d / "resaved.json"
    v1 = {"schema": SCHEMA_ID_V1, "created_unix_s": 1.0, "microscope_serial": None,
          "positions": {"p1": {
              "name": "p1", "captured_unix_s": 1.0, "fib_magnification_x": 2000.0,
              "fib_objective_potential_v": 16400.0, "image_bmp": None, "notes": None,
              "stage_at": {"x_m": 0.0, "y_m": 0.0, "z_m": 0.0,
                           "r_deg": 0.0, "m_m": 0.0, "t_deg": None}}}}
    json.dump(v1, open(p, "w"))
    db = PositionDatabase.load(p)
    assert db.schema == SCHEMA_ID
    db.save(q)
    assert json.load(open(q))["schema"] == SCHEMA_ID


def test_unknown_schema_still_rejected():
    d = _tmp(); p = d / "bad.json"
    json.dump({"schema": "smartfib_executor.positions.v999", "positions": {}},
              open(p, "w"))
    try:
        PositionDatabase.load(p)
        raise AssertionError("unknown schema should be rejected")
    except ValueError:
        pass


def test_shared_fov_matches_refine_geometry():
    from ..refine import geometry as g
    assert g.MAG_REF_WIDTH_M == _fov.MAG_REF_WIDTH_M
    assert g.fov_width_m(2000.0) == _fov.fov_width_m(2000.0)
    assert g.pixel_size_m(2000.0, 1024) == _fov.pixel_size_m(2000.0, 1024)


# ------------------------- Phase C: capture populates v2 fields ------------------------- #
class _CapSem:
    def __init__(self, fail=()):
        self.fail = set(fail)
    def set_value(self, m, v): pass
    def execute(self, c): pass
    def get_str(self, m): return ""
    def grab_bmp(self, path, **k):
        Path(path).write_bytes(b"BM")
    def get_float(self, m):
        if m in self.fail:
            raise RuntimeError("unavailable")
        return {"AP_STAGE_AT_X": 1e-3, "AP_STAGE_AT_Y": 2e-3, "AP_STAGE_AT_Z": 3e-3,
                "AP_STAGE_AT_R": 0.0, "AP_STAGE_AT_M": 0.0, "AP_STAGE_AT_T": 0.0,
                "AP_FIB_OBJECTIVE_POTENTIAL": 16400.0, "AP_FIB_WORKING_DISTANCE": 4.98e-3,
                "AP_HEIGHT": 4.286e-5, "AP_FIB_SCAN_ROTATION": 0.0,
                "AP_FIB_BEAM_SHIFT_X": 0.0, "AP_FIB_BEAM_SHIFT_Y": 0.0,
                "AP_FIB_PIXEL_SIZE": 9.77e-8}.get(m, 0.0)


def _capture(sem, name):
    from ..positions import capture_position
    d = _tmp()
    return capture_position(sem, name, fib_magnification_x=2000.0,
                            image_bmp=d / f"{name}.bmp", ensure_fib_image=False,
                            unfreeze_before_grab=False, freeze_after_grab=False)


def test_capture_populates_geometry_and_is_self_consistent():
    r = _capture(_CapSem(), "p")
    assert r.image_width_px == 1024 and r.image_height_px == 768
    assert abs(r.fov_width_m - _fov.fov_width_m(2000.0)) < 1e-15
    # pixel size derived from the grabbed width (store-independent).
    assert abs(r.fib_pixel_size_m - r.fov_width_m / 1024) < 1e-18
    # AP_HEIGHT used directly for fov_height when it reads sensibly.
    assert abs(r.fov_height_m - 4.286e-5) < 1e-12
    assert r.fib_scan_rotation_deg == 0.0 and r.fib_beam_shift_x_m == 0.0
    assert r.ap_fib_pixel_size_m is not None and r.ely_pattern is None


def test_capture_ap_height_fallback_to_square_pixels():
    r = _capture(_CapSem(fail=("AP_HEIGHT",)), "p")
    assert abs(r.fov_height_m - r.fov_width_m * 768 / 1024) < 1e-18


def test_capture_defensive_none_and_note_on_readback_failure():
    r = _capture(_CapSem(fail=("AP_FIB_SCAN_ROTATION", "AP_FIB_BEAM_SHIFT_X")), "p")
    assert r.fib_scan_rotation_deg is None and r.fib_beam_shift_x_m is None
    assert "SCAN_ROTATION unavailable" in (r.notes or "")
    # capture still succeeded with the rest populated
    assert r.image_width_px == 1024 and r.fib_pixel_size_m is not None


# ------------------------- Phase D: mill-time condition report (warn) ------------------------- #
class _MillSem:
    def __init__(self, rot=0.0, bx=0.0, by=0.0):
        self._v = {"AP_FIB_SCAN_ROTATION": rot, "AP_FIB_BEAM_SHIFT_X": bx,
                   "AP_FIB_BEAM_SHIFT_Y": by}
    def get_float(self, m):
        return self._v[m]


def _capture_lines(rec, sem):
    import io, contextlib
    from ..batch import _report_mill_conditions
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _report_mill_conditions(1, 1, rec, sem)
    return buf.getvalue()


def test_mill_report_lists_values_and_does_not_warn_on_refine_beamshift():
    r = PositionRecord(name="s", captured_unix_s=1.0, fib_magnification_x=2000.0,
                       fib_objective_potential_v=16400.0,
                       stage_at=StageAt(0, 0, 0, 0, 0, None),
                       fib_scan_rotation_deg=0.0, fib_beam_shift_x_m=0.0,
                       fib_beam_shift_y_m=0.0)
    out = _capture_lines(r, _MillSem(rot=0.0, bx=3.2e-6, by=-1.1e-6))
    assert "beam shift X" in out and "+3.2000 um" in out    # refine shift listed
    assert "WARNING" not in out                              # but not alarmed


def test_mill_report_warns_on_scan_rotation_drift():
    r = PositionRecord(name="s", captured_unix_s=1.0, fib_magnification_x=2000.0,
                       fib_objective_potential_v=16400.0,
                       stage_at=StageAt(0, 0, 0, 0, 0, None),
                       fib_scan_rotation_deg=0.0, fib_beam_shift_x_m=0.0,
                       fib_beam_shift_y_m=0.0)
    out = _capture_lines(r, _MillSem(rot=2.0))
    assert "WARNING" in out and "scan rotation" in out.lower()


def test_mill_report_handles_v1_record_without_recorded_values():
    r = PositionRecord(name="old", captured_unix_s=1.0, fib_magnification_x=2000.0,
                       fib_objective_potential_v=16400.0,
                       stage_at=StageAt(0, 0, 0, 0, 0, None))  # v2 fields all None
    out = _capture_lines(r, _MillSem(rot=0.0))
    assert "no recorded value" in out and "WARNING" not in out


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t(); print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1; print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1; print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
