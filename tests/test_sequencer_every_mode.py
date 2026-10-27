"""The pulse sequencer in every colour mode: Peak Flash, HSV Music and Band
Colors get its white flashes, dark pulses, strobes and color sweeps as Beat
Sync does, Beat Sync White only its dark pulses and dark sweeps - plus Beat
Sync White's temperature changing every N beats, on the bar (fake clock and
a synthetic kick, no audio, no lamps)."""
import random

import numpy as np
import pytest

import airam_lights.engine.visualization_engine as ve_module
from airam_lights.audio.capture import AudioCapture
from airam_lights.color.models import WhiteTarget
from airam_lights.config.schema import AppConfig, BeatSyncWhiteModeConfig, PerLampEffect, PulseSequencerConfig
from airam_lights.effects.pulse_sequencer import PulseSequencer
from airam_lights.engine import rules
from airam_lights.engine.visualization_engine import VisualizationEngine

PERIOD = 0.5  # 120 BPM
TICK = 1.0 / 30.0
DEVICES = ["a", "b", "c", "d"]
OTHER_MODES = ("peak_flash", "hsv_music", "band_colors")


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def perf_counter(self):
        return self.now

    def time(self):
        return self.now


class _FakeLampManager:
    def __init__(self, clock):
        self.clock = clock
        self.color_calls = []  # (time, {device_id: Color})
        self.white_calls = []  # (time, {device_id: WhiteTarget})
        self.strobes = []

    def selected_device_ids(self):
        return list(DEVICES)

    def push_colors(self, colors):
        self.color_calls.append((self.clock.now, dict(colors)))

    def push_white_targets(self, targets):
        self.white_calls.append((self.clock.now, dict(targets)))

    def start_strobe(self, bursts):
        self.strobes.append((self.clock.now, dict(bursts)))

    def set_network_config(self, network, min_change_threshold):
        pass


def _make_engine(monkeypatch, mode, shared_clock=False, **seq):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)

    def kick_energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", kick_energy)
    # HSV Music reads the spectrum itself (the fake frame has none).
    monkeypatch.setattr(ve_module, "spectral_centroid_hz", lambda frame, *a: 800.0)
    monkeypatch.setattr(ve_module, "spectral_contrast", lambda frame, *a: 0.5)

    config = AppConfig()
    config.color_mapping.mode = mode
    config.rhythm.shared_clock = shared_clock
    config.rhythm.lead_ms = 0.0
    config.rhythm.fallback = False  # the fake frame has no spectrum for the spectral flux
    config.network.lamp_transitions = "direct"
    config.network.lamp_command_rate_hz = 20.0
    config.chase.enabled = False
    config.group_switch.enabled = False
    bs = config.color_mapping.beat_sync
    bs.dark_pulse_enabled = True
    bs.dark_pulse_depth = 1.0
    bs.dark_pulse_duration_ms = 100.0
    bs.dark_pulse_attack_ms = 5.0
    bs.dark_pulse_release_ms = 20.0
    bs.white_pulse_enabled = True
    bs.white_pulse_white_brightness = 0.8
    sq = config.sequencer
    sq.enabled = True
    sq.white_pattern = "beats"
    sq.white_density = 1.0
    sq.white_accent_focus = 0.0
    sq.white_build = 0.0
    sq.double_chance = 0.0
    sq.dark_pattern = "before_backbeats"
    sq.dark_density = 1.0
    sq.phrase_accent = False
    sq.fills = False
    sq.drop_detection = False
    sq.min_group_gap_ms = 0.0
    for name, value in seq.items():
        setattr(sq, name, value)
    for i, dev in enumerate(DEVICES):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)

    lamps = _FakeLampManager(clock)
    engine = VisualizationEngine(config, AudioCapture(), lamps)
    engine.running = True
    engine._raw_frame = object()
    return engine, clock, lamps


def _run(engine, clock, seconds, on_tick=None):
    for _ in range(int(round(seconds / TICK))):
        engine.tick_visual()
        if on_tick is not None:
            on_tick()
        clock.now += TICK


def _run_until(engine, clock, done, max_seconds=10.0):
    for _ in range(int(round(max_seconds / TICK))):
        engine.tick_visual()
        clock.now += TICK
        if done():
            return
    raise AssertionError("never happened")


