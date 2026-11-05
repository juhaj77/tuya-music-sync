"""Color Steps mode (effects/color_steps.py): intensity peaks in the bass, mids
and treble charge up a hue step, the beat clock releases it, and the new colour
rolls across the lamps. And the mode it replaced: a config saved in HSV Music
loads into it, with its hue range kept for Beat Sync and Peak Flash.

Pure tracker tests with made-up band levels, then the engine with a fake
spectrum (see test_sequencer_every_mode) and the real one on a synthetic song,
then the settings page (offscreen Qt, a scratch settings folder).
"""
import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import airam_lights.engine.visualization_engine as ve_module  # noqa: E402
from airam_lights.config.schema import (  # noqa: E402
    AppConfig,
    ColorMappingConfig,
    ColorStepsModeConfig,
    PerLampEffect,
    STEP_TIMINGS,
)
from airam_lights.engine import rules  # noqa: E402
from airam_lights.engine.visualization_engine import VisualizationEngine  # noqa: E402
from airam_lights.effects.color_steps import ColorStepTracker, grid_beats  # noqa: E402

DT = 0.02
BEAT = 0.5


def _cfg(**kw):
    """Without what would blur the plain step logic: every lamp at once, no
    glide, no fan, a snap to the new hue; steps at once on the peaks."""
    defaults = dict(
        step_on="peak", hits_per_step=1.0, wave_beats=0.0, spread_deg=0.0, glide_deg_per_beat=0.0,
        hue_attack_ms=1.0, bar_boost_deg=0.0, direction="forward",
    )
    defaults.update(kw)
    return ColorStepsModeConfig(**defaults)


