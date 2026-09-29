"""Ties audio capture, DSP, color mapping, and lamp control together.

Deliberately split into two independent ticks, driven by two independently
configurable rates (see config.schema.AudioConfig.analysis_update_hz and
config.schema.NetworkConfig.visual_update_hz):

- `tick_analysis()`: pulls the latest audio window and computes one FFT
  frame. Cheap (a 2048-point real FFT is sub-millisecond), mode-agnostic.
- `tick_visual()`: reads the latest FFT frame, extracts whatever
  bands/features the current color mode needs, applies attack/release
  smoothing, runs the color mapping, applies per-lamp effects (including a
  temporal/phase offset via a short history buffer), and finally hands the
  per-lamp target colors to the LampManager.

The LampManager's own per-lamp worker threads then independently rate-limit
actual network sends to `lamp_command_rate_hz` and drop unchanged updates -
so this class never needs to know or care about lamp command pacing.

Both ticks are cheap enough to run on Qt's own timer/event loop without
blocking the UI; the only actually blocking work (LAN sockets) happens on
the LampManager's dedicated worker threads.
"""
from __future__ import annotations

import collections
import logging
import math
import random
import threading
import time
from typing import Deque, Dict, Optional, Set, Tuple

import numpy as np

from ..audio.capture import AudioCapture
from ..color.mapping import ColorMappingEngine, apply_per_lamp_effect
from ..color.models import Color, WhiteTarget, circular_lerp_deg, clip, lerp
from ..config.schema import AppConfig
from ..diagnostics.metrics import RateCounter
from ..dsp.bands import band_energies, band_energy, spectral_centroid_hz, spectral_contrast
from ..dsp.beat_clock import BeatClock, ClockBeat, beat_divides
from ..dsp.beat_detector import BeatDetector
from ..effects.chase import (
    ChaseAnimator,
    GroupSwitchAnimator,
    get_chase_group_dwell_weights,
    get_chase_groups,
    get_group_switch_groups,
    spread_positions,
)
from ..dsp.fft_engine import FFTEngine, SpectrumFrame
from ..effects.pulse_sequencer import LoudnessTracker, PulseEvent, PulseSequencer, metric_weight, phrase_progress
from ..dsp.smoothing import AttackReleaseSmoother, HueSmoother, MultiSmoother
from ..lamps.manager import LampManager

logger = logging.getLogger("airam_lights.engine")

# Below this, a dark/white pulse's smoothed amount is considered to have
# finished fading back out - both (a) a true-white pulse's device reverts to
# normal RGB sends, and (b) either pulse becomes eligible to trigger again
# on a future beat. The underlying smoother only asymptotically approaches
# 0 and never quite reaches it, so without a floor a lamp could stay parked
# in a permanent, imperceptibly-dim WHITE work_mode forever, and/or a new
# pulse could never be considered "fully faded" enough to allow retriggering.
_WHITE_PULSE_EPSILON = 0.02

# Absolute safety ceiling on continuous true-white time, independent of any
# single pulse's own duration/probability/cooldown settings. On a dense/fast
# track, individually-short pulses can still chain closely enough that the
# GLOBAL "white active" state stays true almost continuously for stretches -
# each pulse is legitimately short, but a lamp whose own worker thread is
# rate-limited/backed off (e.g. from an earlier unrelated send failure) can
# consistently miss the brief RGB gaps between chained pulses and never
# actually get a turn to send the "back to RGB" command, which looks
# indistinguishable from being stuck. If continuous true-white time exceeds
# this, the engine forces a real break - not just one skipped tick, but a
# guaranteed minimum RGB-only window (_WHITE_FORCED_GAP_S) long enough that
# even a lamp backed off to the worst case (16x its base send interval) gets
# at least one real chance to see and send the reverted color.
_WHITE_MAX_CONTINUOUS_S = 1.5
_WHITE_FORCED_GAP_S = 1.0
# A run of chained pulses has brief natural gaps between individual pulses
# (the per-pulse cooldown in _tick_beat_sync_mode). Only a gap LONGER than
# this counts as the chain having genuinely ended for _WHITE_MAX_CONTINUOUS_S
# accounting purposes - otherwise those brief gaps would keep resetting the
# continuous-time tracker before it could ever reach the ceiling, defeating
# the whole point of it.
_WHITE_CHAIN_GAP_TOLERANCE_S = 0.3
# The same ceiling, tracked per lamp for the pulse sequencer (whose flashes
# walk between lamp groups, so the installation as a whole can be "white
# somewhere" continuously while each single lamp gets plenty of RGB time).
# A shorter tolerance: one lamp's RGB gap between two of its own flashes is
# already a real chance for its worker to send the reverted color.
_SEQ_WHITE_GAP_TOLERANCE_S = 0.15


class TimeSeriesBuffer:
    """Small history of (timestamp, vector) samples, used to implement the
    optional per-lamp temporal/phase offset via linear interpolation."""

    def __init__(self, max_age_s: float = 2.0):
        self.max_age_s = max_age_s
        self._entries: Deque[Tuple[float, np.ndarray]] = collections.deque()

    def push(self, t: float, values: np.ndarray) -> None:
        self._entries.append((t, np.array(values, dtype=float)))
        cutoff = t - self.max_age_s
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()

    def sample_at(self, t: float) -> Optional[np.ndarray]:
        if not self._entries:
            return None
        if t <= self._entries[0][0]:
            return self._entries[0][1]
        if t >= self._entries[-1][0]:
            return self._entries[-1][1]
        prev = self._entries[0]
        for entry in self._entries:
            if entry[0] >= t:
                t1, v1 = prev
                t2, v2 = entry
                if t2 <= t1:
                    return v2
                frac = (t - t1) / (t2 - t1)
                return v1 + (v2 - v1) * frac
            prev = entry
        return self._entries[-1][1]


