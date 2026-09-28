"""Unit tests for the shared Chase overlay (effects/chase.py) - used by both
the music visualizer and the standalone manual control app, so this is
tested independently of both."""
from airam_lights.color.models import Color, WhiteTarget
from airam_lights.config.schema import ChaseEffectConfig, GroupSwitchEffectConfig, PerLampEffect, WhiteChaseEffectConfig
from airam_lights.effects.chase import (
    ChaseAnimator,
    GroupSwitchAnimator,
    WhiteChaseAnimator,
    get_chase_group_dwell_weights,
    get_chase_groups,
    get_group_switch_groups,
)


def _effects(order_by_id: dict) -> dict:
    return {device_id: PerLampEffect(device_id=device_id, chase_order=order) for device_id, order in order_by_id.items()}


def _group_switch_effects(group_by_id: dict) -> dict:
    return {device_id: PerLampEffect(device_id=device_id, effect_group=group) for device_id, group in group_by_id.items()}


def test_get_chase_groups_orders_ascending_and_excludes_unset():
    effects = _effects({"a": 2, "b": 0, "c": None, "d": 1})
    groups = get_chase_groups(effects, ["a", "b", "c", "d"])
    assert groups == [["b"], ["d"], ["a"]]


def test_get_chase_groups_shares_same_order_number():
    # Lamps "b" and "e" share order 0 - they must end up in the same group,
    # so they animate together (this is the "not a physical ring" fix).
    effects = _effects({"a": 1, "b": 0, "e": 0})
    groups = get_chase_groups(effects, ["a", "b", "e"])
    assert len(groups) == 2
    assert sorted(groups[0]) == ["b", "e"]
    assert groups[1] == ["a"]


def test_get_chase_groups_restricted_to_selected_ids():
    effects = _effects({"a": 0, "b": 1})
    groups = get_chase_groups(effects, ["a"])  # "b" not selected
    assert groups == [["a"]]


def test_chase_animator_highlights_nearest_group_and_dims_others():
    cfg = ChaseEffectConfig(enabled=True, width=0.6, intensity=3.0, color_mode="custom", custom_hue_deg=40.0)
    animator = ChaseAnimator(cfg)
    groups = [["a"], ["b"], ["c"], ["d"]]
    base_colors = {g[0]: Color(0.2, 0.2, 0.2) for g in groups}  # dim gray baseline everywhere

    animator.tick(dt=0.0, num_positions=len(groups))  # position stays at 0 with dt=0
    out = animator.apply(base_colors, groups)

    _, _, v_a = out["a"].to_hsv()
    _, _, v_c = out["c"].to_hsv()
    assert v_a > v_c  # "a" is at the chase position (index 0), "c" is opposite - should be dimmer


def test_chase_never_brightens_a_lamp_the_base_mode_set_to_black():
    """Regression test for the reported bug: the chase used to blend toward
    its own fixed brightness, which "filled in" deliberate dark moments
    (e.g. a Beat Sync dark pulse). Multiplicative brightness fixes this:
    0 * anything is still 0, no matter how high intensity/width are."""
    cfg = ChaseEffectConfig(enabled=True, width=5.0, intensity=8.0, color_mode="custom", custom_hue_deg=40.0)
    animator = ChaseAnimator(cfg)
    groups = [["a"], ["b"]]
    base_colors = {"a": Color.black(), "b": Color.black()}

    animator.tick(dt=0.0, num_positions=len(groups))
    out = animator.apply(base_colors, groups)

    for device_id in ("a", "b"):
        _, _, v = out[device_id].to_hsv()
        assert v == 0.0