def _distance(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _pulse(t, period, on=0.08, offset=0.0):
    return ((t - offset) % period) < on


def _kick(t):
    return (0.8 if _pulse(t, BEAT) else 0.5, 0.5, 0.5)


def _hat(t):
    return (0.5, 0.5, 0.8 if _pulse(t, BEAT, offset=BEAT / 2) else 0.5)


def _snare(t):
    return (0.5, 0.8 if _pulse(t, 2 * BEAT, offset=BEAT) else 0.5, 0.5)


def _run(tracker, cfg, levels_at, seconds, clock=None, positions=1, start=0.0, on_tick=None):
    """Feeds levels_at(t) for `seconds`. clock: position_at(t), None = no locked
    clock. Returns (t, signed step) of the steps taken, and the last state."""
    steps = []
    state = None
    seen = tracker.latest.steps
    for i in range(int(round(seconds / DT))):
        t = start + i * DT
        position = clock(t) if clock is not None else None
        period = BEAT if clock is not None else None
        state = tracker.update(levels_at(t), cfg, DT, t, position, period, 4, positions)
        if state.steps != seen:
            steps.append((t, state.last_step))
            seen = state.steps
        if on_tick is not None:
            on_tick(t, state)
    return steps, state


def _position(t):
    return t / BEAT


# -- what causes a step -----------------------------------------------------------------------------


def test_steady_music_never_steps():
    tracker = ColorStepTracker()
    steps, _ = _run(tracker, _cfg(), lambda t: (0.6, 0.5, 0.4), 10.0)
    assert steps == []


def test_a_held_note_wobbling_a_little_is_no_peak():
    tracker = ColorStepTracker()
    steps, _ = _run(tracker, _cfg(), lambda t: (0.6 + 0.005 * np.sin(t * 40.0), 0.5, 0.4), 10.0)
    assert steps == []


@pytest.mark.parametrize("song, band, influence", [
    (_kick, "bass", "bass_influence"),
    (_snare, "mid", "mid_influence"),
    (_hat, "treble", "treble_influence"),
])
def test_each_band_causes_steps_as_far_as_its_influence_goes(song, band, influence):
    off = dict(bass_influence=0.0, mid_influence=0.0, treble_influence=0.0)
    steps, state = _run(ColorStepTracker(), _cfg(**{**off, influence: 1.0}), song, 10.0)
    assert len(steps) >= 8
    assert state.last_band == band
    # Influence 0 lets nothing through, half as much takes about twice as many peaks per step.
    assert _run(ColorStepTracker(), _cfg(**off), song, 10.0)[0] == []
    half, _ = _run(ColorStepTracker(), _cfg(**{**off, influence: 0.5}), song, 10.0)
    assert 0.35 * len(steps) <= len(half) <= 0.65 * len(steps)


def test_only_the_band_let_in_moves_the_colour():
    bass_only = _cfg(mid_influence=0.0, treble_influence=0.0)
    assert _run(ColorStepTracker(), bass_only, _hat, 6.0)[0] == []
    assert _run(ColorStepTracker(), bass_only, _kick, 6.0)[0] != []


def test_hits_per_step_sets_how_many_peaks_a_step_takes():
    every, _ = _run(ColorStepTracker(), _cfg(hits_per_step=1.0), _kick, 20.0)
    fourth, _ = _run(ColorStepTracker(), _cfg(hits_per_step=4.0), _kick, 20.0)
    assert len(every) >= 35
    assert 0.2 * len(every) <= len(fourth) <= 0.3 * len(every)


def test_a_peak_counts_against_its_own_bands_recent_peaks():
    """A quiet mix's kicks step like a loud one's: each band has its own scale."""
    quiet = lambda t: (0.42 if _pulse(t, BEAT) else 0.35, 0.3, 0.3)
    loud = lambda t: (0.9 if _pulse(t, BEAT) else 0.6, 0.6, 0.6)
    a, _ = _run(ColorStepTracker(), _cfg(), quiet, 10.0)
    b, _ = _run(ColorStepTracker(), _cfg(), loud, 10.0)
    assert len(a) == pytest.approx(len(b), abs=3)


def test_silence_stops_everything():
    cfg = _cfg(glide_deg_per_beat=30.0)
    tracker = ColorStepTracker()
    _run(tracker, cfg, _kick, 4.0)
    before = tracker.latest.hues[0]
    steps, state = _run(tracker, cfg, lambda t: (0.0, 0.0, 0.0), 4.0, start=4.0)
    assert steps == []
    assert state.levels[0] == pytest.approx(0.0, abs=0.02)  # fades to Brightness min
    assert _distance(state.hues[0], before) < 5.0  # (the hue settling on the last step, no glide)


# -- the clock ---------------------------------------------------------------------------------------


def test_grid_lengths():
    assert [grid_beats(name, 4) for name in STEP_TIMINGS] == [None, 0.25, 0.5, 1.0, 2.0, 4.0]
    assert grid_beats("bar", 3) == 3.0


@pytest.mark.parametrize("step_on, grid", [("beat", 1.0), ("2 beats", 2.0), ("bar", 4.0), ("8th", 0.5)])
def test_steps_land_on_the_clocks_grid(step_on, grid):
    # Hi-hats between the beats, so the charge fills up well away from the lines.
    cfg = _cfg(step_on=step_on, treble_influence=1.0)
    steps, _ = _run(ColorStepTracker(), cfg, _hat, 20.0, clock=_position)
    assert len(steps) >= 4
    for t, _step in steps:
        since_line = (t / BEAT) % grid
        assert since_line * BEAT <= 0.15 + DT, (t, since_line)


def test_a_charge_waits_for_the_next_line_at_most_one_step_per_line():
    cfg = _cfg(step_on="bar", hits_per_step=1.0)
    steps, state = _run(ColorStepTracker(), cfg, _kick, 16.0, clock=_position)
    assert len(steps) == pytest.approx(16.0 / (4 * BEAT), abs=1)
    waited = []
    _run(ColorStepTracker(), cfg, _kick, 2.0, clock=_position, on_tick=lambda t, s: waited.append(s.waiting))
    assert any(waited)


def test_a_hit_landing_right_behind_the_line_still_makes_it():
    """The clock's lead puts its lines ahead of the hits: a hit that fills the
    charge just after the line goes out on that line, not a whole beat later."""
    cfg = _cfg(step_on="beat", hits_per_step=1.0)
    behind = 0.06
    steps, _ = _run(ColorStepTracker(), cfg, lambda t: _kick(t - behind), 10.0, clock=_position)
    assert len(steps) >= 15
    assert all(((t / BEAT) % 1.0) * BEAT <= behind + 0.06 for t, _ in steps)


def test_the_clock_alone_never_moves_the_colour():
    cfg = _cfg(step_on="beat")
    steps, _ = _run(ColorStepTracker(), cfg, lambda t: (0.6, 0.5, 0.4), 10.0, clock=_position)
    assert steps == []


def test_step_on_peak_ignores_the_clock_and_no_clock_means_peaks_too():
    a, _ = _run(ColorStepTracker(), _cfg(step_on="peak"), _hat, 6.0, clock=_position)
    b, _ = _run(ColorStepTracker(), _cfg(step_on="beat"), _hat, 6.0, clock=None)
    assert a and b
    # Right on the hats, halfway between the beats.
    assert all(abs(((t / BEAT) % 1.0) - 0.5) < 0.25 for t, _ in a)
    assert all(abs(((t / BEAT) % 1.0) - 0.5) < 0.25 for t, _ in b)


def test_a_grid_jumping_back_doesnt_fire_a_step():
    cfg = _cfg(step_on="bar", hits_per_step=0.5)
    tracker = ColorStepTracker()
    _run(tracker, cfg, _kick, 1.0, clock=lambda t: 1.0 + t / BEAT)  # charged, inside the first bar
    steps, _ = _run(tracker, cfg, _kick, 0.2, clock=lambda t: 0.5 + (t - 1.0) / BEAT, start=1.0)
    assert steps == []


def test_a_step_on_the_bar_line_goes_further():
    random.seed(1)
    cfg = _cfg(step_on="beat", direction="forward", step_min_deg=30.0, step_max_deg=30.0, bar_boost_deg=90.0)
    steps, _ = _run(ColorStepTracker(), cfg, _kick, 20.0, clock=_position)
    on_bar = [abs(s) for t, s in steps if ((t / BEAT) % 4.0) * BEAT <= 0.2]
    off_bar = [abs(s) for t, s in steps if ((t / BEAT) % 4.0) * BEAT > 0.2]
    assert on_bar and off_bar
    assert min(on_bar) > max(off_bar) + 40.0


# -- how far and which way ---------------------------------------------------------------------------


def test_step_sizes_stay_in_range_and_vary():
    cfg = _cfg(step_min_deg=25.0, step_max_deg=150.0)
    steps, _ = _run(ColorStepTracker(), cfg, _kick, 30.0)
    sizes = [abs(s) for _, s in steps]
    assert min(sizes) >= 25.0 * 0.85 and max(sizes) <= 150.0 * 1.15
    assert len({round(size, 3) for size in sizes}) > 0.8 * len(sizes)  # no loop of the same few colours


def test_a_step_charged_by_the_bass_goes_further_than_one_by_the_treble():
    def median_size(song):
        random.seed(5)
        steps, _ = _run(ColorStepTracker(), _cfg(), song, 20.0)
        return float(np.median([abs(s) for _, s in steps]))

    assert median_size(_kick) > 1.2 * median_size(_hat)


def test_denser_peaks_than_lately_mean_longer_steps():
    def song(t):
        period = BEAT if t < 40.0 else BEAT / 4  # a sparse verse, then a dense chorus
        return (0.8 if _pulse(t, period, on=0.04) else 0.5, 0.5, 0.5)

    random.seed(5)
    steps, _ = _run(ColorStepTracker(), _cfg(), song, 46.0)
    verse = [abs(s) for t, s in steps if 30.0 < t < 40.0]
    chorus = [abs(s) for t, s in steps if 42.0 < t < 46.0]
    assert np.median(chorus) > 1.3 * np.median(verse)


def test_direction_bands_turns_with_the_balance_and_forward_doesnt():
    def song(t):
        # Eight seconds of kicks only, then hi-hats only, then kicks again.
        return _hat(t) if 12.0 <= t < 24.0 else _kick(t)

    cfg = _cfg(direction="bands", treble_influence=1.0)
    steps, _ = _run(ColorStepTracker(), cfg, song, 36.0)
    signs = [s > 0 for _, s in steps]
    assert all(signs[:5])  # starts forward
    assert any(s < 0 for t, s in steps if 12.0 <= t < 24.0)  # the treble turned it back
    assert any(s > 0 for t, s in steps if t >= 30.0)  # the bass turned it forward again
    forward, _ = _run(ColorStepTracker(), _cfg(direction="forward", treble_influence=1.0), song, 36.0)
    assert all(s > 0 for _, s in forward)


def test_a_steady_mix_keeps_going_one_way():
    """Bass and treble both playing all along: no reason to keep turning round -
    jitter between the two would leave the hue hovering on one colour."""
    def song(t):
        return (_kick(t)[0], 0.5, _hat(t)[2])

    cfg = _cfg(direction="bands", treble_influence=1.0)
    steps, _ = _run(ColorStepTracker(), cfg, song, 30.0)
    turns = sum(a[1] * b[1] < 0 for a, b in zip(steps, steps[1:]))
    assert len(steps) > 30 and turns <= 1


# -- how fast: hue snap speed and glide --------------------------------------------------------------


def test_hue_snap_speed_sets_how_fast_the_colour_gets_to_a_step():
    def after_a_step(snap_ms):
        random.seed(9)
        cfg = _cfg(hue_attack_ms=snap_ms, step_min_deg=90.0, step_max_deg=90.0, hits_per_step=1.0)
        tracker = ColorStepTracker()
        start = tracker._walk
        _run(tracker, cfg, lambda t: _kick(t + 0.2), 0.4)  # one kick, at t = 0.3
        return _distance(tracker.latest.hues[0], start)

    assert after_a_step(1.0) > 60.0
    assert after_a_step(5000.0) < 5.0


def test_a_slow_hue_follows_the_walk_round_the_wheel_not_back_across_it():
    """Steps pile up far ahead of a very slow hue - it keeps going their way."""
    cfg = _cfg(hue_attack_ms=8000.0, step_min_deg=120.0, step_max_deg=120.0)
    tracker = ColorStepTracker()
    last = [None]
    moved = []

    def watch(t, state):
        if last[0] is not None:
            moved.append(((state.hues[0] - last[0] + 180.0) % 360.0) - 180.0)
        last[0] = state.hues[0]

    _run(tracker, cfg, _kick, 15.0, on_tick=watch)
    assert sum(moved) > 360.0  # went all the way round...
    assert min(moved) >= -1e-6  # ...and never back


def test_glide_moves_the_hue_between_the_steps_on_the_clocks_tempo():
    cfg = _cfg(glide_deg_per_beat=10.0)
    tracker = ColorStepTracker()
    hues = []
    _run(tracker, cfg, lambda t: (0.6, 0.5, 0.4), 5.0, clock=_position, on_tick=lambda t, s: hues.append(s.hues[0]))
    assert tracker.latest.steps == 0
    assert _distance(hues[-1], hues[0]) == pytest.approx(10.0 * 5.0 / BEAT, abs=2.0)


# -- across the lamps --------------------------------------------------------------------------------


def _arrivals(cfg, song, seconds=1.0, positions=4, clock=_position):
    """When a first step's hue reached each position (hue snap ~instant)."""
    tracker = ColorStepTracker()
    first = {}
    start = {}

    def watch(t, state):
        for i, hue in enumerate(state.hues):
            start.setdefault(i, hue)
            if i not in first and _distance(hue, start[i]) > 10.0:
                first[i] = t

    _run(tracker, cfg, song, seconds, clock=clock, positions=positions, on_tick=watch)
    return first


def test_a_step_rolls_across_the_lamps_over_the_wave_length():
    cfg = _cfg(step_on="peak", wave_beats=1.0, step_min_deg=90.0, step_max_deg=90.0)
    first = _arrivals(cfg, lambda t: _kick(t + 0.2), clock=_position)
    assert set(first) == {0, 1, 2, 3}
    assert first[0] < first[1] < first[2] < first[3]
    assert first[3] - first[0] == pytest.approx(BEAT, abs=0.06)


def test_wave_length_zero_changes_every_lamp_at_once():
    cfg = _cfg(step_on="peak", wave_beats=0.0, step_min_deg=90.0, step_max_deg=90.0)
    first = _arrivals(cfg, lambda t: _kick(t + 0.2))
    assert len(set(first.values())) == 1


def test_the_wave_runs_the_way_the_hue_stepped():
    cfg = _cfg(step_on="peak", direction="bands", wave_beats=1.0, step_min_deg=90.0, step_max_deg=90.0)
    tracker = ColorStepTracker()
    tracker._direction = -1.0
    first = {}
    start = {}

    def watch(t, state):
        for i, hue in enumerate(state.hues):
            start.setdefault(i, hue)
            if i not in first and _distance(hue, start[i]) > 10.0:
                first[i] = t

    _run(tracker, cfg, lambda t: _kick(t + 0.2), 1.0, clock=_position, positions=4, on_tick=watch)
    assert tracker.latest.last_step < 0.0
    assert first[3] < first[2] < first[1] < first[0]


def test_a_crest_lifts_each_lamp_as_the_wave_passes():
    cfg = _cfg(wave_beats=1.0, crest=1.0, crest_release_ms=100.0, step_min_deg=90.0, step_max_deg=90.0)
    peaks = {}

    def watch(t, state):
        for i, level in enumerate(state.levels):
            if level > peaks.get(i, (0.0, 0.0))[0]:
                peaks[i] = (level, t)

    def one_kick(t):
        return _kick(t + 0.2) if t < 0.6 else (0.5, 0.5, 0.5)

    _run(ColorStepTracker(), cfg, one_kick, 1.0, clock=_position, positions=4, on_tick=watch)
    assert all(level > 0.5 for level, _ in peaks.values())
    times = [peaks[i][1] for i in range(4)]
    assert times == sorted(times) and times[-1] - times[0] > 0.3


def test_crest_zero_leaves_the_brightness_to_the_bands():
    levels = []
    _run(ColorStepTracker(), _cfg(crest=0.0), _kick, 6.0, positions=3,
         on_tick=lambda t, s: levels.append(tuple(s.levels)))
    assert all(a == pytest.approx(b) for a, _, b in levels)
    assert max(a for a, _, _ in levels[100:]) > 0.7 and min(a for a, _, _ in levels[100:]) < 0.4


def test_hue_spread_fans_the_lamps_out_along_the_order():
    cfg = _cfg(spread_deg=60.0)
    _, state = _run(ColorStepTracker(), cfg, lambda t: (0.6, 0.5, 0.4), 1.0, positions=4)
    assert _distance(state.hues[0], state.hues[3]) == pytest.approx(60.0, abs=0.5)
    assert _distance(state.hues[0], state.hues[1]) == pytest.approx(20.0, abs=0.5)


def test_overlapping_waves_end_on_the_newest_colour_everywhere():
    cfg = _cfg(wave_beats=8.0, hits_per_step=0.5, step_min_deg=33.0, step_max_deg=33.0)
    tracker = ColorStepTracker()
    _run(tracker, cfg, _kick, 10.0, clock=_position, positions=4)
    _run(tracker, cfg, lambda t: (0.5, 0.5, 0.5), 6.0, clock=_position, positions=4, start=10.0)
    hues = tracker.latest.hues
    assert all(_distance(hues[0], hue) < 1.0 for hue in hues)
    assert not tracker._waves


def test_nothing_piles_up_on_a_long_run():
    cfg = _cfg(wave_beats=2.0, hits_per_step=0.25)
    tracker = ColorStepTracker()
    _run(tracker, cfg, _kick, 200.0, clock=_position, positions=8)
    assert len(tracker._waves) <= 32


def test_a_new_number_of_positions_starts_from_the_same_colour():
    tracker = ColorStepTracker()
    cfg = _cfg(glide_deg_per_beat=0.0)
    _, state = _run(tracker, cfg, _kick, 2.0, positions=1)
    hue = state.hues[0]
    _, state = _run(tracker, cfg, lambda t: (0.5, 0.5, 0.5), DT, positions=5, start=2.0)
    assert len(state.hues) == 5 and all(_distance(h, hue) < 15.0 for h in state.hues)


# -- in the engine -----------------------------------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def perf_counter(self):
        return self.now

    def time(self):
        return self.now


class _FakeLamps:
    def __init__(self, ids):
        self.ids = ids
        self.colors = {}
        self.whites = {}
        self.white_ticks = 0

    def selected_device_ids(self):
        return list(self.ids)

    def push_colors(self, colors):
        self.colors.update(colors)  # (a lamp mid white flash gets a white target instead)

    def push_white_targets(self, targets):
        self.whites = dict(targets)
        self.white_ticks += 1

    def start_strobe(self, bursts):
        pass

    def set_network_config(self, network, threshold):
        pass


class _Audio:
    samplerate = 48000

    def has_data(self):
        return False


TICK = 1.0 / 30.0
LAMPS = ["a", "b", "c", "d", "e"]  # "e" isn't in the Chase order


def _engine(monkeypatch, **steps):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)

    def bands(frame, lo, hi):
        phase = ((clock.now - 1000.0) / BEAT) % 1.0
        kick = 0.9 if min(phase, 1.0 - phase) * BEAT < TICK / 2 else 0.5
        return kick if lo < 200.0 else 0.5

    monkeypatch.setattr(ve_module, "band_energy", bands)
    config = AppConfig()
    config.color_mapping.mode = "color_steps"
    config.chase.enabled = config.group_switch.enabled = False
    config.rhythm.fallback = False
    config.rhythm.lead_ms = 0.0
    for key, value in {"hits_per_step": 1.0, "wave_beats": 0.0, **steps}.items():
        setattr(config.color_mapping.color_steps, key, value)
    for i, dev in enumerate(LAMPS[:4]):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)
    lamps = _FakeLamps(LAMPS)
    engine = VisualizationEngine(config, _Audio(), lamps)
    engine.running = True
    engine._raw_frame = object()
    return engine, clock, lamps


