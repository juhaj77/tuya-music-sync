"""Main visualizer tab: audio device, level meter, spectrum, mode selection,
and the most commonly-tweaked controls (sensitivity/brightness/saturation/
attack/release). Detailed per-mode configuration lives in the Color Mapping
and 8-Band tabs."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...audio.devices import list_loopback_devices, list_microphone_devices
from ..controller import AppController
from ..widgets.level_meter import LevelMeter
from ..widgets.param_slider import FloatSlider
from ..widgets.spectrum_widget import SpectrumWidget

# Beat Sync first - it's the main mode - with the other beat-driven modes next to it.
_MODES = [
    ("beat_sync", "Beat Sync"),
    ("beat_sync_white", "Beat Sync White"),
    ("peak_flash", "Peak Flash"),
    ("rgb_freq", "RGB Frequency"),
    ("hsv_music", "HSV Music"),
    ("8band_spectrum", "8-Band Spectrum"),
    ("custom", "Custom"),
]


class VisualizerTab(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller

        root = QVBoxLayout(self)

        # -- audio -----------------------------------------------------------------
        audio_box = QGroupBox("Audio")
        audio_layout = QVBoxLayout(audio_box)
        audio_cfg = controller.config.audio

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source:"))
        self.source_group = QButtonGroup(self)
        self.loopback_radio = QRadioButton("Loopback (what you hear)")
        self.mic_radio = QRadioButton("Microphone")
        self.source_group.addButton(self.loopback_radio)
        self.source_group.addButton(self.mic_radio)
        source_row.addWidget(self.loopback_radio)
        source_row.addWidget(self.mic_radio)
        source_row.addStretch(1)
        audio_layout.addLayout(source_row)
        audio_layout.addWidget(
            QLabel(
                "Microphone reacts to real room/ambient sound (e.g. talking, clapping, playing an "
                "instrument nearby) instead of only whatever's playing through Windows - handy for "
                "testing without routing any specific playback source."
            )
        )

        self.loopback_device_widget = QWidget()
        device_row = QHBoxLayout(self.loopback_device_widget)
        device_row.setContentsMargins(0, 0, 0, 0)
        device_row.addWidget(QLabel("Loopback device:"))
        self.device_combo = QComboBox()
        self._populate_devices()
        self.device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_row.addWidget(self.device_combo, stretch=1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._populate_devices)
        device_row.addWidget(refresh_btn)
        audio_layout.addWidget(self.loopback_device_widget)

        # One container for everything mic-specific (device picker + gain +
        # its note) so a single setVisible() toggles all of it together.
        self.mic_device_widget = QWidget()
        mic_section = QVBoxLayout(self.mic_device_widget)
        mic_section.setContentsMargins(0, 0, 0, 0)

        mic_device_row = QHBoxLayout()
        mic_device_row.addWidget(QLabel("Microphone device:"))
        self.mic_combo = QComboBox()
        self._populate_mic_devices()
        self.mic_combo.currentIndexChanged.connect(self._on_mic_device_changed)
        mic_device_row.addWidget(self.mic_combo, stretch=1)
        mic_refresh_btn = QPushButton("Refresh")
        mic_refresh_btn.clicked.connect(self._populate_mic_devices)
        mic_device_row.addWidget(mic_refresh_btn)
        mic_section.addLayout(mic_device_row)

        self.mic_gain_slider = FloatSlider(
            "Microphone sensitivity (gain)", 0.1, 10.0, audio_cfg.mic_gain, decimals=2, suffix="x"
        )
        self.mic_gain_slider.valueChanged.connect(self._on_mic_gain_changed)
        mic_section.addWidget(self.mic_gain_slider)
        mic_gain_note = QLabel("Microphones are usually much quieter than loopback - raise this if it barely reacts.")
        mic_gain_note.setWordWrap(True)
        mic_section.addWidget(mic_gain_note)
        audio_layout.addWidget(self.mic_device_widget)

        self.loopback_radio.toggled.connect(self._on_source_changed)
        (self.mic_radio if audio_cfg.source == "microphone" else self.loopback_radio).setChecked(True)
        self._update_source_visibility()

        self.level_meter = LevelMeter()
        audio_layout.addWidget(QLabel("Level:"))
        audio_layout.addWidget(self.level_meter)

        self.spectrum_widget = SpectrumWidget()
        audio_layout.addWidget(QLabel("Spectrum:"))
        audio_layout.addWidget(self.spectrum_widget)
        root.addWidget(audio_box)

        # -- mode --------------------------------------------------------------------
        mode_box = QGroupBox("Mode")
        mode_layout = QHBoxLayout(mode_box)
        self.mode_group = QButtonGroup(self)
        self._mode_buttons = {}
        for key, label in _MODES:
            rb = QRadioButton(label)
            self.mode_group.addButton(rb)
            mode_layout.addWidget(rb)
            self._mode_buttons[key] = rb
            rb.toggled.connect(self._make_mode_handler(key))
        self._mode_buttons[controller.config.color_mapping.mode].setChecked(True)
        root.addWidget(mode_box)

        # -- quick controls ------------------------------------------------------------
        quick_box = QGroupBox("Quick Controls")
        quick_layout = QVBoxLayout(quick_box)
        cm = controller.config.color_mapping

        self.sensitivity_slider = FloatSlider("Sensitivity", 0.1, 4.0, cm.rgb.sensitivity)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity)
        quick_layout.addWidget(self.sensitivity_slider)

        self.brightness_slider = FloatSlider("Brightness", 0.0, 1.0, cm.brightness)
        self.brightness_slider.valueChanged.connect(self._on_brightness)
        quick_layout.addWidget(self.brightness_slider)

        self.saturation_slider = FloatSlider("Saturation", 0.0, 1.0, cm.saturation)
        self.saturation_slider.valueChanged.connect(self._on_saturation)
        quick_layout.addWidget(self.saturation_slider)

        self.attack_slider = FloatSlider("Attack", 1.0, 1000.0, cm.smoothing.attack_ms, decimals=0, suffix=" ms")
        self.attack_slider.valueChanged.connect(self._on_attack)
        quick_layout.addWidget(self.attack_slider)

        self.release_slider = FloatSlider("Release", 1.0, 2000.0, cm.smoothing.release_ms, decimals=0, suffix=" ms")
        self.release_slider.valueChanged.connect(self._on_release)
        quick_layout.addWidget(self.release_slider)

        root.addWidget(quick_box)

        # -- start/stop ------------------------------------------------------------------
        self.start_button = QPushButton("Start Music Visualization")
        self.start_button.setCheckable(True)
        self.start_button.toggled.connect(self._on_toggle_start)
        root.addWidget(self.start_button)

        self.status_label = QLabel("Stopped")
        root.addWidget(self.status_label)

        controller.levelUpdated.connect(self.level_meter.set_level)
        controller.spectrumUpdated.connect(self.spectrum_widget.set_frame)
        controller.audioError.connect(self._on_audio_error)

    # -- audio device -----------------------------------------------------------------

    def _populate_devices(self) -> None:
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        devices = list_loopback_devices()
        current_index = self.controller.config.audio.device_index
        select_row = 0
        for row, d in enumerate(devices):
            label = f"{d.name}{' (default)' if d.is_default else ''}"
            self.device_combo.addItem(label, d.index)
            if d.index == current_index:
                select_row = row
        if devices:
            self.device_combo.setCurrentIndex(select_row)
        else:
            self.device_combo.addItem("No loopback device found", None)
        self.device_combo.blockSignals(False)

    def _on_device_changed(self, row: int) -> None:
        device_index = self.device_combo.itemData(row)
        self.controller.set_audio_device(device_index)

    def _populate_mic_devices(self) -> None:
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        devices = list_microphone_devices()
        current_index = self.controller.config.audio.mic_device_index
        select_row = 0
        for row, d in enumerate(devices):
            label = f"{d.name}{' (default)' if d.is_default else ''}"
            self.mic_combo.addItem(label, d.index)
            if d.index == current_index:
                select_row = row
        if devices:
            self.mic_combo.setCurrentIndex(select_row)
        else:
            self.mic_combo.addItem("No microphone/recording device found", None)
        self.mic_combo.blockSignals(False)

    def _on_mic_device_changed(self, row: int) -> None:
        mic_device_index = self.mic_combo.itemData(row)
        self.controller.set_mic_device(mic_device_index)

    def _on_mic_gain_changed(self, value: float) -> None:
        self.controller.set_mic_gain(value)

    def _on_source_changed(self, loopback_checked: bool) -> None:
        self.controller.set_audio_source("loopback" if loopback_checked else "microphone")
        self._update_source_visibility()

    def _update_source_visibility(self) -> None:
        is_mic = self.mic_radio.isChecked()
        self.loopback_device_widget.setVisible(not is_mic)
        self.mic_device_widget.setVisible(is_mic)

    def _on_audio_error(self, message: str) -> None:
        self.status_label.setText(f"Audio error: {message}")

    # -- mode ---------------------------------------------------------------------------

    def _make_mode_handler(self, key: str):
        def handler(checked: bool):
            if checked:
                self.controller.config.color_mapping.mode = key
                self.controller.apply_config_changes()

        return handler

    # -- quick controls -------------------------------------------------------------------

    def _on_sensitivity(self, value: float) -> None:
        cm = self.controller.config.color_mapping
        cm.rgb.sensitivity = value
        cm.custom.sensitivity = value
        cm.hsv.sensitivity = value
        cm.spectrum.sensitivity = value
        self.controller.apply_config_changes()

    def _on_brightness(self, value: float) -> None:
        self.controller.config.color_mapping.brightness = value
        self.controller.apply_config_changes()

    def _on_saturation(self, value: float) -> None:
        self.controller.config.color_mapping.saturation = value
        self.controller.apply_config_changes()

    def _on_attack(self, value: float) -> None:
        self.controller.config.color_mapping.smoothing.attack_ms = value
        self.controller.apply_config_changes()

    def _on_release(self, value: float) -> None:
        self.controller.config.color_mapping.smoothing.release_ms = value
        self.controller.apply_config_changes()

    # -- start/stop -----------------------------------------------------------------------

    def _on_toggle_start(self, checked: bool) -> None:
        if checked:
            ok = self.controller.start_visualization()
            if ok:
                self.start_button.setText("Stop Music Visualization")
                self.status_label.setText("Running")
            else:
                self.start_button.setChecked(False)
        else:
            self.controller.stop_visualization()
            self.start_button.setText("Start Music Visualization")
            self.status_label.setText("Stopped")
