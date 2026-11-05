"""The settings the Visualizer tab's Quick Controls show: the shared beat
clock's sensitivity (every mode) plus a few of each colour mode's own - the
ones whose effect is easiest to see and hear.

Each is a slider that also sits in a settings tab (the beat clock's in Global
Effects, the rest in Color Mapping); both edit the same config value and
follow each other through AppController.linkedSettingChanged (see
link_slider()). A path is relative to AppConfig ("rhythm.sensitivity",
"color_mapping.band_colors.focus").

The sensitivities are four different things, so they're named apart:
- Beat clock sensitivity: the shared beat clock (Global Effects tab) - what Beat Sync
  and Beat Sync White react to when they follow it, what Color Steps lets its
  hue steps out on, the pulse sequencer, and Chase / Group Switch on speed
  source 'clock'.
- Beat detector sensitivity: Beat Sync's / Beat Sync White's own detector,
  used only when they don't follow the clock.
- Hit sensitivity: which of those beats flash in Beat Sync / Beat Sync White
  (dsp.hits), with either beat source. The only one where higher = more.
- Peak sensitivity: Peak Flash's peak detector (it never uses the clock).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ..engine import rules


@dataclass(frozen=True)
class QuickSetting:
    path: str
    label: str
    minimum: float
    maximum: float
    decimals: int = 2
    suffix: str = ""
    tooltip: str = ""


CLOCK_SENSITIVITY = QuickSetting(
    "rhythm.sensitivity", "Beat clock sensitivity", 1.01, 2.0,
    tooltip="The shared beat clock (Global Effects -> Rhythm): how far above the recent average a kick "
    "must rise to count - higher = only the clearest kicks. Beat Sync and Beat Sync White follow it "
    "when they use the shared clock, Color Steps and the Pulse sequencer always, Chase and Group Switch "
    "with speed source 'clock'.",
)

_DETECTOR_SENSITIVITY_TIP = (
    "This mode's own beat detector - used only when it doesn't follow the shared beat clock "
    "(Global Effects -> Rhythm). Higher = only sharp, obvious hits count as a beat."
)
# Beat Sync's and Beat Sync White's, the same text on the Color Mapping page.
HIT_SENSITIVITY_TIP = (
    "How soft a hit can be and still flash: it multiplies how far the bass rose on each beat, so at 2 half "
    "the rise does the same. Higher = softer hits flash too, and every hit flashes brighter. Raise it if the "
    "lamps sit idle while the music plays on, lower it if they pulse on every beat. From about 2.5, even a "
    "held chord's wobble starts to flash."
)

# Band Colors' settings, the same text on the Color Mapping page.
BAND_FOCUS_TIP = (
    "How clearly the band playing out the most takes the colour: 0 = the bands' colours blend (two at once "
    "give the hue between theirs), 1 = the strongest band's colour alone, so the colour jumps from band to "
    "band - a kick to the bass colour, a hi-hat to the treble's."
)
BAND_CHORD_TURN_TIP = (
    "How far the whole palette turns with the music's note - its place on the circle of fifths, so a chord "
    "change moves every colour along while the bands keep their places relative to each other. Where no "
    "note stands out (drums, noise) it follows the sound's loudness and brightness instead. 0 = off: the "
    "band colours stay exactly as set."
)
BAND_RANGE_MEMORY_TIP = (
    "Each band is measured against its own loudest and quietest of about this long ago - that's what lets "
    "every band swing through its whole range even when all of them are loud. Short = it reacts to every "
    "hit, even in a steady section; long = only to what stands out from the last few bars."
)
BAND_RELEASE_TIP = (
    "How fast a band's activity falls back after its hit - longer = a band's colour lingers between hits, "
    "shorter = the colour flicks back right away."
)
# Color Steps' settings, the same text on the Color Mapping page.
STEPS_BASS_TIP = (
    "How much a peak in the bass (kick, bass notes) charges the next hue step: 1 = a full-strength peak counts "
    "as one hit, 0 = the bass doesn't move the colour at all. It also sets how much the bass shows in the "
    "brightness."
)
STEPS_MID_TIP = (
    "How much a peak in the mids (snare, chords, voices) charges the next hue step: 1 = a full-strength peak "
    "counts as one hit, 0 = the mids don't move the colour at all. It also sets how much the mids show in the "
    "brightness."
)
STEPS_TREBLE_TIP = (
    "How much a peak in the treble (hi-hats, cymbals) charges the next hue step: 1 = a full-strength peak "
    "counts as one hit, 0 = the treble doesn't move the colour at all. It also sets how much the treble shows "
    "in the brightness."
)
STEPS_HITS_TIP = (
    "How much charge a hue step takes, in hits: low = a step on nearly every beat, high = one every bar or so. "
    "Each band's peaks are measured against that band's own strongest lately, so this works the same in loud "
    "and quiet mixes. The charge leaks away slowly, and with no peaks there are no steps."
)
STEPS_SNAP_TIP = (
    "How fast the colour goes to a new step's hue - low = a snap, high = a slow fade over seconds. A slow hue "
    "still goes the way the steps went, round the wheel, however far it falls behind them."
)

QUICK_SETTINGS: Dict[str, List[QuickSetting]] = {
    "beat_sync": [
        QuickSetting("color_mapping.beat_sync.sensitivity", "Beat detector sensitivity", 1.05, 4.0,
                     tooltip=_DETECTOR_SENSITIVITY_TIP),
        QuickSetting("color_mapping.beat_sync.hit_sensitivity", "Hit sensitivity", 0.5, 3.0,
                     tooltip=HIT_SENSITIVITY_TIP),
        QuickSetting("color_mapping.beat_sync.sustain_brightness", "Sustain brightness", 0.0, 1.0,
                     tooltip="How bright the lamps stay between hits."),
        QuickSetting("color_mapping.beat_sync.brightness_release_ms", "Brightness decay", 50.0, 2000.0, 0, " ms",
                     tooltip="How slowly a hit's flash fades back to Sustain brightness."),
        QuickSetting("color_mapping.beat_sync.hue_attack_ms", "Hue snap speed", 5.0, 500.0, 0, " ms",
                     tooltip="How fast the colour changes to the beat's new hue - low = a snap, high = a fade."),
    ],
    "beat_sync_white": [
        QuickSetting("color_mapping.beat_sync_white.sensitivity", "Beat detector sensitivity", 1.05, 4.0,
                     tooltip=_DETECTOR_SENSITIVITY_TIP),
        QuickSetting("color_mapping.beat_sync_white.hit_sensitivity", "Hit sensitivity", 0.5, 3.0,
                     tooltip=HIT_SENSITIVITY_TIP),
        QuickSetting("color_mapping.beat_sync_white.sustain_brightness", "Sustain brightness", 0.0, 1.0,
                     tooltip="How bright the lamps stay between hits."),
        QuickSetting("color_mapping.beat_sync_white.brightness_release_ms", "Brightness decay", 50.0, 2000.0, 0,
                     " ms", tooltip="How slowly a hit's flash fades back to Sustain brightness."),
        QuickSetting("color_mapping.beat_sync_white.temp_attack_ms", "Temp snap speed", 5.0, 500.0, 0, " ms",
                     tooltip="How fast the white changes to the beat's new temperature."),
    ],
    "peak_flash": [
        QuickSetting("color_mapping.peak_flash.sensitivity", "Peak sensitivity", 1.05, 4.0,
                     tooltip="Peak Flash's own peak detector (it doesn't use the beat clock): higher = only "
                     "the strongest peaks flash."),
        QuickSetting("color_mapping.peak_flash.flash_release_ms", "Flash decay", 30.0, 2000.0, 0, " ms",
                     tooltip="How slowly a flash fades back down."),
        QuickSetting("color_mapping.peak_flash.treble_white_amount", "Whiteness amount", 0.0, 3.0,
                     tooltip="How much the treble washes the colour towards white."),
        QuickSetting("color_mapping.peak_flash.hue_flow_ms", "Color richness (hue flow)", 200.0, 15000.0, 0, " ms",
                     tooltip="How slowly the colour flows - high = calm, rich colours."),
        QuickSetting("color_mapping.peak_flash.baseline_min_brightness", "Baseline brightness min", 0.0, 1.0,
                     tooltip="How bright the lamps stay in quiet passages."),
    ],
    "color_steps": [
        QuickSetting("color_mapping.color_steps.bass_influence", "Bass influence", 0.0, 2.0, tooltip=STEPS_BASS_TIP),
        QuickSetting("color_mapping.color_steps.mid_influence", "Mid influence", 0.0, 2.0, tooltip=STEPS_MID_TIP),
        QuickSetting("color_mapping.color_steps.treble_influence", "Treble influence", 0.0, 2.0,
                     tooltip=STEPS_TREBLE_TIP),
        QuickSetting("color_mapping.color_steps.hits_per_step", "Hits per step", 0.25, 16.0, tooltip=STEPS_HITS_TIP),
        QuickSetting("color_mapping.color_steps.hue_attack_ms", "Hue snap speed", 5.0, 8000.0, 0, " ms",
                     tooltip=STEPS_SNAP_TIP),
    ],
    "band_colors": [
        QuickSetting("color_mapping.band_colors.focus", "Color focus", 0.0, 1.0,
                     tooltip=BAND_FOCUS_TIP),
        QuickSetting("color_mapping.band_colors.chord_turn_deg", "Chord turn", 0.0, 360.0, 0, " deg",
                     tooltip=BAND_CHORD_TURN_TIP),
        QuickSetting("color_mapping.band_colors.range_memory_s", "Range memory", 0.5, 20.0, 1, " s",
                     tooltip=BAND_RANGE_MEMORY_TIP),
        QuickSetting("color_mapping.band_colors.release_ms", "Band release", 10.0, 2000.0, 0, " ms",
                     tooltip=BAND_RELEASE_TIP),
    ],
}


def clock_in_use(config) -> bool:
    """Whether anything follows the shared beat clock right now - the
    engine's own rule (engine.rules.clock_in_use)."""
    return rules.clock_in_use(config)


