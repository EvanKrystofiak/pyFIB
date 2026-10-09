"""Tests for fiducial milling: forced pattern, side->file, fiducial_side recording."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..batch import _resolve_position_ely
from ..positions import PositionDatabase, PositionRecord, StageAt
from .. import fiducial as F


def _rec(**kw):
    base = dict(name="s1", captured_unix_s=1.0, fib_magnification_x=2000.0,
                fib_objective_potential_v=16400.0, stage_at=StageAt(0, 0, 0, 0, 0, None))
    base.update(kw)
    return PositionRecord(**base)


def test_force_pattern_bypasses_authored_lamella():
    d = Path(tempfile.mkdtemp()); (d / "patterns").mkdir()
    (d / "patterns" / "s1.ely").write_text("<lamella/>")
    rec = _rec(ely_pattern="patterns/s1.ely")
    fid = Path("/assets/fiducial_left.ely")
    p, src, _ = _resolve_position_ely(rec, fid, ref_base_dir=d, force_pattern=False)
    assert src == "authored"                       # a normal mill uses the lamella
    p, src, _ = _resolve_position_ely(rec, fid, ref_base_dir=d, force_pattern=True)
    assert src == "forced" and Path(p).name == "fiducial_left.ely"   # a fiducial never does


def test_side_maps_to_asset_file_and_rejects_bad_side():
    assert F.fiducial_ely_path("left").name == "fiducial_left.ely"
    assert F.fiducial_ely_path("right").name == "fiducial_right.ely"
    assert F.fiducial_ely_path("Left").name == "fiducial_left.ely"   # case-insensitive
    try:
        F.fiducial_ely_path("up"); raise AssertionError("should reject")
    except ValueError:
        pass


def test_fiducial_side_persists_in_positions_file():
    import dataclasses
    d = Path(tempfile.mkdtemp()); db = PositionDatabase()
    db.positions["s1"] = dataclasses.replace(_rec(), fiducial_side="right")
    pf = d / "positions.json"; db.save(pf)
    assert PositionDatabase.load(pf).positions["s1"].fiducial_side == "right"
    # a record without one loads as None (v1/v2 files)
    db2 = PositionDatabase(); db2.positions["s2"] = _rec(name="s2"); db2.save(pf)
    assert PositionDatabase.load(pf).positions["s2"].fiducial_side is None


def test_prototype_fiducials_are_present_and_offset_by_side():
    # The supplied prototypes ship in assets and encode their own left/right offset.
    assert F.missing_fiducials() == []
    l = F.fiducial_extent_um("left"); r = F.fiducial_extent_um("right")
    assert l[1] < 0 < r[0]                 # left entirely at -X, right entirely at +X


def test_fit_check_flags_high_mag():
    fits, margin, _ = F.fiducial_fit("right", 2000.0)
    assert fits and 0.0 < margin < 1.0     # the prototype only just fits at 2000x
    fits, margin, _ = F.fiducial_fit("right", 2500.0)
    assert not fits and margin < 0.0       # and does not fit at 2500x
    fits, margin, _ = F.fiducial_fit("left", 1500.0)
    assert fits and margin > 5.0


def test_capture_records_fiducial_side():
    """A fiducial milled BEFORE capture is recorded ON the capture (so the reference
    image is known to contain it)."""
    import os
    from ..positions import capture_position

    class _Sem:
        def set_value(self, m, v): pass
        def execute(self, c): pass
        def get_str(self, m): return ""
        def get_float(self, m):
            return {"AP_FIB_OBJECTIVE_POTENTIAL": 16400.0, "AP_HEIGHT": 4.286e-5}.get(m, 0.0)
        def grab_bmp(self, path, **k): Path(path).write_bytes(b"BM")
    d = tempfile.mkdtemp()
    r = capture_position(_Sem(), "p1", fib_magnification_x=2000.0,
                         image_bmp=os.path.join(d, "p1.bmp"), ensure_fib_image=False,
                         unfreeze_before_grab=False, freeze_after_grab=False,
                         fiducial_side="right")
    assert r.fiducial_side == "right"


# ------------------------- numbered fiducials (1-30) ------------------------- #
def test_numbered_fiducial_round_trips_through_elymaker():
    import json
    from elymaker.reader import read_ely
    src = json.loads((F.NUMBERS_DIR / "polygons_um.json").read_text())["numbers"]
    d = Path(tempfile.mkdtemp())
    for n, side in ((1, "left"), (8, "right"), (28, "left"), (30, "right")):
        path = F.number_fiducial_path(d, n, side)
        assert path.parent.name == "fiducials" and path.exists()
        shapes = read_ely(str(path)).structures[0].layer_refs[0].shapes
        assert len(shapes) == len(src[str(n)])                       # one polygon per segment
        cx, cy = F.side_centre_um(side)
        for sh, poly in zip(shapes, src[str(n)]):
            assert type(sh).__name__ == "Polygon"
            for (x, y), (x0, y0) in zip(sh.points, poly):
                assert abs(x - (x0 + cx)) < 1e-6 and abs(y - (y0 + cy)) < 1e-6
            assert sh.exposure.probe.name == "30kV:300pA"             # prototype's probe
            assert abs(sh.depth_um - 5.0) < 1e-6                       # prototype's depth
            assert (x < 0) if side == "left" else (x > 0)


def test_every_numbered_fiducial_fits_at_2000x():
    d = Path(tempfile.mkdtemp())
    for side in F.SIDES:
        for n in range(F.NUMBER_MIN, F.NUMBER_MAX + 1):
            fits, margin, _ = F.fiducial_fit(side, 2000.0,
                                             path=F.number_fiducial_path(d, n, side))
            assert fits and margin > 2.0, (n, side, margin)


def test_numbered_fiducial_rejects_out_of_range():
    for bad in (0, 31, -3):
        try:
            F.number_polygons(bad); raise AssertionError("should reject")
        except ValueError:
            pass


def test_write_all_cli_writes_sixty_files():
    d = Path(tempfile.mkdtemp())
    assert F._main(["--write-all", str(d)]) == 0
    assert len(list(d.glob("num_*_*.ely"))) == 60


def test_capture_records_fiducial_number_and_tilt_by_default():
    import os
    from ..positions import capture_position, PositionDatabase

    class _Sem:
        def set_value(self, m, v): pass
        def execute(self, c): pass
        def get_str(self, m): return "Frozen" if m == "DP_FROZEN" else ""
        def get_float(self, m):
            return {"AP_FIB_OBJECTIVE_POTENTIAL": 16400.0, "AP_HEIGHT": 4.286e-5,
                    "AP_STAGE_AT_T": 12.04}.get(m, 0.0)
        def grab_bmp(self, path, **k): Path(path).write_bytes(b"BM")
    d = tempfile.mkdtemp()
    r = capture_position(_Sem(), "position_7", fib_magnification_x=2000.0,
                         image_bmp=os.path.join(d, "p.bmp"), ensure_fib_image=False,
                         frame_wait_s=0.0, fiducial_side="left", fiducial_number=7)
    assert r.fiducial_side == "left" and r.fiducial_number == 7
    assert r.stage_at.t_deg == 12.04                    # tilt recorded by DEFAULT now
    db = PositionDatabase(); db.positions[r.name] = r
    pf = Path(d) / "positions.json"; db.save(pf)
    assert PositionDatabase.load(pf).positions["position_7"].fiducial_number == 7


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
