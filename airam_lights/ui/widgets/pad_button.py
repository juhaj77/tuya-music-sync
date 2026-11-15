"""A pad of the Real Time tab: a big button played like a key of an
instrument, with the mouse or its keyboard key (shown in its corner).

- A hold pad is on for as long as it's held down. Shift+click or a
  right-click latches it on; a click lets it go again.
- A tap pad fires once per press.

    pad = PadButton("Blackout", "3", hold=True, tooltip="...")
    pad.engaged.connect(lambda on: ...)   # hold pads
    pad.fired.connect(...)                # tap pads
    pad.key_down() / pad.key_up()         # the tab's keyboard handling
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

from .. import theme

_FLASH_S = 0.22  # how long a tap pad glows after it fired


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class PadButton(QAbstractButton):
    engaged = Signal(bool)
    fired = Signal()

    def __init__(self, title: str, key_text: str, hold: bool, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.key_text = key_text
        self.hold = hold
        self._on = False
        self._latched = False
        self._fired_at = -1e9
        self.setFocusPolicy(Qt.NoFocus)  # played, not tabbed to - and a click never takes the keys away
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover)  # repaint as the pointer comes and goes
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if tooltip:
            how = (
                "Hold it down; Shift+click or right-click latches it on, a click lets go."
                if hold else "Fires once per press."
            )
            self.setToolTip(f"{tooltip}\n\nKey: {key_text}. {how}")

    def sizeHint(self) -> QSize:
        return QSize(74, 50)

    def minimumSizeHint(self) -> QSize:
        return QSize(52, 50)

    # -- state ------------------------------------------------------------------------------

    def is_on(self) -> bool:
        return self._on

    def is_latched(self) -> bool:
        return self._latched

    def _set_on(self, on: bool, latched: bool = False) -> None:
        self._latched = on and latched
        if on != self._on:
            self._on = on
            self.engaged.emit(on)
        self.update()

    def _fire(self) -> None:
        self._fired_at = time.perf_counter()
        self.fired.emit()
        self.update()
        QTimer.singleShot(int(_FLASH_S * 500), self.update)
        QTimer.singleShot(int(_FLASH_S * 1000) + 20, self.update)

    def release(self) -> None:
        """Lets go of the pad, latched or not (Esc, or the tab going out of view)."""
        if self.hold:
            self._set_on(False)

    def release_momentary(self) -> None:
        """Lets go of the pad unless it's latched (the window lost the keys)."""
        if self.hold and self._on and not self._latched:
            self._set_on(False)

    # -- the keyboard (driven by the tab) -------------------------------------------------------

    def key_down(self) -> None:
        if not self.isEnabled():
            return
        if not self.hold:
            self._fire()
        elif self._latched:
            self._set_on(False)
        else:
            self._set_on(True)

    def key_up(self) -> None:
        if self.hold and self._on and not self._latched:
            self._set_on(False)

    # -- the mouse -----------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if not self.isEnabled():
            return
        latch = event.button() == Qt.RightButton or bool(event.modifiers() & Qt.ShiftModifier)
        if event.button() not in (Qt.LeftButton, Qt.RightButton):
            return
        if not self.hold:
            self._fire()
        elif self._latched:
            self._set_on(False)
        else:
            self._set_on(True, latched=latch)

    def mouseReleaseEvent(self, event) -> None:
        if self.hold and self._on and not self._latched:
            self._set_on(False)

    # -- drawing --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        accent = QColor(theme.LIVE if self.hold else theme.ACCENT)
        dim = QColor(theme.LIVE_DIM if self.hold else theme.ACCENT_DIM)
        glow = 1.0 if self._on else max(0.0, 1.0 - (time.perf_counter() - self._fired_at) / _FLASH_S)
        enabled = self.isEnabled()

        fill = _mix(QColor(theme.CARD_ALT), dim, glow)
        border = _mix(QColor(theme.BORDER), accent, max(glow, 0.45 if self.underMouse() and enabled else 0.0))
        painter.setPen(QPen(border, 1.4 if glow > 0.0 else 1.0))
        painter.setBrush(fill if enabled else QColor(theme.CARD))
        painter.drawRoundedRect(box, 7.0, 7.0)

        # A line along the bottom says which kind of pad it is: pink = hold, blue = tap.
        line = QColor(accent)
        line.setAlpha(255 if glow > 0.0 else 150 if enabled else 50)
        painter.setPen(Qt.NoPen)
        painter.setBrush(line)
        painter.drawRoundedRect(QRectF(box.left() + 8.0, box.bottom() - 4.0, box.width() - 16.0, 2.0), 1.0, 1.0)

        key_font = QFont(self.font())
        key_font.setPointSizeF(max(6.5, theme.FONT_SIZE_PT - 3.0))
        painter.setFont(key_font)
        painter.setPen(QColor(theme.MUTED if enabled else theme.DIM))
        painter.drawText(box.adjusted(6.0, 3.0, -6.0, 0.0), Qt.AlignLeft | Qt.AlignTop, self.key_text)
        if self._latched:
            painter.setPen(accent)
            painter.drawText(box.adjusted(6.0, 3.0, -6.0, 0.0), Qt.AlignRight | Qt.AlignTop, "LATCH")

        title_font = QFont(self.font())
        title_font.setPointSizeF(theme.FONT_SIZE_PT - 0.5)
        title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#ffffff") if glow > 0.3 else QColor(theme.TEXT if enabled else theme.DIM))
        # Two short lines when the name doesn't fit on one ("Color strobe").
        painter.drawText(box.adjusted(3.0, 11.0, -3.0, -5.0), Qt.AlignCenter | Qt.TextWordWrap, self.title)
