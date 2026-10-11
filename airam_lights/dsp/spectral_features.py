"""Per-tick spectral features that need a little memory of past frames.

bands.py only looks at one frame at a time; the features here compare the
current frame with the recent ones:

- Harmonic / percussive separation (HPSS): in a spectrogram, sustained notes
  are horizontal lines (one frequency, held over time) and drum hits are
  vertical lines (every frequency, for one moment). A median over the last
  few frames per bin keeps the horizontal lines (harmonic estimate); a
  median across neighbouring bins of the current frame keeps the vertical
  ones (percussive estimate). Their soft (Wiener) mask splits each bin
  between the two. Causal - only past frames - so the harmonic part lags a
  new note by about half the time window.
- Spectral flux: how much the spectrum grew since the previous frame,
  summed over bins. Unlike bass-band energy it rises on any new note - a
  piano key, a bowed string, a choir entry - which is what lets the beat
  clock follow music without drums. Each bin is compared against the
  loudest of its neighbours in the previous frame, so vibrato (a note
  wobbling into the next bin) doesn't read as a new note.

Pure numpy, driven by explicit frames, unit-tested with synthetic spectra
(tests/test_spectral_features.py). The harmonic spectrum is kept for the
later pitch-class analysis.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np

from .fft_engine import SpectrumFrame

# Analysed range: below ~30 Hz is rumble, above ~8 kHz adds mostly noise
# to the flux and nothing to pitch.
_LOW_HZ = 30.0
_HIGH_HZ = 8000.0
# Log compression strength: loud bins are compressed, quiet ones stay
# roughly linear (and small), so their random frame-to-frame jitter
# doesn't drown the flux of real note onsets.
_LOG_GAIN = 100.0
_HARMONIC_FRAMES = 9  # time median length (~0.3 s at the default 30 Hz visual tick)
_PERCUSSIVE_BINS = 9  # frequency median length (~200 Hz at 2048 @ 48 kHz)
_EPS = 1e-12


@dataclass
class FeatureFrame:
    """What SpectralFeatures.update() reports for one tick."""

    flux: float = 0.0  # spectral flux of the whole (log-compressed) spectrum
    percussive_share: float = 0.0  # 0..1 share of the energy in the percussive part
    harmonic: Optional[np.ndarray] = None  # harmonic part of the analysed bins (log-compressed)
    freqs: Optional[np.ndarray] = None  # Hz of the analysed bins


class SpectralFeatures:
    def __init__(self, harmonic_frames: int = _HARMONIC_FRAMES, percussive_bins: int = _PERCUSSIVE_BINS):
        self.harmonic_frames = max(1, int(harmonic_frames))
        self.percussive_bins = max(1, int(percussive_bins)) | 1  # odd: centred on the bin
        self.reset()

    def reset(self) -> None:
        self._frames: Deque[np.ndarray] = collections.deque(maxlen=self.harmonic_frames)
        self._prev: Optional[np.ndarray] = None
        self._freqs: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None
        self.latest = FeatureFrame()

    def update(self, frame: SpectrumFrame) -> FeatureFrame:
        if self._freqs is None or len(self._freqs) != len(frame.freqs) or self._freqs[1] != frame.freqs[1]:
            # First frame, or a new sample rate / FFT size: start over.
            self.reset()
            self._mask = (frame.freqs >= _LOW_HZ) & (frame.freqs <= _HIGH_HZ)
            self._freqs = np.asarray(frame.freqs)
        x = np.log1p(_LOG_GAIN * np.maximum(frame.magnitude[self._mask], 0.0))
        self._frames.append(x)

        harmonic_est = np.median(np.stack(self._frames), axis=0) if len(self._frames) > 1 else x
        percussive_est = _median_across(x, self.percussive_bins)
        h2, p2 = harmonic_est ** 2, percussive_est ** 2
        harmonic_mask = h2 / (h2 + p2 + _EPS)
        harmonic = x * harmonic_mask

        total = float(np.dot(x, x))
        percussive_share = float(np.dot(x * x, 1.0 - harmonic_mask) / total) if total > _EPS else 0.0
        flux = _flux(x, self._prev)
        self._prev = x

        self.latest = FeatureFrame(
            flux=flux,
            percussive_share=percussive_share,
            harmonic=harmonic,
            freqs=self._freqs[self._mask],
        )
        return self.latest


def _median_across(x: np.ndarray, width: int) -> np.ndarray:
    """Median of each bin's `width` neighbouring bins (edges repeated)."""
    if width <= 1 or len(x) < width:
        return x
    half = width // 2
    padded = np.pad(x, half, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, width), axis=1)


def _flux(x: np.ndarray, prev: Optional[np.ndarray]) -> float:
    """Mean growth per bin since `prev`, each bin compared against the
    loudest of itself and its two neighbours in `prev` (vibrato suppression)."""
    if prev is None or len(prev) != len(x):
        return 0.0
    ref = prev.copy()
    ref[1:] = np.maximum(ref[1:], prev[:-1])
    ref[:-1] = np.maximum(ref[:-1], prev[1:])
    return float(np.mean(np.maximum(0.0, x - ref)))
