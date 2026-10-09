"""Session report (v0.40.0): builds from files only and never touches the microscope."""

from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path

from ...positions import PositionDatabase, PositionRecord, StageAt
from ..runlog import RunLog

REPORT_DIR = Path(__file__).resolve().parents[1]

ELY = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<ELAYOUT version="2.0" locked="false" name="{name}">
  <VERSION created="01.10.2026 17:04:21" modified="01.10.2026 17:04:21" number="1.0"/>
  <AXES show="true"/>
  <GRID horizontal="1" show="true" snap_to="false" vertical="1"/>
  <LAYER_LIST><LAYER fill_color="#00FF00" fill_opacity="0.5" hidden="false" locked="false" name="Layer"></LAYER></LAYER_LIST>
  <STRUCTURE_LIST><STRUCTURE locked="false" name="Structure">
    <VERSION created="01.10.2026 17:04:21" modified="01.10.2026 17:04:21" number="1.0"/>
    <INSTANCE_LIST/>
    <LAYER_REFERENCE frame_cx="0" frame_cy="0" frame_size="6.4" ref="Layer">
      <RECT dose_factor="{d}" z_extent="{d}e-006 m" edge_sel_auto_scan_angle="true" height="-0.5" width="2" angle="0 deg" x="-1" y="{y}" imaging="false">
        <EXPOSURE version="2.1" file="D:\\FIB Recipes\\Vitriuos_Ice.epm">
        <PROBE name="30kV:300pA" type="specific" current="3e-010 A" diameter="1.75e-007 m"/>
        </EXPOSURE>
      </RECT>
    </LAYER_REFERENCE>
  </STRUCTURE></STRUCTURE_LIST>
</ELAYOUT>
"""


def _write_ely(path: Path, name="pat", depth=5, y=1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ELY.format(name=name, d=depth, y=y), encoding="utf-8")
    return path


def _bmp(path: Path, shade=128) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("L", (64, 48), shade).save(path)


def _rec(name, x_mm, *, image=True, authored=None, fid=None, coarse=False):
    return PositionRecord(
        name=name, captured_unix_s=1790899257.0 + x_mm, fib_magnification_x=2000.0,
        fib_objective_potential_v=16400.0,
        stage_at=StageAt(x_m=x_mm * 1e-3, y_m=56.8e-3, z_m=31.7e-3, r_deg=12.0, m_m=0.013,
                         t_deg=2.0),
        image_bmp=(f"images\\{name}.bmp" if image else None),
        image_width_px=64, image_height_px=48, fov_width_m=6.4e-6, fov_height_m=1.07e-4,
        fib_pixel_size_m=1e-7, ely_pattern=authored,
        fiducial_side=(fid[1] if fid else None), fiducial_number=(fid[0] if fid else None),
        image_bmp_coarse=(f"images\\{name}_coarse.bmp" if coarse else None),
        coarse_magnification_x=(800.0 if coarse else None),
    )


def _session():
    d = Path(tempfile.mkdtemp()) / "session_20261001_170335"
    pf = d / "positions.json"
    db = PositionDatabase()
    db.positions["position_1"] = _rec("position_1", 43.60, authored="patterns\\position_1.ely",
                                      coarse=True)
    db.positions["position_2"] = _rec("position_2", 43.65, fid=(2, "left"))
    db.positions["position_10"] = _rec("position_10", 43.70)
    db.positions["position_3"] = _rec("position_3", 43.75, image=False)
    d.mkdir(parents=True)
    db.save(pf)
    for n in ("position_1", "position_2", "position_10"):
        _bmp(d / "images" / f"{n}.bmp")
    _bmp(d / "images" / "position_1_coarse.bmp", 90)
    _write_ely(d / "patterns" / "position_1.ely", "authored", depth=3)
    _write_ely(d / "fiducials" / "num_02_left.ely", "fid2", depth=5, y=-1.0)
    _write_ely(d / "fiducials" / "num_05_right.ely", "fid5")          # nobody records #5
    std = _write_ely(d.parent / "standard.ely", "standard", depth=8, y=0.25)
    return d, pf, db, std


def _log_run(d, pf, db, std, *, authored_changed=False):
    rl = RunLog(d, positions_file=str(pf), standard_pattern=str(std),
                positions=["position_1", "position_2"], printer=lambda m: None)
    rl.site_start("position_1", 1, 2, db.positions["position_1"])
    rl.site_pattern(d / "patterns" / "position_1.ely", "authored", "using authored pattern")
    rl.site_end("milled")
    rl.site_start("position_2", 1, 2, db.positions["position_2"])
    rl.site_end("error", "RuntimeError: stage did not arrive")
    rl.finish("completed")
    if authored_changed:
        _write_ely(d / "patterns" / "position_1.ely", "authored", depth=4)
    return rl


def _build(d, **kw):
    from ..html_report import build_report
    return build_report(d / "positions.json", clock=lambda: 1790950000.0, **kw)


def _sec(h, name):
    """One position's section of the report."""
    return re.search(r"id='pos-" + re.escape(name) + r"'.*?</section>", h, re.S).group(0)


