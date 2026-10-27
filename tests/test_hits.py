"""Beat Sync and Beat Sync White on the hits (dsp/hits.py): only the beats
with a hit flash, as brightly as they hit, and the colour changes once the
hits add up to Hits per change - every beat on a pounding four-on-the-floor,
every second beat on a half-time kick, never in a breakdown although the
clock keeps the beat going.

Pure tracker tests with made-up beat band levels, one with a synthetic song
through the real FFT, and the engine on the shared clock with a fake kick
envelope (see test_rhythm_engine).
"""
import math

import numpy as np

import airam_lights.engine.visualization_engine as ve_module
from airam_lights.config.schema import AppConfig, BeatSyncModeConfig, BeatSyncWhiteModeConfig
from airam_lights.dsp.bands import band_energy
from airam_lights.dsp.fft_engine import FFTEngine
from airam_lights.dsp.hits import FLASH_MIN, HitTracker, flash_level

from test_rhythm_engine import PERIOD, TICK, _make_engine, _run

DT = 1.0 / 30.0
BEAT = 0.5


def _song(kicks, rise=0.15, base=0.6):
    """The beat band's level: `base`, and a hit `rise` above it on every beat k with kicks(k)."""

    def level(t):
        k = int(t // BEAT)
        return base + (rise * math.exp(-(t - k * BEAT) / 0.05) if kicks(k) else 0.0)

    return level


def _play(tracker, start, seconds, level_at, hits=1.0, lead=0.05):
    """Ticks level_at(t) from `start` for `seconds`, beat k going out `lead` s
    before k * BEAT. Returns {beat k: (flash strength, colour changed)}."""
    out = {}
    k = math.ceil((start + lead) / BEAT - 1e-9)
    for i in range(int(round(seconds / DT))):
        t = start + (i + 1) * DT
        tracker.update(level_at(t), DT)
        is_beat = t >= k * BEAT - lead
        strength = tracker.flash_strength(is_beat)
        if is_beat:
            out[k] = (0.0, False)
            k += 1
        if strength >= FLASH_MIN and out:
            out[max(out)] = (strength, tracker.count_flash(strength, hits))
    return out


def _flashed(beats):
    return sorted(k for k, (strength, _) in beats.items() if strength >= FLASH_MIN)


def _changed(beats):
    return sorted(k for k, (_, changed) in beats.items() if changed)


def test_a_four_on_the_floor_flashes_and_changes_on_every_beat():
    tracker = HitTracker()
    beats = _play(tracker, 0.0, 10.0, _song(lambda k: True))
    steady = {k: v for k, v in beats.items() if k >= 2}
    assert _flashed(steady) == sorted(steady)
    assert all(strength == 1.0 for strength, _ in steady.values())
    assert _changed(steady) == sorted(steady)
    assert tracker.period == 1 and tracker.typical_flashes == 1


def test_a_half_time_kick_flashes_on_its_beats_only():
    tracker = HitTracker()
    beats = _play(tracker, 0.0, 12.0, _song(lambda k: k % 2 == 0))
    steady = {k: v for k, v in beats.items() if k >= 8}
    assert _flashed(steady) == [k for k in sorted(steady) if k % 2 == 0]
    assert _changed(steady) == [k for k in sorted(steady) if k % 2 == 0]
    assert tracker.period == 2 and tracker.pattern_hits == 1


def test_a_kick_once_a_bar_and_a_pattern_of_two():
    tracker = HitTracker()
    beats = _play(tracker, 0.0, 16.0, _song(lambda k: k % 4 == 0))
    assert _flashed({k: v for k, v in beats.items() if k >= 12}) == [k for k in range(12, max(beats) + 1) if k % 4 == 0]
    assert tracker.period == 4

    tracker = HitTracker()
    beats = _play(tracker, 0.0, 16.0, _song(lambda k: k % 4 in (0, 3)))  # beats 1 and 4
    assert _flashed({k: v for k, v in beats.items() if k >= 12}) == [
        k for k in range(12, max(beats) + 1) if k % 4 in (0, 3)
    ]
    assert tracker.period == 4 and tracker.pattern_hits == 2


def test_a_breakdown_neither_flashes_nor_changes_and_the_drop_flashes_at_once():
    tracker = HitTracker()
    level = _song(lambda k: k < 16 or k >= 40)
    beats = _play(tracker, 0.0, 30.0, level)
    breakdown = {k: v for k, v in beats.items() if 16 <= k < 40}
    assert len(_flashed(breakdown)) <= 1  # the hits stopped: one beat still expects one
    assert not _changed({k: v for k, v in breakdown.items() if k > 16})
    drop = {k: v for k, v in beats.items() if k >= 40}
    assert _flashed(drop) == sorted(drop)  # its very first kick included
    assert _changed(drop) == sorted(drop)


def test_a_held_note_or_pad_never_flashes():
    tracker = HitTracker()
    _play(tracker, 0.0, 6.0, _song(lambda k: True))
    pad = _play(tracker, 6.0, 10.0, lambda t: 0.6 + 0.01 * math.sin(t * 7.0))
    assert len(_flashed(pad)) <= 1


def test_silence_never_flashes():
    tracker = HitTracker()
    beats = _play(tracker, 0.0, 6.0, lambda t: 0.02 + 0.06 * ((t % BEAT) < 0.05))
    assert len(_flashed(beats)) <= 1  # (the very first beat has nothing to go by)


def test_softer_hits_flash_lower_and_change_less_often():
    tracker = HitTracker()
    _play(tracker, 0.0, 8.0, _song(lambda k: True))
    soft = _play(tracker, 8.0, 6.0, _song(lambda k: True, rise=0.08, base=0.55))
    later = {k: v for k, v in soft.items() if k >= 18}
    strengths = [strength for strength, _ in later.values() if strength >= FLASH_MIN]
    assert strengths and max(strengths) < 0.8
    assert flash_level(max(strengths)) < 1.0
    assert len(_changed(later)) < 0.6 * len(later)


def test_more_hits_per_change_hold_the_colour_longer():
    one = _play(HitTracker(), 0.0, 10.0, _song(lambda k: True), hits=1.0)
    four = _play(HitTracker(), 0.0, 10.0, _song(lambda k: True), hits=4.0)
    assert len(_changed(four)) * 3 < len(_changed(one))
    assert len(_changed(four)) >= 3


def test_a_synthetic_song_through_the_real_fft():
    """Kick over a bass line and a chord pad: four on the floor, a half-time
    kick, a breakdown (bass line and pad only) and the drop."""
    sr, beat = 48000, 60.0 / 124
    sections = [(16, (0, 1, 2, 3)), (16, (0, 2)), (16, ()), (16, (0, 1, 2, 3))]
    total = sum(n for n, _ in sections)
    t = np.arange(int(sr * total * beat)) / sr
    audio = 0.2 * np.sin(2 * np.pi * 55 * t)
    for f in (261.6, 329.6, 392.0):
        audio += 0.12 * np.sin(2 * np.pi * f * t)
    b = 0
    for n, kicks in sections:
        for _ in range(n):
            if b % 4 in kicks:
                k = (t >= b * beat) & (t < b * beat + 0.25)
                tk = t[k] - b * beat
                audio[k] += 0.9 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 12)
            b += 1

    fft, tracker = FFTEngine(sr, 2048), HitTracker()
    flashes, b = set(), 0
    for i in range(int(len(t) / sr / DT)):
        now = (i + 1) * DT
        end = int(now * sr)
        if end < 2048:
            continue
        tracker.update(band_energy(fft.compute(audio[end - 2048:end].astype(np.float32)), 40.0, 399.0), DT)
        is_beat = b < total and now >= b * beat - 0.06  # the clock's lead
        if tracker.flash_strength(is_beat) >= FLASH_MIN:
            flashes.add(b if is_beat else b - 1)
        b += is_beat
    assert set(range(2, 16)) <= flashes
    assert {k for k in range(20, 32) if k in flashes} == {k for k in range(20, 32) if k % 2 == 0}
    assert len({k for k in range(32, 48) if k in flashes}) <= 1
    assert set(range(48, 64)) <= flashes


