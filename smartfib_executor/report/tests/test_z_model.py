"""Stage Z on the positions map, stage layout and mosaic (v0.45.0).

Evan 2026-10-05: "include Z". At the milling tilt a stage Z change moves the site under
the FIB like a stage Y move, so positions at another Z are drawn at an equivalent stage Y
(plus a little X; map_export.apply_z_model). The two instrument tests below are the measurements the
factor came from (session_20261001_170335, tilt 1.97 deg, R 12 deg; positions 5-7 were
captured 204 um higher than 8-25), kept in data/z_model_20261001.json:

  * SEM overview normal to the grid, lamella centres: with Z, an affine fitted to 8-25
    puts 5-7 on their lamellae; X/Y alone puts them ~1.1 mm away.
  * 800x FIB tiles: 11 overlaps across the Z step agree with both factors to < 2 um.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from .. import map_export as ME

DATA = Path(__file__).parent / "data" / "z_model_20261001.json"
T, R = 1.97, 12.0


def _fx():
    return json.loads(DATA.read_text())


def _rows(stage, names, t=T, r=R):
    return [(f"position_{n}", *stage[str(n)], t, r) for n in names]


def test_rules_of_the_z_model():
    same = [("a", 43.6, 56.8, 31.7, T, R), ("b", 43.7, 56.9, 31.7004, T, R)]
    y, info = ME.apply_z_model(same)                        # 0.4 um apart: one Z
    assert y == {"a": (43.6, 56.8), "b": (43.7, 56.9)}
    assert not info["applied"] and not info["unmodelled"]
    pts = [("a", 43.6, 56.8, 31.7, T, R), ("b", 43.6, 56.8, 31.7, T, R),
           ("c", 43.6, 56.8, 31.904, T, R)]
    y, info = ME.apply_z_model(pts)
    assert info["applied"] and info["z_ref_mm"] == 31.7
    assert abs(y["c"][1] - (56.8 + ME.Z_TO_STAGE_Y * 0.204)) < 1e-9
    assert abs(y["c"][0] - (43.6 + ME.Z_TO_STAGE_X * 0.204)) < 1e-9 and y["a"] == (43.6, 56.8)
    dx, dy = info["shifted_um"]["c"]
    assert abs(dy - ME.Z_TO_STAGE_Y * 204) < 0.1 and abs(dx - ME.Z_TO_STAGE_X * 204) < 0.1
    assert -1.2 < ME.Z_TO_STAGE_Y < -1.05                   # measured: -1.130 FIB, -1.104 SEM
    assert 0.03 < ME.Z_TO_STAGE_X < 0.06                    # measured: +0.047 FIB, +0.049 SEM
    for bad, why in (((30.0, R), "tilt 30.00"), ((None, R), "no stage tilt"),
                     ((T, 0.0), "rotation 0.0")):
        q = [p[:4] + bad for p in pts]
        y, info = ME.apply_z_model(q)
        assert not info["applied"] and y["c"] == (43.6, 56.8), info
        assert why in info["reason"] and "c" in info["unmodelled"]
    y, info = ME.apply_z_model([("a", 43.6, 56.8, None, T, R)])
    assert not info["applied"] and info["reason"] == "no stage Z recorded"


def _overview_fit(fx, kx, ky):
    """Affine overview_um = A [x + kx dz, y + ky dz] + b on 8-25; residuals of 5-7 (um)."""
    import numpy as np
    st, ov, upp = fx["stage_mm_xyz"], fx["sem_overview"]["px"], fx["sem_overview"]["um_per_px"]
    zref = float(np.median([st[str(n)][2] for n in range(8, 26)]))

    def s(n):
        x, y, z = st[str(n)]
        return [(x + kx * (z - zref)) * 1e3, (y + ky * (z - zref)) * 1e3]
    fit = [n for n in range(8, 26) if str(n) in ov]
    A = np.c_[np.array([s(n) for n in fit]), np.ones(len(fit))]
    P = np.array([ov[str(n)] for n in fit]) * upp
    c, *_ = np.linalg.lstsq(A, P, rcond=None)
    pred = np.c_[np.array([s(n) for n in (5, 6, 7)]), np.ones(3)] @ c
    obs = np.array([ov[str(n)] for n in (5, 6, 7)]) * upp
    return c[:2].T, obs - pred


def test_sem_overview_puts_5_to_7_on_their_lamellae_only_with_z():
    import numpy as np
    fx = _fx()
    M, res = _overview_fit(fx, ME.Z_TO_STAGE_X, ME.Z_TO_STAGE_Y)
    # the overview's stage frame (measured): X 1:1, Y stretched ~4.85x along the grid
    assert abs(np.hypot(*M[:, 0]) - 1.005) < 0.02, M
    assert 4.6 < np.hypot(*M[:, 1]) < 5.1, M
    assert np.all(np.abs(res[:, 0]) < 3.0), res                # X: um on the grid
    assert np.all(np.abs(res[:, 1]) < 40.0), res               # Y: lamellae scatter +/-16 um
    _M0, res0 = _overview_fit(fx, 0.0, 0.0)                    # X/Y only: ~1.1 mm off
    assert np.all(np.abs(res0[:, 1]) > 1000.0), res0
    _M1, res1 = _overview_fit(fx, 0.0, ME.Z_TO_STAGE_Y)        # without the X term: ~10 um
    assert np.all(np.abs(res1[:, 0]) > 7.0), res1


def test_fib_800x_overlaps_across_the_z_step_agree_with_the_factor():
    import numpy as np
    fx = _fx()
    st, fp = fx["stage_mm_xyz"], fx["fib_800x_pairs"]
    sx = fp["stage_scale_xy"][0] * fp["stage_sign_xy"][0]
    sy = fp["stage_scale_xy"][1] * fp["stage_sign_xy"][1]
    assert len(fp["pairs"]) >= 10
    ks = []
    for p in fp["pairs"]:
        a, b = p["a"].split("_")[1], p["b"].split("_")[1]
        y, info = ME.apply_z_model(_rows(st, (a, b)))
        assert info["applied"]
        pv = sy * (y[p["b"]][1] - y[p["a"]][1]) * 1000.0      # FIB-view um
        pu = sx * (y[p["b"]][0] - y[p["a"]][0]) * 1000.0
        assert abs(pv - p["true_dv_um"]) < 2.0 and abs(pu - p["true_du_um"]) < 2.0, (p, pu, pv)
        raw = sy * (st[b][1] - st[a][1]) * 1000.0             # X/Y only
        assert abs(raw - p["true_dv_um"]) > 100.0, (p, raw)
        assert abs(sx * (st[b][0] - st[a][0]) * 1000.0 - p["true_du_um"]) > 5.0
        dz = st[b][2] - st[a][2]
        ks.append((p["true_dv_um"] / 1000.0 / sy - (st[b][1] - st[a][1])) / dz)
    assert abs(float(np.median(ks)) - ME.Z_TO_STAGE_Y) < 0.01, ks


def test_map_and_stage_layout_draw_other_z_at_the_equivalent_y():
    import tempfile
    d = Path(tempfile.mkdtemp())
    paths = ME.write_positions_map([("a", 43.6, 56.80, 31.7, T, R), ("b", 43.6, 56.80, 31.7, T, R),
                                    ("c", 43.6, 56.80, 31.904, T, R)], d / "m")
    meta = json.loads(paths["json"].read_text())
    rows = {r["name"]: r for r in meta["positions"]}
    assert meta["z_model"]["applied"] and rows["c"]["stage_y_mm"] == 56.8
    assert abs((rows["a"]["py"] - rows["c"]["py"]) - (-ME.Z_TO_STAGE_Y * 204)) < 0.05
    assert abs((rows["a"]["px"] - rows["c"]["px"]) - (ME.Z_TO_STAGE_X * 204)) < 0.05   # X LEFT
    assert paths["csv"].read_text().splitlines()[1].startswith("a,a,43.6,56.8,31.7,43.6,56.8,")
    # the report's stage layout and warnings
    from .test_report import _session, _build
    d2, pf, db, std = _session()
    rec = db.positions["position_2"]
    db.positions["position_2"] = dataclasses.replace(
        rec, stage_at=dataclasses.replace(rec.stage_at, z_m=rec.stage_at.z_m + 204e-6))
    db.save(pf)
    res = _build(d2, mosaic=False)
    h = res.path.read_text(encoding="utf-8")

    def cy(name):
        m = re.search(r'href="#pos-' + name + r'" class="pt"><title>([^<]*)</title>'
                      r'<circle cx="[\d.]+" cy="([\d.]+)"', h)
        return float(m.group(2)), m.group(1)
    y2, tip2 = cy("position_2")
    y1, _ = cy("position_1")
    assert y2 < y1 and "drawn at X" in tip2 and "Z 31.9040 mm" in tip2   # higher Z -> up
    assert "Stage Y (mm, at Z 31.7000)" in h
    assert not any("stage Z" in w for w in res.warnings)
    mp = json.loads(res.map_paths["json"].read_text())
    assert mp["z_model"]["applied"] and "position_2" in mp["z_model"]["shifted_um"]
    # a tilt where the factor was never measured: X/Y only, and the report says so
    for n in list(db.positions):
        r = db.positions[n]
        db.positions[n] = dataclasses.replace(r, stage_at=dataclasses.replace(r.stage_at, t_deg=30.0))
    db.save(pf)
    res = _build(d2, mosaic=False)
    assert any("different stage Z" in w and "position_2" in w for w in res.warnings), res.warnings
    assert "drawn at X" not in res.path.read_text(encoding="utf-8")


def test_mosaic_places_a_tile_at_another_z_where_its_content_is():
    import numpy as np
    from ..mosaic import build_mosaic
    from .test_mosaic import _session as mosaic_session, GRID
    ref_d, ref_items = mosaic_session(GRID)                    # all at one Z
    ref = build_mosaic(ref_items, ref_d / "m", scale=0.5)
    # same content, but tile 9 recorded 204 um higher with the stage Y that sees it there
    d, items = mosaic_session(GRID)
    name, rec, path = items[8]
    dz = 204e-6
    st = dataclasses.replace(rec.stage_at, z_m=rec.stage_at.z_m + dz,
                             x_m=rec.stage_at.x_m - ME.Z_TO_STAGE_X * dz,
                             y_m=rec.stage_at.y_m - ME.Z_TO_STAGE_Y * dz)
    items[8] = (name, dataclasses.replace(rec, stage_at=st), path)
    y, info = ME.apply_z_model([(n, r.stage_at.x_m * 1e3, r.stage_at.y_m * 1e3,
                                 r.stage_at.z_m * 1e3, r.stage_at.t_deg, r.stage_at.r_deg)
                                for n, r, _p in items])
    assert info["applied"]
    res = build_mosaic(items, d / "m", scale=0.5,
                       xy_eff_m={n: (v[0] * 1e-3, v[1] * 1e-3) for n, v in y.items()})
    off = np.subtract(res.centres[name], res.centres["position_1"])
    want = np.subtract(ref.centres[name], ref.centres["position_1"])
    assert np.hypot(*(off - want)) < 1.5, (off, want)
    assert not any("different stage Z" in w for w in res.warnings), res.warnings
    raw = build_mosaic(items, d / "m2", scale=0.5)              # X/Y only: far off, flagged
    off_raw = np.subtract(raw.centres[name], raw.centres["position_1"])
    assert np.hypot(*(off_raw - want)) > 50, (off_raw, want)
    assert any("different stage Z" in w for w in raw.warnings)
    meta = json.loads(res.json_path.read_text())
    t9 = [t for t in meta["tiles"] if t["name"] == name][0]
    assert abs(t9["stage_y_mm"] - st.y_m * 1e3) < 1e-6 and t9["placed_y_mm"] != t9["stage_y_mm"]
    assert abs(t9["stage_x_mm"] - st.x_m * 1e3) < 1e-6


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    bad = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"FAIL  {t.__name__}: {e!r}"[:600])
    print(f"\n{len(tests) - bad}/{len(tests)} passed")
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if _run_all() else 0)
