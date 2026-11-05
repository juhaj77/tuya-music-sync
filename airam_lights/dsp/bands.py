"""Frequency-band energy extraction and spectral feature helpers.

Pure numpy functions operating on a SpectrumFrame - no state, easy to test
and to reuse for the band levels every mode reads and the spectral centroid
behind the music-driven hues (dsp.hue_step, Peak Flash's "centroid" hue).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .fft_engine import SpectrumFrame

_EPS = 1e-9


def band_energy(frame: SpectrumFrame, low_hz: float, high_hz: float, floor_db: float = -90.0) -> float:
    """Return a normalized 0..1 energy level for one frequency band.

    Uses average magnitude in dB across the band's bins, rescaled from
    [floor_db, 0] to [0, 1]. Using dB (rather than raw linear power) gives a
    perceptually-reasonable, much less "bass-dominated" response than
    summing linear power directly, and matches how humans judge loudness
    across the spectrum.
    """
    mask = (frame.freqs >= low_hz) & (frame.freqs < high_hz)
    if not np.any(mask):
        return 0.0
    db = frame.magnitude_db[mask]
    avg_db = float(np.mean(db))
    level = (avg_db - floor_db) / (0.0 - floor_db)
    return float(np.clip(level, 0.0, 1.0))


def band_energies(frame: SpectrumFrame, bands: Sequence, floor_db: float = -90.0) -> list:
    """bands: sequence of objects with .low_hz / .high_hz (e.g. BandDefinition)."""
    return [band_energy(frame, b.low_hz, b.high_hz, floor_db) for b in bands]


def spectral_centroid_hz(frame: SpectrumFrame, low_hz: float = 20.0, high_hz: float = 16000.0) -> float:
    """Energy-weighted average frequency within [low_hz, high_hz]."""
    mask = (frame.freqs >= low_hz) & (frame.freqs <= high_hz)
    if not np.any(mask):
        return low_hz
    freqs = frame.freqs[mask]
    weights = np.maximum(frame.magnitude[mask], 0.0)
    total = float(np.sum(weights))
    if total < _EPS:
        return low_hz
    return float(np.sum(freqs * weights) / total)
