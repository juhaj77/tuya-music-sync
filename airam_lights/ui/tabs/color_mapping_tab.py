"""Color Mapping tab: full detail controls for every colour mode - one page
per mode - plus the global response curve, lamp transitions and presets.

What every mode shares - the shared beat clock (Rhythm), Chase and Group
Switch, the Dark and White pulses and the Pulse sequencer - is on the Global
Effects tab."""
from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
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
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionTabWidgetFrame,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.builtin_presets import BUILTIN_PRESETS, apply_builtin_preset
from ...dsp.hue_step import SOURCES as HUE_STEP_SOURCES
from ...config.user_presets import apply_preset, snapshot_preset
from ...config.schema import LAMP_TRANSITIONS
from ..controller import AppController
from ..widgets.hue_slider import HueSlider
from ..widgets.musical import beat_divisions, choice_combo, inactive_note, labeled_combo, set_active, show_note
from ..quick_settings import (
    BAND_CHORD_TURN_TIP,
    BAND_FOCUS_TIP,
    BAND_RANGE_MEMORY_TIP,
    BAND_RELEASE_TIP,
    QUICK_SETTINGS,
    get_value,
    link_slider,
)
from ..widgets.param_slider import FloatSlider
from ..widgets.section import CollapsibleSection
from .pulses_panel import sequencer_state