def test_old_settings_files_load():
    bs = BeatSyncModeConfig.from_dict({"hue_every_n_beats": 4, "hue_change_dynamic": False})
    assert bs.hue_change_hits == 1.0 and not hasattr(bs, "hue_every_n_beats")
    bsw = BeatSyncWhiteModeConfig.from_dict({"temp_every_n_beats": 4})
    assert bsw.temp_change_hits == 1.0 and not hasattr(bsw, "temp_every_n_beats")
    config = AppConfig()
    config.color_mapping.beat_sync.hue_change_hits = 2.5
    config.color_mapping.beat_sync_white.temp_change_hits = 4.0
    again = AppConfig.from_dict(config.to_dict()).color_mapping
    assert again.beat_sync.hue_change_hits == 2.5 and again.beat_sync_white.temp_change_hits == 4.0
    assert BeatSyncWhiteModeConfig.from_dict({"temp_change_hits": 0}).temp_change_hits == 0.1


# -- the engine ------------------------------------------------------------------------------------


def _pattern_engine(monkeypatch, mode="beat_sync", **beat_sync):
    def configure(c):
        c.color_mapping.mode = mode
        bs = c.color_mapping.beat_sync
        bs.hue_mode = "step"
        bs.flash_brightness = 1.0
        bs.sustain_brightness = 0.2
        for key, value in beat_sync.items():
            setattr(bs, key, value)

    engine, clock, lamps = _make_engine(monkeypatch, configure)
    kicks = {"on": lambda k: True}

    def energy(frame, lo, hi):
        beats = (clock.now - 1000.0) / PERIOD
        k = round(beats)
        return 0.9 if kicks["on"](k) and abs(beats - k) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", energy)
    return engine, clock, kicks


