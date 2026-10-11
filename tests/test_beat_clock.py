"""BeatClock: tempo lock, off-beat rejection, fill-in, lead and bar counting,
driven by synthetic energy envelopes at a fixed tick rate."""
import pytest

from airam_lights.dsp.beat_clock import BeatClock, ClockBeat, beat_divides

TICK = 1.0 / 30.0  # the engine's default visual_update_hz


def _envelope(duration_s, period_s=0.5, hat_offset=None, skip_beats=(), kick=1.0, hat=0.6, accent_every=None):
    """Yields (t, energy): a kick every `period_s` (one tick wide), optional
    off-beat hits `hat_offset` seconds after each kick, low noise elsewhere."""
    t = 0.0
    n = 0
    while t < duration_s:
        beat_index = int(round(t / period_s))
        beat_t = beat_index * period_s
        energy = 0.05
        if abs(t - beat_t) < TICK / 2 and beat_index not in skip_beats:
            energy = kick * (1.6 if accent_every and beat_index % accent_every == 0 else 1.0)
        elif hat_offset is not None:
            hat_t = beat_t + hat_offset
            if abs(t - hat_t) < TICK / 2:
                energy = hat
        yield t, energy
        n += 1
        t = n * TICK


def _run(clock, envelope):
    return [(t, clock.update(e, t)) for t, e in envelope]


def _beats(results, after=0.0):
    return [(t, b) for t, b in results if b.is_beat and t >= after]


def test_locks_to_tempo():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1)
    results = _run(clock, _envelope(10.0, period_s=0.5))
    assert clock.locked
    assert clock.period_s == pytest.approx(0.5, rel=0.04)
    times = [t for t, _ in _beats(results, after=5.0)]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(abs(g - 0.5) < 0.07 for g in gaps), gaps


def test_ignores_off_beat_hits_once_locked():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1)
    # Loud hi-hat exactly halfway between kicks.
    results = _run(clock, _envelope(12.0, period_s=0.5, hat_offset=0.25, hat=0.9))
    beats = _beats(results, after=6.0)
    # One beat per kick, none on the hats.
    assert 10 <= len(beats) <= 13
    for t, _ in beats:
        phase = (t / 0.5) % 1.0
        assert min(phase, 1.0 - phase) < 0.2, t


def test_fills_in_a_missing_kick():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1)
    results = _run(clock, _envelope(10.0, period_s=0.5, skip_beats={16}))  # kick at t=8.0 missing
    around = [(t, b) for t, b in _beats(results) if 7.6 < t < 8.4]
    assert len(around) == 1
    assert around[0][1].predicted


def test_lead_emits_before_the_beat():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, lead_ms=100)
    results = _run(clock, _envelope(10.0, period_s=0.5))
    early = [t for t, b in _beats(results, after=6.0)]
    offsets = [((t + 0.25) % 0.5) - 0.25 for t in early]  # signed distance to the nearest kick
    assert all(-0.15 < o < -0.04 for o in offsets), offsets


def test_bar_counting_and_divisions():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, beats_per_bar=4)
    # Every 4th kick (beat_index % 4 == 0) is harder: that should become the downbeat.
    results = _run(clock, _envelope(16.0, period_s=0.5, accent_every=4))
    beats = _beats(results, after=8.0)
    positions = [b.bar_position for _, b in beats]
    for a, b in zip(positions, positions[1:]):
        assert b == (a + 1) % 4
    for t, b in beats:
        if b.is_downbeat:
            assert round(t / 0.5) % 4 == 0, t
    assert sum(beat_divides(b, 4) for _, b in beats) == sum(b.is_downbeat for _, b in beats)
    assert any(b.is_accent for _, b in beats)
    assert all(b.is_downbeat for _, b in beats if b.is_accent)


def test_unlocked_reacts_to_every_onset():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, tempo_lock=False)
    results = _run(clock, _envelope(4.0, period_s=0.5, hat_offset=0.25, hat=0.9))
    # Kicks and hats both count when there's no grid to reject off-beats against.
    assert len(_beats(results, after=1.5)) >= 8
    assert not clock.locked


def test_silence_emits_nothing():
    clock = BeatClock()
    results = _run(clock, ((i * TICK, 0.0) for i in range(300)))
    assert not _beats(results)