_EXTRAS = dict(
    strobe_enabled=True, strobe_placement="bars", strobe_chance=1.0, strobe_min_gap_bars=2, strobe_min_level="calm",
    color_sweep_enabled=True, color_sweep_placement="bars", color_sweep_chance=1.0, color_sweep_min_gap_bars=1,
    color_sweep_min_level="calm",
)


# -- the rules -------------------------------------------------------------------------------------


def test_the_sequencer_runs_in_every_mode_and_the_beat_modes_need_the_shared_clock():
    config = AppConfig()
    config.sequencer.enabled = True
    config.rhythm.shared_clock = False
    config.chase.enabled = config.group_switch.enabled = False
    for mode in OTHER_MODES:
        assert rules.sequencer_enabled_for(config, mode) and rules.clock_in_use(config, mode)
    for mode in ("beat_sync", "beat_sync_white"):
        assert not rules.sequencer_enabled_for(config, mode) and not rules.clock_in_use(config, mode)
    config.rhythm.shared_clock = True
    assert rules.sequencer_enabled_for(config, "beat_sync") and rules.sequencer_enabled_for(config, "beat_sync_white")
    config.sequencer.enabled = False
    assert not any(rules.sequencer_enabled_for(config, mode) for mode in OTHER_MODES)
    assert not rules.clock_in_use(config, "band_colors")
    assert rules.sequencer_dark_only("beat_sync_white") and not rules.sequencer_dark_only("peak_flash")


# -- the other colour modes get the sequencer's pulses ----------------------------------------------