def _tick(engine, clock, seconds, on_tick=None):
    for _ in range(int(round(seconds / TICK))):
        engine.tick_visual()
        if on_tick is not None:
            on_tick()
        clock.now += TICK


def test_engine_steps_on_the_locked_clocks_beats(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    _tick(engine, clock, 6.0)
    assert engine.clock_active and engine._clock_beat.locked
    state = engine.latest_color_steps
    seen, at = state.steps, []

    def watch():
        nonlocal seen
        if engine.latest_color_steps.steps != seen:
            seen = engine.latest_color_steps.steps
            at.append(engine._beat_clock.position(clock.now) % 1.0)

    _tick(engine, clock, 5.0, watch)
    assert len(at) >= 8
    assert all(min(p, 1.0 - p) < 0.3 for p in at)
    assert set(lamps.colors) == set(LAMPS)
    assert set(engine.latest_band3_levels) == {"bass", "mid", "treble"}


def test_engine_lays_the_hues_along_the_chase_order_and_the_rest_with_the_first(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, spread_deg=90.0)
    _tick(engine, clock, 2.0)
    hues = {dev: color.to_hsv()[0] for dev, color in lamps.colors.items()}
    assert _distance(hues["a"], hues["d"]) == pytest.approx(90.0, abs=8.0)
    assert _distance(hues["a"], hues["b"]) == pytest.approx(30.0, abs=8.0)
    assert hues["e"] == pytest.approx(hues["a"])


def test_engine_without_a_chase_order_shows_one_colour(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, spread_deg=90.0, wave_beats=2.0)
    engine.config.per_lamp_effects.clear()
    _tick(engine, clock, 3.0)
    assert len({lamps.colors[dev].to_rgb255() for dev in LAMPS}) == 1


def test_the_clock_runs_in_this_mode_whatever_else_is_off():
    config = AppConfig()
    config.chase.enabled = config.group_switch.enabled = config.sequencer.enabled = False
    config.rhythm.shared_clock = False
    assert rules.clock_in_use(config, "color_steps")
    assert not rules.clock_in_use(config, "band_colors")


def test_coming_back_to_the_mode_starts_the_charge_over(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, hits_per_step=16.0)
    _tick(engine, clock, 3.0)
    assert engine.latest_color_steps.charge > 1.0
    engine.config.color_mapping.mode = "peak_flash"
    _tick(engine, clock, 0.2)
    engine.config.color_mapping.mode = "color_steps"
    engine.tick_visual()
    assert engine.latest_color_steps.charge < 1.0


# -- the global effects on top ------------------------------------------------------------------------


def _show_on(engine):
    """The Global Effects tab's show, all at once: Chase, Group Switch and the sequencer."""
    config = engine.config
    config.chase.enabled = True
    config.chase.sync_mode = "clock"
    config.chase.color_mode = "hue_shift_relative"
    config.chase.hue_shift_step_deg = 90.0
    config.group_switch.enabled = True
    config.group_switch.sync_mode = "clock"
    config.group_switch.color_mode = "complementary"
    sq = config.sequencer
    sq.enabled = True
    sq.white_pattern = "beats"
    sq.white_density = 1.0
    sq.white_accent_focus = 0.0
    sq.white_build = 0.0
    sq.dark_pattern = "before_backbeats"
    sq.dark_density = 1.0
    sq.color_sweep_enabled = True
    sq.dark_sweep_enabled = True
    sq.phrase_accent = False
    config.color_mapping.beat_sync.white_pulse_enabled = True
    config.color_mapping.beat_sync.dark_pulse_enabled = True
    config.network.lamp_transitions = "direct"


def test_the_sequencers_pulses_and_the_overlays_play_on_top(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, spread_deg=0.0, glide_deg_per_beat=0.0, crest=0.0)
    _show_on(engine)
    values = []
    hue_gaps = []

    def watch():
        hsv = {dev: lamps.colors[dev].to_hsv() for dev in lamps.colors}
        values.append(min(v for _, _, v in hsv.values()))
        hue_gaps.append(max(_distance(hsv["a"][0], hsv[dev][0]) for dev in ("b", "c", "d")))

    _tick(engine, clock, 8.0)
    _tick(engine, clock, 8.0, watch)
    assert engine.sequencer_status is not None
    kinds = {e["kind"] for e in engine.seq_events}
    assert {"white", "dark"} <= kinds  # the sequencer plays on this mode...
    assert lamps.white_ticks > 0  # ...its white flashes reach the lamps...
    assert min(values) < 0.2  # ...its dark pulses dip the colour...
    assert max(hue_gaps) > 45.0  # ...and Chase / Group Switch turn the lamps' hues.
    assert engine.latest_color_steps.steps > 5  # The mode's own steps go on under all of it.


def test_the_dynamic_color_sweep_reads_this_modes_hue(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    _show_on(engine)
    engine.config.sequencer.color_sweep_color = "dynamic"
    _tick(engine, clock, 2.0)
    assert engine._show_hue is not None  # the "dynamic" sweep colour reads this mode's hue


# -- on the real spectrum ------------------------------------------------------------------------------


class _Song:
    samplerate = 48000

    def __init__(self, clock):
        self.clock = clock

    def has_data(self):
        return True

    def read_latest(self, n):
        t = self.clock.now - 1000.0 - np.arange(n)[::-1] / self.samplerate
        beat = np.mod(t, BEAT)
        kick = np.exp(-beat / 0.06) * np.sin(2 * np.pi * 55.0 * beat)
        off = np.mod(t - BEAT / 2, BEAT)
        hat = np.exp(-off / 0.01) * np.sin(2 * np.pi * 7919.0 * t) * np.sin(2 * np.pi * 5003.0 * t)
        snare_at = np.mod(t - BEAT, 2 * BEAT)
        snare = np.exp(-snare_at / 0.04) * np.sin(2 * np.pi * 900.0 * t)
        chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.2, 329.6))
        return (0.8 * kick + 0.3 * hat + 0.2 * snare + 0.05 * chord).astype(np.float32)


