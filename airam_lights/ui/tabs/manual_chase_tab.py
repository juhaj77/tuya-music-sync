"""Chase / Rotating Light tab for the standalone manual control app.

Same underlying effect as the music visualizer's Chase overlay (shared code
in effects/chase.py) - just without a "sync to beat" option here, since this
app has no audio input to sync to. Assign lamps to chase positions in the
table below; lamps sharing the same position number animate together, which
matters because your physical layout usually isn't a single ring.
"""
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
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..manual_controller import ManualController
from ..widgets.hue_slider import HueSlider
from ..widgets.param_slider import FloatSlider

_TABLE_COLUMNS = ["Lamp", "Chase position", "Dwell x"]


class ManualChaseTab(QWidget):
    def __init__(self, controller: ManualController, parent=None):
        super().__init__(parent)
        self.controller = controller

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)

        # -- per-lamp position table ---------------------------------------------------
        table_box = QGroupBox("Lamp chase positions")
        table_layout = QVBoxLayout(table_box)
        table_layout.addWidget(
            QLabel(
                "Give lamps a position (0, 1, 2, ...) to include them in the rotation, in that order. "
                "Lamps sharing the SAME position number animate together as one group - use this for "
                "physical layouts that aren't a simple ring (e.g. two lamps per wall). 'Dwell x' controls "
                "how long the highlight lingers at that position relative to others (1.0 = default; lower "
                "it for a position with several lamps at once, e.g. a multi-spot ceiling fixture, if the "
                "highlight feels like it's dwelling there too long)."
            )
        )
        self.table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Floor the column width so the spinboxes' up/down buttons always
        # have room to render, even in a narrow window - otherwise they can
        # shrink to the point where only scroll-wheel/typing still work but
        # nothing visibly indicates that.
        self.table.horizontalHeader().setMinimumSectionSize(70)
        table_layout.addWidget(self.table)
        root.addWidget(table_box)

        # -- global chase settings -------------------------------------------------------
        box = QGroupBox("Chase settings")
        layout = QVBoxLayout(box)
        ch = controller.config.chase

        self.enabled_checkbox = QCheckBox("Enabled")
        self.enabled_checkbox.setChecked(ch.enabled)
        self.enabled_checkbox.toggled.connect(self._on_changed)
        layout.addWidget(self.enabled_checkbox)

        rotators_row = QHBoxLayout()
        rotators_row.addWidget(QLabel("Number of rotators:"))
        self.num_rotators_spin = QSpinBox()
        self.num_rotators_spin.setRange(1, 64)  # generous cap, not tied to any specific lamp count
        self.num_rotators_spin.setValue(ch.num_rotators)
        self.num_rotators_spin.valueChanged.connect(self._on_changed)
        rotators_row.addWidget(self.num_rotators_spin)
        rotators_row.addStretch(1)
        layout.addLayout(rotators_row)
        layout.addWidget(
            QLabel("2 = two highlights on opposite sides of the loop, moving together; 3 = a third apart, etc.")
        )

        self.speed_slider = FloatSlider(
            "Speed", 0.02, 5.0, ch.speed_rotations_per_s, decimals=3, suffix=" rotations/s"
        )
        self.width_slider = FloatSlider("Highlight width", 0.2, 8.0, ch.width, decimals=2, suffix=" positions")
        self.intensity_slider = FloatSlider("Intensity (brightness boost)", 0.0, 8.0, ch.intensity, decimals=2)
        for w in (self.speed_slider, self.width_slider, self.intensity_slider):
            w.valueChanged.connect(self._on_changed)
            layout.addWidget(w)
        self.reverse_checkbox = QCheckBox("Reverse direction")
        self.reverse_checkbox.setChecked(ch.reverse)
        self.reverse_checkbox.toggled.connect(self._on_changed)
        layout.addWidget(self.reverse_checkbox)
        layout.addWidget(
            QLabel(
                "Speed is full laps around all chase positions per second (e.g. 0.5 = one lap every 2s). "
                "Intensity multiplies each lamp's own current brightness - it never lights up a lamp "
                "you've turned off/black."
            )
        )

        curve_row = QHBoxLayout()
        curve_row.addWidget(QLabel("Falloff curve:"))
        self.falloff_curve_combo = QComboBox()
        self.falloff_curve_combo.addItems(["linear", "bezier"])
        self.falloff_curve_combo.setCurrentText(ch.falloff_curve)
        self.falloff_curve_combo.currentTextChanged.connect(self._on_changed)
        curve_row.addWidget(self.falloff_curve_combo)
        curve_row.addStretch(1)
        layout.addLayout(curve_row)
        layout.addWidget(
            QLabel(
                "linear: the highlight color changes at a constant rate - the peak is a single fleeting "
                "instant. bezier: an eased S-curve that dwells near the peak color (and near the "
                "background) for longer, transitioning fastest in between - try this if the highlight "
                "feels like it flies by too quickly."
            )
        )

        self.switch_fade_checkbox = QCheckBox("Soft steps (fade each lamp's color change as the highlight moves)")
        self.switch_fade_checkbox.setChecked(ch.switch_fade)
        self.switch_fade_checkbox.setToolTip(
            "With only a handful of lamps each lamp's color changes in visible jumps as the highlight "
            "moves on, whatever the width and falloff curve. On: every lamp's color fades to its new "
            "value over the Switch fade time below instead."
        )
        self.switch_fade_checkbox.toggled.connect(self._on_changed)
        layout.addWidget(self.switch_fade_checkbox)
        self.switch_fade_slider = FloatSlider(
            "Switch fade", 20.0, 1000.0, ch.switch_fade_ms, decimals=0, suffix=" ms",
            tooltip="How long each lamp takes to fade to its new color when the highlight moves (with Soft "
            "steps on). Longer = softer, with a longer trail.",
        )
        self.switch_fade_slider.setEnabled(ch.switch_fade)
        self.switch_fade_slider.valueChanged.connect(self._on_changed)
        layout.addWidget(self.switch_fade_slider)

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Chase color:"))
        self.color_mode_combo = QComboBox()
        self.color_mode_combo.addItems(["custom", "complementary", "hue_shift"])
        self.color_mode_combo.setCurrentText(ch.color_mode)
        self.color_mode_combo.currentTextChanged.connect(self._on_changed)
        color_row.addWidget(self.color_mode_combo)
        color_row.addStretch(1)
        layout.addLayout(color_row)
        layout.addWidget(
            QLabel(
                "custom: a fixed color below. complementary: the opposite hue of each lamp's own color. "
                "hue_shift: a rainbow trail - each position's hue steps by the amount below."
            )
        )

        self.hue_slider = HueSlider("Custom hue", ch.custom_hue_deg)
        self.sat_slider = FloatSlider("Custom saturation", 0.0, 1.0, ch.custom_saturation)
        self.hue_shift_slider = FloatSlider(
            "Hue shift step (for 'hue_shift')", 1.0, 180.0, ch.hue_shift_step_deg, decimals=1, suffix=" deg"
        )
        for w in (self.hue_slider, self.sat_slider, self.hue_shift_slider):
            w.valueChanged.connect(self._on_changed)
            layout.addWidget(w)

        root.addWidget(box)

        self._populated_lamps = None
        controller.lampsChanged.connect(self._populate_table)
        self._populate_table()

    # -- per-lamp table ------------------------------------------------------------------

    def _populate_table(self) -> None:
        devices = list(self.controller.lamp_manager.devices.values())
        # lampsChanged also fires every few seconds from the periodic lamp
        # status refresh - rebuilding the cell widgets then would destroy a
        # spinbox mid-edit (focus lost, typed text gone), so only rebuild
        # when the lamp list itself has actually changed.
        lamps = [(dev.config.id, dev.config.name) for dev in devices]
        if lamps == self._populated_lamps:
            return
        self._populated_lamps = lamps
        self.table.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            effect = self.controller.get_or_create_effect(dev.config.id)

            name_item = QTableWidgetItem(dev.config.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, name_item)

            spin = QSpinBox()
            spin.setRange(-1, max(7, len(devices) - 1))
            spin.setSpecialValueText("Off")
            spin.setValue(effect.chase_order if effect.chase_order is not None else -1)
            spin.valueChanged.connect(
                lambda v, d=dev.config.id: self._on_position_changed(d, (v if v >= 0 else None))
            )
            self.table.setCellWidget(row, 1, spin)

            dwell_spin = QDoubleSpinBox()
            dwell_spin.setRange(0.1, 10.0)
            dwell_spin.setDecimals(2)
            dwell_spin.setSuffix("x")
            dwell_spin.setValue(effect.chase_dwell_mult)
            dwell_spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.UpDownArrows)
            dwell_spin.valueChanged.connect(lambda v, d=dev.config.id: self._on_dwell_changed(d, v))
            self.table.setCellWidget(row, 2, dwell_spin)

    def _on_position_changed(self, device_id: str, chase_order) -> None:
        self.controller.get_or_create_effect(device_id).chase_order = chase_order
        self.controller.apply_config_changes()

    def _on_dwell_changed(self, device_id: str, value: float) -> None:
        self.controller.get_or_create_effect(device_id).chase_dwell_mult = value
        self.controller.apply_config_changes()

    # -- global settings -------------------------------------------------------------------

    def _on_changed(self, *_args) -> None:
        ch = self.controller.config.chase
        ch.enabled = self.enabled_checkbox.isChecked()
        ch.num_rotators = self.num_rotators_spin.value()
        ch.speed_rotations_per_s = self.speed_slider.value()
        ch.width = self.width_slider.value()
        ch.intensity = self.intensity_slider.value()
        ch.reverse = self.reverse_checkbox.isChecked()
        ch.falloff_curve = self.falloff_curve_combo.currentText()
        ch.switch_fade = self.switch_fade_checkbox.isChecked()
        ch.switch_fade_ms = self.switch_fade_slider.value()
        self.switch_fade_slider.setEnabled(ch.switch_fade)
        ch.color_mode = self.color_mode_combo.currentText()
        ch.custom_hue_deg = self.hue_slider.value()
        ch.custom_saturation = self.sat_slider.value()
        ch.hue_shift_step_deg = self.hue_shift_slider.value()
        self.controller.apply_config_changes()
