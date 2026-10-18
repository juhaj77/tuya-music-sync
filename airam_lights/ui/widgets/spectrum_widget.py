"""Real-time log-spaced bar spectrum display.

Independent of the configured color-mapping bands - always shows ~48
log-spaced bars from 20 Hz to 16 kHz (the lowest ones, narrower than an
FFT bin, interpolated between the bins), purely so you can visually confirm
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
        db = self._bar_db(frame)
        levels = np.clip((db - floor_db) / (0.0 - floor_db), 0.0, 1.0)
        # light attack/release smoothing purely for a nicer-looking display
        rising = levels > self._levels
        alpha = np.where(rising, 0.6, 0.25)
        self._levels = self._levels + (levels - self._levels) * alpha
        self.update()

    def _bar_db(self, frame: SpectrumFrame) -> np.ndarray:
        """Each bar's level in dB: the mean of the FFT bins inside it. At the
        low end a bar is narrower than the bin spacing (2048-point FFT at
        48 kHz = 23 Hz per bin, while the bars below ~150 Hz are narrower),
        so it may hold no bin at all - it takes the spectrum interpolated
        at its centre instead of showing as a gap."""
        freqs, mags = frame.freqs, frame.magnitude_db
        lo_idx = np.searchsorted(freqs, self._edges[:-1], side="left")
        hi_idx = np.searchsorted(freqs, self._edges[1:], side="left")
        sums = np.concatenate(([0.0], np.cumsum(mags, dtype=np.float64)))
        counts = hi_idx - lo_idx
        means = (sums[hi_idx] - sums[lo_idx]) / np.maximum(counts, 1)
        centres = np.sqrt(self._edges[:-1] * self._edges[1:])
        return np.where(counts > 0, means, np.interp(centres, freqs, mags))

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
