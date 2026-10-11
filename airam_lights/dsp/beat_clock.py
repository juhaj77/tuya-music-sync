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
- Coasting: when the tempo estimate loses the beat while the music keeps
  playing (a breakdown, a fill, a vocal passage with no clear kick), the
  grid keeps running on the last tempo for up to `coast_bars` bars instead
  of dropping the lock (only a lock that held for a few seconds - a
  briefly-seen tempo is more likely chance than a beat) - otherwise every layer following the clock would
  stand still until the beat is found again. A clear tempo re-anchors as
  usual; silence stops the clock straight away.
- Lead: with `lead_ms` > 0, locked beats are emitted that much BEFORE the
  expected beat time, compensating for the network + bulb reaction delay so
  the light lands on the beat instead of just after it.
- Bar counting: every emitted beat has a position within a bar
  (`beats_per_bar`), with the bar start placed on whichever position has
  been hitting hardest on average (a heuristic "downbeat").
- Accents: a beat whose onset strength is in the top `accent_ratio` of
  recent beats is flagged, so e.g. true-white flashes can go on the big hits
  only instead of on a random subset.
- Fallback (`fallback`, with spectral flux fed to update()): music without a
  steady kick - classical, acoustic, ambient - never gives the bass band a
  tempo, so once coasting runs out the clock moves down a tier instead of
  stopping:
  "flow": the same tempo estimate and grid, but from the spectral flux
  (every new note, not just kicks), with a wider tempo range and a looser,
  faster-following grid so rubato doesn't throw it off.
  "free": no periodicity in the notes either - the grid keeps running at a
  pace set by how many notes are being played and whether the music is
  swelling or calming down, and a strong note late in a beat starts the
  next beat right there.
  A clear tempo on a higher tier takes over again (two agreeing estimates
  in a row, so one odd estimate can't flip it); silence stops the clock.
  `source` on the beats says which tier produced them.

Pure and dependency-light (numpy only), driven by explicit timestamps, so it
can be unit-tested with synthetic pulse trains (tests/test_beat_clock.py).
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple

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
# Agreeing estimates in a row a tier needs to start or move its grid, and to
# take the grid over from a lower tier (see _confirmed).
_CONFIRM_UPDATES = 2
_TAKEOVER_UPDATES = 3
# Only a lock confirmed this many tempo updates (0.5 s each) may coast: a
# tempo that only showed up briefly is more likely chance than a beat.
_COAST_MIN_HITS = 6
# How strongly each tempo update's envelope-wide phase estimate steers the grid.
_COMB_GAIN = 0.3
_PHASE_STEPS = 32
# The tempo is only estimated while the last _RECENT_ACTIVITY_S of the onset
# envelope carries at least this share of the whole window's average.
_RECENT_ACTIVITY_S = 1.5
_RECENT_ACTIVITY_SHARE = 0.25
# A new tempo estimate this far (relative) from the current one re-anchors.
_RETEMPO_TOLERANCE = 0.06
# The autocorrelation peak a tempo needs (white noise reaches ~0.1, a kick
# pattern ~0.3 and up).
_MIN_AC = 0.15

# Fallback tiers (see Fallback in the module docstring).
_FLOW_WINDOW_S = 8.0
_FLOW_MIN_PERIOD_S = 60.0 / 180.0
_FLOW_MAX_PERIOD_S = 60.0 / 40.0
_FLOW_PRIOR_PERIOD_S = 0.6  # ~100 BPM: classical tempos sit lower than dance music
_FLOW_PRIOR_OCTAVES = 1.0
_FLOW_MIN_AC = 0.2
_FLOW_TOLERANCE = 0.3  # wider than _ON_BEAT_TOLERANCE: played notes are less exact than a drum machine
_FLOW_PHASE_GAIN = 0.45
_FLOW_TEMPO_GAIN = 0.35  # follows tempo changes (rubato) faster than the beat tier's 0.2
_FREE_PHASE_GAIN = 0.5
_FREE_MIN_PERIOD_S = 60.0 / 140.0
_FREE_MAX_PERIOD_S = 60.0 / 50.0
_FREE_DEFAULT_PERIOD_S = 0.75
_FREE_DENSITY_WINDOW_S = 6.0
_FREE_NOTES_PER_BEAT = 2.0  # notes per beat the free pace assumes (8ths)
_FREE_TEMPO_GAIN = 0.25  # per tempo update (0.5 s): the pace settles over ~2 s
_FREE_SWELL_OCTAVES_PER_UNIT = 5.0  # pace doubles per +0.2 (~18 dB) of loudness above the slow average
_FREE_MAX_SWELL = 1.33
# Spectral-flux onsets (the fallback tiers' note onsets).
_FLUX_HISTORY_S = 1.2
_FLUX_SENSITIVITY = 1.4
_FLUX_MIN_INTERVAL_S = 0.12
_LOUDNESS_FAST_S = 1.0
_LOUDNESS_SLOW_S = 8.0


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
    coasting: bool = False  # running on the last known tempo, beat not currently detected
    source: str = ""  # tier running the grid: "beat", "flow", "free"; "" = no grid

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
        coast_bars: int = 8,
        fallback: bool = False,
    ):
        self.sensitivity = sensitivity
        self.min_interval_ms = min_interval_ms
        self.min_energy = min_energy
        self.history_seconds = history_seconds
        self.tempo_lock = tempo_lock
        self.lead_ms = lead_ms
        self.beats_per_bar = beats_per_bar
        self.accent_ratio = accent_ratio
        self.coast_bars = coast_bars
        self.fallback = fallback
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
        # Per tier: its latest unconfirmed tempo estimate and how many in a row agreed (see _confirmed).
        self._pending: Dict[str, Tuple[float, int]] = {}
        self._lock_hits = 0  # confirmed tempo updates since the grid last (re)locked
        self._coast_since: Optional[float] = None  # when the current run of tempo misses started
        self._last_beat_time: Optional[float] = None  # real (grid) time of the last emitted beat
        self._last_beat_aligned = 0
        self._emitted_grid_index: Optional[int] = None
        self._beat_count = 0
        self._slot_strength = [0.0] * max(1, self.beats_per_bar)
        self._downbeat_slot = 0
        self._recent_strengths: Deque[float] = collections.deque(maxlen=16)
        self._source = ""  # see ClockBeat.source
        self._flow_misses = 0
        self._flux_history: Deque[Tuple[float, float]] = collections.deque()  # (t, flux) for the rolling average
        self._flux_envelope: Deque[Tuple[float, float]] = collections.deque()  # (t, flux onset strength)
        self._flux_onsets: Deque[float] = collections.deque()  # times, for the free tier's note density
        self._last_flux_onset: Optional[float] = None
        self._flux_strengths: Deque[float] = collections.deque(maxlen=16)
        self._have_flux = False
        self._loud_fast: Optional[float] = None
        self._loud_slow: Optional[float] = None
        self._last_loudness_t: Optional[float] = None
        self.latest = ClockBeat()

    # -- public state ------------------------------------------------------------------

    @property
    def period_s(self) -> Optional[float]:
        return self._period

    @property
    def locked(self) -> bool:
        return self.tempo_lock and self._period is not None and self._grid_anchor is not None

    @property
    def coasting(self) -> bool:
        return self.locked and self._source == "beat" and self._tempo_misses >= _LOCK_LOSS_UPDATES

    @property
    def source(self) -> str:
        return self._source if self.locked else ""

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

    def update(
        self, energy: float, now_s: float, flux: Optional[float] = None, loudness: Optional[float] = None
    ) -> ClockBeat:
        """Feed one energy sample (the caller's band energy, roughly 0..1)
        at wall-clock time `now_s`. Returns what happened on this tick.

        `flux` (SpectralFeatures' spectral flux) and `loudness` (broadband
        level, 0..1) feed the fallback tiers; without them, or with
        `fallback` off, the clock only ever follows the beat band."""
        onset, strength = self._detect_onset(energy, now_s)
        if energy >= self.min_energy or (loudness is not None and loudness >= self.min_energy):
            self._last_loud = now_s
        flux_onset, flux_strength = False, 0.0
        if flux is not None:
            self._have_flux = True
            flux_onset, flux_strength = self._detect_flux_onset(flux, now_s, loudness)
        if loudness is not None:
            self._track_loudness(loudness, now_s)
        self._update_tempo(now_s)

        if not self.locked:
            return self._emit(now_s, strength, predicted=False) if onset else self._idle()

        period = self._period
        lead = max(0.0, self.lead_ms) / 1000.0
        # Never two beats closer than half a period, whichever path emits them.
        spaced = self._last_emit is None or now_s - self._last_emit >= 0.5 * period
        tolerance, phase_gain = _ON_BEAT_TOLERANCE, _PHASE_GAIN
        if self._source in ("flow", "free"):
            # The fallback tiers follow notes, not kicks.
            onset, strength = flux_onset, flux_strength
            tolerance = _FLOW_TOLERANCE
            phase_gain = _FLOW_PHASE_GAIN if self._source == "flow" else _FREE_PHASE_GAIN

        if onset:
            index = self._grid_index(now_s)
            error = now_s - (self._grid_anchor + index * period)
            if abs(error) <= tolerance * period:
                # On-beat onset: pull the grid toward it (a simple phase-locked loop).
                self._grid_anchor += phase_gain * error
                if spaced and (self._emitted_grid_index is None or index > self._emitted_grid_index):
                    self._emitted_grid_index = index
                    return self._emit(now_s, strength, predicted=False, grid_time=self._grid_anchor + index * period)
                # Already emitted early (lead) for this grid beat - just learn how hard it hit.
                self._learn_strength((self._beat_count - 1) % len(self._slot_strength), strength)
            elif self._source == "free" and spaced and self._is_strong_flux(strength):
                # Free pace: a strong note well into the beat starts the next
                # beat right here, so the pulse still answers the music.
                self._grid_anchor = now_s
                self._emitted_grid_index = 0
                return self._emit(now_s, strength, predicted=False, grid_time=now_s)
            # Off-beat onsets (hi-hats, off-beat snares, vocals) are ignored.
            return self._idle()

        # Scheduled beat: the next grid beat, emitted `lead` early; with no
        # lead, a fill-in once the grid beat has clearly passed with no onset.
        music_playing = self._last_loud is not None and now_s - self._last_loud < 2.0 * period
        next_index = self._grid_index(now_s + lead - tolerance * period)
        next_time = self._grid_anchor + next_index * period
        if lead > 0.0:
            due = now_s >= next_time - lead
        else:
            due = now_s >= next_time + tolerance * period
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
            self._source = ""
            return
        if self._last_tempo_update is not None and now_s - self._last_tempo_update < _TEMPO_UPDATE_S:
            return
        self._last_tempo_update = now_s

        period = self._estimate_period(self._envelope)
        music_playing = self._last_loud is not None and now_s - self._last_loud < 2.0
        if period is not None and music_playing and self._confirmed(period, "beat"):
            self._tempo_misses = 0
            self._coast_since = None
            self._accept_tempo(now_s, period, "beat", self._envelope)
            return
        # No tempo, or one the previous estimate doesn't agree with: a miss.
        if self._tempo_misses == 0:
            self._coast_since = now_s
        self._tempo_misses += 1
        if not music_playing:
            if self._tempo_misses >= _LOCK_LOSS_UPDATES:
                self._grid_anchor = None
                self._period = None
                self._source = ""
            return
        lost = self._tempo_misses >= _LOCK_LOSS_UPDATES and not (self._source == "beat" and self._may_coast(now_s))
        if self.fallback and self._have_flux and (self._source in ("flow", "free") or lost):
            self._fallback_tempo(now_s)
        elif lost:
            self._grid_anchor = None  # lost the beat: fall back to reacting to raw onsets
            self._source = ""

    def _confirmed(self, period: Optional[float], source: str) -> bool:
        """Whether tier `source`'s estimate `period` may steer the grid: it
        agrees with the grid that tier is already running, or enough of the
        tier's estimates in a row agree on it. A grid only starts on, or
        moves to, a tempo _CONFIRM_UPDATES estimates in a row agree on - one
        odd estimate (a fill, the window's edge, a chance peak in music with
        no beat at all) must neither throw a running beat off nor lock onto a
        tempo that isn't there - and takes over from a lower tier's running
        grid only after _TAKEOVER_UPDATES, so the tiers don't flip back and
        forth. None (no estimate) forgets the tier's earlier ones."""
        previous = self._pending.pop(source, None)
        if period is None:
            return False
        if (
            self._source == source
            and self._grid_anchor is not None
            and self._period is not None
            and abs(period - self._period) / self._period <= _RETEMPO_TOLERANCE
        ):
            return True
        agreeing = 1
        if previous is not None and abs(period - previous[0]) / previous[0] <= _RETEMPO_TOLERANCE:
            agreeing = previous[1] + 1
        tiers = ("beat", "flow", "free")
        takeover = (
            self._grid_anchor is not None
            and self._source in tiers
            and tiers.index(self._source) > tiers.index(source)
        )
        if agreeing >= (_TAKEOVER_UPDATES if takeover else _CONFIRM_UPDATES):
            return True
        self._pending[source] = (period, agreeing)
        return False

    def _accept_tempo(self, now_s: float, period: float, source: str, envelope) -> None:
        """Moves the grid to a confirmed `period` of tier `source`, its phase
        from `envelope`."""
        relock = (
            self._grid_anchor is None
            or source != self._source
            or self._period is None
            or abs(period - self._period) / self._period > _RETEMPO_TOLERANCE
        )
        if relock:
            self._period = period
            self._source = source
            self._lock_hits = 1
        else:
            gain = _FLOW_TEMPO_GAIN if source == "flow" else 0.2
            self._period += gain * (period - self._period)
            self._lock_hits += 1

        anchor = self._estimate_phase(now_s, self._period, envelope)
        if anchor is None:
            return
        if relock or self._grid_anchor is None:
            self._grid_anchor = anchor
            # Treat grid beats already in the past as done; the next one is scheduled normally.
            self._emitted_grid_index = int(math.floor((now_s - anchor) / self._period + 1e-9))
            if self.lead_ms <= 0.0:
                self._emitted_grid_index -= 1  # the latest one may still be answered by its onset
        else:
            # Gently steer the grid toward where the whole recent envelope says the beats are.
            k = round((anchor - self._grid_anchor) / self._period)
            self._grid_anchor += _COMB_GAIN * (anchor - (self._grid_anchor + k * self._period))

    def _fallback_tempo(self, now_s: float) -> None:
        """The beat band has no tempo (see Fallback in the module docstring):
        follow the notes' tempo ("flow") if they have one, else a free pace."""
        period = self._estimate_period(
            self._flux_envelope,
            min_period=_FLOW_MIN_PERIOD_S,
            max_period=_FLOW_MAX_PERIOD_S,
            prior_period=_FLOW_PRIOR_PERIOD_S,
            prior_octaves=_FLOW_PRIOR_OCTAVES,
            min_ac=_FLOW_MIN_AC,
        )
        if self._confirmed(period, "flow"):
            self._flow_misses = 0
            self._accept_tempo(now_s, period, "flow", self._flux_envelope)
            if self._source == "flow" and self._grid_anchor is not None:
                return
            # Too little envelope for a phase yet: the free pace goes on meanwhile.
        elif self._source == "flow":
            self._flow_misses += 1
            if self._flow_misses < _LOCK_LOSS_UPDATES:
                return  # a short gap in the notes' periodicity: keep the flow grid
        self._free_tempo(now_s)

    def _free_tempo(self, now_s: float) -> None:
        target = self._free_target_period(now_s)
        if self._grid_anchor is None or self._period is None:
            # Start the free pace with a beat right away.
            self._period = target
            self._grid_anchor = now_s
            self._emitted_grid_index = -1
        else:
            if self._last_beat_time is not None and self._last_beat_time >= self._grid_anchor - 1e-9:
                # Re-anchor on the last beat, so a new pace only moves the beats still to come.
                self._grid_anchor = self._last_beat_time
                self._emitted_grid_index = 0
            self._period += _FREE_TEMPO_GAIN * (target - self._period)
        self._source = "free"

    def _free_target_period(self, now_s: float) -> float:
        """The free tier's pace: from the note density (more notes, faster
        beats), sped up while the music swells and slowed while it calms."""
        while self._flux_onsets and self._flux_onsets[0] < now_s - _FREE_DENSITY_WINDOW_S:
            self._flux_onsets.popleft()
        if len(self._flux_onsets) >= 2:
            rate = len(self._flux_onsets) / _FREE_DENSITY_WINDOW_S
            period = _FREE_NOTES_PER_BEAT / rate
        else:
            period = self._period or _FREE_DEFAULT_PERIOD_S
        if self._loud_fast is not None and self._loud_slow is not None:
            swell = 2.0 ** (_FREE_SWELL_OCTAVES_PER_UNIT * (self._loud_fast - self._loud_slow))
            period /= min(_FREE_MAX_SWELL, max(1.0 / _FREE_MAX_SWELL, swell))
        return min(_FREE_MAX_PERIOD_S, max(_FREE_MIN_PERIOD_S, period))

    def _may_coast(self, now_s: float) -> bool:
        """Whether the grid may keep running on the last tempo (see Coasting
        in the module docstring)."""
        if self._grid_anchor is None or self._period is None or self._coast_since is None:
            return False
        if self._lock_hits < _COAST_MIN_HITS:
            return False
        limit = max(0, int(self.coast_bars)) * max(1, int(self.beats_per_bar)) * self._period
        return now_s - self._coast_since < limit

    def _estimate_phase(self, now_s: float, period: float, envelope) -> Optional[float]:
        """Where the beats fall, given the period: the phase whose comb of
        beat times best lines up with the recent onset `envelope` (recent
        samples weighted more). Returns a grid beat time <= now."""
        window = min(_TEMPO_WINDOW_S, 4.0 * period)
        samples = [(t, v) for t, v in envelope if t >= now_s - window]
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

    def _estimate_period(
        self,
        envelope,
        min_period: float = _MIN_PERIOD_S,
        max_period: float = _MAX_PERIOD_S,
        prior_period: float = 0.5,
        prior_octaves: float = 0.9,
        min_ac: float = _MIN_AC,
    ) -> Optional[float]:
        """The onset `envelope`'s dominant period within [min_period,
        max_period], or None without a clear periodicity."""
        if len(envelope) < 20:
            return None
        times = np.fromiter((t for t, _ in envelope), float)
        span = times[-1] - times[0]
        if span < max(3.0, 2.0 * max_period):
            return None
        dt = span / (len(times) - 1)
        x = np.fromiter((v for _, v in envelope), float)
        x = x - x.mean()
        energy = float(np.dot(x, x))
        if energy <= 1e-12:
            return None
        # Only a window that's still active at its recent end says anything
        # about the current tempo: when the beat stops, the last few old
        # kicks sliding out of the window produce junk periodicities.
        raw = np.fromiter((v for _, v in envelope), float)
        recent = raw[times >= times[-1] - _RECENT_ACTIVITY_S]
        if recent.mean() < _RECENT_ACTIVITY_SHARE * raw.mean():
            return None
        lo = max(1, int(min_period / dt))
        hi = min(len(x) - 2, int(math.ceil(max_period / dt)))
        if hi <= lo:
            return None
        # One lag either side of the range, so a peak right at its edge can be told from a slope.
        lags = np.arange(lo - 1, hi + 2)
        ac = np.array([np.dot(x[:-k], x[k:]) if k > 0 else energy for k in lags]) / energy
        # Only real peaks of the autocorrelation are tempo candidates. Long,
        # slowly decaying notes (piano, strings, bass lines) make the onset
        # envelope broad, and its autocorrelation then just slopes down from
        # the shortest lag - picking the best point on that slope locked the
        # clock onto a junk tempo at the edge of the range on any music
        # without drums.
        peaks = [i for i in range(1, len(ac) - 1) if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]]
        if not peaks:
            return None
        peaks = np.array(peaks)
        # Mild log-Gaussian preference around the prior tempo (120 BPM for
        # the beat band) to avoid locking to double/half tempo.
        prior = np.exp(-0.5 * (np.log2((lags[peaks] * dt) / prior_period) / prior_octaves) ** 2)
        best = int(peaks[int(np.argmax(ac[peaks] * prior))])
        if ac[best] < min_ac:
            return None  # no clear periodicity
        lag = float(lags[best])
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        denom = a - 2 * b + c
        if denom < -1e-12:  # parabolic interpolation for sub-sample precision
            lag += max(-0.5, min(0.5, 0.5 * (a - c) / denom))
        return lag * dt

    def _detect_flux_onset(self, flux: float, now_s: float, loudness: Optional[float]) -> Tuple[bool, float]:
        """A note onset: spectral flux well above its recent average (the
        fallback tiers' counterpart of _detect_onset)."""
        self._flux_history.append((now_s, flux))
        cutoff = now_s - _FLUX_HISTORY_S
        while self._flux_history and self._flux_history[0][0] < cutoff:
            self._flux_history.popleft()
        avg = sum(f for _, f in self._flux_history) / len(self._flux_history)

        self._flux_envelope.append((now_s, max(0.0, flux - avg)))
        env_cutoff = now_s - _FLOW_WINDOW_S
        while self._flux_envelope and self._flux_envelope[0][0] < env_cutoff:
            self._flux_envelope.popleft()

        if len(self._flux_history) < 4 or avg <= 1e-9:
            return False, 0.0
        strength = flux / avg
        refractory = _FLUX_MIN_INTERVAL_S
        if self._period is not None:
            refractory = max(refractory, 0.3 * self._period)
        audible = loudness is None or loudness >= self.min_energy
        onset = (
            audible
            and strength > _FLUX_SENSITIVITY
            and (self._last_flux_onset is None or now_s - self._last_flux_onset >= refractory)
        )
        if onset:
            self._last_flux_onset = now_s
            self._flux_onsets.append(now_s)
            self._flux_strengths.append(strength)
        return onset, strength

    def _is_strong_flux(self, strength: float) -> bool:
        """A note onset among the hardest `accent_ratio` of the recent ones."""
        if len(self._flux_strengths) < 4:
            return False
        ranked = sorted(self._flux_strengths)
        top = max(1, int(math.ceil(len(ranked) * max(0.0, min(1.0, self.accent_ratio)))))
        return strength >= ranked[-top]

    def _track_loudness(self, loudness: float, now_s: float) -> None:
        """Fast and slow averages of the broadband level: fast above slow =
        the music is swelling (crescendo), below = calming down."""
        if self._loud_fast is None or self._loud_slow is None or self._last_loudness_t is None:
            self._loud_fast = self._loud_slow = loudness
        else:
            dt = max(0.0, now_s - self._last_loudness_t)
            self._loud_fast += (1.0 - math.exp(-dt / _LOUDNESS_FAST_S)) * (loudness - self._loud_fast)
            self._loud_slow += (1.0 - math.exp(-dt / _LOUDNESS_SLOW_S)) * (loudness - self._loud_slow)
        self._last_loudness_t = now_s

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
            coasting=self.coasting,
            source=self.source,
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
            coasting=self.coasting,
            source=self.source,
        )


def beat_divides(beat: ClockBeat, every_n: int) -> bool:
    """True if `beat` is a beat AND lands on an every-`every_n`-beats step,
    counted from the bar start - so e.g. every_n=4 in 4/4 fires on the
    downbeats, every_n=2 on beats 1 and 3."""
    if not beat.is_beat:
        return False
    return beat.aligned_index % max(1, int(every_n)) == 0
