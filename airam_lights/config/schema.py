"""Dataclass definitions for the whole application configuration.

Everything here is plain, JSON-friendly data - no business logic. Each dataclass
implements `to_dict` / `from_dict` so the config can round-trip through a
human-readable JSON file (see `store.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@dataclass
class DeviceConfig:
    """One physical Airam bulb, as needed for local Tuya control."""

    id: str  # Tuya device id ("gwId")
    name: str  # user-assigned friendly name, e.g. "Lamp 1"
    ip: str  # last-known LAN IP address
    local_key: str  # Tuya local_key, obtained once via the cloud setup wizard
    version: str = "3.3"  # Tuya local protocol version (3.1/3.3/3.4/3.5)
    enabled: bool = True  # participates in visualization when True
    selected: bool = True  # currently selected in the UI lamp list

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceConfig":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            ip=d.get("ip", ""),
            local_key=d.get("local_key", ""),
            version=str(d.get("version", "3.3")),
            enabled=d.get("enabled", True),
            selected=d.get("selected", True),
        )


# ---------------------------------------------------------------------------
# Frequency bands
# ---------------------------------------------------------------------------

@dataclass
class BandDefinition:
    name: str
    low_hz: float
    high_hz: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BandDefinition":
        return cls(name=d["name"], low_hz=float(d["low_hz"]), high_hz=float(d["high_hz"]))


def default_3band() -> List[BandDefinition]:
    return [
        BandDefinition("Bass", 20, 150),
        BandDefinition("Mid", 150, 2000),
        BandDefinition("Treble", 2000, 12000),
    ]


def default_8band() -> List[BandDefinition]:
    return [
        BandDefinition("Band 1", 20, 60),
        BandDefinition("Band 2", 60, 120),
        BandDefinition("Band 3", 120, 250),
        BandDefinition("Band 4", 250, 500),
        BandDefinition("Band 5", 500, 1000),
        BandDefinition("Band 6", 1000, 2000),
        BandDefinition("Band 7", 2000, 5000),
        BandDefinition("Band 8", 5000, 12000),
    ]


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------

@dataclass
class SmoothingConfig:
    """Attack/release exponential smoothing, in milliseconds.

    A rising signal is smoothed with `attack_ms`, a falling one with
    `release_ms`, so e.g. bass hits can snap up quickly but decay gently
    instead of both directions blending abruptly like the stock Airam
    Music Sync.
    """

    attack_ms: float = 60.0
    release_ms: float = 300.0
    # Ignore changes smaller than this (0..1 normalized channel units) to
    # avoid sending imperceptible network updates.
    min_change_threshold: float = 0.015

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SmoothingConfig":
        return cls(
            attack_ms=float(d.get("attack_ms", 60.0)),
            release_ms=float(d.get("release_ms", 300.0)),
            min_change_threshold=float(d.get("min_change_threshold", 0.015)),
        )


# ---------------------------------------------------------------------------
# Color mapping
# ---------------------------------------------------------------------------

@dataclass
class ChannelMap:
    """Maps one frequency range onto one output channel (R, G or B).

    Used both for the "RGB Frequency" default mode (bass/mid/treble -> R/G/B)
    and for "Custom" mode, which is the same mechanism with user-editable
    ranges - there is deliberately no separate code path for "custom".
    """

    low_hz: float
    high_hz: float
    gain: float = 1.0
    min_level: float = 0.0  # input level (0..1) mapped to output 0
    max_level: float = 1.0  # input level (0..1) mapped to output 1
    gamma: float = 1.0  # output = output ** (1/gamma)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelMap":
        return cls(
            low_hz=float(d["low_hz"]),
            high_hz=float(d["high_hz"]),
            gain=float(d.get("gain", 1.0)),
            min_level=float(d.get("min_level", 0.0)),
            max_level=float(d.get("max_level", 1.0)),
            gamma=float(d.get("gamma", 1.0)),
        )


@dataclass
class RGBModeConfig:
    """Backs both 'RGB Frequency' and 'Custom' color mapping modes."""

    r: ChannelMap = field(default_factory=lambda: ChannelMap(20, 150))
    g: ChannelMap = field(default_factory=lambda: ChannelMap(150, 2000))
    b: ChannelMap = field(default_factory=lambda: ChannelMap(2000, 12000))
    sensitivity: float = 1.0  # overall input gain applied before per-channel gain

    def to_dict(self) -> dict:
        return {
            "r": self.r.to_dict(),
            "g": self.g.to_dict(),
            "b": self.b.to_dict(),
            "sensitivity": self.sensitivity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RGBModeConfig":
        return cls(
            r=ChannelMap.from_dict(d.get("r", {"low_hz": 20, "high_hz": 150})),
            g=ChannelMap.from_dict(d.get("g", {"low_hz": 150, "high_hz": 2000})),
            b=ChannelMap.from_dict(d.get("b", {"low_hz": 2000, "high_hz": 12000})),
            sensitivity=float(d.get("sensitivity", 1.0)),
        )


@dataclass
class HSVModeConfig:
    """'HSV Music' mode: hue from spectral centroid, value from overall
    energy, saturation from spectral contrast."""

    hue_min_deg: float = 240.0  # hue at the lowest centroid (low freq -> blue by default)
    hue_max_deg: float = 0.0  # hue at the highest centroid (high freq -> red)
    brightness_min: float = 0.05
    brightness_max: float = 1.0
    saturation_base: float = 0.6
    saturation_contrast_gain: float = 0.5
    sensitivity: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "HSVModeConfig":
        return cls(
            hue_min_deg=float(d.get("hue_min_deg", 240.0)),
            hue_max_deg=float(d.get("hue_max_deg", 0.0)),
            brightness_min=float(d.get("brightness_min", 0.05)),
            brightness_max=float(d.get("brightness_max", 1.0)),
            saturation_base=float(d.get("saturation_base", 0.6)),
            saturation_contrast_gain=float(d.get("saturation_contrast_gain", 0.5)),
            sensitivity=float(d.get("sensitivity", 1.0)),
        )


@dataclass
class SpectrumModeConfig:
    """'8-Band Spectrum' mode: one band drives one lamp's brightness/saturation
    around a user-chosen base hue."""

    base_hue_deg: float = 260.0  # used when a lamp has no per-lamp hue offset
    hue_step_deg: float = 0.0  # optional hue rotation across bands 1..8 (rainbow look)
    saturation: float = 0.9
    min_brightness: float = 0.04
    max_brightness: float = 1.0
    sensitivity: float = 1.0
    drive_saturation_too: bool = False  # if True, band level also modulates saturation

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SpectrumModeConfig":
        return cls(
            base_hue_deg=float(d.get("base_hue_deg", 260.0)),
            hue_step_deg=float(d.get("hue_step_deg", 0.0)),
            saturation=float(d.get("saturation", 0.9)),
            min_brightness=float(d.get("min_brightness", 0.04)),
            max_brightness=float(d.get("max_brightness", 1.0)),
            sensitivity=float(d.get("sensitivity", 1.0)),
            drive_saturation_too=bool(d.get("drive_saturation_too", False)),
        )


PULSE_TRIGGERS = ("random", "accent", "downbeat")
WHITE_TEMP_MODES = ("random", "bar", "alternate", "loudness", "phrase")
SYNC_MODES = ("off", "beat", "intensity_peak", "clock")


def _pulse_trigger(value) -> str:
    return value if value in PULSE_TRIGGERS else "random"


@dataclass
class BeatSyncModeConfig:
    """'Beat Sync' mode: on every detected beat/onset, jump to a fresh,
    fully-saturated hue at full brightness, then decay toward a dimmer
    baseline until the next hit - a much more dramatic, obviously
    rhythm-locked look than smoothly blending continuous band levels."""

    detect_low_hz: float = 40.0
    detect_high_hz: float = 200.0  # kick-drum range by default
    sensitivity: float = 1.6  # beat fires when energy > rolling_avg * sensitivity
    min_interval_ms: float = 120.0  # refractory period between beats
    min_energy: float = 0.12  # absolute floor, avoids false triggers in near-silence

    hue_mode: str = "random"  # "random" | "step" | "spectrum"
    hue_step_deg: float = 137.5  # used when hue_mode == "step" (golden angle - good spread, never repeats)
    # Every beat still flashes, but the hue only changes on every Nth beat -
    # e.g. 4 with the shared beat clock (RhythmConfig) = one color per bar,
    # which reads as a pattern instead of a new random color on every hit.
    hue_every_n_beats: int = 1
    min_hue_jump_deg: float = 60.0  # used when hue_mode == "random": force a visibly different color each hit

    saturation: float = 1.0
    flash_brightness: float = 1.0  # value right at the beat
    sustain_brightness: float = 0.25  # value it decays toward between beats

    hue_attack_ms: float = 40.0  # how fast the hue snaps to the new target
    brightness_attack_ms: float = 15.0  # how fast brightness snaps up on a beat
    brightness_release_ms: float = 350.0  # how slowly brightness decays afterward

    # What moves between beats - either, both or neither:
    # fade_brightness: the classic flash that decays toward sustain_brightness
    #   (off = brightness stays at flash_brightness, except dark pulses - for
    #   RGB+CCT bulbs whose RGB LEDs are much dimmer than their white ones).
    # glide_hue: after each beat the color glides `hue_glide_deg` degrees in
    #   the direction of the next color; the next beat then lands on that
    #   color. Timing "beat" spreads the glide evenly over the whole beat (the
    #   tempo), so the color keeps moving until the next hit; "decay" follows
    #   the brightness attack/decay curve - fast right after the hit, then still.
    fade_brightness: bool = True
    glide_hue: bool = False
    hue_glide_deg: float = 120.0
    hue_glide_timing: str = "beat"  # "beat" | "decay"

    # "Dark pulses": on a random subset of beats, briefly dip toward black
    # (a rhythm-synced pause) BEFORE flashing to the new color, instead of
    # flashing immediately - a tension-and-release, strobe-like accent.
    # Real-time detection can only react to a beat as it happens (it can't
    # anticipate one), so the pause always happens right after the trigger
    # and the actual color flash is delayed until the pause ends. Mirrors
    # the white pulse controls below (enabled toggle + its own independent
    # attack/release), applied as a multiplicative dip on top of whatever
    # the normal flash/sustain brightness envelope is already doing.
    dark_pulse_enabled: bool = True
    dark_pulse_probability: float = 0.0  # 0..1: chance a given beat gets a pause first
    # Which beats may roll the probability above at all: "random" = every
    # beat; "accent" = only the hardest hits; "downbeat" = only bar starts.
    # "accent"/"downbeat" need the shared beat clock (RhythmConfig.
    # shared_clock) - without it they behave like "random".
    dark_pulse_trigger: str = "random"
    dark_pulse_duration_ms: float = 70.0  # how long the pause lasts
    dark_pulse_depth: float = 1.0  # 0..1: how dark (1.0 = fully black)
    dark_pulse_attack_ms: float = 15.0  # how fast brightness snaps down into the pause
    dark_pulse_release_ms: float = 150.0  # how fast it eases back out once the pause ends

    # "White pulses": on a subset of beats, a brief flash of the bulb's own
    # white LEDs, right in sync with that beat's flash - e.g. a hi-hat/cymbal
    # accent. Independent of dark pulses above - each rolls its own probability on
    # every beat (an earlier version tried to make them mutually exclusive
    # via separate detection bands, but that wasn't reliable - see
    # VisualizationEngine._tick_beat_sync_mode's docstring for why).
    # On by default with tuned timing - this combination (probability/duration/attack/release/brightness/temp)
    # was tuned live against real hardware and confirmed to read well, so it
    # ships as the default rather than a generic/untuned starting point that
    # would undersell the effect on first run.
    white_pulse_enabled: bool = True
    white_pulse_probability: float = 0.26  # 0..1: chance a given beat's flash also gets this pulse
    white_pulse_trigger: str = "random"  # same options as dark_pulse_trigger
    white_pulse_duration_ms: float = 45.0  # how long the white flash is held
    white_pulse_attack_ms: float = 17.0  # timing of the flash's start
    white_pulse_release_ms: float = 49.0  # how long after the hold it ends

    # The flash always uses the bulb's WHITE work_mode - its real white
    # diode(s) - never the RGB LEDs: mixing white from RGB would spend the
    # colored LEDs' (much weaker) output on white light instead of color, and
    # only ever approximates white anyway. The lamp switches to WHITE for the
    # pulse, then back to RGB colour mode, resuming wherever the normal Beat
    # Sync hue/brightness envelope has evolved to in the meantime. (Earlier
    # builds also had an RGB-desaturation variant and an "invert" option;
    # both are gone - their saved settings are simply ignored.)
    # The lamp is sent ONE constant WHITE work_mode command on entry (not a
    # per-tick brightness ramp) - white_pulse_attack_ms/release_ms above
    # still govern the timing of when the flash starts/ends, just not a
    # visible fade, since a real bulb needs two separate DP writes per
    # white-mode command and every selected lamp flashes at once (see
    # below) - a smooth ramp multiplied into a command burst large enough to
    # overwhelm the LAN/Wi-Fi and tinytuya's own connection handling in
    # practice, which looked like brightness never quite reaching its peak
    # and inconsistent behavior between lamps.
    # Which lamps a true-white flash lands on: "all" (default) = every
    # selected lamp at once, as before; "chase" = only the lamps the Chase
    # effect's moving highlight is currently on; "group" = only the lamps in
    # Group Switch's currently active group. The set is captured on the
    # flash's first tick and held for its whole duration. Falls back to
    # "all" when the chosen effect isn't enabled/has fewer than 2 positions,
    # since there's then no moving highlight to follow.
    white_pulse_target: str = "all"
    # "rotate": the white flash gets its own rotation through the Chase order
    # (PerLampEffect.chase_order) - every new flash moves exactly one position
    # on, so it never skips a lamp however fast Chase itself is moving.
    # `white_pulse_rotators` > 1 adds more lamps evenly spaced around the loop
    # (2 = opposite lamps, 3 = thirds); the pulse sequencer's walk uses it too.
    white_pulse_rotators: int = 1
    white_pulse_white_brightness: float = 0.3  # 0..1: brightness during the true-white flash
    # Each true-white flash independently rolls warm (0.0) vs cool (1.0) -
    # this is the probability of landing on cool, not a fixed temperature -
    # so at the 0.5 default flashes vary noticeably beat to beat instead of
    # always looking the same, and the roll happens once per NEW flash (tied
    # to Beat Sync's own trigger), not per-tick - see
    # VisualizationEngine._beat_white_pulse_temp. 0.0 = always warm, 1.0 =
    # always cool, same as before this became a ratio.
    white_pulse_cool_ratio: float = 0.5
    # How each flash's warm/cool white is chosen (the color temperature can be
    # anywhere between warm 0.0 and cool 1.0):
    # "random": rolled per flash with white_pulse_cool_ratio (above).
    # "bar": by the weight of the position in the bar - the downbeat coolest,
    #   the bar's middle beat half-cool, other beats warmer, in-between flashes
    #   warmest - so the heavy beats stand out from the light ones.
    # "alternate": cool, warm, cool, warm...
    # "loudness": warm in the quiet parts of a song, cool in the loud ones.
    # "phrase": cools down over each phrase toward its fill, the new phrase's
    #   first flash is cool, then back to warm.
    # "bar"/"phrase" need the shared beat clock; without it they act as "random".
    white_pulse_temp_mode: str = "random"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BeatSyncModeConfig":
        return cls(
            detect_low_hz=float(d.get("detect_low_hz", 40.0)),
            detect_high_hz=float(d.get("detect_high_hz", 200.0)),
            sensitivity=float(d.get("sensitivity", 1.6)),
            min_interval_ms=float(d.get("min_interval_ms", 120.0)),
            min_energy=float(d.get("min_energy", 0.12)),
            hue_mode=d.get("hue_mode", "random"),
            hue_step_deg=float(d.get("hue_step_deg", 137.5)),
            hue_every_n_beats=max(1, int(d.get("hue_every_n_beats", 1))),
            min_hue_jump_deg=float(d.get("min_hue_jump_deg", 60.0)),
            saturation=float(d.get("saturation", 1.0)),
            flash_brightness=float(d.get("flash_brightness", 1.0)),
            sustain_brightness=float(d.get("sustain_brightness", 0.25)),
            hue_attack_ms=float(d.get("hue_attack_ms", 40.0)),
            brightness_attack_ms=float(d.get("brightness_attack_ms", 15.0)),
            brightness_release_ms=float(d.get("brightness_release_ms", 350.0)),
            # Older builds had one either/or "decay_mode" setting.
            fade_brightness=bool(d.get("fade_brightness", d.get("decay_mode") != "hue")),
            glide_hue=bool(d.get("glide_hue", d.get("decay_mode") == "hue")),
            hue_glide_deg=float(d.get("hue_glide_deg", 120.0)),
            hue_glide_timing=d.get("hue_glide_timing") if d.get("hue_glide_timing") in ("beat", "decay") else "beat",
            dark_pulse_enabled=bool(d.get("dark_pulse_enabled", True)),
            dark_pulse_probability=float(d.get("dark_pulse_probability", 0.0)),
            dark_pulse_trigger=_pulse_trigger(d.get("dark_pulse_trigger")),
            dark_pulse_duration_ms=float(d.get("dark_pulse_duration_ms", 70.0)),
            dark_pulse_depth=float(d.get("dark_pulse_depth", 1.0)),
            dark_pulse_attack_ms=float(d.get("dark_pulse_attack_ms", 15.0)),
            dark_pulse_release_ms=float(d.get("dark_pulse_release_ms", 150.0)),
            white_pulse_enabled=bool(d.get("white_pulse_enabled", True)),
            white_pulse_probability=float(d.get("white_pulse_probability", 0.26)),
            white_pulse_trigger=_pulse_trigger(d.get("white_pulse_trigger")),
            white_pulse_duration_ms=float(d.get("white_pulse_duration_ms", 45.0)),
            white_pulse_attack_ms=float(d.get("white_pulse_attack_ms", 17.0)),
            white_pulse_release_ms=float(d.get("white_pulse_release_ms", 49.0)),
            white_pulse_target=d.get("white_pulse_target", "all"),
            white_pulse_rotators=max(1, min(4, int(d.get("white_pulse_rotators", 1)))),
            white_pulse_white_brightness=float(d.get("white_pulse_white_brightness", 0.3)),
            white_pulse_cool_ratio=float(d.get("white_pulse_cool_ratio", 0.5)),
            white_pulse_temp_mode=d.get("white_pulse_temp_mode") if d.get("white_pulse_temp_mode") in WHITE_TEMP_MODES else "random",
        )


@dataclass
class PeakFlashModeConfig:
    """'Peak Flash' mode: reacts sensitively to ANY sudden loudness spike
    across the whole spectrum (not just bass), flashes toward pure white
    specifically when treble/cymbal/sibilance energy is dominant, and
    otherwise shows fully-saturated color whose hue *continuously* and
    slowly flows over time - a narrative color arc - rather than jumping
    discretely on each hit like Beat Sync does. Brightness also gently
    tracks overall loudness between peaks, on top of the sharp peak flashes.
    """

    # Broadband peak/onset detection - deliberately wide by default so any
    # kind of transient (kick, snare, hi-hat, vocal hit) can trigger it.
    detect_low_hz: float = 20.0
    detect_high_hz: float = 16000.0
    sensitivity: float = 1.3
    min_interval_ms: float = 60.0
    min_energy: float = 0.08

    # Treble energy blends the output toward white - a "sparkle" on
    # cymbals/hi-hats/sibilance, layered on top of everything else.
    treble_low_hz: float = 5000.0
    treble_high_hz: float = 16000.0
    treble_white_amount: float = 1.0  # gain: how strongly treble energy pulls toward white
    white_attack_ms: float = 30.0  # how fast it flashes toward white
    white_release_ms: float = 220.0  # how fast it fades back to color

    # Continuous hue "storytelling" flow - this is the smooth color-blend
    # math: a long time constant here means hue drifts like a slow narrative
    # arc instead of snapping, so consecutive colors always flow into each
    # other richly no matter what triggers brightness/whiteness.
    hue_source: str = "drift"  # "drift" (autonomous slow rotation) | "centroid" (follows spectral centroid)
    hue_flow_ms: float = 4000.0  # smoothing time constant for the hue - the main "richness" slider
    drift_speed_deg_per_s: float = 6.0  # used when hue_source == "drift"

    # On top of the continuous flow above: on each detected peak, roll a
    # chance (0..1) to inject a random hue jump - synced to the music since
    # it only ever fires exactly on a detected peak. 0 = pure smooth flow,
    # never jumps. 1 = every peak jumps. The jump persists (the story
    # continues from the new hue) rather than snapping back.
    randomness: float = 0.0
    random_jump_range_deg: float = 180.0  # max size of each jump (uniform +/- this)

    saturation: float = 1.0

    # Brightness: a continuous baseline tracks overall loudness, plus a fast
    # flash on every detected peak that decays back toward that baseline.
    baseline_min_brightness: float = 0.20
    baseline_max_brightness: float = 0.75
    flash_brightness: float = 1.0
    flash_attack_ms: float = 12.0
    flash_release_ms: float = 260.0
    loudness_smoothing_ms: float = 300.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PeakFlashModeConfig":
        return cls(
            detect_low_hz=float(d.get("detect_low_hz", 20.0)),
            detect_high_hz=float(d.get("detect_high_hz", 16000.0)),
            sensitivity=float(d.get("sensitivity", 1.3)),
            min_interval_ms=float(d.get("min_interval_ms", 60.0)),
            min_energy=float(d.get("min_energy", 0.08)),
            treble_low_hz=float(d.get("treble_low_hz", 5000.0)),
            treble_high_hz=float(d.get("treble_high_hz", 16000.0)),
            treble_white_amount=float(d.get("treble_white_amount", 1.0)),
            white_attack_ms=float(d.get("white_attack_ms", 30.0)),
            white_release_ms=float(d.get("white_release_ms", 220.0)),
            hue_source=d.get("hue_source", "drift"),
            hue_flow_ms=float(d.get("hue_flow_ms", 4000.0)),
            drift_speed_deg_per_s=float(d.get("drift_speed_deg_per_s", 6.0)),
            randomness=float(d.get("randomness", 0.0)),
            random_jump_range_deg=float(d.get("random_jump_range_deg", 180.0)),
            saturation=float(d.get("saturation", 1.0)),
            baseline_min_brightness=float(d.get("baseline_min_brightness", 0.20)),
            baseline_max_brightness=float(d.get("baseline_max_brightness", 0.75)),
            flash_brightness=float(d.get("flash_brightness", 1.0)),
            flash_attack_ms=float(d.get("flash_attack_ms", 12.0)),
            flash_release_ms=float(d.get("flash_release_ms", 260.0)),
            loudness_smoothing_ms=float(d.get("loudness_smoothing_ms", 300.0)),
        )


@dataclass
class BeatSyncWhiteModeConfig:
    """'Beat Sync White' mode: the same rhythm-reactive envelope as Beat
    Sync, but drives the bulb's WHITE work_mode (brightness + color
    temperature, DPs 22/23) instead of RGB color (DP 24) - warm/cool flashes
    on the beat instead of hue jumps. Uses its own independent beat detector
    and dark-pulse handling, identical in spirit to Beat Sync's."""

    detect_low_hz: float = 40.0
    detect_high_hz: float = 200.0
    sensitivity: float = 1.6
    min_interval_ms: float = 120.0
    min_energy: float = 0.12

    temp_mode: str = "random"  # "random" | "alternate"
    temp_min: float = 0.0  # 0..1, 0 = warmest
    temp_max: float = 1.0  # 0..1, 1 = coolest
    min_temp_jump: float = 0.35  # used when temp_mode == "random": force a visibly different temp each hit

    flash_brightness: float = 1.0
    sustain_brightness: float = 0.25

    temp_attack_ms: float = 40.0  # how fast the temperature snaps to its new target
    brightness_attack_ms: float = 15.0
    brightness_release_ms: float = 350.0

    dark_pulse_probability: float = 0.0
    dark_pulse_duration_ms: float = 70.0
    dark_pulse_depth: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BeatSyncWhiteModeConfig":
        return cls(
            detect_low_hz=float(d.get("detect_low_hz", 40.0)),
            detect_high_hz=float(d.get("detect_high_hz", 200.0)),
            sensitivity=float(d.get("sensitivity", 1.6)),
            min_interval_ms=float(d.get("min_interval_ms", 120.0)),
            min_energy=float(d.get("min_energy", 0.12)),
            temp_mode=d.get("temp_mode", "random"),
            temp_min=float(d.get("temp_min", 0.0)),
            temp_max=float(d.get("temp_max", 1.0)),
            min_temp_jump=float(d.get("min_temp_jump", 0.35)),
            flash_brightness=float(d.get("flash_brightness", 1.0)),
            sustain_brightness=float(d.get("sustain_brightness", 0.25)),
            temp_attack_ms=float(d.get("temp_attack_ms", 40.0)),
            brightness_attack_ms=float(d.get("brightness_attack_ms", 15.0)),
            brightness_release_ms=float(d.get("brightness_release_ms", 350.0)),
            dark_pulse_probability=float(d.get("dark_pulse_probability", 0.0)),
            dark_pulse_duration_ms=float(d.get("dark_pulse_duration_ms", 70.0)),
            dark_pulse_depth=float(d.get("dark_pulse_depth", 1.0)),
        )


