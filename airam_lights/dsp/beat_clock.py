"""Shared beat clock: one beat source every rhythm-driven layer can follow.

Without this, Beat Sync, the Chase overlay and Group Switch each ran their
own independent BeatDetector with its own band/threshold - so the same drum
hit could advance one layer but not another, or advance them on different
ticks, and the combined show read as three unrelated rhythms layered on top
of each other (i.e. random). BeatClock is detected once, per visual tick,
and every layer that opts in reacts to the very same beat events.

On top of plain onset detection (same energy-vs-rolling-average idea as
BeatDetector) it adds:

- Tempo estimation: autocorrelation of the onset-strength envelope over the
  last few seconds, with a mild preference for ~120 BPM to avoid locking to
  double/half tempo.
- Phase lock: once the tempo is stable, onsets that land near the expected
  beat grid nudge the grid into place, and onsets far off the grid (hi-hats,
  off-beat snares, vocals) are ignored instead of producing a beat - this is
  what makes the rhythm read as regular.
- Fill-in: if the grid expects a beat and the music is still playing but no
  onset shows up (a quiet kick, a fill), a predicted beat is emitted anyway,
  so bar counting doesn't drift.
- Lead: with `lead_ms` > 0, locked beats are emitted that much BEFORE the
  expected beat time, compensating for the network + bulb reaction delay so
  the light lands on the beat instead of just after it.
- Bar counting: every emitted beat has a position within a bar
  (`beats_per_bar`), with the bar start placed on whichever position has
  been hitting hardest on average (a heuristic "downbeat").
- Accents: a beat whose onset strength is in the top `accent_ratio` of
  recent beats is flagged, so e.g. true-white flashes can go on the big hits
  only instead of on a random subset.

Pure and dependency-light (numpy only), driven by explicit timestamps, so it
can be unit-tested with synthetic pulse trains (tests/test_beat_clock.py).
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Deque, List, Optional, Tuple

import numpy as np

_MIN_PERIOD_S = 60.0 / 190.0
_MAX_PERIOD_S = 60.0 / 70.0
_TEMPO_WINDOW_S = 6.0
_TEMPO_UPDATE_S = 0.5
# An onset within this fraction of a period from the expected grid beat
# counts as "on the beat"; anything further away is treated as off-beat.
_ON_BEAT_TOLERANCE = 0.22
# How strongly one on-beat onset pulls the grid toward itself (0..1).
_PHASE_GAIN = 0.35
# The lock is dropped after this many tempo updates in a row without a
# clear periodicity (or with the music silent).
_LOCK_LOSS_UPDATES = 3
# How strongly each tempo update's envelope-wide phase estimate steers the grid.
_COMB_GAIN = 0.3
_PHASE_STEPS = 32
# A new tempo estimate this far (relative) from the current one re-anchors.
_RETEMPO_TOLERANCE = 0.06


@dataclass
class ClockBeat:
    """What BeatClock.update() reports for one tick."""

    is_beat: bool = False
    beat_count: int = 0  # beats emitted so far (monotonic), incl. this one
    aligned_index: int = 0  # beat number counted from a bar start (for every-N-beats divisions)
    bar_position: int = 0  # 0..beats_per_bar-1, 0 = bar start ("downbeat")
    is_downbeat: bool = False
    strength: float = 0.0  # onset energy / rolling average (0 for predicted beats)
    is_accent: bool = False
    predicted: bool = False  # emitted from the tempo grid rather than a detected onset
    period_s: Optional[float] = None
    locked: bool = False

    @property
    def bpm(self) -> Optional[float]:
        return 60.0 / self.period_s if self.period_s else None


class BeatClock:
    def __init__(
        self,
        sensitivity: float = 1.4,
        min_interval_ms: float = 200.0,
        min_energy: float = 0.1,
        history_seconds: float = 1.2,
        tempo_lock: bool = True,
        lead_ms: float = 0.0,
        beats_per_bar: int = 4,
        accent_ratio: float = 0.25,
    ):
        self.sensitivity = sensitivity
        self.min_interval_ms = min_interval_ms
        self.min_energy = min_energy
        self.history_seconds = history_seconds
        self.tempo_lock = tempo_lock
        self.lead_ms = lead_ms
        self.beats_per_bar = beats_per_bar
        self.accent_ratio = accent_ratio
        self.reset()

    def reset(self) -> None:
        self._history: Deque[Tuple[float, float]] = collections.deque()  # (t, energy) for the rolling average
        self._envelope: Deque[Tuple[float, float]] = collections.deque()  # (t, onset strength) for tempo
        self._last_onset: Optional[float] = None
        self._last_emit: Optional[float] = None
        self._last_loud: Optional[float] = None
        self._last_tempo_update: Optional[float] = None
        self._period: Optional[float] = None
        self._grid_anchor: Optional[float] = None  # time of a (real, not lead-shifted) grid beat
        self._tempo_misses = 0
        self._last_beat_time: Optional[float] = None  # real (grid) time of the last emitted beat
        self._last_beat_aligned = 0
        self._emitted_grid_index: Optional[int] = None
        self._beat_count = 0
        self._slot_strength = [0.0] * max(1, self.beats_per_bar)
        self._downbeat_slot = 0
        self._recent_strengths: Deque[float] = collections.deque(maxlen=16)
        self.latest = ClockBeat()

    # -- public state ------------------------------------------------------------------

    @property
    def period_s(self) -> Optional[float]:
        return self._period

    @property
    def locked(self) -> bool:
        return self.tempo_lock and self._period is not None and self._grid_anchor is not None

    def position(self, now_s: float) -> Optional[float]:
        """Where in the music we are (lead included, i.e. where the lamps will
        be by the time a command sent now lands), in beats counted from a bar
        start: 8.5 = the "and" after the first beat of the third bar. None
        while the tempo isn't locked - sub-beat timing is only meaningful on
        a steady grid."""
        if not self.locked or self._last_beat_time is None:
            return None
        lead = max(0.0, self.lead_ms) / 1000.0
        return self._last_beat_aligned + (now_s + lead - self._last_beat_time) / self._period

    # -- update ----------------------------------------------------------------------------

    def update(self, energy: float, now_s: float) -> ClockBeat:
        """Feed one energy sample (the caller's band energy, roughly 0..1)
        at wall-clock time `now_s`. Returns what happened on this tick."""
        onset, strength = self._detect_onset(energy, now_s)
        if energy >= self.min_energy:
            self._last_loud = now_s
        self._update_tempo(now_s)

        if not self.locked:
            return self._emit(now_s, strength, predicted=False) if onset else self._idle()

        period = self._period
        lead = max(0.0, self.lead_ms) / 1000.0
        # Never two beats closer than half a period, whichever path emits them.
        spaced = self._last_emit is None or now_s - self._last_emit >= 0.5 * period

        if onset:
            index = self._grid_index(now_s)
            error = now_s - (self._grid_anchor + index * period)
            if abs(error) <= _ON_BEAT_TOLERANCE * period:
                # On-beat onset: pull the grid toward it (a simple phase-locked loop).
                self._grid_anchor += _PHASE_GAIN * error
                if spaced and (self._emitted_grid_index is None or index > self._emitted_grid_index):
                    self._emitted_grid_index = index
                    return self._emit(now_s, strength, predicted=False, grid_time=self._grid_anchor + index * period)
                # Already emitted early (lead) for this grid beat - just learn how hard it hit.
                self._learn_strength((self._beat_count - 1) % len(self._slot_strength), strength)
            # Off-beat onsets (hi-hats, off-beat snares, vocals) are ignored.
            return self._idle()

        # Scheduled beat: the next grid beat, emitted `lead` early; with no
        # lead, a fill-in once the grid beat has clearly passed with no onset.
        music_playing = self._last_loud is not None and now_s - self._last_loud < 2.0 * period
        next_index = self._grid_index(now_s + lead - _ON_BEAT_TOLERANCE * period)
        next_time = self._grid_anchor + next_index * period
        if lead > 0.0:
            due = now_s >= next_time - lead
        else:
            due = now_s >= next_time + _ON_BEAT_TOLERANCE * period
        if (
            due
            and spaced
            and music_playing
            and (self._emitted_grid_index is None or next_index > self._emitted_grid_index)
        ):
            self._emitted_grid_index = next_index
            # Early (lead) beats haven't been heard yet: judge them by how
            # hard this bar position usually hits. Fill-ins are never accents.
            return self._emit(now_s, None if lead > 0.0 else 0.0, predicted=True, grid_time=next_time)
        return self._idle()

    # -- internals --------------------------------------------------------------------

    def _detect_onset(self, energy: float, now_s: float) -> Tuple[bool, float]:
        self._history.append((now_s, energy))
        cutoff = now_s - self.history_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
        avg = sum(e for _, e in self._history) / len(self._history)

        self._envelope.append((now_s, max(0.0, energy - avg)))
        env_cutoff = now_s - _TEMPO_WINDOW_S
        while self._envelope and self._envelope[0][0] < env_cutoff:
            self._envelope.popleft()

        if len(self._history) < 4:
            return False, 0.0
        strength = energy / avg if avg > 1e-9 else 0.0
        refractory_ms = self.min_interval_ms
        if self._period is not None:
            # Two real beats can't be closer than about half a period apart.
            refractory_ms = max(refractory_ms, 0.45 * self._period * 1000.0)
        since = (now_s - self._last_onset) * 1000.0 if self._last_onset is not None else None
        onset = (
            energy >= self.min_energy
            and energy > avg * self.sensitivity
            and (since is None or since >= refractory_ms)
        )
        if onset:
            self._last_onset = now_s
        return onset, strength

    def _update_tempo(self, now_s: float) -> None:
        if not self.tempo_lock:
            self._period = None
            self._grid_anchor = None
            return
        if self._last_tempo_update is not None and now_s - self._last_tempo_update < _TEMPO_UPDATE_S:
            return
        self._last_tempo_update = now_s

        period = self._estimate_period()
        music_playing = self._last_loud is not None and now_s - self._last_loud < 2.0
        if period is None or not music_playing:
            self._tempo_misses += 1
            if self._tempo_misses >= _LOCK_LOSS_UPDATES:
                self._grid_anchor = None  # lost the beat: fall back to reacting to raw onsets
                if not music_playing:
                    self._period = None
            return
        self._tempo_misses = 0

        relock = self._grid_anchor is None
        if self._period is None or abs(period - self._period) / self._period > _RETEMPO_TOLERANCE:
            self._period = period
            relock = True
        else:
            self._period += 0.2 * (period - self._period)

        anchor = self._estimate_phase(now_s, self._period)
        if anchor is None:
            return
        if relock:
            self._grid_anchor = anchor
            # Treat grid beats already in the past as done; the next one is scheduled normally.
            self._emitted_grid_index = int(math.floor((now_s - anchor) / self._period + 1e-9))
            if self.lead_ms <= 0.0:
                self._emitted_grid_index -= 1  # the latest one may still be answered by its onset
        else:
            # Gently steer the grid toward where the whole recent envelope says the beats are.
            k = round((anchor - self._grid_anchor) / self._period)
            self._grid_anchor += _COMB_GAIN * (anchor - (self._grid_anchor + k * self._period))

    def _estimate_phase(self, now_s: float, period: float) -> Optional[float]:
        """Where the beats fall, given the period: the phase whose comb of
        beat times best lines up with the recent onset envelope (recent
        samples weighted more). Returns a grid beat time <= now."""
        window = min(_TEMPO_WINDOW_S, 4.0 * period)
        samples = [(t, v) for t, v in self._envelope if t >= now_s - window]
        if len(samples) < 8:
            return None
        times = np.fromiter((t for t, _ in samples), float)
        values = np.fromiter((v for _, v in samples), float)
        if values.sum() <= 1e-12:
            return None
        weights = values * (0.3 + 0.7 * (times - times[0]) / max(1e-9, times[-1] - times[0]))
        phases = np.linspace(0.0, period, _PHASE_STEPS, endpoint=False)
        sigma = 0.07 * period
        best_phase, best_score = 0.0, -1.0
        for phase in phases:
            d = np.mod(times - phase + period / 2.0, period) - period / 2.0
            score = float(np.dot(weights, np.exp(-0.5 * (d / sigma) ** 2)))
            if score > best_score:
                best_phase, best_score = phase, score
        return best_phase + math.floor((now_s - best_phase) / period) * period

    def _estimate_period(self) -> Optional[float]:
        if len(self._envelope) < 20:
            return None
        times = np.fromiter((t for t, _ in self._envelope), float)
        span = times[-1] - times[0]
        if span < 3.0:
            return None
        dt = span / (len(times) - 1)
        x = np.fromiter((v for _, v in self._envelope), float)
        x = x - x.mean()
        energy = float(np.dot(x, x))
        if energy <= 1e-12:
            return None
        lo = max(1, int(_MIN_PERIOD_S / dt))
        hi = min(len(x) - 2, int(math.ceil(_MAX_PERIOD_S / dt)))
        if hi <= lo:
            return None
        lags = np.arange(lo, hi + 1)
        ac = np.array([np.dot(x[:-k], x[k:]) for k in lags]) / energy
        # Mild log-Gaussian preference around 120 BPM to avoid octave errors.
        prior = np.exp(-0.5 * (np.log2((lags * dt) / 0.5) / 0.9) ** 2)
        score = ac * prior
        best = int(np.argmax(score))
        if ac[best] < 0.1:
            return None  # no clear periodicity
        lag = float(lags[best])
        if 0 < best < len(ac) - 1:  # parabolic interpolation for sub-sample precision
            a, b, c = ac[best - 1], ac[best], ac[best + 1]
            denom = a - 2 * b + c
            if abs(denom) > 1e-12:
                lag += 0.5 * (a - c) / denom
        return lag * dt

    def _grid_index(self, t: float) -> int:
        return int(round((t - self._grid_anchor) / self._period))

    def _learn_strength(self, slot: int, strength: float) -> None:
        if strength <= 0.0:
            return
        self._recent_strengths.append(strength)
        self._slot_strength[slot] += 0.25 * (strength - self._slot_strength[slot])

    def _is_accent(self, strength: float) -> bool:
        if strength <= 0.0 or len(self._recent_strengths) < 4:
            return False
        ranked = sorted(self._recent_strengths)
        top = int(math.ceil(len(ranked) * max(0.0, min(1.0, self.accent_ratio))))
        return top > 0 and strength >= ranked[-top]

    def _emit(
        self, now_s: float, strength: Optional[float], predicted: bool, grid_time: Optional[float] = None
    ) -> ClockBeat:
        """strength None = an early (lead) beat: use this bar position's
        typical strength, since the real onset hasn't happened yet."""
        bpb = max(1, int(self.beats_per_bar))
        if len(self._slot_strength) != bpb:
            self._slot_strength = [0.0] * bpb
            self._downbeat_slot = 0
        slot = self._beat_count % bpb
        if strength is None:
            # An early (lead) beat hasn't been heard yet, so it can only be
            # judged by the bar's pattern: accent if this position is among the
            # top accent_ratio of positions that usually hit hardest.
            strength = self._slot_strength[slot]
            top = max(1, int(math.ceil(bpb * max(0.0, min(1.0, self.accent_ratio)))))
            ranked = sorted(self._slot_strength, reverse=True)
            is_accent = strength > 0.0 and strength >= ranked[top - 1]
        else:
            is_accent = self._is_accent(strength)
            if not predicted:
                self._learn_strength(slot, strength)
        if slot == bpb - 1:
            # Once per bar: move the bar start to the hardest-hitting slot, with
            # hysteresis so it doesn't flip back and forth between similar ones.
            best = max(range(bpb), key=lambda i: self._slot_strength[i])
            if self._slot_strength[best] > 1.2 * self._slot_strength[self._downbeat_slot]:
                self._downbeat_slot = best

        aligned_index = self._beat_count - self._downbeat_slot
        self._beat_count += 1
        self._last_emit = now_s
        self._last_beat_time = grid_time if grid_time is not None else now_s
        self._last_beat_aligned = aligned_index
        bar_position = aligned_index % bpb
        self.latest = ClockBeat(
            is_beat=True,
            beat_count=self._beat_count,
            aligned_index=aligned_index,
            bar_position=bar_position,
            is_downbeat=bar_position == 0,
            strength=strength,
            is_accent=is_accent,
            predicted=predicted,
            period_s=self._period,
            locked=self.locked,
        )
        return self.latest

    def _idle(self) -> ClockBeat:
        return ClockBeat(
            is_beat=False,
            beat_count=self._beat_count,
            aligned_index=self.latest.aligned_index,
            bar_position=self.latest.bar_position,
            period_s=self._period,
            locked=self.locked,
        )


def beat_divides(beat: ClockBeat, every_n: int) -> bool:
    """True if `beat` is a beat AND lands on an every-`every_n`-beats step,
    counted from the bar start - so e.g. every_n=4 in 4/4 fires on the
    downbeats, every_n=2 on beats 1 and 3."""
    if not beat.is_beat:
        return False
    return beat.aligned_index % max(1, int(every_n)) == 0
