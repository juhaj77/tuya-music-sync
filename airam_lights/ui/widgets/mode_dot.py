"""The dot on a colour mode's tab: green on the running mode, grey on the
others, and a grey one is clicked to turn its mode on. A checkable
QAbstractButton, so an exclusive QButtonGroup keeps exactly one green."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from .. import theme

_SIZE = 20  # what takes the click: roomier than the dot drawn in it
_DIAMETER = 11


class ModeDot(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        # With the focus, an arrow key would click the group's next dot and
        # change the running mode. The Visualizer tab's radios are the keyboard way.
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover)  # repainted as the mouse comes and goes
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(_SIZE, _SIZE)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        color = theme.OK if self.isChecked() else theme.MUTED if self.underMouse() else theme.DIM
        painter.setBrush(QColor(color))
        rect = QRectF(0, 0, _DIAMETER, _DIAMETER)
        rect.moveCenter(QRectF(self.rect()).center())
        painter.drawEllipse(rect)
