"""Beat Sync's Follow the hits (dsp/hue_drive.py): the hue changes when the
music hits - every hard hit in a pounding stretch, less often on softer
hits, not at all in a quiet passage - instead of on a fixed count of beats.

Pure tracker tests with made-up beat band levels, one with a synthetic song
through the real FFT, and the engine on the shared clock with a fake kick
envelope (see test_rhythm_engine).
"""
import numpy as np

import airam_lights.engine.visualization_engine as ve_module
from airam_lights.config.schema import BeatSyncModeConfig
from airam_lights.dsp.bands import band_energy
from airam_lights.dsp.fft_engine import FFTEngine
from airam_lights.dsp.hue_drive import HueDrive

from test_rhythm_engine import PERIOD, TICK, _make_engine, _run

DT = 1.0 / 30.0
BEAT = 0.5


def _kick_level(t, rise, base=0.6):
    """The beat band's level: `base`, and a hit `rise` above it on every beat, fading in ~0.1 s."""
    since = t % BEAT
    return base + rise * np.exp(-since / 0.05)


def _changes(drive, seconds, level_at, hits=1.0, every_n=1, start=0.0, lead=0.05):
    """Ticks `seconds` of level_at(t), a beat `lead` s before each hit; one bool per beat."""
    out, beats = [], 0
    for i in range(int(seconds / DT)):
        t = start + (i + 1) * DT
        drive.update(level_at(t), DT)
        if t >= start + beats * BEAT + BEAT - lead:
            out.append(drive.beat(hits, beats % every_n == 0))
            beats += 1
    return out


def test_a_pounding_bass_changes_the_hue_on_every_beat():
    drive = HueDrive()
    changes = _changes(drive, 10.0, lambda t: _kick_level(t, 0.15))
    assert sum(changes[2:]) >= len(changes[2:]) - 1
    assert drive.typical_gap == 1


def test_a_held_note_or_pad_never_changes_it():
    drive = HueDrive()
    _changes(drive, 6.0, lambda t: _kick_level(t, 0.15))
    # The kick stops; a pad wobbles a dB or two (the clock keeps sending beats).
    pad = _changes(drive, 10.0, lambda t: 0.6 + 0.01 * np.sin(t * 7.0), start=6.0)
    assert sum(pad[2:]) == 0


def test_silence_never_changes_it():
    drive = HueDrive()
    assert sum(_changes(drive, 6.0, lambda t: 0.02 + 0.06 * ((t % BEAT) < 0.05))) == 0


def test_softer_hits_change_it_less_often_and_it_comes_back_with_the_drop():
    drive = HueDrive()
    loud = _changes(drive, 8.0, lambda t: _kick_level(t, 0.15))
    # (The first beat after a section still sees the last hit before it.)
    softer = _changes(drive, 6.0, lambda t: _kick_level(t, 0.1), start=8.0)[1:]
    soft_and_quiet = _changes(drive, 6.0, lambda t: _kick_level(t, 0.07, base=0.55), start=14.0)[1:]
    drop = _changes(drive, 4.0, lambda t: _kick_level(t, 0.15), start=20.0)
    assert sum(loud[2:]) > 0.9 * len(loud[2:])
    assert 0.25 * len(softer) <= sum(softer) <= 0.75 * len(softer)
    assert sum(soft_and_quiet) <= 1
    assert all(drop[1:])  # straight back to every beat


def test_change_hue_still_decides_which_beats_may_change():
    drive = HueDrive()
    changes = _changes(drive, 10.0, lambda t: _kick_level(t, 0.15), every_n=4)
    assert all(i % 4 == 0 for i, c in enumerate(changes) if c)
    assert sum(changes) >= len(changes) // 4 - 1
    assert drive.typical_gap == 4


def test_more_hits_per_change_hold_the_colour_longer():
    one = _changes(HueDrive(), 10.0, lambda t: _kick_level(t, 0.15), hits=1.0)
    three = _changes(HueDrive(), 10.0, lambda t: _kick_level(t, 0.15), hits=3.0)
    assert sum(three) * 2.5 < sum(one)
    assert sum(three) >= 4