@dataclass
class ColorMappingConfig:
    # "beat_sync" is the default: a percussive, obviously rhythm-locked flash
    # on every beat reads as far more "alive" in practice than the smoothly
    # continuous blending the other modes do - see README.md.
    mode: str = "beat_sync"  # "rgb_freq" | "hsv_music" | "custom" | "8band_spectrum" | "beat_sync" | "peak_flash" | "beat_sync_white"
    rgb: RGBModeConfig = field(default_factory=RGBModeConfig)
    custom: RGBModeConfig = field(default_factory=RGBModeConfig)
    hsv: HSVModeConfig = field(default_factory=HSVModeConfig)
    spectrum: SpectrumModeConfig = field(default_factory=SpectrumModeConfig)
    beat_sync: BeatSyncModeConfig = field(default_factory=BeatSyncModeConfig)
    peak_flash: PeakFlashModeConfig = field(default_factory=PeakFlashModeConfig)
    beat_sync_white: BeatSyncWhiteModeConfig = field(default_factory=BeatSyncWhiteModeConfig)
    smoothing: SmoothingConfig = field(default_factory=SmoothingConfig)
    brightness: float = 1.0  # global brightness multiplier
    saturation: float = 1.0  # global saturation multiplier
    response_curve: str = "linear"  # "linear" | "log" | "exp2"
    invert_brightness: bool = False  # applies to EVERY mode: 0 becomes bright, 1 becomes black

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "rgb": self.rgb.to_dict(),
            "custom": self.custom.to_dict(),
            "hsv": self.hsv.to_dict(),
            "spectrum": self.spectrum.to_dict(),
            "beat_sync": self.beat_sync.to_dict(),
            "peak_flash": self.peak_flash.to_dict(),
            "beat_sync_white": self.beat_sync_white.to_dict(),
            "smoothing": self.smoothing.to_dict(),
            "brightness": self.brightness,
            "saturation": self.saturation,
            "response_curve": self.response_curve,
            "invert_brightness": self.invert_brightness,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ColorMappingConfig":
        return cls(
            mode=d.get("mode", "rgb_freq"),
            rgb=RGBModeConfig.from_dict(d.get("rgb", {})),
            custom=RGBModeConfig.from_dict(d.get("custom", {})),
            hsv=HSVModeConfig.from_dict(d.get("hsv", {})),
            spectrum=SpectrumModeConfig.from_dict(d.get("spectrum", {})),
            beat_sync=BeatSyncModeConfig.from_dict(d.get("beat_sync", {})),
            peak_flash=PeakFlashModeConfig.from_dict(d.get("peak_flash", {})),
            beat_sync_white=BeatSyncWhiteModeConfig.from_dict(d.get("beat_sync_white", {})),
            smoothing=SmoothingConfig.from_dict(d.get("smoothing", {})),
            brightness=float(d.get("brightness", 1.0)),
            saturation=float(d.get("saturation", 1.0)),
            response_curve=d.get("response_curve", "linear"),
            invert_brightness=bool(d.get("invert_brightness", False)),
        )


