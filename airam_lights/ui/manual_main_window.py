"""Main window for the standalone manual (no-music) light control app.

Reuses the Devices & Setup tab as-is from the music visualizer (device
management/local Tuya control is identical regardless of which app is
driving colors), and adds two new tabs for manual color + the Chase overlay.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from .manual_controller import ManualController
from .tabs.devices_tab import DevicesTab
from .tabs.manual_ambient_tab import ManualAmbientTab
from .tabs.manual_chase_tab import ManualChaseTab
from .tabs.manual_color_tab import ManualColorTab
from .tabs.manual_mix_tab import ManualMixTab
from .tabs.manual_white_tab import ManualWhiteTab

logger = logging.getLogger("airam_lights.ui.manual")


class ManualMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Airam Manual Lights")
        self.resize(900, 700)

        self.controller = ManualController()

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        tabs.addTab(DevicesTab(self.controller), "Devices && Setup")
        tabs.addTab(ManualColorTab(self.controller), "Manual Color")
        tabs.addTab(ManualWhiteTab(self.controller), "White Balance")
        tabs.addTab(ManualMixTab(self.controller), "Color + White")
        tabs.addTab(ManualChaseTab(self.controller), "Chase / Rotating Light")
        tabs.addTab(ManualAmbientTab(self.controller), "Ambient Scenes")

    def closeEvent(self, event) -> None:
        logger.info("Shutting down manual control app...")
        self.controller.shutdown()
        super().closeEvent(event)