def test_a_synthetic_song_through_the_real_fft():
    """Kick over a bass line and a chord pad, then a breakdown (bass line and
    pad only), then the kick back: the hue follows the kicks and holds
    still in the breakdown, though the beats keep coming."""
    sr, beat = 48000, 60.0 / 124
    sections = [(16, 0.9), (16, 0.0), (16, 0.9)]
    total = sum(n for n, _ in sections)
    t = np.arange(int(sr * total * beat)) / sr
    audio = 0.2 * np.sin(2 * np.pi * 55 * t)
    for f in (261.6, 329.6, 392.0):
        audio += 0.12 * np.sin(2 * np.pi * f * t)
    kicked, b = [], 0
    for n, amp in sections:
        for _ in range(n):
            kicked.append(amp > 0)
            k = (t >= b * beat) & (t < b * beat + 0.25)
            tk = t[k] - b * beat
            audio[k] += amp * np.sin(2 * np.pi * (50 + 80 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 12)
            b += 1

    fft, drive = FFTEngine(sr, 2048), HueDrive()
    changes, b = [], 0
    for i in range(int(len(t) / sr / DT)):
        now = (i + 1) * DT
        end = int(now * sr)
        if end < 2048:
            continue
        drive.update(band_energy(fft.compute(audio[end - 2048:end].astype(np.float32)), 40.0, 399.0), DT)
        if b < total and now >= b * beat - 0.06:  # the clock's lead
            changes.append(drive.beat(1.0))
            b += 1
    first, breakdown, drop = changes[2:16], changes[18:32], changes[33:48]
    assert sum(first) >= len(first) - 1
    assert sum(breakdown) == 0
    assert sum(drop) >= len(drop) - 1


def test_old_configs_follow_the_hits():
    cfg = BeatSyncModeConfig.from_dict({"hue_every_n_beats": 4})
    assert cfg.hue_change_dynamic and cfg.hue_change_hits == 1.0
    back = BeatSyncModeConfig.from_dict({**cfg.to_dict(), "hue_change_dynamic": False, "hue_change_hits": 2.5})
    assert not back.hue_change_dynamic and back.hue_change_hits == 2.5


# -- the engine ------------------------------------------------------------------------------------


def _hue_changes_per_beat(engine, clock, seconds):
    out = []
    last = engine._beat_target_hue

    def on_tick():
        nonlocal last
        if engine.latest_clock_beat.is_beat:
            out.append(engine._beat_target_hue != last)
        last = engine._beat_target_hue

    _run(engine, clock, seconds, on_tick)
    return out


def _kick_stops_engine(monkeypatch, dynamic):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.hue_mode = "step"
        bs.hue_every_n_beats = 1
        bs.hue_change_dynamic = dynamic

    engine, clock, _ = _make_engine(monkeypatch, configure)
    kicking = {"on": True}

    def energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if kicking["on"] and min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", energy)
    return engine, clock, kicking


def test_engine_holds_the_hue_while_the_clock_coasts_through_a_break(monkeypatch):
    engine, clock, kicking = _kick_stops_engine(monkeypatch, dynamic=True)
    _run(engine, clock, 6.0, lambda: None)  # the clock locks
    pounding = _hue_changes_per_beat(engine, clock, 4.0)
    kicking["on"] = False
    brk = _hue_changes_per_beat(engine, clock, 6.0)
    kicking["on"] = True
    back = _hue_changes_per_beat(engine, clock, 4.0)
    assert len(brk) >= 8  # the clock keeps sending beats (coasting)...
    assert sum(brk[2:]) == 0  # ...but the colour holds
    assert sum(pounding) >= len(pounding) - 1
    assert sum(back[2:]) >= len(back[2:]) - 1
    assert engine.hue_drive.typical_gap == 1


def test_engine_without_follow_the_hits_changes_on_every_beat(monkeypatch):
    engine, clock, kicking = _kick_stops_engine(monkeypatch, dynamic=False)
    _run(engine, clock, 6.0, lambda: None)
    kicking["on"] = False
    brk = _hue_changes_per_beat(engine, clock, 6.0)
    assert len(brk) >= 8 and all(brk)


def test_glide_waits_at_the_end_while_no_change_comes(monkeypatch):
    def configure(c):
        bs = c.color_mapping.beat_sync
        bs.hue_mode = "step"
        bs.hue_step_deg = 120.0
        bs.hue_every_n_beats = 1
        bs.glide_hue = True
        bs.hue_glide_deg = 90.0
        bs.hue_glide_timing = "beat"
        bs.hue_attack_ms = 5.0

    engine, clock, _ = _make_engine(monkeypatch, configure)
    kicking = {"on": True}

    def energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if kicking["on"] and min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", energy)
    _run(engine, clock, 8.0, lambda: None)
    kicking["on"] = False
    _run(engine, clock, 1.5, lambda: None)  # the last hit's change has come and its glide arrived
    base = engine._beat_target_hue
    hues = []
    _run(engine, clock, 3.0, lambda: hues.append(engine.latest_band3_levels["hue"]))
    assert engine._beat_target_hue == base  # no change in the break
    travelled = [(h - base) % 360.0 for h in hues]
    assert all(abs(x - 90.0) < 1.0 for x in travelled), travelled  # held at the glide's end, no jumping back