# ---------------------------------------------------------------------------
# Per-lamp effects (optional variation across the 8 lamps)
# ---------------------------------------------------------------------------

@dataclass
class PerLampEffect:
    device_id: str
    band_gains: Dict[str, float] = field(default_factory=dict)  # band name -> multiplier
    phase_offset_ms: float = 0.0
    brightness_mult: float = 1.0
    saturation_mult: float = 1.0
    hue_offset_deg: float = 0.0
    sensitivity_mult: float = 1.0
    # For 8-band spectrum mode: which band index (0-based) this lamp shows.
    # None = assign automatically in selection order.
    band_index: Optional[int] = None
    # For the Chase overlay (applies regardless of mode - see ChaseEffectConfig):
    # this lamp's position in the rotation order, 0-based. None = not part of
    # the chase. The chase's actual lamp order is derived by sorting all
    # lamps that have this set, ascending.
    chase_order: Optional[int] = None
    # How long the chase's moving highlight lingers at this lamp's position
    # relative to others, e.g. a ceiling fixture with several physical spots
    # sharing one chase_order can feel like it dwells there longer just from
    # having more lamps lit at once - lower this for that position to
    # compensate. 1.0 = default/uniform (matches the original behavior).
    chase_dwell_mult: float = 1.0
    # For the Group Switch overlay (GroupSwitchEffectConfig) - a separate,
    # independent grouping from chase_order/chase_dwell_mult above, since a
    # lamp can take part in the continuous Chase rotation and/or the
    # discrete Group Switch at once, with a different grouping for each.
    # Same "0-based, ascending, same-number lamps grouped together" rule as
    # chase_order; None = not part of any group switch group.
    effect_group: Optional[int] = None
    # Multiplier on BeatSyncModeConfig.white_pulse_white_brightness for this
    # lamp's true-white flashes - e.g. 0.5 to tone down a group of spots
    # that sits close to plants/walls and reads much brighter than the
    # rest. A ratio rather than an absolute cap, so the balance between
    # lamps holds when the global brightness is changed. 1.0 = unchanged.
    white_pulse_brightness_mult: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PerLampEffect":
        return cls(
            device_id=d["device_id"],
            band_gains=dict(d.get("band_gains", {})),
            phase_offset_ms=float(d.get("phase_offset_ms", 0.0)),
            brightness_mult=float(d.get("brightness_mult", 1.0)),
            saturation_mult=float(d.get("saturation_mult", 1.0)),
            hue_offset_deg=float(d.get("hue_offset_deg", 0.0)),
            sensitivity_mult=float(d.get("sensitivity_mult", 1.0)),
            band_index=d.get("band_index", None),
            chase_order=d.get("chase_order", None),
            chase_dwell_mult=float(d.get("chase_dwell_mult", 1.0)),
            effect_group=d.get("effect_group", None),
            white_pulse_brightness_mult=float(d.get("white_pulse_brightness_mult", 1.0)),
        )


