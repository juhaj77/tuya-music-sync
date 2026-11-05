"""Color Steps mode: the music walks the hue around the colour wheel, one step
at a time, and each new colour rolls across the lamps as a wave.

Beat Sync flashes on the kick and Band Colors gives each band a colour of its
own. Here no colour belongs to anything: the bands push the hue along, so
where it goes depends on what has been playing. (It replaced HSV Music, whose
hue followed the spectral centroid - on real music that barely moved: 13-40
degrees between the 5th and 95th percentile, depending on the song.)

- Band peaks charge the next step. Each band's level is watched for rises
  from the dip before them, measured against the band's own strongest rises
  lately (see _BandPeaks), so a band's ordinary hit counts as a full hit in any
  mix. A peak's strength, squared, times the band's influence (Bass / Mid /
  Treble influence) adds to the charge, which leaks away slowly.
- The beat clock releases it. Once the charge reaches Hits per step, the step
  waits for the next line of the clock's grid (Step on: 16ths .. bars) and
  goes out there - or just after it, when the hit that fills the charge lands
  right behind the line. Without a locked clock, or with Step on "peak", it
  goes out as soon as the charge is there. No peaks, no steps: the clock alone
  never moves the colour.
- How far: between Step min and Step max, further the denser and the more
  bass-heavy the peaks are against the song's own recent range, and the more of
  the charge came from the low end - a kick moves the colour further than a
  hi-hat. Mostly short steps between neighbouring colours, the long ones saved
  for the strong moments. A step on a bar's first beat gets Bar line boost on
  top, so the colour jumps where the bar turns. Steps aren't exactly equal, so a
  steady beat doesn't settle on a loop of the same few colours.
- Which way: forward, until the balance of the peaks shifts clearly toward the
  treble compared with the last while; then back, until it shifts toward the
  bass again (Direction "bands"). So a section of the song tends to keep going
  one way round the wheel, and the next one can turn it back.
- How fast: Hue snap speed - low = a snap, high = a slow fade. The walk isn't
  wrapped at 360 degrees, so even a very slow hue follows the way the walk
  went instead of cutting back across the wheel when it falls behind.
- Between the steps the hue keeps gliding the same way (Glide, degrees per
  beat), so the colour is never quite still while the music plays.
- Across the lamps: a step reaches the positions of the Chase order one after
  the other, over Wave length beats, running the way the hue stepped, and lifts
  each lamp's brightness as it passes (Crest). Hue spread fans the lamps out
  over that many degrees around the walk.

Pure logic: band levels and the clock's position in, per-position hues and
brightness levels out. The engine reads the levels from the spectrum and shows
the result (VisualizationEngine._tick_color_steps_mode); what plays on top -
Chase, Group Switch, the pulse sequencer - takes it like any other mode's colour.
"""
from __future__ import annotations

import collections
import math
import random
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence

from ..dsp.hue_step import AdaptiveRange
from ..dsp.smoothing import AttackReleaseSmoother
from .band_colors import BANDS

_TROUGH_S = 0.25  # how fast the dip before a rise is forgotten: longer than a hit's rise, shorter than a beat
# A band's level averages dB over the whole band, which dilutes a sharp hit:
# on real music the rises' 99th percentile is only 0.08-0.28 (7-25 dB).
_PEAK_FLOOR = 0.02  # a rise (dB-scaled level, ~1.8 dB) below which it's no peak: held notes, pads, noise
_PEAK_FULL_MIN = 0.05  # ~4.5 dB: a full-strength peak rises at least this much...
_PEAK_FULL_SHARE = 0.7  # ...and this share of the band's strongest rises lately
_PEAK_MEMORY_S = 10.0
_REARM = 0.5  # a peak is over when its strength has fallen to this share; the next rise counts anew
_PRESENT = (0.05, 0.15)  # a band's level: silent (~-85 dB) .. there (~-77 dB), as in Band Colors
_SILENCE = (0.1, 0.2)  # the loudest band's level: silence (~-81 dB) .. music (~-72 dB)

_LEAK_S = 8.0  # how fast the charge leaks away
_SLACK = 0.9  # a charge this close to Hits per step will do
_MIN_GAP_S = 0.1  # between steps going out at once on the peaks
_WINDOW = (0.25, 0.04, 0.15)  # a grid line stays open this share of its length, within these seconds