def _per_beat(engine, clock, seconds, value):
    """Per clock beat: (beat count, flashed, value changed)."""
    out = []
    last = value()

    def on_tick():
        nonlocal last
        beat = engine.latest_clock_beat
        hit = engine.latest_band3_levels.get("hit", 0.0) > 0.0
        if beat.is_beat:
            out.append([beat.beat_count, hit, value() != last])
        elif hit and out:
            out[-1][1] = True
        last = value()

    _run(engine, clock, seconds, on_tick)
    return out


def test_engine_flashes_only_on_the_hits_while_the_clock_coasts_through_a_break(monkeypatch):
    engine, clock, kicks = _pattern_engine(monkeypatch)
    _run(engine, clock, 6.0, lambda: None)  # the clock locks
    pounding = _per_beat(engine, clock, 4.0, lambda: engine._beat_target_hue)
    kicks["on"] = lambda k: False
    brk = _per_beat(engine, clock, 6.0, lambda: engine._beat_target_hue)
    values = []
    _run(engine, clock, 1.0, lambda: values.append(engine.latest_band3_levels["value"]))
    kicks["on"] = lambda k: True
    back = _per_beat(engine, clock, 4.0, lambda: engine._beat_target_hue)

    assert all(hit and changed for _, hit, changed in pounding)
    assert len(brk) >= 8  # the clock keeps sending beats (coasting)...
    assert not any(hit or changed for _, hit, changed in brk[2:])  # ...but nothing flashes or changes
    assert max(values) < 0.25  # resting at Sustain brightness
    assert all(hit and changed for _, hit, changed in back[1:])


def test_engine_half_time_kick_flashes_every_second_beat(monkeypatch):
    engine, clock, kicks = _pattern_engine(monkeypatch)
    _run(engine, clock, 6.0, lambda: None)  # the clock locks on every beat...
    kicks["on"] = lambda k: k % 2 == 0  # ...and keeps it when the kick goes half-time
    _run(engine, clock, 3.0, lambda: None)
    beats = _per_beat(engine, clock, 6.0, lambda: engine._beat_target_hue)
    flashed = [hit for _, hit, _ in beats]
    assert len(beats) >= 10
    assert flashed in (([True, False] * 8)[: len(beats)], ([False, True] * 8)[: len(beats)])
    assert [changed for _, _, changed in beats] == flashed
    assert engine.hits.period == 2


def test_a_dark_pulse_with_no_hit_under_it_does_not_flash(monkeypatch):
    engine, clock, kicks = _pattern_engine(monkeypatch)
    _run(engine, clock, 6.0, lambda: None)
    kicks["on"] = lambda k: False
    _run(engine, clock, 3.0, lambda: None)
    engine._beat_dark_until = clock.now + 0.1  # (the sequencer's, say)
    values = []
    _run(engine, clock, 1.0, lambda: values.append(engine.latest_band3_levels["value"]))
    assert max(values) < 0.25


def test_glide_waits_at_the_end_while_no_change_comes(monkeypatch):
    engine, clock, kicks = _pattern_engine(
        monkeypatch, hue_step_deg=120.0, glide_hue=True, hue_glide_deg=90.0, hue_glide_timing="beat",
        hue_attack_ms=5.0,
    )
    _run(engine, clock, 8.0, lambda: None)
    kicks["on"] = lambda k: False
    _run(engine, clock, 1.5, lambda: None)  # the last hit's glide has arrived
    base = engine._beat_target_hue
    hues = []
    _run(engine, clock, 3.0, lambda: hues.append(engine.latest_band3_levels["hue"]))
    assert engine._beat_target_hue == base  # no change in the break
    travelled = [(h - base) % 360.0 for h in hues]
    assert all(abs(x - 90.0) < 1.0 for x in travelled), travelled  # held at the glide's end, no jumping back


def test_beat_sync_white_holds_its_temperature_and_brightness_through_a_break(monkeypatch):
    engine, clock, kicks = _pattern_engine(monkeypatch, mode="beat_sync_white")
    bsw = engine.config.color_mapping.beat_sync_white
    bsw.sustain_brightness, bsw.temp_change_hits = 0.2, 2.0
    _run(engine, clock, 6.0, lambda: None)
    pounding = _per_beat(engine, clock, 4.0, lambda: engine._beat_white_target_temp)
    kicks["on"] = lambda k: False
    brk = _per_beat(engine, clock, 6.0, lambda: engine._beat_white_target_temp)
    assert all(hit for _, hit, _ in pounding)
    assert 0.35 * len(pounding) <= sum(changed for _, _, changed in pounding) <= 0.65 * len(pounding)
    assert not any(hit or changed for _, hit, changed in brk[2:])
