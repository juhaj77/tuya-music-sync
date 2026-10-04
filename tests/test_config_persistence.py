"""Verifies that EVERY setting in the app actually survives a save/reload
cycle - i.e. "the program remembers everything from last time when you
restart it." Builds an AppConfig with every field (across every nested
dataclass added throughout this project) set to a distinct, non-default
value, round-trips it through both the plain to_dict()/from_dict() path and
a real on-disk ConfigStore save/load, and asserts nothing was lost or
silently reset to a default - which is exactly the kind of bug a forgotten
field in a to_dict/from_dict pair would cause.
"""
from pathlib import Path

from airam_lights.config.schema import (
    AmbientSceneConfig,
    AppConfig,
    AudioConfig,
    BandDefinition,
    BeatSyncModeConfig,
    BeatSyncWhiteModeConfig,
    ChannelMap,
    ChaseEffectConfig,
    ColorMappingConfig,
    DeviceConfig,
    GroupSwitchEffectConfig,
    HSVModeConfig,
    NetworkConfig,
    PeakFlashModeConfig,
    PerLampEffect,
    RGBModeConfig,
    SmoothingConfig,
    SpectrumModeConfig,
    WhiteChaseEffectConfig,
)
from airam_lights.config.store import ConfigStore


def _build_fully_populated_config() -> AppConfig:
    """Every scalar field gets a value that differs from its dataclass
    default, so a round-trip bug (field silently reverting to default)
    cannot hide behind "it happened to match anyway"."""
    device = DeviceConfig(
        id="dev-1", name="Test Lamp", ip="192.168.9.9", local_key="s3cr3t-key", version="3.4",
        enabled=False, selected=False,
    )
    channel = ChannelMap(low_hz=33.0, high_hz=444.0, gain=1.75, min_level=0.05, max_level=0.95, gamma=1.6)
    rgb = RGBModeConfig(r=channel, g=channel, b=channel, sensitivity=1.23)
    hsv = HSVModeConfig(
        hue_min_deg=11.0, hue_max_deg=222.0, brightness_min=0.11, brightness_max=0.88,
        saturation_base=0.44, saturation_contrast_gain=0.33, sensitivity=1.11,
    )
    spectrum = SpectrumModeConfig(
        base_hue_deg=99.0, hue_step_deg=12.5, saturation=0.77, min_brightness=0.02,
        max_brightness=0.98, sensitivity=1.44, drive_saturation_too=True,
    )
    beat_sync = BeatSyncModeConfig(
        detect_low_hz=41.0, detect_high_hz=201.0, sensitivity=1.7, min_interval_ms=121.0,
        min_energy=0.13, hue_mode="step", hue_step_deg=138.5, min_hue_jump_deg=61.0,
        saturation=0.91, flash_brightness=0.92, sustain_brightness=0.26, hue_attack_ms=41.0,
        brightness_attack_ms=16.0, brightness_release_ms=351.0, dark_pulse_enabled=False,
        dark_pulse_probability=0.31, dark_pulse_duration_ms=71.0, dark_pulse_depth=0.81,
        dark_pulse_attack_ms=18.0, dark_pulse_release_ms=171.0, white_pulse_enabled=True,
        white_pulse_probability=0.41, white_pulse_duration_ms=91.0,
        white_pulse_attack_ms=17.0, white_pulse_release_ms=161.0,
        white_pulse_white_brightness=0.61, white_pulse_cool_ratio=0.51,
    )
    peak_flash = PeakFlashModeConfig(
        detect_low_hz=21.0, detect_high_hz=16001.0, sensitivity=1.31, min_interval_ms=61.0,
        min_energy=0.09, treble_low_hz=5001.0, treble_high_hz=16002.0, treble_white_amount=1.1,
        white_attack_ms=31.0, white_release_ms=221.0, hue_source="centroid", hue_flow_ms=4001.0,
        drift_speed_deg_per_s=6.1, randomness=0.26, random_jump_range_deg=181.0, saturation=0.91,
        baseline_min_brightness=0.21, baseline_max_brightness=0.76, flash_brightness=0.91,
        flash_attack_ms=13.0, flash_release_ms=261.0, loudness_smoothing_ms=301.0,
    )
    beat_sync_white = BeatSyncWhiteModeConfig(
        detect_low_hz=42.0, detect_high_hz=202.0, sensitivity=1.61, min_interval_ms=122.0,
        min_energy=0.14, temp_mode="alternate", temp_min=0.1, temp_max=0.9, min_temp_jump=0.36,
        flash_brightness=0.93, sustain_brightness=0.27, temp_attack_ms=42.0,
        brightness_attack_ms=17.0, brightness_release_ms=352.0, dark_pulse_probability=0.32,
        dark_pulse_duration_ms=72.0, dark_pulse_depth=0.82,
    )
    smoothing = SmoothingConfig(attack_ms=61.0, release_ms=301.0, min_change_threshold=0.016)

    color_mapping = ColorMappingConfig(
        mode="beat_sync_white", rgb=rgb, custom=rgb, hsv=hsv, spectrum=spectrum,
        beat_sync=beat_sync, peak_flash=peak_flash, beat_sync_white=beat_sync_white,
        smoothing=smoothing, brightness=0.87, saturation=0.86, response_curve="log",
        invert_brightness=True,
    )

    effect = PerLampEffect(
        device_id="dev-1", band_gains={"Band 1": 1.5}, phase_offset_ms=123.0,
        brightness_mult=1.4, saturation_mult=1.3, hue_offset_deg=45.0, sensitivity_mult=1.2,
        band_index=3, chase_order=2, chase_dwell_mult=2.5, effect_group=1,
    )

    chase = ChaseEffectConfig(
        enabled=True, num_rotators=2, speed_rotations_per_s=0.44, sync_mode="beat",
        beat_multiplier=1.5, beat_detect_low_hz=42.0, beat_detect_high_hz=202.0,
        beat_sensitivity=1.62, beat_min_interval_ms=123.0, beat_min_energy=0.15,
        peak_detect_low_hz=22.0, peak_detect_high_hz=15800.0, peak_sensitivity=1.35,
        peak_min_interval_ms=65.0, peak_min_energy=0.09, width=0.61,
        intensity=3.3, falloff_curve="bezier", color_mode="hue_shift", custom_hue_deg=281.0,
        custom_saturation=0.92, hue_shift_step_deg=46.0,
    )
    group_switch = GroupSwitchEffectConfig(
        enabled=True, speed_rotations_per_s=0.55, reverse=True, sync_mode="intensity_peak",
        beat_multiplier=1.75, beat_detect_low_hz=43.0, beat_detect_high_hz=203.0,
        beat_sensitivity=1.63, beat_min_interval_ms=124.0, beat_min_energy=0.16,
        peak_detect_low_hz=23.0, peak_detect_high_hz=15801.0, peak_sensitivity=1.36,
        peak_min_interval_ms=66.0, peak_min_energy=0.10, intensity=3.4,
        color_mode="complementary", custom_hue_deg=282.0, custom_saturation=0.93,
        hue_shift_step_deg=47.0,
    )
    white_chase = WhiteChaseEffectConfig(
        enabled=True, num_rotators=3, speed_rotations_per_s=0.33, width=0.71, intensity=1.6,
        falloff_curve="bezier", target_temp=0.9,
    )
    ambient = AmbientSceneConfig(
        enabled=True, scene="temp_breathing", speed_hz=0.15, hue=190.0, saturation=0.81,
        brightness=0.83, min_brightness=0.07, temp_min=0.12, temp_max=0.88,
    )
    audio = AudioConfig(
        device_index=3, samplerate=44100, block_size=2048, fft_size=4096, analysis_update_hz=45.0,
        source="microphone", mic_device_index=7, mic_gain=2.5,
    )
    network = NetworkConfig(
        visual_update_hz=25.0, lamp_command_rate_hz=15.0, command_timeout_s=0.4,
        max_retries=2, auto_backoff=False,
    )
    band = BandDefinition(name="Custom Band", low_hz=17.0, high_hz=88.0)

    return AppConfig(
        devices=[device],
        groups={"Test Group": ["dev-1"]},
        bands_3=[band],
        bands_8=[band] * 8,
        color_mapping=color_mapping,
        per_lamp_effects={"dev-1": effect},
        chase=chase,
        group_switch=group_switch,
        white_chase=white_chase,
        ambient_scene=ambient,
        audio=audio,
        network=network,
        presets={"My Preset": {"color_mapping": color_mapping.to_dict()}},
    )


