"""Tests for v0.8.0: current colour ramp, SmartFIB send/export, config."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# --------------------------------------------------------------------------- #
# colour ramp: blue -> red at 1.5 nA, then magenta above
# --------------------------------------------------------------------------- #
# Probe colours are now CATEGORICAL (one fixed Okabe-Ito colour per library probe),
# not a hue ramp: the tests assert distinctness/determinism, not specific hues.
def test_probe_colour_700pa_is_fixed_and_distinct(qapp):
    from elymaker.gui.items import current_color
    c = current_color(7e-10)
    assert c.rgb() == current_color(7e-10).rgb()                 # deterministic
    assert c.rgb() != current_color(3e-10).rgb() != current_color(1.5e-9).rgb()


def test_probe_colours_10pa_and_20pa_are_different_probes(qapp):
    from elymaker.gui.items import current_color
    # they are distinct library probes -> distinct colours (was a shared "blue floor")
    assert current_color(1e-11).rgb() != current_color(2e-11).rgb()


def test_probe_colour_30na_distinct_from_700pa(qapp):
    from elymaker.gui.items import current_color
    assert current_color(3e-8).rgb() != current_color(7e-10).rgb()


def test_current_ramp_spreads_mid_currents(qapp):
    """The mid-range probes should be clearly different hues now that red caps
    at 700 pA."""
    from elymaker.gui.items import current_color
    cols = [current_color(v).rgb() for v in (2e-11, 5e-11, 1e-10, 3e-10, 7e-10)]
    assert len(set(cols)) == 5


# --------------------------------------------------------------------------- #
# config round-trip
# --------------------------------------------------------------------------- #
def test_config_roundtrip(tmp_path, monkeypatch):
    from elymaker.gui import config
    monkeypatch.setattr(config, "_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_CONFIG_PATH", str(tmp_path / "config.json"))
    config.set("smartfib_drop_path", r"D:\SmartFIB\ApiLayout.ely")
    assert config.get("smartfib_drop_path") == r"D:\SmartFIB\ApiLayout.ely"


# --------------------------------------------------------------------------- #
# send to SmartFIB (export)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def win(qapp, qt_cleanup):
    from elymaker.gui.main_window import MainWindow
    w = MainWindow(); w.show()
    qt_cleanup.append(w)
    w.new_layout()
    return w


def test_export_to_smartfib_writes_loadable_ely(win, tmp_path):
    from elymaker.gui.items import RectItem
    from elymaker.gui.coords import layout_to_scene
    from elymaker.reader import read_ely
    it = RectItem(10, -4); sx, sy = layout_to_scene(0, 0); it.setPos(sx, sy)
    win.view.defaults.apply(it); win.scene.add_shape_item(it); win._on_shape_created(it)
    drop = str(tmp_path / "ApiLayout.ely")
    assert win.export_to_smartfib(drop) is True
    assert read_ely(drop).shape_count() == 1     # SmartFIB-loadable


def test_export_empty_pattern_refuses(win, tmp_path):
    drop = str(tmp_path / "empty.ely")
    assert win.export_to_smartfib(drop) is False
    assert not os.path.exists(drop)


# --------------------------------------------------------------------------- #
# trench enable is an explicit checkbox now
# --------------------------------------------------------------------------- #
def test_trench_checkbox_controls_spec(qapp, qt_cleanup):
    from elymaker.gui.lamella_dialog import LamellaDialog
    dlg = LamellaDialog()
    qt_cleanup.append(dlg)
    assert dlg.spec().trenches is False
    dlg.tr_enable.setChecked(True)
    assert dlg.spec().trenches is True
