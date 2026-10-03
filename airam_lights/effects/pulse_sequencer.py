"""Pulse sequencer: places true-white flashes and dark "breaths" on musical
positions, the way a lighting operator or drummer would, instead of rolling
a probability on every detected beat.

It reads the shared beat clock's position (beats counted from a bar start,
see BeatClock.position()) and works on a 16th-note grid, so pulses can land
between beats - on the "and", on syncopations - not only on the beats
themselves. On top of that grid:

- Patterns: which 16ths of the bar get a white flash (downbeats, every beat,
  off-beats, a 3-3-2 syncopation, a gallop, straight 16ths) and which get a
  dark pulse (a breath right before the downbeat, before the phrase, before
  the backbeats).
- Thinning: the white flashes can be thinned out the way a drummer
  would - the light 16ths first, the downbeat last (white_accent_focus),
  sparser at the start of a phrase and filling in toward its end
  (white_build), and with the choice of steps repeated bar after bar like
  a groove instead of rolled anew every time (white_repeat).
- Group walk: every white flash goes to the next lamp group (forward,
  ping-pong or random), so the white "dances" around the room; with
  `double_chance` a group sometimes gets a second flash an 8th later.
- Phrases: bars are grouped into phrases of `phrase_bars` (4 or 8 - how
  most pop/dance music is built). The last half bar of a phrase is a fill
  (flashes get denser), followed by a dark breath and a big all-groups
  flash on the phrase's first beat: tension, then release.
- Dynamics: the music's loudness relative to the last ~30 s (a percentile,
  so it works at any volume) picks the "auto" patterns - sparse in quiet
  parts, busy in the loud ones - and a sudden jump from quiet to loud (a
  drop) re-aligns the phrase to start right there, with the big flash.
- Strobe (optional): now and then the end of a bar - half a beat up to the
  whole bar - becomes a burst of fast cool-white flashes on every lamp at
  once: a roll into the next phrase or bar. Rare by default: only on the
  chosen bars, only when the music is loud enough, with a chance roll and a
  minimum number of bars between two of them - all adjustable up to twice
  per bar. The flash rate is a subdivision of the beat (see strobe_plan),
  so the roll sits on the grid at any tempo.

Pure logic: returns events, never touches lamps. The engine turns events
into lamp commands (see VisualizationEngine._apply_sequencer_events).
"""
from __future__ import annotations

import collections
import math
import random
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Set

from ..config.schema import PulseSequencerConfig

STEPS_PER_BEAT = 4  # 16th notes

# White patterns as sets of 16th positions within one beat (sub) or within
# the bar (bar steps) - resolved per bar length in _white_steps().
WHITE_PATTERNS = ("off", "auto", "downbeats", "beats", "offbeats", "syncopated", "gallop", "sixteenths")
DARK_PATTERNS = ("off", "auto", "before_downbeat", "before_phrase", "before_backbeats", "stutter")
GROUP_WALKS = ("forward", "pingpong", "random", "all")
# Where a strobe may end: on the bar line into a new phrase; also into the
# phrase's second half; on any bar line; also at the middle of each bar.
STROBE_PLACEMENTS = ("phrase", "half_phrase", "bars", "half_bars")
# Loudness levels, quietest first (see LoudnessTracker.level).
LEVELS = ("calm", "groove", "high", "peak")
# Flashes per beat a strobe may use: 8ths, 8th triplets, 16ths, 16th
# triplets, 32nds - the fastest that fits under the ceiling is picked.
STROBE_SUBDIVISIONS = (2, 3, 4, 6, 8)

# "auto" picks by how loud the music is right now compared to the last ~30 s.
_AUTO_WHITE = {"calm": "downbeats", "groove": "beats", "high": "syncopated", "peak": "gallop"}
_AUTO_DARK = {
    "calm": ("before_phrase", "before_downbeat"),
    "groove": ("before_downbeat", "before_backbeats"),
    "high": ("before_downbeat", "before_backbeats", "stutter"),
    "peak": ("before_downbeat", "before_backbeats", "stutter"),
}

