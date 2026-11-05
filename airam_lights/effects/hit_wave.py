"""Beat Sync's Across the lamps: a new colour travels along the Chase order on
the hits.

Color Steps' wave takes a set time to cross the lamps, in the clock's beats
(effects/color_steps.py). Beat Sync goes by the hits, so here only they move
it: every flash carries each colour on its way further along, by what the
flash counts toward Hits per change (dsp.hits: a full hit 1, a half-strength
one a quarter). Nothing moves on a beat without a hit, the lamps change just
as they flash, and a breakdown leaves the wave standing where it is until the
music comes back.

Wave length = how many hits from the first lamp to the last, the hit that
brings the colour counted in full: at 1 every lamp takes it on that hit, at 4
a quarter of the lamps per full hit. With Wave length and Hits per change
equal, one colour crosses the room at a time; with a longer Wave length
several follow each other down the line. They all run the same way: a
"random" hue has no direction of its own to run in, and waves coming from
both ends would only cancel out in the middle.

Pure logic: the engine picks the colours, works out each lamp's hue from what
has reached it and shows it (VisualizationEngine._tick_beat_wave). The flash
and what plays on top are the whole room's, as in the rest of Beat Sync.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from ..dsp.smoothing import HueSmoother


@dataclass
class WaveColor:
    """A hue on its way, and what its glide needs (VisualizationEngine._glided_hue)."""

    hue: float
    x: Optional[float] = None  # where in the hue range, in hue mode "spectrum"
    glide_direction: float = 1.0


@dataclass
class _Wave:
    number: int
    color: WaveColor
    hits: float = 1.0  # the hit that brought it counts in full


@dataclass
class WavePlace:
    """One position along the Chase order: the colour that has reached it."""

    color: WaveColor
    hue: HueSmoother  # the hue it shows, on its way to that colour
    number: int
    arrived_at: Optional[float]
    flashes: int  # since it arrived


class HitWave:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._waves: List[_Wave] = []
        self._numbered = 0
        self.places: List[WavePlace] = []

    def resize(self, positions: int, color: WaveColor, hue: float, arrived_at: Optional[float], flashes: int) -> None:
        """Every tick: with a different number of positions, all of them start
        from what the room shows now."""
        if len(self.places) == positions:
            return
        self._waves = []
        self.places = [
            WavePlace(color, HueSmoother(initial=hue), self._numbered, arrived_at, flashes) for _ in range(positions)
        ]

    def flash(self, strength: float, wave_hits: float, color: Optional[WaveColor], now_s: float) -> None:
        """A flash of `strength` went out, bringing a new `color` if the hue
        changed on it. wave_hits: BeatSyncModeConfig.wave_hits."""
        for place in self.places:
            place.flashes += 1
        for wave in self._waves:
            wave.hits += strength ** 2
        if color is not None:
            self._numbered += 1
            self._waves.append(_Wave(self._numbered, color))
        length = max(wave_hits, 1.0)
        n = len(self.places)
        for wave in self._waves:  # the oldest first: the newest colour stays
            reached = max(1, math.floor(n * wave.hits / length + 1e-9))
            for place in self.places[:reached]:
                if place.number < wave.number:
                    place.number, place.color, place.arrived_at, place.flashes = wave.number, wave.color, now_s, 0
        self._waves = [wave for wave in self._waves if wave.hits < length - 1e-9]
