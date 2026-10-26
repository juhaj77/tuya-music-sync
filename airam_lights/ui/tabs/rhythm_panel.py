"""The Rhythm box: the shared beat clock every layer can follow - tempo
lock, bars, lead time, and what to do when there's no beat. First on the
Global Effects tab, since the pulses, Chase, Group Switch and the beat modes
all go by it.

`changed` fires after a setting is stored, so the settings that depend on the
clock (the pulse sequencer's) can grey out or come back. A new Beats per bar
rebuilds the settings tabs instead - the every-N-beats choices everywhere
follow the bar length."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from ...config.schema import RhythmConfig
from ..controller import AppController
from ..quick_settings import link_slider
from ..widgets.param_slider import FloatSlider
from ..widgets.section import CollapsibleSection


class RhythmPanel(QWidget):
    changed = Signal()

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.section = self._build_rhythm_box(controller.config.rhythm)
        layout.addWidget(self.section)
        # Also on the Visualizer tab's Quick Controls (ui/quick_settings.py).
        link_slider(controller, "rhythm.sensitivity", self.rhythm_sensitivity_slider)

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
            "colour mode; Chase / Group Switch with Speed source 'clock'."
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
        self.changed.emit()
        self.controller.apply_config_changes()