def test_hue_shift_mode_varies_hue_by_position():
    # Narrow width so only the exact position the rotator sits on gets full
    # (weight=1) blending - lets us check each position's hue in isolation
    # by moving the rotator there directly, rather than reading a partial
    # blend at every position simultaneously from one fixed spot.
    cfg = ChaseEffectConfig(
        enabled=True, width=0.3, intensity=1.0, color_mode="hue_shift", custom_hue_deg=0.0, hue_shift_step_deg=90.0
    )
    animator = ChaseAnimator(cfg)
    groups = [["a"], ["b"], ["c"], ["d"]]
    base_colors = {g[0]: Color(0.5, 0.5, 0.5) for g in groups}

    expected_hues = [0.0, 90.0, 180.0, 270.0]
    for i, expected_hue in enumerate(expected_hues):
        animator.position = float(i)
        out = animator.apply(dict(base_colors), groups)
        h = out[groups[i][0]].to_hsv()[0]
        assert abs(((h - expected_hue + 180.0) % 360.0) - 180.0) < 1.0


def test_multiple_rotators_light_opposite_positions():
    cfg = ChaseEffectConfig(enabled=True, num_rotators=2, width=0.6, intensity=3.0)
    animator = ChaseAnimator(cfg)
    groups = [[f"lamp{i}"] for i in range(8)]
    base_colors = {g[0]: Color(0.2, 0.2, 0.2) for g in groups}

    animator.tick(dt=0.0, num_positions=len(groups))  # position 0.0
    out = animator.apply(base_colors, groups)

    _, _, v0 = out["lamp0"].to_hsv()
    _, _, v4 = out["lamp4"].to_hsv()  # opposite side with 8 groups / 2 rotators
    _, _, v2 = out["lamp2"].to_hsv()  # in between - should be dim
    assert v0 > v2
    assert v4 > v2
    assert abs(v0 - v4) < 1e-6  # both rotators produce the same boost


def test_too_few_groups_returns_colors_unchanged():
    cfg = ChaseEffectConfig(enabled=True)
    animator = ChaseAnimator(cfg)
    colors = {"a": Color(0.5, 0.5, 0.5)}
    out = animator.apply(colors, [["a"]])  # only 1 group - chase needs >= 2
    assert out is colors


# -- WhiteChaseAnimator ----------------------------------------------------------------------


def test_white_chase_moves_target_temp_toward_region():
    cfg = WhiteChaseEffectConfig(enabled=True, width=0.3, intensity=1.0, target_temp=1.0)
    animator = WhiteChaseAnimator(cfg)
    groups = [["a"], ["b"], ["c"]]
    base = {g[0]: WhiteTarget(0.5, 0.0) for g in groups}  # everyone starts warm (temp=0)

    animator.position = 0.0  # rotator sits exactly on group 0 ("a")
    out = animator.apply(dict(base), groups)

    assert abs(out["a"].temp - 1.0) < 0.05  # "a" pulled fully toward the cool target region
    assert out["b"].temp == 0.0  # untouched (weight 0 at this narrow width)
    assert out["c"].temp == 0.0


def test_white_chase_never_brightens_a_lamp_at_zero():
    cfg = WhiteChaseEffectConfig(enabled=True, width=5.0, intensity=8.0, target_temp=1.0)
    animator = WhiteChaseAnimator(cfg)
    groups = [["a"], ["b"]]
    base = {"a": WhiteTarget(0.0, 0.5), "b": WhiteTarget(0.0, 0.5)}

    animator.tick(dt=0.0, num_positions=len(groups))
    out = animator.apply(dict(base), groups)

    assert out["a"].brightness == 0.0
    assert out["b"].brightness == 0.0


def test_white_chase_too_few_groups_returns_targets_unchanged():
    cfg = WhiteChaseEffectConfig(enabled=True)
    animator = WhiteChaseAnimator(cfg)
    targets = {"a": WhiteTarget(0.5, 0.5)}
    out = animator.apply(targets, [["a"]])
    assert out is targets


def test_white_target_distance_and_clamp():
    a = WhiteTarget(0.2, 0.9)
    b = WhiteTarget(0.8, 0.1)
    assert abs(a.distance(b) - 0.8) < 1e-9
    clamped = WhiteTarget(1.5, -0.5).clamped()
    assert clamped.brightness == 1.0
    assert clamped.temp == 0.0


# -- falloff_curve: linear vs. bezier -------------------------------------------------------