def _line(h, name, label="Rough"):
    """The v0.51.1 one-line stage entry (status badge + pattern + residual) of a position."""
    for m in re.finditer(r'<div class="stl" title="([^"]*)">(.*?)</div>', _sec(h, name), re.S):
        if f"</i>{label}</span>" in m.group(2):
            return m.group(1) + " || " + m.group(2)
    return ""


def test_report_from_session_with_run_log():
    d, pf, db, std = _session()
    _log_run(d, pf, db, std)
    before = {p for p in d.rglob("*")}
    res = _build(d, standard_pattern=str(std))
    after = {p for p in d.rglob("*")}
    stem = res.path.stem + "_positions_map"
    expect = {res.path} | {d / f"{stem}.{k}" for k in ("png", "tif", "json", "csv")}
    assert after - before == expect, after - before    # report + pyCLEM map (mosaic is opt-in)
    assert res.path.parent == d and res.path.name.startswith("report_")
    # position_1 milled (logged), position_2 error, position_3 / position_10 no record
    assert res.positions == 4 and res.milled == 3 and res.assumed == 2
    h = res.path.read_text(encoding="utf-8")
    for n in ("position_1", "position_2", "position_3", "position_10"):
        assert f"id='pos-{n}'" in h
    # natural order: position_2 before position_10
    assert h.index("id='pos-position_2'") < h.index("id='pos-position_10'")
    assert h.count('href="data:image/jpeg;base64,') == 4   # 3 images + 1 coarse inset
    l1 = _line(h, "position_1")
    assert "s-milled" in l1 and "position_1.ely (authored)" in l1     # v0.51.1 one line
    sec1 = _sec(h, "position_1")                                     # v0.51.1 compact rows
    assert re.search(r"<dt>Stage</dt><dd>X [\d.]+ · Y [\d.]+ · Z [\d.]+ mm · T [\d.]+° · "
                     r"R [\d.]+°</dd>", sec1), sec1[:2000]                # v0.51.2: X/Y/Z
    fib = re.search(r"<dt>FIB</dt><dd>(.*?)</dd>", sec1).group(1)
    shown = re.sub(r"<[^>]+>", "", fib)                               # visible text only
    assert re.fullmatch(r"Mag \d+x( · WD [\d.]+ mm)?", shown), shown  # v0.51.2: mag (+ WD)
    assert "objective 16" in fib and "nm/px" in fib                   # ... the rest on hover
    assert '<span class="rs">Residual</span>' in sec1                 # v0.51.2 header
    assert "Milling record" not in h and "Captured</dt>" not in sec1
    assert "<h2 id='pattern'>Patterns</h2>" in h and "id='twoup' checked" in h
    assert "<body class='two-up'>" in h
    l2 = _line(h, "position_2")
    assert "s-error" in l2 and "stage did not arrive" in l2           # reason in the line
    assert "RuntimeError: stage did not arrive" in l2                 # full message on hover
    assert "fiducial #2 (left)" in h
    assert "num_05_right.ely" in h                                   # unassigned fiducial
    assert "image not found" not in h                                # position_3 has none recorded
    assert "no reference image recorded" in h
    l10 = _line(h, "position_10")
    assert "s-assumed" in l10 and std.name in l10 and "no run log" in l10   # pos10
    assert "standard (given with --pattern)" in h                      # Patterns section
    # pos1 authored; pos2 (errored before milling) and pos10 get the given standard one
    assert len(re.findall(r'<g class="ov ov-pattern">', h)) == 3
    assert len(re.findall(r'<g class="ov ov-fid">', h)) == 1
    assert h.count('<g class="inset">') == 1                          # pos1 800x
    assert "dashed box" not in h and "Drawn to scale" not in h       # v0.51.2: notes removed


def test_report_without_runs_or_pattern():
    d, pf, db, std = _session()
    res = _build(d)
    h = res.path.read_text(encoding="utf-8")
    assert res.milled == 4 and res.assumed == 4                      # no log -> assumed milled
    assert "No run logs in this session" in h
    assert "Milled (assumed)" in h and "Assumed (no log)" in h
    l1 = _line(h, "position_1")
    assert "s-assumed" in l1 and "position_1.ely (authored)" in l1     # pos1 still drawn
    # pos10: no --pattern -> pyFIB's bundled default.ely
    assert "standard (pyFIB default)" in h
    assert "default.ely" in _line(h, "position_10") and "no run log" in _line(h, "position_10")


