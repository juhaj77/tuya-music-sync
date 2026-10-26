"""Chase and Group Switch: the two overlays that run on top of the colour
mode - Chase (a highlight travelling round the lamps in their Chase order)
and Group Switch (one lamp group active at a time) - each a collapsible
section, on the Global Effects tab. Which lamps take part, and where, is set
per lamp on the Devices & Setup tab (Chase order, Effect group)."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...dsp.hue_step import SOURCES as HUE_STEP_SOURCES
from ..controller import AppController
from ..widgets.hue_slider import HueSlider
from ..widgets.musical import beat_divisions, choice_combo, inactive_note, set_active, show_note
from ..widgets.param_slider import FloatSlider
from ..widgets.section import CollapsibleSection


class OverlaysPanel(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # -- chase / rotating light overlay ----------------------------------------------
        self.chase_box = chase_box = CollapsibleSection(
            "Chase · rotating light", key="overlays/chase", checkable=True,
            checked=controller.config.chase.enabled,
            tooltip="A moving highlight travels through the lamps in their Chase order, on top of whatever "
            "colour mode is active. Off: the mode shows with no traveling highlight layered on top.",
        )
        self.chase_white_note = chase_box.note
        chase_outer_layout = chase_box.content_layout()
        chase_intro_label = QLabel(
            "Set 'Chase order' (0, 1, 2, ...) on the lamps on the Devices & Setup tab to include "
            "them in the rotation, in that order - a moving highlight travels through them, "
            "overlaid on whatever the active mode is already showing."
        )
        chase_intro_label.setWordWrap(True)
        chase_outer_layout.addWidget(chase_intro_label)
        # Two side-by-side columns instead of one long one - roughly halves
        # how far you need to scroll to reach any given control here. Each
        # column is a real QWidget (not a bare layout handed to addLayout())
        # - word-wrapped QLabels below need their own widget's resize events
        # to recompute wrapped height correctly at the column's actual
        # width; nested directly as bare layouts, they can mis-measure and
        # clip their last line instead of wrapping to it.
        columns_row = QHBoxLayout()
        chase_outer_layout.addLayout(columns_row)
        speed_widget = QWidget()
        appearance_widget = QWidget()
        speed_col = QVBoxLayout(speed_widget)
        appearance_col = QVBoxLayout(appearance_widget)
        speed_col.setContentsMargins(0, 0, 0, 0)
        appearance_col.setContentsMargins(0, 0, 0, 0)
        columns_row.addWidget(speed_widget, 1)
        columns_row.addWidget(appearance_widget, 1)
        ch = controller.config.chase

        # -- left column: speed/sync --------------------------------------------------
        self.chase_enabled_checkbox = chase_box.toggle
        self.chase_enabled_checkbox.toggled.connect(self._on_chase_changed)

        rotators_row = QHBoxLayout()
        rotators_row.addWidget(QLabel("Number of rotators:"))
        self.chase_num_rotators_spin = QSpinBox()
        self.chase_num_rotators_spin.setRange(1, 64)  # generous cap, not tied to any specific lamp count
        self.chase_num_rotators_spin.setValue(ch.num_rotators)
        self.chase_num_rotators_spin.setToolTip(
            "How many highlights travel the loop at once, evenly spaced and always moving together - "
            "2 puts them on opposite sides, 3 a third apart, etc."
        )
        self.chase_num_rotators_spin.valueChanged.connect(self._on_chase_changed)
        rotators_row.addWidget(self.chase_num_rotators_spin)
        rotators_row.addStretch(1)
        speed_col.addLayout(rotators_row)
        rotators_label = QLabel(
            "How many highlights travel the loop at once, evenly spaced and moving together - "
            "2 puts them on opposite sides of the loop, 3 spaces them a third of the way apart, etc."
        )
        rotators_label.setWordWrap(True)
        speed_col.addWidget(rotators_label)

        self.chase_speed_slider = FloatSlider(
            "Speed (constant)", 0.02, 5.0, ch.speed_rotations_per_s, decimals=3, suffix=" rotations/s",
            tooltip="Only used when Speed source is 'off' - full laps around all chase-ordered lamps "
            "per second, ignoring the audio entirely.",
        )
        self.chase_speed_slider.valueChanged.connect(self._on_chase_changed)
        speed_col.addWidget(self.chase_speed_slider)
        speed_label = QLabel(
            "This is full laps around all the chase-ordered lamps per second - e.g. 0.5 = one full "
            "loop every 2 seconds, regardless of how many lamps are in the chase."
        )
        speed_label.setWordWrap(True)
        speed_col.addWidget(speed_label)

        self.chase_reverse_checkbox = QCheckBox("Reverse direction")
        self.chase_reverse_checkbox.setChecked(ch.reverse)
        self.chase_reverse_checkbox.setToolTip("Flips which way the highlight travels around the chase order.")
        self.chase_reverse_checkbox.toggled.connect(self._on_chase_changed)
        speed_col.addWidget(self.chase_reverse_checkbox)

        sync_mode_row = QHBoxLayout()
        sync_mode_row.addWidget(QLabel("Speed source:"))
        self.chase_sync_mode_combo = QComboBox()
        self.chase_sync_mode_combo.addItems(["off", "beat", "intensity_peak", "clock"])
        self.chase_sync_mode_combo.setCurrentText(ch.sync_mode)
        self.chase_sync_mode_combo.setToolTip(
            "off: use the constant Speed slider above. beat/intensity_peak: ignore that slider and "
            "instead advance only when the matching detector below fires a hit."
        )
        self.chase_sync_mode_combo.currentTextChanged.connect(self._on_chase_changed)
        sync_mode_row.addWidget(self.chase_sync_mode_combo)
        sync_mode_row.addStretch(1)
        speed_col.addLayout(sync_mode_row)
        sync_mode_label = QLabel(
            "off: constant speed above, ignoring audio. beat: sits still and only advances on a "
            "detected bass-drum-style hit. intensity_peak: sits still and only advances on ANY sudden "
            "loudness spike (better for tracks without a strong, steady beat). Both hit-based modes "
            "never drift on their own between hits - they move only with the actual rhythm. clock: "
            "steps on the shared beat clock (the Rhythm box above) every N beats below - the "
            "same beats every other clock-driven layer uses."
        )
        sync_mode_label.setWordWrap(True)
        speed_col.addWidget(sync_mode_label)

        self.chase_multiplier_slider = FloatSlider(
            "Steps per hit", 0.125, 8.0, ch.beat_multiplier, decimals=3, suffix="x",
            tooltip="Only used when Speed source is 'beat' or 'intensity_peak' - how many lamp-steps "
            "the highlight advances on each detected hit.",
        )
        self.chase_multiplier_slider.valueChanged.connect(self._on_chase_changed)
        speed_col.addWidget(self.chase_multiplier_slider)
        multiplier_label = QLabel(
            "1 = one lamp-step per hit, 2 = twice as fast (two steps per hit), 0.5 = half as fast "
            "(one step every two hits). Applies to both beat and intensity_peak modes."
        )
        multiplier_label.setWordWrap(True)
        speed_col.addWidget(multiplier_label)
        self.chase_clock_every_combo, self.chase_clock_steps_spin = self._clock_controls(
            speed_col, ch.clock_every_n_beats, ch.beat_multiplier, "lamp-steps", self._on_chase_changed
        )
        self.chase_mode_note = inactive_note()
        speed_col.addWidget(self.chase_mode_note)

        beat_detect_row = QHBoxLayout()
        beat_detect_row.addWidget(QLabel("Beat detection band:"))
        self.chase_beat_low_spin = QSpinBox()
        self.chase_beat_low_spin.setRange(20, 20000)
        self.chase_beat_low_spin.setSuffix(" Hz")
        self.chase_beat_low_spin.setValue(int(ch.beat_detect_low_hz))
        self.chase_beat_low_spin.setToolTip(
            "Which frequencies count as a 'beat' at all, for the Chase overlay's own independent beat "
            "detector (separate from Beat Sync mode's) - only used when Speed source is 'beat'."
        )
        beat_detect_row.addWidget(self.chase_beat_low_spin)
        beat_detect_row.addWidget(QLabel("-"))
        self.chase_beat_high_spin = QSpinBox()
        self.chase_beat_high_spin.setRange(20, 20000)
        self.chase_beat_high_spin.setSuffix(" Hz")
        self.chase_beat_high_spin.setValue(int(ch.beat_detect_high_hz))
        self.chase_beat_high_spin.setToolTip(self.chase_beat_low_spin.toolTip())
        beat_detect_row.addWidget(self.chase_beat_high_spin)
        beat_detect_row.addStretch(1)
        speed_col.addLayout(beat_detect_row)

        self.chase_beat_sensitivity_slider = FloatSlider(
            "Beat sensitivity", 1.05, 4.0, ch.beat_sensitivity, decimals=2,
            tooltip="A beat fires when energy spikes above this multiple of the recent average - "
            "LOWER value = more sensitive (triggers more easily, on smaller hits), HIGHER value = less "
            "sensitive (only sharp, obvious hits register). This is a threshold, not a volume knob, so "
            "it's the opposite direction you might expect from the word 'sensitivity'.",
        )
        self.chase_beat_min_interval_slider = FloatSlider(
            "Beat min interval", 30.0, 1000.0, ch.beat_min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two triggered beats - stops one sustained hit from "
            "re-advancing the highlight many times in quick succession.",
        )

        peak_detect_row = QHBoxLayout()
        peak_detect_row.addWidget(QLabel("Intensity-peak detection band:"))
        self.chase_peak_low_spin = QSpinBox()
        self.chase_peak_low_spin.setRange(20, 20000)
        self.chase_peak_low_spin.setSuffix(" Hz")
        self.chase_peak_low_spin.setValue(int(ch.peak_detect_low_hz))
        self.chase_peak_low_spin.setToolTip(
            "Which frequencies feed the 'intensity_peak' detector - broaden this (e.g. to the whole "
            "audible range) to react to any sudden loud moment, not just bass. Only used when Speed "
            "source is 'intensity_peak'."
        )
        peak_detect_row.addWidget(self.chase_peak_low_spin)
        peak_detect_row.addWidget(QLabel("-"))
        self.chase_peak_high_spin = QSpinBox()
        self.chase_peak_high_spin.setRange(20, 20000)
        self.chase_peak_high_spin.setSuffix(" Hz")
        self.chase_peak_high_spin.setValue(int(ch.peak_detect_high_hz))
        self.chase_peak_high_spin.setToolTip(self.chase_peak_low_spin.toolTip())
        peak_detect_row.addWidget(self.chase_peak_high_spin)
        peak_detect_row.addStretch(1)
        speed_col.addLayout(peak_detect_row)

        self.chase_peak_sensitivity_slider = FloatSlider(
            "Peak sensitivity", 1.05, 4.0, ch.peak_sensitivity, decimals=2,
            tooltip="Same threshold logic as Beat sensitivity above, just applied to the "
            "intensity-peak detector: LOWER = more sensitive/more triggers, HIGHER = fewer, only the "
            "most obvious loudness spikes.",
        )
        self.chase_peak_min_interval_slider = FloatSlider(
            "Peak min interval", 30.0, 1000.0, ch.peak_min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two triggered peaks - stops one loud passage from "
            "re-advancing the highlight many times in quick succession.",
        )
        for w in (
            self.chase_beat_sensitivity_slider,
            self.chase_beat_min_interval_slider,
            self.chase_peak_sensitivity_slider,
            self.chase_peak_min_interval_slider,
        ):
            w.valueChanged.connect(self._on_chase_changed)
            speed_col.addWidget(w)
        speed_col.addStretch(1)

        # -- right column: width/intensity/falloff/color appearance -----------------
        self.chase_width_slider = FloatSlider(
            "Highlight width", 0.2, 8.0, ch.width, decimals=2, suffix=" lamps",
            tooltip="How many lamp-positions the glow spans, with soft falloff. Scale with your lamp "
            "count: lower (0.5-0.8) for a crisp single-dot look with few lamps, higher for a chase of "
            "12+ lamps so it doesn't look like a single lamp popping on and off.",
        )
        self.chase_intensity_slider = FloatSlider(
            "Intensity (brightness boost)", 0.0, 8.0, ch.intensity, decimals=2,
            tooltip="A brightness BOOST multiplier on top of whatever the active mode already computed "
            "for that lamp - never an independent/fixed brightness. A lamp the active mode has dimmed "
            "to black (e.g. a Beat Sync dark pulse) stays black regardless of this value.",
        )
        for w in (self.chase_width_slider, self.chase_intensity_slider):
            w.valueChanged.connect(self._on_chase_changed)
            appearance_col.addWidget(w)
        width_intensity_label = QLabel(
            "Lower width = a crisper, single-lamp-like highlight (easier to see as 'one dot moving'). "
            "Intensity multiplies the lamp's own current brightness at the highlight's peak (never adds "
            "light where the active mode says black - e.g. a Beat Sync dark pulse stays dark)."
        )
        width_intensity_label.setWordWrap(True)
        appearance_col.addWidget(width_intensity_label)

        chase_curve_row = QHBoxLayout()
        chase_curve_row.addWidget(QLabel("Falloff curve:"))
        self.chase_falloff_curve_combo = QComboBox()
        self.chase_falloff_curve_combo.addItems(["linear", "bezier"])
        self.chase_falloff_curve_combo.setCurrentText(ch.falloff_curve)
        self.chase_falloff_curve_combo.setToolTip(
            "linear: constant-rate falloff, peak color is a single fleeting instant. bezier: an eased "
            "S-curve that dwells near the peak (and the background) longer - try this if the highlight "
            "feels like it flies by too quickly."
        )
        self.chase_falloff_curve_combo.currentTextChanged.connect(self._on_chase_changed)
        chase_curve_row.addWidget(self.chase_falloff_curve_combo)
        chase_curve_row.addStretch(1)
        appearance_col.addLayout(chase_curve_row)
        curve_label = QLabel(
            "linear: constant-rate falloff - the peak color is a single fleeting instant. bezier: an "
            "eased S-curve that dwells near the peak (and near the background) longer - try this if "
            "the highlight feels like it flies by too quickly."
        )
        curve_label.setWordWrap(True)
        appearance_col.addWidget(curve_label)

        self.chase_switch_fade_checkbox = QCheckBox("Soft steps (fade each lamp's color change as the highlight moves)")
        self.chase_switch_fade_checkbox.setChecked(ch.switch_fade)
        self.chase_switch_fade_checkbox.setToolTip(
            "With only a handful of lamps the highlight can't really glide: whatever the Width and "
            "Falloff curve, each lamp's color changes in visible jumps as the highlight moves on - and "
            "jumps outright when it steps on a beat. On: every lamp's color fades to its new value over "
            "the Switch fade time below instead. Off: as before."
        )
        self.chase_switch_fade_checkbox.toggled.connect(self._on_chase_changed)
        appearance_col.addWidget(self.chase_switch_fade_checkbox)
        self.chase_switch_fade_slider = FloatSlider(
            "Switch fade", 20.0, 1000.0, ch.switch_fade_ms, decimals=0, suffix=" ms",
            tooltip="How long each lamp takes to fade to its new color when the highlight moves (with Soft "
            "steps on) - the same kind of time setting as Group Switch's Switch fade and Beat Sync's Hue "
            "snap speed, so the same value gives the same feel. Longer = softer, with a longer trail.",
        )
        self.chase_switch_fade_slider.valueChanged.connect(self._on_chase_changed)
        appearance_col.addWidget(self.chase_switch_fade_slider)

        color_mode_row = QHBoxLayout()
        color_mode_row.addWidget(QLabel("Chase color:"))
        self.chase_color_mode_combo = QComboBox()
        self.chase_color_mode_combo.addItems(["custom", "complementary", "hue_shift", "hue_shift_relative"])
        self.chase_color_mode_combo.setCurrentText(ch.color_mode)
        self.chase_color_mode_combo.setToolTip(
            "custom: one fixed hue/saturation for the whole highlight. complementary: always the "
            "opposite hue (+180 deg) of whatever that lamp's active mode is already showing. hue_shift: "
            "each chase position gets a progressively different hue (a rainbow trail). hue_shift_relative: "
            "each lamp's own hue turned by 'Hue shift step'."
        )
        self.chase_color_mode_combo.currentTextChanged.connect(self._on_chase_changed)
        color_mode_row.addWidget(self.chase_color_mode_combo)
        color_mode_row.addStretch(1)
        appearance_col.addLayout(color_mode_row)
        color_mode_label = QLabel(
            "custom: a fixed color you set below. complementary: the opposite hue of each lamp's own "
            "color. hue_shift: each chase position shows a different hue (a rainbow trail) starting "
            "from the custom hue below, stepped by 'Hue shift step' per position. hue_shift_relative: "
            "each lamp's own color, its hue turned by 'Hue shift step'."
        )
        color_mode_label.setWordWrap(True)
        appearance_col.addWidget(color_mode_label)

        self.chase_hue_slider = HueSlider("Custom hue", ch.custom_hue_deg)
        self.chase_hue_slider.setToolTip(
            "Used by 'custom' (the whole highlight's fixed color) and as the starting hue for "
            "'hue_shift'. For the clearest effect, pick something far from your mode's usual palette."
        )
        self.chase_sat_slider = FloatSlider(
            "Custom saturation", 0.0, 1.0, ch.custom_saturation,
            tooltip="Saturation used by the 'custom' color mode only.",
        )
        self.chase_hue_shift_slider = FloatSlider(
            "Hue shift step (for 'hue_shift')", 1.0, 180.0, ch.hue_shift_step_deg, decimals=1, suffix=" deg",
            tooltip="Only used by the 'hue_shift' color modes. hue_shift: how far the hue advances from one "
            "chase position to the next, starting from Custom hue above. hue_shift_relative: how far the "
            "highlight turns each lamp's own hue.",
        )
        for w in (self.chase_hue_slider, self.chase_sat_slider, self.chase_hue_shift_slider):
            w.valueChanged.connect(self._on_chase_changed)
            appearance_col.addWidget(w)

        (
            self.chase_hue_step_source_combo,
            self.chase_hue_step_min_slider,
            self.chase_hue_step_max_slider,
            self.chase_hue_step_status_label,
        ) = self._hue_step_controls(appearance_col, ch, self._on_chase_changed)
        appearance_col.addStretch(1)

        self.chase_beat_low_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_beat_high_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_peak_low_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_peak_high_spin.valueChanged.connect(self._on_chase_changed)

        root.addWidget(chase_box)

        # -- group switch: discrete alternative to the chase overlay above --------------------
        self.gs_box = gs_box = CollapsibleSection(
            "Group Switch · one group at a time", key="overlays/group_switch", checkable=True,
            checked=controller.config.group_switch.enabled,
            tooltip="A discrete alternative to Chase: the lamps are grouped by their Effect group, and one "
            "group at a time is active with a hard, instant switch. Off: no group is forced active and "
            "Chase (if on) is the only overlay running.",
        )
        self.gs_white_note = gs_box.note
        gs_outer_layout = gs_box.content_layout()
        gs_intro_label = QLabel(
            "Set 'Effect group' (0, 1, 2, ...) on the lamps on the Devices & Setup tab to include "
            "them here, in that order - a SEPARATE grouping from Chase's 'Chase order' above, so a "
            "lamp can be in the Chase, in a Group Switch group, both, or neither. Unlike Chase, "
            "exactly one group is 'active' at a time and shows the target color at full strength - "
            "every other group is left completely untouched. No width/falloff: switching from one "
            "active group to the next is instant, a hard step rather than a gradient. (With 'Fade "
            "across groups' the color is spread over all the groups in steps instead.)"
        )
        gs_intro_label.setWordWrap(True)
        gs_outer_layout.addWidget(gs_intro_label)

        gs_columns_row = QHBoxLayout()
        gs_outer_layout.addLayout(gs_columns_row)
        gs_speed_widget = QWidget()
        gs_appearance_widget = QWidget()
        gs_speed_col = QVBoxLayout(gs_speed_widget)
        gs_appearance_col = QVBoxLayout(gs_appearance_widget)
        gs_speed_col.setContentsMargins(0, 0, 0, 0)
        gs_appearance_col.setContentsMargins(0, 0, 0, 0)
        gs_columns_row.addWidget(gs_speed_widget, 1)
        gs_columns_row.addWidget(gs_appearance_widget, 1)
        gs = controller.config.group_switch

        # -- left column: speed/sync ----------------------------------------------------
        self.gs_enabled_checkbox = gs_box.toggle
        self.gs_enabled_checkbox.toggled.connect(self._on_group_switch_changed)

        self.gs_speed_slider = FloatSlider(
            "Speed (constant)", 0.02, 5.0, gs.speed_rotations_per_s, decimals=3, suffix=" switches/s",
            tooltip="Only used when Speed source is 'off' - full loops through all groups per second, "
            "ignoring the audio entirely.",
        )
        self.gs_speed_slider.valueChanged.connect(self._on_group_switch_changed)
        gs_speed_col.addWidget(self.gs_speed_slider)
        gs_speed_label = QLabel(
            "This is full loops through all groups per second - e.g. 0.5 = one full loop every 2 "
            "seconds, regardless of how many groups there are."
        )
        gs_speed_label.setWordWrap(True)
        gs_speed_col.addWidget(gs_speed_label)

        self.gs_reverse_checkbox = QCheckBox("Reverse direction")
        self.gs_reverse_checkbox.setChecked(gs.reverse)
        self.gs_reverse_checkbox.setToolTip("Flips which way the active group advances through the group order.")
        self.gs_reverse_checkbox.toggled.connect(self._on_group_switch_changed)
        gs_speed_col.addWidget(self.gs_reverse_checkbox)

        gs_active_row = QHBoxLayout()
        gs_active_row.addWidget(QLabel("Active groups at once:"))
        self.gs_num_active_spin = QSpinBox()
        self.gs_num_active_spin.setRange(1, 16)
        self.gs_num_active_spin.setValue(gs.num_active_groups)
        self.gs_num_active_spin.setToolTip(
            "How many groups show the group color at once, evenly spaced through the group order - "
            "like Chase's Number of rotators. With 4 groups, 2 = every other group: groups 1+3, then "
            "2+4. White pulses that follow the group land on all the active groups."
        )
        self.gs_num_active_spin.valueChanged.connect(self._on_group_switch_changed)
        gs_active_row.addWidget(self.gs_num_active_spin)
        gs_active_row.addStretch(1)
        gs_speed_col.addLayout(gs_active_row)

        gs_sync_mode_row = QHBoxLayout()
        gs_sync_mode_row.addWidget(QLabel("Speed source:"))
        self.gs_sync_mode_combo = QComboBox()
        self.gs_sync_mode_combo.addItems(["off", "beat", "intensity_peak", "clock"])
        self.gs_sync_mode_combo.setCurrentText(gs.sync_mode)
        self.gs_sync_mode_combo.setToolTip(
            "off: use the constant Speed slider above. beat/intensity_peak: ignore that slider and "
            "instead switch groups only when the matching detector below fires a hit."
        )
        self.gs_sync_mode_combo.currentTextChanged.connect(self._on_group_switch_changed)
        gs_sync_mode_row.addWidget(self.gs_sync_mode_combo)
        gs_sync_mode_row.addStretch(1)
        gs_speed_col.addLayout(gs_sync_mode_row)
        gs_sync_mode_label = QLabel(
            "off: constant speed above, ignoring audio. beat: sits still and only switches on a "
            "detected bass-drum-style hit. intensity_peak: sits still and only switches on ANY sudden "
            "loudness spike (better for tracks without a strong, steady beat). Both hit-based modes "
            "never switch on their own between hits - they move only with the actual rhythm. clock: "
            "switches on the shared beat clock (the Rhythm box above) every N beats below - "
            "e.g. 4 = once per bar."
        )
        gs_sync_mode_label.setWordWrap(True)
        gs_speed_col.addWidget(gs_sync_mode_label)

        self.gs_multiplier_slider = FloatSlider(
            "Groups per hit", 0.125, 8.0, gs.beat_multiplier, decimals=3, suffix="x",
            tooltip="Only used when Speed source is 'beat' or 'intensity_peak' - how many groups the "
            "active switch advances on each detected hit.",
        )
        self.gs_multiplier_slider.valueChanged.connect(self._on_group_switch_changed)
        gs_speed_col.addWidget(self.gs_multiplier_slider)
        gs_multiplier_label = QLabel(
            "1 = advance one group per hit, 2 = twice as fast (two groups per hit), 0.5 = half as "
            "fast (one group every two hits). Applies to both beat and intensity_peak modes."
        )
        gs_multiplier_label.setWordWrap(True)
        gs_speed_col.addWidget(gs_multiplier_label)
        self.gs_clock_every_combo, self.gs_clock_steps_spin = self._clock_controls(
            gs_speed_col, gs.clock_every_n_beats, gs.beat_multiplier, "groups", self._on_group_switch_changed
        )
        self.gs_mode_note = inactive_note()
        gs_speed_col.addWidget(self.gs_mode_note)

        gs_beat_detect_row = QHBoxLayout()
        gs_beat_detect_row.addWidget(QLabel("Beat detection band:"))
        self.gs_beat_low_spin = QSpinBox()
        self.gs_beat_low_spin.setRange(20, 20000)
        self.gs_beat_low_spin.setSuffix(" Hz")
        self.gs_beat_low_spin.setValue(int(gs.beat_detect_low_hz))
        self.gs_beat_low_spin.setToolTip(
            "Which frequencies count as a 'beat' at all, for Group Switch's own independent beat "
            "detector (separate from Chase's and from Beat Sync mode's) - only used when Speed source "
            "is 'beat'."
        )
        gs_beat_detect_row.addWidget(self.gs_beat_low_spin)
        gs_beat_detect_row.addWidget(QLabel("-"))
        self.gs_beat_high_spin = QSpinBox()
        self.gs_beat_high_spin.setRange(20, 20000)
        self.gs_beat_high_spin.setSuffix(" Hz")
        self.gs_beat_high_spin.setValue(int(gs.beat_detect_high_hz))
        self.gs_beat_high_spin.setToolTip(self.gs_beat_low_spin.toolTip())
        gs_beat_detect_row.addWidget(self.gs_beat_high_spin)
        gs_beat_detect_row.addStretch(1)
        gs_speed_col.addLayout(gs_beat_detect_row)

        self.gs_beat_sensitivity_slider = FloatSlider(
            "Beat sensitivity", 1.05, 4.0, gs.beat_sensitivity, decimals=2,
            tooltip="A beat fires when energy spikes above this multiple of the recent average - "
            "LOWER value = more sensitive (triggers more easily, on smaller hits), HIGHER value = less "
            "sensitive (only sharp, obvious hits register). This is a threshold, not a volume knob, so "
            "it's the opposite direction you might expect from the word 'sensitivity'.",
        )
        self.gs_beat_min_interval_slider = FloatSlider(
            "Beat min interval", 30.0, 1000.0, gs.beat_min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two triggered beats - stops one sustained hit from "
            "re-switching the active group many times in quick succession.",
        )

        gs_peak_detect_row = QHBoxLayout()
        gs_peak_detect_row.addWidget(QLabel("Intensity-peak detection band:"))
        self.gs_peak_low_spin = QSpinBox()
        self.gs_peak_low_spin.setRange(20, 20000)
        self.gs_peak_low_spin.setSuffix(" Hz")
        self.gs_peak_low_spin.setValue(int(gs.peak_detect_low_hz))
        self.gs_peak_low_spin.setToolTip(
            "Which frequencies feed the 'intensity_peak' detector - broaden this (e.g. to the whole "
            "audible range) to react to any sudden loud moment, not just bass. Only used when Speed "
            "source is 'intensity_peak'."
        )
        gs_peak_detect_row.addWidget(self.gs_peak_low_spin)
        gs_peak_detect_row.addWidget(QLabel("-"))
        self.gs_peak_high_spin = QSpinBox()
        self.gs_peak_high_spin.setRange(20, 20000)
        self.gs_peak_high_spin.setSuffix(" Hz")
        self.gs_peak_high_spin.setValue(int(gs.peak_detect_high_hz))
        self.gs_peak_high_spin.setToolTip(self.gs_peak_low_spin.toolTip())
        gs_peak_detect_row.addWidget(self.gs_peak_high_spin)
        gs_peak_detect_row.addStretch(1)
        gs_speed_col.addLayout(gs_peak_detect_row)

        self.gs_peak_sensitivity_slider = FloatSlider(
            "Peak sensitivity", 1.05, 4.0, gs.peak_sensitivity, decimals=2,
            tooltip="Same threshold logic as Beat sensitivity above, just applied to the "
            "intensity-peak detector: LOWER = more sensitive/more triggers, HIGHER = fewer, only the "
            "most obvious loudness spikes.",
        )
        self.gs_peak_min_interval_slider = FloatSlider(
            "Peak min interval", 30.0, 1000.0, gs.peak_min_interval_ms, decimals=0, suffix=" ms",
            tooltip="Minimum time between two triggered peaks - stops one loud passage from "
            "re-switching the active group many times in quick succession.",
        )
        for w in (
            self.gs_beat_sensitivity_slider,
            self.gs_beat_min_interval_slider,
            self.gs_peak_sensitivity_slider,
            self.gs_peak_min_interval_slider,
        ):
            w.valueChanged.connect(self._on_group_switch_changed)
            gs_speed_col.addWidget(w)
        gs_speed_col.addStretch(1)

        # -- right column: intensity/color appearance --------------------------------
        self.gs_intensity_slider = FloatSlider(
            "Intensity (brightness boost)", 0.0, 8.0, gs.intensity, decimals=2,
            tooltip="A brightness BOOST multiplier applied to the active group's lamps, on top of "
            "whatever the active mode already computed - never an independent/fixed brightness. A lamp "
            "the active mode has dimmed to black (e.g. a Beat Sync dark pulse) stays black regardless "
            "of this value.",
        )
        self.gs_intensity_slider.valueChanged.connect(self._on_group_switch_changed)
        gs_appearance_col.addWidget(self.gs_intensity_slider)
        gs_intensity_label = QLabel(
            "Multiplies the lamp's own current brightness on the active group (never adds light "
            "where the active mode says black - e.g. a Beat Sync dark pulse stays dark)."
        )
        gs_intensity_label.setWordWrap(True)
        gs_appearance_col.addWidget(gs_intensity_label)

        gs_color_mode_row = QHBoxLayout()
        gs_color_mode_row.addWidget(QLabel("Group color:"))
        self.gs_color_mode_combo = QComboBox()
        self.gs_color_mode_combo.addItems(["custom", "complementary", "hue_shift", "hue_shift_relative"])
        self.gs_color_mode_combo.setCurrentText(gs.color_mode)
        self.gs_color_mode_combo.setToolTip(
            "custom: one fixed hue/saturation shown on whichever group is active. complementary: always "
            "the opposite hue (+180 deg) of that group's own current color. hue_shift: each group gets a "
            "progressively different hue, so which color shows depends on which group is active. "
            "hue_shift_relative: each lamp's own hue turned by 'Hue shift step'."
        )
        self.gs_color_mode_combo.currentTextChanged.connect(self._on_group_switch_changed)
        gs_color_mode_row.addWidget(self.gs_color_mode_combo)
        gs_color_mode_row.addStretch(1)
        gs_appearance_col.addLayout(gs_color_mode_row)
        gs_color_mode_label = QLabel(
            "custom: a fixed color you set below, shown on whichever group is currently active. "
            "complementary: the opposite hue of that group's own current color. hue_shift: each "
            "group shows a different hue starting from the custom hue below, stepped by 'Hue shift "
            "step' per group - so which color flashes on depends on which group is active. "
            "hue_shift_relative: each lamp's own color (with Chase's color on it), its hue turned by "
            "'Hue shift step'."
        )
        gs_color_mode_label.setWordWrap(True)
        gs_appearance_col.addWidget(gs_color_mode_label)

        self.gs_fade_checkbox = QCheckBox("Fade across groups (own color -> group color in steps)")
        self.gs_fade_checkbox.setChecked(gs.fade_across_groups)
        self.gs_fade_checkbox.setToolTip(
            "Off: only the active group shows the group color, the others keep their own. On: the color "
            "fades across all the groups in equal steps - the active group shows the full group color, "
            "the group before it a step less, and so on back to the lamps' own color. With 3 groups and "
            "'complementary': own color -> halfway there -> the opposite color; with more groups, "
            "smaller steps. The ramp moves along with the active group. Needs at least 3 groups (with 2 "
            "there is nothing in between). The brightness boost fades the same way. With several active "
            "groups each one gets its own ramp, back to the active group behind it. White pulses that "
            "follow the group still land on the active groups only."
        )
        self.gs_fade_checkbox.toggled.connect(self._on_group_switch_changed)
        gs_appearance_col.addWidget(self.gs_fade_checkbox)

        self.gs_switch_fade_slider = FloatSlider(
            "Switch fade", 0.0, 1000.0, gs.switch_fade_ms, decimals=0, suffix=" ms",
            tooltip="How the colors change when the active group moves on. 0 = a hard, instant step (as "
            "before). Above 0 = a soft switch: every group's color glides to its new place over about "
            "this long - the same kind of time setting as Beat Sync's Hue snap speed, so the same value "
            "gives the same feel. Works with and without 'Fade across groups'. White pulses that follow "
            "the group still jump with the active group.",
        )
        self.gs_switch_fade_slider.valueChanged.connect(self._on_group_switch_changed)
        gs_appearance_col.addWidget(self.gs_switch_fade_slider)

        self.gs_hue_slider = HueSlider("Custom hue", gs.custom_hue_deg)
        self.gs_hue_slider.setToolTip(
            "Used by 'custom' (the fixed color shown on the active group) and as the starting hue for "
            "'hue_shift'."
        )
        self.gs_sat_slider = FloatSlider(
            "Custom saturation", 0.0, 1.0, gs.custom_saturation,
            tooltip="Saturation used by the 'custom' color mode only.",
        )
        self.gs_hue_shift_slider = FloatSlider(
            "Hue shift step (for 'hue_shift')", 1.0, 180.0, gs.hue_shift_step_deg, decimals=1, suffix=" deg",
            tooltip="Only used by the 'hue_shift' color modes. hue_shift: how far the hue advances from one "
            "group to the next, starting from Custom hue above. hue_shift_relative: how far the active "
            "group turns each lamp's own hue.",
        )
        for w in (self.gs_hue_slider, self.gs_sat_slider, self.gs_hue_shift_slider):
            w.valueChanged.connect(self._on_group_switch_changed)
            gs_appearance_col.addWidget(w)
        (
            self.gs_hue_step_source_combo,
            self.gs_hue_step_min_slider,
            self.gs_hue_step_max_slider,
            self.gs_hue_step_status_label,
        ) = self._hue_step_controls(gs_appearance_col, gs, self._on_group_switch_changed)
        gs_appearance_col.addStretch(1)

        self.gs_beat_low_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_beat_high_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_peak_low_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_peak_high_spin.valueChanged.connect(self._on_group_switch_changed)

        root.addWidget(gs_box)

        self.update_states()

        self._hue_step_timer = QTimer(self)
        self._hue_step_timer.timeout.connect(self._refresh_hue_step_status)
        self._hue_step_timer.start(200)

    @staticmethod
    def _hue_step_controls(layout: QVBoxLayout, cfg, on_change):
        """Chase's and Group Switch's 'Hue shift step from' row, its Step
        min/max sliders and the status line under them."""
        row = QHBoxLayout()
        row.addWidget(QLabel("Hue shift step from:"))
        combo = QComboBox()
        combo.addItems(list(HUE_STEP_SOURCES))
        combo.setCurrentText(cfg.hue_step_source)
        combo.setToolTip(
            "fixed: the Hue shift step above. The others let the music set the step, between Step min "
            "and Step max below, every moment: intensity = how loud it is right now (wider in a chorus, "
            "narrower in a quiet part); brightness = bass-heavy vs bright sound (follows which "
            "instruments play); intensity_brightness = both averaged; pitch = the note that stands out, "
            "by how much tension it has in the song's key (the key note itself = Step min, notes "
            "foreign to the key = Step max). When no note stands out clearly enough (drums, noise, "
            "atonal passages), pitch glides over to intensity_brightness and back - no switching. "
            "Each source follows the music's own recent range, so quiet and loud songs both use the "
            "whole min-max range."
        )
        combo.currentTextChanged.connect(on_change)
        row.addWidget(combo)
        row.addStretch(1)
        layout.addLayout(row)
        step_min = FloatSlider(
            "Step min", 0.0, 180.0, cfg.hue_step_min_deg, decimals=1, suffix=" deg",
            tooltip="The step when the music source is at its lowest (quiet, dark sound, the key note).",
        )
        step_max = FloatSlider(
            "Step max", 0.0, 180.0, cfg.hue_step_max_deg, decimals=1, suffix=" deg",
            tooltip="The step when the music source is at its highest (loud, bright sound, notes foreign "
            "to the key). May be below Step min to turn the mapping around.",
        )
        for w in (step_min, step_max):
            w.valueChanged.connect(on_change)
            layout.addWidget(w)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        return combo, step_min, step_max, status

    def _clock_controls(self, layout: QVBoxLayout, every: int, multiplier: float, unit: str, on_change):
        """Speed source 'clock': how often (a fixed musical division, so moves
        always land on the same place in the bar) and how far (whole steps -
        a fraction would park the highlight between two lamps)."""
        row = QHBoxLayout()
        row.addWidget(QLabel("Clock: move"))
        combo = choice_combo(
            beat_divisions(self.controller.config.rhythm.beats_per_bar), every,
            "Only used when Speed source is 'clock' - moves on the shared beat clock, counted from the "
            "bar start, so every move lands on the same place in the bar. The choices follow Beats per "
            "bar (the Rhythm box above).",
        )
        combo.currentIndexChanged.connect(on_change)
        row.addWidget(combo)
        row.addWidget(QLabel("by"))
        spin = QSpinBox()
        spin.setRange(1, 8)
        spin.setValue(max(1, int(round(multiplier))))
        spin.setSuffix(f" {unit}")
        spin.setToolTip(
            f"Only used when Speed source is 'clock' - how many {unit} each move advances. Whole steps "
            "only: with the clock, a half step would leave the highlight halfway between two lamps."
        )
        spin.valueChanged.connect(on_change)
        row.addWidget(spin)
        row.addStretch(1)
        layout.addLayout(row)
        return combo, spin

    def update_states(self) -> None:
        """Grey out whatever the chosen Speed source / color mode doesn't use."""
        for prefix, name in (("chase", "Chase"), ("gs", "Group Switch")):
            w = lambda n: getattr(self, f"{prefix}_{n}")  # noqa: E731
            mode = w("sync_mode_combo").currentText()
            reason = f"{name}'s Speed source is '{mode}'."
            set_active([w("speed_slider")], mode == "off", reason)
            set_active([w("multiplier_slider")], mode in ("beat", "intensity_peak"), reason)
            set_active(
                [w("beat_low_spin"), w("beat_high_spin"), w("beat_sensitivity_slider"), w("beat_min_interval_slider")],
                mode == "beat", reason,
            )
            set_active(
                [w("peak_low_spin"), w("peak_high_spin"), w("peak_sensitivity_slider"), w("peak_min_interval_slider")],
                mode == "intensity_peak", reason,
            )
            set_active([w("clock_every_combo"), w("clock_steps_spin")], mode == "clock", reason)
            notes = {
                "off": "Speed source 'off': only the constant Speed is used - the hit, detector and clock "
                "settings are greyed out.",
                "beat": "Speed source 'beat': moves on this overlay's own beat detector (the Beat detection "
                "settings). Constant speed, peak and clock settings are greyed out.",
                "intensity_peak": "Speed source 'intensity_peak': moves on loudness peaks (the Peak detection "
                "settings). Constant speed, beat and clock settings are greyed out.",
                "clock": "Speed source 'clock': moves on the shared beat clock, set up in the Rhythm box "
                "above - this overlay's own detector settings are greyed out.",
            }
            show_note(w("mode_note"), notes.get(mode, ""))

            color_mode = w("color_mode_combo").currentText()
            color_reason = f"{name}'s color mode is '{color_mode}'."
            set_active([w("hue_slider"), w("sat_slider")], color_mode in ("custom", "hue_shift"), color_reason)
            shifting = color_mode in ("hue_shift", "hue_shift_relative")
            source = w("hue_step_source_combo").currentText()
            set_active([w("hue_step_source_combo")], shifting, color_reason)
            if shifting:
                set_active([w("hue_shift_slider")], source == "fixed", f"Hue shift step comes from '{source}'.")
                set_active(
                    [w("hue_step_min_slider"), w("hue_step_max_slider")],
                    source != "fixed", "Hue shift step is 'fixed' (the slider above).",
                )
            else:
                set_active(
                    [w("hue_shift_slider"), w("hue_step_min_slider"), w("hue_step_max_slider")], False, color_reason
                )
        set_active([self.chase_switch_fade_slider], self.chase_switch_fade_checkbox.isChecked(), "Soft steps is off.")

        # The overlays recolor RGB - Beat Sync White drives the lamps' white
        # mode instead, so there they don't run at all.
        white = self.controller.config.color_mapping.mode == "beat_sync_white"
        for box, note in ((self.chase_box, self.chase_white_note), (self.gs_box, self.gs_white_note)):
            set_active([box], not white, "The color mode is Beat Sync White - the overlays only recolor RGB.")
            show_note(
                note,
                "Not used in Beat Sync White: that mode drives the lamps' white mode (brightness + color "
                "temperature), and this overlay recolors RGB. It runs in every other color mode."
                if white else "",
            )

    # -- chase overlay --------------------------------------------------------------------

    def _on_chase_changed(self, *_args) -> None:
        ch = self.controller.config.chase
        ch.enabled = self.chase_enabled_checkbox.isChecked()
        ch.num_rotators = self.chase_num_rotators_spin.value()
        ch.speed_rotations_per_s = self.chase_speed_slider.value()
        ch.reverse = self.chase_reverse_checkbox.isChecked()
        ch.sync_mode = self.chase_sync_mode_combo.currentText()
        if ch.sync_mode == "clock":
            ch.beat_multiplier = float(self.chase_clock_steps_spin.value())
        else:
            ch.beat_multiplier = self.chase_multiplier_slider.value()
        ch.clock_every_n_beats = self.chase_clock_every_combo.currentData()
        ch.beat_detect_low_hz = self.chase_beat_low_spin.value()
        ch.beat_detect_high_hz = self.chase_beat_high_spin.value()
        ch.beat_sensitivity = self.chase_beat_sensitivity_slider.value()
        ch.beat_min_interval_ms = self.chase_beat_min_interval_slider.value()
        ch.peak_detect_low_hz = self.chase_peak_low_spin.value()
        ch.peak_detect_high_hz = self.chase_peak_high_spin.value()
        ch.peak_sensitivity = self.chase_peak_sensitivity_slider.value()
        ch.peak_min_interval_ms = self.chase_peak_min_interval_slider.value()
        ch.width = self.chase_width_slider.value()
        ch.intensity = self.chase_intensity_slider.value()
        ch.falloff_curve = self.chase_falloff_curve_combo.currentText()
        ch.switch_fade = self.chase_switch_fade_checkbox.isChecked()
        ch.switch_fade_ms = self.chase_switch_fade_slider.value()
        ch.color_mode = self.chase_color_mode_combo.currentText()
        ch.custom_hue_deg = self.chase_hue_slider.value()
        ch.custom_saturation = self.chase_sat_slider.value()
        ch.hue_shift_step_deg = self.chase_hue_shift_slider.value()
        ch.hue_step_source = self.chase_hue_step_source_combo.currentText()
        ch.hue_step_min_deg = self.chase_hue_step_min_slider.value()
        ch.hue_step_max_deg = self.chase_hue_step_max_slider.value()
        self.update_states()
        self.controller.apply_config_changes()

    def _refresh_hue_step_status(self) -> None:
        engine = self.controller.engine
        for cfg, label, status, name in (
            (self.controller.config.chase, self.chase_hue_step_status_label, engine.hue_step_status, "Chase"),
            (
                self.controller.config.group_switch, self.gs_hue_step_status_label, engine.group_hue_step_status,
                "Group Switch",
            ),
        ):
            if cfg.hue_step_source == "fixed" or cfg.color_mode not in ("hue_shift", "hue_shift_relative"):
                label.setText("")
                continue
            if status is None or not engine.running:
                label.setText(f"Step now: - (runs while the visualizer and {name} are on)")
                continue
            text = f"Step now: {status['step_deg']:.0f} deg"
            pitch = status.get("pitch")
            if pitch is not None:
                text += (
                    f"  |  note {pitch.note_name} in {pitch.key_name}, note clear {pitch.confidence:.0%}, "
                    f"notes moving {pitch.variety:.0%} -> pitch share {status['pitch_weight']:.0%} "
                    "(the rest from intensity+brightness)"
                )
            label.setText(text)

    # -- group switch overlay --------------------------------------------------------------

    def _on_group_switch_changed(self, *_args) -> None:
        gs = self.controller.config.group_switch
        gs.enabled = self.gs_enabled_checkbox.isChecked()
        gs.speed_rotations_per_s = self.gs_speed_slider.value()
        gs.reverse = self.gs_reverse_checkbox.isChecked()
        gs.num_active_groups = self.gs_num_active_spin.value()
        gs.sync_mode = self.gs_sync_mode_combo.currentText()
        if gs.sync_mode == "clock":
            gs.beat_multiplier = float(self.gs_clock_steps_spin.value())
        else:
            gs.beat_multiplier = self.gs_multiplier_slider.value()
        gs.clock_every_n_beats = self.gs_clock_every_combo.currentData()
        gs.beat_detect_low_hz = self.gs_beat_low_spin.value()
        gs.beat_detect_high_hz = self.gs_beat_high_spin.value()
        gs.beat_sensitivity = self.gs_beat_sensitivity_slider.value()
        gs.beat_min_interval_ms = self.gs_beat_min_interval_slider.value()
        gs.peak_detect_low_hz = self.gs_peak_low_spin.value()
        gs.peak_detect_high_hz = self.gs_peak_high_spin.value()
        gs.peak_sensitivity = self.gs_peak_sensitivity_slider.value()
        gs.peak_min_interval_ms = self.gs_peak_min_interval_slider.value()
        gs.intensity = self.gs_intensity_slider.value()
        gs.color_mode = self.gs_color_mode_combo.currentText()
        gs.custom_hue_deg = self.gs_hue_slider.value()
        gs.custom_saturation = self.gs_sat_slider.value()
        gs.hue_shift_step_deg = self.gs_hue_shift_slider.value()
        gs.hue_step_source = self.gs_hue_step_source_combo.currentText()
        gs.hue_step_min_deg = self.gs_hue_step_min_slider.value()
        gs.hue_step_max_deg = self.gs_hue_step_max_slider.value()
        gs.fade_across_groups = self.gs_fade_checkbox.isChecked()
        gs.switch_fade_ms = self.gs_switch_fade_slider.value()
        self.update_states()
        self.controller.apply_config_changes()