# ---------------------------------------------------------------------------
# Chase overlay - an optional effect layered on top of ANY color mode
# ---------------------------------------------------------------------------

@dataclass
class ChaseEffectConfig:
    """A moving highlight rotates through a chosen, ordered subset of lamps
    (each lamp's position comes from its PerLampEffect.chase_order), creating
    a spinning/chasing light effect - independent of, and layered on top of,
    whichever color mode is currently active (RGB/HSV/8-Band/Beat Sync/Peak
    Flash/Custom all get this overlay the same way).

    Speed is either a constant number of full rotations per second
    (`speed_rotations_per_s` - e.g. 0.5 means one full lap around the chase
    order every 2 seconds), or, when `sync_mode` is "beat" or
    "intensity_peak", purely event-driven: the highlight sits still and only
    advances `beat_multiplier` lamp-steps the instant a beat/peak is
    detected - `beat_multiplier=1` means one step per hit, `2` means two
    steps per hit (twice as fast), `0.5` means one step every two hits (half
    as fast) - no music-theory knowledge needed.

    This is deliberately NOT "estimate a tempo, then rotate continuously at
    that speed": an earlier version did that, and it meant the chase kept
    gliding on its own between hits (and even after the music went quiet, on
    whatever tempo it last estimated) - looking like it was "spinning on its
    own" with no audible rhythm behind it. Sitting still until the next
    actual hit is what makes it read as driven by the music.

    "beat" reacts to a specific low-frequency band (`beat_detect_*`, tuned
    for kick drums by default) via its own independent detector. "intensity_
    peak" instead reacts to ANY sudden broadband loudness spike (`peak_
    detect_*`, wide by default) via a second, separately-tuned detector -
    useful for tracks without a strong, steady bass beat. Both share
    `beat_multiplier` for how far each hit advances the highlight.

    The highlight only ever *modulates* whatever the active color mode is
    already showing on that lamp - crucially, brightness is a multiplicative
    boost on top of the base brightness, never an independent value, so a
    lamp the active mode has deliberately dimmed to black (e.g. a Beat Sync
    dark pulse) stays black even while the chase highlight passes over it.
    """

    # On by default, paired with color_mode="complementary" below - a
    # rotating complementary-color highlight on top of Beat Sync (also the
    # default mode) is the combination this app looks best with out of the
    # box. Only actually visible once lamps have a chase_order assigned
    # (Per-Lamp Effects tab), so this is harmless on a fresh install with no
    # lamps configured yet.
    enabled: bool = True

    num_rotators: int = 1  # how many highlights travel the loop at once, evenly spaced
    # (e.g. 2 = two highlights on opposite sides of the loop, both moving together)

    speed_rotations_per_s: float = 0.3  # constant speed when sync_mode == "off": full loops/second
    reverse: bool = False  # flips which way the highlight travels around the chase order
    sync_mode: str = "off"  # "off" (constant speed) | "beat" | "intensity_peak" | "clock"
    beat_multiplier: float = 1.0  # lamp-steps advanced per detected beat/peak, when synced
    # sync_mode == "clock": step on every Nth beat of the shared beat clock
    # (RhythmConfig), counted from the bar start - 1 = every beat, 2 = beats
    # 1 and 3, 4 = once per bar - so every layer moves on the same beats.
    clock_every_n_beats: int = 1

    # The chase's own independent beat detector (works regardless of which
    # color mode/its own beat detector, if any, is active). Used when
    # sync_mode == "beat".
    beat_detect_low_hz: float = 40.0
    beat_detect_high_hz: float = 200.0
    beat_sensitivity: float = 1.6
    beat_min_interval_ms: float = 120.0
    beat_min_energy: float = 0.12

    # A second, independent detector for sync_mode == "intensity_peak" -
    # deliberately wide-band by default (same idea as Peak Flash mode) so
    # ANY sudden loudness spike advances the chase, not just bass hits.
    peak_detect_low_hz: float = 20.0
    peak_detect_high_hz: float = 16000.0
    peak_sensitivity: float = 1.3
    peak_min_interval_ms: float = 60.0
    peak_min_energy: float = 0.08

    # How many lamp-positions wide the highlight is (soft falloff) - smaller
    # = crisper single-lamp look, larger = a smoother wave touching more
    # lamps at once. The "right" value scales with how many lamps are in the
    # chase - as a starting point, roughly a third of the chase's lamp count
    # tends to look smooth without every lamp being lit at once; tune to
    # taste (see README.md).
    width: float = 1.5
    intensity: float = 3.0  # brightness boost multiplier at the highlight's peak (base 0 always stays 0)
    # "linear": weight falls off at a constant rate from the peak - the peak
    # is a single instant, never lingered on, which can feel like the
    # highlight color flashes by too briefly. "bezier": an eased S-curve
    # (smoothstep) that's nearly flat right at the peak and right at zero,
    # transitioning fastest in between - the highlight visibly *dwells* in
    # its color for longer before smoothly handing off to the background.
    falloff_curve: str = "linear"  # "linear" | "bezier"

    # "complementary" is the default - the highlight always opposes
    # whatever hue the active color mode (Beat Sync by default) already put
    # on that lamp, so it stays visually interesting/varied no matter what
    # colors the base mode is currently showing, instead of imposing one
    # fixed hue regardless of context.
    color_mode: str = "complementary"  # "custom" | "complementary" | "hue_shift"
    custom_hue_deg: float = 280.0
    custom_saturation: float = 1.0
    # Used when color_mode == "hue_shift": each successive chase position
    # shows a hue offset by this many degrees from the previous one (starting
    # from custom_hue_deg), so the traveling light's own color gradually
    # cycles through the spectrum as it moves around the loop.
    hue_shift_step_deg: float = 45.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChaseEffectConfig":
        # Backward-compat: earlier builds used "speed_steps_per_s" (steps/second,
        # not rotations/second) - if only the old key is present, ignore it and
        # fall back to the new default rather than silently misinterpreting units.
        # Also backward-compat: earlier builds had a boolean "sync_to_beat"
        # instead of today's 3-way "sync_mode" - translate it if that's all an
        # old saved file has.
        sync_mode = d.get("sync_mode")
        if sync_mode not in SYNC_MODES:
            sync_mode = "beat" if d.get("sync_to_beat", False) else "off"
        return cls(
            enabled=bool(d.get("enabled", False)),
            num_rotators=int(d.get("num_rotators", 1)),
            speed_rotations_per_s=float(d.get("speed_rotations_per_s", 0.3)),
            reverse=bool(d.get("reverse", False)),
            sync_mode=sync_mode,
            beat_multiplier=float(d.get("beat_multiplier", 1.0)),
            clock_every_n_beats=max(1, int(d.get("clock_every_n_beats", 1))),
            beat_detect_low_hz=float(d.get("beat_detect_low_hz", 40.0)),
            beat_detect_high_hz=float(d.get("beat_detect_high_hz", 200.0)),
            beat_sensitivity=float(d.get("beat_sensitivity", 1.6)),
            beat_min_interval_ms=float(d.get("beat_min_interval_ms", 120.0)),
            beat_min_energy=float(d.get("beat_min_energy", 0.12)),
            peak_detect_low_hz=float(d.get("peak_detect_low_hz", 20.0)),
            peak_detect_high_hz=float(d.get("peak_detect_high_hz", 16000.0)),
            peak_sensitivity=float(d.get("peak_sensitivity", 1.3)),
            peak_min_interval_ms=float(d.get("peak_min_interval_ms", 60.0)),
            peak_min_energy=float(d.get("peak_min_energy", 0.08)),
            width=float(d.get("width", 0.7)),
            intensity=float(d.get("intensity", 3.0)),
            falloff_curve=d.get("falloff_curve", "linear"),
            color_mode=d.get("color_mode", "custom"),
            hue_shift_step_deg=float(d.get("hue_shift_step_deg", 45.0)),
            custom_hue_deg=float(d.get("custom_hue_deg", 280.0)),
            custom_saturation=float(d.get("custom_saturation", 1.0)),
        )