@pytest.mark.parametrize("mode", OTHER_MODES)
def test_every_mode_gets_the_white_flashes_and_dark_pulses(monkeypatch, mode):
    engine, clock, lamps = _make_engine(monkeypatch, mode)
    seen = []  # (in a dark pulse, lamp a's colour) per tick it got one

    def on_tick():
        if lamps.color_calls and lamps.color_calls[-1][0] == clock.now and "a" in lamps.color_calls[-1][1]:
            seen.append((engine._beat_dark_until is not None, lamps.color_calls[-1][1]["a"]))

    _run(engine, clock, 12.0, on_tick)
    # No 'follow the shared clock' needed here - that's Beat Sync's / Beat Sync White's.
    assert engine.clock_active and engine.sequencer_status is not None
    assert {"white", "dark"} <= {e["kind"] for e in engine.seq_events}

    # White flashes: the lamps' white LEDs, with the mode's colour under them,
    # walking between the groups instead of lighting every lamp.
    flashes = [(set(call), t) for _, call in lamps.white_calls for t in call.values()]
    assert flashes and all(isinstance(t, WhiteTarget) and t.under is not None for _, t in flashes)
    assert max(t.brightness for _, t in flashes) > 0.5
    assert any(lamps_lit != set(DEVICES) for lamps_lit, _ in flashes)

    # Dark pulses: a dip toward black on top of the mode's colour.
    in_dark = [max(c.r, c.g, c.b) for dark, c in seen if dark]
    outside = sorted(max(c.r, c.g, c.b) for dark, c in seen if not dark)
    assert in_dark and outside
    assert min(in_dark) < 0.2 * outside[len(outside) // 2]


def test_strobes_and_color_sweeps_play_in_the_other_modes_too(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "peak_flash", **_EXTRAS)
    _run(engine, clock, 20.0)
    assert lamps.strobes
    kinds = {e["kind"] for e in engine.seq_events}
    assert {"strobe", "sweep"} <= kinds


def test_the_pulses_switch_off_with_the_sequencer(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "band_colors")
    _run(engine, clock, 8.0)
    assert lamps.white_calls
    engine.config.sequencer.enabled = False
    _run(engine, clock, 1.0)
    lamps.white_calls.clear()
    count = len(engine.seq_events)
    _run(engine, clock, 4.0)
    assert not lamps.white_calls and len(engine.seq_events) == count
    assert engine.sequencer_status is None


def test_old_pulses_stay_in_the_past_when_the_sequencer_comes_back(monkeypatch):
    # The position from before a pause read as a jump of the grid, which
    # moved the timeline's old pulses forward by the whole pause - up to "now".
    engine, clock, lamps = _make_engine(monkeypatch, "band_colors")
    _run(engine, clock, 8.0)
    entry = engine.seq_events[-1]
    age = engine.seq_position - entry["pos"]  # beats ago
    engine.config.sequencer.enabled = False
    _run(engine, clock, 4.0)
    engine.config.sequencer.enabled = True
    _run(engine, clock, 1.0)
    assert engine.seq_position - entry["pos"] == pytest.approx(age + 5.0 / PERIOD, abs=1.0)


def test_switching_modes_keeps_the_sequencer_going(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "band_colors", shared_clock=True)
    _run(engine, clock, 8.0)
    phrase_bar = engine.sequencer_status["phrase_bar"]
    for mode in ("peak_flash", "beat_sync", "hsv_music", "beat_sync_white", "band_colors"):
        engine.config.color_mapping.mode = mode
        _run(engine, clock, 2.0)  # one bar
        assert engine.sequencer_status is not None, mode
    # About six bars on from where it was - not started over by the switches.
    assert (engine.sequencer_status["phrase_bar"] - phrase_bar) % engine.config.sequencer.phrase_bars in (5, 6, 7)


# -- Beat Sync White: only the dark pulses ------------------------------------------------------------


def test_beat_sync_white_takes_only_the_dark_pulses(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync_white", shared_clock=True, **_EXTRAS)
    engine.config.sequencer.white_pattern = "sixteenths"  # (would leave no step free for a dark pulse)
    seen = []  # (in a dark pulse, brightness) per tick

    def on_tick():
        target = lamps.white_calls[-1][1]["a"]
        seen.append((engine._beat_dark_until is not None, target.brightness))

    _run(engine, clock, 20.0, on_tick)
    assert engine.sequencer_status is not None and engine.sequencer_status["white_pattern"] is None
    assert {e["kind"] for e in engine.seq_events} == {"dark"}
    assert not lamps.strobes and engine._color_sweep is None
    # Only its own white: no colour under it, no walking flash.
    assert all(t.under is None and set(call) == set(DEVICES) for _, call in lamps.white_calls for t in call.values())
    # The dark pulses dip it, on the Dark pulses section's depth and attack/release.
    in_dark = [b for dark, b in seen if dark]
    outside = sorted(b for dark, b in seen if not dark)
    assert in_dark and min(in_dark) < 0.2 * outside[len(outside) // 2]


def test_beat_sync_white_own_dark_pulses_step_aside_while_the_sequencer_places_them(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync_white", shared_clock=True)
    engine.config.sequencer.dark_pattern = "off"
    engine.config.color_mapping.beat_sync_white.dark_pulse_probability = 1.0  # every beat, when it rolls
    _run(engine, clock, 6.0)  # the clock locks, the sequencer takes over
    rolled = []
    _run(engine, clock, 6.0, lambda: rolled.append(engine._beat_white_dark_until is not None))
    assert not any(rolled)
    engine.config.sequencer.enabled = False
    _run(engine, clock, 3.0, lambda: rolled.append(engine._beat_white_dark_until is not None))
    assert any(rolled)  # its own per-beat ones are back


def test_dark_only_sequencer_places_no_white_strobe_or_sweep():
    cfg = PulseSequencerConfig(
        enabled=True, white_pattern="sixteenths", white_density=1.0, white_accent_focus=0.0, white_build=0.0,
        double_chance=1.0, dark_pattern="before_backbeats", dark_density=1.0, phrase_bars=4, phrase_accent=False,
        fills=False, drop_detection=False, **_EXTRAS,
    )

    def run(seq, dark_only):
        out, pos, now = [], 0.1, 0.0
        while pos < 16:  # four bars
            out.extend(seq.tick(pos, 0.5, now, 0.03, 2, 4, dark_only=dark_only))
            pos += 0.125
            now += 0.03
        return out

    normal = run(PulseSequencer(cfg, random.Random(1)), False)
    assert not [e for e in normal if e.kind == "dark"]  # every 16th has a white flash there
    dark_only = run(PulseSequencer(cfg, random.Random(1)), True)
    assert {e.kind for e in dark_only} == {"dark"}
    assert [e.step % 16 for e in dark_only] == [3, 11] * 4  # before beats 2 and 4 of every bar


def test_dark_only_drops_a_double_decided_before():
    cfg = PulseSequencerConfig(
        enabled=True, white_pattern="downbeats", white_density=1.0, white_accent_focus=0.0, white_build=0.0,
        double_chance=1.0, dark_pattern="off", phrase_accent=False, fills=False, drop_detection=False,
    )
    seq = PulseSequencer(cfg, random.Random(1))
    seq.tick(3.9, 0.5, 0.0, 0.03, 2, 4)
    events = seq.tick(4.05, 0.5, 0.03, 0.03, 2, 4)  # the downbeat: a flash, its double due an 8th later
    assert [e.kind for e in events] == ["white"] and seq._pending_doubles
    assert seq.tick(4.6, 0.5, 0.06, 0.03, 2, 4, dark_only=True) == []


# -- Beat Sync White: the temperature changes once the hits add up (dsp.hits) -----------------------


@pytest.mark.parametrize("hits", [1, 2, 4])
def test_beat_sync_white_changes_temperature_every_n_hits(monkeypatch, hits):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync_white", shared_clock=True)
    engine.config.sequencer.enabled = False
    engine.config.color_mapping.beat_sync_white.temp_change_hits = float(hits)
    _run(engine, clock, 6.0)  # let the clock lock
    beats, changes = [], []
    last = engine._beat_white_target_temp

    def on_tick():
        nonlocal last
        beat = engine.latest_clock_beat
        if beat.is_beat:
            beats.append(beat)
        if engine._beat_white_target_temp != last:
            changes.append(beat)
            last = engine._beat_white_target_temp

    _run(engine, clock, 12.0, on_tick)
    assert len(beats) >= 20
    assert abs(len(changes) - len(beats) / hits) <= 1
    assert all(beat.is_beat for beat in changes)  # on a beat's flash (a kick on every beat here)


def test_beat_sync_white_without_the_clock_follows_its_own_beats(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync_white", shared_clock=False)
    engine.config.sequencer.enabled = False
    bsw = engine.config.color_mapping.beat_sync_white
    bsw.temp_change_hits = 2.0
    bsw.sensitivity, bsw.min_interval_ms, bsw.min_energy = 1.3, 200.0, 0.1
    engine._apply_smoothing_settings()
    beats = changes = 0
    last = engine._beat_white_target_temp

    def on_tick():
        nonlocal beats, changes, last
        beats += engine.latest_band3_levels.get("beat", 0.0) > 0.0
        if engine._beat_white_target_temp != last:
            changes += 1
            last = engine._beat_white_target_temp

    _run(engine, clock, 10.0, on_tick)
    assert beats >= 15 and abs(changes - beats / 2) <= 1


@pytest.mark.parametrize("mode", ["beat_sync", "beat_sync_white"])
def test_a_change_held_back_by_a_dark_pulse_survives_the_next_beat(monkeypatch, mode):
    # The change is decided on the hit and made on the flash after it - a
    # long dark pulse can hold that flash past the next hit, which must not
    # cancel the change.
    engine, clock, lamps = _make_engine(monkeypatch, mode, shared_clock=True)
    engine.config.sequencer.enabled = False
    engine.config.color_mapping.beat_sync.hue_change_hits = 2.0
    engine.config.color_mapping.beat_sync_white.temp_change_hits = 2.0

    def value():
        return engine._beat_target_hue if mode == "beat_sync" else engine._beat_white_target_temp

    _run(engine, clock, 6.0)
    # Right on a change (the hits count from zero again): hold every flash from here on.
    _run_until(engine, clock, lambda: engine.latest_clock_beat.is_beat and engine.hits.drive == 0.0)
    engine._beat_dark_until = clock.now + 60.0
    before = value()
    seen = []

    def two_more_beats():
        if engine.latest_clock_beat.is_beat:
            seen.append(engine.latest_clock_beat)
        return len(seen) == 2

    _run_until(engine, clock, two_more_beats)
    assert engine._beat_hue_due if mode == "beat_sync" else engine._bsw_temp_due  # the second hit made it due...
    assert value() == before  # ...but held, no flash on either
    engine._beat_dark_until = clock.now  # the dark pulse ends: the flash that was held back
    _run(engine, clock, TICK)
    assert value() != before


def test_beat_sync_white_temperature_snaps_both_ways_and_follows_its_slider(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync_white", shared_clock=True)
    smoother = engine._temp_smoother_beat_white
    bsw = engine.config.color_mapping.beat_sync_white
    assert smoother.attack_ms == smoother.release_ms == bsw.temp_attack_ms
    bsw.temp_attack_ms = 120.0
    engine.apply_config(engine.config)
    assert smoother.attack_ms == smoother.release_ms == 120.0
    # Toward warm as fast as toward cool (a change toward warm took 300 ms before).
    smoother.reset(1.0)
    warm = 1.0 - smoother.update(0.0, 0.05)
    smoother.reset(0.0)
    cool = smoother.update(1.0, 0.05)
    assert warm == pytest.approx(cool)


def test_temp_change_hits_is_saved_and_kept_sane():
    config = AppConfig()
    config.color_mapping.beat_sync_white.temp_change_hits = 4.0
    again = AppConfig.from_dict(config.to_dict())
    assert again.color_mapping.beat_sync_white.temp_change_hits == 4.0
    assert BeatSyncWhiteModeConfig.from_dict({"temp_every_n_beats": 4}).temp_change_hits == 1.0  # older files
    assert BeatSyncWhiteModeConfig.from_dict({"temp_change_hits": 0}).temp_change_hits == 0.1


# -- the whole show on real spectra -----------------------------------------------------------------


class _SyntheticAudio:
    """Stands in for AudioCapture: a kick drum on every beat (a decaying
    55 Hz thump), a hi-hat between the beats and a quiet chord - the latest
    `n` samples up to the fake clock's now."""

    samplerate = 48000

    def __init__(self, clock):
        self.clock = clock

    def has_data(self):
        return True

    def read_latest(self, n):
        t = self.clock.now - 1000.0 - np.arange(n)[::-1] / self.samplerate
        beat = np.mod(t, PERIOD)
        kick = np.exp(-beat / 0.06) * np.sin(2 * np.pi * 55.0 * beat)
        off = np.mod(t - PERIOD / 2, PERIOD)
        hat = np.exp(-off / 0.01) * np.sin(2 * np.pi * 7919.0 * t) * np.sin(2 * np.pi * 5003.0 * t)
        chord = sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.2, 329.6))
        return (0.8 * kick + 0.3 * hat + 0.05 * chord).astype(np.float32)


def _run_on_real_spectra(monkeypatch, mode, seconds=16.0):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)
    config = AppConfig.with_defaults()  # the fresh-install show: strobes, sweeps, Chase, Group Switch...
    config.color_mapping.mode = mode
    for i, dev in enumerate(DEVICES):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)
    lamps = _FakeLampManager(clock)
    random.seed(7)  # (the sequencer's own random numbers are seeded in conftest)
    engine = VisualizationEngine(config, _SyntheticAudio(clock), lamps)
    engine.running = True
    for _ in range(int(round(seconds / TICK))):
        engine.tick_analysis()
        engine.tick_visual()
        clock.now += TICK
    return engine, lamps


def test_every_mode_plays_the_same_pulses_on_real_spectra(monkeypatch):
    logs = {}
    for mode in ("beat_sync", "beat_sync_white", *OTHER_MODES):
        engine, lamps = _run_on_real_spectra(monkeypatch, mode)
        assert engine._beat_clock.locked and engine.sequencer_status is not None, mode
        assert engine._beat_clock.period_s == pytest.approx(PERIOD, rel=0.02), mode
        ticks = len({t for t, _ in lamps.color_calls} | {t for t, _ in lamps.white_calls})
        assert ticks >= int(16.0 / TICK) - 2, mode  # every lamp sent something every tick
        logs[mode] = [(e["kind"], round(e["pos"], 6)) for e in engine.seq_events]
    assert "dark" in {kind for kind, _ in logs["beat_sync_white"]} <= {"dark", "dark_sweep"}  # (the dark sweep dims its white)
    assert {"white", "dark"} <= {kind for kind, _ in logs["beat_sync"]}
    assert {"white", "dark"} <= {kind for kind, _ in logs["peak_flash"]}
    # One set of settings: the same pulses on the same beats in every mode without beats of its own.
    assert logs["peak_flash"] == logs["hsv_music"] == logs["band_colors"]


# -- switching modes -------------------------------------------------------------------------------


def test_coming_back_to_beat_sync_replays_no_old_white_flash(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, "beat_sync", shared_clock=True)
    engine.config.sequencer.enabled = False
    bs = engine.config.color_mapping.beat_sync
    bs.white_pulse_probability, bs.white_pulse_trigger = 1.0, "random"
    bs.white_pulse_attack_ms, bs.white_pulse_duration_ms, bs.white_pulse_release_ms = 5.0, 300.0, 400.0
    engine.apply_config(engine.config)
    _run_until(engine, clock, lambda: engine._last_white_amount > 0.5)
    engine.config.color_mapping.mode = "band_colors"  # mid flash
    _run(engine, clock, TICK)
    bs.white_pulse_probability = 0.0  # no new ones: anything white now would be the old flash
    engine.config.color_mapping.mode = "beat_sync"
    lamps.white_calls.clear()
    _run(engine, clock, 3 * TICK)
    assert not lamps.white_calls
