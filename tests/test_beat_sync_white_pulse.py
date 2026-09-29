"""Verifies Beat Sync mode's "true white" pulse: at the peak of a white
pulse the affected lamp should receive an actual WhiteTarget (physical
WHITE work_mode, full brightness, coolest temp) instead of an RGB Color,
and should fall back to normal RGB colour sends once the pulse ends -
exercising VisualizationEngine.tick_visual() end to end with a fake clock
and a fake lamp manager, no real audio hardware or bulbs involved.
"""
import airam_lights.engine.visualization_engine as ve_module
from airam_lights.audio.capture import AudioCapture
from airam_lights.color.models import WhiteTarget
from airam_lights.config.schema import AppConfig, PerLampEffect
from airam_lights.engine.visualization_engine import VisualizationEngine


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, dt: float) -> None:
        self.now += dt

    def perf_counter(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now


class _FakeLampManager:
    def __init__(self, device_ids):
        self._ids = list(device_ids)
        self.color_calls = []
        self.white_calls = []
        self.call_log = []  # ordered ("color" | "white", {device_id: value}) log, across both call types

    def selected_device_ids(self):
        return list(self._ids)

    def push_colors(self, colors):
        self.color_calls.append(dict(colors))
        self.call_log.append(("color", dict(colors)))

    def push_white_targets(self, targets):
        self.white_calls.append(dict(targets))
        self.call_log.append(("white", dict(targets)))


def _make_engine(monkeypatch, *, energy_sequence, device_ids=("dev1",), cool_ratio=1.0):
    """energy_sequence: values returned by band_energy() on successive
    calls (one per tick_visual() call) - lets us deterministically force
    a beat on tick 0 and silence afterward, without a real FFT frame."""
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)

    energies = iter(energy_sequence)
    monkeypatch.setattr(ve_module, "band_energy", lambda frame, lo, hi: next(energies, 0.0))

    # VisualizationEngine never reads config.devices - lamp identity/selection
    # comes entirely from the (fake) LampManager below - so it's left empty.
    config = AppConfig()
    config.color_mapping.mode = "beat_sync"
    bs = config.color_mapping.beat_sync
    bs.sensitivity = 1.05
    bs.min_interval_ms = 0.0
    bs.min_energy = 0.0
    bs.white_pulse_enabled = True
    bs.white_pulse_probability = 1.0
    bs.white_pulse_duration_ms = 80.0
    bs.white_pulse_attack_ms = 5.0
    bs.white_pulse_release_ms = 40.0
    bs.white_pulse_white_brightness = 1.0
    bs.white_pulse_cool_ratio = cool_ratio
    # Dark pulse would otherwise compete for the same beat (independent
    # random roll) - disable it so the test is deterministic.
    bs.dark_pulse_enabled = False

    lamp_manager = _FakeLampManager(list(device_ids))
    engine = VisualizationEngine(config, AudioCapture(), lamp_manager)
    engine.running = True
    engine._raw_frame = object()  # never read by band_energy, which is mocked
    return engine, clock, lamp_manager


def test_white_pulse_peak_sends_true_white_target(monkeypatch):
    # BeatDetector needs >=4 history samples before it can fire at all (see
    # dsp/beat_detector.py), so prime it with a few quiet samples first, then
    # a strong spike well above that rolling average - which, with
    # probability=1.0, always selects the white pulse. Ticks after that:
    # silence, letting the pulse play out toward its peak.
    engine, clock, lamp_manager = _make_engine(
        monkeypatch, energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 10
    )

    dt = 1.0 / 60.0
    for _ in range(15):
        engine.tick_visual()
        clock.advance(dt)

    assert lamp_manager.white_calls, "expected at least one push_white_targets() call during the pulse"
    peak_targets = max(
        (t for call in lamp_manager.white_calls for t in call.values()),
        key=lambda t: t.brightness,
    )
    assert isinstance(peak_targets, WhiteTarget)
    assert peak_targets.brightness > 0.9  # attack_ms=5ms is fast enough to reach ~full brightness in a few ticks
    assert peak_targets.temp == 1.0  # coolest white, as configured


