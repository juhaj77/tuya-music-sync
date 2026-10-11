"""Pitch-class (note) and key tracking from the harmonic part of the spectrum.

Works on SpectralFeatures' harmonic spectrum (drums already filtered out by
the harmonic/percussive split), ideally from a long FFT window so the bass
notes are resolved:

- Harmonic summation: every candidate note (C2..B5) collects its first few
  overtones from the spectrum, weighted down with the overtone number. A
  real note lines all its overtones up and stands out; the overtones on
  their own don't (they only get a few of theirs). Low notes, whose
  fundamentals sit closer together than the FFT bins, are told apart by
  their higher overtones, which are.
- Pitch classes: the candidates are folded into the 12 pitch classes
  (C, C#, ... B) - which octave doesn't matter for a color, and folding
  makes octave errors harmless.
- Key: a slow average of the pitch classes, correlated with the
  Krumhansl-Kessler major and minor key profiles (how well each note fits
  each key, from listening experiments), gives the tonic and the mode.
- Tension: how unstable the dominant note is in that key, from the same
  profiles - the tonic 0, the fifth and the third low, the chromatic notes
  near 1. This is what the hue step follows.
- Variety: how many different notes have been the dominant one lately
  (the effective count from the entropy of their share of the last few
  seconds). One note droning on - a pedal tone, a bass drone - is a clear
  note, but following it gives a color that never moves; users of the
  pitch can weigh it by this too.
- Confidence: how clearly one note stands out (a peaked pitch-class profile)
  times how clearly the music is in a key, faded out in drum-heavy
  passages, and smoothed so whoever uses it can cross-fade to
  something else as the note gets lost instead of switching. Silence and
  noise need no gate of their own: neither has a clear note.

Pure numpy, driven by explicit spectra, unit-tested with synthetic notes
(tests/test_pitch.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .smoothing import AttackReleaseSmoother

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Krumhansl-Kessler probe-tone profiles, index 0 = the tonic.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_MIDI_LOW = 36  # C2, 65 Hz
_MIDI_HIGH = 83  # B5, 988 Hz
_HARMONICS = 6
_HARMONIC_DECAY = 0.8
_MAX_HARMONIC_HZ = 5000.0

_SHORT_S = 0.12  # the "current note" averaging time
_KEY_S = 12.0  # the key averaging time
_NOTE_HYSTERESIS = 1.15  # a new dominant note must beat the current one by this ratio
_KEY_HYSTERESIS = 0.05  # a new key must correlate this much better than the current one
_TENSION_MS = 200.0

# Confidence: clarity = (peak - mean) / peak of the current pitch-class
# profile; noise sits around 0.1-0.2, a clear note or chord around 0.6 -
# though atonal music can be just as "clear": the key correlation tells those apart.
_CLARITY_LOW, _CLARITY_HIGH = 0.3, 0.55
_KEY_CORR_LOW, _KEY_CORR_HIGH = 0.5, 0.8
_PERCUSSIVE_FULL, _PERCUSSIVE_NONE = 0.45, 0.75  # percussive share where the confidence starts / ends fading
_CONFIDENCE_ATTACK_MS = 600.0
_CONFIDENCE_RELEASE_MS = 1000.0
_VARIETY_S = 6.0
# Effective number of dominant notes lately where the variety starts / is full.
_VARIETY_LOW, _VARIETY_HIGH = 1.5, 3.0


@dataclass
class PitchState:
    pitch_class: Optional[int] = None  # dominant note, 0 = C
    tonic: Optional[int] = None
    minor: bool = False
    tension: float = 0.0  # 0..1, smoothed - see the module docstring
    confidence: float = 0.0  # 0..1, smoothed
    clarity: float = 0.0  # 0..1, this tick, unsmoothed
    variety: float = 0.0  # 0..1: one note droning on = 0, a melody moving between notes = 1

    @property
    def note_name(self) -> str:
        return NOTE_NAMES[self.pitch_class] if self.pitch_class is not None else "-"

    @property
    def key_name(self) -> str:
        if self.tonic is None:
            return "-"
        return f"{NOTE_NAMES[self.tonic]} {'minor' if self.minor else 'major'}"


class PitchTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._freqs: Optional[np.ndarray] = None
        self._salience_matrix: Optional[np.ndarray] = None  # candidates x bins
        self._candidate_class: Optional[np.ndarray] = None
        self._short: Optional[np.ndarray] = None
        self._key = np.zeros(12)
        self._note_share = np.zeros(12)  # how much of the last few seconds each note was the dominant one
        self._pitch_class: Optional[int] = None
        self._tonic: Optional[int] = None
        self._minor = False
        self._key_corr = 0.0
        self._tension = AttackReleaseSmoother(_TENSION_MS, _TENSION_MS)
        self._confidence = AttackReleaseSmoother(_CONFIDENCE_ATTACK_MS, _CONFIDENCE_RELEASE_MS)
        self.latest = PitchState()

    def update(
        self,
        harmonic: Optional[np.ndarray],
        freqs: Optional[np.ndarray],
        percussive_share: float,
        dt: float,
    ) -> PitchState:
        if harmonic is None or freqs is None or len(harmonic) != len(freqs) or len(freqs) < 2:
            return self._fade(dt)
        if self._freqs is None or len(self._freqs) != len(freqs) or self._freqs[1] != freqs[1]:
            self._build_salience_matrix(np.asarray(freqs))

        chroma = np.bincount(self._candidate_class, weights=self._salience_matrix @ harmonic, minlength=12)
        alpha = 1.0 - np.exp(-max(dt, 0.0) / _SHORT_S)
        self._short = chroma if self._short is None else self._short + alpha * (chroma - self._short)
        peak = float(self._short.max())
        if peak <= 1e-9:
            return self._fade(dt)
        profile = self._short / peak
        clarity = float(1.0 - profile.mean())

        best = int(np.argmax(profile))
        if self._pitch_class is None or profile[best] > _NOTE_HYSTERESIS * profile[self._pitch_class]:
            self._pitch_class = best

        clear = _ramp(clarity, _CLARITY_LOW, _CLARITY_HIGH)
        harmonic_gate = 1.0 - _ramp(percussive_share, _PERCUSSIVE_FULL, _PERCUSSIVE_NONE)
        # Only tonal moments shape the key, so a drum break or noise can't drag it around.
        key_alpha = (1.0 - np.exp(-max(dt, 0.0) / _KEY_S)) * clear * harmonic_gate
        self._key += key_alpha * (profile - self._key)
        self._update_key()

        key_profile = _MINOR if self._minor else _MAJOR
        tension = 0.0
        if self._tonic is not None:
            rel = (self._pitch_class - self._tonic) % 12
            tension = float((key_profile.max() - key_profile[rel]) / (key_profile.max() - key_profile.min()))
        raw_confidence = clear * _ramp(self._key_corr, _KEY_CORR_LOW, _KEY_CORR_HIGH) * harmonic_gate

        share_alpha = (1.0 - np.exp(-max(dt, 0.0) / _VARIETY_S)) * clear
        dominant = np.zeros(12)
        dominant[self._pitch_class] = 1.0
        self._note_share += share_alpha * (dominant - self._note_share)
        self.latest = PitchState(
            pitch_class=self._pitch_class,
            tonic=self._tonic,
            minor=self._minor,
            tension=self._tension.update(tension, dt),
            confidence=self._confidence.update(raw_confidence, dt),
            clarity=clarity,
            variety=self._variety(),
        )
        return self.latest

    def _variety(self) -> float:
        total = float(self._note_share.sum())
        if total <= 1e-9:
            return 0.0
        p = self._note_share[self._note_share > 1e-9] / total
        effective_notes = float(np.exp(-np.sum(p * np.log(p))))
        return _ramp(effective_notes, _VARIETY_LOW, _VARIETY_HIGH)

    def _fade(self, dt: float) -> PitchState:
        """No usable spectrum this tick: let the confidence fall, keep the rest."""
        s = self.latest
        self.latest = PitchState(
            pitch_class=s.pitch_class,
            tonic=s.tonic,
            minor=s.minor,
            tension=s.tension,
            confidence=self._confidence.update(0.0, dt),
            clarity=0.0,
            variety=s.variety,
        )
        return self.latest

    def _update_key(self) -> None:
        if float(self._key.max()) <= 1e-9:
            return
        best_corr, best_tonic, best_minor = -2.0, 0, False
        current_corr = -2.0
        for minor, profile in ((False, _MAJOR), (True, _MINOR)):
            for tonic in range(12):
                corr = _pearson(self._key, np.roll(profile, tonic))
                if corr > best_corr:
                    best_corr, best_tonic, best_minor = corr, tonic, minor
                if tonic == self._tonic and minor == self._minor:
                    current_corr = corr
        if self._tonic is None or best_corr > current_corr + _KEY_HYSTERESIS:
            self._tonic, self._minor, self._key_corr = best_tonic, best_minor, best_corr
        else:
            self._key_corr = current_corr

    def _build_salience_matrix(self, freqs: np.ndarray) -> None:
        """Each candidate note's row: linear-interpolation weights picking its
        overtones out of the spectrum, so salience = matrix @ spectrum."""
        self.reset()
        self._freqs = freqs
        midis = np.arange(_MIDI_LOW, _MIDI_HIGH + 1)
        matrix = np.zeros((len(midis), len(freqs)))
        bin_hz = float(freqs[1] - freqs[0])
        for row, midi in enumerate(midis):
            f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
            for h in range(1, _HARMONICS + 1):
                f = f0 * h
                if f > _MAX_HARMONIC_HZ or f >= freqs[-1] or f < freqs[0]:
                    break
                pos = (f - freqs[0]) / bin_hz
                i = int(pos)
                frac = pos - i
                weight = _HARMONIC_DECAY ** (h - 1)
                matrix[row, i] += weight * (1.0 - frac)
                if i + 1 < len(freqs):
                    matrix[row, i + 1] += weight * frac
        self._salience_matrix = matrix
        self._candidate_class = midis % 12


def _ramp(x: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if x >= high else 0.0
    return float(min(1.0, max(0.0, (x - low) / (high - low))))


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / denom) if denom > 1e-12 else 0.0
