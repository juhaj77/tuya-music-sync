"""SpectralFeatures: spectral flux and harmonic/percussive split, driven by
synthetic spectra."""
import numpy as np

from airam_lights.dsp.fft_engine import SpectrumFrame
from airam_lights.dsp.spectral_features import SpectralFeatures

FREQS = np.fft.rfftfreq(2048, d=1.0 / 48000)
BIN_HZ = FREQS[1]


def _frame(tones=(), noise=0.0, level=0.05, seed=None):
    """A spectrum with sine-like peaks at `tones` (Hz) plus flat `noise`."""
    mag = np.full(len(FREQS), 1e-5)
    if noise:
        rng = np.random.default_rng(seed)
        mag += noise * (0.8 + 0.4 * rng.random(len(FREQS)))
    for hz in tones:
        i = int(round(hz / BIN_HZ))
        mag[i] += level
        mag[i - 1] += level * 0.5
        mag[i + 1] += level * 0.5
    db = 20.0 * np.log10(np.maximum(mag, 1e-9))
    return SpectrumFrame(freqs=FREQS, magnitude=mag, magnitude_db=np.maximum(db, -90.0))


def test_held_note_has_no_flux_and_a_new_note_does():
    sf = SpectralFeatures()
    for _ in range(10):
        held = sf.update(_frame([440.0]))
    assert held.flux < 1e-6
    new = sf.update(_frame([440.0, 660.0]))
    assert new.flux > 100 * max(held.flux, 1e-9)


def test_vibrato_is_not_a_new_note():
    sf = SpectralFeatures()
    for _ in range(5):
        sf.update(_frame([440.0]))
    wobble = sf.update(_frame([440.0 + BIN_HZ]))  # the note drifts into the next bin
    sf2 = SpectralFeatures()
    for _ in range(5):
        sf2.update(_frame([440.0]))
    new_note = sf2.update(_frame([440.0, 1200.0]))
    assert wobble.flux < 0.3 * new_note.flux


def test_drum_hit_is_percussive_and_held_notes_harmonic():
    sf = SpectralFeatures()
    for _ in range(10):
        tonal = sf.update(_frame([220.0, 440.0, 660.0]))
    hit = sf.update(_frame([220.0, 440.0, 660.0], noise=0.02, seed=1))
    assert tonal.percussive_share < 0.2
    assert hit.percussive_share > 0.6
    assert hit.flux > tonal.flux


def test_harmonic_part_keeps_the_notes():
    sf = SpectralFeatures()
    for _ in range(10):
        out = sf.update(_frame([440.0]))
    peak = int(np.argmax(out.harmonic))
    assert abs(out.freqs[peak] - 440.0) < BIN_HZ


def test_new_fft_size_starts_over():
    sf = SpectralFeatures()
    sf.update(_frame([440.0]))
    freqs = np.fft.rfftfreq(4096, d=1.0 / 48000)
    mag = np.full(len(freqs), 1e-3)
    out = sf.update(SpectrumFrame(freqs=freqs, magnitude=mag, magnitude_db=20 * np.log10(mag)))
    assert out.flux == 0.0
    assert len(out.harmonic) == len(out.freqs)
