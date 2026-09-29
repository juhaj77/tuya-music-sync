"""Minimal color value type + HSV conversions.

Kept independent of numpy/Qt/Tuya on purpose - this is pure, easily testable
color math.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Optional


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * clip(t)


def circular_lerp_deg(a_deg: float, b_deg: float, t: float) -> float:
    """Interpolate between two angles (degrees, wrapping at 360) always
    taking the shortest path - a plain lerp would go the wrong way round
    whenever the two angles straddle the 0/360 boundary."""
    delta = ((b_deg - a_deg + 180.0) % 360.0) - 180.0
    return (a_deg + delta * clip(t)) % 360.0


@dataclass
class Color:
    r: float  # 0..1
    g: float  # 0..1
    b: float  # 0..1

    def clamped(self) -> "Color":
        return Color(clip(self.r), clip(self.g), clip(self.b))

    def to_rgb255(self) -> "tuple[int, int, int]":
        c = self.clamped()
        return (round(c.r * 255), round(c.g * 255), round(c.b * 255))

    def to_hsv(self) -> "tuple[float, float, float]":
        """Returns (hue_deg 0..360, saturation 0..1, value 0..1)."""
        c = self.clamped()
        h, s, v = colorsys.rgb_to_hsv(c.r, c.g, c.b)
        return h * 360.0, s, v

    @classmethod
    def from_hsv(cls, hue_deg: float, saturation: float, value: float) -> "Color":
        h = (hue_deg % 360.0) / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, clip(saturation), clip(value))
        return cls(r, g, b)

    @classmethod
    def black(cls) -> "Color":
        return cls(0.0, 0.0, 0.0)

    def distance(self, other: "Color") -> float:
        """Simple perceptual-ish distance used for the min-change threshold."""
        return max(abs(self.r - other.r), abs(self.g - other.g), abs(self.b - other.b))

    def blend(self, other: "Color", t: float) -> "Color":
        """Linear RGB blend toward `other`. t=0 -> self, t=1 -> other. Used
        by the Chase overlay to fade its highlight color in/out across
        nearby lamp positions."""
        t = clip(t)
        return Color(
            self.r + (other.r - self.r) * t,
            self.g + (other.g - self.g) * t,
            self.b + (other.b - self.b) * t,
        )


@dataclass
class WhiteTarget:
    """A target for the bulb's WHITE work_mode (DP 21="white"): brightness +
    color temperature, instead of RGB color (DP 24, work_mode="colour").
    Both are 0..1 here; `temp` is 0=warmest .. 1=coolest, mapped onto the
    device's own confirmed 0-1000 `temp_value_v2` range at send time (see
    lamps/tuya_device.py). Kept as its own type (not a Color) since the two
    work modes are mutually exclusive on the physical bulb and mean
    different things."""

    brightness: float  # 0..1
    temp: float  # 0..1, 0=warmest .. 1=coolest
    # The colour shown at the same time underneath the white (the RGB LEDs),
    # for crossfades - only bulbs driven through the real-time control
    # datapoint can show both at once (see NetworkConfig.lamp_transitions);
    # with "legacy" it's ignored and the bulb shows only the white.
    under: Optional["Color"] = None

    def clamped(self) -> "WhiteTarget":
        return WhiteTarget(clip(self.brightness), clip(self.temp), self.under)

    def distance(self, other: "WhiteTarget") -> float:
        d = max(abs(self.brightness - other.brightness), abs(self.temp - other.temp))
        if self.under is not None or other.under is not None:
            a = self.under or Color.black()
            b = other.under or Color.black()
            d = max(d, a.distance(b))
        return d

    def to_preview_color(self) -> Color:
        """A perceptually-plausible RGB approximation of this white-balance
        setting, used ONLY for UI swatches/previews (lamp tiles etc.) - the
        real bulb command sent is brightness/temp percentages via
        LampDevice.set_white(), never this RGB value."""
        # Warm (temp=0) -> a warm amber hue; cool (temp=1) -> a cool blue-white hue.
        hue = lerp(30.0, 210.0, self.temp)
        white = Color.from_hsv(hue, 0.35, self.brightness)
        if self.under is None:
            return white
        u = self.under
        return Color(clip(white.r + u.r), clip(white.g + u.g), clip(white.b + u.b))
