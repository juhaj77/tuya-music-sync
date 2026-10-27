"""Beat Sync and Beat Sync White on the hits: the lamps flash, and the colour
(or the temperature) changes, where the music hits - not on every beat.

The beats - the shared beat clock's grid, or the mode's own detector - keep
the timing: a flash goes out on its beat, ahead by the clock's lead time, so
it lands on the lamps together with the hit. But a beat only flashes when it
expects a hit, as brightly as the hit it expects (flash_level), and the
colour changes once the flashes add up to Hits per change
(BeatSyncModeConfig.hue_change_hits, BeatSyncWhiteModeConfig.
temp_change_hits). A pounding four-on-the-floor flashes - and at 1 hit per
change changes colour - on every beat, a half-time kick on every second
beat, a kick on the bar's first beat once a bar; a quiet passage or a
breakdown does neither, though the clock keeps the beat going through it.

A beat's hit is measured the way Band Colors measures its bands (see
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

It is scored over a window around its beat: from a moment before it (a
detector fires on the hit's rise) to well before the off-beat (the clock's
beats go out ahead of their hits) - see _window_end.

Which beats expect a hit: hits repeat in a pattern - every beat, every
second one, beats 1 and 4 of a bar... The tracker keeps the recent beats'
hits, finds the period they repeat over (1, 2, 3, 4, 6 or 8 beats: the one
that would have predicted the last few beats best, a longer one only when
it's clearly better), and a beat expects the hit that came that period
before it. A hit is only known once it has landed, so a new pattern shows
one period late: a flash where the hits just stopped (once), and a hit no
beat expected - the drop after a break - flashes as it lands, not ahead of
it on its beat (on time where the beat comes with the hit: the mode's own
detector, or the clock without lead time).

The flashes' strengths, squared, add up to a "drive" that leaks away slowly
(over _LEAK_S); the colour changes on the flash that takes it to Hits per
change. Squared, so hard hits count much more than soft ones: a full hit 1,
a half-strength hit 0.25.
"""
from __future__ import annotations

import collections
import math
import statistics
from dataclasses import dataclass
from typing import Deque, List, Optional

from .hue_step import AdaptiveRange

FLASH_MIN = 0.2  # a beat expecting a softer hit than this doesn't flash
_FLASH_FULL = 0.7  # a hit this hard flashes at full Flash brightness, softer ones lower
_TROUGH_S = 0.25  # how fast the dip before a hit is forgotten: longer than a hit's rise, shorter than a beat
_PUNCH_FLOOR = 0.04  # a rise of the dB-scaled level (~3.6 dB) below which it's no hit: held notes, pads, noise
_PUNCH_FULL_MIN = 0.1  # ~9 dB: a full-strength hit rises at least this much...
_PUNCH_FULL_SHARE = 0.7  # ...and this share of the strongest rises lately
_MEMORY_S = 20.0  # how long the strongest rises and the hits' loudness are remembered
_LOUD_MIN_SPAN = 0.1  # ~9 dB: hit peaks steadier than this read mid-range (see AdaptiveRange)
_LOUD_FULL = 0.35  # the loudness reading that counts fully: about as loud as the hits lately
_SILENCE = (0.1, 0.2)  # the hit's peak level: silence (~-81 dB) .. music (~-72 dB)
_BEFORE_S = 0.1  # a beat's window starts this long before it...
_AFTER = (0.4, 0.12, 0.3)  # ...and ends this share of a beat after it, within these seconds
_PERIODS = (1, 2, 3, 4, 6, 8)  # the patterns tried, in beats
_FIT_BEATS = 8  # a pattern is judged by how well it would have predicted this many last beats...
_FIT_MIN = 4  # ...and needs at least this many of them
_PERIOD_COST = 0.02  # per beat of period: a longer pattern has to predict clearly better
_LATE_PUNCH = 0.6  # a hit no beat expected flashes as it lands when it's at least this hard
_LEAK_S = 8.0
_SLACK = 0.9  # a drive this close to Hits per change will do: nearly full hits are full hits
_GAPS = 3  # the typical gap between colour changes is the median of this many
_HISTORY_BEATS = 24
_HISTORY_S = 3.0  # level samples kept
_DEFAULT_BEAT_S = 0.5


def flash_level(strength: float) -> float:
    """0..1 of the way from Sustain to Flash brightness for a hit of `strength`."""
    return min(1.0, max(0.0, strength / _FLASH_FULL))


@dataclass
class _Beat:
    time: float
    expected: float  # the hit it expected (it flashed if >= FLASH_MIN)
    hit: Optional[float] = None  # the hit it got, once its window is over
    late: bool = False  # flashed on its hit landing, unexpected


class HitTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._now = 0.0
        self._trough: Optional[float] = None
        self._samples: Deque = collections.deque()  # (time, rise, level) per tick
        self._strongest = 0.0  # the strongest rise lately, fading over _MEMORY_S
        self._loudness = AdaptiveRange(_LOUD_MIN_SPAN, _MEMORY_S)
        self._scored_at: Optional[float] = None
        self._beats: Deque[_Beat] = collections.deque(maxlen=_HISTORY_BEATS)
        self._changed_at: Optional[float] = None
        self._flashes_since_change = 0
        self._intervals: Deque[float] = collections.deque(maxlen=_GAPS)
        self._flash_gaps: Deque[int] = collections.deque(maxlen=_GAPS)
        self.period = 1  # beats the hits repeat over
        self.drive = 0.0
        self.hit = 0.0  # the last scored beat's hit, 0..1

    # -- for the glide and the display ----------------------------------------------

    @property
    def typical_interval_s(self) -> Optional[float]:
        """Seconds between the recent colour changes (their median), None before two."""
        return statistics.median(self._intervals) if self._intervals else None

    @property
    def typical_flashes(self) -> Optional[int]:
        """Flashes between the recent colour changes (their median), None before two."""
        return max(1, round(statistics.median(self._flash_gaps))) if self._flash_gaps else None

    @property
    def pattern_hits(self) -> int:
        """How many beats of the last pattern period got a hit worth a flash."""
        scored = [b.hit for b in self._beats if b.hit is not None][-self.period:]
        return sum(h >= FLASH_MIN for h in scored)

    def beat_period_s(self) -> float:
        times = [b.time for b in self._beats]
        gaps = [b - a for a, b in zip(times, times[1:])][-4:]
        return statistics.median(gaps) if gaps else _DEFAULT_BEAT_S

    # -- the music ------------------------------------------------------------------

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
        self._score()

    def flash_strength(self, is_beat: bool) -> float:
        """Every tick, after update(): how hard a flash goes out now (0..1;
        below FLASH_MIN = none) - a beat's expected hit, or a hit landing that
        its beat didn't expect."""
        expected = self._beat() if is_beat else 0.0
        if expected >= FLASH_MIN:
            return expected
        return self._late_hit()

    def _beat(self) -> float:
        """A beat now: the hit it expects (0..1) - the one that came a pattern
        period before it."""
        self._score(force=True)
        hits = [b.hit for b in self._beats]
        self.period = self._fit_period(hits)
        expected = 1.0 if not hits else hits[-self.period] if len(hits) >= self.period else hits[-1]
        self._beats.append(_Beat(self._now, expected))
        return expected

    def _late_hit(self) -> float:
        """A hit landing that the last beat didn't expect (so it didn't flash):
        its strength, once per beat; else 0."""
        if not self._beats:
            return 0.0
        beat = self._beats[-1]
        if beat.expected >= FLASH_MIN or beat.late or self._now > self._window_end(beat):
            return 0.0
        _, rise, level = self._samples[-1]
        strength = _ramp(rise, _PUNCH_FLOOR, self._full()) * _ramp(level, *_SILENCE)
        if strength < _LATE_PUNCH:
            return 0.0
        beat.late = True
        return strength

    def count_flash(self, strength: float, hits_per_change: float) -> bool:
        """A flash of `strength` went out: True = the colour changes on it."""
        self.drive += strength ** 2
        self._flashes_since_change += 1
        if self.drive < _SLACK * max(hits_per_change, 1e-3):
            return False
        if self._changed_at is not None:
            self._intervals.append(self._now - self._changed_at)
            self._flash_gaps.append(self._flashes_since_change)
        self._changed_at = self._now
        self._flashes_since_change = 0
        self.drive = 0.0
        return True

    # -- inside ---------------------------------------------------------------------

    def _window_end(self, beat: _Beat) -> float:
        share, shortest, longest = _AFTER
        return beat.time + min(longest, max(shortest, share * self.beat_period_s()))

    def _full(self) -> float:
        return max(_PUNCH_FULL_MIN, _PUNCH_FULL_SHARE * self._strongest)

    def _score(self, force: bool = False) -> None:
        """Scores the beats whose window is over (with `force`, also the one
        still open - a beat came early)."""
        for beat in self._beats:
            if beat.hit is not None:
                continue
            end = self._window_end(beat)
            if not force and self._now < end:
                break
            window = [(rise, level) for t, rise, level in self._samples if beat.time - _BEFORE_S <= t <= end]
            beat.hit = self._hit(window)

    def _hit(self, window: List[tuple]) -> float:
        rise = max((r for r, _ in window), default=0.0)
        peak = max((level for _, level in window), default=0.0)
        elapsed = 0.0 if self._scored_at is None else self._now - self._scored_at
        self._scored_at = self._now
        self._strongest = max(rise, self._strongest * math.exp(-elapsed / _MEMORY_S))
        punch = _ramp(rise, _PUNCH_FLOOR, self._full())
        loud = _ramp(self._loudness.update(peak, elapsed), 0.0, _LOUD_FULL)
        self.hit = punch * (0.5 + 0.5 * loud) * _ramp(peak, *_SILENCE)
        return self.hit

    @staticmethod
    def _fit_period(hits: List[float]) -> int:
        """The pattern period that would have predicted the last beats best."""
        n = len(hits)
        best, best_cost = 1, math.inf
        for period in _PERIODS:
            pairs = [(hits[i], hits[i - period]) for i in range(max(period, n - _FIT_BEATS), n)]
            if len(pairs) < _FIT_MIN:
                continue
            cost = sum(abs(a - b) for a, b in pairs) / len(pairs) + _PERIOD_COST * period
            if cost < best_cost:
                best, best_cost = period, cost
        return best


def _ramp(x: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (x - low) / (high - low)))