def test_bezier_curve_dwells_longer_near_the_peak_than_linear():
    # Slightly off-center (dist=0.3 of width=1.0), the eased "bezier" curve
    # (smoothstep) must produce a HIGHER weight than the constant-rate
    # linear falloff - smoothstep(t) > t for all 0 < t < 1 - meaning the
    # highlight visibly holds its color longer before handing off, which is
    # exactly the reported "flies by too quickly" complaint.
    linear_animator = ChaseAnimator(ChaseEffectConfig(enabled=True, width=1.0, intensity=1.0, falloff_curve="linear"))
    bezier_animator = ChaseAnimator(ChaseEffectConfig(enabled=True, width=1.0, intensity=1.0, falloff_curve="bezier"))

    groups = [["a"], ["b"], ["c"], ["d"], ["e"]]
    base = {g[0]: Color(0.0, 0.0, 0.3) for g in groups}  # dim blue background, v=0.3 (room to boost)

    linear_animator.position = 2.3  # 0.3 away from group index 2 ("c")
    bezier_animator.position = 2.3
    linear_out = linear_animator.apply(dict(base), groups)
    bezier_out = bezier_animator.apply(dict(base), groups)

    # Brightness boost scales directly with weight (intensity=1 => v =
    # base_v*(1+weight)), so it's a clean proxy for "how close to the peak".
    _, _, v_linear = linear_out["c"].to_hsv()
    _, _, v_bezier = bezier_out["c"].to_hsv()
    assert v_bezier > v_linear


def test_falloff_curve_default_is_linear_and_backward_compatible():
    cfg = ChaseEffectConfig()
    assert cfg.falloff_curve == "linear"
    restored = ChaseEffectConfig.from_dict({})  # no falloff_curve key at all (old saved config)
    assert restored.falloff_curve == "linear"


# -- reverse direction ------------------------------------------------------------------------


def test_chase_reverse_flips_travel_direction():
    forward = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=1.0, reverse=False))
    backward = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=1.0, reverse=True))

    forward.tick(dt=0.1, num_positions=8)
    backward.tick(dt=0.1, num_positions=8)

    assert forward.position > 0.0
    # Both started at position 0 with the same speed/dt - reverse must land
    # exactly on the negated (mod n) position, not just "somewhere else".
    assert abs(backward.position - (8.0 - forward.position)) < 1e-9


def test_white_chase_reverse_flips_travel_direction():
    forward = WhiteChaseAnimator(WhiteChaseEffectConfig(speed_rotations_per_s=1.0, reverse=False))
    backward = WhiteChaseAnimator(WhiteChaseEffectConfig(speed_rotations_per_s=1.0, reverse=True))

    forward.tick(dt=0.1, num_positions=8)
    backward.tick(dt=0.1, num_positions=8)

    assert forward.position > 0.0
    assert abs(backward.position - (8.0 - forward.position)) < 1e-9


def test_reverse_default_is_false_and_backward_compatible():
    assert ChaseEffectConfig().reverse is False
    assert WhiteChaseEffectConfig().reverse is False
    assert ChaseEffectConfig.from_dict({}).reverse is False
    assert WhiteChaseEffectConfig.from_dict({}).reverse is False


# -- per-lamp chase dwell multiplier ("lingers longer at this spot") ------------------------


def test_chase_dwell_mult_defaults_to_one_and_is_backward_compatible():
    effect = PerLampEffect(device_id="a")
    assert effect.chase_dwell_mult == 1.0
    restored = PerLampEffect.from_dict({"device_id": "a"})  # no chase_dwell_mult key at all (old config)
    assert restored.chase_dwell_mult == 1.0