def test_beat_divides_requires_a_beat():
    assert not beat_divides(ClockBeat(is_beat=False, aligned_index=0), 1)
    assert beat_divides(ClockBeat(is_beat=True, aligned_index=8), 4)
    assert not beat_divides(ClockBeat(is_beat=True, aligned_index=6), 4)


def _with_breakdown(kicks_s, breakdown_s, period_s=0.5, level=0.3):
    """Kicks for `kicks_s`, then music with no beat at all (a steady level)."""
    yield from _envelope(kicks_s, period_s=period_s)
    n = 0
    while n * TICK < breakdown_s:
        yield kicks_s + n * TICK, level
        n += 1


def test_keeps_the_beat_going_through_a_breakdown():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1)
    results = _run(clock, _with_breakdown(10.0, 10.0))
    late = _beats(results, after=16.0)
    assert len(late) >= 7  # still a beat every ~0.5 s near the end of the breakdown
    assert all(b.predicted for _, b in late)
    assert clock.locked and clock.coasting
    gaps = [b[0] - a[0] for a, b in zip(late, late[1:])]
    assert all(g == pytest.approx(0.5, abs=0.05) for g in gaps)


def test_coasting_stops_after_coast_bars():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, coast_bars=2)
    results = _run(clock, _with_breakdown(10.0, 12.0))
    assert not _beats(results, after=18.0)
    assert not clock.locked


def test_coast_bars_zero_stops_when_the_beat_is_lost():
    coasting = _beats(_run(BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1), _with_breakdown(10.0, 10.0)))
    stopping = _beats(
        _run(BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, coast_bars=0), _with_breakdown(10.0, 10.0))
    )
    assert stopping[-1][0] < coasting[-1][0] - 3.0


def test_silence_after_music_stops_at_once_even_when_coasting():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1)
    results = _run(clock, _with_breakdown(10.0, 8.0, level=0.0))
    assert not _beats(results, after=12.0)


def test_keeps_beating_when_kicks_stay_under_the_sensitivity():
    """Loud, compressed music: the kicks rise only ~10 % above the average,
    under a 1.2 sensitivity, so not one of them is detected as an onset.
    The tempo still shows in the envelope - the clock must keep beating
    on it instead of declaring the beat lost (seen as lamps frozen for
    tens of seconds)."""
    def compressed(duration_s, period_s=0.5):
        n = 0
        while n * TICK < duration_s:
            t = n * TICK
            phase = (t % period_s) / period_s
            yield t, 0.72 + 0.08 * max(0.0, 1.0 - phase * 6.0)  # short decaying bump on each beat
            n += 1

    clock = BeatClock(sensitivity=1.2, min_interval_ms=150, min_energy=0.2, lead_ms=65)
    results = _run(clock, compressed(30.0))
    assert clock.locked
    assert len(_beats(results, after=10.0)) == pytest.approx(40, abs=2)


# -- fallback tiers: music without a kick -----------------------------------------------


def _notes(duration_s, note_times, energy=0.3, loudness=0.5, start=0.0):
    """Yields (t, energy, flux, loudness): a steady, kick-less bass level and
    a spectral-flux spike (one tick wide) at each of `note_times`."""
    notes = sorted(note_times)
    i = 0
    n = 0
    t = start
    while t < start + duration_s:
        flux = 0.05 + 0.01 * ((n * 7) % 5)  # a little jitter between notes
        while i < len(notes) and notes[i] < t - TICK / 2:
            i += 1
        if i < len(notes) and abs(notes[i] - t) < TICK / 2:
            flux = 1.0
        yield t, energy, flux, loudness
        n += 1
        t = start + n * TICK


def _run_notes(clock, stream):
    return [(t, clock.update(e, t, flux=f, loudness=l)) for t, e, f, l in stream]


def _rubato_times(duration_s, start_period, end_period):
    """Note times whose spacing glides from start_period to end_period."""
    times, t = [], 0.3
    while t < duration_s:
        times.append(t)
        t += start_period + (end_period - start_period) * (t / duration_s)
    return times


