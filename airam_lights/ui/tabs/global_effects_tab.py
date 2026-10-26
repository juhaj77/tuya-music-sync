"""Global Effects tab: what every colour mode shares, in three tinted blocks.

1. Beat clock - the Rhythm box, first since everything else here can follow
   it (ui/tabs/rhythm_panel.py).
2. Chase & Group Switch - the overlays on top of the colour mode
   (ui/tabs/overlays_panel.py).
3. Pulses & sequencer - the Dark and White pulses and the Pulse sequencer
   with its strobes and sweeps (ui/tabs/pulses_panel.py).

What the active colour mode doesn't use is greyed out, with the reason. The
mode is picked on the Visualizer tab and the lamp transitions on the Color
Mapping tab, so the blocks catch up whenever this tab is shown."""
from __future__ import annotations

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from ..controller import AppController
from ..widgets.block import Block
from .overlays_panel import OverlaysPanel
from .pulses_panel import MODE_NAMES, PulsesPanel
from .rhythm_panel import RhythmPanel

_PULSES_TEXT = (
    "Dark and white pulses, and the Pulse sequencer that places them on the beat grid with strobes and "
    "sweeps - one set of settings for every colour mode."
)


class GlobalEffectsTab(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        root.setSpacing(12)

        self.rhythm = RhythmPanel(controller)
        self.clock_block = Block(
            "Beat clock",
            "The tempo, beats and bars that Beat Sync, Chase and Group Switch on 'clock', and the Pulse "
            "sequencer all go by.",
            tint="clock",
        )
        self.clock_block.content_layout().addWidget(self.rhythm)
        root.addWidget(self.clock_block)

        self.overlays = OverlaysPanel(controller)
        self.overlays_block = Block(
            "Chase & Group Switch",
            "Overlays on top of the colour mode: a highlight travelling round the lamps, or one lamp group "
            "at a time.",
            tint="overlays",
        )
        self.overlays_block.content_layout().addWidget(self.overlays)
        root.addWidget(self.overlays_block)

        self.pulses = PulsesPanel(controller)
        self.pulses_block = Block("Pulses & sequencer", _PULSES_TEXT, tint="pulses")
        self.pulses_block.content_layout().addWidget(self.pulses)
        root.addWidget(self.pulses_block)
        root.addStretch(1)

        # Lock to tempo and 'follow the shared clock' decide whether the sequencer can run.
        self.rhythm.changed.connect(self.pulses.update_states)
        self.update_states()

    def update_states(self) -> None:
        self.overlays.update_states()
        self.pulses.update_states()
        mode = self.controller.config.color_mapping.mode
        self.pulses_block.set_description(
            f"{_PULSES_TEXT} Shown for the active colour mode, {MODE_NAMES.get(mode, mode)} (picked on the "
            "Visualizer tab)."
        )

    def showEvent(self, event) -> None:
        # The colour mode and the lamp transitions are picked on other tabs - catch up with them here.
        super().showEvent(event)
        self.update_states()