def get_value(config, path: str) -> float:
    obj = config
    for name in path.split("."):
        obj = getattr(obj, name)
    return float(obj)


def set_value(config, path: str, value: float) -> None:
    *parents, name = path.split(".")
    obj = config
    for parent in parents:
        obj = getattr(obj, parent)
    setattr(obj, name, value)


def link_slider(controller, path: str, slider) -> None:
    """Keeps `slider` showing `path`'s value when another copy of it (Quick
    Controls <-> the settings tabs) changes, and tells the other copies when
    this one does. Connect the slider's own handler (the one writing the config)
    first: Qt calls slots in connection order, so the config is already
    updated when the others read it."""
    slider.valueChanged.connect(lambda _v: controller.linkedSettingChanged.emit(path, slider))

    def follow(changed_path: str, source) -> None:
        if changed_path == path and source is not slider:
            slider.set_value(get_value(controller.config, path))

    controller.linkedSettingChanged.connect(follow)
    # A plain function stays connected after the slider is gone (the Color
    # Mapping tab is rebuilt on a preset load) - drop it with the slider.
    slider.destroyed.connect(lambda *_: _disconnect(controller.linkedSettingChanged, follow))


def _disconnect(signal, slot) -> None:
    try:
        signal.disconnect(slot)
    except (RuntimeError, TypeError):
        pass
