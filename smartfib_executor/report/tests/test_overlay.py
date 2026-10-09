"""Overlay geometry: .ely layout -> report image pixels.

Instrument data (session_20261001_170335, captured with v0.39.0, 2000x, 55.81 nm/px):
position_6 and position_7 each had a numbered fiducial milled on the left ("Mill
fiducial here") before the reference image was captured, so the milled "6" and "7"
are in those images. The crops in tests/data hold that region. Drawn with the
recorded pixel size and the FOV-centred mapping, the .ely outlines land on the
milled digits; the best-matching offset measured 2026-10-03 was (+0.50, +0.17) um
for position_6 and (+0.33, +0.28) um for position_7 (milled features right of and
below the drawn pattern). The tests pin those measured offsets to +/-3 px (0.17 um),
which catches a 2 % scale error, and require the match to stay strong (a y mirror
drops it from ~100 to ~80).
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from ..overlay import layout_to_pixel, load_overlay, rect_corners_layout, layout_overlay

DATA = Path(__file__).resolve().parent / "data"


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_origin_is_fov_centre_and_y_up():
    # 1024 x 768 image, 0.1 um/px
    assert layout_to_pixel(0, 0, 0.1, 1024, 768) == (512.0, 384.0)
    px, py = layout_to_pixel(10.0, 5.0, 0.1, 1024, 768)
    assert _close(px, 612.0) and _close(py, 334.0)        # +x right, +y UP (smaller row)


def test_rect_anchor_is_corner_with_negative_height():
    # SmartFIB writes height negative: box spans y .. y+h (downwards) from the anchor.
    c = rect_corners_layout(-4.0, 1.0, 8.0, -2.0, 0.0)
    xs = sorted({round(p[0], 9) for p in c}); ys = sorted({round(p[1], 9) for p in c})
    assert xs == [-4.0, 4.0] and ys == [-1.0, 1.0], (xs, ys)


def test_rect_rotation_matches_elymaker_rectitem():
    """Same corners as elymaker's own RectItem (Qt), when PyQt5 is available."""
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from qtpy.QtWidgets import QApplication
        from elymaker.gui.items import RectItem
        from elymaker.gui.coords import scene_to_layout
        from elymaker.model import Rect
    except Exception:
        print("    (PyQt5/qtpy not available: RectItem cross-check skipped)")
        return
    app = QApplication.instance() or QApplication([])  # noqa: F841
    cases = [(-4.1, 0.47, 9.25, -0.27, 0.0), (2.0, -3.0, 5.0, -1.5, 30.0),
             (0.5, 0.5, 3.0, 2.0, 90.0), (-1.0, 2.0, 4.0, -6.0, 180.0),
             (3.0, 1.0, -2.0, -1.0, -45.0)]
    for x, y, w, h, a in cases:
        r = Rect(dose_factor=1.0, z_extent_m=1e-6, x=x, y=y, width=w, height=h, angle_deg=a)
        it = RectItem.from_model(r)
        local = it._local_rect()
        qt = []
        for pt in (local.topLeft(), local.topRight(), local.bottomRight(), local.bottomLeft()):
            sp = it.mapToScene(pt)
            qt.append(scene_to_layout(sp.x(), sp.y()))
        mine = rect_corners_layout(x, y, w, h, a)
        for q in qt:
            assert min(math.hypot(q[0] - m[0], q[1] - m[1]) for m in mine) < 1e-9, \
                (x, y, w, h, a, qt, mine)


def test_polygon_and_text_and_depth():
    from elymaker.model import Layout, Structure, LayerReference, Polygon, Text, Exposure, Probe
    lr = LayerReference(shapes=[
        Polygon(dose_factor=5, z_extent_m=5e-6, points=[(0, 0), (1, 0), (1, 1)],
                exposure=Exposure(file="x.epm", probe=Probe("30kV:300pA", 3e-10, 1.75e-7))),
        Text(dose_factor=2, z_extent_m=2e-6, x=0.0, y=-1.0, height=2.0, text="7"),
    ])
    lay = Layout(structures=[Structure(layer_refs=[lr])])
    ov = layout_overlay(lay, 0.1, 100, 100)
    poly, txt = ov.shapes
    assert poly.points_px == [(50.0, 50.0), (60.0, 50.0), (60.0, 40.0)], poly.points_px
    assert poly.probe == "30kV:300pA" and _close(poly.depth_um, 5.0)
    assert txt.kind == "text" and txt.points_px == [(50.0, 60.0)] and _close(txt.text_height_px, 20.0)
    assert any("approximate" in w for w in ov.warnings)


def test_missing_or_bad_pattern_never_raises(tmp=None):
    ov = load_overlay(DATA / "does_not_exist.ely", 0.05, 1024, 768)
    assert not ov.ok and "not found" in ov.warnings[0]
    bad = DATA.parent / "_bad_tmp.ely"
    bad.write_text("<NOT_ELAYOUT/>", encoding="utf-8")
    try:
        ov = load_overlay(bad, 0.05, 1024, 768)
        assert not ov.ok and ov.warnings
    finally:
        bad.unlink()
    ov = load_overlay(DATA / "num_06_left.ely", None, 1024, 768)
    assert not ov.ok and "pixel size" in ov.warnings[0]


def _best_offset(name):
    """Offset (px) that best puts the drawn digit on the milled (dark) digit."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    m = json.loads((DATA / "fiducial_check.json").read_text())[name]
    crop = Image.open(DATA / f"fiducial_{name}_crop.png").convert("L")
    a = np.asarray(crop, float)
    ov = load_overlay(DATA / m["ely"], m["pixel_size_um"], m["image_w"], m["image_h"])
    assert ov.ok, ov.warnings
    bx, by = m["crop_box"][:2]

    def score(dx, dy):
        mask = Image.new("L", crop.size, 0)
        d = ImageDraw.Draw(mask)
        for s in ov.shapes:
            d.polygon([(x - bx + dx, y - by + dy) for x, y in s.points_px], fill=255)
        mk = np.asarray(mask) > 0
        ring = (np.asarray(mask.filter(ImageFilter.MaxFilter(9))) > 0) & ~mk
        return a[ring].mean() - a[mk].mean()      # holes are dark: ring - inside > 0

    best = max((score(dx, dy), dx, dy) for dx in range(-24, 25, 3) for dy in range(-24, 25, 3))
    _, cx, cy = best
    best = max((score(dx, dy), dx, dy) for dx in range(cx - 3, cx + 4) for dy in range(cy - 3, cy + 4))
    return best, m["pixel_size_um"]


def _check_site(name):
    m = json.loads((DATA / "fiducial_check.json").read_text())[name]
    (c, dx, dy), ps = _best_offset(name)
    ex, ey = m["measured_offset_px"]
    assert c >= 90, f"match too weak ({c:.1f}); measured {m['measured_contrast']}"
    assert abs(dx - ex) <= 3 and abs(dy - ey) <= 3, \
        f"best offset ({dx}, {dy}) px, measured ({ex}, {ey}) px"
    assert abs(dx * ps) <= 0.67 and abs(dy * ps) <= 0.67, (dx * ps, dy * ps)


def test_instrument_fiducial_6_lands_on_milled_digit():
    _check_site("pos6")


def test_instrument_fiducial_7_lands_on_milled_digit():
    _check_site("pos7")


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
