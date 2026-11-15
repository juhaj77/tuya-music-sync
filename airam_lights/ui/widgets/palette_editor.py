"""The Real Time tab's palette: the colours the looks are made of, shown as
the gradient they form.

- The bar on top is the gradient itself (first colour to last).
- Under it one swatch per colour: click to change it, right-click to take
  it out; "+" adds one (up to six).
- The small chips are ready-made palettes (effects.realtime.PALETTES).

    editor = PaletteEditor(["#ff2d95", "#7a2bff"])
    editor.changed.connect(lambda colors: ...)   # list of "#rrggbb"
"""
from __future__ import annotations

from typing import List, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QColorDialog, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from ...effects.realtime import MAX_PALETTE_COLORS, PALETTES
from .. import theme


def _gradient(box: QRectF, colors: Sequence[str]) -> QLinearGradient:
    gradient = QLinearGradient(box.left(), 0.0, box.right(), 0.0)
    if len(colors) == 1:
        gradient.setColorAt(0.0, QColor(colors[0]))
        gradient.setColorAt(1.0, QColor(colors[0]))
    for index, color in enumerate(colors):
        gradient.setColorAt(index / max(1, len(colors) - 1), QColor(color))
    return gradient


class _GradientBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors: List[str] = []
        self.setFixedHeight(18)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(theme.BORDER), 1.0))
        painter.setBrush(_gradient(box, self.colors or ["#000000"]))
        painter.drawRoundedRect(box, 5.0, 5.0)


class _Swatch(QAbstractButton):
    """One colour (or, with `plus`, the button that adds one)."""

    removeRequested = Signal()

    def __init__(self, color: str = "", plus: bool = False, parent=None):
        super().__init__(parent)
        self.color = color
        self.plus = plus
        self.setFixedSize(30, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)  # repaint as the pointer comes and goes
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(
            "Add a colour to the palette." if plus else "Click to change this colour; right-click to take it out."
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton and not self.plus:
            self.removeRequested.emit()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        hover = self.underMouse()
        painter.setPen(QPen(QColor(theme.LIVE if hover else theme.BORDER), 1.2))
        painter.setBrush(QColor(theme.CARD_ALT) if self.plus else QColor(self.color))
        painter.drawRoundedRect(box, 5.0, 5.0)
        if self.plus:
            painter.setPen(QPen(QColor(theme.TEXT if hover else theme.MUTED), 1.6))
            cx, cy = box.center().x(), box.center().y()
            painter.drawLine(int(cx - 4), int(cy), int(cx + 4), int(cy))
            painter.drawLine(int(cx), int(cy - 4), int(cx), int(cy + 4))


class _PresetChip(QAbstractButton):
    def __init__(self, name: str, colors: Sequence[str], parent=None):
        super().__init__(parent)
        self.name = name
        self.colors = list(colors)
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)  # repaint as the pointer comes and goes
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(f"{name} palette")

    def sizeHint(self) -> QSize:
        return QSize(48, 16)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        painter.setPen(QPen(QColor(theme.LIVE if self.underMouse() else theme.BORDER), 1.0))
        painter.setBrush(_gradient(box, self.colors))
        painter.drawRoundedRect(box, 7.0, 7.0)


class PaletteEditor(QWidget):
    changed = Signal(list)

    def __init__(self, colors: Sequence[str], parent=None):
        super().__init__(parent)
        self._colors = list(colors) or ["#ffffff"]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self._bar = _GradientBar()
        root.addWidget(self._bar)
        self._swatch_row = QHBoxLayout()
        self._swatch_row.setSpacing(4)
        root.addLayout(self._swatch_row)
        presets = QGridLayout()
        presets.setSpacing(4)
        self.preset_chips: List[_PresetChip] = []
        for index, (name, preset) in enumerate(PALETTES.items()):
            chip = _PresetChip(name, preset)
            chip.clicked.connect(lambda _checked=False, p=preset: self.set_colors(p, notify=True))
            presets.addWidget(chip, index // 5, index % 5)
            self.preset_chips.append(chip)
        root.addLayout(presets)
        self._rebuild()

    def colors(self) -> List[str]:
        return list(self._colors)

    def set_colors(self, colors: Sequence[str], notify: bool = False) -> None:
        self._colors = list(colors)[:MAX_PALETTE_COLORS] or ["#ffffff"]
        self._rebuild()
        if notify:
            self.changed.emit(self.colors())

    def _rebuild(self) -> None:
        while self._swatch_row.count():
            item = self._swatch_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, color in enumerate(self._colors):
            swatch = _Swatch(color)
            swatch.clicked.connect(lambda _checked=False, i=index: self._pick(i))
            swatch.removeRequested.connect(lambda i=index: self._remove(i))
            self._swatch_row.addWidget(swatch)
        if len(self._colors) < MAX_PALETTE_COLORS:
            plus = _Swatch(plus=True)
            plus.clicked.connect(self._add)
            self._swatch_row.addWidget(plus)
        self._swatch_row.addStretch(1)
        self._bar.colors = self.colors()
        self._bar.update()

    def _pick(self, index: int) -> None:
        color = QColorDialog.getColor(QColor(self._colors[index]), self, "Palette colour")
        if color.isValid():
            self._colors[index] = color.name()
            self.set_colors(self._colors, notify=True)

    def _remove(self, index: int) -> None:
        if len(self._colors) > 1:
            del self._colors[index]
            self.set_colors(self._colors, notify=True)

    def _add(self) -> None:
        """A new colour at the end: the last one turned a third of the way round the wheel."""
        last = QColor(self._colors[-1])
        hue = last.hsvHue() if last.hsvHue() >= 0 else 0
        added = QColor.fromHsv((hue + 120) % 360, max(last.hsvSaturation(), 200), max(last.value(), 230))
        self.set_colors(self._colors + [added.name()], notify=True)
