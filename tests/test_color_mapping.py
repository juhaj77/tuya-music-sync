"""Unit tests for the color mapping engine - pure functions, no audio or
network dependencies (this is the whole point of the module split)."""
import pytest

from airam_lights.color.mapping import ColorMappingEngine
from airam_lights.color.models import Color
from airam_lights.config.schema import ColorMappingConfig


def test_band_colors_brightness_is_continuous_between_min_and_max():
    cfg = ColorMappingConfig()
    engine = ColorMappingEngine(cfg)
    levels = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0]
    values = [engine.compute_band_colors(0.0, lv).to_hsv()[2] for lv in levels]
    # Strictly non-decreasing, and no big jump between adjacent small steps in input.
    assert values == sorted(values)
    for i in range(1, len(values)):
        assert abs(values[i] - values[i - 1]) < 0.5
    assert values[0] == pytest.approx(cfg.band_colors.brightness_min, abs=0.01)
    assert values[-1] == pytest.approx(cfg.band_colors.brightness_max, abs=0.01)


def test_band_colors_keeps_the_hue_and_saturation():
    engine = ColorMappingEngine(ColorMappingConfig())
    hue, saturation, _ = engine.compute_band_colors(240.0, 1.0).to_hsv()
    assert hue == pytest.approx(240.0, abs=1.0) and saturation == pytest.approx(1.0, abs=0.01)


def test_hsv_mode_low_freq_vs_high_freq_hue_differs():
    engine = ColorMappingEngine(ColorMappingConfig())
    low = engine.compute_hsv(centroid_hz=100.0, overall_energy=0.5, contrast=0.5)
    high = engine.compute_hsv(centroid_hz=8000.0, overall_energy=0.5, contrast=0.5)
    h_low, _, _ = low.to_hsv()
    h_high, _, _ = high.to_hsv()
    assert h_low != h_high


def test_color_rgb255_roundtrip():
    c = Color(0.2, 0.5, 0.8)
    r, g, b = c.to_rgb255()
    assert (r, g, b) == (51, 128, 204)


def test_peak_flash_zero_saturation_is_white():
    engine = ColorMappingEngine(ColorMappingConfig())
    # saturation=0 (full "whiteness") must produce a neutral gray/white at
    # the given brightness, regardless of hue - this is the treble->white
    # blend's core assumption.
    color = engine.compute_peak_flash(hue_deg=123.0, saturation=0.0, value=0.8)
    assert abs(color.r - color.g) < 1e-6
    assert abs(color.g - color.b) < 1e-6
    assert abs(color.r - 0.8) < 1e-6


def test_peak_flash_full_saturation_is_colorful():
    engine = ColorMappingEngine(ColorMappingConfig())
    color = engine.compute_peak_flash(hue_deg=0.0, saturation=1.0, value=1.0)
    # Hue 0 = red: R should dominate G and B.
    assert color.r > color.g
    assert color.r > color.b


def test_invert_brightness_flips_value_for_every_mode():
    cfg = ColorMappingConfig()
    cfg.invert_brightness = True
    engine = ColorMappingEngine(cfg)

    # Band Colors: level 0 should now be bright, level 1 should now be dark.
    dark_input = engine.compute_band_colors(0.0, 0.0)
    bright_input = engine.compute_band_colors(0.0, 1.0)
    _, _, v_dark_input = dark_input.to_hsv()
    _, _, v_bright_input = bright_input.to_hsv()
    assert v_dark_input > v_bright_input  # inverted: input 0 -> bright, input 1 -> dark

    # Same check for Beat Sync: its value 0 now shows bright.
    _, _, v_dim = engine.compute_beat_sync(0.0, 1.0, 0.0).to_hsv()
    _, _, v_bright = engine.compute_beat_sync(0.0, 1.0, 1.0).to_hsv()
    assert v_dim > v_bright


def test_invert_brightness_off_by_default_is_normal():
    engine = ColorMappingEngine(ColorMappingConfig())  # invert_brightness defaults False
    dark = engine.compute_beat_sync(0.0, 1.0, 0.0)
    bright = engine.compute_beat_sync(0.0, 1.0, 1.0)
    _, _, v_dark = dark.to_hsv()
    _, _, v_bright = bright.to_hsv()
    assert v_bright > v_dark  # normal (non-inverted) behavior
