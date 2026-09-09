import math

import pytest

from elymaker.gui.coords import (
    ImageCalibration,
    layout_to_scene,
    scene_to_layout,
)


def test_y_flip_roundtrip():
    for x, y in [(0, 0), (10, -5), (-3.2, 7.7), (100, 100)]:
        sx, sy = layout_to_scene(x, y)
        assert (sx, sy) == (x, -y)
        lx, ly = scene_to_layout(sx, sy)
        assert lx == pytest.approx(x)
        assert ly == pytest.approx(y)


def test_calibration_from_hfw_pixel_size():
    cal = ImageCalibration.from_hfw(hfw_um=50.0, image_w_px=1000, image_h_px=800)
    assert cal.pixel_size_um == pytest.approx(0.05)
    assert cal.width_um == pytest.approx(50.0)
    assert cal.height_um == pytest.approx(40.0)


def test_calibration_pixel_layout_roundtrip():
    cal = ImageCalibration(pixel_size_um=0.05, image_w_px=1000, image_h_px=800,
                           center_x_um=10.0, center_y_um=-4.0)
    # centre pixel maps to image centre in layout
    lx, ly = cal.pixel_to_layout(500, 400)
    assert lx == pytest.approx(10.0)
    assert ly == pytest.approx(-4.0)
    # round-trip an arbitrary pixel
    px, py = cal.layout_to_pixel(lx, ly)
    assert px == pytest.approx(500)
    assert py == pytest.approx(400)


def test_calibration_y_is_up_in_layout():
    cal = ImageCalibration(pixel_size_um=1.0, image_w_px=100, image_h_px=100)
    # a pixel *above* centre (smaller py) is *higher* in layout (larger y)
    _, ly_top = cal.pixel_to_layout(50, 10)
    _, ly_bottom = cal.pixel_to_layout(50, 90)
    assert ly_top > ly_bottom


def test_calibration_rejects_bad_input():
    with pytest.raises(ValueError):
        ImageCalibration(pixel_size_um=0, image_w_px=10, image_h_px=10)
    with pytest.raises(ValueError):
        ImageCalibration.from_hfw(-1, 10, 10)