class _ModeTabs(QTabWidget):
    """The mode pages' tab widget, as tall as the page shown. Qt's own is as
    tall as its tallest page, leaving a short page (Beat Sync with its
    sections closed) a long empty space above the Global box. The pages not
    shown have an Ignored size policy (ColorMappingTab._fit_to_page), so the
    minimum size hint already counts the page shown alone; the size hint
    and the height for a width (the word-wrapped notes) are swapped for the
    page shown's here."""

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        page = self.currentWidget()
        if page is not None:
            # The tab bar and frame, plus the room the page asks for.
            hint.setHeight(
                self.minimumSizeHint().height() - page.minimumSizeHint().height() + page.sizeHint().height()
            )
        return hint

    def heightForWidth(self, width: int) -> int:
        height = super().heightForWidth(width)
        page = self.currentWidget()
        if page is None or height < 0:
            return height
        # Qt's asks every page (QStackedLayout.heightForWidth) and takes the
        # tallest: swap that for the page shown's.
        opt = QStyleOptionTabWidgetFrame()
        self.initStyleOption(opt)
        opt.state = QStyle.State_None
        page_width = width - self.style().sizeFromContents(QStyle.CT_TabWidget, opt, QSize(0, 0), self).width()
        page_height = page.heightForWidth(page_width) if page.hasHeightForWidth() else page.sizeHint().height()
        return height - page.parentWidget().heightForWidth(page_width) + page_height


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

        shared_note = QLabel(
            "What every mode shares - the shared beat clock (Rhythm), Chase and Group Switch, the Dark and "
            "White pulses and the Pulse sequencer - is on the Global Effects tab."
        )
        shared_note.setWordWrap(True)
        root.addWidget(shared_note)

        sub_tabs = _ModeTabs()
        root.addWidget(sub_tabs)
        self.sub_tabs = sub_tabs

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
        sub_tabs.addTab(hsv_widget, "HSV Music")

        # -- Band Colors mode -----------------------------------------------------------
        band_widget = self._build_band_colors_page(cm.band_colors)
        sub_tabs.addTab(band_widget, "Band Colors")

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
            "clock (the Rhythm box on the Global Effects tab).",
        ).content_layout()
        detect_row = QHBoxLayout()
        detect_row.addWidget(QLabel("Beat detection band:"))
        self.beat_low_spin = QSpinBox()
        self.beat_low_spin.setRange(20, 20000)
        self.beat_low_spin.setSuffix(" Hz")
        self.beat_low_spin.setValue(int(bs.detect_low_hz))
        self.beat_low_spin.setToolTip(
            "Which frequencies count as a 'beat' at all - every hue/brightness snap below, and both "
            "pulse types (Global Effects tab), only ever happen when a hit is detected somewhere in this range."
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
            "you want the white pulse (Global Effects tab) to have hits of its own to react to."
        )
        beat_band_note.setWordWrap(True)
        beat_layout.addWidget(beat_band_note)
        self.beat_detect_note = inactive_note()
        beat_layout.addWidget(self.beat_detect_note)

        self.beat_sensitivity_slider = FloatSlider(
            "Beat detector sensitivity", 1.05, 4.0, bs.sensitivity, decimals=2,
            tooltip="Higher = only very sharp, obvious hits register as a beat at all, so every hue/"
            "brightness snap and both pulse types fire less often but more confidently.",
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
        self._spectrum_timer.timeout.connect(self._refresh_band_status)
        self._spectrum_timer.start(200)

        hue_every_row = QHBoxLayout()
        hue_every_row.addWidget(QLabel("Change hue:"))
        self.beat_hue_every_combo = choice_combo(
            beat_divisions(controller.config.rhythm.beats_per_bar), bs.hue_every_n_beats,
            "Every beat still flashes, but the color only changes on these beats. With the shared beat "
            "clock this is counted from the bar start, so e.g. 'every bar' changes color exactly on "
            "beat 1. The choices follow Beats per bar in the Rhythm box (Global Effects tab).",
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
            "pastel/washed out) - this is the saturation both the dark and white pulses "
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

        self.beat_low_spin.valueChanged.connect(self._on_beat_changed)
        self.beat_high_spin.valueChanged.connect(self._on_beat_changed)

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
            "clock (Global Effects tab, Rhythm box). Higher = only sharp, obvious hits count as a beat.",
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
            "it doesn't flicker warm-cool on every hit. With the shared beat clock and Lock to tempo (Global "
            "Effects tab, Rhythm box) it's counted from the bar start, so the change lands on the strong beats: "
            "every 2 beats = beats 1 and 3, every bar = the downbeat. The choices follow Beats per bar there.",
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
            "beats. While the Pulse sequencer runs, it places the dark pulses instead - shaped by the Dark "
            "pulses section, as in every mode (both on the Global Effects tab)."
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
            "hsv_music": hsv_widget,
            "band_colors": band_widget,
        }
        self._link_quick_settings()
        # A stretch at the end of each page, so its rows stay together at the
        # top whatever height the tab widget gives it.
        for index in range(sub_tabs.count()):
            page_layout = sub_tabs.widget(index).layout()
            if isinstance(page_layout, QBoxLayout):
                page_layout.addStretch(1)
        # Only the page shown sets the tab widget's height (see _ModeTabs), and
        # it never grows taller than that page.
        sub_tabs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        sub_tabs.currentChanged.connect(self._fit_to_page)
        self._fit_to_page(sub_tabs.currentIndex())

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
            "Reshapes the raw 0..1 audio level BEFORE it becomes brightness, in HSV Music and Band Colors "
            "only (Beat Sync, Peak Flash, and Beat Sync White don't use this at all - they react to "
            "individual detected hits instead of a continuous level). linear: unchanged. log: boosts quiet detail, so quieter passages still "
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

        self.transitions_combo = labeled_combo(
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
            "Sets the colour mode, Beat Sync, the shared beat clock, the pulses and the pulse sequencer "
            "(strobes and sweeps too), Chase and Group Switch together (all but the first two on the "
            "Global Effects tab). Leaves your lamp groups, Chase width/intensity/rotators, Group Switch "
            "intensity and active groups, what the white walk steps through, beat detection, lead time, "
            "true-white brightness/cool ratio, strobe brightness and max rate, Beat Sync's fade and "
            "hue glide, and your Band Colors palette as they are."
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
        why - so changing one never looks ignored: Beat Sync's own and Beat
        Sync White's. (What they depend on - the beat clock, the sequencer -
        is on the Global Effects tab, so this runs again whenever this tab is
        shown.)"""
        cfg = self.controller.config
        bs, rh = cfg.color_mapping.beat_sync, cfg.rhythm
        shared = rh.shared_clock

        clock_reason = "Beat Sync follows the shared beat clock (Global Effects tab, Rhythm box)."
        set_active(
            [self.beat_low_spin, self.beat_high_spin, self.beat_sensitivity_slider,
             self.beat_min_interval_slider, self.beat_min_energy_slider],
            not shared, clock_reason,
        )
        show_note(self.beat_detect_note, (
            "Beat Sync follows the shared beat clock, so its own detection settings here are greyed "
            "out - tune detection in the Rhythm box on the Global Effects tab instead."
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

        self._update_bsw_states()

    def _update_bsw_states(self) -> None:
        """Beat Sync White's own settings: its detector while it doesn't follow
        the clock, its per-beat dark pulses while the sequencer doesn't place
        them, and what 'Change temperature' is counted from."""
        cfg = self.controller.config
        rh, bsw = cfg.rhythm, cfg.color_mapping.beat_sync_white
        set_active(
            [self.bsw_low_spin, self.bsw_high_spin, self.bsw_sensitivity_slider, self.bsw_min_interval_slider,
             self.bsw_min_energy_slider],
            not rh.shared_clock,
            "Beat Sync White follows the shared beat clock (Global Effects tab, Rhythm box).",
        )
        _, sequencing, _ = sequencer_state(cfg, "beat_sync_white")
        set_active(
            [self.bsw_dark_prob_slider, self.bsw_dark_duration_slider, self.bsw_dark_depth_slider], not sequencing,
            "The Pulse sequencer places Beat Sync White's dark pulses - shaped by the Dark pulses section "
            "(both on the Global Effects tab).",
        )
        show_note(self.bsw_dark_seq_note, (
            "The Pulse sequencer places the dark pulses here (shaped by the Dark pulses section - both on the "
            "Global Effects tab), so these "
            "per-beat ones are greyed out - they apply again while the sequencer is off or the beat clock hasn't "
            "locked yet."
        ) if sequencing else "")
        if bsw.temp_every_n_beats > 1 and not (rh.shared_clock and rh.tempo_lock):
            show_note(self.bsw_temp_every_note, (
                "Counted from the bar start only with the shared beat clock and Lock to tempo (Global Effects "
                "tab, Rhythm box) - "
                "now it counts the detected beats, so the changes can drift off the strong beats."
            ))
        else:
            show_note(self.bsw_temp_every_note, "")

    def _add_smoothing_sliders(self, layout) -> "tuple[FloatSlider, FloatSlider]":
        """HSV Music's Attack/Release (the level smoothing), as on the Quick
        Controls."""
        sliders = []
        for setting in (s for s in QUICK_SETTINGS["hsv_music"] if ".smoothing." in s.path):
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
            "color_mapping.hsv.sensitivity": [self.hsv_sensitivity_slider],
            "color_mapping.smoothing.attack_ms": [self.hsv_attack_slider],
            "color_mapping.smoothing.release_ms": [self.hsv_release_slider],
            "color_mapping.band_colors.focus": [self.band_focus_slider],
            "color_mapping.band_colors.chord_turn_deg": [self.band_chord_turn_slider],
            "color_mapping.band_colors.range_memory_s": [self.band_range_memory_slider],
            "color_mapping.band_colors.release_ms": [self.band_release_slider],
        }
        for path, sliders in links.items():
            for slider in sliders:
                link_slider(self.controller, path, slider)

    def show_mode(self, mode: str) -> None:
        """Brings up `mode`'s page (the Quick Controls' "more settings" link)."""
        page = self._mode_pages.get(mode)
        if page is not None:
            self.sub_tabs.setCurrentWidget(page)

    def _fit_to_page(self, index: int) -> None:
        """Sizes the mode pages' tab widget to page `index` alone: the others'
        size hints are ignored while they're hidden."""
        for i in range(self.sub_tabs.count()):
            policy = QSizePolicy.Preferred if i == index else QSizePolicy.Ignored
            self.sub_tabs.widget(i).setSizePolicy(policy, policy)
        self.sub_tabs.updateGeometry()

    def showEvent(self, event) -> None:
        # The beat clock and the sequencer are set on the Global Effects tab - catch up with them here.
        super().showEvent(event)
        self._update_beat_states()

    # -- handlers -----------------------------------------------------------------------

    def _on_smoothing_changed(self, name: str, value: float) -> None:
        setattr(self.controller.config.color_mapping.smoothing, name, value)
        self.controller.apply_config_changes()

    def _build_band_colors_page(self, bc) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Bass, mids and treble each have a colour, and the lamps show the band playing out the most right "
            "now: a kick turns them to the bass colour, a chord or a voice to the mid colour, a hi-hat to the "
            "treble colour, two bands at once to the colour between theirs. Each band is measured against its "
            "own recent range, not against the others or an absolute level - so even when everything is loud, "
            "each band still swings through its whole range and the colour keeps moving."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        split_row = QHBoxLayout()
        split_row.addWidget(QLabel("Bass / mid split:"))
        self.band_bass_mid_spin = QSpinBox()
        self.band_bass_mid_spin.setRange(60, 1000)
        self.band_bass_mid_spin.setSuffix(" Hz")
        self.band_bass_mid_spin.setValue(int(bc.bass_mid_hz))
        self.band_bass_mid_spin.setToolTip(
            "Where the bass band ends and the mids begin (the bass starts at 30 Hz). Around 150-250 Hz keeps the "
            "kick drum and the bass line in the bass band."
        )
        split_row.addWidget(self.band_bass_mid_spin)
        split_row.addSpacing(16)
        split_row.addWidget(QLabel("Mid / treble split:"))
        self.band_mid_treble_spin = QSpinBox()
        self.band_mid_treble_spin.setRange(500, 10000)
        self.band_mid_treble_spin.setSingleStep(100)
        self.band_mid_treble_spin.setSuffix(" Hz")
        self.band_mid_treble_spin.setValue(int(bc.mid_treble_hz))
        self.band_mid_treble_spin.setToolTip(
            "Where the mids end and the treble begins (the treble goes up to 12 kHz). Around 3-5 kHz leaves the "
            "voices and chords in the mids and the hi-hats and cymbals in the treble."
        )
        split_row.addWidget(self.band_mid_treble_spin)
        split_row.addStretch(1)
        layout.addLayout(split_row)
        for spin in (self.band_bass_mid_spin, self.band_mid_treble_spin):
            spin.valueChanged.connect(self._on_band_colors_changed)

        self.band_bass_hue_slider = HueSlider("Bass colour", bc.bass_hue_deg)
        self.band_mid_hue_slider = HueSlider("Mid colour", bc.mid_hue_deg)
        self.band_treble_hue_slider = HueSlider("Treble colour", bc.treble_hue_deg)
        self.band_focus_slider = FloatSlider("Color focus", 0.0, 1.0, bc.focus, decimals=2, tooltip=BAND_FOCUS_TIP)
        self.band_chord_turn_slider = FloatSlider(
            "Chord turn", 0.0, 360.0, bc.chord_turn_deg, decimals=0, suffix=" deg", tooltip=BAND_CHORD_TURN_TIP,
        )
        self.band_range_memory_slider = FloatSlider(
            "Range memory", 0.5, 20.0, bc.range_memory_s, decimals=1, suffix=" s", tooltip=BAND_RANGE_MEMORY_TIP,
        )
        self.band_attack_slider = FloatSlider(
            "Band attack", 1.0, 500.0, bc.attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast a band's activity rises at its hit - low = the colour snaps to the band at once.",
        )
        self.band_release_slider = FloatSlider(
            "Band release", 10.0, 2000.0, bc.release_ms, decimals=0, suffix=" ms", tooltip=BAND_RELEASE_TIP,
        )
        self.band_saturation_slider = FloatSlider("Saturation", 0.0, 1.0, bc.saturation, decimals=2)
        self.band_bright_min_slider = FloatSlider(
            "Brightness min", 0.0, 1.0, bc.brightness_min, decimals=2,
            tooltip="How bright the lamps stay between the hits, and in silence.",
        )
        self.band_bright_max_slider = FloatSlider(
            "Brightness max", 0.0, 1.0, bc.brightness_max, decimals=2,
            tooltip="How bright a band's hit makes them (the brightness follows the most active band; the Global "
            "box's Response curve shapes it).",
        )
        for w in (
            self.band_bass_hue_slider, self.band_mid_hue_slider, self.band_treble_hue_slider, self.band_focus_slider,
            self.band_chord_turn_slider, self.band_range_memory_slider, self.band_attack_slider,
            self.band_release_slider, self.band_saturation_slider, self.band_bright_min_slider,
            self.band_bright_max_slider,
        ):
            w.valueChanged.connect(self._on_band_colors_changed)
            layout.addWidget(w)
        self.band_status_label = QLabel()
        self.band_status_label.setWordWrap(True)
        layout.addWidget(self.band_status_label)
        return page

    def _on_band_colors_changed(self, *_args) -> None:
        bc = self.controller.config.color_mapping.band_colors
        bc.bass_mid_hz = float(self.band_bass_mid_spin.value())
        bc.mid_treble_hz = float(self.band_mid_treble_spin.value())
        bc.bass_hue_deg = self.band_bass_hue_slider.value()
        bc.mid_hue_deg = self.band_mid_hue_slider.value()
        bc.treble_hue_deg = self.band_treble_hue_slider.value()
        bc.focus = self.band_focus_slider.value()
        bc.chord_turn_deg = self.band_chord_turn_slider.value()
        bc.range_memory_s = self.band_range_memory_slider.value()
        bc.attack_ms = self.band_attack_slider.value()
        bc.release_ms = self.band_release_slider.value()
        bc.saturation = self.band_saturation_slider.value()
        bc.brightness_min = self.band_bright_min_slider.value()
        bc.brightness_max = self.band_bright_max_slider.value()
        self.controller.apply_config_changes()

    def _refresh_band_status(self) -> None:
        """The line at the bottom of the Band Colors page: how active each band is, and the chord turn."""
        engine = self.controller.engine
        if self.controller.config.color_mapping.mode != "band_colors":
            self.band_status_label.setText("")
            return
        if not engine.running:
            self.band_status_label.setText("Now: - (runs while the visualizer is on)")
            return
        state = engine.latest_band_colors
        bands = "  ".join(f"{band} {value:.0%}" for band, value in state.activities.items())
        text = f"Now: {bands}"
        if self.controller.config.color_mapping.band_colors.chord_turn_deg > 0.0:
            text += f"  |  chord turn {state.turn:.0f} deg"
            pitch = engine.latest_pitch
            if pitch is not None:
                text += f" (note {pitch.note_name} in {pitch.key_name}, pitch share {engine.pitch_weight:.0%})"
        self.band_status_label.setText(text)

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

    def _on_transitions_changed(self, text: str) -> None:
        self.controller.config.network.lamp_transitions = text
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
