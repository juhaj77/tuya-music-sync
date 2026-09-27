"""Devices & Setup tab: the lamp list, selection, groups, and add/edit/scan."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...color.models import Color
from ...config.schema import DeviceConfig
from ..controller import AppController
from ..widgets.device_dialog import DeviceDialog
from ..widgets.lamp_tile import LampTile

logger = logging.getLogger("airam_lights.ui")


class DevicesTab(QWidget):
    _keysRefreshed = Signal(str)  # result text, emitted from the worker thread

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._tiles: "dict[str, LampTile]" = {}

        root = QVBoxLayout(self)

        # -- lamp list -----------------------------------------------------------
        list_box = QGroupBox("Lamps")
        list_layout = QVBoxLayout(list_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.addStretch(1)
        scroll.setWidget(self._list_container)
        list_layout.addWidget(scroll)
        root.addWidget(list_box, stretch=1)

        selection_row = QHBoxLayout()
        for text, handler in [
            ("Select All", self._select_all),
            ("Clear", self._clear_selection),
            ("Test Selected", self._test_selected),
            ("Refresh Status", self._refresh_status),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            selection_row.addWidget(btn)
        selection_row.addStretch(1)
        root.addLayout(selection_row)

        # -- groups ---------------------------------------------------------------
        group_box = QGroupBox("Lamp groups")
        group_row = QHBoxLayout(group_box)
        self.group_combo = QComboBox()
        self.group_combo.addItem("(none)")
        group_row.addWidget(self.group_combo, stretch=1)
        apply_btn = QPushButton("Apply Group")
        apply_btn.clicked.connect(self._apply_group)
        group_row.addWidget(apply_btn)
        save_btn = QPushButton("Save Current Selection As...")
        save_btn.clicked.connect(self._save_group)
        group_row.addWidget(save_btn)
        root.addWidget(group_box)

        # -- device management --------------------------------------------------
        manage_row = QHBoxLayout()
        add_btn = QPushButton("Add Device Manually")
        add_btn.clicked.connect(self._add_device)
        scan_btn = QPushButton("Scan Network")
        scan_btn.clicked.connect(self._scan_network)
        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(self._edit_selected)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        self.keys_btn = QPushButton("Refresh keys from Tuya cloud")
        self.keys_btn.setToolTip(
            "A lamp's local key changes when it's re-paired (reset and added again in the phone app) - "
            "after that the app can't reach it, even though the phone app still works through the "
            "cloud. This fetches every lamp's current key with the Tuya IoT credentials the setup "
            "wizard saved (tinytuya.json) and updates the ones that changed."
        )
        self.keys_btn.clicked.connect(self._refresh_keys)
        self._keysRefreshed.connect(self._on_keys_refreshed)
        for b in (add_btn, scan_btn, edit_btn, remove_btn, self.keys_btn):
            manage_row.addWidget(b)
        manage_row.addStretch(1)
        root.addLayout(manage_row)

        self.hint_label = QLabel(
            "New here? Run tools/setup_wizard.py (or AiramSetupWizard.exe) once to pull local_key values from the "
            "Tuya cloud, or use 'Scan Network' to find IPs/device IDs and enter the key "
            "manually. See README.md for details."
        )
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #999;")
        root.addWidget(self.hint_label)

        self._current_selected_id: "str | None" = None
        controller.lampsChanged.connect(self.refresh)
        self.refresh()
        self._rebuild_groups()

    # -- rendering ------------------------------------------------------------------

    def refresh(self) -> None:
        for device_id, dev in self.controller.lamp_manager.devices.items():
            tile = self._tiles.get(device_id)
            if tile is None:
                tile = LampTile(device_id, dev.config.name)
                tile.selectionChanged.connect(self._on_tile_selection)
                tile.mousePressEvent = self._make_select_handler(device_id, tile)
                self._tiles[device_id] = tile
                self._list_layout.insertWidget(self._list_layout.count() - 1, tile)
            tile.set_name(dev.config.name)
            tile.set_ip(dev.config.ip)
            tile.set_selected(dev.config.selected)
            tile.set_online(dev.status.online, dev.status.last_error or "")
            tile.set_latency(dev.status.last_latency_ms)
            color = self.controller.engine.latest_lamp_colors.get(device_id)
            if color:
                r, g, b = color.to_rgb255()
                tile.set_color(r, g, b)

        for device_id in list(self._tiles.keys()):
            if device_id not in self.controller.lamp_manager.devices:
                self._list_layout.removeWidget(self._tiles[device_id])
                self._tiles[device_id].deleteLater()
                del self._tiles[device_id]

    def _make_select_handler(self, device_id: str, tile: LampTile):
        def handler(event):
            self._current_selected_id = device_id
            for t in self._tiles.values():
                t.setStyleSheet("")
            tile.setStyleSheet("background-color: rgba(100,140,255,40);")

        return handler

    # -- selection --------------------------------------------------------------------

    def _on_tile_selection(self, device_id: str, selected: bool) -> None:
        self.controller.lamp_manager.set_selected(device_id, selected)

    def _select_all(self) -> None:
        self.controller.lamp_manager.select_all()
        self.refresh()

    def _clear_selection(self) -> None:
        self.controller.lamp_manager.clear_selection()
        self.refresh()

    def _test_selected(self) -> None:
        ids = self.controller.lamp_manager.selected_device_ids()
        if not ids:
            QMessageBox.information(self, "No selection", "Select at least one lamp first.")
            return

        def _run():
            import time

            for r, g, b in [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]:
                self.controller.lamp_manager.push_colors({i: Color(r / 255, g / 255, b / 255) for i in ids})
                time.sleep(1.0)

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_status(self) -> None:
        self.controller._refresh_lamp_status()

    # -- groups --------------------------------------------------------------------------

    def _rebuild_groups(self) -> None:
        self.group_combo.clear()
        self.group_combo.addItem("(none)")
        for name in self.controller.config.groups.keys():
            self.group_combo.addItem(name)

    def _apply_group(self) -> None:
        name = self.group_combo.currentText()
        if name == "(none)":
            return
        ids = self.controller.config.groups.get(name, [])
        self.controller.lamp_manager.apply_group(ids)
        self.refresh()

    def _save_group(self) -> None:
        name, ok = QInputDialog.getText(self, "Save group", "Group name:")
        if not ok or not name.strip():
            return
        ids = self.controller.lamp_manager.selected_device_ids()
        self.controller.config.groups[name.strip()] = ids
        self._rebuild_groups()
        self.controller.save_config()

    # -- device management -----------------------------------------------------------------

    def _add_device(self) -> None:
        dialog = DeviceDialog(parent=self)
        if dialog.exec():
            cfg = dialog.get_device_config()
            if not cfg.id or not cfg.ip:
                QMessageBox.warning(self, "Missing info", "Device ID and IP are required.")
                return
            self.controller.add_device(cfg)
            self.controller.save_config()

    def _edit_selected(self) -> None:
        if not self._current_selected_id:
            QMessageBox.information(self, "No selection", "Click a lamp row first, then Edit Selected.")
            return
        dev = self.controller.lamp_manager.devices.get(self._current_selected_id)
        if not dev:
            return
        dialog = DeviceDialog(dev.config, parent=self)
        if dialog.exec():
            cfg = dialog.get_device_config()
            self.controller.lamp_manager.update_device_config(cfg)
            self.controller.save_config()
            self.refresh()

    def _remove_selected(self) -> None:
        if not self._current_selected_id:
            QMessageBox.information(self, "No selection", "Click a lamp row first, then Remove Selected.")
            return
        self.controller.remove_device(self._current_selected_id)
        self.controller.save_config()
        self._current_selected_id = None

    def _refresh_keys(self) -> None:
        self.keys_btn.setEnabled(False)
        self.hint_label.setText("Fetching the lamps' current keys from the Tuya cloud...")

        def _run():
            try:
                report = self.controller.lamp_manager.refresh_keys_from_cloud()
            except RuntimeError as e:
                self._keysRefreshed.emit(f"Couldn't refresh keys: {e}")
                return
            except Exception as e:
                logger.exception("Refreshing keys from the Tuya cloud failed")
                self._keysRefreshed.emit(f"Couldn't refresh keys: {e}")
                return
            updated = [name for name, result in report.items() if result == "updated"]
            missing = [name for name, result in report.items() if result == "not found"]
            text = f"Keys updated: {', '.join(updated)}." if updated else "All keys were already up to date."
            if missing:
                text += f" Not found in the cloud: {', '.join(missing)}."
            if not updated:
                text += (
                    " If a lamp still doesn't respond, its local connection is probably stuck - switch it "
                    "off and on at the wall; the app picks it up again automatically."
                )
            self._keysRefreshed.emit(text)

        threading.Thread(target=_run, daemon=True).start()

    def _on_keys_refreshed(self, text: str) -> None:
        self.keys_btn.setEnabled(True)
        self.hint_label.setText(text)
        if text.startswith("Keys updated"):
            self.controller.save_config()

    def _scan_network(self) -> None:
        self.hint_label.setText("Scanning LAN for Tuya devices...")

        def _run():
            results = self.controller.lamp_manager.discover(timeout=8.0)
            summary = "\n".join(f"{d.ip}  id={d.device_id}  v{d.version}" for d in results) or "No devices found."
            self.hint_label.setText(
                "Scan results (local_key is not broadcast - add each device manually with "
                "the key from the setup wizard):\n" + summary
            )

        threading.Thread(target=_run, daemon=True).start()
