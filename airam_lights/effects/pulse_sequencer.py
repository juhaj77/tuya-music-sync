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
    kind: str  # "white" | "dark"
    group: Optional[int] = None  # index into the lamp groups; None = every lamp
    length: float = 1.0  # multiplier on the configured pulse duration
    reason: str = ""  # for diagnostics/tests: "pattern", "double", "fill", "phrase", "drop", ...


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
        self._phrase_origin_bar = 0
        self._short_energy: Optional[float] = None
        self._energy_history: Deque[tuple] = collections.deque()
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
            return []
        events: List[PulseEvent] = []
        for s in range(self._last_step + 1, step + 1):
            events.extend(self._events_for_step(s, max(1, num_groups), max(1, beats_per_bar)))
        self._last_step = step
        return events

    # -- dynamics ------------------------------------------------------------------------------

    def _track_intensity(self, energy: float, now_s: float, dt: float) -> None:
        alpha = 1.0 - math.exp(-max(dt, 0.0) / _SHORT_TIME_CONSTANT_S)
        self._short_energy = energy if self._short_energy is None else self._short_energy + alpha * (energy - self._short_energy)
        self._energy_history.append((now_s, self._short_energy))
        while self._energy_history and self._energy_history[0][0] < now_s - _INTENSITY_WINDOW_S:
            self._energy_history.popleft()
        if len(self._energy_history) >= 10:
            below = sum(1 for _, e in self._energy_history if e < self._short_energy)
            self.intensity = below / len(self._energy_history)
        self.level = _intensity_level(self.intensity)

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

        # -- white ------------------------------------------------------------------
        white_group: Optional[int] = None
        white_reason = ""
        if cfg.white_pattern != "off":
            if phrase_start and cfg.phrase_accent:
                white_reason = "phrase"
            elif in_fill and self._fill_step(bar_step, steps_per_bar):
                white_reason = "fill"
            elif bar_step in self._white_steps(steps_per_bar) and self._rng.random() < cfg.white_density:
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
                if white_reason == "pattern" and self._rng.random() < cfg.double_chance:
                    self._pending_doubles[step + STEPS_PER_BEAT // 2] = white_group
            events.append(PulseEvent("white", white_group, 1.0, white_reason))

        # -- dark (never on the same step as a white flash) --------------------------------
        if cfg.dark_pattern != "off" and not white_reason:
            dark = self._dark_for_step(bar_step, steps_per_bar, last_bar_of_phrase, in_fill)
            if dark is not None and (dark.reason == "phrase" or self._rng.random() < cfg.dark_density):
                events.append(dark)
        return events

    def _on_bar_start(self, bar: int) -> None:
        # A drop: the bar that just ended was clearly quiet, now it's clearly
        # loud -> the phrase (and its big opening flash) starts right here.
        samples = self._bar_intensities
        ended = sum(samples) / len(samples) if samples else None
        if self.config.drop_detection and ended is not None and ended < 0.35 and self.intensity > 0.8:
            self._phrase_origin_bar = bar
        self._bar_intensities = []

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