def test_white_pulse_ends_and_resumes_rgb(monkeypatch):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch, energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 60
    )

    dt = 1.0 / 60.0
    for _ in range(60):  # well past duration_ms(80) + release_ms(40) at 60fps
        engine.tick_visual()
        clock.advance(dt)

    # The pulse must actually have happened (some white call with dev1)...
    assert any("dev1" in targets for _kind, targets in lamp_manager.call_log if _kind == "white")
    # ...but by the end, the lamp must be back on ordinary RGB colour sends,
    # not still parked in WHITE work_mode: the LAST command issued for it,
    # in call order, has to be a "color" one.
    last_kind_for_dev1 = next(kind for kind, payload in reversed(lamp_manager.call_log) if "dev1" in payload)
    assert last_kind_for_dev1 == "color"
    assert "dev1" not in engine.latest_lamp_white_targets


def test_rapid_repeated_beats_do_not_extend_pulse_forever(monkeypatch):
    """Regression test: with white_pulse_probability=1.0 and a fast/dense
    track (many beats in quick succession), an earlier version re-rolled and
    re-extended the pulse's end time on EVERY qualifying beat, so a lamp
    could get stuck in physical WHITE work_mode for the entire song instead
    of a brief accent. A pulse must now run to completion (bounded by its
    own duration + release) and let RGB resume even while beats keep
    arriving - a new pulse can start again afterward, but the current one
    can't be extended mid-flight."""
    # 4 quiet priming samples, then a spike every OTHER tick for 2 full
    # seconds (120 ticks @60fps) - far longer than duration_ms(80) +
    # release_ms(40), so if the chaining bug were still present the lamp
    # would still be on a white send at the end of this loop.
    spikes = [0.05, 0.05, 0.05, 0.05] + [1.0, 0.0] * 60
    engine, clock, lamp_manager = _make_engine(monkeypatch, energy_sequence=spikes)

    dt = 1.0 / 60.0
    for _ in range(120):
        engine.tick_visual()
        clock.advance(dt)

    assert any("dev1" in targets for _kind, targets in lamp_manager.call_log if _kind == "white")
    # Some push_colors call for dev1 must have happened AFTER the first
    # white call for dev1 - i.e. it actually returned to RGB at least once
    # during the barrage, rather than staying pinned to white the whole time.
    first_white_index = next(
        i for i, (kind, payload) in enumerate(lamp_manager.call_log) if kind == "white" and "dev1" in payload
    )
    assert any(
        kind == "color" and "dev1" in payload
        for kind, payload in lamp_manager.call_log[first_white_index + 1:]
    )


def test_true_white_flash_is_synchronized_across_phase_offsets(monkeypatch):
    """Regression test: the true-white flash used to be sampled through each
    lamp's own per-lamp phase offset, exactly like hue/brightness/saturation
    are. Since the flash window is short, a lamp with a large enough offset
    could sample a point in time just before/after the peak and miss the
    white call entirely on a given beat - from the user's perspective, the
    flash appeared to land on arbitrary/random lamps instead of hitting the
    whole installation together. It must now be synchronized: every selected
    lamp gets (or doesn't get) the white call on the same tick, regardless of
    its own phase offset."""
    device_ids = ["dev1", "dev2", "dev3"]
    engine, clock, lamp_manager = _make_engine(
        monkeypatch,
        energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 10,
        device_ids=device_ids,
    )
    # Deliberately staggered, including offsets large relative to the short
    # pulse (duration_ms=80 + attack_ms=5) - dev3's 300ms offset alone would
    # have been enough to sample well outside the old pulse window entirely.
    engine.config.per_lamp_effects["dev1"] = PerLampEffect(device_id="dev1", phase_offset_ms=0.0)
    engine.config.per_lamp_effects["dev2"] = PerLampEffect(device_id="dev2", phase_offset_ms=120.0)
    engine.config.per_lamp_effects["dev3"] = PerLampEffect(device_id="dev3", phase_offset_ms=300.0)

    dt = 1.0 / 60.0
    for _ in range(15):
        engine.tick_visual()
        clock.advance(dt)

    white_call_device_sets = [frozenset(targets.keys()) for kind, targets in lamp_manager.call_log if kind == "white"]
    assert white_call_device_sets, "expected at least one push_white_targets() call"
    # Every white call must cover ALL three lamps together, never a subset -
    # that's what "synchronized" means here.
    for devices in white_call_device_sets:
        assert devices == frozenset(device_ids), devices


