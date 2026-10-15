"""Main application window: tabbed UI wired to a single AppController, with
the info panel (what the music sync hears and does) along the bottom."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from .controller import AppController
from .tabs.color_mapping_tab import ColorMappingTab
from .tabs.devices_tab import DevicesTab
from .tabs.diagnostics_tab import DiagnosticsTab
from .tabs.overlays_tab import OverlaysTab
from .tabs.visualizer_tab import VisualizerTab
from .widgets.info_panel import InfoPanel

logger = logging.getLogger("airam_lights.ui")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Airam Music Lights")
        self.resize(1000, 750)

        self.controller = AppController()

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)
        # Outside the tabs, so it never scrolls away and shows on every tab.
        self.info_panel = InfoPanel(self.controller)
        layout.addWidget(self.info_panel)
        self.setCentralWidget(central)

        self.tabs.addTab(VisualizerTab(self.controller), "Visualizer")
        self.tabs.addTab(DevicesTab(self.controller), "Devices && Setup")
        self.tabs.addTab(ColorMappingTab(self.controller), "Color Mapping")
        self.tabs.addTab(OverlaysTab(self.controller), "Chase && Group Switch")
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
            (3, OverlaysTab, "Chase && Group Switch"),
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