def test_on_a_real_spectrum_the_colour_goes_all_the_way_round(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)
    config = AppConfig()
    config.color_mapping.mode = "color_steps"
    config.chase.enabled = config.group_switch.enabled = False
    for i, dev in enumerate(LAMPS):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i)
    lamps = _FakeLamps(LAMPS)
    engine = VisualizationEngine(config, _Song(clock), lamps)
    engine.running = True
    hues = []
    values = []
    for i in range(int(30.0 / TICK)):
        engine.tick_analysis()
        engine.tick_visual()
        clock.now += TICK
        if i > 150:
            hue, _, value = lamps.colors["a"].to_hsv()
            hues.append(hue)
            values.append(value)
    assert engine._clock_beat.locked
    steps = engine.latest_color_steps.steps
    assert 15 <= steps <= 90  # neither frantic nor stuck, at the default settings
    filled = np.count_nonzero(np.histogram(hues, bins=12, range=(0.0, 360.0))[0])
    assert filled >= 10  # what HSV Music couldn't: its hue stayed within a few bins
    assert max(values) - min(values) > 0.2  # a crest and the bands show
    assert min(values) >= config.color_mapping.color_steps.brightness_min - 1e-6


# -- settings ------------------------------------------------------------------------------------------


def test_a_config_saved_in_hsv_music_loads_into_color_steps_keeping_its_hue_range():
    loaded = ColorMappingConfig.from_dict({
        "mode": "hsv_music",
        "hsv": {"hue_min_deg": 11.0, "hue_max_deg": 252.0, "brightness_min": 0.5, "sensitivity": 0.1},
        "smoothing": {"attack_ms": 8.0, "release_ms": 107.0, "min_change_threshold": 0.02},
    })
    assert loaded.mode == "color_steps"
    assert loaded.hue_range.hue_min_deg == 11.0 and loaded.hue_range.hue_max_deg == 252.0
    assert loaded.smoothing.min_change_threshold == 0.02
    again = ColorMappingConfig.from_dict(loaded.to_dict())
    assert again.to_dict() == loaded.to_dict() and "hsv" not in loaded.to_dict()


