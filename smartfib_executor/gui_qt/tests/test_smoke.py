"""Headless smoke test for the pyFIB Qt GUI scaffold.

Runs where qtpy/PyQt5 is installed (e.g. the instrument PC) using the offscreen
platform, and SKIPS cleanly where Qt is absent (e.g. this sandbox). Mirrors gisdep's
approach -- a Qt GUI is verifiable headless, unlike the tkinter one.

    QT_QPA_PLATFORM=offscreen python -m smartfib_executor.gui_qt.tests
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qt_available() -> bool:
    try:
        import qtpy  # noqa: F401
        from qtpy.QtWidgets import QApplication  # noqa: F401
        return True
    except Exception:
        return False


HAVE_QT = _qt_available()
_APP = None  # a single, held QApplication for the whole run (must not be GC'd)


def _app():
    global _APP
    from qtpy.QtWidgets import QApplication
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_argv_builder_is_correct():
    # Pure function -- always runs, even without Qt.
    from ..worker import build_argv
    argv = build_argv(["list-positions", "--positions-file", "p.json"])
    assert argv[1:4] == ["-u", "-m", "smartfib_executor.cli"]
    assert argv[4] == "list-positions"


def test_window_builds_and_has_all_pages():
    if not HAVE_QT:
        print("SKIP test_window_builds_and_has_all_pages (Qt not installed)")
        return
    _app()
    from ..app import MainWindow, NAV
    win = MainWindow()
    # every nav entry has a button and a page
    for key, _label in NAV:
        assert key in win._nav_buttons
        assert key in win._pages
    win.close()


def test_navigation_switches_pages_and_checks_button():
    if not HAVE_QT:
        print("SKIP test_navigation_switches_pages_and_checks_button (Qt not installed)")
        return
    _app()
    from ..app import MainWindow
    win = MainWindow()
    win.show_page("mill")
    assert win.stack.currentWidget() is win._pages["mill"]
    assert win._nav_buttons["mill"].isChecked()
    assert not win._nav_buttons["capture"].isChecked()
    win.close()


def test_theme_applies_without_error():
    if not HAVE_QT:
        print("SKIP test_theme_applies_without_error (Qt not installed)")
        return
    app = _app()
    from ..theme import apply_theme, palette
    apply_theme(app)
    pal = palette()
    assert pal["ACCENT"] == "#17c3b2" and pal["BG"] == "#232833"


def test_log_page_appends():
    if not HAVE_QT:
        print("SKIP test_log_page_appends (Qt not installed)")
        return
    _app()
    from ..app import LogPage
    lp = LogPage()
    lp.append("hello")
    assert "hello" in lp.view.toPlainText()


def test_capture_argv_gates_arm_flag():
    if not HAVE_QT:
        print("SKIP test_capture_argv_gates_arm_flag (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    _app()
    from ..app import MainWindow
    win = MainWindow()
    pf = str(Path(tempfile.mkdtemp()) / "positions.json")
    win.arm_bar.pos_edit.setText(pf)
    built = []
    win.run_cli = lambda args, **k: built.append(args)  # capture the argv
    win.capture_page.name_edit.setText("site_x")
    win.capture_page._on_capture()
    assert built and built[-1][0] == "capture-position" and built[-1][1] == "site_x"
    assert "--positions-file" in built[-1] and "--arm" not in built[-1]  # dry-run
    win.arm_bar.arm_btn.setChecked(True)
    win.capture_page._on_capture()
    assert "--arm" in built[-1]                                          # armed
    # v0.39.0: the 800x refine-fallback image is captured by default; untick -> not.
    a = built[-1]
    assert a[a.index("--coarse-mag") + 1] == "800"
    win.capture_page.coarse_chk.setChecked(False)
    win.capture_page._on_capture()
    assert "--coarse-mag" not in built[-1]
    win.close()


def test_positions_table_loads_v2_records():
    if not HAVE_QT:
        print("SKIP test_positions_table_loads_v2_records (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp())
    db = PositionDatabase()
    db.positions["p1"] = PositionRecord(
        name="p1", captured_unix_s=1.0, fib_magnification_x=2000.0,
        fib_objective_potential_v=16400.0,
        stage_at=StageAt(1e-3, 2e-3, 3e-3, 0.0, 0.0, None),
        fib_pixel_size_m=5.58e-8, fov_width_m=5.715e-5, ely_pattern="patterns/p1.ely")
    pf = d / "positions.json"; db.save(pf)
    win = MainWindow()
    win.arm_bar.pos_edit.setText(str(pf))
    win.show_page("positions")
    t = win.positions_page.table
    assert t.rowCount() == 1
    assert t.item(0, 0).text() == "p1"
    assert t.item(0, 6).text() == "55.80"          # px size in nm
    assert t.item(0, 8).text() == "yes"            # has ely_pattern
    win.close()


def test_nav_setup_is_first_and_default():
    if not HAVE_QT:
        print("SKIP test_nav_setup_is_first_and_default (Qt not installed)")
        return
    _app()
    from ..app import MainWindow, NAV
    assert NAV[0][0] == "setup"
    win = MainWindow()
    assert win.stack.currentWidget() is win._pages["setup"]
    win.close()


def test_mill_argv_assembly_from_checkboxes():
    if not HAVE_QT:
        print("SKIP test_mill_argv_assembly_from_checkboxes (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from qtpy.QtCore import Qt
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp())
    db = PositionDatabase()
    for n in ("s1", "s2", "s3"):
        db.positions[n] = PositionRecord(
            name=n, captured_unix_s=1.0, fib_magnification_x=2000.0,
            fib_objective_potential_v=16400.0,
            stage_at=StageAt(0, 0, 0, 0, 0, None))
    pf = d / "positions.json"; db.save(pf)
    (d / "p.ely").write_text("<pattern/>")
    win = MainWindow()
    win.arm_bar.pos_edit.setText(str(pf))
    win.show_page("mill")
    mp = win.mill_page
    mp.ely_edit.setText(str(d / "p.ely"))
    mp.table.item(1, mp.C_MILL).setCheckState(Qt.Unchecked)          # drop s2
    mp.table.item(mp._row_by_name["s1"], mp.C_AF).setCheckState(Qt.Checked)
    mp.table.item(mp._row_by_name["s3"], mp.C_REFINE).setCheckState(Qt.Checked)
    argv = mp.build_argv()
    assert argv[0] == "mill-positions"
    assert "s1" in argv and "s3" in argv and "s2" not in argv
    assert argv[argv.index("--autofocus") + 1] == "s1"
    assert argv[argv.index("--refine") + 1] == "s3"
    assert argv[argv.index("--move-method") + 1] == "movestage"   # v0.38.0 default
    assert "--arm" not in argv
    win.arm_bar.arm_btn.setChecked(True)
    assert "--arm" in mp.build_argv()
    win.close()


def test_mill_live_status_parsing():
    if not HAVE_QT:
        print("SKIP test_mill_live_status_parsing (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp())
    db = PositionDatabase()
    for n in ("s1", "s3"):
        db.positions[n] = PositionRecord(
            name=n, captured_unix_s=1.0, fib_magnification_x=2000.0,
            fib_objective_potential_v=16400.0, stage_at=StageAt(0, 0, 0, 0, 0, None))
    pf = d / "positions.json"; db.save(pf)
    win = MainWindow()
    win.arm_bar.pos_edit.setText(str(pf))
    win.show_page("mill")
    mp = win.mill_page

    def st(name):
        return mp.table.item(mp._row_by_name[name], mp.C_STATUS).text()

    mp._on_mill_line("[1/2] Milling at s1")
    assert st("s1") == "Milling…"
    mp._on_mill_line("[1/2] Completed milling at s1")
    assert st("s1") == "Done"
    mp._on_mill_line("Error at s3: boom")       # trailing colon must be stripped
    assert st("s3") == "Error"
    win.close()


def test_setup_page_has_four_sections_and_move_argv():
    if not HAVE_QT:
        print("SKIP test_setup_page_has_four_sections_and_move_argv (Qt not installed)")
        return
    _app()
    from ..app import MainWindow
    win = MainWindow()
    win.show_page("setup")
    sp = win.setup_page
    for attr in ("prep_btn", "move_btn", "measure_btn", "adjust_btn"):
        assert hasattr(sp, attr), attr
    # Move dropdown is populated from the shipped setup fixtures.
    names = [sp.pos_combo.itemText(i) for i in range(sp.pos_combo.count())]
    assert "CLEM SAFE" in names and "Cryo Insertion" in names
    # Insertion is listed before Removal.
    assert names.index("Cryo Insertion") < names.index("Cryo Removal")
    built = []
    win.run_module = lambda m, a, **k: built.append((m, a))
    sp.pos_combo.setCurrentText("Cryo Insertion")
    sp._on_move()  # unarmed -> setup_move, no --arm
    assert built[-1] == ("smartfib_executor.setup_move", ["Cryo Insertion"])
    # Prep uses the shipped fixtures (no session positions-file).
    sp._on_prep()
    assert built[-1] == ("smartfib_executor.prep_cryo", [])
    win.close()


def test_setup_coincidence_runs_as_module():
    if not HAVE_QT:
        print("SKIP test_setup_coincidence_runs_as_module (Qt not installed)")
        return
    _app()
    from ..app import MainWindow
    win = MainWindow()
    built = []
    win.run_module = lambda m, a, **k: built.append((m, a))
    win.setup_page._on_measure()  # unarmed -> --simulate --dry-run
    mod, args = built[-1]
    assert mod == "smartfib_executor.coincidence"
    assert "--simulate" in args and "--dry-run" in args
    # image markers are parsed for the side-by-side display
    win.setup_page._on_coinc_line("SEM_IMAGE: /tmp/x_sem.bmp")
    win.setup_page._on_coinc_line("FIB_IMAGE: /tmp/x_fib.bmp")
    assert win.setup_page._coinc_sem.endswith("x_sem.bmp")
    assert win.setup_page._coinc_fib.endswith("x_fib.bmp")
    win.close()


def test_session_path_convention():
    # Pure -- always runs.
    from ..session import new_session_positions_path, images_dir_for
    p = new_session_positions_path()
    assert p.name == "positions.json" and p.parent.name.startswith("session_")
    assert p.parent.parent.name == "positions"
    assert images_dir_for(str(p)).name == "images"
    assert images_dir_for(str(p)).parent == p.parent.resolve()


def test_fresh_session_set_on_startup_and_new_session_differs():
    if not HAVE_QT:
        print("SKIP test_fresh_session_set_on_startup_and_new_session_differs (Qt not installed)")
        return
    import time
    _app()
    from ..app import MainWindow
    win = MainWindow()
    pf = win.arm_bar.pos_edit.text()
    assert "session_" in pf and pf.endswith("positions.json")
    time.sleep(1)  # timestamp granularity
    win.arm_bar._new_session()
    assert win.arm_bar.pos_edit.text() != pf
    win.close()


def test_capture_defaults_image_dir_into_session():
    if not HAVE_QT:
        print("SKIP test_capture_defaults_image_dir_into_session (Qt not installed)")
        return
    _app()
    from ..app import MainWindow
    win = MainWindow()  # already has a fresh session file
    built = []
    win.run_cli = lambda a, **k: built.append(a)
    win.capture_page.imgdir_edit.setText("")   # empty -> should default to session
    win.capture_page._on_capture()
    argv = built[-1]
    img = argv[argv.index("--image-dir") + 1]
    assert img.endswith("images")
    win.close()


def test_gisdep_tab_embeds_the_real_window():
    if not HAVE_QT:
        print("SKIP test_gisdep_tab_embeds_the_real_window (Qt not installed)")
        return
    _app()
    from ..app import MainWindow, NAV
    # gisdep tab sits between Setup and Capture; elymaker right after gisdep
    keys = [k for k, _ in NAV]
    assert keys.index("gisdep") == keys.index("setup") + 1
    assert keys.index("elymaker") == keys.index("gisdep") + 1
    assert keys.index("elymaker") < keys.index("capture")
    win = MainWindow()
    win.show_page("gisdep")                       # builds lazily (mock offline)
    gt = win.gisdep_page
    assert gt._gwin is not None
    import gisdep.gui.app as ga
    assert isinstance(gt._gwin, ga.GisdepWindow)
    for attr in ("image_label", "caption", "arm_btn", "stop_btn", "log"):
        assert hasattr(gt._gwin, attr), attr
    # elymaker tab embeds elymaker's real MainWindow with its handoff hooks
    win.show_page("elymaker")
    et = win.elymaker_page
    assert et._ewin is not None
    import elymaker.gui.main_window as emw
    assert isinstance(et._ewin, emw.MainWindow)
    assert hasattr(et._ewin, "open_handoff") and hasattr(et._ewin, "send_to_smartfib")
    win.close()


def test_mill_select_all_none_is_explicit_not_inverting():
    """REGRESSION: 'Toggle all' inverted mixed columns -- from the defaults (Mill on,
    AF/Refine off) it UNchecked Mill and checked AF/Refine. All/None must set fixed
    states, and AF/Refine 'All' only checks rows selected for milling."""
    if not HAVE_QT:
        print("SKIP test_mill_select_all_none_is_explicit_not_inverting (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from qtpy.QtCore import Qt
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); db = PositionDatabase()
    for n in ("s1", "s2", "s3"):
        db.positions[n] = PositionRecord(name=n, captured_unix_s=1.0,
                                         fib_magnification_x=2000.0,
                                         fib_objective_potential_v=16400.0,
                                         stage_at=StageAt(0, 0, 0, 0, 0, None))
    pf = d / "positions.json"; db.save(pf)
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf)); win.show_page("mill")
    mp = win.mill_page

    def col(c):
        return [mp.table.item(r, c).checkState() == Qt.Checked for r in range(3)]

    # from defaults, 'All' on every column must yield all-checked (the broken case)
    mp._set_col(mp.C_MILL, True); mp._set_col(mp.C_AF, True); mp._set_col(mp.C_REFINE, True)
    assert all(col(mp.C_MILL)) and all(col(mp.C_AF)) and all(col(mp.C_REFINE))
    mp._set_col(mp.C_REFINE, False); assert not any(col(mp.C_REFINE))
    # AF 'All' respects the Mill selection
    mp.table.item(1, mp.C_MILL).setCheckState(Qt.Unchecked)
    mp._set_col(mp.C_AF, True)
    assert col(mp.C_AF) == [True, False, True]
    win.close()


def test_capture_tab_mill_fiducial_here_then_capture_records_side():
    if not HAVE_QT:
        print("SKIP test_capture_tab_mill_fiducial_here_then_capture_records_side (Qt not installed)")
        return
    _app()
    from ..app import MainWindow
    win = MainWindow(); win.show_page("capture")
    cp = win.capture_page
    built = []; win.run_cli = lambda a, **k: built.append(a); win.confirm = lambda m: True
    cp.fid_kind.setCurrentText("square")                   # the prototype-square flow
    cp.mag_edit.setText("2000"); cp.fid_side.setCurrentText("left")
    cp._on_mill_fiducial_here()
    a = built[-1]
    assert a[0] == "run" and a[1].endswith("fiducial_left.ely")
    assert a[a.index("--fib-mag") + 1] == "2000"          # milled at the capture mag
    assert cp.fid_record.currentText() == "left"          # auto-set for the capture
    cp.name_edit.setText("s"); cp._on_capture()
    b = built[-1]
    assert b[0] == "capture-position" and b[b.index("--fiducial-side") + 1] == "left"
    win.close()


def test_next_position_name():
    from ..pages import next_position_name
    assert next_position_name("position_1", set()) == "position_2"
    assert next_position_name("pos09", set()) == "pos10"
    assert next_position_name("siteA", set()) == "siteA_2"
    assert next_position_name("position_1", {"position_2", "position_3"}) == "position_4"


def test_capture_tab_numbered_fiducial_and_counters():
    if not HAVE_QT:
        print("SKIP test_capture_tab_numbered_fiducial_and_counters (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf)); win.show_page("capture")
    cp = win.capture_page
    calls = []; win.run_cli = lambda a, **k: calls.append((a, k)); win.confirm = lambda m: True
    assert cp.tilt_chk.isChecked() and cp.fid_kind.currentText() == "number"
    cp._on_mill_fiducial_here()
    a, _ = calls[-1]
    assert a[0] == "run" and Path(a[1]).name == "num_01_left.ely" and Path(a[1]).exists()
    assert cp.fid_record.currentText() == "left"
    # unarmed capture = dry run: counters must NOT advance
    cp._on_capture(); a, k = calls[-1]
    assert a[a.index("--fiducial-number") + 1] == "1" and "--include-tilt" in a
    k["on_done"](0)
    assert cp.name_edit.text() == "position_1" and cp.fid_number.value() == 1
    # armed success advances name + number and clears the pending fiducial
    win.arm_bar.arm_btn.setChecked(True); cp._on_capture(); a, k = calls[-1]
    db = PositionDatabase()
    db.positions["position_1"] = PositionRecord(
        name="position_1", captured_unix_s=1.0, fib_magnification_x=2000.0,
        fib_objective_potential_v=16400.0, stage_at=StageAt(0, 0, 0, 0, 0, 2.0),
        fiducial_side="left", fiducial_number=1)
    db.save(pf)
    k["on_done"](0)
    assert cp.name_edit.text() == "position_2" and cp.fid_number.value() == 2
    assert cp.fid_record.currentText() == "none"
    # a failed armed capture does not advance
    cp._on_capture(); a, k = calls[-1]; k["on_done"](1)
    assert cp.name_edit.text() == "position_2" and cp.fid_number.value() == 2
    # re-capturing an existing name asks first; declining runs nothing
    win.confirm = lambda m: False; cp.name_edit.setText("position_1"); n = len(calls)
    cp._on_capture(); assert len(calls) == n
    win.close()


def test_mill_tab_refine_stage_checkboxes():
    if not HAVE_QT:
        print("SKIP test_mill_tab_refine_stage_checkboxes (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from qtpy.QtCore import Qt
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase()
    db.positions["s1"] = PositionRecord(name="s1", captured_unix_s=1.0,
                                        fib_magnification_x=2000.0,
                                        fib_objective_potential_v=16400.0,
                                        stage_at=StageAt(0, 0, 0, 0, 0, None))
    db.save(pf)
    ely = d / "x.ely"; ely.write_text("<x/>")
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf)); win.show_page("mill")
    mp = win.mill_page
    mp.ely_edit.setText(str(ely))
    # v0.39.0: "Refine may move stage" is ticked by default in the UI.
    assert mp.refine_stage_chk.isChecked() and not mp.refine_skip_chk.isChecked()
    mp._set_col(mp.C_REFINE, True)
    a = mp.build_argv()
    assert a and "--refine-enable-stage" in a and "--refine-abort-on-fail" not in a
    mp.refine_stage_chk.setChecked(False)
    a = mp.build_argv()
    assert "--refine-enable-stage" not in a
    mp.refine_stage_chk.setChecked(True); mp.refine_skip_chk.setChecked(True)
    a = mp.build_argv()
    assert "--refine-enable-stage" in a and "--refine-abort-on-fail" in a
    mp._set_col(mp.C_REFINE, False)         # no refine selected -> no stage flag
    a = mp.build_argv()
    assert "--refine-enable-stage" not in a
    # v0.51.0: after-mill image ON by default (the next stage aligns to it): --stage turns
    # it on in the CLI, unticking adds --no-after-image.
    assert mp.after_chk.isChecked() and "--no-after-image" not in a
    assert a[a.index("--stage") + 1] == "rough"
    mp.after_chk.setChecked(False)
    assert "--no-after-image" in mp.build_argv()
    win.close()


def test_mill_armed_start_checks_tilt_first_and_asks_before_skipping():
    """v0.46.0: armed Start -> read-only --tilt-check -> OK for the skips -> armed run with
    --accept-tilt-skips. Not armed: straight to the dry run, as before."""
    if not HAVE_QT:
        print("SKIP test_mill_armed_start_checks_tilt_first_and_asks_before_skipping")
        return
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    db = PositionDatabase()
    for n, t in (("s1", 1.97), ("s2", 30.0), ("s3", 1.97)):
        db.positions[n] = PositionRecord(name=n, captured_unix_s=1.0, fib_magnification_x=2000.0,
                                         fib_objective_potential_v=16400.0,
                                         stage_at=StageAt(0, 0, 0, 12.0, 0, t))
    db.save(pf); (d / "p.ely").write_text("<pattern/>")
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf)); win.show_page("mill")
    mp = win.mill_page; mp.ely_edit.setText(str(d / "p.ely"))
    calls, confirms, answer = [], [], [True]
    win.run_cli = lambda a, **k: calls.append((list(a), k)) or True
    win.confirm = lambda m: confirms.append(m) or answer[0]
    mp._on_start()                                     # not armed: dry run only
    assert len(calls) == 1 and "--arm" not in calls[0][0] and "--tilt-check" not in calls[0][0]
    _set_armed(win, True)
    for ans, want in ((True, "run"), (False, "none")):
        calls.clear(); confirms.clear(); answer[0] = ans
        mp._on_start()
        a, k = calls[0]
        assert "--tilt-check" in a and "--arm" not in a, a      # the check never arms
        k["on_line"]("[tilt] current stage tilt 1.97 deg")
        k["on_line"]("TILT-CHECK: live=1.9700 limit=2 allowed=s1,s3 excluded=s2")
        k["on_done"](0)
        assert len(confirms) == 1 and "s2" in confirms[0] and "NOT be milled" in confirms[0]
        if want == "run":
            a2 = calls[1][0]
            assert "--arm" in a2 and a2[a2.index("--accept-tilt-skips") + 1] == "s2"
            assert mp.table.item(mp._row_by_name["s2"], mp.C_STATUS).text() == "Skipped (tilt)"
        else:
            assert len(calls) == 1                     # declined: nothing else runs
    calls.clear(); confirms.clear()                    # nothing excluded: no question
    mp._on_start(); k = calls[0][1]
    k["on_line"]("TILT-CHECK: live=1.9700 limit=2 allowed=s1,s2,s3 excluded=")
    k["on_done"](0)
    assert confirms == [] and "--arm" in calls[1][0] and "--accept-tilt-skips" not in calls[1][0]
    calls.clear()                                      # check failed (rc 2): nothing runs
    mp._on_start(); calls[0][1]["on_done"](2)
    assert len(calls) == 1
    win.close()


def test_three_milling_tabs_rough_prepolish_polish():
    """v0.50.0 (Evan): Mill -> Rough Mill, then Prepolish and Polish below it, each with
    the same milling options for now -- separate pages, so their selections and options
    are independent."""
    if not HAVE_QT:
        print("SKIP test_three_milling_tabs_rough_prepolish_polish (Qt not installed)")
        return
    import tempfile
    from pathlib import Path
    from qtpy.QtCore import Qt
    from qtpy.QtWidgets import QLabel
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow, NAV
    from ..pages import MillPage
    keys = [k for k, _ in NAV]
    labels = dict(NAV)
    i = keys.index("mill")
    assert keys[i:i + 5] == ["mill", "prepolish", "polish", "tools", "log"], keys
    assert (labels["mill"], labels["prepolish"], labels["polish"]) == \
        ("Rough Mill", "Prepolish", "Polish")
    d = Path(tempfile.mkdtemp())
    db = PositionDatabase()
    for n in ("s1", "s2"):
        db.positions[n] = PositionRecord(name=n, captured_unix_s=1.0, fib_magnification_x=2000.0,
                                         fib_objective_potential_v=16400.0,
                                         stage_at=StageAt(0, 0, 0, 12.0, 0, 2.0))
    pf = d / "positions.json"; db.save(pf); (d / "p.ely").write_text("<pattern/>")
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf))
    pages = win.mill_pages()
    assert [p.title for p in pages] == ["Rough Mill", "Prepolish", "Polish"]
    assert [p.stage for p in pages] == ["rough", "prepolish", "polish"]
    assert len({id(p) for p in pages}) == 3 and all(isinstance(p, MillPage) for p in pages)
    for key, page in zip(("mill", "prepolish", "polish"), pages):
        assert win._nav_buttons[key].text() == labels[key]
        win.show_page(key)
        assert win.stack.currentWidget() is page and win._nav_buttons[key].isChecked()
        assert any(l.text() == page.title for l in page.findChildren(QLabel))   # page title
        assert page.table.rowCount() == 2                                        # reloaded
    # same options, independent state
    rough, pre, pol = pages
    for p in pages:
        p.ely_edit.setText(str(d / "p.ely"))
    pre.table.item(pre._row_by_name["s2"], pre.C_MILL).setCheckState(Qt.Unchecked)
    pol.table.item(pol._row_by_name["s1"], pol.C_AF).setCheckState(Qt.Checked)
    a_r, a_p, a_l = rough.build_argv(), pre.build_argv(), pol.build_argv()
    assert "s1" in a_r and "s2" in a_r and "s2" not in a_p
    assert "--autofocus" in a_l and "--autofocus" not in a_r
    assert all(a[0] == "mill-positions" for a in (a_r, a_p, a_l))
    win.close()


def _tools_window(tmp_session=True):
    """MainWindow with a small session; run_module captured instead of executed."""
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    if tmp_session:
        db = PositionDatabase()
        for i, (t, v) in enumerate(((1.0, 16300.0), (5.0, 16450.0))):
            db.positions[f"position_{i+1}"] = PositionRecord(
                name=f"position_{i+1}", captured_unix_s=t, fib_magnification_x=2000.0,
                fib_objective_potential_v=v, stage_at=StageAt(0, 0, 0, 12.0, 0, 2.0))
        db.save(pf)
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf))
    calls, confirms = [], []
    win.run_module = lambda mod, args, **kw: calls.append((mod, list(args), kw)) or True
    win.confirm = lambda msg: confirms.append(msg) or True
    return win, pf, calls, confirms


def test_tools_tab_sits_between_mill_and_log():
    if not HAVE_QT:
        print("SKIP test_tools_tab_sits_between_mill_and_log (Qt not installed)")
        return
    from ..app import NAV
    keys = [k for k, _ in NAV]
    assert keys.index("tools") == keys.index("polish") + 1      # v0.50.0: after the mill stages
    assert keys.index("log") == keys.index("tools") + 1
    win, pf, calls, confirms = _tools_window()
    win.show_page("tools")
    assert win.stack.currentWidget() is win.tools_page
    # bounds prefilled from the LAST captured position (16450 V) +/- 400 V
    tp = win.tools_page
    assert (tp.vmin.value(), tp.vmax.value()) == (16050.0, 16850.0)
    win.close()


def test_tools_report_button_runs_the_report_module_armed_or_not():
    if not HAVE_QT:
        print("SKIP test_tools_report_button_runs_the_report_module_armed_or_not (Qt not installed)")
        return
    win, pf, calls, confirms = _tools_window()
    tp = win.tools_page
    tp._on_report()
    mod, args, kw = calls[-1]
    assert mod == "smartfib_executor.report" and args == [str(pf)]
    assert "--mosaic" not in args and not confirms              # files only: no confirm
    tp.pattern_edit.setText("C:/x/std.ely"); tp.mosaic_chk.setChecked(True)
    _set_armed(win, True)                                         # armed: still no confirm
    tp._on_report()
    assert not confirms
    assert calls[-1][1] == [str(pf), "--pattern", "C:/x/std.ely", "--mosaic"]
    # the report path is parsed from the output and opened when the run succeeds
    opened = []
    tp._open_url = opened.append
    tp._on_report_line(f"Report: {pf.parent}/report_20261005_120000.html (2 positions, ...)")
    tp._on_report_done(0)
    assert opened == [f"{pf.parent}/report_20261005_120000.html"]
    tp._on_report_done(1)                                         # failed run: nothing more
    assert len(opened) == 1
    win.close()


def test_tools_report_needs_a_positions_file():
    if not HAVE_QT:
        print("SKIP test_tools_report_needs_a_positions_file (Qt not installed)")
        return
    win, pf, calls, confirms = _tools_window(tmp_session=False)
    win.tools_page._on_report()
    assert calls == []
    win.close()


def _set_armed(win, armed):
    """Flip the shared ARM toggle the way the user does."""
    ab = win.arm_bar
    for name in ("arm_btn", "arm_chk", "toggle"):
        w = getattr(ab, name, None)
        if w is not None and hasattr(w, "setChecked"):
            w.setChecked(armed)
            break
    assert win.is_armed() is armed


def test_tools_sem_autofocus_dry_run_unarmed_confirmed_when_armed():
    if not HAVE_QT:
        print("SKIP test_tools_sem_autofocus_dry_run_unarmed_confirmed_when_armed (Qt not installed)")
        return
    win, pf, calls, confirms = _tools_window()
    tp = win.tools_page
    _set_armed(win, False)
    tp._on_sem_af()
    mod, args, _ = calls[-1]
    assert mod == "smartfib_executor.sem_autofocus" and "--arm" not in args and not confirms
    assert args == ["--sem-mag", "2000"]
    _set_armed(win, True)
    tp._on_sem_af()
    mod, args, _ = calls[-1]
    assert args[:2] == ["--arm", "--yes"] and len(confirms) == 1
    assert "--out" in args and str(pf.parent) in args[args.index("--out") + 1]
    win.confirm = lambda msg: False                               # user says No
    n = len(calls); tp._on_sem_af()
    assert len(calls) == n
    win.close()


def test_tools_fib_autofocus_dry_run_unarmed_confirmed_when_armed():
    if not HAVE_QT:
        print("SKIP test_tools_fib_autofocus_dry_run_unarmed_confirmed_when_armed (Qt not installed)")
        return
    win, pf, calls, confirms = _tools_window()
    tp = win.tools_page
    win.show_page("tools")                                        # fills bounds
    _set_armed(win, False)
    tp._on_fib_af()
    mod, args, _ = calls[-1]
    assert mod == "smartfib_executor.autofocus" and args[-1] == "--dry-run" and not confirms
    assert args[args.index("--vmin") + 1] == "16050" and args[args.index("--vmax") + 1] == "16850"
    # same settings as the Mill tab's per-position autofocus
    for flag, val in (("--half-range", "150"), ("--coarse-points", "7"),
                      ("--raster-side", "auto"), ("--scan-rate", "8"),
                      ("--scan-rate-method", "cmd"), ("--beam-shift-x", "1.2e-05")):
        assert args[args.index(flag) + 1] == val, (flag, args)
    # the FIB autofocus CLI accepts exactly these arguments
    from ...autofocus.__main__ import build_parser
    build_parser().parse_args(args)
    _set_armed(win, True)
    tp._on_fib_af()
    assert "--dry-run" not in calls[-1][1] and len(confirms) == 1
    # bad bounds: refused before anything runs
    tp.vmin.setValue(17000); tp.vmax.setValue(16000)
    n = len(calls); tp._on_fib_af()
    assert len(calls) == n
    win.close()


def test_tools_sem_args_parse_with_the_sem_autofocus_cli():
    if not HAVE_QT:
        print("SKIP test_tools_sem_args_parse_with_the_sem_autofocus_cli (Qt not installed)")
        return
    win, pf, calls, confirms = _tools_window()
    from ...sem_autofocus.__main__ import build_parser
    for armed in (False, True):
        build_parser().parse_args(win.tools_page.sem_args(armed))
    win.close()


def _stage_window():
    """MainWindow + a session where only s1 has a Rough Mill after image."""
    import tempfile
    from pathlib import Path
    from ...positions import PositionDatabase, PositionRecord, StageAt
    _app()
    from ..app import MainWindow
    d = Path(tempfile.mkdtemp()); pf = d / "positions.json"
    (d / "images").mkdir()
    db = PositionDatabase()
    for n in ("s1", "s2"):
        db.positions[n] = PositionRecord(name=n, captured_unix_s=1.0, fib_magnification_x=2000.0,
                                         fib_objective_potential_v=16400.0,
                                         stage_at=StageAt(0, 0, 0, 12.0, 0, 2.0),
                                         image_bmp=f"images/{n}.bmp", ely_pattern="a.ely")
        (d / "images" / f"{n}.bmp").write_bytes(b"BM")
    db.save(pf)
    (d / "images" / "s1_1-rough_20261007_101010.bmp").write_bytes(b"BM")
    for n in ("rough.ely", "pre.ely", "a.ely"):
        (d / n).write_text("<pattern/>")
    win = MainWindow(); win.arm_bar.pos_edit.setText(str(pf))
    calls, confirms, answer = [], [], [True]
    win.run_cli = lambda a, **k: calls.append((list(a), k)) or True
    win.confirm = lambda m: confirms.append(m) or answer[0]
    return win, d, pf, calls, confirms, answer


def test_stage_tabs_pattern_reference_columns_and_skip_ok():
    """v0.51.0: Prepolish mills its own pattern with --stage prepolish, refine on by
    default, 'Align to' shows the rough after image or 'missing', and an armed start lists
    the positions without one and asks before skipping them."""
    if not HAVE_QT:
        print("SKIP test_stage_tabs_pattern_reference_columns_and_skip_ok (Qt not installed)")
        return
    win, d, pf, calls, confirms, answer = _stage_window()
    rough, pre, pol = win.mill_pages()
    win.show_page("prepolish")
    assert pre.table.rowCount() == 2
    r1, r2 = pre._row_by_name["s1"], pre._row_by_name["s2"]
    assert pre.table.item(r1, pre.C_REF).text() == "rough 10-07 10:10"
    assert pre.table.item(r2, pre.C_REF).text() == "missing"
    assert pre.table.item(r1, pre.C_REFINE).checkState() == 2           # refine on by default
    win.show_page("mill")
    assert rough.table.item(rough._row_by_name["s1"], rough.C_REF).text() == "capture image"
    assert rough.table.item(rough._row_by_name["s1"], rough.C_AFTER).text() == "10-07 10:10"
    assert rough.table.item(rough._row_by_name["s1"], rough.C_REFINE).checkState() == 0
    pre.ely_edit.setText(str(d / "pre.ely"))
    a = pre.build_argv()
    assert a[a.index("--stage") + 1] == "prepolish" and "--refine" in a
    assert "--no-after-image" not in a                                  # after image on
    pre._on_start()                                                     # not armed: dry run
    assert len(calls) == 1 and confirms == []
    assert calls[0][0][calls[0][0].index("--accept-reference-skips") + 1] == "s2"
    _set_armed(win, True)
    for ans in (False, True):
        calls.clear(); confirms.clear(); answer[0] = ans
        pre._on_start()
        assert len(confirms) == 1 and "s2" in confirms[0] and "no Rough Mill after image" \
            in confirms[0] and "NOT be prepolished" in confirms[0], confirms
        if not ans:
            assert calls == []                                          # declined: nothing runs
        else:
            a = calls[0][0]
            assert "--tilt-check" in a and "--arm" not in a and \
                a[a.index("--accept-reference-skips") + 1] == "s2"
            assert pre.table.item(r2, pre.C_STATUS).text().startswith("Skipped")
    # review: a failed refine never mills in Prepolish/Polish (locked on); an unrefined
    # site needs its own OK when armed
    from qtpy.QtCore import Qt
    assert pre.refine_skip_chk.isChecked() and not pre.refine_skip_chk.isEnabled()
    assert rough.refine_skip_chk.isEnabled() and not rough.refine_skip_chk.isChecked()
    pre.table.item(r1, pre.C_REFINE).setCheckState(Qt.Unchecked)
    calls.clear(); confirms.clear(); answer[0] = True
    pre._on_start()
    assert len(confirms) == 2 and "s1" in confirms[1] and "WITHOUT refine" in confirms[1]
    calls.clear(); confirms.clear(); answer[0] = True
    win.confirm = lambda m: confirms.append(m) or ("WITHOUT refine" not in m)
    pre._on_start()
    assert calls == [] and len(confirms) == 2                           # declined: nothing runs
    win.confirm = lambda m: confirms.append(m) or answer[0]
    pre.table.item(r1, pre.C_REFINE).setCheckState(Qt.Checked)
    # ticks survive a tab switch (reload keeps them)
    pre.table.item(r1, pre.C_AF).setCheckState(Qt.Checked)
    win.show_page("log"); win.show_page("prepolish")
    assert pre.table.item(r1, pre.C_AF).checkState() == Qt.Checked
    win.close()


def test_manual_buttons_build_site_commands_and_confirm_when_armed():
    """v0.51.0: Go to / Align / Push pattern / Record after image act on the selected row,
    stay on the tab (show_log=False), dry-run unarmed, ask first when armed."""
    if not HAVE_QT:
        print("SKIP test_manual_buttons_build_site_commands_and_confirm_when_armed")
        return
    win, d, pf, calls, confirms, answer = _stage_window()
    rough, pre, pol = win.mill_pages()
    win.show_page("prepolish")
    pre._on_manual("goto")
    assert calls == []                                                  # nothing selected
    pre.table.selectRow(pre._row_by_name["s1"])
    assert pre.sel_label.text() == "Selected position: s1"
    pre._on_manual("goto")
    a, k = calls[-1]
    assert a[:6] == ["site", "goto", "s1", "--stage", "prepolish", "--positions-file"]
    assert "--arm" not in a and k["show_log"] is False and confirms == []
    pre._on_manual("push")
    assert len(calls) == 1                                              # no pattern chosen yet
    pre.ely_edit.setText(str(d / "pre.ely"))
    _set_armed(win, True)
    for act in ("goto", "align", "push", "after-image"):
        calls.clear(); confirms.clear(); answer[0] = False
        pre._on_manual(act)
        assert len(confirms) == 1 and calls == [], act                  # declined
        answer[0] = True
        pre._on_manual(act)
        a, k = calls[-1]
        assert a[1] == act and "--arm" in a and k["show_log"] is False
    calls.clear(); confirms.clear()
    pre._on_manual("push")
    assert "pre.ely" in confirms[0] and "NOT start" in confirms[0]       # stage pattern named
    assert calls[-1][0][calls[-1][0].index("--ely") + 1] == str(d / "pre.ely")
    win.show_page("mill"); rough.table.selectRow(rough._row_by_name["s1"])
    rough.ely_edit.setText(str(d / "rough.ely"))
    confirms.clear(); rough._on_manual("push")
    assert "a.ely" in confirms[0]                                       # authored for rough
    calls.clear(); confirms.clear()
    pre._on_manual("align")
    assert "s1_1-rough_20261007_101010.bmp" in confirms[0]
    assert "--refine-enable-stage" in calls[-1][0]
    # after the after-image step the columns refresh from positions.json
    from ...mill_stages import record_after_image
    img = d / "images" / "s1_2-prepolish_20261007_121212.bmp"; img.write_bytes(b"BM")
    record_after_image(pf, "s1", "prepolish", img)
    pre._on_manual("after-image")
    calls[-1][1]["on_line"]("SITE-DONE: after-image s1 s1_2-prepolish_20261007_121212.bmp")
    calls[-1][1]["on_done"](0)
    assert pre.table.item(pre._row_by_name["s1"], pre.C_AFTER).text() == "10-07 12:12"
    assert pre.manual_status.text().startswith("SITE-DONE")
    win.show_page("polish")
    assert pol.table.item(pol._row_by_name["s1"], pol.C_REF).text() == "prepolish 10-07 12:12"
    win.close()


def _run_all() -> int:
    if HAVE_QT:
        _app()  # create + hold one QApplication before any window is built
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    tag = "" if HAVE_QT else "  (Qt absent -- window tests skipped)"
    print(f"\n{len(tests) - failed}/{len(tests)} passed{tag}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
