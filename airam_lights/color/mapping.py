"""Color mapping engine.

Pure functions/classes converting already-extracted frequency-band levels
(plain floats, 0..1) into Color values. Deliberately has no dependency on the
DSP module's SpectrumFrame type, PyAudio, or Tuya - this is what lets the
color system be tested and iterated on completely independently of both the
audio capture and the lamp-control layers (see README architecture section).

Continuous behavior is the whole point here: every function below is a smooth
(if sometimes non-linear via gamma/response-curve) function of its inputs -
there is no discrete "if bass > threshold: red" branching anywhere.
"""
from __future__ import annotations

import math

from ..config.schema import ColorMappingConfig, ChannelMap, HSVModeConfig, RGBModeConfig
from .models import Color, clip, lerp


def apply_response_curve(x: float, curve: str) -> float:
    """Reshape a 0..1 level before gain/gamma are applied.

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


def _map_channel(level: float, ch: ChannelMap, sensitivity: float, curve: str, extra_mult: float = 1.0) -> float:
    x = clip(level) * sensitivity * extra_mult
    x = apply_response_curve(x, curve)
    x *= ch.gain
    span = ch.max_level - ch.min_level
    if span <= 1e-6:
        windowed = 0.0
    else:
        windowed = clip((x - ch.min_level) / span)
    gamma = max(ch.gamma, 1e-3)
    return windowed ** (1.0 / gamma)


class ColorMappingEngine:
    """Stateless (no smoothing/history here - that lives in the engine layer
    so it can be applied uniformly to the final RGB output regardless of
    which mode produced it)."""

    def __init__(self, config: ColorMappingConfig):
        self.config = config

    def update_config(self, config: ColorMappingConfig) -> None:
        self.config = config

    # -- RGB Frequency / Custom (same mechanism, different default ranges) --

    def compute_rgb(
        self,
        level_r: float,
        level_g: float,
        level_b: float,
        cfg: "RGBModeConfig | None" = None,
        mult_r: float = 1.0,
        mult_g: float = 1.0,
        mult_b: float = 1.0,
    ) -> Color:
        cfg = cfg or self.config.rgb
        curve = self.config.response_curve
        r = _map_channel(level_r, cfg.r, cfg.sensitivity, curve, mult_r)
        g = _map_channel(level_g, cfg.g, cfg.sensitivity, curve, mult_g)
        b = _map_channel(level_b, cfg.b, cfg.sensitivity, curve, mult_b)
        return self.apply_global(Color(r, g, b))

    # -- HSV Music mode -------------------------------------------------------

    def compute_hsv(
        self,
        centroid_hz: float,
        overall_energy: float,
        contrast: float,
        low_hz: float = 20.0,
        high_hz: float = 16000.0,
        cfg: "HSVModeConfig | None" = None,
    ) -> Color:
        cfg = cfg or self.config.hsv
        curve = self.config.response_curve

        # log-scale position of the centroid within [low_hz, high_hz] -> hue
        low = max(low_hz, 1.0)
        high = max(high_hz, low * 2)
        t = (math.log(clip(centroid_hz, low, high)) - math.log(low)) / (math.log(high) - math.log(low))
        hue = lerp(cfg.hue_min_deg, cfg.hue_max_deg, t)

        energy = apply_response_curve(clip(overall_energy) * cfg.sensitivity, curve)
        value = lerp(cfg.brightness_min, cfg.brightness_max, energy)

        sat = clip(cfg.saturation_base + clip(contrast) * cfg.saturation_contrast_gain)

        return self.apply_global(Color.from_hsv(hue, sat, value))

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
        (RGB, HSV, Beat Sync, Peak Flash) - so global settings here
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

