"""Band Colors mode: bass, mids and treble each have a colour of their own,
and the lamps show whichever band is playing out the most right now.

The trick that makes the colour move (where plain bass/mid/treble -> R/G/B
mostly sits on one pale mix): each band is measured against its own recent
range, not against an absolute scale. Loud music fills every band at once,
so their raw levels rise and fall together and their mix hardly changes;
against its own range each band reads near 1 at its own hits - a kick in
the bass, a chord or a vocal entry in the mids, a hi-hat in the treble - and
near 0 between them: three independent signals.

Then:
- Focus: the band activities are raised to a power before they're mixed,
  so the band that's out the most takes the colour (focus 1) or the bands
  blend (focus 0).
- The mix is a weighted average of the bands' hues as directions on the
  colour wheel, not of R, G and B: two bands at once give the hue between
  theirs at full saturation instead of a paler colour (bass + treble a
  magenta with the default red and blue, say).
- Brightness: how far out the most active band is - and silence fades to
  Brightness min. A band has to be audible at all to count: a nearly silent
  band's flicker, swung up to its full range, would otherwise take the
  colour between the hits.
- Chord turn: the whole palette turns with the music's dominant note (its
  place on the circle of fifths, see dsp.hue_step) - a chord change moves
  every colour along, the bands keeping their places relative to each
  other.

Pure logic: band levels in, a hue and a brightness level out. The engine
reads the levels from the spectrum (VisualizationEngine._tick_band_colors_mode).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..dsp.hue_step import AdaptiveRange
from ..dsp.smoothing import AttackReleaseSmoother

BANDS = ("bass", "mid", "treble")
LOW_HZ = 30.0  # the bass band's bottom (below is rumble)
HIGH_HZ = 12000.0  # the treble band's top (above is mostly noise)

_MIN_SPAN = 0.04  # a band steadier than this (dB-scaled level, ~3.6 dB) reads mid-range, not 0 / 1 flicker
_PRESENT = (0.05, 0.15)  # a band's dB-scaled level: silent (~-85 dB) .. there (~-77 dB)
_SILENCE = (0.1, 0.2)  # the loudest band's level: silence (~-81 dB) .. music (~-72 dB)
_HUE_MS = 60.0  # the mixed colour's own glide, against jitter while the bands are even
_TURN_MS = 400.0  # the chord turn's glide: a new chord moves the palette over, it doesn't jump


def band_edges(bass_mid_hz: float, mid_treble_hz: float) -> List[Tuple[float, float]]:
    """The three bands' (low, high) Hz from the two crossover frequencies."""
    split1 = min(max(bass_mid_hz, LOW_HZ + 10.0), HIGH_HZ - 20.0)
    split2 = min(max(mid_treble_hz, split1 + 10.0), HIGH_HZ - 10.0)
    return [(LOW_HZ, split1), (split1, split2), (split2, HIGH_HZ)]


def focus_power(focus: float) -> float:
    """Focus 0..1 -> the power the band activities are raised to before mixing."""
    return 1.0 + 5.0 * min(1.0, max(0.0, focus))


@dataclass
class BandColorsState:
    hue: float = 0.0  # degrees, the chord turn included
    level: float = 0.0  # 0..1 brightness level (before Brightness min..max)
    activities: Dict[str, float] = field(default_factory=dict)  # band -> 0..1 against its own range
    turn: float = 0.0  # degrees the chord turn moves the palette right now


class BandColorTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._ranges = [AdaptiveRange(_MIN_SPAN) for _ in BANDS]
        self._activities = [AttackReleaseSmoother(30.0, 250.0, initial=0.0) for _ in BANDS]
        self._x = AttackReleaseSmoother(_HUE_MS, _HUE_MS)
        self._y = AttackReleaseSmoother(_HUE_MS, _HUE_MS)
        self._turn = AttackReleaseSmoother(_TURN_MS, _TURN_MS)
        self._hue: Optional[float] = None
        self.latest = BandColorsState()

    def update(
        self,
        levels: Sequence[float],
        cfg,
        dt: float,
        chord: Optional[float] = None,
    ) -> BandColorsState:
        """levels: the three bands' dB-scaled levels (bands.band_energy), bass
        first. cfg: a BandColorsModeConfig. chord: 0..1 where the music's note sits for the
        chord turn (dsp.hue_step's "pitch" signal as a note), None = no turn."""
        hues = (cfg.bass_hue_deg, cfg.mid_hue_deg, cfg.treble_hue_deg)
        power = focus_power(cfg.focus)
        activities, x, y, total = [], 0.0, 0.0, 0.0
        for level, band_range, smoother, hue in zip(levels, self._ranges, self._activities, hues):
            band_range.decay_s = max(0.5, cfg.range_memory_s)
            smoother.attack_ms, smoother.release_ms = cfg.attack_ms, cfg.release_ms
            present = _ramp(level, *_PRESENT)
            activity = smoother.update(band_range.update(level, dt) * present, dt)
            activities.append(activity)
            weight = activity ** power
            x += weight * math.cos(math.radians(hue))
            y += weight * math.sin(math.radians(hue))
            total += weight
        if total > 1e-9:
            x, y = x / total, y / total
        sx, sy = self._x.update(x, dt), self._y.update(y, dt)
        if self._hue is None or math.hypot(sx, sy) > 1e-3:
            # (Three bands exactly even cancel out: the colour stays where it was.)
            self._hue = math.degrees(math.atan2(sy, sx)) % 360.0

        turn = self._turn.update(cfg.chord_turn_deg * chord if chord is not None else 0.0, dt)
        gate = _ramp(max(levels, default=0.0), *_SILENCE)
        self.latest = BandColorsState(
            hue=(self._hue + turn) % 360.0,
            level=max(activities, default=0.0) * gate,
            activities=dict(zip(BANDS, activities)),
            turn=turn,
        )
        return self.latest


def _ramp(x: float, low: float, high: float) -> float:
    return min(1.0, max(0.0, (x - low) / (high - low)))