_FLOW_S = 3.0  # the peaks' rate per band is averaged over this long...
_DENSITY_S = 1.0  # ...density (the hits in that) with each hit's own jump smoothed out, as a step
# follows right after a hit and would always read the top of it...
_DENSITY_MIN_SPAN = 1.5  # ...and steadier than this reads mid-range
_BALANCE_MIN_SPAN = 0.4  # bass-vs-treble (-1..1) steadier than this reads mid-range: no turns
_RANGE_MEMORY_S = 10.0  # density's and balance's "lately"
_TURN = (0.15, 0.85)  # balance against its range: below turns the walk back, above forward
_MIN_TURN_GAP_S = 4.0  # the walk keeps a direction at least this long
# How far a step goes, 0..1: how dense the peaks are and how bass-heavy, against
# the song's recent range, and which bands charged this step.
_SIZE_DENSITY, _SIZE_BALANCE, _SIZE_BANDS = 0.4, 0.3, 0.3
_LOW_END = (1.0, 0.5, 0.0)  # per band: how much a charge from it counts as "from the low end"
_SIZE_POWER = 3.0  # mostly short steps between neighbouring colours, the long ones for the strong moments
_JITTER = 0.15  # a step's size varies this share either way

_ACTIVITY_MIN_SPAN = 0.04  # brightness: a band against its own recent range, as in Band Colors
_ACTIVITY_MEMORY_S = 4.0
_CREST_MIN = 0.6  # the crest of the shortest step; the longest lifts a lamp fully
_DEFAULT_BEAT_S = 0.5  # the wave's and the glide's beat while the clock has no tempo yet


def _ramp(x: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (x - low) / (high - low)))


def grid_beats(step_on: str, beats_per_bar: int) -> Optional[float]:
    """The grid Step on puts the steps on, in beats - None = no grid, at once on the peaks."""
    if step_on == "bar":
        return float(max(1, beats_per_bar))
    return {"16th": 0.25, "8th": 0.5, "beat": 1.0, "2 beats": 2.0}.get(step_on)


class _BandPeaks:
    """One band's intensity peaks: a rise above the dip before it, 0..1 against
    the band's own strongest rises lately. update() returns what this tick adds
    to the charge: a peak's strength squared (a full peak 1, a half-strength one
    a quarter), paid out as the peak climbs - so it counts as soon as it lands,
    however many ticks the rise takes."""

    def __init__(self):
        self._trough: Optional[float] = None
        self._strongest = 0.0
        self._top = 0.0  # the strength the current peak has reached

    def update(self, level: float, dt: float) -> float:
        if self._trough is None or level < self._trough:
            self._trough = level
        else:
            self._trough += (1.0 - math.exp(-dt / _TROUGH_S)) * (level - self._trough)
        rise = level - self._trough
        self._strongest = max(rise, self._strongest * math.exp(-dt / _PEAK_MEMORY_S))
        full = max(_PEAK_FULL_MIN, _PEAK_FULL_SHARE * self._strongest)
        strength = _ramp(rise, _PEAK_FLOOR, full) * _ramp(level, *_PRESENT)
        if strength > self._top:
            gain = strength ** 2 - self._top ** 2
            self._top = strength
            return gain
        if strength < _REARM * self._top:
            self._top = strength
        return 0.0


@dataclass
class _Wave:
    """A step on its way across the lamps."""

    number: int
    at: float
    walk: float  # the hue it brings (not wrapped)
    length: float  # seconds from the first position to the last
    direction: float
    crest: float


@dataclass
class ColorStepsState:
    hues: List[float] = field(default_factory=lambda: [0.0])  # per position, degrees
    levels: List[float] = field(default_factory=lambda: [0.0])  # per position, 0..1 brightness level
    charges: Dict[str, float] = field(default_factory=dict)  # band -> hits it has charged since the last step
    charge: float = 0.0  # their sum
    steps: int = 0
    last_step: float = 0.0  # degrees, signed
    last_band: str = ""  # the band that charged the last step the most
    waiting: bool = False  # charged, waiting for the clock's next line


class ColorStepTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._peaks = [_BandPeaks() for _ in BANDS]
        self._charges = [0.0 for _ in BANDS]
        self._flows = [0.0 for _ in BANDS]
        self._density_range = AdaptiveRange(_DENSITY_MIN_SPAN, _RANGE_MEMORY_S)
        self._balance_range = AdaptiveRange(_BALANCE_MIN_SPAN, _RANGE_MEMORY_S)
        self._smooth_flow = AttackReleaseSmoother(_DENSITY_S * 1000.0, _DENSITY_S * 1000.0)
        self._density = 0.5
        self._balance = 0.5
        self._direction = 1.0
        self._turned_at = -1e9
        self._ranges = [AdaptiveRange(_ACTIVITY_MIN_SPAN, _ACTIVITY_MEMORY_S) for _ in BANDS]
        self._activities = [AttackReleaseSmoother(30.0, 400.0) for _ in BANDS]
        # The grid line last crossed, whether it was a bar line, and until when it's open.
        self._line: Optional[int] = None
        self._line_on_bar = False
        self._open_until = -1e9
        self._last_step_at = -1e9
        self._walk = random.uniform(0.0, 360.0)  # the hue target, not wrapped (see the module docstring)
        self._glide = 0.0
        self._waves: Deque[_Wave] = collections.deque()
        self._numbered = 0
        # Per position: what's reached it so far, and the hue on its way there.
        self._arrived: List[int] = []
        self._targets: List[float] = []
        self._hues: List[AttackReleaseSmoother] = []
        self._crests: List[float] = []
        self.latest = ColorStepsState()

    # -- the charge ----------------------------------------------------------------

    def _charge_up(self, levels: Sequence[float], influences: Sequence[float], dt: float) -> None:
        leak = math.exp(-dt / _LEAK_S)
        fade = math.exp(-dt / _FLOW_S)
        for i, (level, influence) in enumerate(zip(levels, influences)):
            gain = self._peaks[i].update(level, dt) * max(0.0, influence)
            self._charges[i] = self._charges[i] * leak + gain
            self._flows[i] = self._flows[i] * fade + gain

    def _track_flow(self, cfg, dt: float, now_s: float) -> None:
        """What the peaks have been lately: how dense, and bass or treble."""
        total = sum(self._flows)
        self._density = self._density_range.update(self._smooth_flow.update(total, dt), dt)
        if total < 1e-3:
            return
        self._balance = self._balance_range.update((self._flows[0] - self._flows[2]) / total, dt)
        if cfg.direction != "bands":
            self._direction = 1.0
        elif now_s - self._turned_at >= _MIN_TURN_GAP_S:
            direction = -1.0 if self._balance < _TURN[0] else 1.0 if self._balance > _TURN[1] else self._direction
            if direction != self._direction:
                self._direction, self._turned_at = direction, now_s

    # -- the clock -------------------------------------------------------------------

    def _may_step(self, cfg, now_s: float, position: Optional[float], beat_s: float, beats_per_bar: int) -> bool:
        """Whether a step may go out this tick: just after a line of the
        clock's grid - or any time, with no grid to wait for."""
        grid = grid_beats(cfg.step_on, beats_per_bar)
        if grid is None or position is None:
            self._line = None
            self._line_on_bar = False
            return now_s - self._last_step_at >= _MIN_GAP_S
        line = math.floor(position / grid)
        if self._line is None or line < self._line:
            self._line = line  # the first tick on the grid, or it jumped back: wait for the next line
        elif line > self._line:
            self._line = line
            self._line_on_bar = (line * grid) % max(1, beats_per_bar) == 0.0
            share, shortest, longest = _WINDOW
            self._open_until = now_s + min(longest, max(shortest, share * grid * beat_s))
        return now_s <= self._open_until

    # -- a step ----------------------------------------------------------------------

    def _step(self, cfg, now_s: float, beat_s: float) -> None:
        total = sum(self._charges)
        low_end = sum(c * weight for c, weight in zip(self._charges, _LOW_END)) / total
        x = _SIZE_DENSITY * self._density + _SIZE_BALANCE * self._balance + _SIZE_BANDS * low_end
        size = cfg.step_min_deg + (cfg.step_max_deg - cfg.step_min_deg) * x ** _SIZE_POWER
        size *= random.uniform(1.0 - _JITTER, 1.0 + _JITTER)
        if self._line_on_bar:
            size += cfg.bar_boost_deg
        step = self._direction * size
        self._walk += step
        self._numbered += 1
        self._waves.append(_Wave(
            self._numbered, now_s, self._walk, max(0.0, cfg.wave_beats) * beat_s, self._direction,
            _CREST_MIN + (1.0 - _CREST_MIN) * x,
        ))
        strongest = max(range(len(BANDS)), key=lambda i: self._charges[i])
        self.latest.steps += 1
        self.latest.last_step = step
        self.latest.last_band = BANDS[strongest]
        self._charges = [0.0 for _ in BANDS]
        self._last_step_at = now_s
        self._open_until = -1e9  # one step per line

    # -- what the lamps show ---------------------------------------------------------

    def _resize(self, positions: int) -> None:
        if len(self._hues) == positions:
            return
        hue = self._hues[0].value if self._hues else self._walk
        self._arrived = [self._numbered for _ in range(positions)]
        self._targets = [self._walk for _ in range(positions)]
        self._hues = [AttackReleaseSmoother(initial=hue) for _ in range(positions)]
        self._crests = [0.0 for _ in range(positions)]

    def _move_waves(self, cfg, dt: float, now_s: float) -> None:
        n = len(self._hues)
        fade = math.exp(-dt / max(cfg.crest_release_ms / 1000.0, 1e-3))
        self._crests = [crest * fade for crest in self._crests]
        for wave in self._waves:
            for i in range(n):
                along = i / (n - 1) if n > 1 else 0.0
                lag = wave.length * (along if wave.direction > 0.0 else 1.0 - along)
                # (A wave running the other way can overtake an older one: the newer hue stays.)
                if wave.number > self._arrived[i] and now_s >= wave.at + lag:
                    self._arrived[i] = wave.number
                    self._targets[i] = wave.walk
                    self._crests[i] = max(self._crests[i], wave.crest)
        while self._waves and now_s >= self._waves[0].at + self._waves[0].length:
            self._waves.popleft()

    def _breath(self, levels: Sequence[float], influences: Sequence[float], cfg, dt: float) -> float:
        """0..1: how far out the bands let in by their influence are, against their own recent range."""
        loudest = max(max(influences, default=0.0), 1e-6)
        breath = 0.0
        for level, influence, band_range, smoother in zip(levels, influences, self._ranges, self._activities):
            smoother.attack_ms, smoother.release_ms = cfg.attack_ms, cfg.release_ms
            activity = smoother.update(band_range.update(level, dt) * _ramp(level, *_PRESENT), dt)
            breath = max(breath, activity * max(0.0, influence) / loudest)
        return breath

    # -- main entry point ------------------------------------------------------------

    def update(
        self,
        levels: Sequence[float],
        cfg,
        dt: float,
        now_s: float,
        position: Optional[float] = None,
        period_s: Optional[float] = None,
        beats_per_bar: int = 4,
        positions: int = 1,
    ) -> ColorStepsState:
        """levels: the three bands' dB-scaled levels (bands.band_energy), bass
        first. cfg: a ColorStepsModeConfig. position: BeatClock.position(), None
        without a locked clock. period_s: seconds per beat, if known. positions:
        how many places along the wave (the Chase order's), at least 1."""
        dt = max(dt, 1e-6)
        beat_s = period_s if period_s else _DEFAULT_BEAT_S
        influences = (cfg.bass_influence, cfg.mid_influence, cfg.treble_influence)
        self._resize(max(1, positions))

        self._charge_up(levels, influences, dt)
        self._track_flow(cfg, dt, now_s)
        may_step = self._may_step(cfg, now_s, position, beat_s, beats_per_bar)
        charged = sum(self._charges) >= _SLACK * max(cfg.hits_per_step, 1e-3)
        if charged and may_step:
            self._step(cfg, now_s, beat_s)
            charged = False

        self._move_waves(cfg, dt, now_s)
        gate = _ramp(max(levels, default=0.0), *_SILENCE)
        self._glide += self._direction * cfg.glide_deg_per_beat * gate * dt / beat_s
        breath = self._breath(levels, influences, cfg, dt) * gate

        n = len(self._hues)
        crest_share = min(1.0, max(0.0, cfg.crest))
        hues, brightness = [], []
        for i in range(n):
            smoother = self._hues[i]
            smoother.attack_ms = smoother.release_ms = cfg.hue_attack_ms
            spread = cfg.spread_deg * (i / (n - 1) - 0.5) if n > 1 else 0.0
            hues.append((smoother.update(self._targets[i], dt) + self._glide + spread) % 360.0)
            brightness.append(breath * (1.0 - crest_share) + self._crests[i] * crest_share)

        state = self.latest
        state.hues, state.levels = hues, brightness
        state.charges = dict(zip(BANDS, self._charges))
        state.charge = sum(self._charges)
        state.waiting = charged
        return state
