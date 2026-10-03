"""Shared beat clock wired through VisualizationEngine: Beat Sync hue
divisions, pulse triggers, and Chase / Group Switch "clock" sync all
following the same beats (fake clock + synthetic kick envelope, no audio)."""
import airam_lights.engine.visualization_engine as ve_module
from airam_lights.audio.capture import AudioCapture
from airam_lights.config.schema import AppConfig, PerLampEffect
from airam_lights.engine.visualization_engine import VisualizationEngine

PERIOD = 0.5  # 120 BPM
TICK = 1.0 / 30.0
DEVICES = ["a", "b", "c", "d"]


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def perf_counter(self):
        return self.now

    def time(self):
        return self.now


class _FakeLampManager:
    def __init__(self):
        self.white_calls = []

    def selected_device_ids(self):
        return list(DEVICES)

    def push_colors(self, colors):
        pass

    def push_white_targets(self, targets):
        self.white_calls.append(dict(targets))


def _make_engine(monkeypatch, configure):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)

    def kick_energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", kick_energy)

    config = AppConfig()
    config.color_mapping.mode = "beat_sync"
    config.rhythm.shared_clock = True
    config.rhythm.lead_ms = 0.0
    bs = config.color_mapping.beat_sync
    bs.dark_pulse_enabled = False
    bs.white_pulse_enabled = False
    config.chase.enabled = False
    config.group_switch.enabled = False
    for i, dev in enumerate(DEVICES):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)
    configure(config)

    lamps = _FakeLampManager()
    engine = VisualizationEngine(config, AudioCapture(), lamps)
    engine.running = True
    engine._raw_frame = object()
    return engine, clock, lamps


def _run(engine, clock, seconds, on_tick):
    for _ in range(int(seconds / TICK)):
        engine.tick_visual()
        on_tick()
        clock.now += TICK


def test_hue_changes_once_per_bar(monkeypatch):
    def configure(c):
        c.color_mapping.beat_sync.hue_every_n_beats = 4

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)  # let the clock lock first
    beats, hue_changes = 0, 0
    last_hue = engine._beat_target_hue

    def on_tick():
        nonlocal beats, hue_changes, last_hue
        beats += engine.latest_clock_beat.is_beat
        if engine._beat_target_hue != last_hue:
            hue_changes += 1
            last_hue = engine._beat_target_hue

    _run(engine, clock, 12.0, on_tick)
    assert beats >= 20
    assert abs(hue_changes - beats / 4) <= 1


def test_chase_and_group_switch_follow_clock_divisions(monkeypatch):
    def configure(c):
        c.chase.enabled = True
        c.chase.sync_mode = "clock"
        c.chase.clock_every_n_beats = 2
        c.chase.beat_multiplier = 1.0
        c.group_switch.enabled = True
        c.group_switch.sync_mode = "clock"
        c.group_switch.clock_every_n_beats = 4

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    beats, chase_steps, group_switches = 0, 0, 0
    last_chase = engine._chase_animator.position
    last_group = engine._group_switch_animator.position

    def on_tick():
        nonlocal beats, chase_steps, group_switches, last_chase, last_group
        beat = engine.latest_clock_beat
        beats += beat.is_beat
        if engine._chase_animator.position != last_chase:
            chase_steps += 1
            assert beat.is_beat and beat.bar_position % 2 == 0
            last_chase = engine._chase_animator.position
        if engine._group_switch_animator.position != last_group:
            group_switches += 1
            assert beat.is_beat and beat.is_downbeat
            last_group = engine._group_switch_animator.position

    _run(engine, clock, 12.0, on_tick)
    assert abs(chase_steps - beats / 2) <= 1
    assert abs(group_switches - beats / 4) <= 1