_INTENSITY_WINDOW_S = 30.0
_SHORT_TIME_CONSTANT_S = 0.6


@dataclass
class PulseEvent:
    kind: str  # "white" | "dark" | "strobe"
    group: Optional[int] = None  # index into the lamp groups; None = every lamp
    # White/dark: multiplier on the configured pulse duration. Strobe: the
    # burst's length in beats.
    length: float = 1.0
    reason: str = ""  # for diagnostics/tests: "pattern", "double", "fill", "phrase", "drop", ...
    # Where in the music it happens (e.g. for the warm/cool choice of a white flash).
    step: int = 0  # 16th on the clock's grid (BeatClock.position() * STEPS_PER_BEAT)
    # Beats from `step` until it actually starts. A strobe is announced one
    # 16th early, so every lamp gets the whole burst in advance and they all
    # start it at the same instant, on the grid.
    lead: float = 0.0
    bar_step: int = 0  # 16th within the bar, 0 = the downbeat
    steps_per_bar: int = 16
    phrase_bar: int = 0  # bar within the phrase, 0 = the phrase's first bar
    phrase_bars: int = 1
    # Shape of a white flash relative to the white pulse settings (see
    # PulseSequencerConfig.pulse_dynamics): brightness, attack, hold (the
    # pulse's duration) and release multipliers.
    brightness: float = 1.0
    attack: float = 1.0
    hold: float = 1.0
    release: float = 1.0