def test_get_chase_group_dwell_weights_averages_group_members():
    effects = {
        "a": PerLampEffect(device_id="a", chase_order=0, chase_dwell_mult=2.0),
        "b": PerLampEffect(device_id="b", chase_order=0, chase_dwell_mult=4.0),  # same position as "a"
        "c": PerLampEffect(device_id="c", chase_order=1, chase_dwell_mult=0.5),
        "d": PerLampEffect(device_id="d", chase_order=2),  # default 1.0
    }
    groups = get_chase_groups(effects, ["a", "b", "c", "d"])
    weights = get_chase_group_dwell_weights(effects, groups)
    assert weights == [3.0, 0.5, 1.0]  # group 0 = avg(2.0, 4.0)


def test_get_chase_group_dwell_weights_defaults_missing_lamps_to_one():
    groups = [["a"], ["b"]]
    weights = get_chase_group_dwell_weights({}, groups)  # neither lamp has a PerLampEffect
    assert weights == [1.0, 1.0]


def test_all_dwell_weights_one_reproduces_original_uniform_speed():
    """Passing uniform 1.0 weights (or omitting dwell_weights) must move the
    position identically - this is what keeps every lamp's chase behavior
    unchanged for anyone who never touches the new setting."""
    no_weights = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5))
    uniform_weights = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5))

    no_weights.tick(dt=0.2, num_positions=6)
    uniform_weights.tick(dt=0.2, num_positions=6, dwell_weights=[1.0] * 6)

    assert abs(no_weights.position - uniform_weights.position) < 1e-9


def test_higher_dwell_weight_slows_down_local_travel():
    """A dwell weight > 1.0 on the position the highlight currently sits at
    (nearest by rounding) must make it advance MORE SLOWLY than the
    uniform-speed baseline - this is the actual 'lingers longer' effect."""
    baseline = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    slowed = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    # Both start at position 0.0, which rounds to group index 0.
    weights = [4.0, 1.0, 1.0, 1.0, 1.0, 1.0]  # group 0 (where we start) dwells 4x longer

    baseline.tick(dt=0.05, num_positions=6)
    slowed.tick(dt=0.05, num_positions=6, dwell_weights=weights)

    assert 0.0 < slowed.position < baseline.position