def test_settings_round_trip_and_bad_choices_fall_back():
    cfg = ColorStepsModeConfig(
        bass_mid_hz=180.0, mid_treble_hz=3300.0, bass_influence=1.5, mid_influence=0.2, treble_influence=1.9,
        hits_per_step=5.0, step_on="8th", step_min_deg=11.0, step_max_deg=99.0, bar_boost_deg=7.0, direction="forward",
        hue_attack_ms=3000.0, glide_deg_per_beat=2.5, wave_beats=3.5, spread_deg=120.0, saturation=0.8,
        brightness_min=0.2, brightness_max=0.9, crest=0.25, crest_release_ms=123.0, attack_ms=12.0, release_ms=420.0,
    )
    assert ColorStepsModeConfig.from_dict(cfg.to_dict()) == cfg
    assert ColorStepsModeConfig.from_dict({"step_on": "whenever", "direction": "sideways"}) == ColorStepsModeConfig()


# -- the settings page ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
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


def test_the_mode_shows_on_the_visualizer_tab_with_its_quick_controls(window):
    vis, cmt = window.tabs.widget(0), window.tabs.widget(2)
    cs = window.controller.config.color_mapping.color_steps
    assert "hsv_music" not in vis._mode_buttons
    vis._mode_buttons["color_steps"].setChecked(True)
    assert window.controller.config.color_mapping.mode == "color_steps"
    assert vis.mode_settings_stack.currentWidget() is vis._mode_settings_pages["color_steps"]
    for name, slider, value in (
        ("bass_influence", cmt.steps_bass_slider, 0.4),
        ("mid_influence", cmt.steps_mid_slider, 1.3),
        ("treble_influence", cmt.steps_treble_slider, 1.7),
        ("hits_per_step", cmt.steps_hits_slider, 6.0),
        ("hue_attack_ms", cmt.steps_snap_slider, 2500.0),
    ):
        quick = vis.quick_sliders[("color_steps", f"color_mapping.color_steps.{name}")]
        quick._spin.setValue(value)
        assert getattr(cs, name) == pytest.approx(value) and slider.value() == pytest.approx(value), name
        slider._spin.setValue(value / 2)
        assert quick.value() == pytest.approx(value / 2), name
    cmt.show_mode("color_steps")
    assert cmt.sub_tabs.currentWidget() is cmt._mode_pages["color_steps"]
    assert "Color Steps" in [cmt.sub_tabs.tabText(i) for i in range(cmt.sub_tabs.count())]
    assert "HSV Music" not in [cmt.sub_tabs.tabText(i) for i in range(cmt.sub_tabs.count())]