# ---------------------------------------------------------------------------
# Group Switch - an alternative to the Chase overlay's continuous rotation
# ---------------------------------------------------------------------------

@dataclass
class GroupSwitchEffectConfig:
    """Alternative to the Chase overlay's continuous rotation: lamps are
    grouped by PerLampEffect.effect_group (own, separate grouping from
    Chase's chase_order - a lamp can be part of either, both, or neither),
    and exactly ONE group is "active" at a time, shown at full target-color
    strength - every other group is left completely untouched. Unlike
    Chase, there is no width/falloff shaping: switching from one active
    group to the next is instant, a hard on/off step rather than a
    gradient. (Deliberately simple for now - a first version to try out
    before adding any more shaping/complexity.)

    Movement uses the exact same three-way model as ChaseEffectConfig:
    `sync_mode` "off" advances continuously at `speed_rotations_per_s`
    (full loops through all groups per second); "beat"/"intensity_peak"
    instead sit still and only advance `beat_multiplier` groups the instant
    a beat/broadband loudness peak is detected by this effect's own,
    independent detector.
    """

    enabled: bool = False

    speed_rotations_per_s: float = 0.3  # constant speed when sync_mode == "off": full loops/second
    reverse: bool = False  # flips which way the active group advances through the group order
    sync_mode: str = "off"  # "off" (constant speed) | "beat" | "intensity_peak" | "clock"
    beat_multiplier: float = 1.0  # groups advanced per detected beat/peak, when synced
    clock_every_n_beats: int = 4  # sync_mode == "clock": switch every Nth shared-clock beat (4 = once per bar)

    # Own independent beat detector, used when sync_mode == "beat".
    beat_detect_low_hz: float = 40.0
    beat_detect_high_hz: float = 200.0
    beat_sensitivity: float = 1.6
    beat_min_interval_ms: float = 120.0
    beat_min_energy: float = 0.12

    # Own independent, deliberately wide-band detector, used when
    # sync_mode == "intensity_peak" - same idea as Chase's.
    peak_detect_low_hz: float = 20.0
    peak_detect_high_hz: float = 16000.0
    peak_sensitivity: float = 1.3
    peak_min_interval_ms: float = 60.0
    peak_min_energy: float = 0.08

    intensity: float = 3.0  # brightness boost multiplier on the active group (base 0 always stays 0)

    color_mode: str = "custom"  # "custom" | "complementary" | "hue_shift"
    custom_hue_deg: float = 280.0
    custom_saturation: float = 1.0
    # Used when color_mode == "hue_shift": each successive group shows a hue
    # offset by this many degrees from the previous one (starting from
    # custom_hue_deg) - same slider/meaning as Chase's hue_shift_step_deg.
    hue_shift_step_deg: float = 45.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GroupSwitchEffectConfig":
        sync_mode = d.get("sync_mode")
        if sync_mode not in SYNC_MODES:
            sync_mode = "off"
        return cls(
            enabled=bool(d.get("enabled", False)),
            speed_rotations_per_s=float(d.get("speed_rotations_per_s", 0.3)),
            reverse=bool(d.get("reverse", False)),
            sync_mode=sync_mode,
            beat_multiplier=float(d.get("beat_multiplier", 1.0)),
            clock_every_n_beats=max(1, int(d.get("clock_every_n_beats", 4))),
            beat_detect_low_hz=float(d.get("beat_detect_low_hz", 40.0)),
            beat_detect_high_hz=float(d.get("beat_detect_high_hz", 200.0)),
            beat_sensitivity=float(d.get("beat_sensitivity", 1.6)),
            beat_min_interval_ms=float(d.get("beat_min_interval_ms", 120.0)),
            beat_min_energy=float(d.get("beat_min_energy", 0.12)),
            peak_detect_low_hz=float(d.get("peak_detect_low_hz", 20.0)),
            peak_detect_high_hz=float(d.get("peak_detect_high_hz", 16000.0)),
            peak_sensitivity=float(d.get("peak_sensitivity", 1.3)),
            peak_min_interval_ms=float(d.get("peak_min_interval_ms", 60.0)),
            peak_min_energy=float(d.get("peak_min_energy", 0.08)),
            intensity=float(d.get("intensity", 3.0)),
            color_mode=d.get("color_mode", "custom"),
            custom_hue_deg=float(d.get("custom_hue_deg", 280.0)),
            custom_saturation=float(d.get("custom_saturation", 1.0)),
            hue_shift_step_deg=float(d.get("hue_shift_step_deg", 45.0)),
        )