def test_lower_dwell_weight_speeds_up_local_travel():
    baseline = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    sped_up = ChaseAnimator(ChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    weights = [0.25, 1.0, 1.0, 1.0, 1.0, 1.0]  # group 0 (where we start) is passed through 4x faster

    baseline.tick(dt=0.05, num_positions=6)
    sped_up.tick(dt=0.05, num_positions=6, dwell_weights=weights)

    assert sped_up.position > baseline.position


def test_white_chase_dwell_weight_slows_down_local_travel():
    baseline = WhiteChaseAnimator(WhiteChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    slowed = WhiteChaseAnimator(WhiteChaseEffectConfig(speed_rotations_per_s=0.5, reverse=False))
    weights = [4.0, 1.0, 1.0, 1.0, 1.0, 1.0]

    baseline.tick(dt=0.05, num_positions=6)
    slowed.tick(dt=0.05, num_positions=6, dwell_weights=weights)

    assert 0.0 < slowed.position < baseline.position


# -- sync_mode: "beat" / "intensity_peak" (event-driven speed) -----------------------------
#
# Regression coverage for the reported bug: an earlier version estimated a
# rolling tempo from recent beats and rotated CONTINUOUSLY at that estimate,
# which kept the highlight gliding on its own between hits (and even after
# the music went quiet, on the last estimate) - it looked like it was
# spinning with no audible rhythm behind it. These modes must now do nothing
# at all except jump exactly `beat_multiplier` steps on the tick a beat/peak
# is actually detected.


def test_sync_beat_mode_never_drifts_between_hits_and_steps_on_each_one():
    # speed_rotations_per_s is deliberately huge - if any fallback/continuous
    # path were still reachable in "beat" mode, it would show up immediately
    # as movement with no beat, since it dwarfs one legitimate per-beat step.
    cfg = ChaseEffectConfig(sync_mode="beat", beat_multiplier=1.0, speed_rotations_per_s=5.0)
    animator = ChaseAnimator(cfg)
    dt = 1.0 / 30.0
    t = 0.0

    # Quiet priming samples, all below min_energy (0.12 default) - must never trigger.
    for _ in range(6):
        animator.tick(dt, num_positions=8, beat_band_energy=0.05, now_s=t)
        t += dt
    assert animator.position == 0.0  # no beat yet -> must not have moved AT ALL

    animator.tick(dt, num_positions=8, beat_band_energy=0.9, now_s=t)  # one genuine beat
    t += dt
    assert animator.position == 1.0  # exactly one lamp-step, matching beat_multiplier

    for _ in range(10):  # back to quiet - must hold, not keep gliding
        animator.tick(dt, num_positions=8, beat_band_energy=0.05, now_s=t)
        t += dt
    assert animator.position == 1.0


def test_sync_intensity_peak_mode_steps_on_detected_peak_only():
    cfg = ChaseEffectConfig(sync_mode="intensity_peak", beat_multiplier=2.0, speed_rotations_per_s=5.0)
    animator = ChaseAnimator(cfg)
    dt = 1.0 / 30.0
    t = 0.0

    for _ in range(6):  # below peak_min_energy (0.08 default) - must never trigger
        animator.tick(dt, num_positions=8, intensity_energy=0.03, now_s=t)
        t += dt
    assert animator.position == 0.0

    animator.tick(dt, num_positions=8, intensity_energy=0.9, now_s=t)  # one genuine peak
    t += dt
    assert animator.position == 2.0  # matches beat_multiplier

    for _ in range(10):
        animator.tick(dt, num_positions=8, intensity_energy=0.03, now_s=t)
        t += dt
    assert animator.position == 2.0


def test_sync_beat_mode_ignores_intensity_energy_and_vice_versa():
    """The two sync modes must each react only to their own signal - beat
    mode ignores intensity_energy, and intensity_peak mode ignores
    beat_band_energy - confirming they're wired to independent detectors."""
    beat_mode = ChaseAnimator(ChaseEffectConfig(sync_mode="beat", beat_multiplier=1.0))
    peak_mode = ChaseAnimator(ChaseEffectConfig(sync_mode="intensity_peak", beat_multiplier=1.0))

    beat_mode.tick(dt=1.0, num_positions=8, intensity_energy=0.9, now_s=0.0)
    peak_mode.tick(dt=1.0, num_positions=8, beat_band_energy=0.9, now_s=0.0)

    assert beat_mode.position == 0.0
    assert peak_mode.position == 0.0


def test_sync_mode_falls_back_to_constant_speed_when_no_time_context_given():
    """The no-audio manual app never passes now_s at all (nothing to sync
    to) - "beat"/"intensity_peak" must fall back to the same constant speed
    as "off" there, rather than freezing forever."""
    off_animator = ChaseAnimator(ChaseEffectConfig(sync_mode="off", speed_rotations_per_s=1.0))
    beat_animator = ChaseAnimator(ChaseEffectConfig(sync_mode="beat", speed_rotations_per_s=1.0))
    peak_animator = ChaseAnimator(ChaseEffectConfig(sync_mode="intensity_peak", speed_rotations_per_s=1.0))

    off_animator.tick(dt=0.2, num_positions=8)
    beat_animator.tick(dt=0.2, num_positions=8)  # no now_s/beat_band_energy at all
    peak_animator.tick(dt=0.2, num_positions=8)

    assert off_animator.position > 0.0
    assert beat_animator.position == off_animator.position
    assert peak_animator.position == off_animator.position


def test_chase_sync_mode_backward_compatible_with_old_boolean_field():
    assert ChaseEffectConfig().sync_mode == "off"
    assert ChaseEffectConfig.from_dict({}).sync_mode == "off"
    assert ChaseEffectConfig.from_dict({"sync_to_beat": True}).sync_mode == "beat"
    assert ChaseEffectConfig.from_dict({"sync_to_beat": False}).sync_mode == "off"
    assert ChaseEffectConfig.from_dict({"sync_mode": "intensity_peak"}).sync_mode == "intensity_peak"


# -- Group Switch: discrete alternative to Chase's continuous rotation ----------------------


def test_get_group_switch_groups_orders_ascending_and_excludes_unset():
    effects = _group_switch_effects({"a": 2, "b": 0, "c": None, "d": 1})
    groups = get_group_switch_groups(effects, ["a", "b", "c", "d"])
    assert groups == [["b"], ["d"], ["a"]]


def test_group_switch_groups_independent_from_chase_order():
    """A lamp's chase_order and effect_group are separate knobs - setting
    one must never leak into the other's grouping."""
    effects = {
        "a": PerLampEffect(device_id="a", chase_order=0, effect_group=None),
        "b": PerLampEffect(device_id="b", chase_order=None, effect_group=0),
        "c": PerLampEffect(device_id="c", chase_order=1, effect_group=1),
    }
    chase_groups = get_chase_groups(effects, ["a", "b", "c"])
    switch_groups = get_group_switch_groups(effects, ["a", "b", "c"])
    assert chase_groups == [["a"], ["c"]]  # "b" excluded - no chase_order
    assert switch_groups == [["b"], ["c"]]  # "a" excluded - no effect_group


def test_group_switch_applies_full_color_with_no_blend_on_other_groups():
    """The defining difference from Chase: the active group gets the exact
    target color (no interpolation toward it), and every other group is
    left completely untouched - a hard step, not a gradient."""
    cfg = GroupSwitchEffectConfig(enabled=True, color_mode="custom", custom_hue_deg=200.0, custom_saturation=0.9, intensity=0.0)
    animator = GroupSwitchAnimator(cfg)
    groups = [["a"], ["b"], ["c"]]
    base_colors = {"a": Color(0.1, 0.1, 0.1), "b": Color(0.1, 0.1, 0.1), "c": Color(0.1, 0.1, 0.1)}

    animator.position = 0.0  # active group = index 0 ("a")
    out = animator.apply(dict(base_colors), groups)

    h_a, s_a, _ = out["a"].to_hsv()
    assert abs(h_a - 200.0) < 1e-6  # exact target hue, not partially blended
    assert abs(s_a - 0.9) < 1e-6
    assert out["b"] == base_colors["b"]  # untouched, no partial bleed
    assert out["c"] == base_colors["c"]


def test_group_switch_hue_shift_mode_varies_hue_by_active_group():
    cfg = GroupSwitchEffectConfig(enabled=True, color_mode="hue_shift", custom_hue_deg=0.0, hue_shift_step_deg=90.0, intensity=0.0)
    animator = GroupSwitchAnimator(cfg)
    groups = [["a"], ["b"], ["c"], ["d"]]
    base_colors = {g[0]: Color(0.5, 0.5, 0.5) for g in groups}

    expected_hues = [0.0, 90.0, 180.0, 270.0]
    for i, expected_hue in enumerate(expected_hues):
        animator.position = float(i)
        out = animator.apply(dict(base_colors), groups)
        h = out[groups[i][0]].to_hsv()[0]
        assert abs(((h - expected_hue + 180.0) % 360.0) - 180.0) < 1e-6


def test_group_switch_too_few_groups_returns_colors_unchanged():
    animator = GroupSwitchAnimator(GroupSwitchEffectConfig(enabled=True))
    colors = {"a": Color(0.5, 0.5, 0.5)}
    out = animator.apply(colors, [["a"]])
    assert out is colors


def test_group_switch_off_mode_advances_continuously():
    animator = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="off", speed_rotations_per_s=1.0))
    animator.tick(dt=0.5, num_positions=4)  # half a "lap" through 4 groups = 2 groups advanced
    assert abs(animator.position - 2.0) < 1e-9


