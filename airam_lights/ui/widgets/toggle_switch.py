"""An on/off switch: a pill with a knob that slides to the right when on,
drawn with the theme's colours. A QAbstractButton, so it has QCheckBox's
API (isChecked / setChecked / toggled, setToolTip, setEnabled) and the
code that used a check box for a feature's Enabled can use one of these
as it is."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from .. import theme

_WIDTH, _HEIGHT = 38, 20
_MARGIN = 3


class ToggleSwitch(QAbstractButton):
    # The tooltip or the enabled state changed - a CollapsibleSection mirrors
    # them onto its header.
    lookChanged = Signal()

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._offset = 1.0 if checked else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QEasingCurve.InOutQuad)
        self._animation.valueChanged.connect(self._on_animated)
        self.toggled.connect(self._on_toggled)

    def sizeHint(self) -> QSize:
        return QSize(_WIDTH, _HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _on_toggled(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()
        self.lookChanged.emit()

    def _on_animated(self, value) -> None:
        self._offset = float(value)
        self.update()

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.ToolTipChange, QEvent.EnabledChange):
            self.lookChanged.emit()
        return super().event(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, _WIDTH, _HEIGHT)
        rect.moveCenter(QRectF(self.rect()).center())
        on = self._offset
        if not self.isEnabled():
            track = QColor(theme.ACCENT_DIM) if self.isChecked() else QColor("#1d232c")
            knob = QColor("#4b5361")
        else:
            track = _mix(QColor("#2a3240"), QColor(theme.ACCENT), on)
            knob = _mix(QColor("#9aa3b2"), QColor("#ffffff"), on)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, _HEIGHT / 2, _HEIGHT / 2)
        if self.hasFocus():
            painter.setPen(QColor(theme.ACCENT_HOVER))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), _HEIGHT / 2, _HEIGHT / 2)
            painter.setPen(Qt.NoPen)
        diameter = _HEIGHT - 2 * _MARGIN
        x = rect.left() + _MARGIN + on * (_WIDTH - 2 * _MARGIN - diameter)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, rect.top() + _MARGIN, diameter, diameter))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )
