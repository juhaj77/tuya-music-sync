import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from airam_lights.config.builtin_presets import BUILTIN_PRESETS, apply_builtin_preset  # noqa: E402
from airam_lights.config.schema import (  # noqa: E402
    COLOR_MODES,
    PULSE_TRIGGERS,
    RELEASE_CURVES,
    SYNC_MODES,
    WHITE_TEMP_MODES,
    AppConfig,
)
from airam_lights.dsp.hue_step import SOURCES as HUE_STEP_SOURCES  # noqa: E402
from airam_lights.effects import pulse_sequencer as ps  # noqa: E402

# Personal / installation-specific settings a preset must never overwrite.
PROTECTED = {
    "beat_sync": {
        "white_pulse_white_brightness", "white_pulse_cool_ratio",
        "fade_brightness", "glide_hue", "hue_glide_deg", "hue_glide_timing",
    },
    "chase": {"width", "intensity", "num_rotators"},
    "group_switch": {"intensity", "num_active_groups"},
    "rhythm": {"detect_low_hz", "detect_high_hz", "sensitivity", "min_interval_ms", "min_energy", "lead_ms"},
    # What the walk steps through depends on how the lamps are set up; the
    # strobe's brightness and rate ceiling are a comfort and safety limit.
    "sequencer": {"walk_positions", "strobe_max_hz", "strobe_brightness"},
}

# Each preset says which layers are on, so the look is the same whatever was on before.
LAYER_SWITCHES = {
    "sequencer": ("enabled",),
    "chase": ("enabled",),
    "group_switch": ("enabled",),
}
SEQUENCER_LAYERS = ("strobe_enabled", "flash_strobe_enabled", "color_sweep_enabled", "dark_sweep_enabled")

