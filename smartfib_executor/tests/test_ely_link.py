"""Tests for the pyFIB<->elymaker linking (build job, pick up markers, verify hash)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..positions import PositionDatabase, PositionRecord, StageAt
from .. import ely_link


def _db_with_image(with_geometry=True):
    d = Path(tempfile.mkdtemp()); (d / "images").mkdir()
    try:
        from PIL import Image
        Image.new("L", (1024, 768), 128).save(d / "images" / "s1.bmp")
    except Exception:
        (d / "images" / "s1.bmp").write_bytes(b"BM")
    db = PositionDatabase()
    kw = dict(name="s1", captured_unix_s=1.0, fib_magnification_x=2000.0,
              fib_objective_potential_v=16400.0,
              stage_at=StageAt(0, 0, 0, 0, 0, None), image_bmp="images/s1.bmp",
              image_width_px=1024, image_height_px=768, fov_width_m=5.715e-5)
    if with_geometry:
        kw["fib_pixel_size_m"] = 5.58e-8
    db.positions["s1"] = PositionRecord(**kw)
    pf = d / "positions.json"; db.save(pf)
    return db, str(pf)


def _author(kw):
    from elymaker.handoff import HandoffJob, run_job
    run_job(HandoffJob(image=kw["image_path"], pixel_size_m=kw["pixel_size_um"] / 1e6,
                       out=kw["out_path"], fov_width_m=5.715e-5,
                       image_width_px=1024, image_height_px=768, site=kw["site"]),
            write_marker=True)


def test_build_handoff_kwargs_from_v2_position():
    db, pf = _db_with_image()
    kw = ely_link.build_handoff_kwargs(db.positions["s1"], pf)
    assert kw["site"] == "s1"
    assert abs(kw["pixel_size_um"] - 0.0558) < 1e-6
    assert kw["out_path"].replace("\\", "/").endswith("patterns/s1.ely")
    assert abs(kw["fov_width_um"] - 57.15) < 1e-3


def test_build_handoff_refuses_v1_without_pixel_size():
    db, pf = _db_with_image(with_geometry=False)
    try:
        ely_link.build_handoff_kwargs(db.positions["s1"], pf)
        raise AssertionError("should refuse without fib_pixel_size_m")
    except ValueError as e:
        assert "pixel_size" in str(e)


def test_link_sets_ely_pattern_from_marker():
    db, pf = _db_with_image()
    kw = ely_link.build_handoff_kwargs(db.positions["s1"], pf)
    _author(kw)
    summary = ely_link.link_patterns_to_positions(db, pf)
    assert summary["linked"] == ["s1"] and not summary["stale"]
    assert db.positions["s1"].ely_pattern.replace("\\", "/") == "patterns/s1.ely"


def test_edited_ely_flagged_stale_on_link_and_verify():
    db, pf = _db_with_image()
    kw = ely_link.build_handoff_kwargs(db.positions["s1"], pf)
    _author(kw)
    assert ely_link.verify_ely_against_marker(kw["out_path"]) is True
    with open(kw["out_path"], "a") as fh:
        fh.write("\n<!-- edited -->")
    assert ely_link.verify_ely_against_marker(kw["out_path"]) is False
    summary = ely_link.link_patterns_to_positions(db, pf)
    assert "s1" in summary["stale"]


def test_verify_returns_none_without_marker():
    d = Path(tempfile.mkdtemp())
    ely = d / "x.ely"; ely.write_text("<pattern/>")
    assert ely_link.verify_ely_against_marker(str(ely)) is None


def test_unmatched_marker_reported():
    db, pf = _db_with_image()
    kw = ely_link.build_handoff_kwargs(db.positions["s1"], pf)
    kw["site"] = "ghost"; kw["out_path"] = kw["out_path"].replace("s1.ely", "ghost.ely")
    _author(kw)
    summary = ely_link.link_patterns_to_positions(db, pf)
    assert "ghost" in summary["unmatched"]


# --- per-position pattern override (authored overrides the standard) --- #
def test_authored_pattern_overrides_standard():
    from ..batch import _resolve_position_ely
    d = Path(tempfile.mkdtemp()); (d / "patterns").mkdir()
    (d / "patterns" / "s1.ely").write_text("<authored/>")
    default = Path("/std/standard.ely")

    def rec(name, ely):
        return PositionRecord(name=name, captured_unix_s=1.0, fib_magnification_x=2000.0,
                              fib_objective_potential_v=16400.0,
                              stage_at=StageAt(0, 0, 0, 0, 0, None), ely_pattern=ely)

    p, src, _ = _resolve_position_ely(rec("s1", "patterns/s1.ely"), default, ref_base_dir=d)
    assert src == "authored" and Path(p).name == "s1.ely"
    p, src, _ = _resolve_position_ely(rec("s2", None), default, ref_base_dir=d)
    assert src == "standard" and Path(p).name == "standard.ely"
    p, src, msg = _resolve_position_ely(rec("s3", "patterns/gone.ely"), default, ref_base_dir=d)
    assert src == "authored_missing" and Path(p).name == "standard.ely" and "WARNING" in msg


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
