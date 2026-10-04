from airam_lights.config.schema import AppConfig, PerLampEffect
from airam_lights.config.user_presets import apply_preset, snapshot_preset


def _config() -> AppConfig:
    cfg = AppConfig.with_defaults()
    cfg.per_lamp_effects = {
        "a": PerLampEffect(device_id="a", chase_order=0, chase_dwell_mult=2.0, effect_group=1),
        "b": PerLampEffect(device_id="b", effect_group=0),
        "c": PerLampEffect(device_id="c"),
    }
    cfg.chase.enabled = True
    cfg.group_switch.enabled = True
    cfg.group_switch.intensity = 1.7
    return cfg


def test_preset_restores_chase_and_group_layout():
    cfg = _config()
    preset = snapshot_preset(cfg)

    # Change everything after saving.
    cfg.chase.enabled = False
    cfg.group_switch.enabled = False
    cfg.group_switch.intensity = 3.0
    cfg.per_lamp_effects["a"].chase_dwell_mult = 1.0
    cfg.per_lamp_effects["a"].effect_group = None
    cfg.per_lamp_effects["b"].effect_group = 5
    cfg.per_lamp_effects["c"].chase_order = 3
    cfg.per_lamp_effects["c"].effect_group = 2

    apply_preset(cfg, preset)

    assert cfg.chase.enabled is True
    assert cfg.group_switch.enabled is True
    assert cfg.group_switch.intensity == 1.7
    a, b, c = (cfg.per_lamp_effects[k] for k in "abc")
    assert (a.chase_order, a.chase_dwell_mult, a.effect_group) == (0, 2.0, 1)
    assert (b.chase_order, b.chase_dwell_mult, b.effect_group) == (None, 1.0, 0)
    # "c" was in neither overlay when the preset was saved.
    assert (c.chase_order, c.chase_dwell_mult, c.effect_group) == (None, 1.0, None)


def test_preset_leaves_other_per_lamp_settings_alone():
    cfg = _config()
    preset = snapshot_preset(cfg)
    cfg.per_lamp_effects["a"].brightness_mult = 0.4
    apply_preset(cfg, preset)
    assert cfg.per_lamp_effects["a"].brightness_mult == 0.4


def test_old_preset_with_chase_orders_only_still_loads():
    cfg = _config()
    old = {
        "color_mapping": cfg.color_mapping.to_dict(),
        "chase": cfg.chase.to_dict(),
        "per_lamp_chase_orders": {"c": 4},
    }
    apply_preset(cfg, old)
    assert cfg.per_lamp_effects["c"].chase_order == 4
    assert cfg.per_lamp_effects["b"].effect_group == 0  # untouched


def test_preset_restores_command_rate_and_transitions_only():
    cfg = _config()
    cfg.network.lamp_command_rate_hz = 44.0
    cfg.network.lamp_transitions = "smooth"
    preset = snapshot_preset(cfg)

    cfg.network.lamp_command_rate_hz = 10.0
    cfg.network.lamp_transitions = "legacy"
    cfg.network.command_timeout_s = 0.9
    apply_preset(cfg, preset)

    assert cfg.network.lamp_command_rate_hz == 44.0
    assert cfg.network.lamp_transitions == "smooth"
    # Connection settings aren't part of the look.
    assert cfg.network.command_timeout_s == 0.9


def test_preset_without_network_keeps_current_rate():
    cfg = _config()
    preset = snapshot_preset(cfg)
    del preset["network"]
    cfg.network.lamp_command_rate_hz = 33.0
    apply_preset(cfg, preset)
    assert cfg.network.lamp_command_rate_hz == 33.0
