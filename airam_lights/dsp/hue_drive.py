"""Beat Sync's dynamic hue change: the colour changes when the music hits,
not on a fixed count of beats.

With Follow the hits on (BeatSyncModeConfig.hue_change_dynamic) every beat
scores how hard it hit, and the scores add up to a "drive". The hue changes
on a beat Change hue allows (hue_every_n_beats - every beat, every bar...)
once the drive has reached hue_change_hits, and the drive starts over. A
pounding bass changes the colour as often as Change hue allows, softer hits
less often, a quiet passage - no hits - not at all.

A hit is measured the way Band Colors measures its bands (see
effects/band_colors.py): against the music's own recent range, not an
absolute scale, so loud and quiet mixes alike use the whole scale.

- Punch: how far the beat band's level rose from the dip before the hit
  (dB-scaled, so the volume knob doesn't change it). A kick over a bass line
  rises 10-15 dB, a held bass note or a pad wobbles a dB or two. Below
  _PUNCH_FLOOR it is no hit at all; it counts fully from 70 % of the
  strongest rises lately (an ordinary hit of a pounding beat), though never
  from less than _PUNCH_FULL_MIN.
- Loudness: the hit's peak against the recent hits' peaks. Quieter than the
  music just was (a verse after the chorus) counts less, down to half; about
  as loud as lately counts fully. (A level that stays put reads mid-range
  against its own range - see AdaptiveRange - so "as loud as lately" is
  around 0.5, a little less for a slightly weaker hit.)
- Silence: a beat band near silence never counts.

The score is the hit squared, so hard hits count much more than soft ones (a
full hit 1, a half-strength hit 0.25), and the drive leaks away slowly (over
_LEAK_S), so a few soft hits spread over a quiet passage don't add up to a
change.

Each beat scores the biggest rise since a moment before the beat before
(_OVERLAP_S), its own hit included as far as it has arrived. The clock sends
its beats ahead of the hit (lead_ms) and a hit takes a moment to rise, so a
hit lands around its beat, not after it - and the next beat sees it whole
even when this one only caught its start. (A window starting right at the
beat before missed the hit whenever that beat had caught it, and with the
beats falling between the ticks a steady kick scored full and nothing on
alternate beats.) A drop after a quiet break changes the colour on its
first or second kick.
"""
from __future__ import annotations

import collections
import math
import statistics
from typing import Optional

from .hue_step import AdaptiveRange

_TROUGH_S = 0.25  # how fast the dip before a hit is forgotten: longer than a hit's rise, shorter than a beat
_PUNCH_FLOOR = 0.04  # a rise of the dB-scaled level (~3.6 dB) below which it's no hit: held notes, pads, noise
_PUNCH_FULL_MIN = 0.1  # ~9 dB: a full-strength hit rises at least this much...
_PUNCH_FULL_SHARE = 0.7  # ...and this share of the strongest rises lately
_MEMORY_S = 20.0  # how long the strongest rises and the hits' loudness are remembered
_LOUD_MIN_SPAN = 0.1  # ~9 dB: hit peaks steadier than this read mid-range (see AdaptiveRange)
_LOUD_FULL = 0.35  # the loudness reading that counts fully: about as loud as the hits lately
_SILENCE = (0.1, 0.2)  # the hit's peak level: silence (~-81 dB) .. music (~-72 dB)
_LEAK_S = 8.0
_SLACK = 0.9  # a drive this close to Hits per change will do: nearly full hits are full hits
_GAPS = 3  # the typical gap between changes is the median of this many
_OVERLAP_S = 0.15  # each beat's window starts this long before the beat before
_HISTORY_S = 3.0  # the longest window kept (a beat slower than this sees only its last 3 s)


class HueDrive:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._trough: Optional[float] = None
        self._now = 0.0
        self._samples: collections.deque = collections.deque()  # (time, rise, level) per tick
        self._last_beat: Optional[float] = None
        self._strongest = 0.0  # the strongest rise lately, fading over _MEMORY_S
        self._loudness = AdaptiveRange(_LOUD_MIN_SPAN, _MEMORY_S)
        self._beats_since_change = 0
        self._gaps: collections.deque = collections.deque(maxlen=_GAPS)
        self.drive = 0.0
        self.hit = 0.0  # the last beat's hit, 0..1

    @property
    def typical_gap(self) -> Optional[int]:
        """Beats between the recent hue changes (their median), None before two changes."""
        if not self._gaps:
            return None
        return max(1, round(statistics.median(self._gaps)))

    def update(self, level: float, dt: float) -> None:
        """Every tick: the beat band's dB-scaled level (bands.band_energy)."""
        dt = max(dt, 0.0)
        self._now += dt
        self.drive *= math.exp(-dt / _LEAK_S)
        if self._trough is None or level < self._trough:
            self._trough = level
        else:
            self._trough += (1.0 - math.exp(-dt / _TROUGH_S)) * (level - self._trough)
        self._samples.append((self._now, level - self._trough, level))
        while self._samples[0][0] < self._now - _HISTORY_S:
            self._samples.popleft()

    def beat(self, hits_per_change: float, allowed: bool = True) -> bool:
        """On every beat: scores its hit into the drive. True = change the hue
        now - on an `allowed` beat (Change hue's every N beats) once the drive
        has reached `hits_per_change`."""
        start = -math.inf if self._last_beat is None else self._last_beat - _OVERLAP_S
        window = [(rise, level) for t, rise, level in self._samples if t >= start] or [(0.0, 0.0)]
        rise = max(r for r, _ in window)
        peak = max(level for _, level in window)
        elapsed = 0.0 if self._last_beat is None else self._now - self._last_beat
        self._last_beat = self._now

        self._strongest = max(rise, self._strongest * math.exp(-elapsed / _MEMORY_S))
        full = max(_PUNCH_FULL_MIN, _PUNCH_FULL_SHARE * self._strongest)
        punch = _ramp(rise, _PUNCH_FLOOR, full)
        loud = _ramp(self._loudness.update(peak, elapsed), 0.0, _LOUD_FULL)
        self.hit = punch * (0.5 + 0.5 * loud) * _ramp(peak, *_SILENCE)
        self.drive += self.hit ** 2

        self._beats_since_change += 1
        if not allowed or self.drive < _SLACK * max(hits_per_change, 1e-3):
            return False
        self._gaps.append(self._beats_since_change)
        self._beats_since_change = 0
        self.drive = 0.0
        return True


def _ramp(x: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (x - low) / (high - low)))