def metric_weight(bar_step: int, steps_per_bar: int) -> float:
    """How heavy a position in the bar is, 1.0 (the downbeat) .. 0.0 (a
    16th between beats): the downbeat, then the bar's middle beat (beat 3 in
    4/4), the other beats, the 8th-note "ands", the 16ths - the metric
    hierarchy a drummer accents by."""
    beats_per_bar = max(1, steps_per_bar // STEPS_PER_BEAT)
    if bar_step == 0:
        return 1.0
    beat, sub = divmod(bar_step, STEPS_PER_BEAT)
    if sub == 0:
        return 0.65 if beats_per_bar % 2 == 0 and beat == beats_per_bar // 2 else 0.35
    if sub == STEPS_PER_BEAT // 2:
        return 0.15
    return 0.0


def _blend(amount: float, value: float) -> float:
    """1.0 (no variation) .. `value` (full variation)."""
    return 1.0 + (value - 1.0) * amount


def flash_shape(reason: str, bar_step: int, steps_per_bar: int, intensity: float, amount: float) -> tuple:
    """(brightness, attack, hold, release) multipliers for a white flash:
    - the weight of its position in the bar: the downbeat long and bright, a
      16th between beats short, crisp and a little dimmer;
    - the loudness (0..1 percentile): quiet parts dimmer and softer (slower
      attack), loud parts full and sharp;
    - fills: short and snappy, getting brighter toward the phrase start;
    - the phrase start: full brightness, held longer, slow fade out."""
    amount = max(0.0, min(1.0, amount))
    if amount == 0.0:
        return 1.0, 1.0, 1.0, 1.0
    loud = max(0.0, min(1.0, intensity))
    attack = _blend(amount, 1.6 - 1.2 * loud)  # quiet 1.6x softer .. loud 0.4x sharper
    if reason == "phrase":
        return 1.0, attack, _blend(amount, 1.6), _blend(amount, 3.0)
    if reason == "fill":
        progress = bar_step / max(1, steps_per_bar - 1)  # the fill sits in the bar's second half
        return _blend(amount, 0.6 + 0.4 * progress), _blend(amount, 0.5), _blend(amount, 0.5), _blend(amount, 0.5)
    weight = metric_weight(bar_step, steps_per_bar)
    brightness = _blend(amount, (0.55 + 0.45 * weight) * (0.7 + 0.3 * loud))
    length = _blend(amount, 0.5 + 1.5 * weight)  # 16th 0.5x .. downbeat 2x
    return brightness, attack, length, length


def white_chance(
    density: float, weight: float, focus: float, build: float, phrase_bar: int, phrase_bars: int
) -> float:
    """Chance a white pattern step at metric `weight` (see metric_weight)
    actually flashes. `focus` 0..1 thins from the light end of the metric
    hierarchy: at 1 a step keeps only its weight's share of `density`, so the
    16ths drop out first and the downbeat stays. `build` 0..1 adds focus at
    the start of a phrase, fading out bar by bar toward its last bar - the
    phrase starts sparse and fills in, building up to the fill."""
    focus = max(0.0, min(1.0, focus))
    build = max(0.0, min(1.0, build))
    if build > 0.0 and phrase_bars > 1:
        remaining = 1.0 - phrase_bar / (phrase_bars - 1)
        focus += build * (1.0 - focus) * remaining
    return density * (1.0 - focus * (1.0 - weight))


def strobe_steps(beats: float, steps_per_bar: int) -> int:
    """A strobe's length on the 16th grid: half a beat at least, the whole bar at most."""
    return max(STEPS_PER_BEAT // 2, min(max(1, steps_per_bar), int(round(beats * STEPS_PER_BEAT))))


def strobe_plan(
    beat_period_s: Optional[float], beats: float, max_hz: float, duty: float = 0.5,
    command_rate_hz: Optional[float] = None,
) -> Optional[tuple]:
    """How a strobe lasting `beats` beats is played at this tempo: (number of
    flashes, seconds from one flash to the next, seconds the white is on in
    each) - or None when no musical rate fits.

    The rate is a subdivision of the beat (STROBE_SUBDIVISIONS) that fills
    the burst with a whole number of flashes: the fastest one not above
    `max_hz`, nor above what the lamp command rate allows - a flash is two
    commands (on, off), and neither the on nor the off part may be shorter
    than one command interval. At 128 BPM with the defaults that's 16ths:
    4 flashes in a beat, 8.5 per second."""
    if not beat_period_s or beat_period_s <= 0.0 or beats <= 0.0:
        return None
    duty = max(0.2, min(0.8, duty))
    ceiling = max_hz
    if command_rate_hz:
        ceiling = min(ceiling, command_rate_hz * min(duty, 1.0 - duty))
    best = None
    for per_beat in STROBE_SUBDIVISIONS:
        flashes = beats * per_beat
        whole = abs(flashes - round(flashes)) < 1e-6 and round(flashes) >= 2
        if whole and per_beat / beat_period_s <= ceiling + 1e-9:
            best = per_beat
    if best is None:
        return None
    period = beat_period_s / best
    return int(round(beats * best)), period, period * duty


def phrase_progress(phrase_bar: int, phrase_bars: int, bar_step: int, steps_per_bar: int) -> float:
    """0 at the start of a phrase .. ~1 at its very end - except the phrase's
    first step itself, which counts as the peak (1.0): the release of the
    tension the phrase built up."""
    if bar_step == 0 and phrase_bar == 0:
        return 1.0
    total = max(1, phrase_bars) * max(1, steps_per_bar)
    return (phrase_bar * steps_per_bar + bar_step) / total


class LoudnessTracker:
    """How loud the music is right now compared to the last ~30 s, as a
    percentile (0 = the quietest it's been, 1 = the loudest) - so it works
    at any playback volume. `level` names the range: calm/groove/high/peak."""

    def __init__(self):
        self._short: Optional[float] = None
        self._history: Deque[tuple] = collections.deque()
        self.intensity = 0.5
        self.level = "groove"

    def update(self, energy: float, now_s: float, dt: float) -> None:
        alpha = 1.0 - math.exp(-max(dt, 0.0) / _SHORT_TIME_CONSTANT_S)
        self._short = energy if self._short is None else self._short + alpha * (energy - self._short)
        self._history.append((now_s, self._short))
        while self._history and self._history[0][0] < now_s - _INTENSITY_WINDOW_S:
            self._history.popleft()
        if len(self._history) >= 10:
            below = sum(1 for _, e in self._history if e < self._short)
            self.intensity = below / len(self._history)
        self.level = _intensity_level(self.intensity)


def _intensity_level(intensity: float) -> str:
    if intensity < 0.3:
        return "calm"
    if intensity < 0.7:
        return "groove"
    if intensity < 0.9:
        return "high"
    return "peak"


class PulseSequencer:
    def __init__(self, config: PulseSequencerConfig, rng: Optional[random.Random] = None):
        self.config = config
        self._rng = rng or random.Random()
        self.reset()

    def reset(self) -> None:
        self._last_step: Optional[int] = None
        self._group_index = -1
        self._group_direction = 1
        self._pending_doubles: Dict[int, int] = {}  # absolute step -> group
        # With white_repeat: this phrase's rolls, kept so every bar repeats them.
        self._rolls: Dict[tuple, float] = {}
        # The 16ths [from, until) a strobe covers - no white flashes there.
        self._strobe_from_step = 0
        self._strobe_until_step = 0
        self._last_strobe_start: Optional[int] = None  # the 16th the previous strobe started on
        self._phrase_origin_bar = 0
        self._loudness = LoudnessTracker()
        self._bar_intensities: List[float] = []  # this bar's intensity samples, for drop detection
        self.intensity = 0.5
        self.level = "groove"
        self.phrase_bar = 0

    def update_config(self, config: PulseSequencerConfig) -> None:
        self.config = config

    # -- main entry point ---------------------------------------------------------------

    def tick(
        self, position: Optional[float], energy: float, now_s: float, dt: float, num_groups: int, beats_per_bar: int
    ) -> List[PulseEvent]:
        """position: BeatClock.position() (None when not locked -> no events).
        energy: broadband loudness level (0..1) for the dynamics. Returns the
        events for every 16th step crossed since the previous tick."""
        self._track_intensity(energy, now_s, dt)
        self._bar_intensities.append(self.intensity)
        if position is None:
            self._last_step = None
            return []
        step = int(math.floor(position * STEPS_PER_BEAT))
        if self._last_step is None or step < self._last_step or step - self._last_step > STEPS_PER_BEAT:
            # First tick, or the grid jumped (re-lock, bar start re-estimated): resync silently.
            self._last_step = step
            self._pending_doubles.clear()
            self._strobe_from_step = self._strobe_until_step = 0
            return []
        events: List[PulseEvent] = []
        for s in range(self._last_step + 1, step + 1):
            step_events = self._events_for_step(s, max(1, num_groups), max(1, beats_per_bar))
            steps_per_bar = max(1, beats_per_bar) * STEPS_PER_BEAT
            for e in step_events:
                e.step = s
                e.bar_step = s % steps_per_bar
                e.steps_per_bar = steps_per_bar
                e.phrase_bar = self.phrase_bar
                e.phrase_bars = max(1, int(self.config.phrase_bars))
            events.extend(step_events)
        self._last_step = step
        return events

    # -- dynamics ------------------------------------------------------------------------------

    def _track_intensity(self, energy: float, now_s: float, dt: float) -> None:
        self._loudness.update(energy, now_s, dt)
        self.intensity = self._loudness.intensity
        self.level = self._loudness.level

    # -- one 16th step -----------------------------------------------------------------------

    def _events_for_step(self, step: int, num_groups: int, bpb: int) -> List[PulseEvent]:
        cfg = self.config
        steps_per_bar = bpb * STEPS_PER_BEAT
        bar = step // steps_per_bar
        bar_step = step % steps_per_bar
        events: List[PulseEvent] = []

        if bar_step == 0:
            self._on_bar_start(bar)
        phrase_bars = max(1, int(cfg.phrase_bars))
        self.phrase_bar = (bar - self._phrase_origin_bar) % phrase_bars
        last_bar_of_phrase = self.phrase_bar == phrase_bars - 1
        phrase_start = self.phrase_bar == 0 and bar_step == 0
        in_fill = cfg.fills and last_bar_of_phrase and phrase_bars > 1 and bar_step >= steps_per_bar // 2
        if phrase_start:
            self._rolls.clear()  # a new phrase, a new variation of the groove

        # -- strobe (announced one 16th before it starts) ----------------------------
        strobe = self._strobe_for_step(step, steps_per_bar, phrase_bars)
        if strobe is not None:
            events.append(strobe)
        strobing = self._strobe_from_step <= step < self._strobe_until_step

        # -- white (the strobe has the white LEDs to itself while it runs) -------------
        white_group: Optional[int] = None
        white_reason = ""
        if cfg.white_pattern != "off" and not strobing:
            if phrase_start and cfg.phrase_accent:
                white_reason = "phrase"
            elif in_fill and self._fill_step(bar_step, steps_per_bar):
                white_reason = "fill"
            elif bar_step in self._white_steps(steps_per_bar) and self._roll("white", bar_step) < white_chance(
                cfg.white_density, metric_weight(bar_step, steps_per_bar), cfg.white_accent_focus,
                cfg.white_build, self.phrase_bar, phrase_bars,
            ):
                white_reason = "pattern"
            if not white_reason and step in self._pending_doubles:
                white_group = self._pending_doubles[step]
                white_reason = "double"
        self._pending_doubles.pop(step, None)
        if white_reason:
            if white_reason == "phrase" or cfg.group_walk == "all" or num_groups < 2:
                white_group = None
            elif white_reason != "double":
                white_group = self._next_group(num_groups)
                if white_reason == "pattern" and self._roll("double", bar_step) < cfg.double_chance:
                    self._pending_doubles[step + STEPS_PER_BEAT // 2] = white_group
            event = PulseEvent("white", white_group, 1.0, white_reason)
            if cfg.pulse_dynamics:
                event.brightness, event.attack, event.hold, event.release = flash_shape(
                    white_reason, bar_step, steps_per_bar, self.intensity, cfg.pulse_dynamics_amount
                )
            events.append(event)

        # -- dark (never on the same step as a white flash) --------------------------------
        if cfg.dark_pattern != "off" and not white_reason:
            dark = self._dark_for_step(bar_step, steps_per_bar, last_bar_of_phrase, in_fill)
            if dark is not None and (dark.reason == "phrase" or self._rng.random() < cfg.dark_density):
                events.append(dark)
        return events

    def _roll(self, kind: str, bar_step: int) -> float:
        """A random 0..1 for a decision at this step of the bar - with
        white_repeat the same one in every bar of the phrase."""
        if not self.config.white_repeat:
            return self._rng.random()
        key = (kind, bar_step)
        if key not in self._rolls:
            self._rolls[key] = self._rng.random()
        return self._rolls[key]

    def _on_bar_start(self, bar: int) -> None:
        # A drop: the bar that just ended was clearly quiet, now it's clearly
        # loud -> the phrase (and its big opening flash) starts right here.
        samples = self._bar_intensities
        ended = sum(samples) / len(samples) if samples else None
        if self.config.drop_detection and ended is not None and ended < 0.35 and self.intensity > 0.8:
            self._phrase_origin_bar = bar
        self._bar_intensities = []

    def _strobe_for_step(self, step: int, steps_per_bar: int, phrase_bars: int) -> Optional[PulseEvent]:
        """A strobe starting on the next 16th (it's decided one 16th early)
        and running up to a bar line - or, with placement "half_bars", up to
        the middle of the bar. Every condition has to hold: the spot is one
        the placement allows, the previous strobe is over and far enough
        back, the music is loud enough, and the chance roll comes up.

        A strobe as long as the bar starts on the downbeat, so it's decided
        on the last 16th of the bar before."""
        cfg = self.config
        if not cfg.strobe_enabled:
            return None
        length_steps = strobe_steps(cfg.strobe_beats, steps_per_bar)
        start = step + 1
        end = start + length_steps
        placement = cfg.strobe_placement
        half_bar = steps_per_bar // 2
        if end % steps_per_bar == 0:
            # Ends on a bar line - of which bar of the phrase?
            phrase_bar = (end // steps_per_bar - 1 - self._phrase_origin_bar) % phrase_bars
            if phrase_bar == phrase_bars - 1:
                spot = "phrase"
            elif placement == "half_phrase" and phrase_bars >= 2 and phrase_bar == phrase_bars // 2 - 1:
                spot = "half_phrase"
            elif placement in ("bars", "half_bars"):
                spot = "bar"
            else:
                return None
        elif (
            placement == "half_bars"
            and half_bar % STEPS_PER_BEAT == 0  # the middle of the bar is on a beat (not in 3/4)
            and end % steps_per_bar == half_bar
            and length_steps <= half_bar
        ):
            spot = "half_bar"
        else:
            return None
        if start < self._strobe_until_step:
            return None  # the previous one is still running
        last = self._last_strobe_start
        if last is not None and 0 <= start - last < max(0, int(cfg.strobe_min_gap_bars)) * steps_per_bar:
            return None
        min_level = cfg.strobe_min_level if cfg.strobe_min_level in LEVELS else LEVELS[0]
        if LEVELS.index(self.level) < LEVELS.index(min_level):
            return None
        if self._rng.random() >= cfg.strobe_chance:
            return None
        self._last_strobe_start = start
        self._strobe_from_step = start
        self._strobe_until_step = end
        return PulseEvent(
            "strobe", None, length_steps / STEPS_PER_BEAT, spot, lead=1.0 / STEPS_PER_BEAT
        )

    def _white_steps(self, steps_per_bar: int) -> Set[int]:
        pattern = self.config.white_pattern
        if pattern == "auto":
            pattern = _AUTO_WHITE[self.level]
        every = range(steps_per_bar)
        if pattern == "downbeats":
            return {0}
        if pattern == "beats":
            return {s for s in every if s % STEPS_PER_BEAT == 0}
        if pattern == "offbeats":
            return {s for s in every if s % STEPS_PER_BEAT == 2}
        if pattern == "syncopated":  # 3-3-2 over every two beats (tresillo)
            return {s for s in every if s % 8 in (0, 3, 6)}
        if pattern == "gallop":  # 8th + two 16ths
            return {s for s in every if s % STEPS_PER_BEAT in (0, 2, 3)}
        if pattern == "sixteenths":
            return set(every)
        return set()

    @staticmethod
    def _fill_step(bar_step: int, steps_per_bar: int) -> bool:
        """Fill in the second half of a phrase's last bar: 8ths first, then
        16ths in the last beat - the density rises toward the phrase start.
        The very last 16th is left for the dark breath."""
        if bar_step == steps_per_bar - 1:
            return False
        if bar_step >= steps_per_bar - STEPS_PER_BEAT:
            return True
        return bar_step % 2 == 0

    def _dark_for_step(self, bar_step: int, steps_per_bar: int, last_bar_of_phrase: bool, in_fill: bool) -> Optional[PulseEvent]:
        pattern = self.config.dark_pattern
        patterns = _AUTO_DARK[self.level] if pattern == "auto" else (pattern,)
        length = self.config.dark_length
        last_step = bar_step == steps_per_bar - 1
        if last_step and last_bar_of_phrase and self.config.phrase_accent:
            # The breath before the phrase starts: a bit longer, and always there.
            return PulseEvent("dark", None, 1.5 * length, "phrase")
        if "before_phrase" in patterns and last_step and last_bar_of_phrase:
            return PulseEvent("dark", None, 1.5 * length, "before_phrase")
        if "before_downbeat" in patterns and last_step:
            return PulseEvent("dark", None, length, "before_downbeat")
        if "before_backbeats" in patterns and bar_step % (2 * STEPS_PER_BEAT) == STEPS_PER_BEAT - 1:
            return PulseEvent("dark", None, length, "before_backbeat")
        if "stutter" in patterns and in_fill and bar_step % 2 == 1:
            return PulseEvent("dark", None, 0.6 * length, "stutter")
        return None

    def _next_group(self, num_groups: int) -> int:
        walk = self.config.group_walk
        if walk == "random":
            choices = [g for g in range(num_groups) if g != self._group_index] or [0]
            self._group_index = self._rng.choice(choices)
        elif walk == "pingpong" and num_groups > 1:
            nxt = self._group_index + self._group_direction
            if nxt < 0 or nxt >= num_groups:
                self._group_direction = -self._group_direction
                nxt = self._group_index + self._group_direction
            self._group_index = max(0, min(num_groups - 1, nxt))
        else:
            self._group_index = (self._group_index + 1) % num_groups
        return self._group_index