class VisualizationEngine:
    def __init__(self, config: AppConfig, audio: AudioCapture, lamp_manager: LampManager):
        self.config = config
        self.audio = audio
        self.lamp_manager = lamp_manager

        self.fft = FFTEngine(audio.samplerate, config.audio.fft_size)
        self.color_engine = ColorMappingEngine(config.color_mapping)

        sm = config.color_mapping.smoothing
        self._smoother_r = AttackReleaseSmoother(sm.attack_ms, sm.release_ms)
        self._smoother_g = AttackReleaseSmoother(sm.attack_ms, sm.release_ms)
        self._smoother_b = AttackReleaseSmoother(sm.attack_ms, sm.release_ms)
        self._smoother_log_centroid = AttackReleaseSmoother(sm.attack_ms, sm.release_ms, initial=math.log(200))
        self._smoother_value = AttackReleaseSmoother(sm.attack_ms, sm.release_ms)
        self._smoother_contrast = AttackReleaseSmoother(sm.attack_ms, sm.release_ms)
        self._smoother_bands8 = MultiSmoother(len(config.bands_8), sm.attack_ms, sm.release_ms)

        self._history_3 = TimeSeriesBuffer()
        self._history_8 = TimeSeriesBuffer()
        self._history_beat = TimeSeriesBuffer()
        self._history_peak = TimeSeriesBuffer()

        bs = config.color_mapping.beat_sync
        self._beat_detector = BeatDetector(
            sensitivity=bs.sensitivity, min_interval_ms=bs.min_interval_ms, min_energy=bs.min_energy
        )
        self._beat_target_hue = random.uniform(0.0, 360.0)
        self._beat_hue_cursor = self._beat_target_hue
        self._hue_smoother_beat = HueSmoother(bs.hue_attack_ms, initial=self._beat_target_hue)
        self._smoother_beat_value = AttackReleaseSmoother(bs.brightness_attack_ms, bs.brightness_release_ms)
        # glide_hue with timing "decay": the same flash envelope, normalized to
        # 1 (on the beat) .. 0 (fully decayed), drives how far the hue has
        # glided toward the next color.
        self._smoother_beat_glide = AttackReleaseSmoother(bs.brightness_attack_ms, bs.brightness_release_ms)
        self._beat_upcoming_hue: Optional[float] = None  # hue_mode "random": the next color, picked in advance
        # Both pulse smoothers track a 0..1 "how deep into the pulse are we"
        # fraction, not the underlying brightness/saturation value directly -
        # entering a pulse is always a rise toward 1 and leaving it always a
        # fall back toward 0, regardless of which way the underlying value
        # itself happens to be moving, so each pulse's attack_ms/release_ms
        # map onto attack/release exactly the way their names suggest.
        # (An earlier version fed white_pulse_attack_ms/release_ms straight
        # into a smoother tracking saturation itself - that smoother picks
        # attack vs. release based on whether the target is numerically
        # rising or falling, which for the default, non-inverted pulse
        # (desaturating DOWN toward white) is backwards: release_ms ended up
        # controlling the snap INTO the pulse, attack_ms the recovery OUT of
        # it - the opposite of what the labels and tooltips promise.)
        self._smoother_beat_dark = AttackReleaseSmoother(bs.dark_pulse_attack_ms, bs.dark_pulse_release_ms)
        self._smoother_beat_white = AttackReleaseSmoother(bs.white_pulse_attack_ms, bs.white_pulse_release_ms)
        self._beat_dark_until: Optional[float] = None  # set while a dark-pulse pause is pending/active
        self._beat_white_pulse_until: Optional[float] = None  # set while a white-pulse hold is pending/active
        # Warm (0.0) vs cool (1.0) for the CURRENT true-white flash - rolled
        # fresh once per new flash (see white_pulse_cool_ratio), then held
        # for that flash's whole duration, exactly like _beat_target_hue is
        # for RGB hue - so it varies beat to beat instead of always looking
        # the same, without flickering mid-flash.
        self._beat_white_pulse_temp = 1.0
        # Last tick's smoothed white-pulse amount (see _smoother_beat_white
        # above and the comment on white_ready in _tick_beat_sync_mode) - a
        # new true-white flash may only START once the previous one has
        # actually faded back out below _WHITE_PULSE_EPSILON, not merely
        # once _beat_white_pulse_until goes back to None.
        self._last_white_amount = 0.0
        # See _WHITE_MAX_CONTINUOUS_S/_WHITE_FORCED_GAP_S/
        # _WHITE_CHAIN_GAP_TOLERANCE_S above.
        self._white_continuous_since: Optional[float] = None
        self._white_last_active_at: Optional[float] = None
        self._white_forced_gap_until: Optional[float] = None
        self.last_beat_time: Optional[float] = None

        pf = config.color_mapping.peak_flash
        self._peak_detector = BeatDetector(
            sensitivity=pf.sensitivity, min_interval_ms=pf.min_interval_ms, min_energy=pf.min_energy
        )
        self._hue_drift_deg = random.uniform(0.0, 360.0)
        self._hue_smoother_peak = HueSmoother(pf.hue_flow_ms, initial=self._hue_drift_deg)
        self._white_smoother = AttackReleaseSmoother(pf.white_attack_ms, pf.white_release_ms)
        self._loudness_smoother = AttackReleaseSmoother(pf.loudness_smoothing_ms, pf.loudness_smoothing_ms)
        self._flash_value_smoother = AttackReleaseSmoother(pf.flash_attack_ms, pf.flash_release_ms)
        self._hue_random_offset = 0.0  # persistent, only changes on a qualifying peak (see randomness)
        self.last_peak_time: Optional[float] = None

        bsw = config.color_mapping.beat_sync_white
        self._beat_detector_white = BeatDetector(
            sensitivity=bsw.sensitivity, min_interval_ms=bsw.min_interval_ms, min_energy=bsw.min_energy
        )
        self._beat_white_target_temp = random.uniform(bsw.temp_min, bsw.temp_max)
        self._temp_smoother_beat_white = AttackReleaseSmoother(bsw.temp_attack_ms, initial=self._beat_white_target_temp)
        self._smoother_beat_white_value = AttackReleaseSmoother(bsw.brightness_attack_ms, bsw.brightness_release_ms)
        self._beat_white_dark_until: Optional[float] = None
        self._history_beat_white = TimeSeriesBuffer()
        self.last_beat_time_white: Optional[float] = None

        self._chase_animator = ChaseAnimator(config.chase)
        self._group_switch_animator = GroupSwitchAnimator(config.group_switch)

        # Shared beat clock (see config.schema.RhythmConfig): ticked once per
        # visual tick, only while something actually follows it, and read by
        # every layer that opts in - so they all react to the same beats.
        self._beat_clock = BeatClock()
        self._apply_rhythm_settings()
        self._clock_beat = ClockBeat()
        self._clock_energy = 0.0
        self.clock_active = False
        # Beat Sync's "change hue every N beats" bookkeeping: the decision is
        # made on the beat itself, then used on the (possibly dark-pulse-
        # delayed) flash that follows it.
        self._beat_sync_beat_count = 0
        self._beat_hue_due = True

        # Pulse sequencer (see effects/pulse_sequencer.py): per-lamp white
        # flash state, since its flashes walk between groups and overlap.
        self._sequencer = PulseSequencer(config.sequencer)
        self._sequencing = False
        self._seq_groups: list = []
        self._seq_white_until: Dict[str, float] = {}
        self._seq_white_started: Dict[str, float] = {}
        self._seq_white_temp: Dict[str, float] = {}
        self._seq_white_hold: Dict[str, float] = {}
        self._seq_white_attack: Dict[str, float] = {}
        self._seq_white_release: Dict[str, float] = {}
        self._seq_white_brightness: Dict[str, float] = {}
        self._seq_continuous_since: Dict[str, float] = {}
        self._seq_last_active: Dict[str, float] = {}
        self._seq_forced_gap_until: Dict[str, float] = {}
        self._white_targets_preselected = False
        # white_pulse_temp_mode "alternate" / "loudness" state.
        self._white_temp_alternate = False
        self._loudness = LoudnessTracker()

        self.running = False
        self._raw_lock = threading.Lock()
        self._raw_frame: Optional[SpectrumFrame] = None
        self._last_visual_time: Optional[float] = None

        self.rate_counter_analysis = RateCounter()
        self.rate_counter_visual = RateCounter()

        # Latest results, read by the UI for display purposes only.
        self.latest_frame: Optional[SpectrumFrame] = None
        self.latest_band3_levels: Dict[str, float] = {}
        self.latest_band8_levels: list = [0.0] * len(config.bands_8)
        self.latest_lamp_colors: Dict[str, Color] = {}
        self.latest_lamp_white_targets: Dict[str, WhiteTarget] = {}  # actual DP targets for beat_sync_white mode
        # Populated by _tick_beat_sync_mode only, for lamps currently mid
        # true-white flash (see BeatSyncModeConfig's white pulse settings) -
        # reset to {} at the top of every tick_visual() so a stale entry can
        # never survive into a tick where the mode has since changed.
        self._beat_sync_white_targets: Dict[str, WhiteTarget] = {}
        # Lamps the current true-white flash was restricted to (see
        # BeatSyncModeConfig.white_pulse_target) - captured on the flash's
        # first tick and held until it ends, so a chase stepping on mid-flash
        # doesn't turn it into a burst of white on/off commands.
        self._white_pulse_lamps: Optional[Set[str]] = None
        self._white_rotate_index = -1  # white_pulse_target "rotate": the flash's own position in the Chase order

    # -- configuration -----------------------------------------------------------

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self.color_engine.update_config(config.color_mapping)
        self._apply_smoothing_settings()
        if len(self._smoother_bands8.values) != len(config.bands_8):
            self._smoother_bands8 = MultiSmoother(
                len(config.bands_8), config.color_mapping.smoothing.attack_ms, config.color_mapping.smoothing.release_ms
            )

    def _apply_smoothing_settings(self) -> None:
        sm = self.config.color_mapping.smoothing
        for s in (
            self._smoother_r,
            self._smoother_g,
            self._smoother_b,
            self._smoother_log_centroid,
            self._smoother_value,
            self._smoother_contrast,
        ):
            s.attack_ms = sm.attack_ms
            s.release_ms = sm.release_ms
        self._smoother_bands8.attack_ms = sm.attack_ms
        self._smoother_bands8.release_ms = sm.release_ms

        bs = self.config.color_mapping.beat_sync
        self._beat_detector.sensitivity = bs.sensitivity
        self._beat_detector.min_interval_ms = bs.min_interval_ms
        self._beat_detector.min_energy = bs.min_energy
        self._hue_smoother_beat.time_constant_ms = bs.hue_attack_ms
        self._smoother_beat_value.attack_ms = bs.brightness_attack_ms
        self._smoother_beat_value.release_ms = bs.brightness_release_ms
        self._smoother_beat_glide.attack_ms = bs.brightness_attack_ms
        self._smoother_beat_glide.release_ms = bs.brightness_release_ms
        self._smoother_beat_dark.attack_ms = bs.dark_pulse_attack_ms
        self._smoother_beat_dark.release_ms = bs.dark_pulse_release_ms
        self._smoother_beat_white.attack_ms = bs.white_pulse_attack_ms
        self._smoother_beat_white.release_ms = bs.white_pulse_release_ms

        pf = self.config.color_mapping.peak_flash
        self._peak_detector.sensitivity = pf.sensitivity
        self._peak_detector.min_interval_ms = pf.min_interval_ms
        self._peak_detector.min_energy = pf.min_energy
        self._hue_smoother_peak.time_constant_ms = pf.hue_flow_ms
        self._white_smoother.attack_ms = pf.white_attack_ms
        self._white_smoother.release_ms = pf.white_release_ms
        self._loudness_smoother.attack_ms = pf.loudness_smoothing_ms
        self._loudness_smoother.release_ms = pf.loudness_smoothing_ms
        self._flash_value_smoother.attack_ms = pf.flash_attack_ms
        self._flash_value_smoother.release_ms = pf.flash_release_ms

        bsw = self.config.color_mapping.beat_sync_white
        self._beat_detector_white.sensitivity = bsw.sensitivity
        self._beat_detector_white.min_interval_ms = bsw.min_interval_ms
        self._beat_detector_white.min_energy = bsw.min_energy
        self._temp_smoother_beat_white.time_constant_ms = bsw.temp_attack_ms
        self._smoother_beat_white_value.attack_ms = bsw.brightness_attack_ms
        self._smoother_beat_white_value.release_ms = bsw.brightness_release_ms

        self._chase_animator.update_config(self.config.chase)
        self._group_switch_animator.update_config(self.config.group_switch)
        self._apply_rhythm_settings()
        self._sequencer.update_config(self.config.sequencer)

        self.lamp_manager.set_network_config(self.config.network, sm.min_change_threshold)

    def _apply_rhythm_settings(self) -> None:
        rh = self.config.rhythm
        clock = self._beat_clock
        clock.sensitivity = rh.sensitivity
        clock.min_interval_ms = rh.min_interval_ms
        clock.min_energy = rh.min_energy
        clock.tempo_lock = rh.tempo_lock
        clock.lead_ms = rh.lead_ms
        clock.beats_per_bar = rh.beats_per_bar
        clock.accent_ratio = rh.accent_ratio

    @property
    def sequencer_status(self) -> Optional[dict]:
        """Phrase position and loudness level while the pulse sequencer runs, for display."""
        if not self._sequencing:
            return None
        return {
            "phrase_bar": self._sequencer.phrase_bar,
            "phrase_bars": self.config.sequencer.phrase_bars,
            "level": self._sequencer.level,
        }

    @property
    def latest_clock_beat(self) -> ClockBeat:
        """Latest shared beat clock state, for display (tempo, lock, bar position)."""
        return self._clock_beat

    def start(self) -> None:
        self.running = True
        self._last_visual_time = None
        logger.info("Visualization engine started (mode=%s)", self.config.color_mapping.mode)

    def stop(self) -> None:
        self.running = False
        logger.info("Visualization engine stopped")

    # -- fast tick: audio -> FFT only -----------------------------------------------

    def tick_analysis(self) -> None:
        if not self.running or not self.audio.has_data():
            return
        samples = self.audio.read_latest(self.config.audio.fft_size)
        self.fft.set_samplerate(self.audio.samplerate)
        frame = self.fft.compute(samples)
        with self._raw_lock:
            self._raw_frame = frame
        self.latest_frame = frame
        self.rate_counter_analysis.tick()

    # -- slower tick: bands -> smoothing -> color -> lamps ----------------------------

    def tick_visual(self) -> None:
        if not self.running:
            return
        with self._raw_lock:
            frame = self._raw_frame
        if frame is None:
            return

        now = time.perf_counter()
        dt = now - self._last_visual_time if self._last_visual_time else 1.0 / max(self.config.network.visual_update_hz, 1.0)
        self._last_visual_time = now
        self.rate_counter_visual.tick()
        wall_now = time.time()

        selected_ids = self.lamp_manager.selected_device_ids()
        if not selected_ids:
            self.latest_lamp_colors = {}
            return

        mode = self.config.color_mapping.mode
        self._beat_sync_white_targets = {}  # only _tick_beat_sync_mode ever (re-)populates this
        self._white_targets_preselected = False
        self._tick_beat_clock(frame, wall_now, mode)

        if mode == "beat_sync_white":
            # A different physical DP (brightness+temp, work_mode='white')
            # than every other mode (RGB colour DP) - handled as its own
            # branch with its own push path. The RGB Chase overlay doesn't
            # apply here (it operates on hue/saturation, meaningless for a
            # white-balance value); WhiteChaseAnimator is a separate effect
            # used by the standalone manual app instead.
            self._white_pulse_lamps = None
            white_targets, preview_colors = self._tick_beat_sync_white_mode(frame, dt, wall_now, selected_ids)
            self.latest_lamp_white_targets = white_targets
            self.latest_lamp_colors = preview_colors  # UI-swatch approximation only
            if white_targets:
                self.lamp_manager.push_white_targets(white_targets)
            return

        colors: Dict[str, Color] = {}

        if mode in ("rgb_freq", "custom"):
            colors = self._tick_rgb_modes(frame, dt, wall_now, selected_ids, mode)
        elif mode == "hsv_music":
            colors = self._tick_hsv_mode(frame, dt, wall_now, selected_ids)
        elif mode == "8band_spectrum":
            colors = self._tick_spectrum_mode(frame, dt, wall_now, selected_ids)
        elif mode == "beat_sync":
            colors = self._tick_beat_sync_mode(frame, dt, wall_now, selected_ids)
        elif mode == "peak_flash":
            colors = self._tick_peak_flash_mode(frame, dt, wall_now, selected_ids)
        else:
            logger.warning("Unknown color mapping mode '%s'", mode)

        if colors and self.config.chase.enabled:
            colors = self._apply_chase_overlay(colors, frame, dt, wall_now, selected_ids)

        if colors and self.config.group_switch.enabled:
            colors = self._apply_group_switch_overlay(colors, frame, dt, wall_now, selected_ids)

        self._restrict_white_targets_to_moving_lamps(selected_ids)

        if self._beat_sync_white_targets:
            # Some lamps are mid true-white flash this tick (Beat Sync mode
            # only) - split them out so each lamp gets exactly one command
            # this tick, never both a colour and a white target (the worker
            # mailbox would just have the second call clobber the first).
            rgb_colors = {k: v for k, v in colors.items() if k not in self._beat_sync_white_targets}
            self.latest_lamp_colors = {
                **rgb_colors,
                **{k: t.to_preview_color() for k, t in self._beat_sync_white_targets.items()},
            }
            self.latest_lamp_white_targets = dict(self._beat_sync_white_targets)
            if rgb_colors:
                self.lamp_manager.push_colors(rgb_colors)
            self.lamp_manager.push_white_targets(self._beat_sync_white_targets)
        else:
            self.latest_lamp_colors = colors
            self.latest_lamp_white_targets = {}
            if colors:
                self.lamp_manager.push_colors(colors)

    def _tick_beat_clock(self, frame: SpectrumFrame, wall_now: float, mode: str) -> None:
        rh = self.config.rhythm
        chase, gs = self.config.chase, self.config.group_switch
        self.clock_active = (
            (rh.shared_clock and mode in ("beat_sync", "beat_sync_white"))
            or (chase.enabled and chase.sync_mode == "clock")
            or (gs.enabled and gs.sync_mode == "clock")
        )
        if not self.clock_active:
            self._clock_beat = ClockBeat()
            return
        self._clock_energy = band_energy(frame, rh.detect_low_hz, rh.detect_high_hz)
        self._clock_beat = self._beat_clock.update(self._clock_energy, wall_now)

    @staticmethod
    def _pulse_trigger_allows(trigger: str, clock: Optional[ClockBeat]) -> bool:
        """BeatSyncModeConfig.dark/white_pulse_trigger: which beats may roll a
        pulse's probability. Without the shared clock there's no accent/bar
        information, so every trigger behaves like "random"."""
        if clock is None:
            return True
        if trigger == "accent":
            return clock.is_accent
        if trigger == "downbeat":
            return clock.is_downbeat
        return True

    def _restrict_white_targets_to_moving_lamps(self, selected_ids) -> None:
        """Applies BeatSyncModeConfig.white_pulse_target: narrows this tick's
        true-white targets down to the lamps Chase's highlight / Group
        Switch's active group is on. Runs after the overlays have ticked, so
        it sees their current position."""
        if not self._beat_sync_white_targets or self._white_targets_preselected:
            self._white_pulse_lamps = None
            return
        target = self.config.color_mapping.beat_sync.white_pulse_target
        if self._white_pulse_lamps is None:
            lamps: Optional[Set[str]] = None
            if target == "chase" and self.config.chase.enabled:
                groups = get_chase_groups(self.config.per_lamp_effects, selected_ids)
                if len(groups) >= 2:
                    lamps = self._chase_animator.active_device_ids(groups)
            elif target == "group" and self.config.group_switch.enabled:
                groups = get_group_switch_groups(self.config.per_lamp_effects, selected_ids)
                if len(groups) >= 2:
                    lamps = self._group_switch_animator.active_device_ids(groups)
            elif target == "rotate":
                # Its own rotation through the Chase order: exactly one
                # position per flash, so it can't skip a lamp the way
                # following a fast-moving Chase highlight can.
                positions = get_chase_groups(self.config.per_lamp_effects, selected_ids)
                if len(positions) >= 2:
                    step = -1 if self.config.chase.reverse else 1
                    self._white_rotate_index = (self._white_rotate_index + step) % len(positions)
                    lamps = spread_positions(
                        positions, self._white_rotate_index, self.config.color_mapping.beat_sync.white_pulse_rotators
                    )
            # None = "all" (or the chosen effect has no highlight to follow)
            self._white_pulse_lamps = lamps if lamps is not None else set(self._beat_sync_white_targets)
        self._beat_sync_white_targets = {
            k: v for k, v in self._beat_sync_white_targets.items() if k in self._white_pulse_lamps
        }

    # -- per-mode implementations ---------------------------------------------------

    def _tick_rgb_modes(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids, mode: str) -> Dict[str, Color]:
        cfg = self.config.color_mapping.rgb if mode == "rgb_freq" else self.config.color_mapping.custom

        lvl_r = band_energy(frame, cfg.r.low_hz, cfg.r.high_hz)
        lvl_g = band_energy(frame, cfg.g.low_hz, cfg.g.high_hz)
        lvl_b = band_energy(frame, cfg.b.low_hz, cfg.b.high_hz)

        sr = self._smoother_r.update(lvl_r, dt)
        sg = self._smoother_g.update(lvl_g, dt)
        sb = self._smoother_b.update(lvl_b, dt)
        self.latest_band3_levels = {"R": sr, "G": sg, "B": sb}
        self._history_3.push(wall_now, np.array([sr, sg, sb]))

        colors: Dict[str, Color] = {}
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_3.sample_at(sample_t)
            if vals is None:
                vals = np.array([sr, sg, sb])
            mult = effect.sensitivity_mult if effect else 1.0
            mult_r = mult * (effect.band_gains.get("R", 1.0) if effect else 1.0)
            mult_g = mult * (effect.band_gains.get("G", 1.0) if effect else 1.0)
            mult_b = mult * (effect.band_gains.get("B", 1.0) if effect else 1.0)
            color = self.color_engine.compute_rgb(vals[0], vals[1], vals[2], cfg, mult_r, mult_g, mult_b)
            if effect:
                color = apply_per_lamp_effect(color, effect)
            colors[device_id] = color
        return colors

    def _tick_hsv_mode(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids) -> Dict[str, Color]:
        centroid = spectral_centroid_hz(frame)
        log_c = self._smoother_log_centroid.update(math.log(max(centroid, 1.0)), dt)
        centroid_s = math.exp(log_c)

        overall = band_energy(frame, 20, 16000)
        value_s = self._smoother_value.update(overall, dt)

        contrast = spectral_contrast(frame)
        contrast_s = self._smoother_contrast.update(contrast, dt)

        self.latest_band3_levels = {"centroid_hz": centroid_s, "value": value_s, "contrast": contrast_s}
        self._history_3.push(wall_now, np.array([centroid_s, value_s, contrast_s]))

        colors: Dict[str, Color] = {}
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_3.sample_at(sample_t)
            if vals is None:
                vals = np.array([centroid_s, value_s, contrast_s])
            mult = effect.sensitivity_mult if effect else 1.0
            color = self.color_engine.compute_hsv(vals[0], vals[1] * mult, vals[2])
            if effect:
                color = apply_per_lamp_effect(color, effect)
            colors[device_id] = color
        return colors

    def _tick_spectrum_mode(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids) -> Dict[str, Color]:
        bands = self.config.bands_8
        raw_levels = np.array(band_energies(frame, bands))
        smoothed = self._smoother_bands8.update(raw_levels, dt)
        self.latest_band8_levels = list(smoothed)
        self._history_8.push(wall_now, smoothed)

        colors: Dict[str, Color] = {}
        auto_index = 0
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            if effect is not None and effect.band_index is not None:
                band_idx = effect.band_index % len(bands)
            else:
                band_idx = auto_index % len(bands)
                auto_index += 1

            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_8.sample_at(sample_t)
            level = float(vals[band_idx]) if vals is not None else float(smoothed[band_idx])

            mult = effect.sensitivity_mult if effect else 1.0
            if effect is not None:
                mult *= effect.band_gains.get(bands[band_idx].name, 1.0)

            color = self.color_engine.compute_band_color(level * mult, band_idx)
            if effect is not None:
                color = apply_per_lamp_effect(color, effect)
            colors[device_id] = color
        return colors

    def _tick_beat_sync_mode(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids) -> Dict[str, Color]:
        """On every detected beat: snap to a fresh, fully-saturated hue at
        full brightness. Between beats: hold the hue and let brightness decay
        toward a dim baseline (a classic percussive attack/decay envelope) -
        this is what makes the rhythm visually obvious, unlike the smoothly
        continuous blending the other modes use.

        Optionally, a random subset of beats first get a brief "dark pulse"
        (a rhythm-synced pause toward black) before the flash actually
        happens - a real-time system can only react to a beat as it occurs,
        not anticipate one, so the pause always starts right on the trigger
        and the color flash is simply delayed until the pause ends.

        Independently, on that SAME beat, a white pulse can also be rolled
        (see white_pulse_* config) - briefly pushing saturation toward one
        extreme, a hi-hat/cymbal-style accent that snaps to near-white (or,
        inverted, to fully vivid) for an instant. Dark and white pulses each
        roll their own independent probability on every beat - deliberately
        simple: an earlier version tried to make them mutually exclusive by
        comparing their two configurable frequency bands (so a kick-like hit
        could only trigger dark, a hi-hat-like hit only white), but that
        comparison isn't reliable in practice - averaging a wide frequency
        band's level dilutes a sharp, narrow transient (a real kick's energy
        lives in a narrow ~40-100 Hz sliver, not evenly across a whole
        "0-6000 Hz" band) enough that it stopped firing reliably at all.
        Independent rolls trade "guaranteed never both on the same beat" for
        "always works"."""
        cfg = self.config.color_mapping.beat_sync

        clock = self._clock_beat if self.config.rhythm.shared_clock else None
        if clock is not None:
            raw_energy = self._clock_energy
            is_beat = clock.is_beat
        else:
            raw_energy = band_energy(frame, cfg.detect_low_hz, cfg.detect_high_hz)
            is_beat = self._beat_detector.update(raw_energy, wall_now)

        # Dark pulse only needs "not currently active" - it's a plain RGB
        # brightness multiplier, so even a fast chain of back-to-back pulses
        # is at worst a rapid strobe (arguably the point, at a high
        # probability), never a lamp stuck in some other physical state.
        # White pulse ALSO requires the previous one to have fully faded
        # below the epsilon (not just ended) before a new one can start -
        # that extra gate matters there specifically because
        # a white pulse briefly switches the lamp's real WHITE
        # work_mode on, and immediately re-triggering before the fade-out
        # finished was, in practice, keeping lamps pinned in that physical
        # mode almost continuously on fast/dense tracks (see
        # test_rapid_repeated_beats_do_not_extend_pulse_forever).
        dark_ready = self._beat_dark_until is None
        white_ready = self._beat_white_pulse_until is None and self._last_white_amount <= _WHITE_PULSE_EPSILON
        # With the pulse sequencer running (and the clock locked, so there's
        # a grid to place pulses on) it decides every pulse; the per-beat
        # probability rolls below step aside.
        sequencer_on = self.config.sequencer.enabled and clock is not None
        self._sequencing = sequencer_on and clock.locked
        roll_pulses = not self._sequencing

        flash_now = False
        if is_beat:
            every_n = max(1, int(cfg.hue_every_n_beats))
            if clock is not None:
                self._beat_hue_due = beat_divides(clock, every_n)
            else:
                self._beat_hue_due = self._beat_sync_beat_count % every_n == 0
                self._beat_sync_beat_count += 1

            # Also gated on "not currently active" for dark, so a fixed-
            # duration pulse can never be extended/re-rolled mid-flight by a
            # later beat (see the class's docstring) - only whether/when the
            # NEXT one can start differs between dark and white, per above.
            if roll_pulses and dark_ready and (
                cfg.dark_pulse_enabled
                and cfg.dark_pulse_probability > 0.0
                and cfg.dark_pulse_duration_ms > 0.0
                and self._pulse_trigger_allows(cfg.dark_pulse_trigger, clock)
                and random.random() < cfg.dark_pulse_probability
            ):
                self._beat_dark_until = wall_now + cfg.dark_pulse_duration_ms / 1000.0
            elif self._beat_dark_until is None:
                flash_now = True

            if roll_pulses and white_ready and (
                cfg.white_pulse_enabled
                and cfg.white_pulse_probability > 0.0
                and cfg.white_pulse_duration_ms > 0.0
                and self._pulse_trigger_allows(cfg.white_pulse_trigger, clock)
                and random.random() < cfg.white_pulse_probability
            ):
                self._beat_white_pulse_until = wall_now + cfg.white_pulse_duration_ms / 1000.0
                # Warm vs cool for THIS flash, rolled once here (not per
                # tick) so it stays constant for the flash's whole duration.
                self._beat_white_pulse_temp = self._white_pulse_temp(cfg, clock)

        if sequencer_on:
            self._run_sequencer(frame, dt, wall_now, selected_ids, cfg)
        if cfg.white_pulse_temp_mode == "loudness":
            self._loudness.update(band_energy(frame, 20.0, 16000.0), wall_now, dt)

        if self._beat_dark_until is not None and wall_now >= self._beat_dark_until:
            self._beat_dark_until = None
            flash_now = True

        if self._beat_white_pulse_until is not None and wall_now >= self._beat_white_pulse_until:
            self._beat_white_pulse_until = None

        if flash_now:
            if self._beat_hue_due:
                self._beat_target_hue = self._pick_next_beat_hue(cfg, frame)
                self._beat_hue_due = False
            self.last_beat_time = wall_now

        in_dark_pulse = self._beat_dark_until is not None
        in_white_pulse = self._beat_white_pulse_until is not None
        target_value = cfg.flash_brightness if flash_now else cfg.sustain_brightness
        value_s = self._smoother_beat_value.update(target_value, dt)
        if not cfg.fade_brightness:
            value_s = cfg.flash_brightness  # full strength all the time (dark pulses still dip it below)
        glide = self._smoother_beat_glide.update(1.0 if flash_now else 0.0, dt)
        target_hue = self._beat_target_hue
        if cfg.glide_hue:
            progress = self._hue_glide_progress(cfg, glide, wall_now)
            toward = self._upcoming_beat_hue(cfg)
            direction = -1.0 if ((toward - self._beat_target_hue + 180.0) % 360.0) - 180.0 < 0.0 else 1.0
            target_hue = (self._beat_target_hue + direction * cfg.hue_glide_deg * progress) % 360.0
        hue_s = self._hue_smoother_beat.update(target_hue, dt)
        # Dark pulse is layered on top as an independent multiplicative dip,
        # smoothed on its own attack/release - not folded into target_value
        # above, since that would tie its timing to brightness_attack_ms/
        # release_ms instead of its own dark_pulse_attack_ms/release_ms.
        dark_amount = self._smoother_beat_dark.update(1.0 if in_dark_pulse else 0.0, dt)
        if dark_amount > 0.0:
            value_s = value_s * (1.0 - dark_amount * cfg.dark_pulse_depth)

        # Smoothed 0..1 "how deep into the white pulse are we" fraction (see
        # the comment on _smoother_beat_white above). It's used below (per
        # lamp) to switch that lamp's physical WHITE work_mode on for the
        # pulse. The RGB color is never desaturated for it: the white comes
        # from the white LEDs only, and the RGB LEDs keep all their (much
        # weaker) output for color - which is also what the lamp resumes to.
        white_amount = self._smoother_beat_white.update(1.0 if in_white_pulse else 0.0, dt)
        self._last_white_amount = white_amount
        saturation_s = cfg.saturation

        self.latest_band3_levels = {
            "beat_energy": raw_energy,
            "hue": hue_s,
            "value": value_s,
            "saturation": saturation_s,
            "beat": 1.0 if is_beat else 0.0,
            "dark_pulse": 1.0 if in_dark_pulse else 0.0,
            "white_pulse": 1.0 if in_white_pulse else 0.0,
        }
        self._history_beat.push(wall_now, np.array([hue_s, value_s, saturation_s]))

        # True-white is deliberately NOT sampled through the per-lamp phase
        # offset below like hue/value/saturation are - it's a brief, binary
        # flash (duration_ms + attack is often well under 200ms), and
        # different lamps sampling it at different phase-shifted instants
        # could each just barely miss the narrow window entirely, making the
        # flash look like it lands on arbitrary/random lamps instead of the
        # synchronized whole-installation strobe it's meant to be. Every
        # selected lamp enters/exits it at exactly the same wall-clock
        # instant, using this tick's raw amount, regardless of phase offset.
        # The target itself is also deliberately CONSTANT while active, not
        # ramped by white_amount tick-by-tick - a real Tuya bulb needs two
        # separate DP writes per white-mode command (colourtemp + brightness,
        # see LampDevice.set_white()), so a smooth per-tick ramp multiplies
        # into a burst of commands to every selected lamp at once (they're
        # all synchronized, above) - in practice enough to overwhelm the
        # LAN/Wi-Fi and tinytuya's own per-socket state handling, which
        # showed up as brightness never quite reaching its peak and
        # inconsistent behavior between lamps. A constant target lets the
        # worker's existing min_change_threshold dedup collapse this down to
        # one real send on entry and one on exit (reverting to RGB), exactly
        # like every other beat-triggered event in this app already works.
        use_true_white_now = white_amount > _WHITE_PULSE_EPSILON

        # Absolute safety ceiling (see _WHITE_MAX_CONTINUOUS_S) - independent
        # of the individual-pulse cooldown logic above, which only guarantees
        # any ONE pulse can't be extended/re-triggered early; it does not
        # bound how long a CHAIN of separate short pulses can keep the
        # global "white active" state continuously true on a dense/fast
        # track. Forces a real, minimum-length RGB-only window
        # (_WHITE_FORCED_GAP_S) so every lamp - including one whose own
        # worker thread is currently rate-limited/backed off - gets at least
        # one real chance to see and send the reverted color, not just a
        # single engine tick's worth of gap that a slow worker could easily
        # never observe.
        if use_true_white_now:
            if self._white_forced_gap_until is not None and wall_now < self._white_forced_gap_until:
                use_true_white_now = False
            else:
                self._white_forced_gap_until = None
                # A brief gap between two chained pulses (the per-pulse
                # cooldown above) does NOT count as the chain having ended -
                # only a gap longer than _WHITE_CHAIN_GAP_TOLERANCE_S does.
                # Without this, that natural inter-pulse gap would reset
                # _white_continuous_since on every single pulse, so a long
                # chain of short pulses could never actually reach the
                # ceiling below.
                if (
                    self._white_continuous_since is None
                    or self._white_last_active_at is None
                    or (wall_now - self._white_last_active_at) > _WHITE_CHAIN_GAP_TOLERANCE_S
                ):
                    self._white_continuous_since = wall_now
                self._white_last_active_at = wall_now
                if wall_now - self._white_continuous_since > _WHITE_MAX_CONTINUOUS_S:
                    use_true_white_now = False
                    self._white_continuous_since = None
                    self._white_forced_gap_until = wall_now + _WHITE_FORCED_GAP_S

        # With instant lamp transitions (DP 28) the flash's brightness follows
        # the attack/release envelope - one command per step. With "legacy"
        # every white brightness change costs several commands (work_mode +
        # two DPs), so it stays one constant target there and the bulb's own
        # ~0.7 s fade softens it instead (see NetworkConfig.lamp_transitions).
        shaped = self.config.network.lamp_transitions != "legacy"
        shared_white_target = (
            WhiteTarget(
                brightness=cfg.white_pulse_white_brightness * (white_amount if shaped else 1.0),
                temp=self._beat_white_pulse_temp,
            ).clamped()
            if use_true_white_now
            else None
        )

        colors: Dict[str, Color] = {}
        white_targets: Dict[str, WhiteTarget] = {}
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_beat.sample_at(sample_t)
            if vals is None:
                vals = np.array([hue_s, value_s, saturation_s])
            mult = effect.sensitivity_mult if effect else 1.0

            if self._sequencing and self._seq_white_active(device_id, wall_now):
                brightness = cfg.white_pulse_white_brightness * self._seq_white_envelope(device_id, wall_now, cfg)
                if effect is not None:
                    brightness *= effect.white_pulse_brightness_mult
                white_targets[device_id] = WhiteTarget(
                    brightness=brightness, temp=self._seq_white_temp.get(device_id, 1.0)
                ).clamped()
            elif shared_white_target is not None:
                if effect is not None and effect.white_pulse_brightness_mult != 1.0:
                    white_targets[device_id] = WhiteTarget(
                        brightness=shared_white_target.brightness * effect.white_pulse_brightness_mult,
                        temp=shared_white_target.temp,
                    ).clamped()
                else:
                    white_targets[device_id] = shared_white_target

            color = self.color_engine.compute_beat_sync(vals[0], vals[2], clip(vals[1] * mult))
            if effect is not None:
                color = apply_per_lamp_effect(color, effect)
            colors[device_id] = color
        self._beat_sync_white_targets = white_targets
        self._white_targets_preselected = self._sequencing
        return colors

    # -- pulse sequencer ------------------------------------------------------------------

    def _run_sequencer(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids, cfg) -> None:
        position = self._beat_clock.position(wall_now) if self._sequencing else None
        loudness = band_energy(frame, 20.0, 16000.0)
        if self.config.sequencer.walk_positions == "chase_order":
            self._seq_groups = get_chase_groups(self.config.per_lamp_effects, selected_ids)
        else:
            self._seq_groups = get_group_switch_groups(self.config.per_lamp_effects, selected_ids)
        events = self._sequencer.tick(
            position, loudness, wall_now, dt, len(self._seq_groups), self.config.rhythm.beats_per_bar
        )
        for event in events:
            self._apply_sequencer_event(event, cfg, wall_now, selected_ids)

    def _apply_sequencer_event(self, event: PulseEvent, cfg, wall_now: float, selected_ids) -> None:
        # Beat Sync's pulse Enabled checkboxes stay the master switches.
        if event.kind == "dark":
            if cfg.dark_pulse_enabled and self._beat_dark_until is None and cfg.dark_pulse_duration_ms > 0.0:
                self._beat_dark_until = wall_now + cfg.dark_pulse_duration_ms / 1000.0 * event.length
            return
        if not cfg.white_pulse_enabled:
            return
        groups = self._seq_groups
        if event.group is not None and len(groups) >= 2:
            lamps = spread_positions(groups, event.group % len(groups), cfg.white_pulse_rotators)
        else:
            lamps = selected_ids
        # The flash's shape: the white pulse settings, scaled per flash by the
        # sequencer's pulse dynamics (all 1.0 when that's off).
        attack = cfg.white_pulse_attack_ms / 1000.0 * event.attack
        hold = cfg.white_pulse_duration_ms / 1000.0 * event.length * event.hold
        release = cfg.white_pulse_release_ms / 1000.0 * event.release
        length = attack + hold + release
        gap = self.config.sequencer.min_group_gap_ms / 1000.0
        temp = self._white_pulse_temp(cfg, self._clock_beat, event)
        for lamp in lamps:
            if wall_now < self._seq_white_until.get(lamp, 0.0):
                continue  # still mid-flash
            if wall_now - self._seq_white_started.get(lamp, -1e9) < gap:
                continue  # protect the bulb: not two flashes back to back
            self._seq_white_started[lamp] = wall_now
            self._seq_white_until[lamp] = wall_now + length
            self._seq_white_attack[lamp] = attack
            self._seq_white_hold[lamp] = hold
            self._seq_white_release[lamp] = release
            self._seq_white_brightness[lamp] = event.brightness
            self._seq_white_temp[lamp] = temp

    def _white_pulse_temp(self, cfg, clock: Optional[ClockBeat], event: Optional[PulseEvent] = None) -> float:
        """Warm (0.0) .. cool (1.0) for a new white flash - see
        BeatSyncModeConfig.white_pulse_temp_mode. `event`: the sequencer's
        flash (knows its 16th in the bar); otherwise the flash is on `clock`'s
        beat."""
        mode = cfg.white_pulse_temp_mode
        if mode == "alternate":
            self._white_temp_alternate = not self._white_temp_alternate
            return 1.0 if self._white_temp_alternate else 0.0
        if mode == "loudness":
            return self._loudness.intensity
        if mode in ("bar", "phrase"):
            position = None
            if event is not None:
                position = (event.bar_step, event.steps_per_bar, event.phrase_bar, event.phrase_bars)
            elif clock is not None and clock.is_beat:
                bpb = max(1, self.config.rhythm.beats_per_bar)
                phrase_bars = max(1, int(self.config.sequencer.phrase_bars))
                position = (clock.bar_position * 4, bpb * 4, (clock.aligned_index // bpb) % phrase_bars, phrase_bars)
            if position is not None:
                bar_step, steps_per_bar, phrase_bar, phrase_bars = position
                if mode == "bar":
                    return metric_weight(bar_step, steps_per_bar)
                return phrase_progress(phrase_bar, phrase_bars, bar_step, steps_per_bar)
        return 1.0 if random.random() < cfg.white_pulse_cool_ratio else 0.0

    def _seq_white_envelope(self, lamp: str, wall_now: float, cfg) -> float:
        """0..1 brightness of a sequencer flash: ramps up over attack, holds,
        ramps down over release (constant 1 with legacy transitions - see
        the comment on shared_white_target in _tick_beat_sync_mode)."""
        peak = self._seq_white_brightness.get(lamp, 1.0)
        if self.config.network.lamp_transitions == "legacy":
            return peak
        t = wall_now - self._seq_white_started.get(lamp, wall_now)
        attack = self._seq_white_attack.get(lamp, cfg.white_pulse_attack_ms / 1000.0)
        hold = self._seq_white_hold.get(lamp, cfg.white_pulse_duration_ms / 1000.0)
        release = self._seq_white_release.get(lamp, cfg.white_pulse_release_ms / 1000.0)
        if attack > 0.0 and t < attack:
            return peak * max(0.05, t / attack)
        if t < attack + hold:
            return peak
        if release > 0.0:
            return peak * max(0.0, 1.0 - (t - attack - hold) / release)
        return 0.0

    def _seq_white_active(self, lamp: str, wall_now: float) -> bool:
        """Is this lamp mid sequencer flash - with the per-lamp version of the
        continuous-white safety ceiling (see _WHITE_MAX_CONTINUOUS_S)."""
        if wall_now >= self._seq_white_until.get(lamp, 0.0):
            return False
        if wall_now < self._seq_forced_gap_until.get(lamp, 0.0):
            return False
        last = self._seq_last_active.get(lamp)
        if lamp not in self._seq_continuous_since or last is None or wall_now - last > _SEQ_WHITE_GAP_TOLERANCE_S:
            self._seq_continuous_since[lamp] = wall_now
        self._seq_last_active[lamp] = wall_now
        if wall_now - self._seq_continuous_since[lamp] > _WHITE_MAX_CONTINUOUS_S:
            self._seq_continuous_since.pop(lamp, None)
            self._seq_forced_gap_until[lamp] = wall_now + _WHITE_FORCED_GAP_S
            return False
        return True

    def _pick_next_beat_hue(self, cfg, frame: SpectrumFrame) -> float:
        if cfg.hue_mode == "step":
            self._beat_hue_cursor = (self._beat_hue_cursor + cfg.hue_step_deg) % 360.0
            return self._beat_hue_cursor

        if cfg.hue_mode == "spectrum":
            centroid = spectral_centroid_hz(frame)
            hsv_cfg = self.config.color_mapping.hsv
            high = 16000.0
            t = (math.log(clip(centroid, 20.0, high)) - math.log(20.0)) / (math.log(high) - math.log(20.0))
            return lerp(hsv_cfg.hue_min_deg, hsv_cfg.hue_max_deg, t)

        # "random" (default): pick a hue that's visibly different from the
        # last one, so every beat gives a clearly new color instead of
        # sometimes landing right next to the previous hue by chance. If the
        # hue glide already picked the next color to glide toward, land
        # exactly on that one.
        if self._beat_upcoming_hue is not None:
            candidate, self._beat_upcoming_hue = self._beat_upcoming_hue, None
            return candidate
        return self._random_hue_away_from(self._beat_target_hue, cfg.min_hue_jump_deg)

    @staticmethod
    def _random_hue_away_from(reference: float, min_jump: float) -> float:
        candidate = random.uniform(0.0, 360.0)
        for _ in range(8):
            delta = abs(((candidate - reference + 180.0) % 360.0) - 180.0)
            if delta >= min_jump:
                break
            candidate = random.uniform(0.0, 360.0)
        return candidate

    def _hue_glide_progress(self, cfg, decay_glide: float, wall_now: float) -> float:
        """0 right on a beat .. 1 by the next one. Timing "beat": evenly over
        the beat interval (the shared clock's tempo, or Beat Sync's own
        detector's average interval) so the color keeps moving until the next
        hit. Timing "decay", or no tempo known yet: follows the flash's decay."""
        if cfg.hue_glide_timing == "beat" and self.last_beat_time is not None:
            if self.config.rhythm.shared_clock:
                period = self._clock_beat.period_s
            else:
                period = self._beat_detector.average_interval_s()
            if period:
                return clip((wall_now - self.last_beat_time) / period)
        return 1.0 - decay_glide

    def _upcoming_beat_hue(self, cfg) -> float:
        """The color the next hue change will land on - the hue glide moves
        in its direction, so the glide flows into the next color."""
        if cfg.hue_mode == "step":
            return (self._beat_hue_cursor + cfg.hue_step_deg) % 360.0
        if cfg.hue_mode == "random":
            if self._beat_upcoming_hue is None:
                self._beat_upcoming_hue = self._random_hue_away_from(self._beat_target_hue, cfg.min_hue_jump_deg)
            return self._beat_upcoming_hue
        # "spectrum": the next color depends on the music at that moment, so
        # there's nothing to glide toward yet - drift one hue step onward.
        return (self._beat_target_hue + cfg.hue_step_deg) % 360.0

    def _tick_beat_sync_white_mode(
        self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids
    ) -> "Tuple[Dict[str, WhiteTarget], Dict[str, Color]]":
        """Same rhythm-reactive envelope as Beat Sync (including dark
        pulses), but the "hue" being jumped between beats is a color
        TEMPERATURE (warm<->cool) instead - drives the bulb's WHITE
        work_mode (brightness + temp_value_v2) rather than RGB. Returns
        (real WhiteTargets to send, approximate RGB preview Colors for the
        UI only)."""
        cfg = self.config.color_mapping.beat_sync_white

        if self.config.rhythm.shared_clock:
            raw_energy = self._clock_energy
            is_beat = self._clock_beat.is_beat
        else:
            raw_energy = band_energy(frame, cfg.detect_low_hz, cfg.detect_high_hz)
            is_beat = self._beat_detector_white.update(raw_energy, wall_now)

        flash_now = False
        if is_beat:
            if (
                cfg.dark_pulse_probability > 0.0
                and cfg.dark_pulse_duration_ms > 0.0
                and random.random() < cfg.dark_pulse_probability
            ):
                self._beat_white_dark_until = wall_now + cfg.dark_pulse_duration_ms / 1000.0
            else:
                self._beat_white_dark_until = None
                flash_now = True

        if self._beat_white_dark_until is not None and wall_now >= self._beat_white_dark_until:
            self._beat_white_dark_until = None
            flash_now = True

        if flash_now:
            self._beat_white_target_temp = self._pick_next_white_temp(cfg)
            self.last_beat_time_white = wall_now

        in_dark_pulse = self._beat_white_dark_until is not None
        temp_s = self._temp_smoother_beat_white.update(self._beat_white_target_temp, dt)

        if in_dark_pulse:
            target_value = cfg.sustain_brightness * (1.0 - cfg.dark_pulse_depth)
        elif flash_now:
            target_value = cfg.flash_brightness
        else:
            target_value = cfg.sustain_brightness
        value_s = self._smoother_beat_white_value.update(target_value, dt)

        self.latest_band3_levels = {
            "beat_energy": raw_energy,
            "temp": temp_s,
            "value": value_s,
            "beat": 1.0 if is_beat else 0.0,
            "dark_pulse": 1.0 if in_dark_pulse else 0.0,
        }
        self._history_beat_white.push(wall_now, np.array([temp_s, value_s]))

        targets: Dict[str, WhiteTarget] = {}
        preview: Dict[str, Color] = {}
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_beat_white.sample_at(sample_t)
            if vals is None:
                vals = np.array([temp_s, value_s])
            mult = effect.sensitivity_mult * (effect.brightness_mult if effect else 1.0) if effect else 1.0
            target = WhiteTarget(brightness=vals[1] * mult, temp=vals[0]).clamped()
            targets[device_id] = target
            preview[device_id] = target.to_preview_color()
        return targets, preview

    def _pick_next_white_temp(self, cfg) -> float:
        if cfg.temp_mode == "alternate":
            # Ping-pong between the warm and cool ends of the range.
            dist_to_max = abs(self._beat_white_target_temp - cfg.temp_max)
            dist_to_min = abs(self._beat_white_target_temp - cfg.temp_min)
            return cfg.temp_min if dist_to_max < dist_to_min else cfg.temp_max

        # "random" (default): pick a temp visibly different from the last one.
        span = max(cfg.temp_max - cfg.temp_min, 1e-6)
        candidate = cfg.temp_min + random.random() * span
        for _ in range(8):
            if abs(candidate - self._beat_white_target_temp) >= min(cfg.min_temp_jump, span):
                break
            candidate = cfg.temp_min + random.random() * span
        return candidate

    def _tick_peak_flash_mode(self, frame: SpectrumFrame, dt: float, wall_now: float, selected_ids) -> Dict[str, Color]:
        """Sensitive broadband peak detection drives a brightness flash (on
        top of a loudness-tracking baseline), treble energy blends the
        output toward white, and hue continuously, slowly flows over time
        instead of snapping - see PeakFlashModeConfig's docstring for the
        full rationale."""
        cfg = self.config.color_mapping.peak_flash

        raw_energy = band_energy(frame, cfg.detect_low_hz, cfg.detect_high_hz)
        is_peak = self._peak_detector.update(raw_energy, wall_now)
        if is_peak:
            self.last_peak_time = wall_now
            # Randomness factor: on this detected (music-synced) peak, roll a
            # chance to inject a random hue jump on top of the continuous
            # flow below. The jump persists (accumulates) rather than
            # snapping back, so the color story keeps going from wherever it
            # lands - 0 = never jumps (pure smooth flow), 1 = jumps every peak.
            if cfg.randomness > 0.0 and random.random() < cfg.randomness:
                jump = random.uniform(-cfg.random_jump_range_deg, cfg.random_jump_range_deg)
                self._hue_random_offset = (self._hue_random_offset + jump) % 360.0

        # Continuous loudness-tracking baseline brightness.
        loudness_s = self._loudness_smoother.update(raw_energy, dt)
        baseline_value = lerp(cfg.baseline_min_brightness, cfg.baseline_max_brightness, loudness_s)
        target_value = cfg.flash_brightness if is_peak else baseline_value
        value_s = self._flash_value_smoother.update(target_value, dt)

        # Treble -> white blend (independent of the peak flash above).
        treble_energy = band_energy(frame, cfg.treble_low_hz, cfg.treble_high_hz)
        whiteness_target = clip(treble_energy * cfg.treble_white_amount)
        whiteness_s = self._white_smoother.update(whiteness_target, dt)
        effective_saturation = clip(cfg.saturation * (1.0 - whiteness_s))

        # Continuous, slowly-flowing hue - the "storytelling" color arc.
        if cfg.hue_source == "centroid":
            centroid = spectral_centroid_hz(frame)
            hsv_cfg = self.config.color_mapping.hsv
            high = 16000.0
            t = (math.log(clip(centroid, 20.0, high)) - math.log(20.0)) / (math.log(high) - math.log(20.0))
            hue_target = lerp(hsv_cfg.hue_min_deg, hsv_cfg.hue_max_deg, t)
        else:  # "drift": a constant slow autonomous rotation, independent of content
            self._hue_drift_deg = (self._hue_drift_deg + cfg.drift_speed_deg_per_s * dt) % 360.0
            hue_target = self._hue_drift_deg
        hue_target = (hue_target + self._hue_random_offset) % 360.0
        hue_s = self._hue_smoother_peak.update(hue_target, dt)

        self.latest_band3_levels = {
            "peak_energy": raw_energy,
            "treble_energy": treble_energy,
            "hue": hue_s,
            "value": value_s,
            "whiteness": whiteness_s,
            "peak": 1.0 if is_peak else 0.0,
        }
        self._history_peak.push(wall_now, np.array([hue_s, value_s, effective_saturation]))

        colors: Dict[str, Color] = {}
        for device_id in selected_ids:
            effect = self.config.per_lamp_effects.get(device_id)
            sample_t = wall_now - ((effect.phase_offset_ms / 1000.0) if effect else 0.0)
            vals = self._history_peak.sample_at(sample_t)
            if vals is None:
                vals = np.array([hue_s, value_s, effective_saturation])
            mult = effect.sensitivity_mult if effect else 1.0
            color = self.color_engine.compute_peak_flash(vals[0], vals[2], clip(vals[1] * mult))
            if effect is not None:
                color = apply_per_lamp_effect(color, effect)
            colors[device_id] = color
        return colors

    # -- Chase overlay: layered on top of whichever mode ran above ------------------------
    #
    # The actual position/weight/color math lives in effects/chase.py
    # (ChaseAnimator + get_chase_groups), shared with the standalone manual
    # control app - this method just wires it up to this engine's audio data.

    def _apply_chase_overlay(
        self, colors: Dict[str, Color], frame: SpectrumFrame, dt: float, wall_now: float, selected_ids
    ) -> Dict[str, Color]:
        cfg = self.config.chase
        groups = get_chase_groups(self.config.per_lamp_effects, selected_ids)
        if len(groups) < 2:
            return colors  # need at least 2 chase positions for a chase to mean anything

        beat_energy = None
        intensity_energy = None
        if cfg.sync_mode == "beat":
            beat_energy = band_energy(frame, cfg.beat_detect_low_hz, cfg.beat_detect_high_hz)
        elif cfg.sync_mode == "intensity_peak":
            intensity_energy = band_energy(frame, cfg.peak_detect_low_hz, cfg.peak_detect_high_hz)

        dwell_weights = get_chase_group_dwell_weights(self.config.per_lamp_effects, groups)
        self._chase_animator.tick(
            dt,
            num_positions=len(groups),
            beat_band_energy=beat_energy,
            intensity_energy=intensity_energy,
            now_s=wall_now,
            dwell_weights=dwell_weights,
            clock_beat=self._clock_beat,
        )
        return self._chase_animator.apply(colors, groups)

    # -- Group Switch overlay: discrete alternative to Chase, layered the same way ---------
    #
    # The actual position/color math lives in effects/chase.py
    # (GroupSwitchAnimator + get_group_switch_groups) - this method just
    # wires it up to this engine's audio data, mirroring
    # _apply_chase_overlay() above exactly (same sync_mode options, own
    # independent detector) except there's no dwell-weight concept here -
    # Group Switch has no continuous falloff for a dwell multiplier to shape.

    def _apply_group_switch_overlay(
        self, colors: Dict[str, Color], frame: SpectrumFrame, dt: float, wall_now: float, selected_ids
    ) -> Dict[str, Color]:
        cfg = self.config.group_switch
        groups = get_group_switch_groups(self.config.per_lamp_effects, selected_ids)
        if len(groups) < 2:
            return colors  # need at least 2 groups for switching to mean anything

        beat_energy = None
        intensity_energy = None
        if cfg.sync_mode == "beat":
            beat_energy = band_energy(frame, cfg.beat_detect_low_hz, cfg.beat_detect_high_hz)
        elif cfg.sync_mode == "intensity_peak":
            intensity_energy = band_energy(frame, cfg.peak_detect_low_hz, cfg.peak_detect_high_hz)

        self._group_switch_animator.tick(
            dt,
            num_positions=len(groups),
            beat_band_energy=beat_energy,
            intensity_energy=intensity_energy,
            now_s=wall_now,
            clock_beat=self._clock_beat,
        )
        return self._group_switch_animator.apply(colors, groups)
