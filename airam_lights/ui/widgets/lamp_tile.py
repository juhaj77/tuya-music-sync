"""One row representing a single lamp: selection checkbox, online dot, name,
live color swatch, latency readout - and the lamp's place in the effects
(Chase order, Chase dwell, Effect group, True white brightness). Every
column has a fixed width, so the header row (LampListHeader) lines up with
the rows under it."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSpinBox, QWidget

from .. import theme

# (header text, width, tooltip) per column, in order.
_COLUMNS = [
    ("", 22, "Selected: the lamps the show plays on."),
    ("", 18, "Online (green) or not answering (red)."),
    ("Lamp", 150, "The lamp's name (Edit Selected to change it)."),
    ("IP", 110, "The lamp's address on the local network."),
    ("Now", 30, "The colour the lamp was last sent - what it shows now."),
    ("Status", 70, "Whether the lamp answers."),
    ("Latency", 60, "How long the lamp's last answer took."),
    (
        "Chase order", 90,
        "This lamp's position in the Chase overlay's rotation, 0-based. Lamps with the same number light "
        "up together as one position. Off = not part of the chase. The pulse sequencer's color sweep and "
        "white walk ('chase_order' positions) use the same order.",
    ),
    (
        "Chase dwell x", 95,
        "How long the Chase highlight lingers at this lamp's position relative to others - lower it for a "
        "position with several lamps (e.g. a multi-spot ceiling fixture) if the highlight seems to dwell "
        "there too long. 1.0 = default.",
    ),
    (
        "Effect group", 90,
        "This lamp's group for the Group Switch overlay and the pulse sequencer's white walk (independent "
        "of Chase order). Lamps with the same number switch together. Off = not part of any group.",
    ),
    (
        "True white x", 95,
        "Multiplies the true-white flash brightness (Beat Sync's white pulses, the sequencer's flashes "
        "and strobes) for this lamp - 0.5 = half, 1.0 = unchanged. Give every lamp in a group the same "
        "value to balance groups against each other (e.g. wall spots vs. ceiling).",
    ),
]
_ORDER_MAX = 15


def _fix(widget: QWidget, column: int) -> QWidget:
    _, width, tooltip = _COLUMNS[column]
    widget.setFixedWidth(width)
    widget.setToolTip(tooltip)
    return widget


def _row_layout(parent: QWidget) -> QHBoxLayout:
    layout = QHBoxLayout(parent)
    layout.setContentsMargins(6, 4, 6, 4)
    layout.setSpacing(8)
    return layout


class LampListHeader(QWidget):
    """Column titles over the lamp rows, with the columns' tooltips."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = _row_layout(self)
        # The rows are framed panels: one frame width further in.
        layout.setContentsMargins(7, 0, 7, 0)
        for column, (text, _, _) in enumerate(_COLUMNS):
            label = QLabel(text)
            label.setStyleSheet(f"color: {theme.MUTED};")
            layout.addWidget(_fix(label, column))
        layout.addStretch(1)


class LampTile(QFrame):
    selectionChanged = Signal(str, bool)  # device_id, selected
    effectChanged = Signal(str, str, object)  # device_id, PerLampEffect attribute, value

    def __init__(self, device_id: str, name: str, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self.setFrameShape(QFrame.StyledPanel)

        layout = _row_layout(self)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(True)
        self.checkbox.toggled.connect(lambda checked: self.selectionChanged.emit(self.device_id, checked))
        layout.addWidget(_fix(self.checkbox, 0))

        self.status_dot = QLabel("●")  # ●
        self.status_dot.setStyleSheet(f"color: {theme.DIM}; font-size: 14px;")
        layout.addWidget(_fix(self.status_dot, 1))

        self.name_label = QLabel(name)
        layout.addWidget(_fix(self.name_label, 2))

        self.ip_label = QLabel("")
        self.ip_label.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(_fix(self.ip_label, 3))

        self.swatch = QFrame()
        self.swatch.setFixedHeight(16)
        self.swatch.setStyleSheet("background-color: #000; border: 1px solid #555;")
        layout.addWidget(_fix(self.swatch, 4))
        self._swatch_rgb: Optional[tuple] = None

        self.status_label = QLabel("unknown")
        layout.addWidget(_fix(self.status_label, 5))

        self.latency_label = QLabel("- ms")
        layout.addWidget(_fix(self.latency_label, 6))

        self.chase_order_spin = self._order_spin("chase_order", 7)
        layout.addWidget(self.chase_order_spin)
        self.chase_dwell_spin = self._mult_spin("chase_dwell_mult", 8, 0.1, 10.0)
        layout.addWidget(self.chase_dwell_spin)
        self.effect_group_spin = self._order_spin("effect_group", 9)
        layout.addWidget(self.effect_group_spin)
        self.white_mult_spin = self._mult_spin("white_pulse_brightness_mult", 10, 0.0, 2.0)
        layout.addWidget(self.white_mult_spin)

        layout.addStretch(1)

    def _order_spin(self, attr: str, column: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-1, _ORDER_MAX)
        spin.setSpecialValueText("Off")
        spin.valueChanged.connect(lambda v: self.effectChanged.emit(self.device_id, attr, v if v >= 0 else None))
        _fix(spin, column)
        return spin

    def _mult_spin(self, attr: str, column: int, lo: float, hi: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setSingleStep(0.05)
        spin.setValue(1.0)
        spin.valueChanged.connect(lambda v: self.effectChanged.emit(self.device_id, attr, v))
        _fix(spin, column)
        return spin

    def set_effect(self, effect) -> None:
        """Shows the lamp's PerLampEffect (without reporting it back as a change)."""
        for spin, value in (
            (self.chase_order_spin, effect.chase_order if effect.chase_order is not None else -1),
            (self.chase_dwell_spin, effect.chase_dwell_mult),
            (self.effect_group_spin, effect.effect_group if effect.effect_group is not None else -1),
            (self.white_mult_spin, effect.white_pulse_brightness_mult),
        ):
            if spin.hasFocus():
                continue  # mid-edit: leave the typed text alone
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def set_name(self, name: str) -> None:
        self.name_label.setText(name)

    def set_ip(self, ip: str) -> None:
        self.ip_label.setText(ip)

    def set_selected(self, selected: bool) -> None:
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(selected)
        self.checkbox.blockSignals(False)

    def set_online(self, online: bool, error: str = "") -> None:
        if online:
            self.status_dot.setStyleSheet(f"color: {theme.OK}; font-size: 14px;")
            self.status_label.setText("Online")
            self.status_label.setToolTip("")
        else:
            self.status_dot.setStyleSheet(f"color: {theme.ERR}; font-size: 14px;")
            self.status_label.setText("Offline")
            self.status_label.setToolTip(error or "No response")

    def set_color(self, r: int, g: int, b: int) -> None:
        if self._swatch_rgb == (r, g, b):
            return  # called many times a second: restyle only on a change
        self._swatch_rgb = (r, g, b)
        self.swatch.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid #555;")

    def set_latency(self, latency_ms: "float | None") -> None:
        self.latency_label.setText(f"{latency_ms:.0f} ms" if latency_ms is not None else "- ms")
