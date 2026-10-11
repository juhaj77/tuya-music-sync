"""PitchTracker: dominant note, key and confidence from synthetic harmonic spectra."""
import numpy as np
import pytest

from airam_lights.dsp.pitch import NOTE_NAMES, PitchTracker

FREQS = np.fft.rfftfreq(8192, d=1.0 / 48000)[:1400]  # up to ~8 kHz, like SpectralFeatures' harmonic part
BIN_HZ = FREQS[1]
TICK = 1.0 / 30.0
C_MAJOR = [60, 62, 64, 65, 67, 69, 71, 72]


def _spectrum(midis, level=1.0):
    """A harmonic spectrum: each note's first 8 overtones, decaying."""
    spec = np.zeros(len(FREQS))
    for midi in midis:
        f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        for h in range(1, 9):
            pos = f0 * h / BIN_HZ
            i = int(round(pos))
            if i + 1 >= len(spec):
                break
            spec[i] += level * 0.7 ** (h - 1)
            spec[i - 1] += level * 0.3 * 0.7 ** (h - 1)
            spec[i + 1] += level * 0.3 * 0.7 ** (h - 1)
    return spec


def _play(tracker, midis, seconds, percussive_share=0.1):
    state = None
    for _ in range(int(seconds / TICK)):
        state = tracker.update(_spectrum(midis), FREQS, percussive_share, TICK)
    return state


def _song(tracker, key_offset=0, repeats=3, note_s=0.4):
    """A melody over the key's scale, with the tonic triad underneath."""
    state = None
    for _ in range(repeats):
        for midi in C_MAJOR + C_MAJOR[::-1]:
            triad = [48 + key_offset, 52 + key_offset, 55 + key_offset]
            state = _play(tracker, [midi + key_offset] + triad, note_s)
    return state


def test_finds_the_note():
    tracker = PitchTracker()
    for midi in (57, 64, 69, 76):
        state = _play(tracker, [midi], 1.0)
        assert state.note_name == NOTE_NAMES[midi % 12]


def test_finds_a_low_bass_note_by_its_overtones():
    tracker = PitchTracker()
    for midi in (40, 41, 43):  # E2, F2, G2: fundamentals only ~5 Hz apart, under one bin
        state = _play(tracker, [midi], 1.0)
        assert state.note_name == NOTE_NAMES[midi % 12]


@pytest.mark.parametrize("key_offset", [0, 7, 2])
def test_finds_the_key(key_offset):
    tracker = PitchTracker()
    state = _song(tracker, key_offset)
    assert state.key_name == f"{NOTE_NAMES[key_offset]} major"
    assert state.confidence > 0.7


def test_tension_follows_the_note_s_place_in_the_key():
    tracker = PitchTracker()
    _song(tracker)
    tonic = _play(tracker, [60, 48, 52, 55], 1.5).tension
    fifth = _play(tracker, [67, 48, 52, 55], 1.5).tension
    foreign = _play(tracker, [66], 1.5).tension  # F#: outside C major
    assert tonic < 0.1
    assert tonic < fifth < foreign
    assert foreign > 0.8


def test_noise_has_no_confidence():
    tracker = PitchTracker()
    rng = np.random.default_rng(0)
    state = None
    for _ in range(150):
        state = tracker.update(rng.random(len(FREQS)), FREQS, 0.2, TICK)
    assert state.confidence < 0.05


def test_drums_fade_the_confidence_out():
    tracker = PitchTracker()
    _song(tracker)
    assert tracker.latest.confidence > 0.7
    state = _play(tracker, [60, 64, 67], 4.0, percussive_share=0.9)
    assert state.confidence < 0.1


def test_confidence_glides_instead_of_switching():
    tracker = PitchTracker()
    _song(tracker)
    values = []
    for _ in range(30):
        values.append(tracker.update(None, None, 0.0, TICK).confidence)
    steps = [a - b for a, b in zip(values, values[1:])]
    assert max(steps) < 0.1  # no jump from one tick to the next
    assert values[-1] < values[0] * 0.5


def test_silence_is_not_a_note():
    tracker = PitchTracker()
    state = None
    for _ in range(60):
        state = tracker.update(np.zeros(len(FREQS)), FREQS, 0.0, TICK)
    assert state.confidence == 0.0
    assert state.pitch_class is None


def test_variety_tells_a_melody_from_a_drone():
    melody = PitchTracker()
    _song(melody)
    assert melody.latest.variety > 0.8
    drone = PitchTracker()
    state = _play(drone, [50], 10.0)
    assert state.variety < 0.1
