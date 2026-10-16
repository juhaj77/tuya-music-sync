"""Simple horizontal audio level meter (RMS bar + peak marker)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .. import theme


class LevelMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rms = 0.0
        self._peak = 0.0
        self.setMinimumHeight(22)

    def set_level(self, rms: float, peak: float) -> None:
        self._rms = max(0.0, min(1.0, rms))
        self._peak = max(0.0, min(1.0, peak))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()

        painter.fillRect(rect, QColor(theme.INPUT))

        rms_w = int(rect.width() * self._rms)
        if rms_w > 0:
            # green -> yellow -> red gradient by position
            for x in range(0, rms_w, max(1, rect.width() // 60)):
                frac = x / max(rect.width(), 1)
                color = QColor.fromHsv(int(120 * (1.0 - frac)), 220, 230)
                painter.fillRect(x, 0, max(1, rect.width() // 60), rect.height(), color)

        peak_x = int(rect.width() * self._peak)
        painter.setPen(QColor(255, 255, 255))
        painter.drawLine(peak_x, 0, peak_x, rect.height())

        painter.setPen(QColor(theme.BORDER))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
