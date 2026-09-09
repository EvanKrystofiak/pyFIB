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
