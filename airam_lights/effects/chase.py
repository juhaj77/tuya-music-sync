"""Shared 'Chase / Rotating Light' overlay.

This is used by BOTH the audio-reactive VisualizationEngine (engine/) and the
standalone, no-audio manual control app (ui/manual_controller.py) - one
implementation, so a fix or feature here (like lamp-position grouping)
benefits both places at once instead of drifting apart.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Set

from ..color.models import Color, WhiteTarget, circular_lerp_deg, clip, lerp
from ..config.schema import ChaseEffectConfig, GroupSwitchEffectConfig, PerLampEffect, WhiteChaseEffectConfig
from ..dsp.beat_clock import ClockBeat, beat_divides
from ..dsp.beat_detector import BeatDetector
from ..dsp.smoothing import _alpha_for


def _smoothstep(t: float) -> float:
    """The standard 'ease in/out' S-curve (t*t*(3-2t)) - flat (near-zero
    slope) at both t=0 and t=1, fastest change in between. Equivalent to a
    symmetric cubic Bezier ease, which is what users usually mean by
    "Bezier curve" for this kind of transition."""
    t = clip(t)
    return t * t * (3.0 - 2.0 * t)


def _falloff_weight(dist: float, width: float, curve: str) -> float:
    """0..1 highlight weight at a given distance (in chase positions) from
    the nearest rotator. 'linear' falls off at a constant rate, so the peak
    (dist=0) is a single fleeting instant. 'bezier' dwells near the peak
    (and near zero) for longer, transitioning fastest in the middle -
    directly addresses "the highlight color flies by too quickly"."""
    linear_weight = clip(1.0 - dist / max(width, 1e-6))
    if curve == "bezier":
        return _smoothstep(linear_weight)
    return linear_weight


# _hue_part_way(): how close to 0 or 1 `amount` must be for going back to the
# short way round to be nearly invisible (moves the hue by 360 * this at most).
_REWRAP_AMOUNT = 0.05


def _hue_part_way(h_base: float, target_hue: float, amount: float, deltas: Dict[str, float], key: str) -> float:
    """The hue `amount` (0..1) of the way from a lamp's own hue to a target
    hue: the short way round the circle - but once on its way, the same way
    round for as long as this lamp stays in between (`deltas` remembers it
    per lamp; drop the entry when the lamp is back at 0 or at the full
    target). Otherwise its color would jump to the other side of the circle
    the moment its own hue and the target (both can be moving: a hue glide,
    a hue snap, a soft switch) pass through being exactly opposite.

    NOT for a target that is by definition exactly opposite
    ("complementary": own hue + 180): there both ways round are equally
    short and floating-point rounding picks one at random, tick by tick -
    use `(h_base + 180 * amount) % 360` for that instead.

    The long way round is only kept while it's needed: the lamp's own hue
    can keep turning the same way for as long as it likes (Beat Sync's
    "step" hue), and a soft step or Fade across groups can keep a lamp in
    between for just as long - an unbounded winding would then turn tiny
    changes in `amount` into whole turns of the hue, flickering between
    colors. So it goes back to the short way as soon as that is nearly
    invisible (`amount` near 0 or 1: a change of at most 18 degrees), and in
    any case before it gets a full turn long."""
    delta = ((target_hue - h_base + 180.0) % 360.0) - 180.0
    previous = deltas.get(key)
    if previous is not None:
        kept = delta + 360.0 * round((previous - delta) / 360.0)
        nearly_invisible = amount <= _REWRAP_AMOUNT or amount >= 1.0 - _REWRAP_AMOUNT
        if abs(kept) <= 180.0 or (abs(kept) < 360.0 and not nearly_invisible):
            delta = kept
    deltas[key] = delta
    return (h_base + delta * amount) % 360.0


def _swept_min_distance(target: float, start: float, end: float, n: float) -> float:
    """Minimum circular distance (topology of size n) from `target` to the
    whole continuous arc swept from `start` to `end` during one tick - not
    just to `end` (the position's value after the tick).

    At high rotation speeds, a single tick can move the highlight all the
    way past a lamp's position between one sample and the next, so sampling
    only the end-of-tick position means that lamp's dist never reaches 0 and
    it never sees its full target color - the highlight "jumps over" it.
    Using the whole swept arc means a lamp the highlight crossed during the
    tick still registers dist=0 (its true peak), regardless of speed."""
    if n <= 0:
        return 0.0
    lo, hi = (start, end) if start <= end else (end, start)
    span = hi - lo
    if span >= n:
        return 0.0  # swept a full lap or more this tick: every position was covered
    k_lo = int(math.floor((lo - target) / n)) - 1
    k_hi = int(math.ceil((hi - target) / n)) + 1
    best = float("inf")
    for k in range(k_lo, k_hi + 1):
        candidate = target + k * n
        if lo <= candidate <= hi:
            return 0.0
        best = min(best, abs(candidate - lo), abs(candidate - hi))
    return best


def _local_dwell_weight(position: float, n: int, dwell_weights: Optional[Sequence[float]]) -> float:
    """The dwell weight of whichever chase position `position` currently
    sits nearest to, clamped away from zero. Returns 1.0 (uniform) if no
    weights were given."""
    if not dwell_weights:
        return 1.0
    idx = int(round(position)) % n
    if idx >= len(dwell_weights):
        return 1.0
    return max(dwell_weights[idx], 0.05)


def get_effect_order_groups(
    per_lamp_effects: Dict[str, PerLampEffect], selected_ids: Sequence[str], order_attr: str
) -> List[List[str]]:
    """Groups the given lamps by an ordinal-valued PerLampEffect attribute
    (named by `order_attr`), ascending - the shared mechanism behind both
    `get_chase_groups()` (keyed on `chase_order`) and
    `get_group_switch_groups()` (keyed on `effect_group`).

    Lamps that share the same order number end up in the same group and
    always animate together as one "position" - this is what makes either
    overlay work for physical layouts that aren't a single ring (e.g. two
    lamps on each of four walls: give each wall's pair the same order
    number, and the overlay treats the whole wall as one step).

    Lamps whose `order_attr` is unset (None) are excluded entirely.
    """
    buckets: Dict[int, List[str]] = {}
    for device_id in selected_ids:
        effect = per_lamp_effects.get(device_id)
        if effect is None:
            continue
        order = getattr(effect, order_attr)
        if order is not None:
            buckets.setdefault(order, []).append(device_id)
    return [buckets[key] for key in sorted(buckets.keys())]


def get_chase_groups(per_lamp_effects: Dict[str, PerLampEffect], selected_ids: Sequence[str]) -> List[List[str]]:
    """Lamps grouped by PerLampEffect.chase_order - see get_effect_order_groups()."""
    return get_effect_order_groups(per_lamp_effects, selected_ids, "chase_order")


def spread_positions(positions: List[List[str]], index: int, rotators: int = 1) -> Set[str]:
    """The lamps at `index` plus `rotators - 1` more positions evenly spaced
    around the loop - 2 = the opposite side too, 3 = thirds, and so on (the
    same spacing Chase's num_rotators uses)."""
    n = len(positions)
    if n == 0:
        return set()
    count = max(1, min(int(rotators), n))
    lamps: Set[str] = set()
    for k in range(count):
        lamps.update(positions[(index + int(round(k * n / count))) % n])
    return lamps


def get_group_switch_groups(per_lamp_effects: Dict[str, PerLampEffect], selected_ids: Sequence[str]) -> List[List[str]]:
    """Lamps grouped by PerLampEffect.effect_group - see get_effect_order_groups().
    A separate, independent grouping from get_chase_groups()'s chase_order -
    a lamp can belong to either, both, or neither."""
    return get_effect_order_groups(per_lamp_effects, selected_ids, "effect_group")


def get_chase_group_dwell_weights(
    per_lamp_effects: Dict[str, PerLampEffect], groups: List[List[str]]
) -> List[float]:
    """Per-position dwell-time multiplier, in the same order as `groups`
    (from `get_chase_groups`). A group's weight is the average of its
    members' `PerLampEffect.chase_dwell_mult` - normally every lamp sharing
    a physical fixture is set to the same value. Higher = the moving
    highlight lingers there longer; lower = it passes through faster.
    Missing lamps/effects default to 1.0 (uniform dwell, matching the
    original behavior before this setting existed)."""
    weights: List[float] = []
    for group in groups:
        mults = [
            per_lamp_effects[device_id].chase_dwell_mult
            for device_id in group
            if device_id in per_lamp_effects
        ]
        weights.append(sum(mults) / len(mults) if mults else 1.0)
    return weights


class ChaseAnimator:
    """Owns the chase's moving position(s) and renders them onto a color
    dict. Stateful (the position persists between calls) but otherwise
    independent of audio, lamps, or UI - easy to unit test and to drive from
    either a music-reactive tick or a plain timer."""

    def __init__(self, config: ChaseEffectConfig):
        self.config = config
        self._position = 0.0
        self._sweep_start = 0.0
        self._sweep_end = 0.0
        self._beat_detector = BeatDetector(
            sensitivity=config.beat_sensitivity,
            min_interval_ms=config.beat_min_interval_ms,
            min_energy=config.beat_min_energy,
        )
        self._peak_detector = BeatDetector(
            sensitivity=config.peak_sensitivity,
            min_interval_ms=config.peak_min_interval_ms,
            min_energy=config.peak_min_energy,
        )
        # Soft steps (config.switch_fade): each position's current share of
        # the highlight, on its way to where the highlight now puts it (see
        # _smooth()). Empty = nothing to continue from.
        self._weights: List[float] = []
        # Per lamp, while it is part of the way to the highlight color: which
        # way round the hue circle that way goes - see _hue_part_way().
        self._hue_deltas: Dict[str, float] = {}

    @property
    def position(self) -> float:
        return self._position

    @position.setter
    def position(self, value: float) -> None:
        """Directly placing the rotator (e.g. tests pinning it to an exact
        spot, bypassing tick()) collapses to a zero-length sweep right there
        - a "teleport", not a move - so `apply()` samples only that spot,
        matching the pre-sweep-tracking behavior. `tick()` is the only path
        that produces a genuine swept arc; it writes `_position` directly to
        bypass this collapse."""
        self._position = value
        self._sweep_start = value
        self._sweep_end = value

    def update_config(self, config: ChaseEffectConfig) -> None:
        self.config = config
        self._beat_detector.sensitivity = config.beat_sensitivity
        self._beat_detector.min_interval_ms = config.beat_min_interval_ms
        self._beat_detector.min_energy = config.beat_min_energy
        self._peak_detector.sensitivity = config.peak_sensitivity
        self._peak_detector.min_interval_ms = config.peak_min_interval_ms
        self._peak_detector.min_energy = config.peak_min_energy

    def reset(self) -> None:
        self.position = 0.0
        self._beat_detector.reset()
        self._peak_detector.reset()
        self._weights = []
        self._hue_deltas = {}

    def _target_weights(self, n: int) -> List[float]:
        """How much of the highlight each of the `n` positions gets right
        now, 0..1: the falloff around each rotator, measured against the arc
        it swept during the last tick (so a fast highlight can't jump over a
        lamp without touching it)."""
        cfg = self.config
        num_rotators = max(1, int(cfg.num_rotators))
        spacing = n / num_rotators
        sweeps = [(self._sweep_start + k * spacing, self._sweep_end + k * spacing) for k in range(num_rotators)]
        weights = []
        for i in range(n):
            weight = 0.0
            for start, end in sweeps:
                w = _falloff_weight(_swept_min_distance(float(i), start, end, n), cfg.width, cfg.falloff_curve)
                if w > weight:
                    weight = w
            weights.append(weight)
        return weights

    def _smooth(self, dt: float, n: int) -> None:
        """Soft steps: instead of taking its new share of the highlight at
        once, each position moves a fraction of the way there every tick -
        the same exponential approach as Group Switch's switch fade. With it
        off (or nothing to continue from) there is no state to keep."""
        cfg = self.config
        if not cfg.switch_fade or cfg.switch_fade_ms <= 0.0:
            self._weights = []
            return
        targets = self._target_weights(n)
        if len(self._weights) != n:
            self._weights = targets
            return
        alpha = _alpha_for(cfg.switch_fade_ms, dt)
        self._weights = [weight + (target - weight) * alpha for weight, target in zip(self._weights, targets)]

    def tick(
        self,
        dt: float,
        num_positions: int,
        beat_band_energy: Optional[float] = None,
        intensity_energy: Optional[float] = None,
        now_s: Optional[float] = None,
        dwell_weights: Optional[Sequence[float]] = None,
        clock_beat: Optional[ClockBeat] = None,
    ) -> None:
        """Advances the chase position. `num_positions` is the current
        number of distinct chase groups (from `get_chase_groups`) - it can
        change over time as lamps are added/removed from the chase without
        breaking anything.

        sync_mode == "off": advances continuously at the constant
        `speed_rotations_per_s`, every tick, same as always.

        sync_mode == "beat" / "intensity_peak": purely event-driven. The
        position does NOT move on its own between hits - it only jumps
        `beat_multiplier` lamp-steps on the exact tick a beat/peak is
        detected in `beat_band_energy`/`intensity_energy`. An earlier
        version instead estimated a rolling tempo and rotated continuously
        at that estimate, which kept the highlight gliding on its own
        between hits (and even after the music stopped, on the last
        estimate) - looking like it spins with no audible rhythm behind it.
        Never moving except on an actual hit is what fixes that.

        `beat_band_energy`/`intensity_energy` + `now_s`: pass these (from
        the caller's own band energy extraction) to enable "beat"/
        "intensity_peak" respectively. Without a `now_s` at all (as the
        no-audio manual app does, since it has nothing to sync to), both
        modes fall back to the same constant `speed_rotations_per_s` as
        "off" - there is no rhythm signal available to wait for, so standing
        still forever would just look broken there instead.

        `dwell_weights`: optional per-position dwell-time multipliers (see
        `get_chase_group_dwell_weights`), same length/order as `groups`.
        In "off" mode (or the no-`now_s` fallback above) this scales the
        continuous speed, exactly as before; in the event-driven modes it
        scales the size of each discrete hit-triggered step instead, so a
        higher-dwell position still ends up "held" relatively longer across
        many hits.

        sync_mode == "clock": same event-driven stepping, but on the shared
        beat clock's beats (`clock_beat`, from the engine) - only every
        `clock_every_n_beats`th one, counted from the bar start - so Chase
        moves on exactly the same beats as every other clock-driven layer.
        """
        cfg = self.config
        n = max(1, num_positions)
        direction = -1.0 if cfg.reverse else 1.0
        local_dwell = _local_dwell_weight(self._position, n, dwell_weights)

        if cfg.sync_mode == "beat" and now_s is not None:
            triggered = beat_band_energy is not None and self._beat_detector.update(beat_band_energy, now_s)
            delta = direction * cfg.beat_multiplier / local_dwell if triggered else 0.0
        elif cfg.sync_mode == "intensity_peak" and now_s is not None:
            triggered = intensity_energy is not None and self._peak_detector.update(intensity_energy, now_s)
            delta = direction * cfg.beat_multiplier / local_dwell if triggered else 0.0
        elif cfg.sync_mode == "clock" and now_s is not None:
            triggered = clock_beat is not None and beat_divides(clock_beat, cfg.clock_every_n_beats)
            delta = direction * cfg.beat_multiplier / local_dwell if triggered else 0.0
        else:
            steps_per_s = cfg.speed_rotations_per_s * n
            delta = direction * steps_per_s * dt / local_dwell

        self._sweep_start = self._position
        self._sweep_end = self._position + delta
        self._position = self._sweep_end % n  # bypass the setter: keep the sweep just computed
        self._smooth(dt, n)

    def active_device_ids(self, groups: List[List[str]]) -> Set[str]:
        """The lamps the highlight is currently on: for each rotator, the
        group nearest its current position (not the swept arc - this is
        "where it is now", used e.g. to target Beat Sync's true-white flash
        at just the moving lamps)."""
        n = len(groups)
        if n == 0:
            return set()
        num_rotators = max(1, int(self.config.num_rotators))
        spacing = n / num_rotators
        active: Set[str] = set()
        for k in range(num_rotators):
            index = int(round(self._position + k * spacing)) % n
            active.update(groups[index])
        return active

    def apply(self, colors: Dict[str, Color], groups: List[List[str]]) -> Dict[str, Color]:
        """Renders the current position onto `colors`, returning a new dict.
        Brightness is always a multiplicative boost on each lamp's own
        current value - never an independent/fixed brightness - so a lamp
        the active mode has deliberately driven to black (e.g. a Beat Sync
        dark pulse) stays black no matter the chase's intensity or width."""
        n = len(groups)
        if n < 2:
            return colors

        cfg = self.config
        # Mid soft step (see _smooth()) the positions are between two places.
        soft = cfg.switch_fade and cfg.switch_fade_ms > 0.0 and len(self._weights) == n
        weights = self._weights if soft else self._target_weights(n)

        result = dict(colors)
        for i, group in enumerate(groups):
            weight = weights[i]
            if weight >= 0.999:
                weight = 1.0
            if weight <= (0.001 if soft else 0.0):
                for device_id in group:
                    self._hue_deltas.pop(device_id, None)
                continue

            for device_id in group:
                base_color = result.get(device_id, Color.black())
                h_base, s_base, v_base = base_color.to_hsv()

                if cfg.color_mode == "complementary":
                    target_hue = (h_base + 180.0) % 360.0
                    target_sat = s_base
                elif cfg.color_mode == "hue_shift":
                    # Each position shows a progressively different hue, so
                    # the traveling light's own color gradually shifts
                    # through the spectrum as it moves around the loop.
                    target_hue = (cfg.custom_hue_deg + i * cfg.hue_shift_step_deg) % 360.0
                    target_sat = cfg.custom_saturation
                else:  # "custom"
                    target_hue = cfg.custom_hue_deg
                    target_sat = cfg.custom_saturation

                if cfg.color_mode == "complementary":
                    # Exactly opposite: both ways round are equally short, so
                    # always go the same way - see _hue_part_way().
                    h_out = (h_base + 180.0 * weight) % 360.0
                elif weight == 1.0:
                    self._hue_deltas.pop(device_id, None)
                    h_out = target_hue
                else:
                    h_out = _hue_part_way(h_base, target_hue, weight, self._hue_deltas, device_id)
                s_out = lerp(s_base, target_sat, weight)
                v_out = clip(v_base * (1.0 + weight * cfg.intensity))

                result[device_id] = Color.from_hsv(h_out, s_out, v_out)
        return result


class WhiteChaseAnimator:
    """The White-mode counterpart to ChaseAnimator: instead of an RGB/hue
    highlight, a warm-or-cool color TEMPERATURE region rotates through the
    chase-ordered lamp positions. No beat-sync here (see
    WhiteChaseEffectConfig) - built for the standalone manual control app,
    which has no audio input.

    Brightness is, exactly like ChaseAnimator, a multiplicative boost on
    each lamp's own current brightness - a lamp at 0 stays at 0."""

    def __init__(self, config: WhiteChaseEffectConfig):
        self.config = config
        self._position = 0.0
        self._sweep_start = 0.0
        self._sweep_end = 0.0

    @property
    def position(self) -> float:
        return self._position

    @position.setter
    def position(self, value: float) -> None:
        """See ChaseAnimator.position setter: a direct assignment "teleports"
        (zero-length sweep at that spot); only tick() produces a real swept
        arc, writing `_position` directly to bypass this collapse."""
        self._position = value
        self._sweep_start = value
        self._sweep_end = value

    def update_config(self, config: WhiteChaseEffectConfig) -> None:
        self.config = config

    def reset(self) -> None:
        self.position = 0.0

    def tick(self, dt: float, num_positions: int, dwell_weights: Optional[Sequence[float]] = None) -> None:
        cfg = self.config
        n = max(1, num_positions)
        direction = -1.0 if cfg.reverse else 1.0
        steps_per_s = cfg.speed_rotations_per_s * n
        local_dwell = _local_dwell_weight(self._position, n, dwell_weights)
        delta = direction * steps_per_s * dt / local_dwell
        self._sweep_start = self._position
        self._sweep_end = self._position + delta
        self._position = self._sweep_end % n  # bypass the setter: keep the sweep just computed

    def apply(self, targets: Dict[str, WhiteTarget], groups: List[List[str]]) -> Dict[str, WhiteTarget]:
        n = len(groups)
        if n < 2:
            return targets

        cfg = self.config
        num_rotators = max(1, int(cfg.num_rotators))
        spacing = n / num_rotators
        rotator_sweeps = [
            (self._sweep_start + k * spacing, self._sweep_end + k * spacing) for k in range(num_rotators)
        ]

        result = dict(targets)
        for i, group in enumerate(groups):
            weight = 0.0
            for start, end in rotator_sweeps:
                dist = _swept_min_distance(float(i), start, end, n)
                w = _falloff_weight(dist, cfg.width, cfg.falloff_curve)
                if w > weight:
                    weight = w
            if weight <= 0.0:
                continue

            for device_id in group:
                base = result.get(device_id, WhiteTarget(0.0, 0.5))
                temp_out = lerp(base.temp, cfg.target_temp, weight)
                bright_out = clip(base.brightness * (1.0 + weight * cfg.intensity))
                result[device_id] = WhiteTarget(brightness=bright_out, temp=temp_out)
        return result


class GroupSwitchAnimator:
    """Alternative to ChaseAnimator's continuous rotation: lamps are grouped
    by PerLampEffect.effect_group (see get_group_switch_groups() - a
    separate, independent grouping from Chase's chase_order), and exactly
    ONE group is "active" at a time, shown at full target-color strength -
    every other group is left completely untouched. There is no width or
    falloff shaping here: the switch from one active group to the next is
    instant, a hard on/off step rather than Chase's gradient - the target
    hue/saturation is assigned outright on the one active group.

    With `fade_across_groups` the color is instead spread over all the
    groups in equal steps (see apply()): the active group still gets the
    full target color, each group before it one step less. The switch
    itself stays a hard step - the whole ramp moves on by one group -
    unless `switch_fade_ms` is set: then every group's share of the color
    (and in hue_shift mode the hue itself) glides to its new value over
    that time constant instead of jumping (see _smooth()).

    Movement otherwise follows the exact same event-driven model as
    ChaseAnimator.tick() (see its docstring for the full rationale): "off"
    advances continuously at `speed_rotations_per_s`; "beat"/
    "intensity_peak" sit still and only step `beat_multiplier` groups on an
    actual detected hit; "clock" steps on every `clock_every_n_beats`th
    shared-clock beat; with no `now_s` at all (no time/audio context to
    sync to), both fall back to the same constant speed as "off".

    Deliberately no "num_rotators"-style multi-active-group support (yet) -
    a first, simple version to try out before adding more shaping, per the
    request that prompted this class."""

    def __init__(self, config: GroupSwitchEffectConfig):
        self.config = config
        self.position = 0.0
        self._beat_detector = BeatDetector(
            sensitivity=config.beat_sensitivity,
            min_interval_ms=config.beat_min_interval_ms,
            min_energy=config.beat_min_energy,
        )
        self._peak_detector = BeatDetector(
            sensitivity=config.peak_sensitivity,
            min_interval_ms=config.peak_min_interval_ms,
            min_energy=config.peak_min_energy,
        )
        # switch_fade_ms: each group's current share of the group color and
        # the current hue_shift hue, moving toward where the active group
        # puts them (see _smooth()). Empty/None = nothing to continue from.
        self._amounts: List[float] = []
        self._shift_hue: Optional[float] = None
        # Per lamp, while it shows a color part of the way to the group
        # color: which way round the hue circle (and how far) that way goes -
        # see apply().
        self._hue_deltas: Dict[str, float] = {}

    def update_config(self, config: GroupSwitchEffectConfig) -> None:
        self.config = config
        self._beat_detector.sensitivity = config.beat_sensitivity
        self._beat_detector.min_interval_ms = config.beat_min_interval_ms
        self._beat_detector.min_energy = config.beat_min_energy
        self._peak_detector.sensitivity = config.peak_sensitivity
        self._peak_detector.min_interval_ms = config.peak_min_interval_ms
        self._peak_detector.min_energy = config.peak_min_energy

    def reset(self) -> None:
        self.position = 0.0
        self._beat_detector.reset()
        self._peak_detector.reset()
        self._amounts = []
        self._shift_hue = None
        self._hue_deltas = {}

    def tick(
        self,
        dt: float,
        num_positions: int,
        beat_band_energy: Optional[float] = None,
        intensity_energy: Optional[float] = None,
        now_s: Optional[float] = None,
        clock_beat: Optional[ClockBeat] = None,
    ) -> None:
        cfg = self.config
        n = max(1, num_positions)
        direction = -1.0 if cfg.reverse else 1.0

        if cfg.sync_mode == "beat" and now_s is not None:
            triggered = beat_band_energy is not None and self._beat_detector.update(beat_band_energy, now_s)
            delta = direction * cfg.beat_multiplier if triggered else 0.0
        elif cfg.sync_mode == "intensity_peak" and now_s is not None:
            triggered = intensity_energy is not None and self._peak_detector.update(intensity_energy, now_s)
            delta = direction * cfg.beat_multiplier if triggered else 0.0
        elif cfg.sync_mode == "clock" and now_s is not None:
            triggered = clock_beat is not None and beat_divides(clock_beat, cfg.clock_every_n_beats)
            delta = direction * cfg.beat_multiplier if triggered else 0.0
        else:
            steps_per_s = cfg.speed_rotations_per_s * n
            delta = direction * steps_per_s * dt

        self.position = (self.position + delta) % n
        self._smooth(dt, n)

    def _shift_target_hue(self, active_index: int) -> float:
        """hue_shift mode: each group shows a progressively different hue, so
        which color shows depends on which group is currently active."""
        return (self.config.custom_hue_deg + active_index * self.config.hue_shift_step_deg) % 360.0

    def _smooth(self, dt: float, n: int) -> None:
        """The soft switch (`switch_fade_ms`): instead of jumping when the
        active group changes, each group's share of the group color - and in
        hue_shift mode the hue, which also changes with the active group -
        moves a fraction of the way to its new value every tick, the same
        exponential approach Beat Sync's hue snap uses. With 0 ms, or with
        nothing to continue from (first tick, the number of groups changed),
        it's simply set."""
        cfg = self.config
        active_index = int(math.floor(self.position)) % n
        targets = [self._group_amount(index, active_index, n) for index in range(n)]
        target_hue = self._shift_target_hue(active_index)
        if cfg.switch_fade_ms <= 0.0 or len(self._amounts) != n or self._shift_hue is None:
            self._amounts = targets
            self._shift_hue = target_hue
            return
        alpha = _alpha_for(cfg.switch_fade_ms, dt)
        self._amounts = [amount + (target - amount) * alpha for amount, target in zip(self._amounts, targets)]
        self._shift_hue = circular_lerp_deg(self._shift_hue, target_hue, alpha)

    def active_device_ids(self, groups: List[List[str]]) -> Set[str]:
        """The lamps in the currently active group (same index apply() uses)."""
        if not groups:
            return set()
        return set(groups[int(math.floor(self.position)) % len(groups)])

    def apply(self, colors: Dict[str, Color], groups: List[List[str]]) -> Dict[str, Color]:
        """Renders the current active group onto `colors`. Brightness is,
        like Chase, always a multiplicative boost on the lamp's own current
        value - a lamp the active mode has already driven to black stays
        black no matter this effect's intensity."""
        n = len(groups)
        if n < 2:
            return colors

        cfg = self.config
        active_index = int(math.floor(self.position)) % n

        # Mid soft switch (see _smooth()) the groups are between two places.
        soft = cfg.switch_fade_ms > 0.0 and len(self._amounts) == n and self._shift_hue is not None

        result = dict(colors)
        for index, group in enumerate(groups):
            amount = self._amounts[index] if soft else self._group_amount(index, active_index, n)
            if amount >= 0.999:
                amount = 1.0
            if amount <= 0.001 or amount == 1.0:
                for device_id in group:
                    self._hue_deltas.pop(device_id, None)  # not in between: nothing to keep track of
            if amount <= 0.001:
                continue  # left completely untouched
            for device_id in group:
                base_color = result.get(device_id, Color.black())
                h_base, s_base, v_base = base_color.to_hsv()

                if cfg.color_mode == "complementary":
                    target_hue = (h_base + 180.0) % 360.0
                    target_sat = s_base
                elif cfg.color_mode == "hue_shift":
                    target_hue = self._shift_hue if soft else self._shift_target_hue(active_index)
                    target_sat = cfg.custom_saturation
                else:  # "custom"
                    target_hue = cfg.custom_hue_deg
                    target_sat = cfg.custom_saturation

                if amount < 1.0:
                    # Part of the way from the lamp's own color to the target,
                    # around the hue circle (so the steps stay vivid instead of
                    # greying out like an RGB blend would).
                    if cfg.color_mode == "complementary":
                        # Exactly opposite: both ways round are equally short,
                        # so always go the same way instead of letting rounding
                        # pick one tick by tick.
                        target_hue = (h_base + 180.0 * amount) % 360.0
                    else:
                        target_hue = _hue_part_way(h_base, target_hue, amount, self._hue_deltas, device_id)
                    target_sat = lerp(s_base, target_sat, amount)

                v_out = clip(v_base * (1.0 + cfg.intensity * amount))
                result[device_id] = Color.from_hsv(target_hue, target_sat, v_out)
        return result

    def _group_amount(self, index: int, active_index: int, n: int) -> float:
        """How much of the group color the group at `index` shows, 0..1.
        Normally all (the active group) or nothing (every other one). With
        `fade_across_groups`: a ramp over all the groups - the active one
        1.0, each group before it (where the active group just came from)
        one equal step less, down to 0 for the one right ahead of it. E.g.
        3 groups: 1.0, 0.5, 0."""
        if index == active_index:
            return 1.0
        if not self.config.fade_across_groups or n < 3:
            return 0.0
        behind = (index - active_index) % n if self.config.reverse else (active_index - index) % n
        return 1.0 - behind / (n - 1)
