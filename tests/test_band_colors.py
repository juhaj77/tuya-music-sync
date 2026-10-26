"""Band Colors mode (effects/band_colors.py): bass, mids and treble each with
a colour of their own, the lamps showing the band playing out the most -
each band measured against its own recent range, so the colour moves with
the kicks, chords and hi-hats instead of sitting on one pale mix. And the
modes it replaced: a config saved in RGB Frequency or Custom loads into it.

Pure tracker tests with made-up band levels, one with a synthetic song
through the real FFT, and the engine with a fake spectrum (see test_strobe).
"""
import math

import numpy as np
import pytest

from airam_lights.config.schema import AppConfig, BandColorsModeConfig, ColorMappingConfig
from airam_lights.dsp.bands import band_energy
from airam_lights.dsp.fft_engine import FFTEngine
from airam_lights.effects.band_colors import BandColorTracker, band_edges, focus_power

DT = 0.02


def _hue_distance(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _run(tracker, cfg, levels_at, seconds, chord=None):
    """Feeds levels_at(t) -> (bass, mid, treble) for `seconds`; returns the states."""
    states = []
    for i in range(int(seconds / DT)):
        states.append(tracker.update(levels_at(i * DT), cfg, DT, chord))
    return states


def _hits(t, period, length=0.08):
    return (t % period) < length


# -- the tracker -----------------------------------------------------------------------------------


def test_each_band_takes_its_own_colour_at_its_hits():
    cfg = BandColorsModeConfig(chord_turn_deg=0.0, focus=1.0)
    tracker = BandColorTracker()
    beat = 0.5

    def song(t):
        kick = _hits(t, beat)
        hat = _hits(t - beat / 2, beat)
        # Everything stays loud the whole time - only the hits stand out.
        return (0.75 if kick else 0.6, 0.6, 0.7 if hat else 0.55)

    states = _run(tracker, cfg, song, 6.0)
    at_kicks = [s.hue for i, s in enumerate(states) if i * DT > 2.0 and 0.03 < (i * DT) % beat < 0.1]
    at_hats = [s.hue for i, s in enumerate(states) if i * DT > 2.0 and 0.28 < (i * DT) % beat < 0.35]
    assert at_kicks and all(_hue_distance(h, cfg.bass_hue_deg) < 25 for h in at_kicks)
    assert at_hats and all(_hue_distance(h, cfg.treble_hue_deg) < 25 for h in at_hats)


def test_two_bands_at_once_give_the_hue_between_theirs():
    cfg = BandColorsModeConfig(chord_turn_deg=0.0, bass_hue_deg=0.0, treble_hue_deg=240.0, focus=0.5)
    tracker = BandColorTracker()
    states = _run(tracker, cfg, lambda t: (0.8, 0.3, 0.8) if _hits(t, 0.5, 0.2) else (0.4, 0.3, 0.4), 4.0)
    hit = [s.hue for i, s in enumerate(states) if i * DT > 2.0 and 0.1 < (i * DT) % 0.5 < 0.2]
    assert hit and all(_hue_distance(h, 300.0) < 15 for h in hit)  # magenta, not a pale mix


def test_focus_decides_between_a_blend_and_the_strongest_band():
    assert focus_power(0.0) == 1.0 and focus_power(1.0) == 6.0

    def hue_at_hit(focus):
        cfg = BandColorsModeConfig(chord_turn_deg=0.0, focus=focus)
        tracker = BandColorTracker()
        # The bass hits hard, the mids a little at the same time.
        states = _run(tracker, cfg, lambda t: (0.8, 0.55, 0.3) if _hits(t, 0.5, 0.2) else (0.4, 0.45, 0.3), 4.0)
        return [s.hue for i, s in enumerate(states) if i * DT > 2.0 and 0.12 < (i * DT) % 0.5 < 0.2][-1]

    assert _hue_distance(hue_at_hit(1.0), 0.0) < _hue_distance(hue_at_hit(0.0), 0.0)
    assert _hue_distance(hue_at_hit(0.0), 0.0) > 15  # blended toward the mid colour


def test_silence_fades_to_brightness_min_and_a_silent_bands_flicker_doesnt_count():
    cfg = BandColorsModeConfig(chord_turn_deg=0.0, focus=1.0)
    tracker = BandColorTracker()
    _run(tracker, cfg, lambda t: (0.7 if _hits(t, 0.5) else 0.5, 0.5, 0.5), 3.0)
    states = _run(tracker, cfg, lambda t: (0.0, 0.0, 0.0), 2.0)
    assert states[-1].level == 0.0
    # Music in the bass only; the treble just flickers on the noise floor.
    tracker = BandColorTracker()
    states = _run(tracker, cfg, lambda t: (0.7 if _hits(t, 0.5) else 0.5, 0.0, 0.02 + 0.01 * math.sin(t * 40)), 4.0)
    assert all(_hue_distance(s.hue, cfg.bass_hue_deg) < 30 for s in states[100:])


def test_the_chord_turn_moves_the_whole_palette_and_glides():
    cfg = BandColorsModeConfig(chord_turn_deg=90.0, focus=1.0)
    tracker = BandColorTracker()
    kick = lambda t: (0.75 if _hits(t, 0.5, 0.25) else 0.5, 0.3, 0.3)  # noqa: E731
    _run(tracker, cfg, kick, 2.0, chord=0.0)
    first = tracker.update((0.75, 0.3, 0.3), cfg, DT, 1.0)
    assert first.turn < 10.0  # glides over, doesn't jump
    states = _run(tracker, cfg, kick, 3.0, chord=1.0)
    assert states[-1].turn == pytest.approx(90.0, abs=1.0)
    at_kicks = [s.hue for i, s in enumerate(states) if i * DT > 2.0 and 0.1 < (i * DT) % 0.5 < 0.25]
    assert at_kicks and all(_hue_distance(h, cfg.bass_hue_deg + 90.0) < 25 for h in at_kicks)
    assert tracker.update((0.75, 0.3, 0.3), BandColorsModeConfig(chord_turn_deg=0.0), DT, None).turn < 90.0


def test_band_edges_follow_the_splits_and_stay_in_order():
    assert band_edges(250.0, 4000.0) == [(30.0, 250.0), (250.0, 4000.0), (4000.0, 12000.0)]
    low, mid, high = band_edges(5000.0, 1000.0)  # crossed over: kept in order
    assert low[1] <= mid[0] < mid[1] <= high[0] < high[1]


def test_a_synthetic_song_moves_through_the_colour_wheel():
    """Kick, hi-hats and a chord pad - all loud - through the real FFT: the
    colour goes round the wheel within each beat (red on the kick), where
    the bands' raw levels alone would sit on the bass colour."""
    sr, beat = 48000, 60.0 / 124
    t = np.arange(int(sr * 8 * beat)) / sr
    rng = np.random.default_rng(1)
    audio = 0.2 * np.sin(2 * np.pi * 55 * t)  # a bass line under everything
    for b in range(8):
        k = (t >= b * beat) & (t < b * beat + 0.25)
        tk = t[k] - b * beat
        audio[k] += 0.9 * np.sin(2 * np.pi * (50 + 80 * np.exp(-tk * 30)) * tk) * np.exp(-tk * 12)
        h = (t >= b * beat + beat / 2) & (t < b * beat + beat / 2 + 0.06)
        audio[h] += 0.25 * np.diff(np.concatenate([[0.0], rng.standard_normal(h.sum())])) * np.exp(
            -(t[h] - b * beat - beat / 2) * 60)
    for f in (261.6, 329.6, 392.0):
        audio += 0.12 * np.sin(2 * np.pi * f * t)

    fft = FFTEngine(sr, 2048)
    cfg = BandColorsModeConfig(chord_turn_deg=0.0)
    edges = band_edges(cfg.bass_mid_hz, cfg.mid_treble_hz)
    tracker = BandColorTracker()
    hues, raw_bass_wins = [], 0
    for i in range(int(len(t) / sr / DT)):
        end = int((i + 1) * DT * sr)
        if end < 2048:
            continue
        frame = fft.compute(audio[end - 2048:end].astype(np.float32))
        levels = [band_energy(frame, lo, hi) for lo, hi in edges]
        raw_bass_wins += levels[0] == max(levels)
        hues.append(tracker.update(levels, cfg, DT).hue)
    hues = hues[50:]
    assert raw_bass_wins > 0.9 * len(hues)  # the raw levels: the bass all the time
    sectors = {int(h // 60) for h in hues}
    assert len(sectors) >= 4  # ... the colour: round the wheel


# -- config ----------------------------------------------------------------------------------------


def test_old_rgb_frequency_and_custom_configs_load_into_band_colors():
    for old in ("rgb_freq", "custom"):
        cm = ColorMappingConfig.from_dict({"mode": old, "rgb": {"sensitivity": 2.0}, "custom": {}})
        assert cm.mode == "band_colors"
        assert "rgb" not in cm.to_dict() and "custom" not in cm.to_dict()
    assert ColorMappingConfig.from_dict({"mode": "8band_spectrum"}).mode == "beat_sync"
    cfg = BandColorsModeConfig(focus=0.2, chord_turn_deg=33.0, bass_hue_deg=300.0)
    assert BandColorsModeConfig.from_dict(cfg.to_dict()) == cfg
    assert AppConfig.from_dict(AppConfig().to_dict()).color_mapping.band_colors == BandColorsModeConfig()


# -- the engine ------------------------------------------------------------------------------------


def test_engine_band_colors_mode(monkeypatch):
    import airam_lights.engine.visualization_engine as ve_module
    from test_strobe import TICK, _make_engine

    engine, clock, lamps = _make_engine(monkeypatch, strobe_enabled=False)
    engine.config.sequencer.enabled = False
    cfg = engine.config.color_mapping
    cfg.mode = "band_colors"
    cfg.band_colors.chord_turn_deg = 0.0
    cfg.band_colors.focus = 1.0

    def fake_band(frame, lo, hi):
        phase = (clock.now % 0.5) / 0.5
        if hi <= 300:  # the bass band: a kick at the start of each beat
            return 0.75 if phase < 0.2 else 0.5
        if lo >= 3000:  # the treble: a hat halfway
            return 0.7 if 0.5 <= phase < 0.7 else 0.5
        return 0.5

    monkeypatch.setattr(ve_module, "band_energy", fake_band)
    hues = []
    for _ in range(int(4.0 / TICK)):
        engine.tick_visual()
        color = engine.latest_lamp_colors["a"]
        assert set(engine.latest_lamp_colors) == {"a", "b", "c", "d"}
        hues.append(color.to_hsv()[0])
        clock.now += TICK
    assert any(_hue_distance(h, 0.0) < 25 for h in hues[50:])
    assert any(_hue_distance(h, 240.0) < 25 for h in hues[50:])
    assert set(engine.latest_band3_levels) == {"bass", "mid", "treble"}
    assert engine.latest_band_colors.activities == engine.latest_band3_levels
