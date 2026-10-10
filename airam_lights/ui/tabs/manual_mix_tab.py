"""Color + White tab for the standalone manual control app: light a lamp's
white LEDs (brightness + color temperature) and its RGB LEDs at the same
time - e.g. blue under cool white, red under warm white - set group by group
from the saved lamp groups (Devices & Setup -> Lamp groups) or for the
current selection. Both go out as one command on the bulbs' real-time
control datapoint (DP 28), the only way the bulb shows them together.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...color.models import Color, WhiteTarget
from ..manual_controller import ManualController
from ..widgets.param_slider import FloatSlider

logger = logging.getLogger("airam_lights.ui.manual")

_SELECTED = "Selected lamps"

# (button text, color temperature, accent hue in degrees)
_ACCENTS = (
    ("Cool white + blue", 1.0, 225.0),
    ("Warm white + red", 0.0, 8.0),
)


class ManualMixTab(QWidget):
    def __init__(self, controller: ManualController, parent=None):
        super().__init__(parent)
        self.controller = controller

        brightness, temp, color = 0.6, 1.0, Color.from_hsv(225.0, 1.0, 0.5)
        saved = controller.config.manual_state.mix_targets
        if saved:
            values = next(iter(saved.values()))
            brightness, temp = values["brightness"], values["temp"]
            color = Color(values["r"], values["g"], values["b"]).clamped()
        hue, sat, strength = color.to_hsv()
        # The picked hue/saturation; the Color strength slider sets its brightness.
        self._picked = QColor.fromHsvF(hue / 360.0, sat, 1.0)

        root = QVBoxLayout(self)

        box = QGroupBox("White and color together")
        box_layout = QVBoxLayout(box)
        box_layout.addWidget(
            QLabel(
                "Lights the white LEDs and the RGB LEDs of the same lamps at once - for example blue "
                "under cool white, red under warm white. Set one group, then pick the next group and "
                "set it differently. Save groups in Devices & Setup (Lamp groups)."
            )
        )
        self.legacy_label = QLabel(
            "Lamp transitions is set to 'legacy' (Color Mapping -> Global in the music app): the "
            "lamps then show only the white, not the color with it. Use 'direct' for this tab."
        )
        self.legacy_label.setStyleSheet("color: #c0392b;")
        self.legacy_label.setWordWrap(True)
        box_layout.addWidget(self.legacy_label)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Apply to:"))
        self.target_combo = QComboBox()
        target_row.addWidget(self.target_combo, stretch=1)
        box_layout.addLayout(target_row)

        white_box = QGroupBox("White LEDs")
        white_layout = QVBoxLayout(white_box)
        self.temp_slider = FloatSlider("Color temperature", 0.0, 1.0, temp, decimals=2)
        white_layout.addWidget(self.temp_slider)
        white_layout.addWidget(QLabel("0.0 = warmest, 1.0 = coolest"))
        self.brightness_slider = FloatSlider("White brightness", 0.0, 1.0, brightness, decimals=2)
        white_layout.addWidget(self.brightness_slider)
        box_layout.addWidget(white_box)

        color_box = QGroupBox("RGB LEDs")
        color_layout = QVBoxLayout(color_box)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self.swatch = QFrame()
        self.swatch.setFixedSize(56, 32)
        self.swatch.setFrameShape(QFrame.Box)
        color_row.addWidget(self.swatch)
        pick_btn = QPushButton("Pick Color...")
        pick_btn.clicked.connect(self._on_pick_color)
        color_row.addWidget(pick_btn)
        color_row.addStretch(1)
        color_layout.addLayout(color_row)
        self.strength_slider = FloatSlider("Color strength", 0.0, 1.0, strength, decimals=2)
        color_layout.addWidget(self.strength_slider)
        box_layout.addWidget(color_box)

        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Quick pick:"))
        for text, accent_temp, accent_hue in _ACCENTS:
            btn = QPushButton(text)
            btn.clicked.connect(lambda _=False, t=accent_temp, h=accent_hue: self._on_accent(t, h))
            accent_row.addWidget(btn)
        accent_row.addStretch(1)
        box_layout.addLayout(accent_row)

        self.preview = QFrame()
        self.preview.setFixedHeight(28)
        self.preview.setFrameShape(QFrame.Box)
        box_layout.addWidget(self.preview)

        self.live_checkbox = QCheckBox("Apply while adjusting")
        self.live_checkbox.setChecked(True)
        box_layout.addWidget(self.live_checkbox)

        apply_row = QHBoxLayout()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._on_apply)
        apply_row.addWidget(apply_btn)
        on_btn = QPushButton("Turn On + Apply")
        on_btn.clicked.connect(self._on_turn_on)
        apply_row.addWidget(on_btn)
        off_btn = QPushButton("Turn Off")
        off_btn.clicked.connect(self._on_turn_off)
        apply_row.addWidget(off_btn)
        apply_row.addStretch(1)
        box_layout.addLayout(apply_row)

        root.addWidget(box)
        root.addStretch(1)

        for slider in (self.temp_slider, self.brightness_slider, self.strength_slider):
            slider.valueChanged.connect(self._on_changed)
        self._refresh_targets()
        self._refresh_legacy_note()
        self._update_preview()

    def showEvent(self, event) -> None:
        # Groups can be saved in Devices & Setup while the app runs.
        self._refresh_targets()
        self._refresh_legacy_note()
        super().showEvent(event)

    def _refresh_targets(self) -> None:
        current = self.target_combo.currentText()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem(_SELECTED)
        for name in self.controller.config.groups.keys():
            self.target_combo.addItem(f"Group: {name}", name)
        index = self.target_combo.findText(current)
        self.target_combo.setCurrentIndex(max(0, index))
        self.target_combo.blockSignals(False)

    def _refresh_legacy_note(self) -> None:
        self.legacy_label.setVisible(self.controller.config.network.lamp_transitions == "legacy")

    def _target_ids(self) -> "list[str]":
        group = self.target_combo.currentData()
        if group is None:
            return list(self.controller.lamp_manager.selected_device_ids())
        known = self.controller.lamp_manager.devices
        return [device_id for device_id in self.controller.config.groups.get(group, []) if device_id in known]

    def _targets_or_warn(self) -> "list[str] | None":
        ids = self._target_ids()
        if not ids:
            if self.target_combo.currentData() is None:
                text = "Select at least one lamp in the Devices & Setup tab first, or pick a group."
            else:
                text = "This group has no lamps that are still in the device list."
            QMessageBox.information(self, "No lamps", text)
            return None
        return ids

    def _color(self) -> Color:
        hue = max(0.0, self._picked.hsvHueF()) * 360.0  # -1 for a grey pick
        return Color.from_hsv(hue, self._picked.hsvSaturationF(), self.strength_slider.value())

    def _update_preview(self) -> None:
        self.swatch.setStyleSheet(
            f"background-color: rgb({self._picked.red()},{self._picked.green()},{self._picked.blue()}); "
            "border: 1px solid #555;"
        )
        mix = WhiteTarget(self.brightness_slider.value(), self.temp_slider.value(), under=self._color())
        r, g, b = mix.to_preview_color().to_rgb255()
        self.preview.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")
        self.preview.setToolTip("Rough preview of white + color together")

    def _apply(self, ids: "list[str]") -> None:
        self.controller.set_mix_for(ids, self.brightness_slider.value(), self.temp_slider.value(), self._color())

    def _on_changed(self, *_args) -> None:
        self._update_preview()
        if self.live_checkbox.isChecked():
            ids = self._target_ids()
            if ids:
                self._apply(ids)

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(self._picked, self, "Pick the color under the white")
        if not color.isValid():
            return
        hue, sat, value = Color(color.redF(), color.greenF(), color.blueF()).to_hsv()
        self._picked = QColor.fromHsvF(hue / 360.0, sat, 1.0)
        self.strength_slider.set_value(value)  # no signal - _on_changed below covers it
        self._on_changed()

    def _on_accent(self, temp: float, hue: float) -> None:
        self._picked = QColor.fromHsvF(hue / 360.0, 1.0, 1.0)
        self.temp_slider.set_value(temp)
        self._on_changed()

    def _on_apply(self) -> None:
        ids = self._targets_or_warn()
        if ids is not None:
            self._apply(ids)

    def _on_turn_on(self) -> None:
        ids = self._targets_or_warn()
        if ids is None:
            return
        self._power(ids, on=True)
        self._apply(ids)

    def _on_turn_off(self) -> None:
        ids = self._targets_or_warn()
        if ids is not None:
            self._power(ids, on=False)

    def _power(self, ids: "list[str]", on: bool) -> None:
        def _run():
            for device_id in ids:
                dev = self.controller.lamp_manager.devices.get(device_id)
                if dev is None:
                    continue
                try:
                    if on:
                        dev.turn_on(wait_for_ack=False)
                    else:
                        dev.turn_off(wait_for_ack=False)
                except Exception as e:
                    logger.warning("Failed to turn %s '%s': %s", "on" if on else "off", device_id, e)

        threading.Thread(target=_run, daemon=True).start()