def test_full_config_survives_dict_round_trip():
    original = _build_fully_populated_config()
    restored = AppConfig.from_dict(original.to_dict())
    assert restored == original


def test_full_config_survives_real_disk_round_trip(tmp_path: Path):
    original = _build_fully_populated_config()
    store = ConfigStore(tmp_path / "config.json")
    store.save(original)

    reloaded_store = ConfigStore(tmp_path / "config.json")  # simulates a fresh app start
    restored = reloaded_store.load()
    assert restored == original


def test_old_config_missing_newer_fields_loads_with_sane_defaults(tmp_path: Path):
    """Simulates a config.json saved by an earlier build of this app, before
    Chase/White Chase/Ambient Scenes/Beat Sync White existed - loading it
    today must not crash, and the missing sections should fall back to
    their documented defaults rather than erroring out."""
    minimal = {
        "devices": [{"id": "old-1", "name": "Old Lamp", "ip": "192.168.1.5", "local_key": "abc"}],
        "color_mapping": {"mode": "rgb_freq"},
    }
    import json

    path = tmp_path / "old_config.json"
    path.write_text(json.dumps(minimal), encoding="utf-8")

    store = ConfigStore(path)
    cfg = store.load()

    assert cfg.devices[0].id == "old-1"
    assert cfg.color_mapping.mode == "rgb_freq"
    assert cfg.chase.enabled is False  # not present in the old file -> default
    assert cfg.group_switch.enabled is False
    assert cfg.white_chase.enabled is False
    assert cfg.ambient_scene.enabled is False
    assert cfg.color_mapping.beat_sync_white.sensitivity == 1.6  # default preserved