def test_white_pulse_cool_ratio_zero_is_always_warm(monkeypatch):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch,
        energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 10,
        cool_ratio=0.0,
    )

    dt = 1.0 / 60.0
    for _ in range(15):
        engine.tick_visual()
        clock.advance(dt)

    temps = {t.temp for _kind, targets in lamp_manager.call_log if _kind == "white" for t in targets.values()}
    assert temps, "expected at least one white call"
    assert temps == {0.0}, f"cool_ratio=0.0 must always land on warm (0.0), got {temps}"


def test_white_pulse_cool_ratio_one_is_always_cool(monkeypatch):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch,
        energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 10,
        cool_ratio=1.0,
    )

    dt = 1.0 / 60.0
    for _ in range(15):
        engine.tick_visual()
        clock.advance(dt)

    temps = {t.temp for _kind, targets in lamp_manager.call_log if _kind == "white" for t in targets.values()}
    assert temps, "expected at least one white call"
    assert temps == {1.0}, f"cool_ratio=1.0 must always land on cool (1.0), got {temps}"


def test_dense_beats_still_force_a_periodic_rgb_only_window(monkeypatch):
    """Regression test: the per-pulse cooldown gate only guarantees any ONE
    pulse can't be extended/re-triggered early - it does not bound how long
    a CHAIN of separate short pulses can keep the global true-white state
    continuously active on a dense/fast track. In practice a lamp whose own
    worker thread was rate-limited/backed off could then consistently miss
    the brief gaps between chained pulses and never get a real turn to send
    the reverted RGB color - indistinguishable from being stuck in white.
    An absolute ceiling (_WHITE_MAX_CONTINUOUS_S) must force a real,
    minimum-length RGB-only window (_WHITE_FORCED_GAP_S) periodically,
    regardless of how densely beats keep arriving."""
    # 4 quiet priming samples, then a spike on every other tick, continuously,
    # for 6 simulated seconds - dense enough (with duration_ms=80/
    # release_ms=40/probability=1.0, all from _make_engine's defaults) that
    # pulses chain back to back almost the whole time.
    spikes = [0.05, 0.05, 0.05, 0.05] + [1.0, 0.0] * 180
    engine, clock, lamp_manager = _make_engine(monkeypatch, energy_sequence=spikes)

    dt = 1.0 / 60.0
    white_active_timeline = []  # (wall_time, was_white_active) per tick
    for _ in range(360):  # 6 simulated seconds @ 60fps
        engine.tick_visual()
        white_active_timeline.append((clock.now, bool(engine.latest_lamp_white_targets)))
        clock.advance(dt)

    # Find the longest continuous stretch where white was NOT active.
    longest_rgb_gap_s = 0.0
    gap_start = None
    for t, active in white_active_timeline:
        if active:
            gap_start = None
        else:
            if gap_start is None:
                gap_start = t
            longest_rgb_gap_s = max(longest_rgb_gap_s, t - gap_start)

    # -dt*2 for sampling quantization (the gap is measured between sampled
    # tick timestamps, not the true underlying window) and float drift from
    # repeatedly summing dt - not a tolerance on the production behavior.
    assert longest_rgb_gap_s >= ve_module._WHITE_FORCED_GAP_S - dt * 2, (
        f"expected a forced RGB-only window of at least ~{ve_module._WHITE_FORCED_GAP_S}s, "
        f"longest observed was {longest_rgb_gap_s:.3f}s"
    )


def test_white_pulse_cool_ratio_stays_constant_within_one_flash(monkeypatch):
    """The warm/cool roll happens once per NEW flash, not per tick - a
    single flash must never flicker between warm and cool mid-duration."""
    engine, clock, lamp_manager = _make_engine(
        monkeypatch,
        energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 10,
        cool_ratio=0.5,
    )

    dt = 1.0 / 60.0
    for _ in range(15):
        engine.tick_visual()
        clock.advance(dt)

    temps_seen = [t.temp for _kind, targets in lamp_manager.call_log if _kind == "white" for t in targets.values()]
    assert temps_seen, "expected at least one white call"
    assert len(set(temps_seen)) == 1, f"a single flash must not change temp mid-flight, saw {temps_seen}"


