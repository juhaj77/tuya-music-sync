"""Color mapping engine.

Pure functions/classes converting already-extracted frequency-band levels
(plain floats, 0..1) into Color values. Deliberately has no dependency on the
DSP module's SpectrumFrame type, PyAudio, or Tuya - this is what lets the
color system be tested and iterated on completely independently of both the
audio capture and the lamp-control layers (see README architecture section).

Continuous behavior is the whole point here: every function below is a smooth
(if sometimes non-linear via the response curve) function of its inputs -
there is no discrete "if bass > threshold: red" branching anywhere.
"""
from __future__ import annotations

import math

from ..config.schema import BandColorsModeConfig, ColorMappingConfig, ColorStepsModeConfig
from .models import Color, clip, lerp


def apply_response_curve(x: float, curve: str) -> float:
    """Reshape a 0..1 level before it becomes brightness.

    - linear: unchanged.
    - log:    boosts quiet detail (log-like loudness perception).
    - exp2:   suppresses quiet noise, emphasizes strong peaks.
    """
    x = clip(x)
    if curve == "log":
        return math.log1p(x * 9.0) / math.log(10.0)
    if curve == "exp2":
        return x * x
    return x


class ColorMappingEngine:
    """Stateless (no smoothing/history here - that lives in the engine layer
    so it can be applied uniformly to the final RGB output regardless of
    which mode produced it)."""

    def __init__(self, config: ColorMappingConfig):
        self.config = config

    def update_config(self, config: ColorMappingConfig) -> None:
        self.config = config

    # -- Band Colors mode ---------------------------------------------------

    def compute_band_colors(self, hue_deg: float, level: float, cfg: "BandColorsModeConfig | None" = None) -> Color:
        """The engine's BandColorTracker (effects/band_colors.py) works out
        the hue and a 0..1 level from the bands; this maps the level through
        the response curve onto Brightness min..max."""
        cfg = cfg or self.config.band_colors
        value = lerp(cfg.brightness_min, cfg.brightness_max, apply_response_curve(level, self.config.response_curve))
        return self.apply_global(Color.from_hsv(hue_deg, clip(cfg.saturation), value))

    # -- Color Steps mode ------------------------------------------------------

    def compute_color_steps(self, hue_deg: float, level: float, cfg: "ColorStepsModeConfig | None" = None) -> Color:
        """The engine's ColorStepTracker (effects/color_steps.py) works out each
        position's hue and 0..1 level; this maps the level through the response
        curve onto Brightness min..max."""
        cfg = cfg or self.config.color_steps
        value = lerp(cfg.brightness_min, cfg.brightness_max, apply_response_curve(level, self.config.response_curve))
        return self.apply_global(Color.from_hsv(hue_deg, clip(cfg.saturation), value))

    # -- Beat Sync mode ---------------------------------------------------------

    def compute_beat_sync(self, hue_deg: float, saturation: float, value: float) -> Color:
        """The engine owns the beat detection + hue/brightness envelope state
        (see engine/visualization_engine.py); this just applies the same
        global brightness/saturation post-processing as every other mode."""
        return self.apply_global(Color.from_hsv(hue_deg, saturation, value))

    # -- Peak Flash mode ----------------------------------------------------------

    def compute_peak_flash(self, hue_deg: float, saturation: float, value: float) -> Color:
        """Same shape as compute_beat_sync - the engine already computes the
        treble-driven desaturation into `saturation` and the peak-driven
        flash into `value` before calling this, so this stays a thin,
        self-documenting wrapper consistent with every other mode."""
        return self.apply_global(Color.from_hsv(hue_deg, saturation, value))

    # -- global post-processing -------------------------------------

    def apply_global(self, color: Color) -> Color:
        """Called as the last step by every single mode's compute_*() method
        (Band Colors, Color Steps, Beat Sync, Peak Flash) - so global settings here
        apply uniformly across all of them without each mode needing its own
        copy of this logic."""
        h, s, v = color.to_hsv()
        if self.config.invert_brightness:
            # 0 becomes fully bright, 1 becomes black - inverted on the raw
            # per-mode value, before the brightness multiplier below, so the
            # multiplier still scales overall intensity on top of it.
            v = 1.0 - v
        s = clip(s * self.config.saturation)
        v = clip(v * self.config.brightness)
        return Color.from_hsv(h, s, v)

