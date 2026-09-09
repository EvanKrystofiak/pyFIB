"""Coordinate transforms between SmartFIB *layout* space and the Qt scene.

This is the safety-critical seam of the editor: it decides where a shape the
operator draws on screen ends up in the milled pattern. It is deliberately kept
free of any Qt dependency so the geometry can be unit-tested without a display.

Conventions
-----------
* **Layout space** (what goes in the .ely file): micrometres, y-axis *up*
  positive, matching SmartFIB's on-screen axis labels.
* **Scene space** (Qt QGraphicsScene): we use **1 scene unit == 1 micrometre**
  so shapes are authored at true size, but Qt's y-axis points *down*. The only
  difference between the two spaces is therefore the sign of y.

Keeping scene units == micrometres means item geometry is stored at physical
scale; only the y sign flips at the layout boundary. Every model<->scene
conversion goes through :func:`layout_to_scene` / :func:`scene_to_layout` so the
sign convention lives in exactly one place.

A FIB image underlay is placed by :class:`ImageCalibration`, which maps image
pixels to micrometres via a pixel size (um/px) derived from the real image
geometry -- never assumed. (pyFIB learned this the hard way: pixel size must
come from the actual grabbed image width, not a stored/display value.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


def layout_to_scene(x_um: float, y_um: float) -> Tuple[float, float]:
    """Layout (um, y-up) -> scene (um, y-down). Only y flips sign."""
    return (x_um, -y_um)


def scene_to_layout(x_scene: float, y_scene: float) -> Tuple[float, float]:
    """Scene (um, y-down) -> layout (um, y-up). Inverse of layout_to_scene."""
    return (x_scene, -y_scene)


def layout_angle_to_scene(angle_deg: float) -> float:
    """Convert a layout rotation (CCW in y-up) to a scene rotation (y-down).

    Flipping the y axis reverses the sense of rotation, so a CCW layout angle
    becomes a CW scene angle. Qt's item rotation is clockwise-positive, which
    means the scene angle we want is simply the layout angle unchanged in
    magnitude but interpreted in Qt's frame; the net effect of the double flip
    (y-sign and Qt's CW-positive) is that the numeric value is preserved. We
    keep this as an explicit function so the reasoning is documented and any
    future correction lives in one spot.
    """
    return angle_deg


def scene_angle_to_layout(angle_deg: float) -> float:
    """Inverse of :func:`layout_angle_to_scene`."""
    return angle_deg


@dataclass
class ImageCalibration:
    """Maps a FIB image (pixels) into layout micrometres.

    Parameters
    ----------
    pixel_size_um:
        Micrometres per image pixel, derived from the *actual* image geometry
        (e.g. horizontal field width / image pixel width). Must be > 0.
    image_w_px, image_h_px:
        Image dimensions in pixels.
    center_x_um, center_y_um:
        Layout coordinate (um, y-up) that the image *centre* sits at. Defaults
        to the layout origin.
    """

    pixel_size_um: float
    image_w_px: int
    image_h_px: int
    center_x_um: float = 0.0
    center_y_um: float = 0.0

    def __post_init__(self) -> None:
        if self.pixel_size_um <= 0:
            raise ValueError("pixel_size_um must be positive")
        if self.image_w_px <= 0 or self.image_h_px <= 0:
            raise ValueError("image dimensions must be positive")

    @classmethod
    def from_hfw(
        cls,
        hfw_um: float,
        image_w_px: int,
        image_h_px: int,
        center_x_um: float = 0.0,
        center_y_um: float = 0.0,
    ) -> "ImageCalibration":
        """Build from horizontal field width (um) instead of pixel size.

        pixel_size = HFW / image_width_px. This is the correct source per the
        pyFIB rule: derive pixel size from the real image width, not a stored
        display value.
        """
        if hfw_um <= 0:
            raise ValueError("hfw_um must be positive")
        return cls(
            pixel_size_um=hfw_um / image_w_px,
            image_w_px=image_w_px,
            image_h_px=image_h_px,
            center_x_um=center_x_um,
            center_y_um=center_y_um,
        )

    @property
    def width_um(self) -> float:
        return self.image_w_px * self.pixel_size_um

    @property
    def height_um(self) -> float:
        return self.image_h_px * self.pixel_size_um

    @property
    def hfw_um(self) -> float:
        return self.width_um

    def pixel_to_layout(self, px: float, py: float) -> Tuple[float, float]:
        """Image pixel (origin top-left, y-down) -> layout um (y-up)."""
        dx = (px - self.image_w_px / 2.0) * self.pixel_size_um
        dy = (py - self.image_h_px / 2.0) * self.pixel_size_um
        return (self.center_x_um + dx, self.center_y_um - dy)

    def layout_to_pixel(self, x_um: float, y_um: float) -> Tuple[float, float]:
        """Layout um (y-up) -> image pixel (origin top-left, y-down)."""
        dx = (x_um - self.center_x_um) / self.pixel_size_um
        dy = (self.center_y_um - y_um) / self.pixel_size_um
        return (self.image_w_px / 2.0 + dx, self.image_h_px / 2.0 + dy)

    def scene_rect_um(self) -> Tuple[float, float, float, float]:
        """Return (x, y, w, h) in scene units (um, y-down) to place the pixmap.

        The pixmap's top-left in scene space corresponds to the image's
        top-left pixel. Scene y is down, image y is down, so no flip is needed
        for the pixmap itself.
        """
        left = self.center_x_um - self.width_um / 2.0
        # image top edge is at layout y = center_y + height/2 (y-up);
        # convert that top edge to scene y (y-down) => -(center_y + h/2)
        top_scene = -(self.center_y_um + self.height_um / 2.0)
        return (left, top_scene, self.width_um, self.height_um)
