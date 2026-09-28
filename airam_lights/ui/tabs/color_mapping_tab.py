"""Color Mapping tab: full detail controls for RGB Frequency / Custom / HSV
Music modes, the shared beat clock, plus global response curve, smoothing,
and presets."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
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

from ...config.builtin_presets import BUILTIN_PRESETS, apply_builtin_preset
from ...effects.pulse_sequencer import DARK_PATTERNS, GROUP_WALKS, WHITE_PATTERNS
from ...config.schema import (
    PULSE_TRIGGERS,
    ChaseEffectConfig,
    ColorMappingConfig,
    GroupSwitchEffectConfig,
    PulseSequencerConfig,
    RGBModeConfig,
    RhythmConfig,
)
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
from ..widgets.param_slider import FloatSlider


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

        self.sensitivity_slider = FloatSlider("Sensitivity", 0.1, 4.0, cfg.sensitivity)
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

        # -- RGB Frequency mode -----------------------------------------------------
        self.rgb_editor = RGBModeEditor(cm.rgb, self._on_rgb_changed)
        sub_tabs.addTab(self.rgb_editor, "RGB Frequency")

        # -- Custom mode --------------------------------------------------------------
        self.custom_editor = RGBModeEditor(cm.custom, self._on_custom_changed)
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
        self.hsv_sensitivity_slider = FloatSlider("Sensitivity", 0.1, 4.0, hsv.sensitivity)
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
        hsv_desc_label = QLabel(
            "Hue follows the spectral centroid (frequency distribution), brightness "
            "follows overall energy, saturation follows spectral contrast (peaky vs. flat)."
        )
        hsv_desc_label.setWordWrap(True)
        hsv_layout.addWidget(hsv_desc_label)
        sub_tabs.addTab(hsv_widget, "HSV Music")

        # -- Beat Sync mode -----------------------------------------------------------
        beat_widget = QWidget()
        beat_layout = QVBoxLayout(beat_widget)
        bs = cm.beat_sync

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
            "Sensitivity", 1.05, 4.0, bs.sensitivity, decimals=2,
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
        hue_row.addStretch(1)
        beat_layout.addLayout(hue_row)
        beat_hue_mode_note = QLabel(
            "random: a fresh, sufficiently-different color every hit. step: cycles through the color "
            "wheel by a fixed angle each hit (never repeats for a long time). spectrum: hue follows "
            "the spectral centroid at the moment of the hit."
        )
        beat_hue_mode_note.setWordWrap(True)
        beat_layout.addWidget(beat_hue_mode_note)

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
            self.beat_min_jump_slider,
            self.beat_saturation_slider,
            self.beat_flash_slider,
            self.beat_sustain_slider,
            self.beat_hue_attack_slider,
            self.beat_bright_attack_slider,
            self.beat_bright_release_slider,
        ):
            w.valueChanged.connect(self._on_beat_changed)
            beat_layout.addWidget(w)

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
            "overshoots and snaps back on the beat; 360 = a full rainbow every beat.",
        )
        self.beat_hue_glide_slider.valueChanged.connect(self._on_beat_changed)
        beat_layout.addWidget(self.beat_hue_glide_slider)

        beat_dark_note = QLabel(
            "Dark pulses: on a random subset of beats, briefly dip brightness toward black BEFORE "
            "flashing - a rhythm-synced pause/strobe accent, on top of the hue and brightness above."
        )
        beat_dark_note.setWordWrap(True)
        beat_layout.addWidget(beat_dark_note)

        self.beat_dark_enabled_checkbox = QCheckBox("Enabled")
        self.beat_dark_enabled_checkbox.setChecked(bs.dark_pulse_enabled)
        self.beat_dark_enabled_checkbox.setToolTip(
            "Turns the dark-pulse accent on or off - leave unchecked if you never want this effect "
            "(white pulses below are unaffected either way)."
        )
        self.beat_dark_enabled_checkbox.toggled.connect(self._on_beat_changed)
        beat_layout.addWidget(self.beat_dark_enabled_checkbox)
        self.beat_dark_trigger_combo = self._trigger_combo(beat_layout, "Dark pulse on:", bs.dark_pulse_trigger)
        self.beat_dark_note = inactive_note()
        beat_layout.addWidget(self.beat_dark_note)

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

        beat_white_pulse_note = QLabel(
            "White pulses: on a subset of beats, a brief flash of the lamp's own white LEDs right as "
            "the beat hits - e.g. a hi-hat/cymbal accent. Always the real white LEDs, never white mixed "
            "from RGB, so the colored LEDs keep all their output for color. Independent of dark pulses "
            "above - each rolls its own probability, so both, either, or neither can happen on a hit."
        )
        beat_white_pulse_note.setWordWrap(True)
        beat_layout.addWidget(beat_white_pulse_note)

        self.beat_white_pulse_enabled_checkbox = QCheckBox("Enabled")
        self.beat_white_pulse_enabled_checkbox.setChecked(bs.white_pulse_enabled)
        self.beat_white_pulse_enabled_checkbox.setToolTip(
            "Turns the white-flash accent on or off - leave unchecked if you never want this effect "
            "(dark pulses above are unaffected either way)."
        )
        self.beat_white_pulse_enabled_checkbox.toggled.connect(self._on_beat_changed)
        beat_layout.addWidget(self.beat_white_pulse_enabled_checkbox)

        white_target_row = QHBoxLayout()
        white_target_row.addWidget(QLabel("True white lamps:"))
        self.beat_white_pulse_target_combo = QComboBox()
        self.beat_white_pulse_target_combo.addItems(["all", "chase", "group"])
        self.beat_white_pulse_target_combo.setCurrentText(bs.white_pulse_target)
        self.beat_white_pulse_target_combo.setToolTip(
            "Which lamps a white flash lands on. all "
            "(default): every selected lamp at once. chase: only the lamps the Chase effect's moving "
            "highlight is on at that moment. group: only the lamps in Group Switch's currently active "
            "group. The lamps are picked when the flash starts and kept for its whole duration. If the "
            "chosen effect isn't enabled (or has fewer than 2 positions), falls back to all lamps."
        )
        self.beat_white_pulse_target_combo.currentTextChanged.connect(self._on_beat_changed)
        white_target_row.addWidget(self.beat_white_pulse_target_combo)
        white_target_row.addStretch(1)
        beat_layout.addLayout(white_target_row)
        self.beat_white_trigger_combo = self._trigger_combo(beat_layout, "White pulse on:", bs.white_pulse_trigger)
        self.beat_white_note = inactive_note()
        beat_layout.addWidget(self.beat_white_note)

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
            tooltip="How quickly the white flash starts after the beat - low = right on the beat.",
        )
        self.beat_white_pulse_release_slider = FloatSlider(
            "White pulse release", 10.0, 1000.0, bs.white_pulse_release_ms, decimals=0, suffix=" ms",
            tooltip="How long after the hold the lamp switches back to color - together with the "
            "duration this sets how long each white flash lasts.",
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

        self.beat_low_spin.valueChanged.connect(self._on_beat_changed)
        self.beat_high_spin.valueChanged.connect(self._on_beat_changed)

        beat_layout.addWidget(self._build_sequencer_box(controller.config.sequencer))

        sub_tabs.addTab(beat_widget, "Beat Sync")

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

        self.bsw_sensitivity_slider = FloatSlider("Sensitivity", 1.05, 4.0, bsw.sensitivity, decimals=2)
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
            "random: a new temperature every hit. alternate: ping-pongs between the warm and cool ends."
        )
        bsw_temp_mode_note.setWordWrap(True)
        bsw_layout.addWidget(bsw_temp_mode_note)

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

        bsw_dark_note = QLabel("Dark pulses: same as Beat Sync - a rhythm-synced pause toward black before flashing.")
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

        self.bsw_low_spin.valueChanged.connect(self._on_beat_white_changed)
        self.bsw_high_spin.valueChanged.connect(self._on_beat_white_changed)

        sub_tabs.addTab(bsw_widget, "Beat Sync White")

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

        self.peak_sensitivity_slider = FloatSlider("Sensitivity", 1.05, 4.0, pf.sensitivity, decimals=2)
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

        sub_tabs.addTab(peak_widget, "Peak Flash")

        # -- global ---------------------------------------------------------------------
        global_box = QGroupBox("Global")
        global_layout = QVBoxLayout(global_box)
        curve_row = QHBoxLayout()
        curve_row.addWidget(QLabel("Response curve:"))
        self.curve_combo = QComboBox()
        self.curve_combo.addItems(["linear", "log", "exp2"])
        self.curve_combo.setCurrentText(cm.response_curve)
        self.curve_combo.setToolTip(
            "Reshapes the raw 0..1 audio level BEFORE it becomes brightness/hue, in RGB Frequency, "
            "Custom, HSV Music, and 8-Band Spectrum modes only (Beat Sync, Peak Flash, and Beat Sync "
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

        self.invert_checkbox = QCheckBox("Invert brightness (0 = bright, 1 = black)")
        self.invert_checkbox.setChecked(cm.invert_brightness)
        self.invert_checkbox.toggled.connect(self._on_invert_changed)
        global_layout.addWidget(self.invert_checkbox)
        invert_note = QLabel("Applies to every mode - flips the brightness response so quiet/dark moments light up instead.")
        invert_note.setWordWrap(True)
        global_layout.addWidget(invert_note)

        root.addWidget(global_box)

        # -- presets ------------------------------------------------------------------------
        preset_box = QGroupBox("Presets")
        preset_layout = QVBoxLayout(preset_box)

        builtin_row = QHBoxLayout()
        builtin_row.addWidget(QLabel("Built-in look:"))
        self.builtin_combo = QComboBox()
        self.builtin_combo.addItems(list(BUILTIN_PRESETS))
        self.builtin_combo.currentTextChanged.connect(self._show_builtin_description)
        builtin_row.addWidget(self.builtin_combo, stretch=1)
        apply_builtin_btn = QPushButton("Apply")
        apply_builtin_btn.setToolTip(
            "Sets the shared beat clock, Beat Sync, Chase and Group Switch together (Chase and Group "
            "Switch are on the 8-Band && Per-Lamp tab). Leaves your lamp groups, Chase width/intensity, "
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

    # -- which Beat Sync settings are in use right now --------------------------------

    def _update_beat_states(self) -> None:
        """Grey out the Beat Sync / sequencer settings the current combination
        doesn't use, and say why - so changing one never looks ignored."""
        cfg = self.controller.config
        bs, rh, sq = cfg.color_mapping.beat_sync, cfg.rhythm, cfg.sequencer
        shared = rh.shared_clock
        sequencing = shared and sq.enabled

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

        # In spectrum mode the hue glide heads one Hue step onward (there's no known next color).
        set_active(
            [self.beat_hue_step_slider], bs.hue_mode == "step" or (bs.glide_hue and bs.hue_mode == "spectrum"),
            f"Hue mode is '{bs.hue_mode}'.",
        )
        set_active(
            [self.beat_sustain_slider], bs.fade_brightness,
            "Fade brightness is off: brightness stays at Flash brightness between beats.",
        )
        set_active([self.beat_hue_glide_slider, self.beat_glide_timing_combo], bs.glide_hue, "Glide hue is off.")
        set_active([self.beat_min_jump_slider], bs.hue_mode == "random", f"Hue mode is '{bs.hue_mode}'.")

        seq_reason = "the Pulse sequencer (below) places the pulses."
        no_clock_reason = "accent/downbeat need the shared beat clock (Rhythm box)."
        for enabled, prob, trigger, others, note, name in (
            (bs.dark_pulse_enabled, self.beat_dark_prob_slider, self.beat_dark_trigger_combo,
             [self.beat_dark_duration_slider, self.beat_dark_depth_slider, self.beat_dark_attack_slider,
              self.beat_dark_release_slider], self.beat_dark_note, "Dark"),
            (bs.white_pulse_enabled, self.beat_white_pulse_prob_slider, self.beat_white_trigger_combo,
             [self.beat_white_pulse_duration_slider, self.beat_white_pulse_attack_slider,
              self.beat_white_pulse_release_slider, self.beat_white_pulse_white_brightness_slider,
              self.beat_white_pulse_cool_ratio_slider], self.beat_white_note, "White"),
        ):
            off_reason = f"{name} pulses are switched off."
            set_active(others, enabled, off_reason)
            set_active([prob], enabled and not sequencing, off_reason if not enabled else seq_reason.capitalize())
            set_active(
                [trigger], enabled and shared and not sequencing,
                off_reason if not enabled else (seq_reason.capitalize() if sequencing else no_clock_reason.capitalize()),
            )
            if enabled and sequencing:
                show_note(note, f"{name} pulses are placed by the Pulse sequencer, so the probability and "
                          "trigger here are greyed out; durations and strength still apply.")
            elif enabled and not shared:
                show_note(note, "'accent' and 'downbeat' triggers need the shared beat clock (Rhythm box) - "
                          "without it every beat rolls the probability.")
            else:
                show_note(note, "")

        white = bs.white_pulse_enabled
        set_active(
            [self.beat_white_pulse_target_combo], white and not sequencing,
            "White pulses are switched off." if not white else
            seq_reason.capitalize() + " Its group walk decides the lamps.",
        )

        seq_controls = [
            self.seq_white_combo, self.seq_walk_combo, self.seq_white_density_slider, self.seq_double_slider,
            self.seq_gap_slider, self.seq_dark_combo, self.seq_dark_density_slider, self.seq_dark_length_slider,
            self.seq_phrase_combo, self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox,
        ]
        set_active([self.seq_enabled_checkbox], shared, "The sequencer needs the shared beat clock.")
        set_active(seq_controls, sequencing, "The sequencer is off." if shared else "The sequencer needs the shared beat clock.")
        if not shared:
            show_note(self.seq_note, "The sequencer places pulses on the shared beat clock's grid - turn on "
                      "'Beat Sync follows the shared clock' in the Rhythm box at the top first.")
        else:
            show_note(self.seq_note, "")

    # -- handlers -----------------------------------------------------------------------

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
        bs.hue_step_deg = self.beat_hue_step_slider.value()
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
        bs.white_pulse_target = self.beat_white_pulse_target_combo.currentText()
        bs.white_pulse_white_brightness = self.beat_white_pulse_white_brightness_slider.value()
        bs.white_pulse_cool_ratio = self.beat_white_pulse_cool_ratio_slider.value()
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
        bsw.flash_brightness = self.bsw_flash_slider.value()
        bsw.sustain_brightness = self.bsw_sustain_slider.value()
        bsw.temp_attack_ms = self.bsw_temp_attack_slider.value()
        bsw.brightness_attack_ms = self.bsw_bright_attack_slider.value()
        bsw.brightness_release_ms = self.bsw_bright_release_slider.value()
        bsw.dark_pulse_probability = self.bsw_dark_prob_slider.value()
        bsw.dark_pulse_duration_ms = self.bsw_dark_duration_slider.value()
        bsw.dark_pulse_depth = self.bsw_dark_depth_slider.value()
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
        # Presets capture the full visualization setup: the color mapping
        # mode's own settings, the Chase overlay's global settings, AND which
        # lamps are in the chase (and in what order) - all of it, so loading
        # a preset later fully restores the look, not just the color mode.
        snapshot = {
            "color_mapping": cfg.color_mapping.to_dict(),
            "chase": cfg.chase.to_dict(),
            "group_switch": cfg.group_switch.to_dict(),
            "rhythm": cfg.rhythm.to_dict(),
            "sequencer": cfg.sequencer.to_dict(),
            "per_lamp_chase_orders": {
                device_id: effect.chase_order
                for device_id, effect in cfg.per_lamp_effects.items()
                if effect.chase_order is not None
            },
        }
        cfg.presets[name.strip()] = snapshot
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

        if "color_mapping" in data:
            self.controller.config.color_mapping = ColorMappingConfig.from_dict(data["color_mapping"])
            self.controller.config.chase = ChaseEffectConfig.from_dict(data.get("chase", {}))
            # Presets saved before these existed keep the current settings.
            if "group_switch" in data:
                self.controller.config.group_switch = GroupSwitchEffectConfig.from_dict(data["group_switch"])
            if "rhythm" in data:
                self.controller.config.rhythm = RhythmConfig.from_dict(data["rhythm"])
            if "sequencer" in data:
                self.controller.config.sequencer = PulseSequencerConfig.from_dict(data["sequencer"])
            for device_id, chase_order in data.get("per_lamp_chase_orders", {}).items():
                self.controller.get_or_create_effect(device_id).chase_order = chase_order
        else:
            # Backward compatibility: presets saved before the Chase overlay
            # existed stored a bare color_mapping dict directly.
            self.controller.config.color_mapping = ColorMappingConfig.from_dict(data)

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
    def _trigger_combo(layout: QVBoxLayout, label: str, value: str) -> QComboBox:
        row = QHBoxLayout()
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
        layout.addLayout(row)
        return combo

    def _build_rhythm_box(self, rh: RhythmConfig) -> QGroupBox:
        box = QGroupBox("Rhythm - shared beat clock")
        layout = QVBoxLayout(box)
        intro = QLabel(
            "One beat detector that every layer can follow, so the flash, the color changes, Chase and "
            "Group Switch all move on the same beats instead of each reacting to different hits. It "
            "locks onto the tempo, ignores off-beat hits (hi-hats, vocals), fills in a missed kick, and "
            "counts bars so slower layers can change on bar starts. Chase / Group Switch follow it with "
            "Speed source 'clock' (8-Band && Per-Lamp tab)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.rhythm_shared_checkbox = QCheckBox("Beat Sync (and Beat Sync White) follow the shared clock")
        self.rhythm_shared_checkbox.setChecked(rh.shared_clock)
        self.rhythm_shared_checkbox.setToolTip(
            "On: Beat Sync flashes on the clock's beats instead of its own detector (whose band/"
            "sensitivity settings are then unused), and its pulse triggers can use accents/bar starts."
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
            "Sensitivity", 1.01, 2.0, rh.sensitivity, decimals=2,
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
        bar_row.addStretch(1)
        layout.addLayout(bar_row)

        self.rhythm_status_label = QLabel()
        layout.addWidget(self.rhythm_status_label)
        self._rhythm_timer = QTimer(self)
        self._rhythm_timer.timeout.connect(self._refresh_rhythm_status)
        self._rhythm_timer.start(200)
        self._refresh_rhythm_status()
        return box

    # -- pulse sequencer ---------------------------------------------------------------------

    def _build_sequencer_box(self, sq: PulseSequencerConfig) -> QGroupBox:
        box = QGroupBox("Pulse sequencer - white and dark pulses on musical positions")
        layout = QVBoxLayout(box)
        intro = QLabel(
            "Instead of rolling a probability on every beat, places the white and dark pulses above "
            "like a lighting operator would: on rhythm patterns in 16th notes (also between the "
            "beats), with the white flash walking from lamp group to lamp group (the Group Switch "
            "groups, 'Effect group' in the Per-Lamp table), phrase fills and a dark breath before each "
            "new phrase, and busier patterns when the music gets louder. Uses the pulse durations, "
            "true-white brightness and cool ratio set above. Needs the shared beat clock (Rhythm box) "
            "- while it isn't locked yet, the per-beat settings above apply."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.seq_enabled_checkbox = QCheckBox("Enabled")
        self.seq_enabled_checkbox.setChecked(sq.enabled)
        self.seq_enabled_checkbox.toggled.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_enabled_checkbox)
        self.seq_note = inactive_note()
        layout.addWidget(self.seq_note)

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
            "around the room. pingpong: back and forth. random: any other group. all: every lamp.",
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
        self.seq_gap_slider = FloatSlider(
            "Min gap per lamp", 100.0, 1000.0, sq.min_group_gap_ms, decimals=0, suffix=" ms",
            tooltip="A lamp never starts two white flashes closer than this. Switching to white and "
            "back takes several commands, so too little here can make lamps lag or get stuck.",
        )
        for combo in (self.seq_white_combo, self.seq_walk_combo):
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
            self.seq_double_slider,
            self.seq_gap_slider,
            self.seq_dark_density_slider,
            self.seq_dark_length_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

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

        self.seq_fills_checkbox = QCheckBox("Fills (denser flashes at the end of each phrase)")
        self.seq_fills_checkbox.setChecked(sq.fills)
        self.seq_accent_checkbox = QCheckBox("Phrase accents (dark breath, then every lamp flashes on the new phrase)")
        self.seq_accent_checkbox.setChecked(sq.phrase_accent)
        self.seq_drop_checkbox = QCheckBox("Follow drops (a quiet-to-loud jump starts a new phrase right there)")
        self.seq_drop_checkbox.setChecked(sq.drop_detection)
        for cb in (self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox):
            cb.toggled.connect(self._on_sequencer_changed)
            layout.addWidget(cb)
        return box

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
        sq.white_density = self.seq_white_density_slider.value()
        sq.double_chance = self.seq_double_slider.value()
        sq.min_group_gap_ms = self.seq_gap_slider.value()
        sq.dark_pattern = self.seq_dark_combo.currentText()
        sq.dark_density = self.seq_dark_density_slider.value()
        sq.dark_length = self.seq_dark_length_slider.value()
        sq.phrase_bars = self.seq_phrase_combo.currentData()
        sq.fills = self.seq_fills_checkbox.isChecked()
        sq.phrase_accent = self.seq_accent_checkbox.isChecked()
        sq.drop_detection = self.seq_drop_checkbox.isChecked()
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
        bar_changed = rh.beats_per_bar != self.rhythm_bpb_spin.value()
        rh.beats_per_bar = self.rhythm_bpb_spin.value()
        if bar_changed:
            # The every-N-beats choices on both settings tabs depend on the bar length.
            self.controller.replace_config_settings()
            return
        self._update_beat_states()
        self.controller.apply_config_changes()

    def _refresh_rhythm_status(self) -> None:
        engine = self.controller.engine
        if not engine.running or not engine.clock_active:
            self.rhythm_status_label.setText("Clock: idle (not running, or nothing is following it)")
            return
        beat = engine.latest_clock_beat
        tempo = f"{beat.bpm:.0f} BPM" if beat.bpm else "finding tempo..."
        lock = "locked" if beat.locked else "not locked"
        bar = self.controller.config.rhythm.beats_per_bar
        text = f"Clock: {tempo}, {lock}, beat {beat.bar_position + 1}/{bar}"
        seq = engine.sequencer_status
        if seq is not None:
            text += f"  |  phrase bar {seq['phrase_bar'] + 1}/{seq['phrase_bars']}, loudness: {seq['level']}"
        self.rhythm_status_label.setText(text)