def _white_device_sets(lamp_manager):
    return [frozenset(targets.keys()) for kind, targets in lamp_manager.call_log if kind == "white"]


def _run_one_flash(engine, clock, ticks=15):
    dt = 1.0 / 60.0
    for _ in range(ticks):
        engine.tick_visual()
        clock.advance(dt)


def _make_targeted_engine(monkeypatch, target):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch,
        energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 20,
        device_ids=["dev1", "dev2", "dev3", "dev4"],
    )
    engine.config.color_mapping.beat_sync.white_pulse_target = target
    # Chase ring: dev1/dev2 are position 0, dev3 position 1, dev4 position 2.
    # Group switch: dev1/dev3 group 0, dev2/dev4 group 1.
    for device_id, order, group in (("dev1", 0, 0), ("dev2", 0, 1), ("dev3", 1, 0), ("dev4", 2, 1)):
        engine.config.per_lamp_effects[device_id] = PerLampEffect(
            device_id=device_id, chase_order=order, effect_group=group
        )
    for effect_cfg in (engine.config.chase, engine.config.group_switch):
        effect_cfg.sync_mode = "off"
        effect_cfg.speed_rotations_per_s = 0.0  # parked on position/group 0
    return engine, clock, lamp_manager


def test_white_pulse_target_chase_only_hits_chase_highlight(monkeypatch):
    engine, clock, lamp_manager = _make_targeted_engine(monkeypatch, "chase")
    engine.config.chase.enabled = True
    _run_one_flash(engine, clock)

    sets = _white_device_sets(lamp_manager)
    assert sets
    assert all(devices == frozenset({"dev1", "dev2"}) for devices in sets), sets


def test_white_pulse_target_group_only_hits_active_group(monkeypatch):
    engine, clock, lamp_manager = _make_targeted_engine(monkeypatch, "group")
    engine.config.group_switch.enabled = True
    _run_one_flash(engine, clock)

    sets = _white_device_sets(lamp_manager)
    assert sets
    assert all(devices == frozenset({"dev1", "dev3"}) for devices in sets), sets


def test_white_pulse_target_falls_back_to_all_when_effect_disabled(monkeypatch):
    engine, clock, lamp_manager = _make_targeted_engine(monkeypatch, "chase")
    engine.config.chase.enabled = False
    _run_one_flash(engine, clock)

    sets = _white_device_sets(lamp_manager)
    assert sets
    assert all(devices == frozenset({"dev1", "dev2", "dev3", "dev4"}) for devices in sets), sets


def test_white_pulse_target_lamps_are_held_for_the_whole_flash(monkeypatch):
    """The chase stepping on mid-flash must not move the white flash along
    with it - the lamps are picked on the flash's first tick and held."""
    engine, clock, lamp_manager = _make_targeted_engine(monkeypatch, "chase")
    engine.config.chase.enabled = True
    engine.config.chase.speed_rotations_per_s = 20.0  # several steps within one flash
    _run_one_flash(engine, clock)

    sets = _white_device_sets(lamp_manager)
    assert sets
    assert len(set(sets)) == 1, sets


def test_white_pulse_brightness_mult_scales_per_lamp(monkeypatch):
    engine, clock, lamp_manager = _make_targeted_engine(monkeypatch, "all")
    engine.config.color_mapping.beat_sync.white_pulse_white_brightness = 0.8
    engine.config.per_lamp_effects["dev3"].white_pulse_brightness_mult = 0.5
    engine.config.per_lamp_effects["dev4"].white_pulse_brightness_mult = 0.5
    _run_one_flash(engine, clock)

    white_calls = [targets for kind, targets in lamp_manager.call_log if kind == "white"]
    assert white_calls
    for targets in white_calls:
        # The flash's brightness follows its attack/release envelope (instant
        # "direct" transitions), so compare lamps within the same step.
        assert targets["dev1"].brightness <= 0.8 + 1e-9
        assert targets["dev2"].brightness == targets["dev1"].brightness
        assert abs(targets["dev3"].brightness - 0.5 * targets["dev1"].brightness) < 1e-9
        assert abs(targets["dev4"].brightness - 0.5 * targets["dev1"].brightness) < 1e-9
        assert targets["dev3"].temp == targets["dev1"].temp
    assert max(t["dev1"].brightness for t in white_calls) > 0.75  # reaches (nearly) full strength


