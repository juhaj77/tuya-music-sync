"""The look a fresh install starts with (AppConfig.with_defaults()).

The dataclass defaults in schema.py stay the plain, neutral baseline - every
layer off or at its simplest - which old configs fall back on for missing
keys and the engine's tests build on. This is the tuned show on top of it:
Beat Sync on the shared beat clock, the pulse sequencer walking true-white
flashes through the lamp groups, Group Switch, soft 140 ms hue switches
everywhere and a command rate high enough to carry them. Chase is off but
already set up to match, so switching it on fits the rest.

Section names follow builtin_presets: "beat_sync" and "smoothing" live under
color_mapping, the rest are AppConfig fields.
"""
from __future__ import annotations

from typing import Dict

DEFAULT_LOOK: Dict[str, dict] = {
    "beat_sync": {
        "detect_low_hz": 30.0,
        "detect_high_hz": 20000.0,
        "sensitivity": 1.15,
        "min_interval_ms": 33.0,
        "min_energy": 0.05,
        "hue_mode": "step",
        "hue_step_deg": 85.0,
        "min_hue_jump_deg": 122.0,
        "sustain_brightness": 0.42,
        "hue_attack_ms": 140.0,
        "brightness_attack_ms": 8.0,
        "brightness_release_ms": 260.0,
        "glide_hue": True,
        "hue_glide_deg": 52.0,
        "dark_pulse_probability": 0.5,
        "dark_pulse_trigger": "downbeat",
        "dark_pulse_duration_ms": 97.0,
        "dark_pulse_attack_ms": 70.0,
        "dark_pulse_release_ms": 151.0,
        "white_pulse_probability": 1.0,
        "white_pulse_trigger": "accent",
        "white_pulse_duration_ms": 86.0,
        "white_pulse_attack_ms": 60.0,
        "white_pulse_release_ms": 240.0,
        "white_pulse_release_curve": "dynamic",
        "white_pulse_target": "rotate",
        "white_pulse_white_brightness": 0.44,
        "white_pulse_temp_mode": "bar",
    },
    "smoothing": {
        "min_change_threshold": 0.0088,
    },
    "rhythm": {
        "shared_clock": True,
        "detect_high_hz": 400.0,
        "sensitivity": 1.05,
        "min_interval_ms": 123.0,
        "min_energy": 0.2,
        "lead_ms": 65.0,
        "accent_ratio": 0.27,
    },
    "sequencer": {
        "enabled": True,
        "white_density": 0.61,
        "group_walk": "pingpong",
        "double_chance": 0.76,
        "white_accent_focus": 0.85,
        "white_build": 0.85,
        "min_group_gap_ms": 333.0,
        "dark_density": 0.75,
        "fills": False,
        "strobe_enabled": True,
        "strobe_placement": "half_phrase",
        "strobe_chance": 0.65,
        "strobe_min_gap_bars": 4,
        "strobe_max_hz": 13.0,
        "strobe_duty": 0.51,
        "strobe_brightness": 0.29,
        "strobe_wave": "bezier",
    },
    "chase": {
        "enabled": False,
        "speed_rotations_per_s": 0.468,
        "sync_mode": "clock",
        "clock_every_n_beats": 2,
        "beat_detect_high_hz": 20000.0,
        "beat_sensitivity": 1.4,
        "beat_min_interval_ms": 205.0,
        "peak_detect_high_hz": 20000.0,
        "peak_sensitivity": 1.12,
        "peak_min_interval_ms": 36.0,
        "width": 3.49,
        "intensity": 1.02,
        "falloff_curve": "bezier",
        "color_mode": "hue_shift",
        "custom_hue_deg": 359.0,
        "hue_shift_step_deg": 66.6,
        "switch_fade": True,
        "switch_fade_ms": 140.0,
    },
    "group_switch": {
        "enabled": True,
        "sync_mode": "clock",
        "beat_detect_high_hz": 20000.0,
        "beat_sensitivity": 1.15,
        "beat_min_interval_ms": 152.0,
        "peak_detect_high_hz": 20000.0,
        "peak_sensitivity": 1.2,
        "peak_min_interval_ms": 173.0,
        "intensity": 8.0,
        "color_mode": "hue_shift",
        "hue_shift_step_deg": 66.6,
        "fade_across_groups": True,
        "switch_fade_ms": 140.0,
    },
    "network": {
        "lamp_command_rate_hz": 50.0,
    },}
