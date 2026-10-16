"""A collapsible settings section: a card with a header row - a chevron,
the title in small capitals, an optional note, and for a feature an on/off
switch - and the settings underneath, shown or hidden by clicking the
header. So a page can list every feature in one glance, switched on or off
right there, and open only the one being tuned.

    section = CollapsibleSection("Dark pulses", key="beat/dark", checked=True, tooltip="...")
    section.toggle.toggled.connect(...)        # the switch: QCheckBox's API (see ToggleSwitch)
    section.content_layout().addWidget(...)    # the settings
    show_note(section.note, "why it's inactive")  # shows in the header, even when collapsed

Which sections are open is remembered between runs (ui.ini, by `key`);
on first run they all start closed, so the page is a short list of features
rather than every setting at once.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...config.store import default_config_dir
from .. import theme
from .toggle_switch import ToggleSwitch


def _settings() -> QSettings:
    """The UI's own state (which sections are open), as ui.ini in the app's
    settings folder next to config.json."""
    return QSettings(str(default_config_dir() / "ui.ini"), QSettings.IniFormat)


class _Chevron(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.expanded = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(theme.MUTED if self.isEnabled() else theme.DIM)
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.expanded:
            points = [QPointF(3.5, 5.5), QPointF(7.0, 9.0), QPointF(10.5, 5.5)]
        else:
            points = [QPointF(5.5, 3.5), QPointF(9.0, 7.0), QPointF(5.5, 10.5)]
        painter.drawPolyline(points)


class _Header(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "sectionHeader")
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class CollapsibleSection(QFrame):
    expandedChanged = Signal(bool)

    def __init__(
        self,
        title: str,
        key: Optional[str] = None,
        checkable: bool = False,
        checked: bool = False,
        tooltip: str = "",
        expanded: bool = False,
        nested: bool = False,
        parent=None,
    ):
        """`key`: remembers whether the section is open between runs (None =
        not remembered). `checkable`: a feature with an on/off switch in the
        header (`checked` its state). `expanded`: whether it starts open when
        there's no remembered state (default: closed). `nested`: a section
        inside another's settings."""
        super().__init__(parent)
        self._key = key
        self.setProperty("role", "section")
        self.setProperty("nested", "true" if nested else "false")
        self.setFrameShape(QFrame.NoFrame)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _Header()
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 9, 12, 9)
        header_layout.setSpacing(10)
        self._chevron = _Chevron()
        header_layout.addWidget(self._chevron)
        self._title = QLabel(title)
        self._title.setProperty("role", "sectionTitle")
        self._title.setFont(theme.title_font(self._title.font()))
        header_layout.addWidget(self._title)
        # Says why the feature is inactive, or what it's doing - visible with
        # the section closed too (see widgets.musical.show_note).
        self.note = QLabel()
        self.note.setProperty("role", "note")
        self.note.setWordWrap(True)
        self.note.hide()
        # In a box of its own, so the header's free space stays here (and the
        # switch at the right edge) whether the note is shown or hidden.
        note_box = QWidget()
        note_layout = QHBoxLayout(note_box)
        note_layout.setContentsMargins(0, 0, 0, 0)
        note_layout.addWidget(self.note)
        header_layout.addWidget(note_box, stretch=1)
        self.toggle: Optional[ToggleSwitch] = None
        if checkable:
            self.toggle = ToggleSwitch(checked)
            self.toggle.lookChanged.connect(self._mirror_toggle)
            header_layout.addWidget(self.toggle)
        self._header.clicked.connect(self.toggle_expanded)
        root.addWidget(self._header)

        self._content = QWidget()
        self._content.setProperty("role", "sectionContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 2, 12, 12)
        self._content_layout.setSpacing(6)
        root.addWidget(self._content)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        if tooltip:
            if self.toggle is not None:
                self.toggle.setToolTip(tooltip)  # _mirror_toggle copies it to the header
            else:
                self._header.setToolTip(tooltip)
        self._mirror_toggle()

        remembered = self._remembered()
        if remembered is None:
            remembered = expanded
        self.set_expanded(remembered, remember=False)

    # -- open / closed -------------------------------------------------------------------

    def is_expanded(self) -> bool:
        return self._chevron.expanded

    def set_expanded(self, expanded: bool, remember: bool = True) -> None:
        self._chevron.expanded = expanded
        self._chevron.update()
        self._content.setVisible(expanded)
        if remember and self._key:
            _settings().setValue(f"sections/{self._key}", expanded)
        self.expandedChanged.emit(expanded)

    def toggle_expanded(self) -> None:
        self.set_expanded(not self.is_expanded())

    def _remembered(self) -> Optional[bool]:
        if not self._key:
            return None
        value = _settings().value(f"sections/{self._key}")
        if value is None:
            return None
        return value in (True, "true", "True", 1, "1")

    # -- content -------------------------------------------------------------------------

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def content_widget(self) -> QWidget:
        return self._content

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    # -- the switch's state on the header --------------------------------------------------

    def _mirror_toggle(self) -> None:
        toggle = self.toggle
        if toggle is None:
            return
        self._header.setToolTip(toggle.toolTip())
        off = "false" if toggle.isChecked() else "true"
        if self._title.property("off") != off:
            self._title.setProperty("off", off)
            self._title.style().unpolish(self._title)
            self._title.style().polish(self._title)
        self._title.setEnabled(toggle.isEnabled())
        self._chevron.setEnabled(toggle.isEnabled())
