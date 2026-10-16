"""Real-time log-spaced bar spectrum display.

Independent of the configured color-mapping bands - always shows ~48
log-spaced bars from 20 Hz to 16 kHz, purely so you can visually confirm
audio capture + FFT are working (Phase 3) and get a feel for where energy
sits in the spectrum.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ...dsp.fft_engine import SpectrumFrame
from .. import theme

_NUM_BARS = 48
_LOW_HZ = 20.0
_HIGH_HZ = 16000.0


class SpectrumWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = np.zeros(_NUM_BARS)
        self._edges = np.geomspace(_LOW_HZ, _HIGH_HZ, _NUM_BARS + 1)
        self.setMinimumHeight(120)

    def set_frame(self, frame: SpectrumFrame, floor_db: float = -90.0) -> None:
        levels = np.zeros(_NUM_BARS)
        for i in range(_NUM_BARS):
            lo, hi = self._edges[i], self._edges[i + 1]
            mask = (frame.freqs >= lo) & (frame.freqs < hi)
            if np.any(mask):
                avg_db = float(np.mean(frame.magnitude_db[mask]))
                levels[i] = max(0.0, min(1.0, (avg_db - floor_db) / (0.0 - floor_db)))
        # light attack/release smoothing purely for a nicer-looking display
        rising = levels > self._levels
        alpha = np.where(rising, 0.6, 0.25)
        self._levels = self._levels + (levels - self._levels) * alpha
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor(theme.INPUT))

        n = len(self._levels)
        if n == 0:
            return
        bar_w = rect.width() / n
        for i, level in enumerate(self._levels):
            h = level * rect.height()
            x = i * bar_w
            hue = int(260 - 260 * (i / max(n - 1, 1)))  # blue (low) -> red (high)
            color = QColor.fromHsv(max(0, min(359, hue)), 200, 255)
            painter.fillRect(int(x), int(rect.height() - h), max(1, int(bar_w) - 1), int(h), color)

        painter.setPen(QColor(theme.BORDER))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