def test_changed_and_missing_patterns_are_flagged():
    d, pf, db, std = _session()
    _log_run(d, pf, db, std, authored_changed=True)
    h = _build(d).path.read_text(encoding="utf-8")
    assert "has CHANGED since it was milled" in h
    (d / "patterns" / "position_1.ely").unlink()
    h = _build(d).path.read_text(encoding="utf-8")
    assert "pattern file not found" in h


def test_interrupted_run_and_unknown_position():
    d, pf, db, std = _session()
    rl = RunLog(d, positions_file=str(pf), positions=["position_1", "ghost"],
                printer=lambda m: None)
    rl.site_start("ghost", 1, 2)
    rl.site_end("milled")
    rl.site_start("position_1", 2, 2, db.positions["position_1"])
    # process "killed": no site_end, no finish
    res = _build(d)
    h = res.path.read_text(encoding="utf-8")
    assert "ended without a final status" in h
    assert "'ghost'" in h or "ghost" in " ".join(res.warnings)
    assert "Interrupted" in h


def test_bad_run_log_is_reported_not_fatal():
    d, pf, db, std = _session()
    (d / "runs").mkdir()
    (d / "runs" / "run_20261001_000000.json").write_text("{not json", encoding="utf-8")
    res = _build(d)
    assert any("could not be read" in w for w in res.warnings)


def test_after_mill_image_from_run_log_and_from_folder():
    d, pf, db, std = _session()
    _bmp(d / "images" / "position_1_after_20261001_190001.bmp", 30)
    _bmp(d / "images" / "position_10_after_20261001_190500.bmp", 40)   # no log: found by name
    _bmp(d / "images" / "position_10_after_20261001_180000.bmp", 50)   # older one ignored
    rl = RunLog(d, positions_file=str(pf), printer=lambda m: None)
    rl.site_start("position_1", 1, 1, db.positions["position_1"])
    rl.site_pattern(d / "patterns" / "position_1.ely", "authored", "authored")
    rl.site_after_image(d / "images" / "position_1_after_20261001_190001.bmp")
    rl.site_end("milled")
    rl.finish("completed")
    h = _build(d).path.read_text(encoding="utf-8")
    assert h.count('class="im-after iv v-rough"') == 2
    assert h.count('class="fig-wrap has-after" data-view="rough"') == 2      # v0.51.0 views
    assert "after image position_1_after_20261001_190001.bmp" in _line(h, "position_1")
    assert "position_10_after_20261001_190500.bmp" in h
    assert "position_10_after_20261001_180000.bmp" not in h
    assert "All rough" in h and "All before" in h                  # toolbar switch (v0.51.0)


def test_positions_map_for_pyclem():
    import json
    from PIL import Image
    d, pf, db, std = _session()
    res = _build(d)
    m = res.map_paths
    meta = json.loads(m["json"].read_text())
    assert meta["schema"] == "pyfib.positions_map.v4" and meta["um_per_px"] == 1.0
    assert meta["z_model"]["applied"] is False              # one Z in this session
    im = Image.open(m["png"]); W, H = im.size
    assert (W, H) == (meta["image"]["width_px"], meta["image"]["height_px"])
    t = meta["transform"]
    for p in meta["positions"]:
        px = (t["x_left_mm"] - p["stage_x_mm"]) * 1000 / meta["um_per_px"]   # X increases LEFT
        assert p["map_y_mm"] == p["stage_y_mm"]                 # same Z: nothing moved
        py = (p["map_y_mm"] - t["y_top_mm"]) * 1000 / meta["um_per_px"]     # Y increases DOWN
        assert abs(px - p["px"]) < 0.02 and abs(py - p["py"]) < 0.02
        assert im.getpixel((int(p["px"]), int(p["py"]))) == 255      # the dot is there
    xs = {p["name"]: p["px"] for p in meta["positions"]}
    # position_1 at 43.60 mm and position_10 at 43.70 mm: 100 um apart at 1 um/px, and the
    # SMALLER stage X is on the RIGHT (Evan 2026-10-03: positions 5-7, the smallest X, sit
    # on the right of the Crossbeam SEM overview; refine's STAGE_SIGN_X = -1 agrees).
    assert abs((xs["position_1"] - xs["position_10"]) - 100.0) < 0.02
    labels = {p["name"]: p["label"] for p in meta["positions"]}
    assert labels["position_10"] == "10"
    tif = Image.open(m["tif"])
    assert tif.size == (W, H) and "unit=micron" in tif.tag_v2.get(270, "")
    rows = m["csv"].read_text().splitlines()
    assert rows[0] == "name,label,stage_x_mm,stage_y_mm,stage_z_mm,map_x_mm,map_y_mm,px,py"
    assert len(rows) == 5
    assert "Positions map for pyCLEM" in res.path.read_text(encoding="utf-8")