@dataclass
class WhiteChaseEffectConfig:
    """The White-mode counterpart to ChaseEffectConfig, used by the
    standalone manual control app (and available for reuse in the music app
    later): instead of an RGB/hue highlight, a warm-or-cool color
    TEMPERATURE region rotates through the chase-ordered lamp positions
    (grouping by PerLampEffect.chase_order works identically to the RGB
    chase - see effects/chase.py's get_chase_groups(), shared by both).

    No "sync to beat" here - this effect is specifically for the no-audio
    manual app.
    """

    enabled: bool = False
    num_rotators: int = 1
    speed_rotations_per_s: float = 0.3
    reverse: bool = False  # flips which way the highlight travels around the chase order
    width: float = 0.7
    intensity: float = 1.5  # brightness boost multiplier at the highlight's peak
    falloff_curve: str = "linear"  # "linear" | "bezier" - see ChaseEffectConfig.falloff_curve
    target_temp: float = 0.0  # 0..1: the region that sweeps through - 0=warm, 1=cool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WhiteChaseEffectConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            num_rotators=int(d.get("num_rotators", 1)),
            speed_rotations_per_s=float(d.get("speed_rotations_per_s", 0.3)),
            reverse=bool(d.get("reverse", False)),
            width=float(d.get("width", 0.7)),
            intensity=float(d.get("intensity", 1.5)),
            falloff_curve=d.get("falloff_curve", "linear"),
            target_temp=float(d.get("target_temp", 0.0)),
        )