def test_white_pulse_never_uses_the_rgb_leds(monkeypatch):
    """White comes from the white LEDs only: even a config saved by an older
    build with the RGB-desaturation variant (true white off / invert on)
    flashes the real white LEDs, and the RGB color stays fully saturated."""
    engine, clock, lamp_manager = _make_engine(
        monkeypatch, energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 15
    )
    from airam_lights.config.schema import BeatSyncModeConfig

    legacy = BeatSyncModeConfig.from_dict(
        {**engine.config.color_mapping.beat_sync.to_dict(), "white_pulse_true_white": False, "white_pulse_invert": True, "white_pulse_depth": 1.0}
    )
    engine.config.color_mapping.beat_sync = legacy
    saturations = []
    for _ in range(15):
        engine.tick_visual()
        saturations.append(engine.latest_band3_levels["saturation"])
        clock.advance(1.0 / 60.0)
    assert lamp_manager.white_calls, "the pulse must go to the white LEDs"
    assert all(s == legacy.saturation for s in saturations)


def _flash_brightness_curve(monkeypatch, transitions):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch, energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 40
    )
    engine.config.network.lamp_transitions = transitions
    bs = engine.config.color_mapping.beat_sync
    bs.white_pulse_attack_ms = 40.0
    bs.white_pulse_duration_ms = 60.0
    bs.white_pulse_release_ms = 80.0
    engine._smoother_beat_white.attack_ms = bs.white_pulse_attack_ms
    engine._smoother_beat_white.release_ms = bs.white_pulse_release_ms
    curve = []
    for _ in range(40):
        engine.tick_visual()
        target = engine.latest_lamp_white_targets.get("dev1")
        curve.append(target.brightness if target else 0.0)
        clock.advance(1.0 / 60.0)
    return curve


def test_white_flash_ramps_with_attack_and_release_on_instant_transitions(monkeypatch):
    curve = [b for b in _flash_brightness_curve(monkeypatch, "direct") if b > 0.0]
    peak = curve.index(max(curve))
    assert peak > 0 and curve[0] < 0.8 * max(curve)  # rises over the attack...
    assert curve[-1] < 0.5 * max(curve)  # ...and fades out over the release


def test_white_flash_stays_constant_with_legacy_transitions(monkeypatch):
    curve = [b for b in _flash_brightness_curve(monkeypatch, "legacy") if b > 0.0]
    assert curve and len(set(curve)) == 1  # one target: the bulb's own fade softens it


def _flash_frames(monkeypatch, transitions):
    engine, clock, lamp_manager = _make_engine(
        monkeypatch, energy_sequence=[0.05, 0.05, 0.05, 0.05, 1.0] + [0.0] * 40
    )
    engine.config.network.lamp_transitions = transitions
    bs = engine.config.color_mapping.beat_sync
    bs.white_pulse_attack_ms = 40.0
    bs.white_pulse_duration_ms = 60.0
    bs.white_pulse_release_ms = 80.0
    engine._smoother_beat_white.attack_ms = bs.white_pulse_attack_ms
    engine._smoother_beat_white.release_ms = bs.white_pulse_release_ms
    frames = []
    for _ in range(40):
        engine.tick_visual()
        target = engine.latest_lamp_white_targets.get("dev1")
        if target is not None:
            frames.append(target)
        clock.advance(1.0 / 60.0)
    return frames


def test_white_flash_crossfades_with_the_colour_on_instant_transitions(monkeypatch):
    frames = _flash_frames(monkeypatch, "direct")
    assert frames and all(f.under is not None for f in frames)
    def level(c):
        return max(c.r, c.g, c.b)
    peak = max(range(len(frames)), key=lambda i: frames[i].brightness)
    # Colour fades out as the white swells, and comes back as it fades: the
    # lamp never has a moment with neither (no dark gap around the flash).
    assert level(frames[0].under) > level(frames[peak].under)
    assert level(frames[-1].under) > level(frames[peak].under)
    for f in frames:
        assert f.brightness / 0.8 + level(f.under) / max(level(frames[0].under), 1e-9) > 0.5


def test_legacy_white_flash_has_no_colour_underneath(monkeypatch):
    frames = _flash_frames(monkeypatch, "legacy")
    assert frames and all(f.under is None for f in frames)