def test_white_pulse_downbeat_trigger(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_probability = 1.0
        bs.white_pulse_trigger = "downbeat"
        bs.white_pulse_duration_ms = 40.0
        bs.white_pulse_attack_ms = 5.0
        bs.white_pulse_release_ms = 20.0

    engine, clock, lamps = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    lamps.white_calls.clear()
    starts, beats, was_white = 0, 0, False

    def on_tick():
        nonlocal starts, beats, was_white
        beats += engine.latest_clock_beat.is_beat
        is_white = bool(engine.latest_lamp_white_targets)
        if is_white and not was_white:
            starts += 1
        was_white = is_white

    _run(engine, clock, 12.0, on_tick)
    assert starts >= 3
    assert abs(starts - beats / 4) <= 1


def test_clock_idle_when_nothing_follows_it(monkeypatch):
    def configure(c):
        c.rhythm.shared_clock = False

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 3.0, lambda: None)
    assert not engine.clock_active
    assert engine.latest_clock_beat.beat_count == 0


def test_sequencer_walks_true_white_between_groups(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_duration_ms = 40.0
        bs.white_pulse_release_ms = 30.0
        c.sequencer.enabled = True
        c.sequencer.white_pattern = "offbeats"
        c.sequencer.white_density = 1.0
        c.sequencer.white_accent_focus = 0.0  # every pattern step
        c.sequencer.white_build = 0.0
        c.sequencer.double_chance = 0.0
        c.sequencer.dark_pattern = "off"
        c.sequencer.phrase_accent = False
        c.sequencer.fills = False
        for i, dev in enumerate(DEVICES):
            c.per_lamp_effects[dev].effect_group = i % 2  # two groups: a+c, b+d

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    starts = []  # (time, frozenset of lamps that just turned white)
    previous = set()

    def on_tick():
        nonlocal previous
        current = set(engine.latest_lamp_white_targets)
        new = current - previous
        if new:
            starts.append((clock.now, frozenset(new)))
        previous = current

    _run(engine, clock, 8.0, on_tick)
    assert len(starts) >= 10  # roughly one per beat, on the "and"
    groups = [s for _, s in starts]
    assert all(g in ({"a", "c"}, {"b", "d"}) for g in groups)
    assert all(a != b for a, b in zip(groups, groups[1:]))  # alternates between the groups
    # Off-beats: each flash starts about half a beat after a beat (lead is 0 here).
    for t, _ in starts:
        phase = ((t - 1000.0) / PERIOD) % 1.0
        assert 0.35 < phase < 0.75, phase


def _glide_config(fade, timing="beat", deg=120.0):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.fade_brightness = fade
        bs.glide_hue = True
        bs.hue_glide_deg = deg
        bs.hue_glide_timing = timing
        bs.hue_mode = "step"
        bs.hue_step_deg = 120.0
        bs.hue_every_n_beats = 1
        bs.flash_brightness = 0.9
        bs.sustain_brightness = 0.1
        bs.hue_attack_ms = 5.0
        bs.brightness_attack_ms = 5.0
        bs.brightness_release_ms = 60.0

    return configure


def _collect(engine, clock, seconds=4.0):
    samples = []  # (is_beat, hue, value, target hue)

    def on_tick():
        lv = engine.latest_band3_levels
        samples.append((engine.latest_clock_beat.is_beat, lv["hue"], lv["value"], engine._beat_target_hue))

    _run(engine, clock, seconds, on_tick)
    return samples


def test_glide_without_fade_keeps_full_brightness_and_moves_all_beat(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _glide_config(fade=False, deg=90.0))
    _run(engine, clock, 6.0, lambda: None)
    samples = _collect(engine, clock)
    assert all(abs(v - 0.9) < 1e-9 for _, _, v, _ in samples)  # never dims between beats
    beat_idx = [i for i, s in enumerate(samples) if s[0]]
    for a, b in zip(beat_idx[2:], beat_idx[3:]):
        base = samples[a][3]
        mid = (samples[(a + b) // 2][1] - base) % 360.0
        end = (samples[b - 1][1] - base) % 360.0
        assert 30.0 < mid < 60.0, mid  # still moving halfway through the beat ("beat" timing)...
        assert 75.0 < end <= 90.5, end  # ...and nearly the full distance just before the next one
        assert abs((samples[b][3] - base) % 360.0 - 120.0) < 1e-6  # the beat lands on the next color


def test_fade_and_glide_together(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _glide_config(fade=True))
    _run(engine, clock, 6.0, lambda: None)
    samples = _collect(engine, clock)
    beat_idx = [i for i, s in enumerate(samples) if s[0]]
    for a, b in zip(beat_idx[2:], beat_idx[3:]):
        assert samples[a][2] > 0.8 and samples[b - 1][2] < 0.2  # brightness still flashes and fades
        assert (samples[b - 1][1] - samples[a][3]) % 360.0 > 90.0  # while the hue travels


def test_old_decay_mode_setting_is_migrated():
    from airam_lights.config.schema import BeatSyncModeConfig

    old = BeatSyncModeConfig.from_dict({"decay_mode": "hue"})
    assert old.glide_hue and not old.fade_brightness
    assert BeatSyncModeConfig.from_dict({}).fade_brightness and not BeatSyncModeConfig.from_dict({}).glide_hue


def _white_starts(engine, clock, seconds):
    starts, previous = [], set()

    def on_tick():
        nonlocal previous
        current = set(engine.latest_lamp_white_targets)
        if current - previous:
            starts.append(frozenset(current - previous))
        previous = current

    _run(engine, clock, seconds, on_tick)
    return starts


def _rotate_config(rotators):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_probability = 1.0
        bs.white_pulse_trigger = "random"
        bs.white_pulse_duration_ms = 40.0
        bs.white_pulse_release_ms = 30.0
        bs.white_pulse_target = "rotate"
        bs.white_pulse_rotators = rotators
        # A Chase that jumps 3 lamps per beat: following it would skip lamps.
        c.chase.enabled = True
        c.chase.sync_mode = "clock"
        c.chase.clock_every_n_beats = 1
        c.chase.beat_multiplier = 3.0

    return configure


def test_rotating_white_never_skips_a_lamp(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _rotate_config(1))
    _run(engine, clock, 6.0, lambda: None)
    starts = _white_starts(engine, clock, 6.0)
    assert len(starts) >= 8
    order = [DEVICES.index(next(iter(s))) for s in starts]
    assert all(len(s) == 1 for s in starts)
    assert all(b == (a + 1) % 4 for a, b in zip(order, order[1:]))


def test_rotating_white_two_opposite_lamps(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _rotate_config(2))
    _run(engine, clock, 6.0, lambda: None)
    starts = _white_starts(engine, clock, 6.0)
    assert starts and all(s in ({"a", "c"}, {"b", "d"}) for s in starts)
    assert all(x != y for x, y in zip(starts, starts[1:]))


def test_sequencer_walks_the_chase_order_with_evenly_spaced_lamps(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_duration_ms = 40.0
        bs.white_pulse_release_ms = 30.0
        bs.white_pulse_rotators = 2
        c.sequencer.enabled = True
        c.sequencer.white_pattern = "beats"
        c.sequencer.white_density = 1.0
        c.sequencer.white_accent_focus = 0.0  # every pattern step
        c.sequencer.white_build = 0.0
        c.sequencer.double_chance = 0.0
        c.sequencer.dark_pattern = "off"
        c.sequencer.phrase_accent = False
        c.sequencer.fills = False
        c.sequencer.walk_positions = "chase_order"

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    starts = _white_starts(engine, clock, 6.0)
    assert len(starts) >= 8
    assert all(s in ({"a", "c"}, {"b", "d"}) for s in starts)


def _temp_config(mode):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_probability = 1.0
        bs.white_pulse_trigger = "random"
        bs.white_pulse_duration_ms = 40.0
        bs.white_pulse_release_ms = 30.0
        bs.white_pulse_temp_mode = mode

    return configure


def _flash_temps(engine, clock, seconds):
    """(bar_position of the beat, temperature) for each new white flash."""
    out, was_white = [], False

    def on_tick():
        nonlocal was_white
        targets = engine.latest_lamp_white_targets
        if targets and not was_white:
            out.append((engine._clock_beat.bar_position, next(iter(targets.values())).temp))
        was_white = bool(targets)

    _run(engine, clock, seconds, on_tick)
    return out


def test_warm_cool_by_bar_weight(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _temp_config("bar"))
    _run(engine, clock, 6.0, lambda: None)
    flashes = _flash_temps(engine, clock, 6.0)
    assert len(flashes) >= 8
    expected = {0: 1.0, 1: 0.35, 2: 0.65, 3: 0.35}
    assert all(abs(temp - expected[pos]) < 1e-9 for pos, temp in flashes)


def test_warm_cool_alternates(monkeypatch):
    engine, clock, _ = _make_engine(monkeypatch, _temp_config("alternate"))
    _run(engine, clock, 6.0, lambda: None)
    temps = [t for _, t in _flash_temps(engine, clock, 6.0)]
    assert len(temps) >= 8 and all(a != b for a, b in zip(temps, temps[1:]))


def test_warm_cool_phrase_cools_toward_the_phrase_end(monkeypatch):
    def configure(c):
        _temp_config("phrase")(c)
        c.sequencer.phrase_bars = 2

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    temps = [t for _, t in _flash_temps(engine, clock, 8.0)]
    starts = [i for i, t in enumerate(temps) if t == 1.0]
    assert len(starts) >= 2
    one_phrase = temps[starts[0] + 1:starts[1]]
    assert one_phrase and all(a < b for a, b in zip(one_phrase, one_phrase[1:]))


def test_sequencer_white_flash_follows_attack_and_release(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_attack_ms = 60.0
        bs.white_pulse_duration_ms = 60.0
        bs.white_pulse_release_ms = 120.0
        c.sequencer.enabled = True
        c.sequencer.white_pattern = "downbeats"
        c.sequencer.white_density = 1.0
        c.sequencer.white_accent_focus = 0.0  # every pattern step
        c.sequencer.white_build = 0.0
        c.sequencer.double_chance = 0.0
        c.sequencer.dark_pattern = "off"
        c.sequencer.phrase_accent = False
        c.sequencer.fills = False
        c.sequencer.group_walk = "all"

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    levels = []
    _run(engine, clock, 4.0, lambda: levels.append(
        engine.latest_lamp_white_targets["a"].brightness if "a" in engine.latest_lamp_white_targets else 0.0))
    flashes, current = [], []
    for b in levels:
        if b > 0.0:
            current.append(b)
        elif current:
            flashes.append(current)
            current = []
    assert flashes
    for f in flashes:
        assert len(f) >= 5
        assert f[0] < max(f) and f[-1] < max(f)  # ramps in and out instead of an on/off block


def test_sequencer_downbeat_flash_outlasts_offbeat_flash(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.white_pulse_enabled = True
        bs.white_pulse_attack_ms = 20.0
        bs.white_pulse_duration_ms = 60.0
        bs.white_pulse_release_ms = 60.0
        c.sequencer.enabled = True
        c.sequencer.white_pattern = "beats"
        c.sequencer.white_density = 1.0
        c.sequencer.white_accent_focus = 0.0  # every pattern step
        c.sequencer.white_build = 0.0
        c.sequencer.double_chance = 0.0
        c.sequencer.dark_pattern = "off"
        c.sequencer.phrase_accent = False
        c.sequencer.fills = False
        c.sequencer.group_walk = "all"
        c.sequencer.pulse_dynamics = True
        c.sequencer.pulse_dynamics_amount = 1.0

    engine, clock, _ = _make_engine(monkeypatch, configure)
    _run(engine, clock, 6.0, lambda: None)
    frames = []  # (bar_position at flash start, frames lit, peak brightness)
    state = {"on": False, "count": 0, "peak": 0.0, "pos": None}

    def on_tick():
        target = engine.latest_lamp_white_targets.get("a")
        if target is not None:
            if not state["on"]:
                state.update(on=True, count=0, peak=0.0, pos=engine.latest_clock_beat.bar_position)
            state["count"] += 1
            state["peak"] = max(state["peak"], target.brightness)
        elif state["on"]:
            frames.append((state["pos"], state["count"], state["peak"]))
            state["on"] = False

    _run(engine, clock, 8.0, on_tick)
    downbeats = [f for f in frames if f[0] == 0]
    others = [f for f in frames if f[0] in (1, 3)]
    assert downbeats and others
    assert min(f[1] for f in downbeats) > max(f[1] for f in others)
    assert min(f[2] for f in downbeats) > max(f[2] for f in others)