def test_every_setting_on_the_page_writes_the_config(window):
    cmt = window.tabs.widget(2)
    cs = window.controller.config.color_mapping.color_steps
    for slider, name, value in (
        (cmt.steps_min_slider, "step_min_deg", 12.0),
        (cmt.steps_max_slider, "step_max_deg", 111.0),
        (cmt.steps_bar_boost_slider, "bar_boost_deg", 33.0),
        (cmt.steps_glide_slider, "glide_deg_per_beat", 7.5),
        (cmt.steps_wave_slider, "wave_beats", 2.5),
        (cmt.steps_spread_slider, "spread_deg", 77.0),
        (cmt.steps_saturation_slider, "saturation", 0.7),
        (cmt.steps_bright_min_slider, "brightness_min", 0.2),
        (cmt.steps_bright_max_slider, "brightness_max", 0.8),
        (cmt.steps_crest_slider, "crest", 0.3),
        (cmt.steps_crest_release_slider, "crest_release_ms", 444.0),
        (cmt.steps_attack_slider, "attack_ms", 44.0),
        (cmt.steps_release_slider, "release_ms", 555.0),
    ):
        slider._spin.setValue(value)
        assert getattr(cs, name) == pytest.approx(value), name
    cmt.steps_on_combo.setCurrentText("2 beats")
    cmt.steps_direction_combo.setCurrentText("forward")
    cmt.steps_bass_mid_spin.setValue(200)
    cmt.steps_mid_treble_spin.setValue(5000)
    assert cs.step_on == "2 beats" and cs.direction == "forward"
    assert cs.bass_mid_hz == 200.0 and cs.mid_treble_hz == 5000.0


