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
