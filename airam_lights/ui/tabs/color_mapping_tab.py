"""Color Mapping tab: full detail controls for every colour mode, the shared
beat clock, plus global response curve, smoothing, and presets.

The Dark pulses, White pulses and Pulse sequencer sections are shared by the
modes - one set of settings, so a mode can be tried without tuning them all
over again. They're one set of widgets, moved to the bottom of whichever mode
page is showing (see _place_pulse_box), with what that mode doesn't use
greyed out or hidden."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...color.models import STROBE_WAVES
from ...config.builtin_presets import BUILTIN_PRESETS, apply_builtin_preset
from ...dsp.hue_step import SOURCES as HUE_STEP_SOURCES
from ...config.user_presets import apply_preset, snapshot_preset
from ...effects.chase import get_chase_groups, get_group_switch_groups
from ...effects.pulse_sequencer import (
    COLOR_SWEEP_COLORS,
    COLOR_SWEEP_DIRECTIONS,
    COLOR_SWEEP_PLACEMENTS,
    DARK_PATTERNS,
    FLASH_STROBE_PLACEMENTS,
    GROUP_WALKS,
    LEVELS,
    STROBE_PLACEMENTS,
    WHITE_PATTERNS,
)
from ...config.schema import (
    INSTANT_TRANSITIONS,
    LAMP_TRANSITIONS,
    PULSE_TRIGGERS,
    RELEASE_CURVES,
    WHITE_TEMP_MODES,
    PulseSequencerConfig,
    RGBModeConfig,
    RhythmConfig,
)
from ...engine.rules import BEAT_MODES, sequencer_dark_only
from ..controller import AppController
from ..widgets.channel_map_editor import ChannelMapEditor
from ..widgets.hue_slider import HueSlider
from ..widgets.musical import (
    PHRASE_LENGTHS,
    beat_divisions,
    choice_combo,
    inactive_note,
    set_active,
    show_note,
)
from ..quick_settings import QUICK_SETTINGS, get_value, link_slider
from ..widgets.param_slider import FloatSlider
from ..widgets.section import CollapsibleSection

_MODE_NAMES = {
    "beat_sync": "Beat Sync",
    "beat_sync_white": "Beat Sync White",
    "peak_flash": "Peak Flash",
    "rgb_freq": "RGB Frequency",
    "hsv_music": "HSV Music",
    "custom": "Custom",
}


class RGBModeEditor(QWidget):
    """Three ChannelMapEditors side by side (R/G/B) + overall sensitivity."""

    def __init__(self, cfg: RGBModeConfig, on_change, parent=None):
        super().__init__(parent)
        self._on_change = on_change
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.r_editor = ChannelMapEditor("R (default: Bass)", cfg.r)
        self.g_editor = ChannelMapEditor("G (default: Mid)", cfg.g)
        self.b_editor = ChannelMapEditor("B (default: Treble)", cfg.b)
        for e in (self.r_editor, self.g_editor, self.b_editor):
            e.changed.connect(self._emit)
            row.addWidget(e)
        layout.addLayout(row)

        self.sensitivity_slider = FloatSlider(
            "Level sensitivity (gain)", 0.1, 4.0, cfg.sensitivity,
            tooltip="How strongly the band levels turn into light - a gain: higher = brighter, livelier.",
        )
        self.sensitivity_slider.valueChanged.connect(self._emit)
        layout.addWidget(self.sensitivity_slider)

    def _emit(self, *_args) -> None:
        self._on_change(self.to_config())

    def to_config(self) -> RGBModeConfig:
        return RGBModeConfig(
            r=self.r_editor.to_channel_map(),
            g=self.g_editor.to_channel_map(),
            b=self.b_editor.to_channel_map(),
            sensitivity=self.sensitivity_slider.value(),
        )


class ColorMappingTab(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        cm = controller.config.color_mapping

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)

        root.addWidget(self._build_rhythm_box(controller.config.rhythm))

        sub_tabs = QTabWidget()
        root.addWidget(sub_tabs)
        self.sub_tabs = sub_tabs

        # -- RGB Frequency mode -----------------------------------------------------
        self.rgb_editor = RGBModeEditor(cm.rgb, self._on_rgb_changed)
        self.rgb_attack_slider, self.rgb_release_slider = self._add_smoothing_sliders(self.rgb_editor.layout())
        sub_tabs.addTab(self.rgb_editor, "RGB Frequency")

        # -- Custom mode --------------------------------------------------------------
        self.custom_editor = RGBModeEditor(cm.custom, self._on_custom_changed)
        self.custom_attack_slider, self.custom_release_slider = self._add_smoothing_sliders(self.custom_editor.layout())
        sub_tabs.addTab(self.custom_editor, "Custom")

        # -- HSV mode -------------------------------------------------------------------
        hsv_widget = QWidget()
        hsv_layout = QVBoxLayout(hsv_widget)
        hsv = cm.hsv
        self.hue_min_slider = HueSlider("Hue @ low freq", hsv.hue_min_deg)
        self.hue_max_slider = HueSlider("Hue @ high freq", hsv.hue_max_deg)
        self.bright_min_slider = FloatSlider("Brightness min", 0.0, 1.0, hsv.brightness_min)
        self.bright_max_slider = FloatSlider("Brightness max", 0.0, 1.0, hsv.brightness_max)
        self.sat_base_slider = FloatSlider("Saturation base", 0.0, 1.0, hsv.saturation_base)
        self.sat_contrast_slider = FloatSlider("Saturation from contrast", 0.0, 1.0, hsv.saturation_contrast_gain)
        self.hsv_sensitivity_slider = FloatSlider(
            "Level sensitivity (gain)", 0.1, 4.0, hsv.sensitivity,
            tooltip="How strongly the sound level turns into light - a gain: higher = brighter, livelier.",
        )
        for w in (
            self.hue_min_slider,
            self.hue_max_slider,
            self.bright_min_slider,
            self.bright_max_slider,
            self.sat_base_slider,
            self.sat_contrast_slider,
            self.hsv_sensitivity_slider,
        ):
            w.valueChanged.connect(self._on_hsv_changed)
            hsv_layout.addWidget(w)
        self.hsv_attack_slider, self.hsv_release_slider = self._add_smoothing_sliders(hsv_layout)
        hsv_desc_label = QLabel(
            "Hue follows the spectral centroid (frequency distribution), brightness "
            "follows overall energy, saturation follows spectral contrast (peaky vs. flat)."
        )
        hsv_desc_label.setWordWrap(True)
        hsv_layout.addWidget(hsv_desc_label)
        sub_tabs.insertTab(1, hsv_widget, "HSV Music")  # before Custom, as in the Visualizer tab

        # -- Beat Sync mode -----------------------------------------------------------
        beat_widget = QWidget()
        beat_page = QVBoxLayout(beat_widget)
        bs = cm.beat_sync

        def beat_section(title: str, key: str, **kw) -> CollapsibleSection:
            section = CollapsibleSection(title, key=f"beat/{key}", **kw)
            beat_page.addWidget(section)
            return section

        # Each part of Beat Sync is a collapsible section; `beat_layout` is
        # the one being filled.
        beat_layout = beat_section(
            "Beat detection", "detect",
            tooltip="Beat Sync's own beat detector - used only while it doesn't follow the shared beat "
            "clock (the Rhythm section at the top).",
        ).content_layout()
        detect_row = QHBoxLayout()
        detect_row.addWidget(QLabel("Beat detection band:"))
        self.beat_low_spin = QSpinBox()
        self.beat_low_spin.setRange(20, 20000)
        self.beat_low_spin.setSuffix(" Hz")
        self.beat_low_spin.setValue(int(bs.detect_low_hz))
        self.beat_low_spin.setToolTip(
            "Which frequencies count as a 'beat' at all - every hue/brightness snap below, and both "
            "pulse types further down, only ever happen when a hit is detected somewhere in this range."
        )
        detect_row.addWidget(self.beat_low_spin)
        detect_row.addWidget(QLabel("-"))
        self.beat_high_spin = QSpinBox()
        self.beat_high_spin.setRange(20, 20000)
        self.beat_high_spin.setSuffix(" Hz")
        self.beat_high_spin.setValue(int(bs.detect_high_hz))
        self.beat_high_spin.setToolTip(self.beat_low_spin.toolTip())
        detect_row.addWidget(self.beat_high_spin)
        detect_row.addStretch(1)
        beat_layout.addLayout(detect_row)
        beat_band_note = QLabel(
            "Default 40-200 Hz targets kick drums; widen it (e.g. to also cover hi-hats/cymbals) if "
            "you want the white pulse below to have hits of its own to react to - see its note further "
            "down."
        )
        beat_band_note.setWordWrap(True)
        beat_layout.addWidget(beat_band_note)
        self.beat_detect_note = inactive_note()
        beat_layout.addWidget(self.beat_detect_note)

        self.beat_sensitivity_slider = FloatSlider(
            "Beat detector sensitivity", 1.05, 4.0, bs.sensitivity, decimals=2,
            tooltip="Higher = only very sharp, obvious hits register as a beat at all, so every hue/"
            "brightness snap and both pulse types below fire less often but more confidently.",
        )
        self.beat_min_interval_slider = FloatSlider(
            "Min interval", 30.0, 1000.0, bs.min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two triggered beats - stops one sustained hit from "
            "re-triggering the hue/brightness snap (and pulses) many times in quick succession.",
        )
        self.beat_min_energy_slider = FloatSlider(
            "Min energy floor", 0.0, 1.0, bs.min_energy,
            tooltip="Absolute loudness floor below which nothing can trigger, even if it's a relative "
            "spike - keeps quiet passages from firing hue/brightness snaps or pulses on near-silence. "
            "This is an AUDIO-side gate inside the beat detector itself, deciding whether a moment "
            "counts as a beat at all - unrelated to the Global tab's 'Min change threshold', which is a "
            "NETWORK optimization applied afterward, on the already-computed color.",
        )
        for w in (self.beat_sensitivity_slider, self.beat_min_interval_slider, self.beat_min_energy_slider):
            w.valueChanged.connect(self._on_beat_changed)
            beat_layout.addWidget(w)
        beat_sensitivity_note = QLabel("Lower sensitivity / higher min-interval = fewer, more confident beat triggers.")
        beat_sensitivity_note.setWordWrap(True)
        beat_layout.addWidget(beat_sensitivity_note)

        beat_layout = beat_section(
            "Colour on the beat", "color",
            tooltip="Which colour each beat jumps to, how bright the flash is and how it decays.",
        ).content_layout()
        hue_row = QHBoxLayout()
        hue_row.addWidget(QLabel("Hue mode:"))
        self.beat_hue_mode_combo = QComboBox()
        self.beat_hue_mode_combo.addItems(["random", "step", "spectrum"])
        self.beat_hue_mode_combo.setCurrentText(bs.hue_mode)
        self.beat_hue_mode_combo.setToolTip(
            "Which hue (color, i.e. the RGB mix) each beat jumps to - see the note below for what each "
            "option does. Saturation and brightness are controlled separately by the sliders further down."
        )
        self.beat_hue_mode_combo.currentTextChanged.connect(self._on_beat_changed)
        hue_row.addWidget(self.beat_hue_mode_combo)
        hue_row.addSpacing(16)
        hue_row.addWidget(QLabel("Spectrum from:"))
        self.beat_spectrum_source_combo = QComboBox()
        self.beat_spectrum_source_combo.addItems(["pitch", "intensity_brightness", "brightness", "intensity"])
        self.beat_spectrum_source_combo.setCurrentText(bs.spectrum_source)
        self.beat_spectrum_source_combo.setToolTip(
            "What places each beat's color in the hue range (HSV Music's Hue min/max) in 'spectrum' "
            "mode. pitch: the note that stands out, around the circle of fifths (related notes and keys "
            "get neighbouring colors) - while a note stands out clearly and the notes move; when they "
            "don't (drums, noise, atonal passages, one note droning on) it glides over to "
            "intensity_brightness and back, no switching. intensity_brightness: loudness and "
            "bass-heavy vs bright sound averaged. brightness / intensity: one of them. Each follows the "
            "music's own recent range, so every song uses the whole hue range."
        )
        self.beat_spectrum_source_combo.currentTextChanged.connect(self._on_beat_changed)
        hue_row.addWidget(self.beat_spectrum_source_combo)
        hue_row.addStretch(1)
        beat_layout.addLayout(hue_row)
        beat_hue_mode_note = QLabel(
            "random: a fresh, sufficiently-different color every hit. step: cycles through the color "
            "wheel by a fixed angle each hit (never repeats for a long time). spectrum: the music picks "
            "the color (see Spectrum from), spread over HSV Music's Hue min..max."
        )
        beat_hue_mode_note.setWordWrap(True)
        beat_layout.addWidget(beat_hue_mode_note)
        self.beat_spectrum_status_label = QLabel()
        self.beat_spectrum_status_label.setWordWrap(True)
        beat_layout.addWidget(self.beat_spectrum_status_label)
        self._spectrum_timer = QTimer(self)
        self._spectrum_timer.timeout.connect(self._refresh_spectrum_status)
        self._spectrum_timer.start(200)

        hue_every_row = QHBoxLayout()
        hue_every_row.addWidget(QLabel("Change hue:"))
        self.beat_hue_every_combo = choice_combo(
            beat_divisions(controller.config.rhythm.beats_per_bar), bs.hue_every_n_beats,
            "Every beat still flashes, but the color only changes on these beats. With the shared beat "
            "clock this is counted from the bar start, so e.g. 'every bar' changes color exactly on "
            "beat 1. The choices follow Beats per bar in the Rhythm box.",
        )
        self.beat_hue_every_combo.currentIndexChanged.connect(self._on_beat_changed)
        hue_every_row.addWidget(self.beat_hue_every_combo)
        hue_every_row.addStretch(1)
        beat_layout.addLayout(hue_every_row)

        self.beat_hue_step_slider = FloatSlider(
            "Hue step (for 'step')", 1.0, 180.0, bs.hue_step_deg, decimals=1, suffix=" deg",
            tooltip="How far around the color wheel the hue jumps on each beat, in 'step' mode - "
            "bigger steps mean more visually different colors from one hit to the next.",
        )
        self.beat_hue_step_source_row = QWidget()
        step_source_layout = QHBoxLayout(self.beat_hue_step_source_row)
        step_source_layout.setContentsMargins(0, 0, 0, 0)
        step_source_layout.addWidget(QLabel("Hue step from:"))
        self.beat_hue_step_source_combo = QComboBox()
        self.beat_hue_step_source_combo.addItems(list(HUE_STEP_SOURCES))
        self.beat_hue_step_source_combo.setCurrentText(bs.hue_step_source)
        self.beat_hue_step_source_combo.setToolTip(
            "'step' mode: fixed = the Hue step above on every color change. The others let the music "
            "set each step, between Step min and Step max: intensity = how loud it is right now (small "
            "steps to neighbouring colors in a quiet part, big jumps to contrasting colors in a chorus); "
            "brightness = bass-heavy vs bright sound; intensity_brightness = both; pitch = the note "
            "that stands out, by its tension in the key (the key note = Step min, notes foreign to the "
            "key = Step max), gliding over to intensity_brightness when no note stands out or the "
            "notes don't move. Each follows the music's own recent range. The color always moves on, "
            "so it can't get stuck on one color."
        )
        self.beat_hue_step_source_combo.currentTextChanged.connect(self._on_beat_changed)
        step_source_layout.addWidget(self.beat_hue_step_source_combo)
        step_source_layout.addStretch(1)
        self.beat_hue_step_min_slider = FloatSlider(
            "Step min", 0.0, 180.0, bs.hue_step_min_deg, decimals=0, suffix=" deg",
            tooltip="The step when the music source is at its lowest (quiet, dark sound, the key note).",
        )
        self.beat_hue_step_max_slider = FloatSlider(
            "Step max", 0.0, 180.0, bs.hue_step_max_deg, decimals=0, suffix=" deg",
            tooltip="The step when the music source is at its highest (loud, bright sound, notes foreign "
            "to the key). 180 = the opposite color.",
        )
        self.beat_min_jump_slider = FloatSlider(
            "Min hue jump (for 'random')", 0.0, 180.0, bs.min_hue_jump_deg, decimals=0, suffix=" deg",
            tooltip="In 'random' mode, how different the new hue must be from the last one - prevents "
            "two consecutive beats from landing on nearly the same color purely by chance.",
        )
        self.beat_saturation_slider = FloatSlider(
            "Saturation", 0.0, 1.0, bs.saturation,
            tooltip="The base color vividness on every beat (1.0 = fully saturated, lower = more "
            "pastel/washed out) - this is the saturation both the dark and white pulses below "
            "temporarily push away from, then ease back to.",
        )
        self.beat_flash_slider = FloatSlider(
            "Flash brightness", 0.0, 1.0, bs.flash_brightness,
            tooltip="How bright the color is exactly on the beat, before it starts decaying - this is "
            "the flash you actually see land on the hit itself.",
        )
        self.beat_sustain_slider = FloatSlider(
            "Sustain brightness", 0.0, 1.0, bs.sustain_brightness,
            tooltip="How bright it settles to between beats, once the flash has decayed - the resting/"
            "idle brightness the lamp sits at until the next hit.",
        )
        self.beat_hue_attack_slider = FloatSlider(
            "Hue snap speed", 5.0, 500.0, bs.hue_attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast the color transitions to the new hue after a beat - low = an almost "
            "instant snap, high = a visible fade from the old color into the new one.",
        )
        self.beat_bright_attack_slider = FloatSlider(
            "Brightness attack", 1.0, 200.0, bs.brightness_attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast brightness jumps up to Flash brightness on a beat - low = a sharp, "
            "percussive flash; high = brightness eases up instead of snapping.",
        )
        self.beat_bright_release_slider = FloatSlider(
            "Brightness decay", 50.0, 2000.0, bs.brightness_release_ms, decimals=0, suffix=" ms",
            tooltip="How slowly brightness fades from the flash back down to Sustain brightness - "
            "higher means a longer glow tail lingering after each hit.",
        )
        for w in (
            self.beat_hue_step_slider,
            self.beat_hue_step_source_row,
            self.beat_hue_step_min_slider,
            self.beat_hue_step_max_slider,
            self.beat_min_jump_slider,
            self.beat_saturation_slider,
            self.beat_flash_slider,
            self.beat_sustain_slider,
            self.beat_hue_attack_slider,
            self.beat_bright_attack_slider,
            self.beat_bright_release_slider,
        ):
            if isinstance(w, FloatSlider):
                w.valueChanged.connect(self._on_beat_changed)
            beat_layout.addWidget(w)

        beat_layout = beat_section(
            "Between beats", "between",
            tooltip="What happens between two beats: the brightness fades, the colour glides on toward "
            "the next one, or both.",
        ).content_layout()
        between_row = QHBoxLayout()
        between_row.addWidget(QLabel("Between beats:"))
        self.beat_fade_checkbox = QCheckBox("Fade brightness")
        self.beat_fade_checkbox.setChecked(bs.fade_brightness)
        self.beat_fade_checkbox.setToolTip(
            "On: the classic flash - Flash brightness on the beat, fading to Sustain brightness. Off: "
            "brightness stays at Flash brightness all the time (dark pulses still dip it) - good for "
            "RGB+CCT bulbs whose colored LEDs are much dimmer than their white ones."
        )
        self.beat_glide_checkbox = QCheckBox("Glide hue")
        self.beat_glide_checkbox.setChecked(bs.glide_hue)
        self.beat_glide_checkbox.setToolTip(
            "On: after each beat the color glides toward the next color, and the next beat lands on "
            "it - the rhythm shows as moving color. Works together with Fade brightness or on its own."
        )
        for cb in (self.beat_fade_checkbox, self.beat_glide_checkbox):
            cb.toggled.connect(self._on_beat_changed)
            between_row.addWidget(cb)
        between_row.addSpacing(16)
        between_row.addWidget(QLabel("Glide timing:"))
        self.beat_glide_timing_combo = QComboBox()
        self.beat_glide_timing_combo.addItems(["beat", "decay"])
        self.beat_glide_timing_combo.setCurrentText(bs.hue_glide_timing)
        self.beat_glide_timing_combo.setToolTip(
            "beat: the color moves evenly through the whole beat and arrives right as the next beat "
            "hits - a color wheel turning in time with the music (uses the tempo). decay: follows "
            "Brightness attack/decay - a quick sweep right after the hit, then still."
        )
        self.beat_glide_timing_combo.currentTextChanged.connect(self._on_beat_changed)
        between_row.addWidget(self.beat_glide_timing_combo)
        between_row.addStretch(1)
        beat_layout.addLayout(between_row)
        self.beat_hue_glide_slider = FloatSlider(
            "Glide distance", 0.0, 360.0, bs.hue_glide_deg, decimals=0, suffix=" deg",
            tooltip="How far around the color wheel the color travels between two beats, toward the next "
            "color. About the Hue step (in 'step' mode) = it arrives exactly at the next color; more "
            "overshoots and snaps back on the beat; 360 = a full rainbow every beat. In 'spectrum' mode "
            "the color glides this far the way the music's color last moved, turning round at the ends "
            "of the hue range.",
        )
        self.beat_hue_glide_slider.valueChanged.connect(self._on_beat_changed)
        beat_layout.addWidget(self.beat_hue_glide_slider)

        # -- the pulses, shared by every mode (see _place_pulse_box) ----------------------
        # Dark pulses, White pulses and the Pulse sequencer, in one box that
        # moves to the bottom of the mode page showing. The settings in them
        # belong to Beat Sync's config (they're its pulses' look), which the
        # sequencer uses in every mode.
        self.pulse_box = QWidget()
        pulse_layout = QVBoxLayout(self.pulse_box)
        pulse_layout.setContentsMargins(0, 0, 0, 0)
        self._pulse_mode = "beat_sync"  # the mode page the box is on

        def pulse_section(title: str, key: str, **kw) -> CollapsibleSection:
            section = CollapsibleSection(title, key=f"beat/{key}", **kw)
            pulse_layout.addWidget(section)
            return section

        dark_section = pulse_section(
            "Dark pulses", "dark", checkable=True, checked=bs.dark_pulse_enabled,
            tooltip="A brief dip toward black - a rhythm-synced pause before the next hit. In Beat Sync on a "
            "random share of beats (probability and trigger below), or wherever the Pulse sequencer puts "
            "them; in the other modes only from the Pulse sequencer (Beat Sync White's own per-beat ones "
            "are on its page). The look below is the same in every mode. White pulses are unaffected "
            "either way.",
        )
        beat_layout = dark_section.content_layout()
        self.beat_dark_enabled_checkbox = dark_section.toggle
        self.beat_dark_enabled_checkbox.toggled.connect(self._on_beat_changed)
        self.beat_dark_trigger_combo, self.beat_dark_trigger_row = self._trigger_combo(
            beat_layout, "Dark pulse on:", bs.dark_pulse_trigger
        )
        self.beat_dark_note = dark_section.note

        self.beat_dark_prob_slider = FloatSlider(
            "Dark pulse probability", 0.0, 1.0, bs.dark_pulse_probability, decimals=2,
            tooltip="Chance a given beat gets this dark dip instead of flashing immediately - "
            "0 = never, 1 = every beat.",
        )
        self.beat_dark_duration_slider = FloatSlider(
            "Dark pulse duration", 10.0, 500.0, bs.dark_pulse_duration_ms, decimals=0, suffix=" ms",
            tooltip="How long brightness is held down near black before the delayed flash actually happens.",
        )
        self.beat_dark_depth_slider = FloatSlider(
            "Dark pulse depth", 0.0, 1.0, bs.dark_pulse_depth, decimals=2,
            tooltip="How far toward black the dip goes - 1.0 = fully black for the duration above, "
            "lower = a partial dim instead of a total blackout.",
        )
        self.beat_dark_attack_slider = FloatSlider(
            "Dark pulse attack", 1.0, 300.0, bs.dark_pulse_attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast brightness snaps down into the dip when the pulse starts - low = an "
            "instant cut to black, right on the beat.",
        )
        self.beat_dark_release_slider = FloatSlider(
            "Dark pulse release", 10.0, 1000.0, bs.dark_pulse_release_ms, decimals=0, suffix=" ms",
            tooltip="How slowly brightness eases back out of the dip once the pulse's duration ends - "
            "higher means a longer visible fade back before/into the delayed flash.",
        )
        for w in (
            self.beat_dark_prob_slider,
            self.beat_dark_duration_slider,
            self.beat_dark_depth_slider,
            self.beat_dark_attack_slider,
            self.beat_dark_release_slider,
        ):
            w.valueChanged.connect(self._on_beat_changed)
            beat_layout.addWidget(w)

        white_section = pulse_section(
            "White pulses", "white", checkable=True, checked=bs.white_pulse_enabled,
            tooltip="A brief flash of the lamp's own white LEDs - e.g. a hi-hat/cymbal accent. In Beat Sync on "
            "a subset of beats (probability and trigger below), or wherever the Pulse sequencer puts them; "
            "in Peak Flash, RGB Frequency, HSV Music and Custom only from the Pulse sequencer; never in "
            "Beat Sync White, which is white already. Always the real white LEDs, never white mixed from "
            "RGB, so the colored LEDs keep all their output for color. The look below is the same in every "
            "mode. Independent of dark pulses - both, either, or neither can happen on a hit.",
        )
        beat_layout = white_section.content_layout()
        self.beat_white_pulse_enabled_checkbox = white_section.toggle
        self.beat_white_pulse_enabled_checkbox.toggled.connect(self._on_beat_changed)

        white_target_row = QHBoxLayout()
        # "True white lamps" is for Beat Sync's per-beat flashes only - its own
        # box, so it can go on the other modes' pages (see _update_pulse_states).
        self.beat_white_target_box = QWidget()
        target_box_layout = QHBoxLayout(self.beat_white_target_box)
        target_box_layout.setContentsMargins(0, 0, 0, 0)
        target_box_layout.addWidget(QLabel("True white lamps:"))
        self.beat_white_pulse_target_combo = QComboBox()
        self.beat_white_pulse_target_combo.addItems(["all", "chase", "group", "rotate"])
        self.beat_white_pulse_target_combo.setCurrentText(bs.white_pulse_target)
        self.beat_white_pulse_target_combo.setToolTip(
            "Which lamps a white flash lands on. all "
            "(default): every selected lamp at once. chase: the lamps the Chase highlight is on at that "
            "moment - if Chase moves more than one lamp between flashes, the white skips lamps. group: "
            "Group Switch's currently active group. rotate: the white gets its own rotation through the "
            "Chase order ('Chase order' on the Devices & Setup tab) - every flash moves exactly one lamp on, "
            "so none is skipped however fast Chase moves; it turns the same way as Chase. The lamps are "
            "picked when the flash starts and kept for its whole duration. With too few positions for "
            "the chosen option, every lamp flashes."
        )
        self.beat_white_pulse_target_combo.currentTextChanged.connect(self._on_beat_changed)
        target_box_layout.addWidget(self.beat_white_pulse_target_combo)
        target_box_layout.addSpacing(16)
        white_target_row.addWidget(self.beat_white_target_box)
        white_target_row.addWidget(QLabel("Rotating:"))
        self.beat_white_rotators_combo = choice_combo(
            [(1, "1 lamp"), (2, "2 opposite lamps"), (3, "3 evenly spaced"), (4, "4 evenly spaced")],
            bs.white_pulse_rotators,
            "How many lamps flash white at once as the white rotates, evenly spaced around the loop - "
            "2 = two lamps on opposite sides, 3 = a third of the loop apart. Used by 'rotate' above and "
            "by the Pulse sequencer's walk.",
        )
        self.beat_white_rotators_combo.currentIndexChanged.connect(self._on_beat_changed)
        white_target_row.addWidget(self.beat_white_rotators_combo)
        white_target_row.addStretch(1)
        beat_layout.addLayout(white_target_row)
        self.beat_white_rotators_note = inactive_note()
        beat_layout.addWidget(self.beat_white_rotators_note)
        self.beat_white_trigger_combo, self.beat_white_trigger_row = self._trigger_combo(
            beat_layout, "White pulse on:", bs.white_pulse_trigger
        )
        self.beat_white_note = white_section.note

        self.beat_white_pulse_prob_slider = FloatSlider(
            "White pulse probability", 0.0, 1.0, bs.white_pulse_probability, decimals=2,
            tooltip="Chance a given beat gets a white flash - 0 = never, 1 = every beat.",
        )
        self.beat_white_pulse_duration_slider = FloatSlider(
            "White pulse duration", 10.0, 500.0, bs.white_pulse_duration_ms, decimals=0, suffix=" ms",
            tooltip="How long the lamp is held on its white LEDs before switching back to color.",
        )
        self.beat_white_pulse_attack_slider = FloatSlider(
            "White pulse attack", 1.0, 300.0, bs.white_pulse_attack_ms, decimals=0, suffix=" ms",
            tooltip="How long the white flash takes to rise to full brightness - low = a hard flash right "
            "on the beat, higher = it swells in. (With 'legacy' Lamp transitions the bulb fades on its own "
            "and this only sets the timing.)",
        )
        self.beat_white_pulse_release_slider = FloatSlider(
            "White pulse release", 10.0, 1000.0, bs.white_pulse_release_ms, decimals=0, suffix=" ms",
            tooltip="How long the white flash takes to fade out after its hold, before the lamp is back on "
            "color. Attack + duration + release is the whole flash. (With 'legacy' Lamp transitions the "
            "bulb fades on its own and this only sets the timing.)",
        )
        self.beat_white_pulse_white_brightness_slider = FloatSlider(
            "White pulse brightness", 0.0, 1.0, bs.white_pulse_white_brightness, decimals=2,
            tooltip="Brightness of the white LEDs during the flash. 1.0 = strongest possible intensity.",
        )
        self.beat_white_pulse_cool_ratio_slider = FloatSlider(
            "White pulse cool ratio", 0.0, 1.0, bs.white_pulse_cool_ratio, decimals=2,
            tooltip="Each flash independently rolls warm vs "
            "cool white using this as the chance of landing on cool, so flashes vary beat to beat "
            "instead of always looking the same. 0.0 = always warm, 1.0 = always cool, 0.5 (default) = "
            "a roughly even, unpredictable mix. The choice is made once per flash and held for its "
            "whole duration, not re-rolled mid-flash.",
        )
        for w in (
            self.beat_white_pulse_prob_slider,
            self.beat_white_pulse_duration_slider,
            self.beat_white_pulse_attack_slider,
            self.beat_white_pulse_release_slider,
            self.beat_white_pulse_white_brightness_slider,
            self.beat_white_pulse_cool_ratio_slider,
        ):
            w.valueChanged.connect(self._on_beat_changed)
            beat_layout.addWidget(w)
        self.beat_white_release_curve_combo = self._labeled_combo(
            beat_layout, "White release curve:", RELEASE_CURVES, bs.white_pulse_release_curve,
            "The shape of the fade over White pulse release - shows best with a longer release "
            "(a few hundred ms). linear: a straight fade. ease_in: starts slowly - the flash lingers "
            "near full, then drops away at the end. ease_out: drops fast, then a long soft tail, like a "
            "struck drum or cymbal. ease_in_out: an S-curve - lingers, falls, then lands softly. "
            "dynamic: picked per flash by where it falls in the music - ease_in_out for the phrase "
            "start and in quiet parts, ease_in (hangs on like a held note) on the heavy beats, "
            "ease_out (percussive) on the lighter ones, the fills and the doubles. "
            "For the Pulse sequencer's flashes, with Lamp transitions other than 'legacy'.",
        )
        self.beat_white_release_curve_combo.currentTextChanged.connect(self._on_beat_changed)
        self.beat_white_temp_combo = self._labeled_combo(
            beat_layout, "Warm/cool:", WHITE_TEMP_MODES, bs.white_pulse_temp_mode,
            "How each flash's white is chosen between warm and cool. random: rolled per flash with the "
            "cool ratio above. bar: by the weight of the beat - the downbeat coolest, the bar's middle "
            "beat half-cool, other beats warmer, flashes between beats warmest - so heavy beats stand out "
            "from light ones. alternate: cool, warm, cool, warm. loudness: warm in the quiet parts of the "
            "song, cool in the loud ones. phrase: cools down over each phrase toward its end, the new "
            "phrase's first flash is cool, then back to warm (phrase length: see the Pulse sequencer).",
        )
        self.beat_white_temp_combo.currentTextChanged.connect(self._on_beat_changed)
        self.beat_white_hybrid_slider = FloatSlider(
            "Hybrid warm/cool", 0.0, 1.0, bs.white_pulse_hybrid_chance, decimals=2,
            tooltip="Chance a flash is a hybrid: its colour temperature glides from one end to the other - "
            "following the flash's light, not the clock: halfway through its light, halfway through the "
            "change, so it happens while the flash is bright. A flash Warm/cool picked cool glides from "
            "cool to warm, a warm one from warm to cool. 0 = never, 1 = every flash. A flash "
            "strobe keeps one temperature. The info panel's timeline shows each flash in its warm/cool, "
            "a hybrid as a gradient.",
        )
        self.beat_white_hybrid_slider.valueChanged.connect(self._on_beat_changed)
        beat_layout.addWidget(self.beat_white_hybrid_slider)
        self.beat_white_temp_note = inactive_note()
        beat_layout.addWidget(self.beat_white_temp_note)

        self.beat_low_spin.valueChanged.connect(self._on_beat_changed)
        self.beat_high_spin.valueChanged.connect(self._on_beat_changed)

        pulse_layout.addWidget(self._build_sequencer_box(controller.config.sequencer))
        beat_page.addWidget(self.pulse_box)

        # The beat modes are built after the others but shown first - Beat Sync is the main mode.
        sub_tabs.insertTab(0, beat_widget, "Beat Sync")

        # -- Beat Sync White mode -------------------------------------------------------
        bsw_widget = QWidget()
        bsw_layout = QVBoxLayout(bsw_widget)
        bsw = cm.beat_sync_white
        bsw_desc_label = QLabel(
            "Same rhythm-reactive envelope as Beat Sync, but drives the bulb's WHITE work_mode "
            "(brightness + color temperature) instead of RGB - warm/cool flashes on the beat. "
            "See DEVICE_NOTES.md: the underlying set_white() call is not yet independently "
            "confirmed against the physical bulbs the way RGB is."
        )
        bsw_desc_label.setWordWrap(True)
        bsw_layout.addWidget(bsw_desc_label)

        bsw_detect_row = QHBoxLayout()
        bsw_detect_row.addWidget(QLabel("Beat detection band:"))
        self.bsw_low_spin = QSpinBox()
        self.bsw_low_spin.setRange(20, 20000)
        self.bsw_low_spin.setSuffix(" Hz")
        self.bsw_low_spin.setValue(int(bsw.detect_low_hz))
        bsw_detect_row.addWidget(self.bsw_low_spin)
        bsw_detect_row.addWidget(QLabel("-"))
        self.bsw_high_spin = QSpinBox()
        self.bsw_high_spin.setRange(20, 20000)
        self.bsw_high_spin.setSuffix(" Hz")
        self.bsw_high_spin.setValue(int(bsw.detect_high_hz))
        bsw_detect_row.addWidget(self.bsw_high_spin)
        bsw_detect_row.addStretch(1)
        bsw_layout.addLayout(bsw_detect_row)

        self.bsw_sensitivity_slider = FloatSlider(
            "Beat detector sensitivity", 1.05, 4.0, bsw.sensitivity, decimals=2,
            tooltip="Beat Sync White's own beat detector - used only when it doesn't follow the shared beat "
            "clock (Rhythm box). Higher = only sharp, obvious hits count as a beat.",
        )
        self.bsw_min_interval_slider = FloatSlider("Min interval", 30.0, 1000.0, bsw.min_interval_ms, decimals=0, suffix=" ms")
        self.bsw_min_energy_slider = FloatSlider("Min energy floor", 0.0, 1.0, bsw.min_energy)
        for w in (self.bsw_sensitivity_slider, self.bsw_min_interval_slider, self.bsw_min_energy_slider):
            w.valueChanged.connect(self._on_beat_white_changed)
            bsw_layout.addWidget(w)

        bsw_temp_mode_row = QHBoxLayout()
        bsw_temp_mode_row.addWidget(QLabel("Temperature mode:"))
        self.bsw_temp_mode_combo = QComboBox()
        self.bsw_temp_mode_combo.addItems(["random", "alternate"])
        self.bsw_temp_mode_combo.setCurrentText(bsw.temp_mode)
        self.bsw_temp_mode_combo.currentTextChanged.connect(self._on_beat_white_changed)
        bsw_temp_mode_row.addWidget(self.bsw_temp_mode_combo)
        bsw_temp_mode_row.addStretch(1)
        bsw_layout.addLayout(bsw_temp_mode_row)
        bsw_temp_mode_note = QLabel(
            "random: a new temperature on every change. alternate: ping-pongs between the warm and cool ends."
        )
        bsw_temp_mode_note.setWordWrap(True)
        bsw_layout.addWidget(bsw_temp_mode_note)

        bsw_every_row = QHBoxLayout()
        bsw_every_row.addWidget(QLabel("Change temperature:"))
        self.bsw_temp_every_combo = choice_combo(
            beat_divisions(controller.config.rhythm.beats_per_bar), bsw.temp_every_n_beats,
            "Every beat still flashes, but the temperature only changes on these beats - fewer changes, so "
            "it doesn't flicker warm-cool on every hit. With the shared beat clock and Lock to tempo (Rhythm "
            "box) it's counted from the bar start, so the change lands on the strong beats: every 2 beats = "
            "beats 1 and 3, every bar = the downbeat. The choices follow Beats per bar in the Rhythm box.",
        )
        self.bsw_temp_every_combo.currentIndexChanged.connect(self._on_beat_white_changed)
        bsw_every_row.addWidget(self.bsw_temp_every_combo)
        bsw_every_row.addStretch(1)
        bsw_layout.addLayout(bsw_every_row)
        self.bsw_temp_every_note = inactive_note()
        bsw_layout.addWidget(self.bsw_temp_every_note)

        self.bsw_temp_min_slider = FloatSlider("Temp range min (warm)", 0.0, 1.0, bsw.temp_min, decimals=2)
        self.bsw_temp_max_slider = FloatSlider("Temp range max (cool)", 0.0, 1.0, bsw.temp_max, decimals=2)
        self.bsw_min_jump_slider = FloatSlider("Min temp jump (for 'random')", 0.0, 1.0, bsw.min_temp_jump, decimals=2)
        self.bsw_flash_slider = FloatSlider("Flash brightness", 0.0, 1.0, bsw.flash_brightness)
        self.bsw_sustain_slider = FloatSlider("Sustain brightness", 0.0, 1.0, bsw.sustain_brightness)
        self.bsw_temp_attack_slider = FloatSlider("Temp snap speed", 5.0, 500.0, bsw.temp_attack_ms, decimals=0, suffix=" ms")
        self.bsw_bright_attack_slider = FloatSlider("Brightness attack", 1.0, 200.0, bsw.brightness_attack_ms, decimals=0, suffix=" ms")
        self.bsw_bright_release_slider = FloatSlider("Brightness decay", 50.0, 2000.0, bsw.brightness_release_ms, decimals=0, suffix=" ms")
        for w in (
            self.bsw_temp_min_slider,
            self.bsw_temp_max_slider,
            self.bsw_min_jump_slider,
            self.bsw_flash_slider,
            self.bsw_sustain_slider,
            self.bsw_temp_attack_slider,
            self.bsw_bright_attack_slider,
            self.bsw_bright_release_slider,
        ):
            w.valueChanged.connect(self._on_beat_white_changed)
            bsw_layout.addWidget(w)

        bsw_dark_note = QLabel(
            "Dark pulses on the beat: a rhythm-synced pause toward black before flashing, on a random share of "
            "beats. While the Pulse sequencer (below) runs, it places the dark pulses instead - shaped by the "
            "Dark pulses section below, as in every mode."
        )
        bsw_dark_note.setWordWrap(True)
        bsw_layout.addWidget(bsw_dark_note)
        self.bsw_dark_prob_slider = FloatSlider("Dark pulse probability", 0.0, 1.0, bsw.dark_pulse_probability, decimals=2)
        self.bsw_dark_duration_slider = FloatSlider(
            "Dark pulse duration", 10.0, 500.0, bsw.dark_pulse_duration_ms, decimals=0, suffix=" ms"
        )
        self.bsw_dark_depth_slider = FloatSlider("Dark pulse depth", 0.0, 1.0, bsw.dark_pulse_depth, decimals=2)
        for w in (self.bsw_dark_prob_slider, self.bsw_dark_duration_slider, self.bsw_dark_depth_slider):
            w.valueChanged.connect(self._on_beat_white_changed)
            bsw_layout.addWidget(w)
        self.bsw_dark_seq_note = inactive_note()
        bsw_layout.addWidget(self.bsw_dark_seq_note)

        self.bsw_low_spin.valueChanged.connect(self._on_beat_white_changed)
        self.bsw_high_spin.valueChanged.connect(self._on_beat_white_changed)

        sub_tabs.insertTab(1, bsw_widget, "Beat Sync White")

        # -- Peak Flash mode ------------------------------------------------------------
        peak_widget = QWidget()
        peak_layout = QVBoxLayout(peak_widget)
        pf = cm.peak_flash

        peak_desc_label = QLabel(
            "Reacts to ANY sudden loudness spike (broadband, not just bass), flashes toward "
            "white on strong treble/cymbals, and shows fully-saturated color the rest of the "
            "time. Hue flows continuously and slowly instead of snapping - a smooth 'storytelling' "
            "color arc rather than discrete jumps."
        )
        peak_desc_label.setWordWrap(True)
        peak_layout.addWidget(peak_desc_label)

        peak_detect_row = QHBoxLayout()
        peak_detect_row.addWidget(QLabel("Peak detection band:"))
        self.peak_low_spin = QSpinBox()
        self.peak_low_spin.setRange(20, 20000)
        self.peak_low_spin.setSuffix(" Hz")
        self.peak_low_spin.setValue(int(pf.detect_low_hz))
        peak_detect_row.addWidget(self.peak_low_spin)
        peak_detect_row.addWidget(QLabel("-"))
        self.peak_high_spin = QSpinBox()
        self.peak_high_spin.setRange(20, 20000)
        self.peak_high_spin.setSuffix(" Hz")
        self.peak_high_spin.setValue(int(pf.detect_high_hz))
        peak_detect_row.addWidget(self.peak_high_spin)
        peak_detect_row.addStretch(1)
        peak_layout.addLayout(peak_detect_row)

        self.peak_sensitivity_slider = FloatSlider(
            "Peak sensitivity", 1.05, 4.0, pf.sensitivity, decimals=2,
            tooltip="Peak Flash's own peak detector (it doesn't use the shared beat clock): higher = only "
            "the strongest peaks flash.",
        )
        self.peak_min_interval_slider = FloatSlider("Min interval", 20.0, 1000.0, pf.min_interval_ms, decimals=0, suffix=" ms")
        self.peak_min_energy_slider = FloatSlider("Min energy floor", 0.0, 1.0, pf.min_energy)
        for w in (self.peak_sensitivity_slider, self.peak_min_interval_slider, self.peak_min_energy_slider):
            w.valueChanged.connect(self._on_peak_changed)
            peak_layout.addWidget(w)

        treble_row = QHBoxLayout()
        treble_row.addWidget(QLabel("Treble/white band:"))
        self.peak_treble_low_spin = QSpinBox()
        self.peak_treble_low_spin.setRange(20, 20000)
        self.peak_treble_low_spin.setSuffix(" Hz")
        self.peak_treble_low_spin.setValue(int(pf.treble_low_hz))
        treble_row.addWidget(self.peak_treble_low_spin)
        treble_row.addWidget(QLabel("-"))
        self.peak_treble_high_spin = QSpinBox()
        self.peak_treble_high_spin.setRange(20, 20000)
        self.peak_treble_high_spin.setSuffix(" Hz")
        self.peak_treble_high_spin.setValue(int(pf.treble_high_hz))
        treble_row.addWidget(self.peak_treble_high_spin)
        treble_row.addStretch(1)
        peak_layout.addLayout(treble_row)

        self.peak_white_amount_slider = FloatSlider("Whiteness amount", 0.0, 3.0, pf.treble_white_amount)
        self.peak_white_attack_slider = FloatSlider("White attack", 2.0, 300.0, pf.white_attack_ms, decimals=0, suffix=" ms")
        self.peak_white_release_slider = FloatSlider("White release", 20.0, 1500.0, pf.white_release_ms, decimals=0, suffix=" ms")
        for w in (self.peak_white_amount_slider, self.peak_white_attack_slider, self.peak_white_release_slider):
            w.valueChanged.connect(self._on_peak_changed)
            peak_layout.addWidget(w)

        hue_source_row = QHBoxLayout()
        hue_source_row.addWidget(QLabel("Hue source:"))
        self.peak_hue_source_combo = QComboBox()
        self.peak_hue_source_combo.addItems(["drift", "centroid"])
        self.peak_hue_source_combo.setCurrentText(pf.hue_source)
        self.peak_hue_source_combo.currentTextChanged.connect(self._on_peak_changed)
        hue_source_row.addWidget(self.peak_hue_source_combo)
        hue_source_row.addStretch(1)
        peak_layout.addLayout(hue_source_row)

        self.peak_hue_flow_slider = FloatSlider(
            "Color richness (hue flow)", 200.0, 15000.0, pf.hue_flow_ms, decimals=0, suffix=" ms"
        )
        self.peak_drift_speed_slider = FloatSlider("Drift speed", 0.0, 60.0, pf.drift_speed_deg_per_s, decimals=1, suffix=" deg/s")
        self.peak_randomness_slider = FloatSlider("Randomness factor", 0.0, 1.0, pf.randomness, decimals=2)
        self.peak_random_range_slider = FloatSlider(
            "Random jump range", 0.0, 360.0, pf.random_jump_range_deg, decimals=0, suffix=" deg"
        )
        self.peak_saturation_slider = FloatSlider("Saturation", 0.0, 1.0, pf.saturation)
        self.peak_baseline_min_slider = FloatSlider("Baseline brightness min", 0.0, 1.0, pf.baseline_min_brightness)
        self.peak_baseline_max_slider = FloatSlider("Baseline brightness max", 0.0, 1.0, pf.baseline_max_brightness)
        self.peak_flash_brightness_slider = FloatSlider("Flash brightness", 0.0, 1.0, pf.flash_brightness)
        self.peak_flash_attack_slider = FloatSlider("Flash attack", 1.0, 200.0, pf.flash_attack_ms, decimals=0, suffix=" ms")
        self.peak_flash_release_slider = FloatSlider("Flash decay", 30.0, 2000.0, pf.flash_release_ms, decimals=0, suffix=" ms")
        self.peak_loudness_smoothing_slider = FloatSlider(
            "Loudness tracking speed", 20.0, 3000.0, pf.loudness_smoothing_ms, decimals=0, suffix=" ms"
        )
        for w in (
            self.peak_hue_flow_slider,
            self.peak_drift_speed_slider,
            self.peak_randomness_slider,
            self.peak_random_range_slider,
            self.peak_saturation_slider,
            self.peak_baseline_min_slider,
            self.peak_baseline_max_slider,
            self.peak_flash_brightness_slider,
            self.peak_flash_attack_slider,
            self.peak_flash_release_slider,
            self.peak_loudness_smoothing_slider,
        ):
            w.valueChanged.connect(self._on_peak_changed)
            peak_layout.addWidget(w)
            if w is self.peak_random_range_slider:
                peak_randomness_note = QLabel(
                    "Randomness factor: on each detected peak, this is the chance the hue takes a "
                    "random jump (synced to the music) instead of just flowing smoothly - 0 = never "
                    "jumps, 1 = jumps on every peak. The jump still eases in via 'Color richness' above, "
                    "and persists (the story continues from the new hue)."
                )
                peak_randomness_note.setWordWrap(True)
                peak_layout.addWidget(peak_randomness_note)

        for spin in (self.peak_low_spin, self.peak_high_spin, self.peak_treble_low_spin, self.peak_treble_high_spin):
            spin.valueChanged.connect(self._on_peak_changed)

        sub_tabs.insertTab(2, peak_widget, "Peak Flash")
        sub_tabs.setCurrentIndex(0)
        self._mode_pages = {
            "beat_sync": beat_widget,
            "beat_sync_white": bsw_widget,
            "peak_flash": peak_widget,
            "rgb_freq": self.rgb_editor,
            "hsv_music": hsv_widget,
            "custom": self.custom_editor,
        }
        self._link_quick_settings()
        # A tab widget makes every page as tall as the tallest one (Beat Sync's):
        # without a stretch at the end, a shorter page spreads its rows over
        # that whole height, with big gaps between them.
        for index in range(sub_tabs.count()):
            page_layout = sub_tabs.widget(index).layout()
            if isinstance(page_layout, QBoxLayout):
                page_layout.addStretch(1)
        # The shared pulse sections follow the page shown (they start on Beat Sync's).
        self._page_modes = {page: mode for mode, page in self._mode_pages.items()}
        sub_tabs.currentChanged.connect(lambda index: self._place_pulse_box(sub_tabs.widget(index)))

        # -- global ---------------------------------------------------------------------
        global_box = CollapsibleSection(
            "Global", key="color/global",
            tooltip="Settings that apply in every colour mode: the response curve, how often and how the "
            "lamps are sent their colours, and inverting the brightness.",
        )
        global_layout = global_box.content_layout()
        curve_row = QHBoxLayout()
        curve_row.addWidget(QLabel("Response curve:"))
        self.curve_combo = QComboBox()
        self.curve_combo.addItems(["linear", "log", "exp2"])
        self.curve_combo.setCurrentText(cm.response_curve)
        self.curve_combo.setToolTip(
            "Reshapes the raw 0..1 audio level BEFORE it becomes brightness/hue, in RGB Frequency, "
            "Custom and HSV Music modes only (Beat Sync, Peak Flash, and Beat Sync "
            "White don't use this at all - they react to individual detected hits instead of a "
            "continuous level). linear: unchanged. log: boosts quiet detail, so quieter passages still "
            "show visible variation instead of looking flat/dark. exp2: the opposite - suppresses quiet "
            "background noise and emphasizes strong peaks, for a punchier, more contrasty look."
        )
        self.curve_combo.currentTextChanged.connect(self._on_curve_changed)
        curve_row.addWidget(self.curve_combo)
        curve_row.addStretch(1)
        global_layout.addLayout(curve_row)

        self.threshold_slider = FloatSlider(
            "Min change threshold", 0.0, 0.2, cm.smoothing.min_change_threshold, decimals=3,
            tooltip="A NETWORK optimization, not an audio one - applies to every mode, including Beat "
            "Sync. After a color is already computed, if it's barely different from the last color "
            "actually SENT to that lamp (within this much per R/G/B channel, 0..1), the send is simply "
            "skipped to cut needless Wi-Fi/Tuya traffic - it never affects what gets computed, only "
            "whether a near-duplicate is worth transmitting. This is unrelated to 'Min energy floor' in "
            "Beat Sync/Peak Flash, which instead gates whether a quiet moment is allowed to count as a "
            "beat at all, on the audio side, before any color is even computed.",
        )
        self.threshold_slider.valueChanged.connect(self._on_threshold_changed)
        global_layout.addWidget(self.threshold_slider)
        threshold_note = QLabel("Lamp updates smaller than this (0..1 per channel) are skipped to reduce network traffic.")
        threshold_note.setWordWrap(True)
        global_layout.addWidget(threshold_note)

        self.command_rate_slider = FloatSlider(
            "Lamp command rate", 2.0, 60.0, controller.config.network.lamp_command_rate_hz, decimals=0, suffix=" /s",
            tooltip="The most commands each lamp is sent per second (a newer color replaces a waiting one, "
            "so nothing queues up). With instant Lamp transitions every command shows as a step, so "
            "higher = smoother fades and glides; the color engine computes at least this often. Lower = "
            "gentler on the bulbs and the Wi-Fi - worth trying if lamps get stuck (answering but not "
            "following the show, see README).",
        )
        self.command_rate_slider.valueChanged.connect(self._on_command_rate_changed)
        global_layout.addWidget(self.command_rate_slider)

        self.transitions_combo = self._labeled_combo(
            global_layout, "Lamp transitions:", LAMP_TRANSITIONS, controller.config.network.lamp_transitions,
            "How colors and white flashes reach the bulbs. direct (default): the bulb's real-time control "
            "channel - every change lands instantly (measured: under 33 ms), and a white flash lights the "
            "white LEDs with one command, without switching the bulb's mode. smooth (experimental): like "
            "direct for big changes - a beat's flash, a white flash lighting up, the strobe - but the small "
            "steps of a fade, a hue snap or a hue glide are sent with the bulb's own short fade, so the "
            "bulb glides from step to step instead of showing each command as a tiny jump; fades look "
            "smoother and a little slower. gradient: the same channel "
            "with the bulb's own short fade (~0.25 s) on every change. legacy: the old way - the bulb fades every change "
            "over ~0.7 s, so fast beats blur together, and white flashes need 4-5 commands. If your bulbs "
            "stop changing color with direct/smooth/gradient, choose legacy.",
        )
        self.transitions_combo.currentTextChanged.connect(self._on_transitions_changed)

        self.invert_checkbox = QCheckBox("Invert brightness (0 = bright, 1 = black)")
        self.invert_checkbox.setChecked(cm.invert_brightness)
        self.invert_checkbox.toggled.connect(self._on_invert_changed)
        global_layout.addWidget(self.invert_checkbox)
        invert_note = QLabel("Applies to every mode - flips the brightness response so quiet/dark moments light up instead.")
        invert_note.setWordWrap(True)
        global_layout.addWidget(invert_note)

        root.addWidget(global_box)

        # -- presets ------------------------------------------------------------------------
        preset_box = CollapsibleSection(
            "Presets", key="color/presets",
            tooltip="Built-in looks, and your own saved snapshots of every colour setting.",
        )
        preset_layout = preset_box.content_layout()

        builtin_row = QHBoxLayout()
        builtin_row.addWidget(QLabel("Built-in look:"))
        self.builtin_combo = QComboBox()
        self.builtin_combo.addItems(list(BUILTIN_PRESETS))
        self.builtin_combo.currentTextChanged.connect(self._show_builtin_description)
        builtin_row.addWidget(self.builtin_combo, stretch=1)
        apply_builtin_btn = QPushButton("Apply")
        apply_builtin_btn.setToolTip(
            "Sets the shared beat clock, Beat Sync, Chase and Group Switch together (Chase and Group "
            "Switch are on the Chase & Group Switch tab). Leaves your lamp groups, Chase width/intensity, "
            "Group Switch intensity, beat detection band/sensitivity, lead time and true-white "
            "depth/brightness/cool ratio as they are."
        )
        apply_builtin_btn.clicked.connect(self._apply_builtin_preset)
        builtin_row.addWidget(apply_builtin_btn)
        preset_layout.addLayout(builtin_row)
        self.builtin_description = QLabel()
        self.builtin_description.setWordWrap(True)
        preset_layout.addWidget(self.builtin_description)
        self._show_builtin_description(self.builtin_combo.currentText())

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Saved:"))
        self.preset_combo = QComboBox()
        self._reload_presets()
        preset_row.addWidget(self.preset_combo, stretch=1)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_preset)
        preset_row.addWidget(load_btn)
        save_btn = QPushButton("Save As...")
        save_btn.clicked.connect(self._save_preset)
        preset_row.addWidget(save_btn)
        preset_layout.addLayout(preset_row)
        root.addWidget(preset_box)

        root.addStretch(1)
        self._update_beat_states()

    # -- which settings are in use right now ---------------------------------------------

    def _update_beat_states(self) -> None:
        """Grey out the settings the current combination doesn't use, and say
        why - so changing one never looks ignored: Beat Sync's own, the shared
        pulse sections (by the mode page they're on), Beat Sync White's."""
        cfg = self.controller.config
        bs, rh = cfg.color_mapping.beat_sync, cfg.rhythm
        shared = rh.shared_clock

        clock_reason = "Beat Sync follows the shared beat clock (Rhythm box at the top)."
        set_active(
            [self.beat_low_spin, self.beat_high_spin, self.beat_sensitivity_slider,
             self.beat_min_interval_slider, self.beat_min_energy_slider],
            not shared, clock_reason,
        )
        show_note(self.beat_detect_note, (
            "Beat Sync follows the shared beat clock, so its own detection settings here are greyed "
            "out - tune detection in the Rhythm box instead."
        ) if shared else "")

        step_reason = f"Hue mode is '{bs.hue_mode}'."
        music_step = bs.hue_step_source != "fixed"
        set_active([self.beat_hue_step_source_combo], bs.hue_mode == "step", step_reason)
        if bs.hue_mode == "step":
            set_active([self.beat_hue_step_slider], not music_step, f"Hue step comes from '{bs.hue_step_source}'.")
            set_active(
                [self.beat_hue_step_min_slider, self.beat_hue_step_max_slider], music_step,
                "Hue step is 'fixed' (the Hue step slider).",
            )
        else:
            set_active(
                [self.beat_hue_step_slider, self.beat_hue_step_min_slider, self.beat_hue_step_max_slider],
                False, step_reason,
            )
        set_active([self.beat_spectrum_source_combo], bs.hue_mode == "spectrum", f"Hue mode is '{bs.hue_mode}'.")
        set_active(
            [self.beat_sustain_slider], bs.fade_brightness,
            "Fade brightness is off: brightness stays at Flash brightness between beats.",
        )
        set_active([self.beat_hue_glide_slider, self.beat_glide_timing_combo], bs.glide_hue, "Glide hue is off.")
        set_active([self.beat_min_jump_slider], bs.hue_mode == "random", f"Hue mode is '{bs.hue_mode}'.")

        self._update_pulse_states()
        self._update_bsw_states()

    def _sequencer_state(self, mode: str) -> "tuple[bool, bool, str]":
        """The pulse sequencer in `mode` (see engine.rules): (can it run there,
        does it place the pulses there - once the clock locks, why not)."""
        cfg = self.controller.config
        rh = cfg.rhythm
        if mode in BEAT_MODES and not rh.shared_clock:
            return False, False, (
                f"The sequencer needs {_MODE_NAMES.get(mode, mode)} to follow the shared beat clock - turn on "
                "'Beat Sync (and Beat Sync White) follow the shared clock' in the Rhythm box at the top."
            )
        if not rh.tempo_lock:
            return False, False, (
                "The sequencer places the pulses on the beat clock's grid - turn on Lock to tempo in the "
                "Rhythm box at the top."
            )
        if not cfg.sequencer.enabled:
            return True, False, "The sequencer is off."
        return True, True, ""

    def _update_pulse_states(self) -> None:
        """The shared Dark pulses / White pulses / Pulse sequencer sections, for
        the mode page they're on (self._pulse_mode): Beat Sync rolls per-beat
        pulses of its own too, the other modes get only the sequencer's, Beat
        Sync White only its dark ones. What a mode never has is hidden on its
        page (Beat Sync's per-beat probability, trigger and lamps), what it
        doesn't use right now greyed out."""
        cfg = self.controller.config
        bs, rh, sq = cfg.color_mapping.beat_sync, cfg.rhythm, cfg.sequencer
        mode = self._pulse_mode
        name = _MODE_NAMES.get(mode, mode)
        beat_sync = mode == "beat_sync"
        dark_only = sequencer_dark_only(mode)
        shared = rh.shared_clock
        can_run, sequencing, seq_off = self._sequencer_state(mode)
        dark, white = bs.dark_pulse_enabled, bs.white_pulse_enabled
        temp_mode = bs.white_pulse_temp_mode
        legacy = cfg.network.lamp_transitions == "legacy"
        dark_only_reason = f"{name} takes only the dark pulses from the sequencer."

        for widget in (self.beat_dark_trigger_row, self.beat_dark_prob_slider, self.beat_white_target_box,
                       self.beat_white_trigger_row, self.beat_white_pulse_prob_slider):
            widget.setVisible(beat_sync)  # Beat Sync's per-beat pulses - no other mode has them

        dark_look = [self.beat_dark_duration_slider, self.beat_dark_depth_slider, self.beat_dark_attack_slider,
                     self.beat_dark_release_slider]
        white_look = [self.beat_white_pulse_duration_slider, self.beat_white_pulse_attack_slider,
                      self.beat_white_pulse_release_slider, self.beat_white_pulse_white_brightness_slider,
                      self.beat_white_temp_combo, self.beat_white_hybrid_slider]

        if beat_sync:
            seq_reason = "The Pulse sequencer (below) places the pulses."
            no_clock_reason = "Accent/downbeat need the shared beat clock (Rhythm box)."
            set_active([self.beat_dark_enabled_checkbox, self.beat_white_pulse_enabled_checkbox], True, "")
            for enabled, prob, trigger, look, note, kind in (
                (dark, self.beat_dark_prob_slider, self.beat_dark_trigger_combo, dark_look, self.beat_dark_note,
                 "Dark"),
                (white, self.beat_white_pulse_prob_slider, self.beat_white_trigger_combo, white_look,
                 self.beat_white_note, "White"),
            ):
                off_reason = f"{kind} pulses are switched off."
                set_active(look, enabled, off_reason)
                set_active([prob], enabled and not sequencing, off_reason if not enabled else seq_reason)
                set_active(
                    [trigger], enabled and shared and not sequencing,
                    off_reason if not enabled else (seq_reason if sequencing else no_clock_reason),
                )
                if enabled and sequencing:
                    show_note(note, f"{kind} pulses are placed by the Pulse sequencer, so the probability and "
                              "trigger here are greyed out; durations and strength still apply.")
                elif enabled and not shared:
                    show_note(note, "'accent' and 'downbeat' triggers need the shared beat clock (Rhythm box) - "
                              "without it every beat rolls the probability.")
                else:
                    show_note(note, "")
            set_active(
                [self.beat_white_pulse_target_combo], white and not sequencing,
                "White pulses are switched off." if not white else seq_reason + " Its group walk decides the lamps.",
            )
            if white and temp_mode in ("bar", "phrase") and not shared:
                show_note(self.beat_white_temp_note, f"Warm/cool '{temp_mode}' needs the shared beat clock (Rhythm "
                          "box) to know where the bar is - until then each flash is rolled with the cool ratio.")
            else:
                show_note(self.beat_white_temp_note, "")
            white_used = white  # by the per-beat flashes or the sequencer's
            white_off = "" if white else "White pulses are switched off."
            rotating = (bs.white_pulse_target == "rotate") if not sequencing else (sq.group_walk != "all")
        else:
            # Here every pulse comes from the sequencer.
            if sequencing:
                only_seq = ""
            elif can_run:
                only_seq = f"In {name} only the Pulse sequencer (below) places these - it's off."
            else:
                only_seq = f"In {name} only the Pulse sequencer places these. {seq_off}"
            dark_note = only_seq
            if dark_only and not sequencing:
                dark_note = (
                    "In Beat Sync White these shape only the Pulse sequencer's dark pulses (its own per-beat "
                    "ones are set above). " + seq_off
                )
            set_active([self.beat_dark_enabled_checkbox], sequencing, dark_note)
            set_active(dark_look, sequencing and dark, dark_note or "Dark pulses are switched off.")
            show_note(self.beat_dark_note, dark_note)
            if dark_only:
                white_off = "Not used in Beat Sync White - it's white already, and takes only the sequencer's dark pulses."
                set_active([self.beat_white_pulse_enabled_checkbox] + white_look, False, white_off)
                show_note(self.beat_white_note, "Not used in Beat Sync White (it's white already).")
                white_used = False
            else:
                white_off = only_seq or ("" if white else "White pulses are switched off.")
                set_active([self.beat_white_pulse_enabled_checkbox], sequencing, only_seq)
                set_active(white_look, sequencing and white, white_off)
                show_note(self.beat_white_note, only_seq)
                white_used = sequencing and white
            show_note(self.beat_white_temp_note, "")  # the sequencer's flashes always know their place in the bar
            rotating = sq.group_walk != "all"

        # The release curve shapes only the sequencer's flashes (the per-beat ones fade out exponentially).
        set_active(
            [self.beat_white_release_curve_combo], white_used and sequencing and not legacy,
            white_off or (
                "The release curve shapes the Pulse sequencer's flashes (the per-beat ones fade out "
                "exponentially). " + seq_off if not sequencing else
                "With 'legacy' Lamp transitions the bulb fades on its own."
            ),
        )
        set_active(
            [self.beat_white_pulse_cool_ratio_slider], white_used and temp_mode == "random",
            white_off or f"Warm/cool is '{temp_mode}', not random.",
        )
        self._show_rotators_warning(white_used and rotating, sequencing)
        set_active(
            [self.beat_white_rotators_combo], white_used and rotating,
            white_off or (
                "The sequencer's Group walk is 'all' - every lamp flashes." if sequencing else
                "True white lamps isn't 'rotate'."
            ),
        )

        # -- the sequencer --------------------------------------------------------------
        set_active([self.seq_enabled_checkbox], can_run, seq_off)
        if not can_run:
            show_note(self.seq_note, seq_off)
        elif dark_only and sq.enabled:
            show_note(self.seq_note, "Beat Sync White takes only its dark pulses.")
        else:
            show_note(self.seq_note, "")
        white_controls = [
            self.seq_white_combo, self.seq_walk_combo, self.seq_walk_positions_combo, self.seq_white_density_slider,
            self.seq_double_slider, self.seq_focus_slider, self.seq_build_slider, self.seq_repeat_checkbox,
            self.seq_gap_slider, self.seq_dynamics_checkbox,
        ]
        # The phrase, fills, accents and drops place dark pulses too.
        dark_controls = [
            self.seq_dark_combo, self.seq_dark_density_slider, self.seq_dark_length_slider, self.seq_phrase_combo,
            self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox,
        ]
        set_active(white_controls, sequencing and not dark_only, seq_off or dark_only_reason)
        set_active(dark_controls, sequencing, seq_off)
        set_active(
            [self.seq_dynamics_slider], sequencing and not dark_only and sq.pulse_dynamics,
            seq_off or (dark_only_reason if dark_only else "Pulse dynamics is off."),
        )
        if beat_sync and shared and white and temp_mode == "phrase":
            set_active([self.seq_phrase_combo], True, "")  # the per-beat flashes' warm/cool 'phrase' uses it too

        # Strobe: placed by the sequencer, and only with instant lamp transitions.
        direct = cfg.network.lamp_transitions in INSTANT_TRANSITIONS
        not_direct = "The strobe needs Lamp transitions 'direct' or 'smooth' (Global box)."
        extras = sequencing and not dark_only  # the strobes and the sweep: white light and RGB colour
        extras_off = seq_off or (dark_only_reason if dark_only else "")
        set_active([self.seq_strobe_checkbox], extras and direct, extras_off or not_direct)
        set_active(
            [self.seq_strobe_placement_combo, self.seq_strobe_level_combo, self.seq_strobe_chance_slider,
             self.seq_strobe_gap_slider, self.seq_strobe_length_combo, self.seq_strobe_wave_combo,
             self.seq_strobe_brightness_slider, self.seq_strobe_test_button],
            extras and direct and sq.strobe_enabled,
            extras_off or (not_direct if not direct else "The strobe is off."),
        )
        # The rate and on-time are the flash strobe's too.
        set_active(
            [self.seq_strobe_rate_slider, self.seq_strobe_duty_slider],
            extras and direct and (sq.strobe_enabled or sq.flash_strobe_enabled),
            extras_off or (not_direct if not direct else "The strobe and the flash strobe are off."),
        )
        set_active(
            [self.seq_flash_strobe_checkbox], extras and direct and white,
            extras_off or (not_direct if not direct else "White pulses are off."),
        )
        set_active(
            [self.seq_flash_strobe_placement_combo, self.seq_flash_strobe_level_combo,
             self.seq_flash_strobe_chance_slider, self.seq_flash_strobe_gap_slider,
             self.seq_flash_strobe_brightness_slider],
            extras and direct and white and sq.flash_strobe_enabled,
            extras_off or (not_direct if not direct else "White pulses are off." if not white else "Flash strobe is off."),
        )
        # Color sweep: on the RGB colour, so any lamp transitions do.
        set_active([self.seq_sweep_checkbox], extras, extras_off)
        set_active(
            [self.seq_sweep_color_combo, self.seq_sweep_saturation_slider, self.seq_sweep_brightness_slider,
             self.seq_sweep_length_combo, self.seq_sweep_rotators_spin, self.seq_sweep_width_spin,
             self.seq_sweep_direction_combo, self.seq_sweep_placement_combo, self.seq_sweep_level_combo,
             self.seq_sweep_chance_slider, self.seq_sweep_gap_slider, self.seq_sweep_test_button],
            extras and sq.color_sweep_enabled,
            extras_off or "Color sweep is off.",
        )
        set_active(
            [self.seq_sweep_hue_slider], extras and sq.color_sweep_enabled and sq.color_sweep_color == "custom",
            extras_off or ("Color sweep is off." if not sq.color_sweep_enabled else "Sweep colour is 'dynamic'."),
        )
        if extras and not direct:
            show_note(self.seq_strobe_note, "The strobe needs Lamp transitions 'direct' or 'smooth' (Global box): only then "
                      "do the bulbs take a change instantly and show white and colour at the same time.")
        else:
            show_note(self.seq_strobe_note, "")

    def _update_bsw_states(self) -> None:
        """Beat Sync White's own settings: its detector while it doesn't follow
        the clock, its per-beat dark pulses while the sequencer doesn't place
        them, and what 'Change temperature' is counted from."""
        cfg = self.controller.config
        rh, bsw = cfg.rhythm, cfg.color_mapping.beat_sync_white
        set_active(
            [self.bsw_low_spin, self.bsw_high_spin, self.bsw_sensitivity_slider, self.bsw_min_interval_slider,
             self.bsw_min_energy_slider],
            not rh.shared_clock, "Beat Sync White follows the shared beat clock (Rhythm box at the top).",
        )
        _, sequencing, _ = self._sequencer_state("beat_sync_white")
        set_active(
            [self.bsw_dark_prob_slider, self.bsw_dark_duration_slider, self.bsw_dark_depth_slider], not sequencing,
            "The Pulse sequencer (below) places Beat Sync White's dark pulses - shaped by the Dark pulses section.",
        )
        show_note(self.bsw_dark_seq_note, (
            "The Pulse sequencer places the dark pulses here (shaped by the Dark pulses section below), so these "
            "per-beat ones are greyed out - they apply again while the sequencer is off or the beat clock hasn't "
            "locked yet."
        ) if sequencing else "")
        if bsw.temp_every_n_beats > 1 and not (rh.shared_clock and rh.tempo_lock):
            show_note(self.bsw_temp_every_note, (
                "Counted from the bar start only with the shared beat clock and Lock to tempo (Rhythm box) - "
                "now it counts the detected beats, so the changes can drift off the strong beats."
            ))
        else:
            show_note(self.bsw_temp_every_note, "")

    def _add_smoothing_sliders(self, layout) -> "tuple[FloatSlider, FloatSlider]":
        """Attack/Release of the level smoothing - one setting shared by RGB
        Frequency, Custom and HSV Music, so each of their pages has a copy
        (kept in step through _link_quick_settings())."""
        sliders = []
        for setting in (s for s in QUICK_SETTINGS["rgb_freq"] if ".smoothing." in s.path):
            slider = FloatSlider(
                setting.label, setting.minimum, setting.maximum, get_value(self.controller.config, setting.path),
                decimals=setting.decimals, suffix=setting.suffix, tooltip=setting.tooltip,
            )
            slider.valueChanged.connect(lambda v, name=setting.path.split(".")[-1]: self._on_smoothing_changed(name, v))
            layout.addWidget(slider)
            sliders.append(slider)
        return sliders[0], sliders[1]

    def _link_quick_settings(self) -> None:
        """The sliders the Visualizer tab's Quick Controls also show: each
        follows the other copies of its setting (see ui/quick_settings.py)."""
        links = {
            "rhythm.sensitivity": [self.rhythm_sensitivity_slider],
            "color_mapping.beat_sync.sensitivity": [self.beat_sensitivity_slider],
            "color_mapping.beat_sync.sustain_brightness": [self.beat_sustain_slider],
            "color_mapping.beat_sync.brightness_release_ms": [self.beat_bright_release_slider],
            "color_mapping.beat_sync.hue_attack_ms": [self.beat_hue_attack_slider],
            "color_mapping.beat_sync_white.sensitivity": [self.bsw_sensitivity_slider],
            "color_mapping.beat_sync_white.sustain_brightness": [self.bsw_sustain_slider],
            "color_mapping.beat_sync_white.brightness_release_ms": [self.bsw_bright_release_slider],
            "color_mapping.beat_sync_white.temp_attack_ms": [self.bsw_temp_attack_slider],
            "color_mapping.peak_flash.sensitivity": [self.peak_sensitivity_slider],
            "color_mapping.peak_flash.flash_release_ms": [self.peak_flash_release_slider],
            "color_mapping.peak_flash.treble_white_amount": [self.peak_white_amount_slider],
            "color_mapping.peak_flash.hue_flow_ms": [self.peak_hue_flow_slider],
            "color_mapping.peak_flash.baseline_min_brightness": [self.peak_baseline_min_slider],
            "color_mapping.rgb.sensitivity": [self.rgb_editor.sensitivity_slider],
            "color_mapping.custom.sensitivity": [self.custom_editor.sensitivity_slider],
            "color_mapping.hsv.sensitivity": [self.hsv_sensitivity_slider],
            "color_mapping.smoothing.attack_ms": [self.rgb_attack_slider, self.custom_attack_slider, self.hsv_attack_slider],
            "color_mapping.smoothing.release_ms": [self.rgb_release_slider, self.custom_release_slider, self.hsv_release_slider],
        }
        for path, sliders in links.items():
            for slider in sliders:
                link_slider(self.controller, path, slider)

    def show_mode(self, mode: str) -> None:
        """Brings up `mode`'s page (the Quick Controls' "more settings" link)."""
        page = self._mode_pages.get(mode)
        if page is not None:
            self.sub_tabs.setCurrentWidget(page)

    def _place_pulse_box(self, page) -> None:
        """Moves the shared Dark pulses / White pulses / Pulse sequencer box to
        the bottom of mode page `page` (above its closing stretch) - one set of
        widgets for every mode, so the settings are the same everywhere - and
        sets what that mode uses in it."""
        mode = self._page_modes.get(page)
        if mode is None:
            return
        if self.pulse_box.parentWidget() is not page:
            old = self.pulse_box.parentWidget()
            if old is not None and old.layout() is not None:
                old.layout().removeWidget(self.pulse_box)
            layout = page.layout()
            layout.insertWidget(max(0, layout.count() - 1), self.pulse_box)
            self.pulse_box.show()
        self._pulse_mode = mode
        self._update_beat_states()

    # -- handlers -----------------------------------------------------------------------

    def _on_smoothing_changed(self, name: str, value: float) -> None:
        setattr(self.controller.config.color_mapping.smoothing, name, value)
        self.controller.apply_config_changes()

    def _on_rgb_changed(self, cfg: RGBModeConfig) -> None:
        self.controller.config.color_mapping.rgb = cfg
        self.controller.apply_config_changes()

    def _on_custom_changed(self, cfg: RGBModeConfig) -> None:
        self.controller.config.color_mapping.custom = cfg
        self.controller.apply_config_changes()

    def _on_hsv_changed(self, *_args) -> None:
        hsv = self.controller.config.color_mapping.hsv
        hsv.hue_min_deg = self.hue_min_slider.value()
        hsv.hue_max_deg = self.hue_max_slider.value()
        hsv.brightness_min = self.bright_min_slider.value()
        hsv.brightness_max = self.bright_max_slider.value()
        hsv.saturation_base = self.sat_base_slider.value()
        hsv.saturation_contrast_gain = self.sat_contrast_slider.value()
        hsv.sensitivity = self.hsv_sensitivity_slider.value()
        self.controller.apply_config_changes()

    def _on_beat_changed(self, *_args) -> None:
        bs = self.controller.config.color_mapping.beat_sync
        bs.detect_low_hz = self.beat_low_spin.value()
        bs.detect_high_hz = self.beat_high_spin.value()
        bs.sensitivity = self.beat_sensitivity_slider.value()
        bs.min_interval_ms = self.beat_min_interval_slider.value()
        bs.min_energy = self.beat_min_energy_slider.value()
        bs.hue_mode = self.beat_hue_mode_combo.currentText()
        bs.spectrum_source = self.beat_spectrum_source_combo.currentText()
        bs.hue_step_deg = self.beat_hue_step_slider.value()
        bs.hue_step_source = self.beat_hue_step_source_combo.currentText()
        bs.hue_step_min_deg = self.beat_hue_step_min_slider.value()
        bs.hue_step_max_deg = self.beat_hue_step_max_slider.value()
        bs.hue_every_n_beats = self.beat_hue_every_combo.currentData()
        bs.min_hue_jump_deg = self.beat_min_jump_slider.value()
        bs.saturation = self.beat_saturation_slider.value()
        bs.flash_brightness = self.beat_flash_slider.value()
        bs.sustain_brightness = self.beat_sustain_slider.value()
        bs.hue_attack_ms = self.beat_hue_attack_slider.value()
        bs.brightness_attack_ms = self.beat_bright_attack_slider.value()
        bs.brightness_release_ms = self.beat_bright_release_slider.value()
        bs.fade_brightness = self.beat_fade_checkbox.isChecked()
        bs.glide_hue = self.beat_glide_checkbox.isChecked()
        bs.hue_glide_timing = self.beat_glide_timing_combo.currentText()
        bs.hue_glide_deg = self.beat_hue_glide_slider.value()
        bs.dark_pulse_enabled = self.beat_dark_enabled_checkbox.isChecked()
        bs.dark_pulse_probability = self.beat_dark_prob_slider.value()
        bs.dark_pulse_trigger = self.beat_dark_trigger_combo.currentText()
        bs.dark_pulse_duration_ms = self.beat_dark_duration_slider.value()
        bs.dark_pulse_depth = self.beat_dark_depth_slider.value()
        bs.dark_pulse_attack_ms = self.beat_dark_attack_slider.value()
        bs.dark_pulse_release_ms = self.beat_dark_release_slider.value()
        bs.white_pulse_enabled = self.beat_white_pulse_enabled_checkbox.isChecked()
        bs.white_pulse_probability = self.beat_white_pulse_prob_slider.value()
        bs.white_pulse_trigger = self.beat_white_trigger_combo.currentText()
        bs.white_pulse_duration_ms = self.beat_white_pulse_duration_slider.value()
        bs.white_pulse_attack_ms = self.beat_white_pulse_attack_slider.value()
        bs.white_pulse_release_ms = self.beat_white_pulse_release_slider.value()
        bs.white_pulse_release_curve = self.beat_white_release_curve_combo.currentText()
        bs.white_pulse_target = self.beat_white_pulse_target_combo.currentText()
        bs.white_pulse_rotators = self.beat_white_rotators_combo.currentData()
        bs.white_pulse_white_brightness = self.beat_white_pulse_white_brightness_slider.value()
        bs.white_pulse_cool_ratio = self.beat_white_pulse_cool_ratio_slider.value()
        bs.white_pulse_temp_mode = self.beat_white_temp_combo.currentText()
        bs.white_pulse_hybrid_chance = self.beat_white_hybrid_slider.value()
        self._update_beat_states()
        self.controller.apply_config_changes()

    def _on_beat_white_changed(self, *_args) -> None:
        bsw = self.controller.config.color_mapping.beat_sync_white
        bsw.detect_low_hz = self.bsw_low_spin.value()
        bsw.detect_high_hz = self.bsw_high_spin.value()
        bsw.sensitivity = self.bsw_sensitivity_slider.value()
        bsw.min_interval_ms = self.bsw_min_interval_slider.value()
        bsw.min_energy = self.bsw_min_energy_slider.value()
        bsw.temp_mode = self.bsw_temp_mode_combo.currentText()
        bsw.temp_min = self.bsw_temp_min_slider.value()
        bsw.temp_max = self.bsw_temp_max_slider.value()
        bsw.min_temp_jump = self.bsw_min_jump_slider.value()
        bsw.temp_every_n_beats = self.bsw_temp_every_combo.currentData()
        bsw.flash_brightness = self.bsw_flash_slider.value()
        bsw.sustain_brightness = self.bsw_sustain_slider.value()
        bsw.temp_attack_ms = self.bsw_temp_attack_slider.value()
        bsw.brightness_attack_ms = self.bsw_bright_attack_slider.value()
        bsw.brightness_release_ms = self.bsw_bright_release_slider.value()
        bsw.dark_pulse_probability = self.bsw_dark_prob_slider.value()
        bsw.dark_pulse_duration_ms = self.bsw_dark_duration_slider.value()
        bsw.dark_pulse_depth = self.bsw_dark_depth_slider.value()
        self._update_bsw_states()
        self.controller.apply_config_changes()

    def _on_peak_changed(self, *_args) -> None:
        pf = self.controller.config.color_mapping.peak_flash
        pf.detect_low_hz = self.peak_low_spin.value()
        pf.detect_high_hz = self.peak_high_spin.value()
        pf.sensitivity = self.peak_sensitivity_slider.value()
        pf.min_interval_ms = self.peak_min_interval_slider.value()
        pf.min_energy = self.peak_min_energy_slider.value()
        pf.treble_low_hz = self.peak_treble_low_spin.value()
        pf.treble_high_hz = self.peak_treble_high_spin.value()
        pf.treble_white_amount = self.peak_white_amount_slider.value()
        pf.white_attack_ms = self.peak_white_attack_slider.value()
        pf.white_release_ms = self.peak_white_release_slider.value()
        pf.hue_source = self.peak_hue_source_combo.currentText()
        pf.hue_flow_ms = self.peak_hue_flow_slider.value()
        pf.drift_speed_deg_per_s = self.peak_drift_speed_slider.value()
        pf.randomness = self.peak_randomness_slider.value()
        pf.random_jump_range_deg = self.peak_random_range_slider.value()
        pf.saturation = self.peak_saturation_slider.value()
        pf.baseline_min_brightness = self.peak_baseline_min_slider.value()
        pf.baseline_max_brightness = self.peak_baseline_max_slider.value()
        pf.flash_brightness = self.peak_flash_brightness_slider.value()
        pf.flash_attack_ms = self.peak_flash_attack_slider.value()
        pf.flash_release_ms = self.peak_flash_release_slider.value()
        pf.loudness_smoothing_ms = self.peak_loudness_smoothing_slider.value()
        self.controller.apply_config_changes()

    def _on_curve_changed(self, text: str) -> None:
        self.controller.config.color_mapping.response_curve = text
        self.controller.apply_config_changes()

    def _on_invert_changed(self, checked: bool) -> None:
        self.controller.config.color_mapping.invert_brightness = checked
        self.controller.apply_config_changes()

    def _show_rotators_warning(self, rotating: bool, sequencing: bool) -> None:
        """Warns when 'Rotating' covers every position the white walks
        through - then every flash lands on every lamp and nothing walks."""
        cfg = self.controller.config
        selected = self.controller.lamp_manager.selected_device_ids()
        if sequencing and cfg.sequencer.walk_positions != "chase_order":
            positions, kind = get_group_switch_groups(cfg.per_lamp_effects, selected), "Group Switch groups"
        else:
            positions, kind = get_chase_groups(cfg.per_lamp_effects, selected), "Chase positions"
        count = len(positions)
        if rotating and count >= 2 and cfg.color_mapping.beat_sync.white_pulse_rotators >= count:
            show_note(self.beat_white_rotators_note, f"Rotating {cfg.color_mapping.beat_sync.white_pulse_rotators} "
                      f"with {count} {kind}: every flash lands on all of them at once, so the white doesn't "
                      f"walk. Choose fewer than {count}.")
        else:
            show_note(self.beat_white_rotators_note, "")

    def _on_transitions_changed(self, text: str) -> None:
        self.controller.config.network.lamp_transitions = text
        self._update_beat_states()  # the strobe is only available with 'direct'
        self.controller.apply_config_changes()

    def _on_command_rate_changed(self, value: float) -> None:
        self.controller.config.network.lamp_command_rate_hz = round(value)
        self.controller.apply_config_changes()

    def _on_threshold_changed(self, value: float) -> None:
        self.controller.config.color_mapping.smoothing.min_change_threshold = value
        self.controller.apply_config_changes()

    # -- presets ---------------------------------------------------------------------------

    def _reload_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItems(list(self.controller.config.presets.keys()))

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if not ok or not name.strip():
            return
        cfg = self.controller.config
        cfg.presets[name.strip()] = snapshot_preset(cfg)
        self._reload_presets()
        self.controller.save_config()

    def _load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        data = self.controller.config.presets.get(name)
        if not data:
            QMessageBox.warning(self, "Not found", f"Preset '{name}' not found.")
            return

        apply_preset(self.controller.config, data)
        self.controller.replace_config_settings()

    def _show_builtin_description(self, name: str) -> None:
        self.builtin_description.setText(BUILTIN_PRESETS.get(name, {}).get("description", ""))

    def _apply_builtin_preset(self) -> None:
        name = self.builtin_combo.currentText()
        if name:
            apply_builtin_preset(self.controller.config, name)
            self.controller.replace_config_settings()

    # -- shared beat clock -------------------------------------------------------------------

    @staticmethod
    def _trigger_combo(layout: QVBoxLayout, label: str, value: str) -> "tuple[QComboBox, QWidget]":
        """A pulse's trigger choice (Beat Sync's per-beat pulses) - and its row,
        which the other modes' pages hide."""
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(list(PULSE_TRIGGERS))
        combo.setCurrentText(value)
        combo.setToolTip(
            "Which beats may roll this pulse's probability at all. random: every beat. accent: only "
            "the hardest hits. downbeat: only bar starts. accent/downbeat need the shared beat clock "
            "(Rhythm box at the top) - without it they behave like random."
        )
        row.addWidget(combo)
        row.addStretch(1)
        layout.addWidget(row_widget)
        return combo, row_widget

    def _build_rhythm_box(self, rh: RhythmConfig) -> CollapsibleSection:
        box = CollapsibleSection(
            "Rhythm · shared beat clock", key="color/rhythm",
            tooltip="The one beat detector every layer can follow: tempo lock, bars, lead time - and what "
            "to do when there's no beat.",
        )
        layout = box.content_layout()
        intro = QLabel(
            "One beat detector that every layer can follow, so the flash, the color changes, Chase and "
            "Group Switch all move on the same beats instead of each reacting to different hits. It "
            "locks onto the tempo, ignores off-beat hits (hi-hats, vocals), fills in a missed kick, and "
            "counts bars so slower layers can change on bar starts. The Pulse sequencer follows it in every "
            "colour mode; Chase / Group Switch with Speed source 'clock' (Chase & Group Switch tab)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.rhythm_shared_checkbox = QCheckBox("Beat Sync (and Beat Sync White) follow the shared clock")
        self.rhythm_shared_checkbox.setChecked(rh.shared_clock)
        self.rhythm_shared_checkbox.setToolTip(
            "On: Beat Sync and Beat Sync White flash on the clock's beats instead of their own detectors "
            "(whose band/sensitivity settings are then unused), their pulse triggers can use accents/bar "
            "starts, and 'Change hue' / 'Change temperature' count from the bar start. The Pulse sequencer "
            "needs it in these two modes, so their flashes and its pulses go by the same beats."
        )
        self.rhythm_shared_checkbox.toggled.connect(self._on_rhythm_changed)
        layout.addWidget(self.rhythm_shared_checkbox)

        band_row = QHBoxLayout()
        band_row.addWidget(QLabel("Beat detection band:"))
        self.rhythm_low_spin = QSpinBox()
        self.rhythm_low_spin.setRange(20, 20000)
        self.rhythm_low_spin.setSuffix(" Hz")
        self.rhythm_low_spin.setValue(int(rh.detect_low_hz))
        band_row.addWidget(self.rhythm_low_spin)
        band_row.addWidget(QLabel("-"))
        self.rhythm_high_spin = QSpinBox()
        self.rhythm_high_spin.setRange(20, 20000)
        self.rhythm_high_spin.setSuffix(" Hz")
        self.rhythm_high_spin.setValue(int(rh.detect_high_hz))
        band_row.addWidget(self.rhythm_high_spin)
        band_row.addStretch(1)
        layout.addLayout(band_row)
        for spin in (self.rhythm_low_spin, self.rhythm_high_spin):
            spin.setToolTip(
                "Keep this on the kick drum (default 40-150 Hz): a narrow bass band gives the cleanest, "
                "most regular beat. A wide band also picks up hi-hats and vocals."
            )
            spin.valueChanged.connect(self._on_rhythm_changed)

        self.rhythm_sensitivity_slider = FloatSlider(
            "Beat clock sensitivity", 1.01, 2.0, rh.sensitivity, decimals=2,
            tooltip="How far above the recent average a kick must rise to count. The level is in dB, so "
            "small values are already selective - around 1.08-1.15 suits most music. Once the tempo "
            "is locked, missed kicks are filled in anyway, so erring on the high side is fine.",
        )
        self.rhythm_min_interval_slider = FloatSlider(
            "Min interval", 50.0, 1000.0, rh.min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two detected kicks before the tempo is locked (after that, "
            "the tempo itself decides).",
        )
        self.rhythm_min_energy_slider = FloatSlider(
            "Min energy floor", 0.0, 1.0, rh.min_energy, decimals=2,
            tooltip="Below this level nothing counts as a beat, and the clock stops when the music does.",
        )
        self.rhythm_lead_slider = FloatSlider(
            "Lead time", 0.0, 300.0, rh.lead_ms, decimals=0, suffix=" ms",
            tooltip="Once the tempo is locked, send each beat this much early so the lamps light up ON "
            "the beat instead of after it (Wi-Fi + bulb reaction is typically ~100-150 ms). Raise it if "
            "the flashes look late, lower it if they look early. 0 = react to the actual kick.",
        )
        self.rhythm_accent_slider = FloatSlider(
            "Accent share", 0.05, 1.0, rh.accent_ratio, decimals=2,
            tooltip="What share of beats count as accents (the hardest hits) for pulse triggers set to "
            "'accent' - 0.25 = the hardest quarter.",
        )
        for w in (
            self.rhythm_sensitivity_slider,
            self.rhythm_min_interval_slider,
            self.rhythm_min_energy_slider,
            self.rhythm_lead_slider,
            self.rhythm_accent_slider,
        ):
            w.valueChanged.connect(self._on_rhythm_changed)
            layout.addWidget(w)

        bar_row = QHBoxLayout()
        self.rhythm_lock_checkbox = QCheckBox("Lock to tempo")
        self.rhythm_lock_checkbox.setChecked(rh.tempo_lock)
        self.rhythm_lock_checkbox.setToolTip(
            "On: once a steady tempo is found, only kicks near the expected beat count, missing ones are "
            "filled in, and Lead time applies. Off: every detected kick is a beat, as-is."
        )
        self.rhythm_lock_checkbox.toggled.connect(self._on_rhythm_changed)
        bar_row.addWidget(self.rhythm_lock_checkbox)
        bar_row.addSpacing(20)
        bar_row.addWidget(QLabel("Beats per bar:"))
        self.rhythm_bpb_spin = QSpinBox()
        self.rhythm_bpb_spin.setRange(2, 8)
        self.rhythm_bpb_spin.setValue(rh.beats_per_bar)
        self.rhythm_bpb_spin.setToolTip("4 for almost all pop/dance music; 3 for waltz time.")
        self.rhythm_bpb_spin.valueChanged.connect(self._on_rhythm_changed)
        bar_row.addWidget(self.rhythm_bpb_spin)
        bar_row.addSpacing(20)
        bar_row.addWidget(QLabel("Keep going (bars):"))
        self.rhythm_coast_spin = QSpinBox()
        self.rhythm_coast_spin.setRange(0, 64)
        self.rhythm_coast_spin.setValue(rh.coast_bars)
        self.rhythm_coast_spin.setToolTip(
            "When the beat gets lost while the music plays on (a breakdown, a fill), keep the lights "
            "moving on the last tempo for up to this many bars instead of standing still until the beat "
            "is found again. A clear beat takes over again at once; silence stops the clock. 0 = off."
        )
        self.rhythm_coast_spin.valueChanged.connect(self._on_rhythm_changed)
        bar_row.addWidget(self.rhythm_coast_spin)
        bar_row.addStretch(1)
        layout.addLayout(bar_row)

        self.rhythm_fallback_checkbox = QCheckBox("No beat? Follow the notes")
        self.rhythm_fallback_checkbox.setChecked(rh.fallback)
        self.rhythm_fallback_checkbox.setToolTip(
            "For music without a steady kick (classical, acoustic, ambient), with Lock to tempo on. Once "
            "the beat is lost and Keep going has run out, the clock follows the tempo of the notes "
            "themselves (every new note, not just kicks; it bends with the music's tempo), and if the "
            "notes have no steady tempo either, a free pace: faster with more notes and while the music "
            "swells, slower when it calms down, a strong note starting the next beat. So Chase, Group "
            "Switch and the pulse sequencer keep moving. A clear beat takes over again by itself. Off: "
            "the clock stops and only reacts to raw kicks."
        )
        self.rhythm_fallback_checkbox.toggled.connect(self._on_rhythm_changed)
        layout.addWidget(self.rhythm_fallback_checkbox)

        # The clock's state (tempo, lock, beat, phrase, loudness) shows in the
        # info panel along the bottom of the window.
        return box

    # -- pulse sequencer ---------------------------------------------------------------------

    def _build_sequencer_box(self, sq: PulseSequencerConfig) -> CollapsibleSection:
        box = CollapsibleSection(
            "Pulse sequencer", key="beat/sequencer", checkable=True, checked=sq.enabled,
            tooltip="White and dark pulses placed on musical positions - rhythm patterns, a white walk "
            "round the lamp groups, phrase fills and breaths, strobes and colour sweeps - in every colour "
            "mode, with the same settings (Beat Sync White takes only the dark pulses). Follows the shared "
            "beat clock: needs Lock to tempo, and in Beat Sync / Beat Sync White 'follow the shared clock' "
            "(Rhythm box).",
        )
        layout = box.content_layout()
        intro = QLabel(
            "Places the white and dark pulses like a lighting operator would: on rhythm patterns in 16th "
            "notes (also between the beats), with the white flash walking from lamp group to lamp group "
            "(the Group Switch groups, 'Effect group' on the Devices & Setup tab), phrase fills and a dark "
            "breath before each new phrase, and busier patterns when the music gets louder. The same in every "
            "colour mode: these settings and the pulses' look (Dark pulses and White pulses above) are "
            "shared, so trying another mode keeps them - Beat Sync White takes only the dark pulses. Follows "
            "the shared beat clock (Rhythm box): needs Lock to tempo, and in Beat Sync and Beat Sync White "
            "'follow the shared clock'. Until the clock has locked, Beat Sync rolls its per-beat pulses "
            "(probability above) and the other modes have none."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.seq_enabled_checkbox = box.toggle
        self.seq_enabled_checkbox.toggled.connect(self._on_sequencer_changed)
        self.seq_note = box.note

        self.seq_white_combo = self._labeled_combo(
            layout, "White pattern:", WHITE_PATTERNS, sq.white_pattern,
            "Which 16ths of the bar get a white flash. auto: follows the loudness - downbeats when "
            "quiet, every beat in a normal groove, the 3-3-2 syncopation when it gets loud, the gallop "
            "at the loudest. downbeats: bar starts. beats: every beat. offbeats: the 'and' between "
            "beats. syncopated: 3-3-2 (tresillo). gallop: an 8th plus two 16ths. sixteenths: every 16th.",
        )
        self.seq_walk_combo = self._labeled_combo(
            layout, "Group walk:", GROUP_WALKS, sq.group_walk,
            "Where each white flash goes. forward: the next group each time, so the white travels "
            "around the room. backward: the same the other way round - e.g. against Group Switch's "
            "colors, which move forward unless Reverse is on. pingpong: back and forth. random: any "
            "other group. all: every lamp.",
        )
        self.seq_white_density_slider = FloatSlider(
            "White density", 0.0, 1.0, sq.white_density, decimals=2,
            tooltip="Chance each pattern step actually flashes - below 1 leaves gaps, so the pattern "
            "breathes instead of being mechanical.",
        )
        self.seq_double_slider = FloatSlider(
            "Double chance", 0.0, 1.0, sq.double_chance, decimals=2,
            tooltip="Chance a flash repeats in the same group an 8th later - a quick 'da-dam' in one "
            "spot among the walking flashes.",
        )
        self.seq_focus_slider = FloatSlider(
            "Accent focus", 0.0, 1.0, sq.white_accent_focus, decimals=2,
            tooltip="Thins the white flashes the way a drummer accents: the lightest positions go first - "
            "the 16ths between beats, then the 'ands', then beats 2 and 4, then beat 3 - and the downbeat "
            "stays. 0 = every pattern step equally likely (only White density), 1 = a step keeps only "
            "its weight's share: a 16th never flashes, an 'and' rarely, the downbeat always as often as "
            "White density says. Calms busy patterns (sixteenths, gallop) without losing the pulse.",
        )
        self.seq_build_slider = FloatSlider(
            "Phrase build", 0.0, 1.0, sq.white_build, decimals=2,
            tooltip="The phrase starts sparse and fills in bar by bar toward its end - tension building "
            "up to the fill and the phrase-start flash, like an arrangement does. Adds to Accent focus at "
            "the phrase's first bar, fading out by its last bar. 0 = the same density all through the "
            "phrase. Needs a phrase length of 2 bars or more.",
        )
        self.seq_gap_slider = FloatSlider(
            "Min gap per lamp", 100.0, 1000.0, sq.min_group_gap_ms, decimals=0, suffix=" ms",
            tooltip="A lamp never starts two white flashes closer than this. Switching to white and "
            "back takes several commands, so too little here can make lamps lag or get stuck.",
        )
        self.seq_walk_positions_combo = self._labeled_combo(
            layout, "Walk through:", ("groups", "chase_order"), sq.walk_positions,
            "What the walk steps through. groups: the Group Switch groups ('Effect group' on the "
            "Devices & Setup tab). chase_order: lamp by lamp in the Chase order ('Chase order'). How many "
            "lamps flash at once follows 'Rotating' next to True white lamps above.",
        )
        for combo in (self.seq_white_combo, self.seq_walk_combo, self.seq_walk_positions_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_dark_combo = self._labeled_combo(
            layout, "Dark pattern:", DARK_PATTERNS, sq.dark_pattern,
            "Where the dark pulses (a short dip toward black) go. before_downbeat: the last 16th "
            "before each bar - a breath, then the downbeat hits. before_phrase: only before a new "
            "phrase. before_backbeats: before beats 2 and 4 too. stutter: fast dark 16ths in the "
            "phrase-end fill. auto: more of these the louder the music is.",
        )
        self.seq_dark_density_slider = FloatSlider(
            "Dark density", 0.0, 1.0, sq.dark_density, decimals=2,
            tooltip="Chance each dark step actually happens (the breath before a phrase always does, "
            "when Phrase accents is on).",
        )
        self.seq_dark_length_slider = FloatSlider(
            "Dark length", 0.3, 3.0, sq.dark_length, decimals=2, suffix="x",
            tooltip="Multiplies the dark pulse duration set above - longer reads as a deeper breath.",
        )
        self.seq_dark_combo.currentTextChanged.connect(self._on_sequencer_changed)
        for w in (
            self.seq_white_density_slider,
            self.seq_focus_slider,
            self.seq_build_slider,
            self.seq_double_slider,
            self.seq_gap_slider,
            self.seq_dark_density_slider,
            self.seq_dark_length_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        self.seq_repeat_checkbox = QCheckBox("Repeat the groove (the same flashes in every bar of a phrase)")
        self.seq_repeat_checkbox.setChecked(sq.white_repeat)
        self.seq_repeat_checkbox.setToolTip(
            "Which pattern steps flash (and which get a double) is decided once per phrase and repeated "
            "in every bar, the way a drummer keeps a groove - so the eye can follow it. A new variation "
            "comes with the next phrase. Off: every step is rolled anew, so the gaps jump around from "
            "bar to bar - livelier, but restless. With Phrase build the groove grows bar by bar: "
            "flashes are added, none taken away."
        )
        self.seq_repeat_checkbox.toggled.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_repeat_checkbox)

        phrase_row = QHBoxLayout()
        phrase_row.addWidget(QLabel("Phrase length:"))
        self.seq_phrase_combo = choice_combo(
            PHRASE_LENGTHS, sq.phrase_bars,
            "Most pop and dance music is built from 4- or 8-bar phrases: something changes or a new "
            "part starts every 8 bars. The fill, breath and accent below mark those boundaries.",
        )
        self.seq_phrase_combo.currentIndexChanged.connect(self._on_sequencer_changed)
        phrase_row.addWidget(self.seq_phrase_combo)
        phrase_row.addStretch(1)
        layout.addLayout(phrase_row)

        self.seq_dynamics_checkbox = QCheckBox("Pulse dynamics (vary each white flash's shape with the music)")
        self.seq_dynamics_checkbox.setChecked(sq.pulse_dynamics)
        self.seq_dynamics_checkbox.setToolTip(
            "The white pulse settings above (brightness, attack, duration, release) become the baseline, "
            "and each flash is shaped by where it falls in the music: the downbeat long and bright, "
            "flashes between beats short and crisp, fills snappier and brighter toward the phrase start, "
            "the phrase start held longest with a slow fade, quiet parts dimmer and softer, loud parts "
            "full and sharp. Off: every flash has exactly the settings above."
        )
        self.seq_dynamics_checkbox.toggled.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_dynamics_checkbox)
        self.seq_dynamics_slider = FloatSlider(
            "Dynamics amount", 0.0, 1.0, sq.pulse_dynamics_amount, decimals=2,
            tooltip="How strongly the flashes vary: 0 = all identical, 1 = the full contrast (a downbeat "
            "flash then lasts about 4x as long as a 16th, and is almost twice as bright).",
        )
        self.seq_dynamics_slider.valueChanged.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_dynamics_slider)

        self.seq_fills_checkbox = QCheckBox("Fills (denser flashes at the end of each phrase)")
        self.seq_fills_checkbox.setChecked(sq.fills)
        self.seq_accent_checkbox = QCheckBox("Phrase accents (dark breath, then every lamp flashes on the new phrase)")
        self.seq_accent_checkbox.setChecked(sq.phrase_accent)
        self.seq_drop_checkbox = QCheckBox("Follow drops (a quiet-to-loud jump starts a new phrase right there)")
        self.seq_drop_checkbox.setChecked(sq.drop_detection)
        for cb in (self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox):
            cb.toggled.connect(self._on_sequencer_changed)
            layout.addWidget(cb)
        self._build_strobe_controls(layout, sq)
        return box

    def _build_strobe_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Strobe", key="beat/strobe", checkable=True, checked=sq.strobe_enabled, nested=True,
            tooltip="Now and then a short cool-white burst on every lamp at once. "
            "A roll into the next phrase or bar: the end of a bar becomes a burst of fast cool-white "
            "flashes on every selected lamp at the same instant, on top of the show - the colours and the "
            "other pulses keep running underneath. Rare by default, so it stays an event; placement, "
            "loudness, chance and minimum gap below make it as frequent as you like, and Strobe length as "
            "long as a whole bar. The flash rate follows the tempo: the fastest subdivision of the beat "
            "under Max rate. Needs Lamp transitions 'direct' or 'smooth' (Global box).\n\n"
            "Flashing light at these rates can trigger seizures in people with photosensitive epilepsy - "
            "the usual recommendation for venues is at most 4 flashes per second.",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro("Now and then a short cool-white burst on every lamp at once."))
        self.seq_strobe_checkbox = section.toggle
        self.seq_strobe_checkbox.toggled.connect(self._on_sequencer_changed)
        self.seq_strobe_note = section.note

        self.seq_strobe_placement_combo = self._labeled_combo(
            layout, "Strobe placement:", STROBE_PLACEMENTS, sq.strobe_placement,
            "Where a strobe may happen, from rarest to most frequent. phrase: only at the end of a phrase's "
            "last bar - the roll into the new phrase. half_phrase: also into the phrase's second half. "
            "bars: at the end of any bar. half_bars: also in the middle of each bar (up to twice per bar - "
            "set Strobe min gap to 0 for that; only with a Strobe length of half a bar or less).",
        )
        self.seq_strobe_level_combo = self._labeled_combo(
            layout, "Strobe from loudness:", LEVELS, sq.strobe_min_level,
            "Only when the music is at least this loud compared to the last half minute (the loudness "
            "shown in the Rhythm box's status line). calm: always. groove: not in the quiet parts. "
            "high / peak: only in the loudest ones.",
        )
        length_row = QHBoxLayout()
        length_row.addWidget(QLabel("Strobe length:"))
        self.seq_strobe_length_combo = choice_combo(
            [(0.5, "half a beat"), (1.0, "1 beat"), (2.0, "2 beats"), (4.0, "4 beats")], sq.strobe_beats,
            "How much of the end of the bar the burst fills (at most the whole bar: 4 beats in 4/4). It "
            "stops at the bar line, so the downbeat that follows lands clean. Half a beat needs at least "
            "two flashes in it - at fast tempos that may not fit under Max rate, and then nothing is played.",
        )
        length_row.addWidget(self.seq_strobe_length_combo)
        length_row.addStretch(1)
        layout.addLayout(length_row)
        self.seq_strobe_wave_combo = self._labeled_combo(
            layout, "Strobe brightness wave:", STROBE_WAVES, sq.strobe_wave,
            "How the brightness moves over one burst. off: every flash at Strobe brightness. linear: the "
            "burst swells - the first flashes are nearly dark, each one brighter up to Strobe brightness "
            "in the middle, then back down to dark. bezier: the same with an eased S-curve (as in Chase's "
            "falloff) - it lingers near dark at the ends and near full in the middle. Shows best on longer "
            "bursts (2 or 4 beats): a 1-beat burst has only a few flashes to shape.",
        )
        for combo in (self.seq_strobe_placement_combo, self.seq_strobe_level_combo, self.seq_strobe_wave_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_strobe_length_combo.currentIndexChanged.connect(self._on_sequencer_changed)

        self.seq_strobe_chance_slider = FloatSlider(
            "Strobe chance", 0.0, 1.0, sq.strobe_chance, decimals=2,
            tooltip="Chance a bar that qualifies actually gets the strobe. 1 = every time (handy while "
            "trying it out), lower = rarer and less predictable.",
        )
        self.seq_strobe_gap_slider = FloatSlider(
            "Strobe min gap", 0.0, 32.0, float(sq.strobe_min_gap_bars), decimals=0, suffix=" bars",
            tooltip="Never two strobes closer than this many bars, whatever the chance says. 1 = at most "
            "one per bar; 0 = no minimum (with placement 'half_bars': two per bar).",
        )
        self.seq_strobe_rate_slider = FloatSlider(
            "Strobe max rate", 4.0, 15.0, sq.strobe_max_hz, decimals=1, suffix=" /s",
            tooltip="Ceiling on flashes per second. The actual rate is the fastest subdivision of the beat "
            "under this (8ths, triplets, 16ths, 16th triplets...): at 128 BPM 8.5 per second, or 12.8 once "
            "the ceiling is above that. The Lamp command rate limits it too: a flash is two commands, so "
            "20 commands/s allow at most 10 flashes/s and 15 flashes/s need 30 commands/s - the Test "
            "button says when that is what's holding the rate down.",
        )
        self.seq_strobe_duty_slider = FloatSlider(
            "Strobe on-time", 0.2, 0.8, sq.strobe_duty, decimals=2,
            tooltip="Share of each flash's period the white is on: 0.5 = on and off equally long, lower = "
            "shorter, sharper flashes. Away from 0.5 the shorter part needs a higher Lamp command rate "
            "(otherwise a slower subdivision is used).",
        )
        self.seq_strobe_brightness_slider = FloatSlider(
            "Strobe brightness", 0.05, 1.0, sq.strobe_brightness, decimals=2,
            tooltip="White brightness of the flashes (cool white - it lights up fastest). The per-lamp "
            "white pulse brightness multiplier applies here too.",
        )
        for w in (
            self.seq_strobe_chance_slider,
            self.seq_strobe_gap_slider,
            self.seq_strobe_rate_slider,
            self.seq_strobe_duty_slider,
            self.seq_strobe_brightness_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        test_row = QHBoxLayout()
        self.seq_strobe_test_button = QPushButton("Test strobe now")
        self.seq_strobe_test_button.setToolTip(
            "Plays one burst on the selected lamps right away with the length, max rate, on-time and "
            "brightness above - to see how it looks without waiting for the music to call for one."
        )
        self.seq_strobe_test_button.clicked.connect(self._on_strobe_test)
        test_row.addWidget(self.seq_strobe_test_button)
        self.seq_strobe_test_label = QLabel()
        self.seq_strobe_test_label.setWordWrap(True)
        test_row.addWidget(self.seq_strobe_test_label, stretch=1)
        layout.addLayout(test_row)

        self._build_flash_strobe_controls(parent_layout, sq)
        self._build_color_sweep_controls(parent_layout, sq)

    def _build_flash_strobe_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Flash strobe", key="beat/flash_strobe", checkable=True, checked=sq.flash_strobe_enabled,
            nested=True,
            tooltip="Now and then one group's white flash becomes a short strobe. Along the white walk: now and then one of the walk's white flashes is played as a short "
            "strobe on just that lamp group instead - so as the white goes round the room, a strobe shows "
            "up here and there, like a glitch. With several White rotators only the walk's own group "
            "strobes; the others keep their plain white flash. It starts on the flash's beat (never the "
            "downbeat, which lands clean) and "
            "lasts as long as that white flash would (its attack + duration + release, as the pulse "
            "dynamics shaped it), stopping at the bar line at the latest. Its flashes follow the white "
            "flash's brightness: they rise over the attack, hold, and fade over the release along its "
            "release curve (times Flash strobe brightness below) - so it doesn't stand out from the white "
            "pulses. With a white flash shorter than two strobe flashes it stays a plain flash. "
            "The rate and on-time are the Strobe's (Strobe max rate, Strobe on-time above). Independent "
            "of the Strobe checkbox. Needs Lamp transitions 'direct' or 'smooth' (Global box).\n\n"
            "Flashing light at these rates can trigger seizures in people with photosensitive epilepsy.",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro(
            "Now and then one group's white flash becomes a short strobe. It uses that white pulse's "
            "brightness curve: its flashes rise over the attack, stay full through the duration and fade "
            "over the release along the release curve (White pulses section), as the pulse dynamics shaped "
            "that flash - times Flash strobe brightness below."
        ))
        self.seq_flash_strobe_checkbox = section.toggle
        self.seq_flash_strobe_checkbox.toggled.connect(self._on_sequencer_changed)

        self.seq_flash_strobe_placement_combo = self._labeled_combo(
            layout, "Flash strobe placement:", FLASH_STROBE_PLACEMENTS, sq.flash_strobe_placement,
            "Which white flashes may become one. bar_end: only the flash on the bar's last beat - a roll "
            "into the next bar. beats: a flash on any beat but the downbeat (8th and 16th flashes in "
            "between never do).",
        )
        self.seq_flash_strobe_level_combo = self._labeled_combo(
            layout, "Flash strobe from loudness:", LEVELS, sq.flash_strobe_min_level,
            "Only when the music is at least this loud compared to the last half minute. calm: always. "
            "groove: not in the quiet parts. high / peak: only in the loudest ones.",
        )
        for combo in (self.seq_flash_strobe_placement_combo, self.seq_flash_strobe_level_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)

        self.seq_flash_strobe_chance_slider = FloatSlider(
            "Flash strobe chance", 0.0, 1.0, sq.flash_strobe_chance, decimals=2,
            tooltip="Chance a white flash that qualifies becomes a strobe. 1 = every time (handy while "
            "trying it out).",
        )
        self.seq_flash_strobe_gap_slider = FloatSlider(
            "Flash strobe min gap", 0.0, 16.0, float(sq.flash_strobe_min_gap_bars), decimals=0, suffix=" bars",
            tooltip="Never two flash strobes closer than this many bars, whatever the chance says. 0 = no "
            "minimum.",
        )
        self.seq_flash_strobe_brightness_slider = FloatSlider(
            "Flash strobe brightness", 0.3, 1.5, sq.flash_strobe_brightness, decimals=2, suffix=" x",
            tooltip="Brightness of its flashes relative to the white flash it replaces, moment by moment "
            "along that flash's attack, duration and release (it follows White brightness, the pulse "
            "dynamics and the per-lamp multiplier). 1 = just as bright, so it blends in with the white "
            "pulses; lower = subtler.",
        )
        for w in (
            self.seq_flash_strobe_chance_slider,
            self.seq_flash_strobe_gap_slider,
            self.seq_flash_strobe_brightness_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

    def _build_color_sweep_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Color sweep", key="beat/color_sweep", checkable=True, checked=sq.color_sweep_enabled, nested=True,
            tooltip="Now and then the colour below runs once round the lamps in Chase order ('Chase order' in the "
            "Devices & Setup tab - lamps without one are left out), fast: over the Sweep length, each position "
            "lit in turn for its share of it, on top of the show. Placed on the beat grid like the strobe: "
            "it ends on a bar line, so the downbeat lands clean, and never runs during the strobe (a strobe "
            "takes over from one still running). Uses only its own settings below - none of the Chase "
            "overlay's (Chase itself can stay off).",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro("Now and then a colour of its own runs fast round the lamps in Chase order."))
        self.seq_sweep_checkbox = section.toggle
        self.seq_sweep_checkbox.toggled.connect(self._on_sequencer_changed)

        self.seq_sweep_color_combo = self._labeled_combo(
            layout, "Sweep colour:", COLOR_SWEEP_COLORS, sq.color_sweep_color,
            "custom: the hue below. dynamic: a new colour for every sweep, one of the brightest the bulbs "
            "make (red, yellow, green, cyan, blue, magenta) - one of the two farthest from the colour the "
            "lamps show at that moment, so it stands out from the show, and never the previous sweep's. "
            "The info panel's timeline shows each sweep in its colour.",
        )
        self.seq_sweep_color_combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_sweep_hue_slider = HueSlider("Custom hue", sq.color_sweep_hue_deg)
        self.seq_sweep_saturation_slider = FloatSlider(
            "Sweep saturation", 0.0, 1.0, sq.color_sweep_saturation, decimals=2,
            tooltip="1 = the full colour, lower = paler (0 = white from the RGB LEDs).",
        )
        self.seq_sweep_brightness_slider = FloatSlider(
            "Sweep brightness", 0.1, 1.0, sq.color_sweep_brightness, decimals=2,
            tooltip="Brightness of the lit lamp.",
        )
        for w in (self.seq_sweep_hue_slider, self.seq_sweep_saturation_slider, self.seq_sweep_brightness_slider):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        length_row = QHBoxLayout()
        length_row.addWidget(QLabel("Sweep length:"))
        self.seq_sweep_length_combo = choice_combo(
            [(0.5, "half a beat"), (1.0, "1 beat"), (2.0, "2 beats"), (4.0, "4 beats")], sq.color_sweep_beats,
            "How long one sweep round the lamps takes (at most the whole bar). Very short sweeps on many lamps "
            "light each one for only a tick or two.",
        )
        self.seq_sweep_length_combo.currentIndexChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(self.seq_sweep_length_combo)
        length_row.addWidget(QLabel("Rotators:"))
        self.seq_sweep_rotators_spin = QSpinBox()
        self.seq_sweep_rotators_spin.setRange(1, 16)
        self.seq_sweep_rotators_spin.setValue(sq.color_sweep_rotators)
        self.seq_sweep_rotators_spin.setToolTip(
            "How many run round at once, evenly spaced round the loop: 2 = from opposite sides, 3 = thirds. "
            "Each covers its own stretch, so every lamp is still lit once per sweep."
        )
        self.seq_sweep_rotators_spin.valueChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(self.seq_sweep_rotators_spin)
        length_row.addWidget(QLabel("Width:"))
        self.seq_sweep_width_spin = QSpinBox()
        self.seq_sweep_width_spin.setRange(1, 8)
        self.seq_sweep_width_spin.setValue(sq.color_sweep_width)
        self.seq_sweep_width_spin.setSuffix(" spots")
        self.seq_sweep_width_spin.setToolTip(
            "How many neighbouring Chase order positions are lit at once: 1 = a single spot running round, "
            "2 or more = a band of that many, easier to follow. Every lamp is still lit for the same share "
            "of the sweep, and the sweep takes the same Sweep length."
        )
        self.seq_sweep_width_spin.valueChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(self.seq_sweep_width_spin)
        length_row.addStretch(1)
        layout.addLayout(length_row)

        self.seq_sweep_direction_combo = self._labeled_combo(
            layout, "Sweep direction:", COLOR_SWEEP_DIRECTIONS, sq.color_sweep_direction,
            "forward: up the Chase order. backward: down it.",
        )
        self.seq_sweep_placement_combo = self._labeled_combo(
            layout, "Sweep placement:", COLOR_SWEEP_PLACEMENTS, sq.color_sweep_placement,
            "Where a sweep may happen, from rarest to most frequent. phrase: ending on the bar line into a "
            "new phrase - a run into the phrase-start flash. bars: ending on any bar line. beats: starting on "
            "any beat (and over by the bar line).",
        )
        self.seq_sweep_level_combo = self._labeled_combo(
            layout, "Sweep from loudness:", LEVELS, sq.color_sweep_min_level,
            "Only when the music is at least this loud compared to the last half minute. calm: always. "
            "groove: not in the quiet parts. high / peak: only in the loudest ones.",
        )
        for combo in (self.seq_sweep_direction_combo, self.seq_sweep_placement_combo, self.seq_sweep_level_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)

        self.seq_sweep_chance_slider = FloatSlider(
            "Sweep chance", 0.0, 1.0, sq.color_sweep_chance, decimals=2,
            tooltip="Chance a spot that qualifies gets a sweep. 1 = every time (handy while trying it out).",
        )
        self.seq_sweep_gap_slider = FloatSlider(
            "Sweep min gap", 0.0, 16.0, float(sq.color_sweep_min_gap_bars), decimals=0, suffix=" bars",
            tooltip="Never two sweeps closer than this many bars, whatever the chance says. 0 = no minimum.",
        )
        for w in (self.seq_sweep_chance_slider, self.seq_sweep_gap_slider):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        test_row = QHBoxLayout()
        self.seq_sweep_test_button = QPushButton("Test sweep now")
        self.seq_sweep_test_button.setToolTip(
            "Plays the sweep 4 times in a row on the selected lamps right away, with the settings above (a "
            "new colour each time with 'dynamic') - to see how it looks without waiting for the music to "
            "call for one. Plays on top of the show, so start the visualizer first."
        )
        self.seq_sweep_test_button.clicked.connect(self._on_sweep_test)
        test_row.addWidget(self.seq_sweep_test_button)
        self.seq_sweep_test_label = QLabel()
        self.seq_sweep_test_label.setWordWrap(True)
        test_row.addWidget(self.seq_sweep_test_label, stretch=1)
        layout.addLayout(test_row)

    def _on_sweep_test(self) -> None:
        self.seq_sweep_test_label.setText(self.controller.engine.trigger_color_sweep_test())

    def _on_strobe_test(self) -> None:
        self.seq_strobe_test_label.setText(self.controller.engine.trigger_strobe_test())

    @staticmethod
    def _intro(text: str) -> QLabel:
        """A one-line explanation at the top of a section's settings."""
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _labeled_combo(layout: QVBoxLayout, label: str, items, value: str, tooltip: str) -> QComboBox:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(list(items))
        combo.setCurrentText(value)
        combo.setToolTip(tooltip)
        row.addWidget(combo)
        row.addStretch(1)
        layout.addLayout(row)
        return combo

    def _on_sequencer_changed(self, *_args) -> None:
        sq = self.controller.config.sequencer
        sq.enabled = self.seq_enabled_checkbox.isChecked()
        sq.white_pattern = self.seq_white_combo.currentText()
        sq.group_walk = self.seq_walk_combo.currentText()
        sq.walk_positions = self.seq_walk_positions_combo.currentText()
        sq.white_density = self.seq_white_density_slider.value()
        sq.double_chance = self.seq_double_slider.value()
        sq.white_accent_focus = self.seq_focus_slider.value()
        sq.white_build = self.seq_build_slider.value()
        sq.white_repeat = self.seq_repeat_checkbox.isChecked()
        sq.min_group_gap_ms = self.seq_gap_slider.value()
        sq.dark_pattern = self.seq_dark_combo.currentText()
        sq.dark_density = self.seq_dark_density_slider.value()
        sq.dark_length = self.seq_dark_length_slider.value()
        sq.phrase_bars = self.seq_phrase_combo.currentData()
        sq.fills = self.seq_fills_checkbox.isChecked()
        sq.phrase_accent = self.seq_accent_checkbox.isChecked()
        sq.drop_detection = self.seq_drop_checkbox.isChecked()
        sq.pulse_dynamics = self.seq_dynamics_checkbox.isChecked()
        sq.pulse_dynamics_amount = self.seq_dynamics_slider.value()
        sq.strobe_enabled = self.seq_strobe_checkbox.isChecked()
        sq.strobe_placement = self.seq_strobe_placement_combo.currentText()
        sq.strobe_min_level = self.seq_strobe_level_combo.currentText()
        sq.strobe_beats = self.seq_strobe_length_combo.currentData()
        sq.strobe_chance = self.seq_strobe_chance_slider.value()
        sq.strobe_min_gap_bars = int(round(self.seq_strobe_gap_slider.value()))
        sq.strobe_max_hz = self.seq_strobe_rate_slider.value()
        sq.strobe_duty = self.seq_strobe_duty_slider.value()
        sq.strobe_brightness = self.seq_strobe_brightness_slider.value()
        sq.strobe_wave = self.seq_strobe_wave_combo.currentText()
        sq.flash_strobe_enabled = self.seq_flash_strobe_checkbox.isChecked()
        sq.flash_strobe_placement = self.seq_flash_strobe_placement_combo.currentText()
        sq.flash_strobe_min_level = self.seq_flash_strobe_level_combo.currentText()
        sq.flash_strobe_chance = self.seq_flash_strobe_chance_slider.value()
        sq.flash_strobe_min_gap_bars = int(round(self.seq_flash_strobe_gap_slider.value()))
        sq.flash_strobe_brightness = self.seq_flash_strobe_brightness_slider.value()
        sq.color_sweep_enabled = self.seq_sweep_checkbox.isChecked()
        sq.color_sweep_color = self.seq_sweep_color_combo.currentText()
        sq.color_sweep_hue_deg = self.seq_sweep_hue_slider.value()
        sq.color_sweep_saturation = self.seq_sweep_saturation_slider.value()
        sq.color_sweep_brightness = self.seq_sweep_brightness_slider.value()
        sq.color_sweep_beats = self.seq_sweep_length_combo.currentData()
        sq.color_sweep_rotators = self.seq_sweep_rotators_spin.value()
        sq.color_sweep_width = self.seq_sweep_width_spin.value()
        sq.color_sweep_direction = self.seq_sweep_direction_combo.currentText()
        sq.color_sweep_placement = self.seq_sweep_placement_combo.currentText()
        sq.color_sweep_min_level = self.seq_sweep_level_combo.currentText()
        sq.color_sweep_chance = self.seq_sweep_chance_slider.value()
        sq.color_sweep_min_gap_bars = int(round(self.seq_sweep_gap_slider.value()))
        self._update_beat_states()
        self.controller.apply_config_changes()

    def _on_rhythm_changed(self, *_args) -> None:
        rh = self.controller.config.rhythm
        rh.shared_clock = self.rhythm_shared_checkbox.isChecked()
        rh.detect_low_hz = self.rhythm_low_spin.value()
        rh.detect_high_hz = self.rhythm_high_spin.value()
        rh.sensitivity = self.rhythm_sensitivity_slider.value()
        rh.min_interval_ms = self.rhythm_min_interval_slider.value()
        rh.min_energy = self.rhythm_min_energy_slider.value()
        rh.lead_ms = self.rhythm_lead_slider.value()
        rh.accent_ratio = self.rhythm_accent_slider.value()
        rh.tempo_lock = self.rhythm_lock_checkbox.isChecked()
        rh.coast_bars = self.rhythm_coast_spin.value()
        rh.fallback = self.rhythm_fallback_checkbox.isChecked()
        bar_changed = rh.beats_per_bar != self.rhythm_bpb_spin.value()
        rh.beats_per_bar = self.rhythm_bpb_spin.value()
        if bar_changed:
            # The every-N-beats choices on both settings tabs depend on the bar length.
            self.controller.replace_config_settings()
            return
        self._update_beat_states()
        self.controller.apply_config_changes()

    def _refresh_spectrum_status(self) -> None:
        """The line under Hue mode: what the music-driven spectrum hue / hue step is doing."""
        cfg = self.controller.config.color_mapping
        bs = cfg.beat_sync
        spectrum = bs.hue_mode == "spectrum"
        music_step = bs.hue_mode == "step" and bs.hue_step_source != "fixed"
        if cfg.mode != "beat_sync" or not (spectrum or music_step):
            self.beat_spectrum_status_label.setText("")
            return
        label = "Spectrum" if spectrum else "Hue step"
        engine = self.controller.engine
        if not engine.running:
            self.beat_spectrum_status_label.setText(f"{label}: - (runs while the visualizer is on)")
            return
        source = bs.spectrum_source if spectrum else bs.hue_step_source
        text = f"{label}: from {source}"
        if music_step and engine.beat_hue_step_now is not None:
            text += f", last step {engine.beat_hue_step_now:.0f} deg"
        pitch = engine.latest_pitch
        if pitch is not None and source == "pitch":
            text += (
                f"  |  note {pitch.note_name} in {pitch.key_name}, note clear {pitch.confidence:.0%}, "
                f"notes moving {pitch.variety:.0%} -> pitch share {engine.pitch_weight:.0%} "
                "(the rest from intensity+brightness)"
            )
        self.beat_spectrum_status_label.setText(text)
