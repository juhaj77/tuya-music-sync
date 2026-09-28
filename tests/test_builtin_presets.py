import pytest

from airam_lights.config.builtin_presets import BUILTIN_PRESETS, apply_builtin_preset
from airam_lights.config.schema import PULSE_TRIGGERS, SYNC_MODES, AppConfig

# Personal / installation-specific settings a preset must never overwrite.
PROTECTED = {
    "beat_sync": {
        "white_pulse_white_brightness", "white_pulse_cool_ratio",
        "fade_brightness", "glide_hue", "hue_glide_deg", "hue_glide_timing",
    },
    "chase": {"width", "intensity", "num_rotators"},
    "group_switch": {"intensity"},
    "rhythm": {"detect_low_hz", "detect_high_hz", "sensitivity", "lead_ms"},
}


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_preset_applies_and_round_trips(name):
    config = AppConfig()
    apply_builtin_preset(config, name)
    assert config.rhythm.shared_clock
    assert config.color_mapping.beat_sync.white_pulse_trigger in PULSE_TRIGGERS
    assert config.color_mapping.beat_sync.dark_pulse_trigger in PULSE_TRIGGERS
    assert config.chase.sync_mode in SYNC_MODES
    assert config.group_switch.sync_mode in SYNC_MODES
    assert AppConfig.from_dict(config.to_dict()).to_dict() == config.to_dict()


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_preset_leaves_personal_settings_alone(name):
    preset = BUILTIN_PRESETS[name]
    for section, keys in PROTECTED.items():
        assert not keys & set(preset.get(section, {})), (name, section)


def test_preset_keeps_existing_white_depth():
    config = AppConfig()
    config.color_mapping.beat_sync.white_pulse_white_brightness = 0.81
    config.chase.width = 3.0
    apply_builtin_preset(config, "Groove - one color per bar")
    assert config.color_mapping.beat_sync.white_pulse_white_brightness == 0.81
    assert config.chase.width == 3.0
