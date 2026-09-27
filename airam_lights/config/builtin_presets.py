"""Built-in "looks": ready-made combinations of the shared beat clock, Beat
Sync, Chase and Group Switch settings, applied together in one click.

Each preset only lists the settings that define its rhythm and color
behavior. Everything else is left as the user has it - in particular the
installation-specific values (Chase width/intensity, Group Switch intensity,
beat detection band/sensitivity, lead time) and the personal true-white
taste (white pulse depth, brightness, cool ratio), which a preset should
never silently overwrite.

All of them use the shared beat clock, so every layer moves on the same
beats: the Beat Sync flash on every beat, and slower layers (hue, Chase,
Group Switch, pulses) on fixed divisions of the bar.
"""
from __future__ import annotations

from typing import Dict

from .schema import AppConfig

_CLOCK = {"shared_clock": True, "tempo_lock": True}
_NO_SEQUENCER = {"enabled": False}

BUILTIN_PRESETS: Dict[str, dict] = {
    "Groove - one color per bar": {
        "description": (
            "Flash on every beat, a new color at each bar start, Chase stepping every beat and "
            "Group Switch changing group every bar. True white on the active group at bar starts."
        ),
        "rhythm": _CLOCK,
        "sequencer": _NO_SEQUENCER,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 120.0,
            "hue_every_n_beats": 4,
            "saturation": 1.0,
            "flash_brightness": 1.0,
            "sustain_brightness": 0.35,
            "hue_attack_ms": 30.0,
            "brightness_attack_ms": 10.0,
            "brightness_release_ms": 280.0,
            "dark_pulse_enabled": False,
            "white_pulse_enabled": True,
            "white_pulse_trigger": "downbeat",
            "white_pulse_probability": 1.0,
            "white_pulse_target": "group",
        },
        "chase": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 1,
            "beat_multiplier": 1.0,
            "color_mode": "hue_shift",
            "hue_shift_step_deg": 90.0,
        },
        "group_switch": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 4,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
    },
    "Calm - slow color flow": {
        "description": (
            "Gentle: colors fade to a neighboring hue every two bars, Chase steps every other beat, "
            "Group Switch every two bars. Occasional true white on bar starts."
        ),
        "rhythm": _CLOCK,
        "sequencer": _NO_SEQUENCER,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 60.0,
            "hue_every_n_beats": 8,
            "saturation": 1.0,
            "flash_brightness": 0.9,
            "sustain_brightness": 0.5,
            "hue_attack_ms": 250.0,
            "brightness_attack_ms": 20.0,
            "brightness_release_ms": 600.0,
            "dark_pulse_enabled": False,
            "white_pulse_enabled": True,
            "white_pulse_trigger": "downbeat",
            "white_pulse_probability": 0.5,
            "white_pulse_target": "group",
        },
        "chase": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 2,
            "beat_multiplier": 1.0,
            "color_mode": "hue_shift",
            "hue_shift_step_deg": 45.0,
        },
        "group_switch": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 8,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
    },
    "Club - punchy": {
        "description": (
            "High energy: a new color every two beats, true white galloping between random groups "
            "with doubles, dark breaths and stutters, Chase every beat and Group Switch every two beats."
        ),
        "rhythm": _CLOCK,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 137.5,
            "hue_every_n_beats": 2,
            "saturation": 1.0,
            "flash_brightness": 1.0,
            "sustain_brightness": 0.15,
            "hue_attack_ms": 5.0,
            "brightness_attack_ms": 5.0,
            "brightness_release_ms": 180.0,
            "dark_pulse_enabled": True,
            "dark_pulse_trigger": "downbeat",
            "dark_pulse_probability": 0.5,
            "dark_pulse_duration_ms": 60.0,
            "dark_pulse_depth": 1.0,
            "white_pulse_enabled": True,
            "white_pulse_trigger": "accent",
            "white_pulse_probability": 1.0,
            "white_pulse_target": "group",
        },
        "sequencer": {
            "enabled": True,
            "white_pattern": "gallop",
            "white_density": 0.8,
            "group_walk": "random",
            "double_chance": 0.3,
            "dark_pattern": "auto",
            "dark_density": 0.8,
            "dark_length": 1.0,
            "phrase_bars": 8,
            "fills": True,
            "phrase_accent": True,
            "drop_detection": True,
        },
        "chase": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 1,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
        "group_switch": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 2,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
    },
    "Dance - white across groups": {
        "description": (
            "Storytelling true white: flashes walk from group to group on musical patterns that get "
            "busier as the song gets louder, sometimes twice in one group; a fill and a dark breath end "
            "each 8-bar phrase and every lamp flashes on the next one. A new color every bar."
        ),
        "rhythm": _CLOCK,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 120.0,
            "hue_every_n_beats": 4,
            "saturation": 1.0,
            "flash_brightness": 1.0,
            "sustain_brightness": 0.35,
            "hue_attack_ms": 20.0,
            "brightness_attack_ms": 8.0,
            "brightness_release_ms": 260.0,
            "dark_pulse_enabled": True,
            "dark_pulse_duration_ms": 70.0,
            "dark_pulse_depth": 1.0,
            "white_pulse_enabled": True,
        },
        "sequencer": {
            "enabled": True,
            "white_pattern": "auto",
            "white_density": 0.9,
            "group_walk": "forward",
            "double_chance": 0.3,
            "dark_pattern": "auto",
            "dark_density": 0.75,
            "dark_length": 1.0,
            "phrase_bars": 8,
            "fills": True,
            "phrase_accent": True,
            "drop_detection": True,
        },
        "chase": {"enabled": False},
        "group_switch": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 4,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
    },
    "Chase focus": {
        "description": (
            "Only the Chase overlay moves (Group Switch off): one step per beat, a new base color "
            "per bar, true white following the Chase highlight on the hardest hits."
        ),
        "rhythm": _CLOCK,
        "sequencer": _NO_SEQUENCER,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 120.0,
            "hue_every_n_beats": 4,
            "sustain_brightness": 0.3,
            "brightness_release_ms": 300.0,
            "dark_pulse_enabled": False,
            "white_pulse_enabled": True,
            "white_pulse_trigger": "accent",
            "white_pulse_probability": 1.0,
            "white_pulse_target": "chase",
        },
        "chase": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 1,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
        "group_switch": {"enabled": False},
    },
    "Group focus": {
        "description": (
            "Only Group Switch moves (Chase off): the active group changes every two beats in the "
            "complementary color, a new base color per bar, true white on the active group's accents."
        ),
        "rhythm": _CLOCK,
        "sequencer": _NO_SEQUENCER,
        "color_mapping": {"mode": "beat_sync"},
        "beat_sync": {
            "hue_mode": "step",
            "hue_step_deg": 120.0,
            "hue_every_n_beats": 4,
            "sustain_brightness": 0.35,
            "brightness_release_ms": 300.0,
            "dark_pulse_enabled": False,
            "white_pulse_enabled": True,
            "white_pulse_trigger": "accent",
            "white_pulse_probability": 1.0,
            "white_pulse_target": "group",
        },
        "chase": {"enabled": False},
        "group_switch": {
            "enabled": True,
            "sync_mode": "clock",
            "clock_every_n_beats": 2,
            "beat_multiplier": 1.0,
            "color_mode": "complementary",
        },
    },
}


def _targets(config: AppConfig) -> dict:
    return {
        "rhythm": config.rhythm,
        "sequencer": config.sequencer,
        "color_mapping": config.color_mapping,
        "beat_sync": config.color_mapping.beat_sync,
        "chase": config.chase,
        "group_switch": config.group_switch,
    }


def apply_builtin_preset(config: AppConfig, name: str) -> None:
    """Writes the named preset's settings into `config` in place."""
    preset = BUILTIN_PRESETS[name]
    for section, target in _targets(config).items():
        for key, value in preset.get(section, {}).items():
            if not hasattr(target, key):
                raise KeyError(f"Preset '{name}': unknown setting {section}.{key}")
            setattr(target, key, value)
