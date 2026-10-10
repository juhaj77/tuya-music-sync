"""Regression tests for ManualLightController's persisted static color/white
balance (config.schema.ManualStateConfig) - reproduces the exact reported
bug: a manually-picked color (e.g. pure blue) was never saved anywhere, so
restarting the app left it looking like a dim chase backdrop blended toward
whatever hue the Chase settings happened to have (a "lighter blue") instead
of the color that was actually picked.
"""
from airam_lights.color.models import Color
from airam_lights.config.schema import AppConfig, ChaseEffectConfig, DeviceConfig
from airam_lights.engine.manual_engine import ManualLightController
from airam_lights.lamps.manager import LampManager


def _manager_with_one_lamp(config: AppConfig) -> LampManager:
    manager = LampManager(config.network, config.color_mapping.smoothing.min_change_threshold)
    manager.load_devices(config.devices)
    return manager


def test_picked_color_is_remembered_in_config():
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]
    manager = _manager_with_one_lamp(config)
    controller = ManualLightController(config, manager)

    pure_blue = Color(0.0, 0.0, 1.0)
    controller.set_color_for_selected(pure_blue)

    assert config.manual_state.last_mode == "rgb"
    assert config.manual_state.last_color_r == 0.0
    assert config.manual_state.last_color_g == 0.0
    assert config.manual_state.last_color_b == 1.0


def test_restart_restores_exact_previously_picked_color_even_with_chase_enabled():
    """This is the reported bug, reproduced directly: pick pure blue, then
    simulate a full app restart (a brand new ManualLightController reading
    the same, now-saved config) - even with Chase enabled and configured
    with an unrelated hue, the lamp must come back showing pure blue, not a
    dim/blended "lighter blue" chase backdrop."""
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]
    # Chase is enabled with an unrelated custom hue/backdrop - exactly the
    # situation that used to leak a "lighter blue" over the real color.
    config.chase = ChaseEffectConfig(enabled=True, custom_hue_deg=90.0)

    manager1 = _manager_with_one_lamp(config)
    session1 = ManualLightController(config, manager1)
    pure_blue = Color(0.0, 0.0, 1.0)
    session1.set_color_for_selected(pure_blue)
    assert session1.last_colors["a"] == pure_blue

    # Simulate save-to-disk-and-reload by round-tripping the config exactly
    # like ConfigStore does, then constructing a fresh controller from it -
    # this is precisely what happens on a real app restart.
    reloaded_config = AppConfig.from_dict(config.to_dict())

    manager2 = _manager_with_one_lamp(reloaded_config)
    session2 = ManualLightController(reloaded_config, manager2)  # restore_last_state() runs here

    assert session2.last_colors["a"] == pure_blue
    assert session2.mode == "rgb"


def test_restart_restores_white_balance_when_that_was_last_used():
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]

    manager1 = _manager_with_one_lamp(config)
    session1 = ManualLightController(config, manager1)
    session1.set_white_for_selected(brightness=0.6, temp=0.25)

    reloaded_config = AppConfig.from_dict(config.to_dict())
    manager2 = _manager_with_one_lamp(reloaded_config)
    session2 = ManualLightController(reloaded_config, manager2)

    assert session2.mode == "white"
    restored = session2.last_white_targets["a"]
    assert abs(restored.brightness - 0.6) < 1e-9
    assert abs(restored.temp - 0.25) < 1e-9


def test_turning_off_does_not_overwrite_the_remembered_color():
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]
    manager = _manager_with_one_lamp(config)
    controller = ManualLightController(config, manager)

    pure_blue = Color(0.0, 0.0, 1.0)
    controller.set_color_for_selected(pure_blue)
    controller.set_black_for_selected()  # e.g. turning the lamp off before closing the app

    assert config.manual_state.last_color_b == 1.0  # still remembers blue, not black


def test_color_and_white_go_out_together_per_group_and_come_back_after_restart():
    """Color + White tab: blue under cool white for one group, red under warm
    white for another - each lamp keeps its own after a restart, whichever
    lamps happen to be selected then."""
    config = AppConfig.with_defaults()
    config.devices = [
        DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=False),
        DeviceConfig(id="b", name="B", ip="10.0.0.2", local_key="k", selected=False),
    ]
    manager1 = _manager_with_one_lamp(config)
    session1 = ManualLightController(config, manager1)
    blue, red = Color(0.0, 0.2, 1.0), Color(1.0, 0.1, 0.0)
    session1.set_mix_for(["a"], brightness=0.7, temp=1.0, color=blue)
    session1.set_mix_for(["b"], brightness=0.5, temp=0.0, color=red)

    sent_a = manager1.workers["a"]._target_white
    assert sent_a is not None and sent_a.under == blue and sent_a.temp == 1.0
    assert manager1.workers["b"]._target_white.under == red

    reloaded_config = AppConfig.from_dict(config.to_dict())
    manager2 = _manager_with_one_lamp(reloaded_config)
    session2 = ManualLightController(reloaded_config, manager2)

    assert session2.mode == "white"
    restored_a, restored_b = manager2.workers["a"]._target_white, manager2.workers["b"]._target_white
    assert restored_a.under == blue and abs(restored_a.brightness - 0.7) < 1e-9
    assert restored_b.under == red and restored_b.temp == 0.0


def test_color_and_white_with_the_white_at_zero_is_sent_as_a_plain_color():
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]
    manager = _manager_with_one_lamp(config)
    controller = ManualLightController(config, manager)

    controller.set_mix_for(["a"], brightness=0.0, temp=0.5, color=Color(0.0, 0.0, 1.0))

    assert manager.workers["a"]._target_color == Color(0.0, 0.0, 1.0)
    assert manager.workers["a"]._target_white is None


def test_a_plain_color_replaces_the_remembered_color_and_white():
    config = AppConfig.with_defaults()
    config.devices = [DeviceConfig(id="a", name="A", ip="10.0.0.1", local_key="k", selected=True)]
    manager = _manager_with_one_lamp(config)
    controller = ManualLightController(config, manager)

    controller.set_mix_for(["a"], brightness=0.7, temp=1.0, color=Color(0.0, 0.0, 1.0))
    controller.set_color_for_selected(Color(0.0, 1.0, 0.0))

    assert "a" not in config.manual_state.mix_targets
    assert config.manual_state.last_mode == "rgb"


def test_white_chase_keeps_the_color_under_the_white():
    from airam_lights.color.models import WhiteTarget
    from airam_lights.config.schema import WhiteChaseEffectConfig
    from airam_lights.effects.chase import WhiteChaseAnimator

    blue = Color(0.0, 0.0, 1.0)
    animator = WhiteChaseAnimator(WhiteChaseEffectConfig(enabled=True))
    out = animator.apply({"a": WhiteTarget(0.5, 0.5, under=blue), "b": WhiteTarget(0.5, 0.5)}, [["a"], ["b"]])
    assert out["a"].under == blue