def test_stage_layout_plot_has_x_increasing_left():
    d, pf, db, std = _session()
    h = _build(d, export_map=False).path.read_text(encoding="utf-8")

    def cx(name):
        m = re.search(r'href="#pos-' + name + r'" class="pt"><title>[^<]*</title>'
                      r'<circle cx="([\d.]+)"', h)
        return float(m.group(1))
    # position_1 (X 43.60) is right of position_2 (43.65), which is right of position_10 (43.70)
    assert cx("position_1") > cx("position_2") > cx("position_10")


def test_mosaic_section_from_the_800x_images():
    d, pf, db, std = _session()                  # position_1 has an 800x image
    assert "id='mosaic'" not in _build(d, export_map=False).path.read_text(encoding="utf-8")
    res = _build(d, export_map=False, mosaic=True)    # opt-in since v0.43.2
    h = res.path.read_text(encoding="utf-8")
    assert "<h2 id='mosaic'>800x mosaic</h2>" in h and 'class="mosaic"' in h
    assert h.index("id='layout'") < h.index("id='mosaic'") < h.index("id='pattern'")
    assert res.mosaic is not None and res.mosaic.png_path.exists()
    assert '<a href="#pos-position_1"><title>position_1' in h       # clickable label
    # v0.43.1: no caption / warning text under the mosaic, and no long notes elsewhere
    sect = h[h.index("<h2 id='mosaic'>"):h.index("<h2 id='pattern'>")]
    assert "<figcaption>" not in sect and "class='warn'" not in sect
    assert "Black image with a white dot" not in h and "drawn the way the" not in h
    from ..__main__ import main
    assert main([str(d), "--mosaic", "--no-map", "--out", str(d / "m.html")]) == 0
    assert "id='mosaic'" in (d / "m.html").read_text(encoding="utf-8")


def test_map_and_layout_y_increase_downward_like_the_mosaic():
    """Evan 2026-10-04: same X/Y conventions as the 800x mosaic (FIB view): larger stage Y
    lower on the map and in the stage-layout plot."""
    from ..map_export import write_positions_map, STAGE_Y_INCREASES_DOWN
    assert STAGE_Y_INCREASES_DOWN
    d = Path(tempfile.mkdtemp())
    paths = write_positions_map([("a", 43.6, 56.80), ("b", 43.6, 56.90)], d / "m")
    import json as _j
    rows = {r["name"]: r for r in _j.loads(paths["json"].read_text())["positions"]}
    assert rows["b"]["py"] - rows["a"]["py"] > 99.9          # 100 um further DOWN
    # the stage-layout plot
    d2, pf, db, std = _session()
    import dataclasses
    db.positions["position_2"] = dataclasses.replace(
        db.positions["position_2"],
        stage_at=dataclasses.replace(db.positions["position_2"].stage_at, y_m=56.9e-3))
    db.save(pf)
    h = _build(d2, export_map=False, mosaic=False).path.read_text(encoding="utf-8")

    def cy(name):
        m = re.search(r'href="#pos-' + name + r'" class="pt"><title>[^<]*</title>'
                      r'<circle cx="[\d.]+" cy="([\d.]+)"', h)
        return float(m.group(1))
    assert cy("position_2") > cy("position_1")                # larger Y plotted lower


def test_labels_are_per_name():
    from ..map_export import short_label
    assert short_label("position_12") == "12"
    assert short_label("lamella_A") == "lamella_A"
    assert short_label("position_3b") == "position_3b"


def test_cli():
    from ..__main__ import main
    d, pf, db, std = _session()
    assert main([str(d), "--pattern", str(std), "--out", str(d / "r.html")]) == 0
    assert (d / "r.html").exists()
    assert main([str(d / "nope.json")]) == 2
    assert main([str(d), "--pattern", str(d / "nope.ely")]) == 2
    assert main([str(d), "--quality", "20"]) == 2


def test_report_code_never_drives_the_microscope():
    """Static check: no SmartSEM connection or command in the report package."""
    banned = ("SmartSEM(", "connect_remoting", "connect_local", "set_value(", "execute(",
              "MoveStage", "recall_position", "PatternRunner")
    for p in REPORT_DIR.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for b in banned:
            assert b not in src, f"{p.name} contains {b!r}"


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