@dataclass
class AmbientSceneConfig:
    """A self-looping ambient animation for the standalone manual control
    app, driven by ONE shared PC-side clock - every selected lamp is
    inherently synchronized by construction, since the same phase value is
    computed once and pushed to all of them together. This is deliberately
    an alternative to the bulb's own onboard 'scene' animations (DP 25):
    those run autonomously on each bulb's own internal clock starting from
    whenever they were individually triggered, which is exactly why
    power-cycling multiple bulbs at once was the only way to line them up -
    and even then, Wi-Fi smart plugs' own switch-on latency makes that
    unreliable. A PC-driven loop has no such problem. The bulb's DP 25
    on-wire packing was also never independently confirmed for these bulbs
    (see DEVICE_NOTES.md), so this avoids depending on it at all.

    Each lamp can optionally run the SAME animation phase-shifted in time
    via its own PerLampEffect.phase_offset_ms (0 = perfectly synchronized,
    the default; nonzero values create a traveling "wave" look instead).
    """

    enabled: bool = False
    scene: str = "color_cycle"  # "color_cycle" | "breathing" | "temp_breathing"
    speed_hz: float = 0.1  # cycles per second; one full loop = 1/speed_hz seconds
    reverse: bool = False  # flips which way "color_cycle" sweeps the hue wheel (no visible effect on the symmetric breathing pulses)

    # color_cycle / breathing (drives RGB colour work_mode)
    hue: float = 0.0  # fixed hue for "breathing" - ignored by "color_cycle" (it cycles hue itself)
    saturation: float = 1.0
    brightness: float = 0.85  # color_cycle's constant brightness, or breathing's peak brightness
    min_brightness: float = 0.05  # breathing's trough brightness

    # temp_breathing (drives WHITE work_mode)
    temp_min: float = 0.0
    temp_max: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AmbientSceneConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            scene=d.get("scene", "color_cycle"),
            speed_hz=float(d.get("speed_hz", 0.1)),
            reverse=bool(d.get("reverse", False)),
            hue=float(d.get("hue", 0.0)),
            saturation=float(d.get("saturation", 1.0)),
            brightness=float(d.get("brightness", 0.85)),
            min_brightness=float(d.get("min_brightness", 0.05)),
            temp_min=float(d.get("temp_min", 0.0)),
            temp_max=float(d.get("temp_max", 1.0)),
        )


@dataclass
class ManualStateConfig:
    """The manual control app's last-applied STATIC color/white-balance -
    deliberately separate from ChaseEffectConfig/WhiteChaseEffectConfig,
    which only remember the *animation* settings, not the actual base color
    a user picked via 'Apply to Selected'. Without this, a picked color only
    ever lived in ManualLightController.base_colors (pure in-memory state),
    so restarting the app reset to an empty base - visually wrong even
    though every *setting* was technically remembered correctly. Restored
    and re-pushed to the lamps once at manual app startup.

    `last_mode` tracks whether RGB or White was the last thing actually
    applied (mirrors ManualLightController.mode), so restart picks up
    whichever one you were using. Turning a selection off does NOT update
    this - "off" is a power state, not a color preference, and should not
    overwrite the color you'll want back when you turn it on again.
    """

    last_mode: str = "rgb"  # "rgb" | "white"
    last_color_r: float = 0.2
    last_color_g: float = 0.5
    last_color_b: float = 0.9
    last_white_brightness: float = 0.8
    last_white_temp: float = 0.5

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ManualStateConfig":
        return cls(
            last_mode=d.get("last_mode", "rgb"),
            last_color_r=float(d.get("last_color_r", 0.2)),
            last_color_g=float(d.get("last_color_g", 0.5)),
            last_color_b=float(d.get("last_color_b", 0.9)),
            last_white_brightness=float(d.get("last_white_brightness", 0.8)),
            last_white_temp=float(d.get("last_white_temp", 0.5)),
        )


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

@dataclass
class RhythmConfig:
    """The shared beat clock (dsp.beat_clock.BeatClock): one beat source
    that Beat Sync (when `shared_clock` is on) and the Chase / Group Switch
    overlays (sync_mode "clock") all follow, instead of each running its own
    detector and reacting to different hits at different moments.

    The defaults target kick drums. `sensitivity` is a ratio on a dB-scaled
    level, so small numbers above 1.0 are already selective (1.1 ~ the top
    few percent of ticks on typical music)."""

    shared_clock: bool = False  # Beat Sync / Beat Sync White follow the clock instead of their own detector
    detect_low_hz: float = 40.0
    detect_high_hz: float = 150.0
    sensitivity: float = 1.1
    min_interval_ms: float = 200.0
    min_energy: float = 0.05
    tempo_lock: bool = True  # lock onto the tempo: ignore off-beat hits, fill in missed beats
    lead_ms: float = 100.0  # send locked beats this early, to cancel out network/bulb delay
    beats_per_bar: int = 4
    accent_ratio: float = 0.25  # the hardest this fraction of beats count as accents
    # When the beat is lost while the music keeps playing, keep going on the
    # last tempo for up to this many bars (0 = stop and wait for the beat).
    coast_bars: int = 8

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RhythmConfig":
        return cls(
            shared_clock=bool(d.get("shared_clock", False)),
            detect_low_hz=float(d.get("detect_low_hz", 40.0)),
            detect_high_hz=float(d.get("detect_high_hz", 150.0)),
            sensitivity=float(d.get("sensitivity", 1.1)),
            min_interval_ms=float(d.get("min_interval_ms", 200.0)),
            min_energy=float(d.get("min_energy", 0.05)),
            tempo_lock=bool(d.get("tempo_lock", True)),
            lead_ms=float(d.get("lead_ms", 100.0)),
            beats_per_bar=max(1, int(d.get("beats_per_bar", 4))),
            accent_ratio=float(d.get("accent_ratio", 0.25)),
            coast_bars=max(0, int(d.get("coast_bars", 8))),
        )


@dataclass
class PulseSequencerConfig:
    """Beat Sync's pulses placed on musical positions (effects/
    pulse_sequencer.py) instead of a probability roll per beat: rhythm
    patterns on a 16th-note grid, white flashes walking from lamp group to
    lamp group (Group Switch's groups), phrase fills and breaths, and
    loudness-following density. Needs the shared beat clock to be locked;
    until it is, Beat Sync's normal per-beat pulse settings apply."""

    enabled: bool = False
    white_pattern: str = "auto"  # see pulse_sequencer.WHITE_PATTERNS
    white_density: float = 0.85  # chance each pattern step actually flashes
    group_walk: str = "forward"  # "forward" | "pingpong" | "random" | "all"
    # What the walk steps through: "groups" = Group Switch's groups
    # (PerLampEffect.effect_group); "chase_order" = the Chase order, lamp
    # position by position. Beat Sync's white_pulse_rotators applies here too.
    walk_positions: str = "groups"
    double_chance: float = 0.25  # chance a flash repeats in the same group an 8th later
    min_group_gap_ms: float = 180.0  # a lamp never starts two white flashes closer than this
    dark_pattern: str = "auto"  # see pulse_sequencer.DARK_PATTERNS
    dark_density: float = 0.7  # chance each dark step actually happens
    dark_length: float = 1.0  # multiplier on Beat Sync's dark pulse duration
    phrase_bars: int = 8  # bars per phrase (4 or 8 in most pop/dance music)
    fills: bool = True  # denser flashes in the second half of a phrase's last bar
    phrase_accent: bool = True  # dark breath before + all-groups flash on each phrase start
    drop_detection: bool = True  # a quiet->loud jump restarts the phrase right there
    # Vary each white flash's shape with the music, around Beat Sync's white
    # pulse settings (which stay the baseline): heavy beats long and bright,
    # 16ths short and crisp, fills tightening toward the phrase end, the
    # phrase start longest with a slow fade, quiet parts dimmer and softer.
    # pulse_dynamics_amount: 0 = every flash identical, 1 = full variation.
    pulse_dynamics: bool = True
    pulse_dynamics_amount: float = 0.7

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PulseSequencerConfig":
        defaults = cls()
        kwargs = {}
        for name, default in asdict(defaults).items():
            value = d.get(name, default)
            kwargs[name] = type(default)(value) if value is not None else default
        return cls(**kwargs)


