"""A block: a tinted panel grouping related sections under a title and a
one-line description - so a page with a lot on it reads as a few parts
rather than one long list of sections. The Global Effects tab has three:
the beat clock, Chase & Group Switch, and the pulses.

    block = Block("Pulses & sequencer", "What they are...", tint="pulses")
    block.content_layout().addWidget(section)

`tint` is one of theme.BLOCK_TINTS: the block's background, its border and
its sections' borders, and its title's colour."""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .. import theme


class Block(QFrame):
    def __init__(self, title: str, description: str = "", tint: str = "clock", parent=None):
        super().__init__(parent)
        self.setProperty("role", "block")
        self.setProperty("tint", tint)
        self.setFrameShape(QFrame.NoFrame)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(4)
        self.title = QLabel(title)
        self.title.setProperty("role", "blockTitle")
        self.title.setProperty("tint", tint)
        font = theme.title_font(self.title.font())
        font.setPointSizeF(theme.FONT_SIZE_PT + 0.5)
        self.title.setFont(font)
        root.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        root.addWidget(self.description)
        root.addSpacing(6)

        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(8)
        root.addLayout(self._content_layout)

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))