def test_group_switch_reverse_flips_direction():
    forward = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="off", speed_rotations_per_s=1.0, reverse=False))
    backward = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="off", speed_rotations_per_s=1.0, reverse=True))
    forward.tick(dt=0.1, num_positions=8)
    backward.tick(dt=0.1, num_positions=8)
    assert forward.position > 0.0
    assert abs(backward.position - (8.0 - forward.position)) < 1e-9


def test_group_switch_beat_mode_never_drifts_between_hits_and_steps_on_each_one():
    cfg = GroupSwitchEffectConfig(sync_mode="beat", beat_multiplier=1.0, speed_rotations_per_s=5.0)
    animator = GroupSwitchAnimator(cfg)
    dt = 1.0 / 30.0
    t = 0.0

    for _ in range(6):  # quiet priming, below min_energy (0.12 default) - must never trigger
        animator.tick(dt, num_positions=8, beat_band_energy=0.05, now_s=t)
        t += dt
    assert animator.position == 0.0

    animator.tick(dt, num_positions=8, beat_band_energy=0.9, now_s=t)  # one genuine beat
    t += dt
    assert animator.position == 1.0

    for _ in range(10):  # back to quiet - must hold, not keep advancing
        animator.tick(dt, num_positions=8, beat_band_energy=0.05, now_s=t)
        t += dt
    assert animator.position == 1.0