@dataclass
class AudioConfig:
    device_index: Optional[int] = None  # PyAudioWPatch loopback device index; None = default
    samplerate: int = 48000
    block_size: int = 1024
    fft_size: int = 2048
    analysis_update_hz: float = 60.0  # how often we pull a new FFT frame

    # Audio source: "loopback" (default - WASAPI "what you hear", i.e. system
    # playback) or "microphone" (a real recording device) - lets you test how
    # the lights react to actual room/ambient sound instead of only to
    # whatever's playing through Windows.
    source: str = "loopback"  # "loopback" | "microphone"
    mic_device_index: Optional[int] = None  # separate index namespace from device_index above
    # Microphones are typically much quieter than a loopback tap - this
    # multiplies captured samples before analysis/level metering (loopback
    # is unaffected). 1.0 = unchanged; raise it if a quiet mic barely
    # triggers anything, lower it if it's clipping/oversensitive.
    mic_gain: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AudioConfig":
        source = d.get("source", "loopback")
        if source not in ("loopback", "microphone"):
            source = "loopback"
        return cls(
            device_index=d.get("device_index", None),
            samplerate=int(d.get("samplerate", 48000)),
            block_size=int(d.get("block_size", 1024)),
            fft_size=int(d.get("fft_size", 2048)),
            analysis_update_hz=float(d.get("analysis_update_hz", 60.0)),
            source=source,
            mic_device_index=d.get("mic_device_index", None),
            mic_gain=float(d.get("mic_gain", 1.0)),
        )


# ---------------------------------------------------------------------------
# Network / lamp command pacing
# ---------------------------------------------------------------------------

LAMP_TRANSITIONS = ("direct", "gradient", "legacy")


@dataclass
class NetworkConfig:
    visual_update_hz: float = 30.0  # how often the color engine recomputes
    lamp_command_rate_hz: float = 20.0  # per-lamp cap on outgoing commands
    command_timeout_s: float = 0.3
    max_retries: int = 1
    auto_backoff: bool = True  # reduce rate automatically on repeated failures/latency
    # How colors and white flashes are sent. "direct" (default): the bulb's
    # real-time control datapoint (DP 28, control_data) in its "jump" mode -
    # changes land instantly, and white flashes use the white LEDs without
    # switching work_mode. "gradient": the same datapoint with the bulb's own
    # short (~0.25 s) fade. "legacy": the persistent colour datapoint (DP 24)
    # and work_mode switching for white, as in earlier builds - the bulb then
    # fades every change over ~0.7 s. Measured on the Airam PAR16 bulbs; bulbs
    # without the v2 datapoint layout always use "legacy".
    lamp_transitions: str = "direct"  # "direct" | "gradient" | "legacy"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NetworkConfig":
        return cls(
            visual_update_hz=float(d.get("visual_update_hz", 30.0)),
            lamp_command_rate_hz=float(d.get("lamp_command_rate_hz", 20.0)),
            command_timeout_s=float(d.get("command_timeout_s", 0.3)),
            max_retries=int(d.get("max_retries", 1)),
            auto_backoff=bool(d.get("auto_backoff", True)),
            lamp_transitions=d.get("lamp_transitions") if d.get("lamp_transitions") in LAMP_TRANSITIONS else "direct",
        )


# ---------------------------------------------------------------------------
# Top-level application config
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    devices: List[DeviceConfig] = field(default_factory=list)
    groups: Dict[str, List[str]] = field(default_factory=dict)  # name -> device ids
    bands_3: List[BandDefinition] = field(default_factory=default_3band)
    bands_8: List[BandDefinition] = field(default_factory=default_8band)
    color_mapping: ColorMappingConfig = field(default_factory=ColorMappingConfig)
    rhythm: RhythmConfig = field(default_factory=RhythmConfig)
    sequencer: PulseSequencerConfig = field(default_factory=PulseSequencerConfig)
    per_lamp_effects: Dict[str, PerLampEffect] = field(default_factory=dict)
    chase: "ChaseEffectConfig" = field(default_factory=lambda: ChaseEffectConfig())
    group_switch: "GroupSwitchEffectConfig" = field(default_factory=lambda: GroupSwitchEffectConfig())
    white_chase: "WhiteChaseEffectConfig" = field(default_factory=lambda: WhiteChaseEffectConfig())
    ambient_scene: "AmbientSceneConfig" = field(default_factory=lambda: AmbientSceneConfig())
    manual_state: "ManualStateConfig" = field(default_factory=lambda: ManualStateConfig())
    audio: AudioConfig = field(default_factory=AudioConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    presets: Dict[str, dict] = field(default_factory=dict)  # saved full color_mapping snapshots

    def to_dict(self) -> dict:
        return {
            "devices": [d.to_dict() for d in self.devices],
            "groups": self.groups,
            "bands_3": [b.to_dict() for b in self.bands_3],
            "bands_8": [b.to_dict() for b in self.bands_8],
            "color_mapping": self.color_mapping.to_dict(),
            "rhythm": self.rhythm.to_dict(),
            "sequencer": self.sequencer.to_dict(),
            "per_lamp_effects": {k: v.to_dict() for k, v in self.per_lamp_effects.items()},
            "chase": self.chase.to_dict(),
            "group_switch": self.group_switch.to_dict(),
            "white_chase": self.white_chase.to_dict(),
            "ambient_scene": self.ambient_scene.to_dict(),
            "manual_state": self.manual_state.to_dict(),
            "audio": self.audio.to_dict(),
            "network": self.network.to_dict(),
            "presets": self.presets,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AppConfig":
        return cls(
            devices=[DeviceConfig.from_dict(x) for x in d.get("devices", [])],
            groups=dict(d.get("groups", {})),
            bands_3=[BandDefinition.from_dict(x) for x in d.get("bands_3", [])] or default_3band(),
            bands_8=[BandDefinition.from_dict(x) for x in d.get("bands_8", [])] or default_8band(),
            color_mapping=ColorMappingConfig.from_dict(d.get("color_mapping", {})),
            rhythm=RhythmConfig.from_dict(d.get("rhythm", {})),
            sequencer=PulseSequencerConfig.from_dict(d.get("sequencer", {})),
            per_lamp_effects={
                k: PerLampEffect.from_dict(v) for k, v in d.get("per_lamp_effects", {}).items()
            },
            chase=ChaseEffectConfig.from_dict(d.get("chase", {})),
            group_switch=GroupSwitchEffectConfig.from_dict(d.get("group_switch", {})),
            white_chase=WhiteChaseEffectConfig.from_dict(d.get("white_chase", {})),
            ambient_scene=AmbientSceneConfig.from_dict(d.get("ambient_scene", {})),
            manual_state=ManualStateConfig.from_dict(d.get("manual_state", {})),
            audio=AudioConfig.from_dict(d.get("audio", {})),
            network=NetworkConfig.from_dict(d.get("network", {})),
            presets=dict(d.get("presets", {})),
        )

    @classmethod
    def with_defaults(cls) -> "AppConfig":
        return cls()
