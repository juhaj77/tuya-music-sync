"""Main application window: tabbed UI wired to a single AppController."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from .controller import AppController
from .tabs.band_mode_tab import BandModeTab
from .tabs.color_mapping_tab import ColorMappingTab
from .tabs.devices_tab import DevicesTab
from .tabs.diagnostics_tab import DiagnosticsTab
from .tabs.visualizer_tab import VisualizerTab

logger = logging.getLogger("airam_lights.ui")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Airam Music Lights")
        self.resize(1000, 750)

        self.controller = AppController()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tabs.addTab(VisualizerTab(self.controller), "Visualizer")
        self.tabs.addTab(DevicesTab(self.controller), "Devices && Setup")
        self.tabs.addTab(ColorMappingTab(self.controller), "Color Mapping")
        self.tabs.addTab(BandModeTab(self.controller), "8-Band && Per-Lamp")
        self.tabs.addTab(DiagnosticsTab(self.controller), "Diagnostics")

        self.controller.audioError.connect(self._on_audio_error)
        # Queued: the signal usually comes from a button inside one of the
        # very tabs being replaced, which must not be deleted mid-click.
        self.controller.configReplaced.connect(self._rebuild_settings_tabs, Qt.QueuedConnection)

    def _rebuild_settings_tabs(self) -> None:
        """Recreates the settings tabs so every widget shows the current
        config after a preset changed many values at once."""
        current = self.tabs.currentIndex()
        for index, tab_class, title in (
            (2, ColorMappingTab, "Color Mapping"),
            (3, BandModeTab, "8-Band && Per-Lamp"),
        ):
            old = self.tabs.widget(index)
            self.tabs.removeTab(index)
            self.tabs.insertTab(index, tab_class(self.controller), title)
            old.deleteLater()
        self.tabs.setCurrentIndex(current)

    def _on_audio_error(self, message: str) -> None:
        QMessageBox.warning(self, "Audio capture error", message)

    def closeEvent(self, event) -> None:
        logger.info("Shutting down...")
        self.controller.shutdown()
        super().closeEvent(event)
