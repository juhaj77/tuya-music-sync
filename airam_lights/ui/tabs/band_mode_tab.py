"""8-Band Spectrum mode tab: per-band frequency ranges (one band per lamp by
default), spectrum-mode color parameters, and the per-lamp effects table
(band assignment, phase offset, brightness/saturation/hue/sensitivity
multipliers) that turns a set of identical bulbs into a coordinated
installation instead of identical copies of the same signal.

Split into inner sub-tabs (Bands & Spectrum / Per-Lamp Effects / Chase
Overlay) rather than one long scrolling page - the Chase overlay alone has
~20 controls, which used to force a single giant column everything else sat
below, and the per-lamp table was squeezed down to a sizeHint of just a row
or two (its own inner scrollbar buried inside the page's outer one). Giving
each concern its own page keeps any one page's scroll distance short, and
lets the per-lamp table claim real vertical space the same way the Devices
& Setup tab's lamp list does."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.schema import BandDefinition, PerLampEffect
from ..controller import AppController
from ..widgets.hue_slider import HueSlider
from ..widgets.musical import beat_divisions, choice_combo, inactive_note, set_active, show_note
from ..widgets.param_slider import FloatSlider

_BAND_COLUMNS = ["Name", "Low (Hz)", "High (Hz)"]
_EFFECT_COLUMNS = [
    "Lamp",
    "Band",
    "Phase offset (ms)",
    "Brightness x",
    "Saturation x",
    "Hue offset (deg)",
    "Sensitivity x",
    "Chase order",
    "Chase dwell x",
    "Effect group",
    "True white x",
]
# Header tooltips, same order as _EFFECT_COLUMNS.
_EFFECT_COLUMN_TOOLTIPS = [
    "The lamp's name (set on the Devices tab).",
    "8-Band Spectrum mode only: which frequency band this lamp shows. Auto = assigned in "
    "selection order.",
    "Delays this lamp's reaction by this many milliseconds relative to the music, so lamps "
    "with staggered offsets form a wave. Beat Sync's true-white flash ignores it (always "
    "synchronized).",
    "Multiplies this lamp's final output brightness, after the color mode has done its work "
    "- 0.5 = half as bright, 1.0 = unchanged. Use it to balance a lamp that's physically "
    "brighter/closer than the others.",
    "Multiplies this lamp's color saturation - below 1.0 = more pastel/whiter, above 1.0 = "
    "more vivid (up to fully saturated).",
    "Rotates this lamp's hue around the color wheel by this many degrees, e.g. 180 = always "
    "the opposite color of the rest.",
    "Multiplies the music-driven level going INTO the color mode (input gain), before it's "
    "turned into a color - above 1.0 = this lamp reacts more strongly to quieter sounds and "
    "reaches its peak sooner, below 1.0 = it reacts less. Unlike Brightness x, which just "
    "scales the finished output. (In RGB Frequency / 8-Band modes it's applied together "
    "with the per-band gains.)",
    "This lamp's position in the Chase overlay's rotation, 0-based. Lamps with the same "
    "number light up together as one position. Off = not part of the chase.",
    "How long the Chase highlight lingers at this lamp's position relative to others - "
    "lower it for a position with several lamps (e.g. a multi-spot ceiling fixture) if the "
    "highlight seems to dwell there too long. 1.0 = default.",
    "This lamp's group for the Group Switch overlay (independent of Chase order). Lamps with "
    "the same number switch together. Off = not part of any group.",
    "Multiplies Beat Sync's true-white flash brightness for this lamp - 0.5 = half, 1.0 = "
    "unchanged. Give every lamp in a group the same value to balance groups against each "
    "other (e.g. wall spots vs. ceiling).",
]


def _scroll_page() -> "tuple[QWidget, QVBoxLayout]":
    """A tab page that's just a QScrollArea wrapping a QVBoxLayout - the
    common shell for pages whose content can outgrow the window height.
    Returns (page_widget_to_add_to_the_tab, layout_to_fill_with_content)."""
    page = QWidget()
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    page_layout.addWidget(scroll)
    inner = QWidget()
    scroll.setWidget(inner)
    return page, QVBoxLayout(inner)


class BandModeTab(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sub_tabs = QTabWidget()
        outer.addWidget(sub_tabs)

        bands_page, bands_root = _scroll_page()
        sub_tabs.addTab(bands_page, "Bands && Spectrum")

        effects_page = QWidget()
        effects_root = QVBoxLayout(effects_page)
        sub_tabs.addTab(effects_page, "Per-Lamp Effects")

        chase_page, chase_root = _scroll_page()
        sub_tabs.addTab(chase_page, "Chase Overlay")

        group_switch_page, group_switch_root = _scroll_page()
        sub_tabs.addTab(group_switch_page, "Group Switch")

        # -- band definitions -------------------------------------------------------
        band_box = QGroupBox("8-Band definitions (default: one band per lamp)")
        band_layout = QVBoxLayout(band_box)
        self.band_table = QTableWidget(len(controller.config.bands_8), len(_BAND_COLUMNS))
        self.band_table.setHorizontalHeaderLabels(_BAND_COLUMNS)
        self.band_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # A QTableWidget's sizeHint doesn't grow with its row count, so
        # without this it defaults to showing only ~5 of the 8 bands behind
        # its own inner scrollbar - Expanding + a layout stretch factor
        # below lets it actually claim the page's spare vertical space.
        self.band_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._populate_band_table()
        band_layout.addWidget(self.band_table)
        bands_root.addWidget(band_box, stretch=1)

        # -- spectrum mode color params -----------------------------------------------
        color_box = QGroupBox("Spectrum mode appearance")
        color_layout = QVBoxLayout(color_box)
        spec = controller.config.color_mapping.spectrum
        self.base_hue_slider = HueSlider("Base hue", spec.base_hue_deg)
        self.hue_step_slider = FloatSlider("Hue step across bands", -60.0, 60.0, spec.hue_step_deg, decimals=0, suffix=" deg")
        self.saturation_slider = FloatSlider("Saturation", 0.0, 1.0, spec.saturation)
        self.min_bright_slider = FloatSlider("Min brightness", 0.0, 1.0, spec.min_brightness)
        self.max_bright_slider = FloatSlider("Max brightness", 0.0, 1.0, spec.max_brightness)
        self.spec_sensitivity_slider = FloatSlider("Sensitivity", 0.1, 4.0, spec.sensitivity)
        for w in (
            self.base_hue_slider,
            self.hue_step_slider,
            self.saturation_slider,
            self.min_bright_slider,
            self.max_bright_slider,
            self.spec_sensitivity_slider,
        ):
            w.valueChanged.connect(self._on_spectrum_changed)
            color_layout.addWidget(w)
        self.drive_sat_checkbox = QCheckBox("Band level also modulates saturation")
        self.drive_sat_checkbox.setChecked(spec.drive_saturation_too)
        self.drive_sat_checkbox.toggled.connect(self._on_spectrum_changed)
        color_layout.addWidget(self.drive_sat_checkbox)
        bands_root.addWidget(color_box)

        # -- per-lamp effects -----------------------------------------------------------
        # Own page, given stretch=1 below (not squeezed inside a scroll area
        # alongside everything else) so it actually claims the page's full
        # height, the same way the Devices & Setup tab's lamp list does -
        # showing every lamp at once instead of one row behind an inner
        # scrollbar.
        effects_box = QGroupBox("Per-lamp effects (applies in every mode)")
        effects_layout = QVBoxLayout(effects_box)
        effects_label = QLabel(
            "Set band assignment (8-Band mode), a small delay for wave/chase effects, "
            "per-lamp brightness/saturation/hue/sensitivity multipliers, 'Chase dwell x' "
            "- how long the Chase overlay's highlight lingers at this lamp's chase position "
            "relative to others (1.0 = default; lower it for a position with several lamps "
            "at once, e.g. a multi-spot ceiling fixture, if the highlight feels like it's "
            "dwelling there too long) - and 'Effect group', a separate, independent grouping "
            "used by the Group Switch tab (a lamp can have a Chase order, an Effect group, "
            "both, or neither). 'True white x' multiplies the Beat Sync true-white flash "
            "brightness for this lamp (1.0 = unchanged, 0.5 = half) - give every lamp in a group "
            "the same value to balance e.g. wall spots against the ceiling group."
        )
        effects_label.setWordWrap(True)
        effects_layout.addWidget(effects_label)
        self.effects_table = QTableWidget(0, len(_EFFECT_COLUMNS))
        self.effects_table.setHorizontalHeaderLabels(_EFFECT_COLUMNS)
        for col, tooltip in enumerate(_EFFECT_COLUMN_TOOLTIPS):
            self.effects_table.horizontalHeaderItem(col).setToolTip(tooltip)
        self.effects_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # With this many columns, equal-share Stretch can squeeze a numeric
        # column below the width a spinbox needs to show its up/down
        # buttons - they'd still work via scroll wheel or typing, but be
        # invisible/undiscoverable. Floor every column's width so the
        # buttons always have room; the table scrolls horizontally instead
        # of ever going narrower than that.
        self.effects_table.horizontalHeader().setMinimumSectionSize(70)
        self.effects_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        effects_layout.addWidget(self.effects_table)
        effects_root.addWidget(effects_box, stretch=1)

        # -- chase / rotating light overlay ----------------------------------------------
        chase_box = QGroupBox("Chase / Rotating Light overlay (works on top of ANY mode above)")
        chase_outer_layout = QVBoxLayout(chase_box)
        chase_intro_label = QLabel(
            "Set 'Chase order' (0, 1, 2, ...) on the lamps in the Per-Lamp Effects tab to include "
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

        # -- left column: enable/speed/sync -----------------------------------------
        self.chase_enabled_checkbox = QCheckBox("Enabled")
        self.chase_enabled_checkbox.setChecked(ch.enabled)
        self.chase_enabled_checkbox.setToolTip(
            "Turns the whole Chase overlay on or off - when off, whichever color mode is active "
            "(RGB, HSV, 8-Band, Beat Sync, etc.) shows with no traveling highlight layered on top."
        )
        self.chase_enabled_checkbox.toggled.connect(self._on_chase_changed)
        speed_col.addWidget(self.chase_enabled_checkbox)

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
            "steps on the shared beat clock (Color Mapping tab, Rhythm box) every N beats below - the "
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

        color_mode_row = QHBoxLayout()
        color_mode_row.addWidget(QLabel("Chase color:"))
        self.chase_color_mode_combo = QComboBox()
        self.chase_color_mode_combo.addItems(["custom", "complementary", "hue_shift"])
        self.chase_color_mode_combo.setCurrentText(ch.color_mode)
        self.chase_color_mode_combo.setToolTip(
            "custom: one fixed hue/saturation for the whole highlight. complementary: always the "
            "opposite hue (+180 deg) of whatever that lamp's active mode is already showing. hue_shift: "
            "each chase position gets a progressively different hue (a rainbow trail)."
        )
        self.chase_color_mode_combo.currentTextChanged.connect(self._on_chase_changed)
        color_mode_row.addWidget(self.chase_color_mode_combo)
        color_mode_row.addStretch(1)
        appearance_col.addLayout(color_mode_row)
        color_mode_label = QLabel(
            "custom: a fixed color you set below. complementary: the opposite hue of each lamp's own "
            "color. hue_shift: each chase position shows a different hue (a rainbow trail) starting "
            "from the custom hue below, stepped by 'Hue shift step' per position."
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
            tooltip="Only used by the 'hue_shift' color mode - how far the hue advances from one chase "
            "position to the next, starting from Custom hue above.",
        )
        for w in (self.chase_hue_slider, self.chase_sat_slider, self.chase_hue_shift_slider):
            w.valueChanged.connect(self._on_chase_changed)
            appearance_col.addWidget(w)
        appearance_col.addStretch(1)

        self.chase_beat_low_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_beat_high_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_peak_low_spin.valueChanged.connect(self._on_chase_changed)
        self.chase_peak_high_spin.valueChanged.connect(self._on_chase_changed)

        chase_root.addWidget(chase_box)

        # -- group switch: discrete alternative to the chase overlay above --------------------
        gs_box = QGroupBox("Group Switch (discrete alternative to Chase - no gradient between groups)")
        gs_outer_layout = QVBoxLayout(gs_box)
        gs_intro_label = QLabel(
            "Set 'Effect group' (0, 1, 2, ...) on the lamps in the Per-Lamp Effects tab to include "
            "them here, in that order - a SEPARATE grouping from Chase's 'Chase order' above, so a "
            "lamp can be in the Chase, in a Group Switch group, both, or neither. Unlike Chase, "
            "exactly one group is 'active' at a time and shows the target color at full strength - "
            "every other group is left completely untouched. No width/falloff: switching from one "
            "active group to the next is instant, a hard step rather than a gradient."
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

        # -- left column: enable/speed/sync ------------------------------------------
        self.gs_enabled_checkbox = QCheckBox("Enabled")
        self.gs_enabled_checkbox.setChecked(gs.enabled)
        self.gs_enabled_checkbox.setToolTip(
            "Turns Group Switch on or off - when off, no group is forced 'active' and Chase (if also "
            "enabled) is the only overlay running."
        )
        self.gs_enabled_checkbox.toggled.connect(self._on_group_switch_changed)
        gs_speed_col.addWidget(self.gs_enabled_checkbox)

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
            "switches on the shared beat clock (Color Mapping tab, Rhythm box) every N beats below - "
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
        self.gs_color_mode_combo.addItems(["custom", "complementary", "hue_shift"])
        self.gs_color_mode_combo.setCurrentText(gs.color_mode)
        self.gs_color_mode_combo.setToolTip(
            "custom: one fixed hue/saturation shown on whichever group is active. complementary: always "
            "the opposite hue (+180 deg) of that group's own current color. hue_shift: each group gets a "
            "progressively different hue, so which color shows depends on which group is active."
        )
        self.gs_color_mode_combo.currentTextChanged.connect(self._on_group_switch_changed)
        gs_color_mode_row.addWidget(self.gs_color_mode_combo)
        gs_color_mode_row.addStretch(1)
        gs_appearance_col.addLayout(gs_color_mode_row)
        gs_color_mode_label = QLabel(
            "custom: a fixed color you set below, shown on whichever group is currently active. "
            "complementary: the opposite hue of that group's own current color. hue_shift: each "
            "group shows a different hue starting from the custom hue below, stepped by 'Hue shift "
            "step' per group - so which color flashes on depends on which group is active."
        )
        gs_color_mode_label.setWordWrap(True)
        gs_appearance_col.addWidget(gs_color_mode_label)

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
            tooltip="Only used by the 'hue_shift' color mode - how far the hue advances from one group "
            "to the next, starting from Custom hue above.",
        )
        for w in (self.gs_hue_slider, self.gs_sat_slider, self.gs_hue_shift_slider):
            w.valueChanged.connect(self._on_group_switch_changed)
            gs_appearance_col.addWidget(w)
        gs_appearance_col.addStretch(1)

        self.gs_beat_low_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_beat_high_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_peak_low_spin.valueChanged.connect(self._on_group_switch_changed)
        self.gs_peak_high_spin.valueChanged.connect(self._on_group_switch_changed)

        group_switch_root.addWidget(gs_box)

        self._populated_lamps = None
        controller.lampsChanged.connect(self._populate_effects_table)
        self._populate_effects_table()
        self._update_overlay_states()

    # -- band definitions --------------------------------------------------------------

    def _populate_band_table(self) -> None:
        self.band_table.blockSignals(True)
        for row, band in enumerate(self.controller.config.bands_8):
            name_item = QTableWidgetItem(band.name)
            self.band_table.setItem(row, 0, name_item)

            low_spin = QSpinBox()
            low_spin.setRange(1, 20000)
            low_spin.setValue(int(band.low_hz))
            low_spin.valueChanged.connect(self._on_band_table_changed)
            self.band_table.setCellWidget(row, 1, low_spin)

            high_spin = QSpinBox()
            high_spin.setRange(1, 20000)
            high_spin.setValue(int(band.high_hz))
            high_spin.valueChanged.connect(self._on_band_table_changed)
            self.band_table.setCellWidget(row, 2, high_spin)
        self.band_table.blockSignals(False)
        self.band_table.itemChanged.connect(self._on_band_table_changed)

    def _on_band_table_changed(self, *_args) -> None:
        bands = []
        for row in range(self.band_table.rowCount()):
            name_item = self.band_table.item(row, 0)
            low_widget = self.band_table.cellWidget(row, 1)
            high_widget = self.band_table.cellWidget(row, 2)
            if name_item is None or low_widget is None or high_widget is None:
                continue
            bands.append(BandDefinition(name=name_item.text() or f"Band {row+1}", low_hz=low_widget.value(), high_hz=high_widget.value()))
        if bands:
            self.controller.config.bands_8 = bands
            self.controller.apply_config_changes()

    # -- spectrum params ------------------------------------------------------------------

    def _on_spectrum_changed(self, *_args) -> None:
        spec = self.controller.config.color_mapping.spectrum
        spec.base_hue_deg = self.base_hue_slider.value()
        spec.hue_step_deg = self.hue_step_slider.value()
        spec.saturation = self.saturation_slider.value()
        spec.min_brightness = self.min_bright_slider.value()
        spec.max_brightness = self.max_bright_slider.value()
        spec.sensitivity = self.spec_sensitivity_slider.value()
        spec.drive_saturation_too = self.drive_sat_checkbox.isChecked()
        self.controller.apply_config_changes()

    # -- per-lamp effects table -------------------------------------------------------------

    def _populate_effects_table(self) -> None:
        devices = list(self.controller.lamp_manager.devices.values())
        # lampsChanged also fires every few seconds from the periodic lamp
        # status refresh - rebuilding the cell widgets then would destroy a
        # spinbox mid-edit (focus lost, typed text gone), so only rebuild
        # when the lamp list itself has actually changed.
        lamps = [(dev.config.id, dev.config.name) for dev in devices]
        if lamps == self._populated_lamps:
            return
        self._populated_lamps = lamps
        self.effects_table.setRowCount(len(devices))
        num_bands = len(self.controller.config.bands_8)

        for row, dev in enumerate(devices):
            effect = self.controller.get_or_create_effect(dev.config.id)

            name_item = QTableWidgetItem(dev.config.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.effects_table.setItem(row, 0, name_item)

            band_combo = QComboBox()
            band_combo.addItem("Auto", None)
            for i in range(num_bands):
                band_combo.addItem(f"Band {i+1}", i)
            if effect.band_index is not None:
                idx = band_combo.findData(effect.band_index)
                if idx >= 0:
                    band_combo.setCurrentIndex(idx)
            band_combo.currentIndexChanged.connect(lambda _i, d=dev.config.id, c=band_combo: self._on_effect_changed(d, "band_index", c.currentData()))
            self.effects_table.setCellWidget(row, 1, band_combo)

            self._add_spin(row, 2, dev.config.id, "phase_offset_ms", -500.0, 500.0, effect.phase_offset_ms, step=5.0)
            self._add_spin(row, 3, dev.config.id, "brightness_mult", 0.0, 2.0, effect.brightness_mult)
            self._add_spin(row, 4, dev.config.id, "saturation_mult", 0.0, 2.0, effect.saturation_mult)
            self._add_spin(row, 5, dev.config.id, "hue_offset_deg", -180.0, 180.0, effect.hue_offset_deg, step=5.0)
            self._add_spin(row, 6, dev.config.id, "sensitivity_mult", 0.0, 2.0, effect.sensitivity_mult)

            chase_spin = QSpinBox()
            chase_spin.setRange(-1, max(7, len(devices) - 1))
            chase_spin.setSpecialValueText("Off")
            chase_spin.setValue(effect.chase_order if effect.chase_order is not None else -1)
            chase_spin.valueChanged.connect(
                lambda v, d=dev.config.id: self._on_effect_changed(d, "chase_order", (v if v >= 0 else None))
            )
            self.effects_table.setCellWidget(row, 7, chase_spin)

            self._add_spin(row, 8, dev.config.id, "chase_dwell_mult", 0.1, 10.0, effect.chase_dwell_mult)

            group_spin = QSpinBox()
            group_spin.setRange(-1, max(7, len(devices) - 1))
            group_spin.setSpecialValueText("Off")
            group_spin.setValue(effect.effect_group if effect.effect_group is not None else -1)
            group_spin.valueChanged.connect(
                lambda v, d=dev.config.id: self._on_effect_changed(d, "effect_group", (v if v >= 0 else None))
            )
            self.effects_table.setCellWidget(row, 9, group_spin)

            self._add_spin(
                row, 10, dev.config.id, "white_pulse_brightness_mult", 0.0, 2.0, effect.white_pulse_brightness_mult
            )

    def _add_spin(
        self, row: int, col: int, device_id: str, attr: str, lo: float, hi: float, value: float, step: float = 0.05
    ) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        spin.setValue(value)
        # Explicit (Qt's default already, but stated here so it's obvious and
        # never silently lost) - these are always visibly clickable, not just
        # scroll-wheel/type-only.
        spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
        spin.valueChanged.connect(lambda v, d=device_id, a=attr: self._on_effect_changed(d, a, v))
        self.effects_table.setCellWidget(row, col, spin)

    def _on_effect_changed(self, device_id: str, attr: str, value) -> None:
        effect = self.controller.get_or_create_effect(device_id)
        setattr(effect, attr, value)
        self.controller.apply_config_changes()

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
            "bar (Color Mapping tab, Rhythm box).",
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

    def _update_overlay_states(self) -> None:
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
                "clock": "Speed source 'clock': moves on the shared beat clock, set up in the Color Mapping "
                "tab's Rhythm box - this overlay's own detector settings are greyed out.",
            }
            show_note(w("mode_note"), notes.get(mode, ""))

            color_mode = w("color_mode_combo").currentText()
            color_reason = f"{name}'s color mode is '{color_mode}'."
            set_active([w("hue_slider"), w("sat_slider")], color_mode in ("custom", "hue_shift"), color_reason)
            set_active([w("hue_shift_slider")], color_mode == "hue_shift", color_reason)

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
        ch.color_mode = self.chase_color_mode_combo.currentText()
        ch.custom_hue_deg = self.chase_hue_slider.value()
        ch.custom_saturation = self.chase_sat_slider.value()
        ch.hue_shift_step_deg = self.chase_hue_shift_slider.value()
        self._update_overlay_states()
        self.controller.apply_config_changes()

    # -- group switch overlay --------------------------------------------------------------

    def _on_group_switch_changed(self, *_args) -> None:
        gs = self.controller.config.group_switch
        gs.enabled = self.gs_enabled_checkbox.isChecked()
        gs.speed_rotations_per_s = self.gs_speed_slider.value()
        gs.reverse = self.gs_reverse_checkbox.isChecked()
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
        self._update_overlay_states()
        self.controller.apply_config_changes()