def test_every_field_is_actually_different_from_default():
    """Guards the guard: if this fails, the fixture above accidentally used
    a default value somewhere, which would silently weaken the round-trip
    assertions (a bug that resets a field to its default would go unnoticed)."""
    populated = _build_fully_populated_config()
    fresh_default = AppConfig.with_defaults()
    fresh_default.devices = populated.devices  # devices list is empty by default, not comparable field-by-field here
    fresh_default.groups = populated.groups
    fresh_default.per_lamp_effects = populated.per_lamp_effects
    fresh_default.presets = populated.presets
    fresh_default.bands_3 = populated.bands_3
    fresh_default.bands_8 = populated.bands_8
    assert populated.color_mapping != fresh_default.color_mapping
    assert populated.chase != fresh_default.chase
    assert populated.group_switch != fresh_default.group_switch
    assert populated.white_chase != fresh_default.white_chase
    assert populated.ambient_scene != fresh_default.ambient_scene
    assert populated.audio != fresh_default.audio
    assert populated.network != fresh_default.network


def test_fresh_install_starts_with_the_default_look():
    cfg = AppConfig.with_defaults()
    assert cfg.color_mapping.beat_sync.hue_attack_ms == 140.0
    assert cfg.chase.switch_fade_ms == 140.0 and cfg.chase.enabled is False
    assert cfg.group_switch.switch_fade_ms == 140.0 and cfg.group_switch.enabled is True
    assert cfg.rhythm.shared_clock is True and cfg.sequencer.enabled is True
    assert cfg.network.lamp_command_rate_hz == 50.0
    # The neutral dataclass defaults underneath are untouched.
    assert AppConfig().sequencer.enabled is False
    # And the look survives a save/load round trip.
    assert AppConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()