def test_follows_the_notes_when_there_is_no_kick():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
    period = 0.7
    times = [0.3 + k * period for k in range(40)]
    results = _run_notes(clock, _notes(25.0, times))
    assert clock.source == "flow"
    assert clock.period_s == pytest.approx(period, rel=0.06)
    beat_times = [t for t, _ in _beats(results, after=15.0)]
    gaps = [b - a for a, b in zip(beat_times, beat_times[1:])]
    assert gaps and all(abs(g - period) < 0.12 for g in gaps), gaps
    assert clock.position(25.0) is not None  # the pulse sequencer has a grid to place pulses on


def test_flow_bends_with_rubato():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
    times = _rubato_times(40.0, 0.6, 0.85)
    _run_notes(clock, _notes(40.0, times))
    assert clock.source == "flow"
    assert clock.period_s == pytest.approx(0.85, rel=0.1)


def test_free_pace_without_a_steady_tempo():
    import random

    rng = random.Random(3)
    times, t = [], 0.2
    while t < 30.0:
        times.append(t)
        t += rng.uniform(0.15, 1.1)  # notes with no steady spacing
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
    results = _run_notes(clock, _notes(30.0, times))
    sources = {b.source for t, b in results if t > 10.0}
    assert "free" in sources
    beats = _beats(results, after=10.0)
    assert len(beats) >= 15  # keeps moving
    gaps = [b - a for (a, _), (b, _) in zip(beats, beats[1:])]
    assert min(gaps) > 0.2  # never jittery


def test_free_pace_follows_note_density():
    def settled_period(spacing):
        clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
        clock._estimate_period = lambda env, **kw: None  # no periodicity anywhere: always the free tier
        times = [0.2 + k * spacing for k in range(int(20.0 / spacing))]
        _run_notes(clock, _notes(20.0, times))
        assert clock.source == "free"
        return clock.period_s

    assert settled_period(0.2) < settled_period(0.45)


def test_free_pace_speeds_up_when_the_music_swells():
    def settled_period(swell):
        clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
        clock._estimate_period = lambda env, **kw: None
        times = [0.2 + k * 0.3 for k in range(60)]
        stream = [
            (t, e, f, l + (swell * (t - 10.0) / 8.0 if t > 10.0 else 0.0))
            for t, e, f, l in _notes(18.0, times, loudness=0.4)
        ]
        _run_notes(clock, stream)
        return clock.period_s

    assert settled_period(0.15) < settled_period(0.0) < settled_period(-0.15)


def test_a_clear_beat_takes_over_from_the_notes():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
    times = [0.3 + k * 0.7 for k in range(30)]
    _run_notes(clock, _notes(20.0, times))
    assert clock.source == "flow"
    # Kicks come in at 120 BPM.
    for t, e in _envelope(12.0, period_s=0.5):
        clock.update(e, 20.0 + t, flux=0.05, loudness=0.5)
    assert clock.source == "beat"
    assert clock.period_s == pytest.approx(0.5, rel=0.04)


def test_beat_lost_coasts_then_follows_the_notes():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, coast_bars=2, fallback=True)
    for t, e in _envelope(10.0, period_s=0.5):
        clock.update(e, t, flux=0.05, loudness=0.5)
    assert clock.source == "beat"
    times = [10.3 + k * 0.7 for k in range(40)]
    results = _run_notes(clock, _notes(25.0, times, start=10.0))
    assert any(b.coasting for t, b in results if t < 16.0)  # the old tempo first
    assert clock.source == "flow"
    assert clock.period_s == pytest.approx(0.7, rel=0.06)


def test_fallback_off_keeps_the_old_behaviour():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=False)
    times = [0.3 + k * 0.7 for k in range(30)]
    results = _run_notes(clock, _notes(20.0, times))
    assert not clock.locked
    assert not _beats(results)  # steady bass, no kicks: nothing to react to


def test_silence_stops_the_fallback_too():
    clock = BeatClock(sensitivity=1.4, min_interval_ms=100, min_energy=0.1, fallback=True)
    times = [0.3 + k * 0.7 for k in range(30)]
    _run_notes(clock, _notes(20.0, times))
    assert clock.locked
    results = _run_notes(clock, _notes(4.0, [], energy=0.0, loudness=0.0, start=20.0))
    assert not clock.locked
    assert not _beats(results, after=21.0)
