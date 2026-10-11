"""Music-driven color: Chase's hue step and Beat Sync's spectrum hue.

Instead of a fixed `hue_shift_step_deg`, Chase's step can follow the music
(ChaseEffectConfig.hue_step_source), and so can each beat's color in Beat
Sync's "spectrum" hue mode (BeatSyncModeConfig.spectrum_source). Every
source is a 0..1 value that the user maps onto a range - the step's
[hue_step_min_deg, hue_step_max_deg], the hue's HSV Music Hue min..max:

- "intensity": how loud the music is right now, against its own recent
  range - the colors open up in a chorus and calm down in a quiet verse.
- "brightness": the spectral centroid (bass-heavy = dark, hi-hats/cymbals/
  sibilants = bright), against its own recent range - follows which
  instruments are playing.
- "intensity_brightness": the average of the two, itself spread over its own
  recent range - otherwise one of them holding still (an even loudness, say)
  would leave the other only half the range.
- "pitch": the dominant note (dsp.pitch) while a note stands out clearly
  enough and the notes actually move, cross-fading by the pitch confidence
  times the note variety to "intensity_brightness" as the note gets lost
  (drums, noise, atonal passages) or just drones on - never a switch, so
  the value glides from one to the other. A hue step uses the note's
  tension in the key; an absolute hue (Beat Sync's spectrum mode) uses the
  note's place on the circle of fifths (see note_position), so related
  notes and chords get neighbouring colors.

Both loudness and centroid are normalized against their own recent range
(see AdaptiveRange), so the whole [min, max] range gets used by quiet and
loud tracks, dull and bright mixes alike.
"""
from __future__ import annotations

import math
from typing import Optional

from .pitch import PitchState
from .smoothing import AttackReleaseSmoother

SOURCES = ("fixed", "intensity", "brightness", "intensity_brightness", "pitch")

_RANGE_DECAY_S = 10.0
_LOUDNESS_MIN_SPAN = 0.05  # dB-scaled level (~4.5 dB)
_CENTROID_MIN_SPAN = 0.5  # octaves
_MIX_MIN_SPAN = 0.2  # of the 0..1 average
_ATTACK_MS = 150.0
_RELEASE_MS = 400.0


def note_position(pitch: PitchState) -> float:
    """0..1 for the note's color: its place on the circle of fifths. A key's
    seven notes sit next to each other there - half the circle - so with the
    key known they are spread over the whole range instead, from the
    subdominant (0) through the tonic and on to the leading note (1), and
    the five notes outside the key go to the nearer end. A minor key counts
    from its relative major, whose notes it shares. Without a key: the
    absolute circle, C = 0, G = 1/12, D = 2/12..."""
    if pitch.pitch_class is None:
        return 0.0
    if pitch.tonic is None:
        return ((pitch.pitch_class * 7) % 12) / 12.0
    major_tonic = (pitch.tonic + 3) % 12 if pitch.minor else pitch.tonic
    fifths = ((pitch.pitch_class - major_tonic) * 7 + 1) % 12  # 0 = subdominant, 1 = tonic ... 6 = leading note
    if fifths <= 6:
        return fifths / 6.0
    return 1.0 if fifths <= 9 else 0.0


class AdaptiveRange:
    """Normalizes a signal to 0..1 against its own recent range: the top
    follows the signal up at once and sinks back toward it over `decay_s`,
    the bottom likewise downwards. A range narrower than `min_span` is
    widened around its middle, so a steady signal reads as mid-range instead
    of jumping between 0 and 1 on tiny wobbles."""

    def __init__(self, min_span: float, decay_s: float = _RANGE_DECAY_S):
        self.min_span = min_span
        self.decay_s = decay_s
        self.low: Optional[float] = None
        self.high: Optional[float] = None

    def update(self, x: float, dt: float) -> float:
        if self.low is None or self.high is None:
            self.low = self.high = x
        else:
            alpha = 1.0 - math.exp(-max(dt, 0.0) / self.decay_s)
            self.high = x if x > self.high else self.high + alpha * (x - self.high)
            self.low = x if x < self.low else self.low + alpha * (x - self.low)
        low, high = self.low, self.high
        if high - low < self.min_span:
            middle = 0.5 * (low + high)
            low, high = middle - 0.5 * self.min_span, middle + 0.5 * self.min_span
        return min(1.0, max(0.0, (x - low) / (high - low)))


class HueStepSignals:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._loudness_range = AdaptiveRange(_LOUDNESS_MIN_SPAN)
        self._centroid_range = AdaptiveRange(_CENTROID_MIN_SPAN)
        self._intensity = AttackReleaseSmoother(_ATTACK_MS, _RELEASE_MS, initial=0.5)
        self._brightness = AttackReleaseSmoother(_ATTACK_MS, _RELEASE_MS, initial=0.5)
        self._mix_range = AdaptiveRange(_MIX_MIN_SPAN)
        self._mix = 0.5
        self.pitch: Optional[PitchState] = None

    @property
    def intensity(self) -> float:
        return self._intensity.value

    @property
    def brightness(self) -> float:
        return self._brightness.value

    @property
    def mix(self) -> float:
        return self._mix

    @property
    def pitch_weight(self) -> float:
        """How much of the "pitch" source the note itself makes up right now (0..1)."""
        if self.pitch is None:
            return 0.0
        return self.pitch.confidence * self.pitch.variety

    def update(self, loudness: float, centroid_hz: float, pitch: Optional[PitchState], dt: float) -> None:
        self._intensity.update(self._loudness_range.update(loudness, dt), dt)
        self._brightness.update(self._centroid_range.update(math.log2(max(centroid_hz, 20.0)), dt), dt)
        self._mix = self._mix_range.update(0.5 * (self.intensity + self.brightness), dt)
        self.pitch = pitch

    def value(self, source: str, pitch_as: str = "tension") -> Optional[float]:
        """0..1 for `source`, or None for "fixed" (or an unknown source).
        `pitch_as`: what the note contributes to "pitch" - "tension" (its
        tension in the key) or "note" (its place on the circle of fifths)."""
        if source == "intensity":
            return self.intensity
        if source == "brightness":
            return self.brightness
        if source == "intensity_brightness":
            return self.mix
        if source == "pitch":
            weight = self.pitch_weight
            note = 0.0
            if self.pitch is not None:
                if pitch_as == "note":
                    note = note_position(self.pitch)
                else:
                    note = self.pitch.tension
            return weight * note + (1.0 - weight) * self.mix
        return None