def test_what_a_setting_needs_shows_when_its_missing(window):
    cmt = window.tabs.widget(2)
    config = window.controller.config
    # A fresh install's lamps have no Chase order: nothing to run a wave along.
    config.per_lamp_effects.clear()
    cmt._update_steps_states()
    assert not cmt.steps_wave_slider.isEnabled() and "Chase order" in cmt.steps_wave_slider.toolTip()
    assert cmt.steps_lamps_note.text()
    config.per_lamp_effects["a"] = PerLampEffect(device_id="a", chase_order=0)
    config.per_lamp_effects["b"] = PerLampEffect(device_id="b", chase_order=1)
    cmt._update_steps_states()
    assert cmt.steps_wave_slider.isEnabled() and cmt.steps_spread_slider.isEnabled()
    assert not cmt.steps_lamps_note.text()
    # No grid to go by.
    config.rhythm.tempo_lock = False
    cmt._update_steps_states()
    assert "Lock to tempo" in cmt.steps_clock_note.text() and not cmt.steps_bar_boost_slider.isEnabled()
    config.rhythm.tempo_lock = True
    cmt.steps_on_combo.setCurrentText("peak")
    assert not cmt.steps_clock_note.text() and not cmt.steps_bar_boost_slider.isEnabled()
    cmt.steps_on_combo.setCurrentText("beat")
    assert cmt.steps_bar_boost_slider.isEnabled()


