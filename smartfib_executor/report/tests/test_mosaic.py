"""800x mosaic (v0.43.0): placement by stage position, refitted from the overlaps.

Synthetic tiles are cut from one textured "sample" at offsets given by a KNOWN stage ->
image matrix, so the test knows the true answer. The real-data result it mirrors
(session_20261001_170335, 21 tiles, 800x, tilt 1.97 deg): model seams 4.8 um median ->
fitted scale X 0.985 / Y 0.614, seams 0.92 um median (34 pairs).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ...positions import PositionRecord, StageAt
from ..mosaic import build_mosaic

TW, TH = 256, 192               # small tiles keep the test fast
MAG = 800.0


def _ps():
    from ... import fov
    return fov.fov_width_m(MAG) / TW


def _texture(w, h, seed=3):
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(h, w))
    k = np.ones(5) / 5                                  # mild blur: correlatable features
    a = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 1, a)
    a = np.apply_along_axis(lambda c: np.convolve(c, k, "same"), 0, a)
    a = (a - a.min()) / (a.max() - a.min()) * 255
    return a


def _session(stage_um, *, sx=0.95, sy=0.62, z_um=None, tilt=1.97):
    """Tiles at stage offsets (um); content cut with the TRUE matrix diag(-sx, +sy)/ps."""
    import numpy as np
    from PIL import Image
    d = Path(tempfile.mkdtemp())
    ps = _ps()
    tex = _texture(1400, 1000)
    c0 = np.array([700.0, 500.0])
    items = []
    for i, (xu, yu) in enumerate(stage_um):
        u = -sx * xu * 1e-6 / ps
        v = +sy * yu * 1e-6 / ps
        cx, cy = c0 + [u, v]
        x0, y0 = int(round(cx - TW / 2)), int(round(cy - TH / 2))
        tile = tex[y0:y0 + TH, x0:x0 + TW]
        p = d / f"p{i}_coarse.bmp"
        Image.fromarray(tile.astype("uint8")).save(p)
        z = 31.7e-3 + ((z_um or {}).get(i, 0.0)) * 1e-6
        rec = PositionRecord(name=f"position_{i + 1}", captured_unix_s=1.0,
                             fib_magnification_x=2000.0, fib_objective_potential_v=16400.0,
                             stage_at=StageAt(x_m=43.6e-3 + xu * 1e-6, y_m=56.8e-3 + yu * 1e-6,
                                              z_m=z, r_deg=12.0, m_m=0.0, t_deg=tilt),
                             coarse_magnification_x=MAG)
        items.append((rec.name, rec, p))
    return d, items


GRID = [(x, y) for y in (0, 40, 80) for x in (0, 50, 100)]     # 3x3, overlapping


def test_fit_recovers_the_true_scale_and_lines_up_the_seams():
    d, items = _session(GRID)
    res = build_mosaic(items, d / "m", scale=0.5)
    assert res.method == "fitted", res.warnings
    assert abs(res.stage_scale_used[0] - 0.95) < 0.015, res.stage_scale_used
    assert abs(res.stage_scale_used[1] - 0.62) < 0.015, res.stage_scale_used
    assert res.stage_scale_model == (0.912, 0.583)
    assert res.pairs >= 4 and res.seam_median_um < 0.5 * _ps() * 1e6 * 2   # ~1 px
    assert res.model_seam_median_um > res.seam_median_um


def test_orientation_x_left_y_down():
    d, items = _session([(0, 0), (60, 0), (0, 60)])
    res = build_mosaic(items, d / "m", fit=False)
    c = res.centres
    assert c["position_2"][0] < c["position_1"][0]          # larger stage X -> LEFT
    assert c["position_3"][1] > c["position_1"][1]          # larger stage Y -> DOWN
    assert res.method == "model"


def test_too_few_overlaps_uses_the_model():
    d, items = _session([(0, 0), (200, 0)])                 # no overlap at all
    res = build_mosaic(items, d / "m")
    assert res.method == "model" and res.pairs == 0
    assert any("confident overlaps" in w for w in res.warnings)


def test_different_z_is_flagged_and_left_out_of_the_fit():
    d, items = _session(GRID, z_um={8: 204.0})
    res = build_mosaic(items, d / "m", scale=0.5)
    assert any("position_9 (+204 um)" in w for w in res.warnings), res.warnings
    assert res.method == "fitted"


def test_files_and_unreadable_tile():
    d, items = _session(GRID[:3])
    bad = d / "missing.bmp"
    from ...positions import PositionRecord as PR
    items.append(("position_x", items[0][1], bad))
    res = build_mosaic(items, d / "report_x_mosaic_800x", label_of=lambda n: n[-1])
    assert any("position_x" in w for w in res.warnings)
    assert res.png_path.exists() and res.json_path.exists() and res.clean_jpeg
    meta = json.loads(res.json_path.read_text())
    assert meta["schema"] == "pyfib.mosaic_800x.v1" and len(meta["tiles"]) == 3
    assert "LEFT" in meta["frame"] and "DOWN" in meta["frame"]
    assert set(res.centres) == {"position_1", "position_2", "position_3"}


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