CHOICES = {
    ("color_mapping", "mode"): COLOR_MODES,
    ("beat_sync", "hue_mode"): ("random", "step", "spectrum"),
    ("beat_sync", "hue_step_source"): HUE_STEP_SOURCES,
    ("beat_sync", "spectrum_source"): HUE_STEP_SOURCES[1:],
    ("beat_sync", "white_pulse_target"): ("all", "chase", "group", "rotate"),
    ("beat_sync", "white_pulse_release_curve"): RELEASE_CURVES,
    ("beat_sync", "white_pulse_temp_mode"): WHITE_TEMP_MODES,
    ("chase", "color_mode"): ("custom", "complementary", "hue_shift", "hue_shift_relative"),
    ("chase", "hue_step_source"): HUE_STEP_SOURCES,
    ("chase", "falloff_curve"): ("linear", "bezier"),
    ("group_switch", "color_mode"): ("custom", "complementary", "hue_shift", "hue_shift_relative"),
    ("group_switch", "hue_step_source"): HUE_STEP_SOURCES,
    ("sequencer", "white_pattern"): ps.WHITE_PATTERNS,
    ("sequencer", "dark_pattern"): ps.DARK_PATTERNS,
    ("sequencer", "group_walk"): ps.GROUP_WALKS,
    ("sequencer", "strobe_placement"): ps.STROBE_PLACEMENTS,
    ("sequencer", "flash_strobe_placement"): ps.FLASH_STROBE_PLACEMENTS,
    ("sequencer", "color_sweep_placement"): ps.COLOR_SWEEP_PLACEMENTS,
    ("sequencer", "dark_sweep_placement"): ps.COLOR_SWEEP_PLACEMENTS,
    ("sequencer", "color_sweep_direction"): ps.COLOR_SWEEP_DIRECTIONS,
    ("sequencer", "dark_sweep_direction"): ps.COLOR_SWEEP_DIRECTIONS,
    ("sequencer", "color_sweep_color"): ps.COLOR_SWEEP_COLORS,
    ("sequencer", "strobe_min_level"): ps.LEVELS,
    ("sequencer", "flash_strobe_min_level"): ps.LEVELS,
    ("sequencer", "color_sweep_min_level"): ps.LEVELS,
    ("sequencer", "dark_sweep_min_level"): ps.LEVELS,
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


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_preset_values_are_valid_choices(name):
    preset = BUILTIN_PRESETS[name]
    for (section, key), allowed in CHOICES.items():
        if key in preset.get(section, {}):
            assert preset[section][key] in allowed, (name, section, key)


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_preset_says_which_layers_are_on(name):
    preset = BUILTIN_PRESETS[name]
    for section, keys in LAYER_SWITCHES.items():
        for key in keys:
            assert key in preset.get(section, {}), (name, section, key)
    if preset["sequencer"]["enabled"]:
        for key in SEQUENCER_LAYERS:
            assert key in preset["sequencer"], (name, key)
        assert {"dark_pulse_enabled", "white_pulse_enabled"} <= set(preset["beat_sync"]), name
    # The same look on a fresh install's tuned show and on the plain defaults.
    looks = []
    for config in (AppConfig(), AppConfig.with_defaults()):
        apply_builtin_preset(config, name)
        sq = config.sequencer
        looks.append((
            config.color_mapping.mode, sq.enabled, config.chase.enabled, config.group_switch.enabled,
            sq.enabled and tuple(getattr(sq, key) for key in SEQUENCER_LAYERS),
        ))
    assert looks[0] == looks[1]


def test_preset_keeps_existing_white_depth():
    config = AppConfig()
    config.color_mapping.beat_sync.white_pulse_white_brightness = 0.81
    config.chase.width = 3.0
    config.sequencer.walk_positions = "chase_order"
    config.sequencer.strobe_max_hz = 4.0
    apply_builtin_preset(config, "Strobe - rolls into the phrase")
    assert config.color_mapping.beat_sync.white_pulse_white_brightness == 0.81
    assert config.chase.width == 3.0
    assert config.sequencer.walk_positions == "chase_order"
    assert config.sequencer.strobe_max_hz == 4.0


def test_the_looks_follow_the_music():
    """Every Beat Sync look picks its colours from the music (the hue step or
    the spectrum hue), and every Chase / Group Switch that turns the hue
    turns it by a step the music sets."""
    for name, preset in BUILTIN_PRESETS.items():
        if preset["color_mapping"]["mode"] == "beat_sync":
            bs = preset["beat_sync"]
            assert bs["hue_mode"] == "spectrum" or bs.get("hue_step_source", "fixed") != "fixed", name
        for overlay in ("chase", "group_switch"):
            cfg = preset[overlay]
            if cfg.get("color_mode", "").startswith("hue_shift"):
                assert cfg.get("hue_step_source", "fixed") != "fixed", (name, overlay)
    assert any(p["color_mapping"]["mode"] == "band_colors" for p in BUILTIN_PRESETS.values())


# -- through the settings tabs -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from airam_lights.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w.controller.shutdown()
    w.close()


def _approx_equal(a, b):
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_approx_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_approx_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, abs=1e-6)
    return a == b


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_every_preset_value_fits_the_settings_tabs(window, qapp, name):
    """Applied and shown, every slider and list holds the preset's value -
    reading the tabs back changes nothing (a value outside a slider's range
    or missing from a list would be clamped or lost on the next edit)."""
    controller = window.controller
    apply_builtin_preset(controller.config, name)
    controller.replace_config_settings()
    qapp.processEvents()
    assert window.tabs.widget(0)._mode_buttons[BUILTIN_PRESETS[name]["color_mapping"]["mode"]].isChecked()
    before = controller.config.to_dict()
    color_tab, effects = window.tabs.widget(2), window.tabs.widget(3)
    color_tab._on_beat_changed()
    color_tab._on_hsv_changed()
    effects.rhythm._on_rhythm_changed()
    effects.overlays._on_chase_changed()
    effects.overlays._on_group_switch_changed()
    effects.pulses._on_pulse_changed()
    effects.pulses._on_sequencer_changed()
    after = controller.config.to_dict()
    for section in ("color_mapping", "rhythm", "sequencer", "chase", "group_switch"):
        assert _approx_equal(before[section], after[section]), (name, section, {
            k: (before[section][k], after[section][k])
            for k in before[section] if not _approx_equal(before[section][k], after[section][k])
        })