def test_group_switch_intensity_peak_mode_steps_on_detected_peak_only():
    cfg = GroupSwitchEffectConfig(sync_mode="intensity_peak", beat_multiplier=2.0, speed_rotations_per_s=5.0)
    animator = GroupSwitchAnimator(cfg)
    dt = 1.0 / 30.0
    t = 0.0

    for _ in range(6):  # below peak_min_energy (0.08 default) - must never trigger
        animator.tick(dt, num_positions=8, intensity_energy=0.03, now_s=t)
        t += dt
    assert animator.position == 0.0

    animator.tick(dt, num_positions=8, intensity_energy=0.9, now_s=t)  # one genuine peak
    t += dt
    assert animator.position == 2.0

    for _ in range(10):
        animator.tick(dt, num_positions=8, intensity_energy=0.03, now_s=t)
        t += dt
    assert animator.position == 2.0


def test_group_switch_falls_back_to_constant_speed_when_no_time_context_given():
    off_animator = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="off", speed_rotations_per_s=1.0))
    beat_animator = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="beat", speed_rotations_per_s=1.0))
    peak_animator = GroupSwitchAnimator(GroupSwitchEffectConfig(sync_mode="intensity_peak", speed_rotations_per_s=1.0))

    off_animator.tick(dt=0.2, num_positions=8)
    beat_animator.tick(dt=0.2, num_positions=8)  # no now_s/beat_band_energy at all
    peak_animator.tick(dt=0.2, num_positions=8)

    assert off_animator.position > 0.0
    assert beat_animator.position == off_animator.position
    assert peak_animator.position == off_animator.position


def test_group_switch_config_defaults_and_backward_compatible():
    cfg = GroupSwitchEffectConfig()
    assert cfg.enabled is False
    assert cfg.sync_mode == "off"
    restored = GroupSwitchEffectConfig.from_dict({})
    assert restored == cfg
    assert GroupSwitchEffectConfig.from_dict({"sync_mode": "intensity_peak"}).sync_mode == "intensity_peak"
    assert GroupSwitchEffectConfig.from_dict({"sync_mode": "bogus"}).sync_mode == "off"


def test_per_lamp_effect_group_defaults_to_none_and_is_backward_compatible():
    effect = PerLampEffect(device_id="a")
    assert effect.effect_group is None
    restored = PerLampEffect.from_dict({"device_id": "a"})  # no effect_group key at all (old config)
    assert restored.effect_group is None


def test_spread_positions_evenly_spaced():
    from airam_lights.effects.chase import spread_positions

    positions = [["p0"], ["p1"], ["p2"], ["p3"], ["p4"], ["p5"]]
    assert spread_positions(positions, 1, 1) == {"p1"}
    assert spread_positions(positions, 1, 2) == {"p1", "p4"}
    assert spread_positions(positions, 5, 3) == {"p5", "p1", "p3"}
    assert spread_positions(positions[:2], 0, 3) == {"p0", "p1"}  # never more rotators than positions
    assert spread_positions([], 0, 2) == set()