def test_the_status_line_shows_the_charge_and_the_last_step(window):
    cmt = window.tabs.widget(2)
    controller = window.controller
    controller.config.color_mapping.mode = "beat_sync"
    cmt._refresh_steps_status()
    assert cmt.steps_status_label.text() == ""
    controller.config.color_mapping.mode = "color_steps"
    cmt._refresh_steps_status()
    assert "runs while" in cmt.steps_status_label.text()
    controller.engine.running = True
    state = controller.engine.latest_color_steps
    state.charges, state.charge, state.waiting = {"bass": 1.5, "mid": 0.5, "treble": 0.0}, 2.0, True
    state.steps, state.last_step, state.last_band = 3, -72.0, "treble"
    cmt._refresh_steps_status()
    text = cmt.steps_status_label.text()
    controller.engine.running = False
    assert "charge 2.0" in text and "bass 1.5" in text and "waiting for the next beat" in text
    assert "-72 deg" in text and "treble" in text


def test_the_hue_range_moved_to_the_pages_that_use_it(window):
    cmt = window.tabs.widget(2)
    config = window.controller.config.color_mapping
    # One setting on two pages, each slider following the other.
    cmt.hue_min_slider._spin.setValue(30.0)
    assert config.hue_range.hue_min_deg == 30.0 and cmt.peak_hue_min_slider.value() == 30.0
    cmt.peak_hue_max_slider._spin.setValue(200.0)
    assert config.hue_range.hue_max_deg == 200.0 and cmt.hue_max_slider.value() == 200.0
    # Greyed out where it isn't used.
    cmt.beat_hue_mode_combo.setCurrentText("random")
    assert not cmt.hue_min_slider.isEnabled()
    cmt.beat_hue_mode_combo.setCurrentText("spectrum")
    assert cmt.hue_max_slider.isEnabled()
    cmt.peak_hue_source_combo.setCurrentText("drift")
    assert not cmt.peak_hue_min_slider.isEnabled()
    cmt.peak_hue_source_combo.setCurrentText("centroid")
    assert cmt.peak_hue_max_slider.isEnabled()
