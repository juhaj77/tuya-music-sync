"""Non-UI controller for the standalone manual (no-music) light control app.

Owns configuration, the lamp manager, and the manual chase/color engine -
deliberately has no audio capture and no FFT/color-mapping visualization
engine at all. Shares the SAME config file (devices, local keys, per-lamp
chase order, chase settings) as the music visualizer app, so tuning done in
one shows up in the other.
"""
from __future__ import annotations

import concurrent.futures
import logging
from types import SimpleNamespace

from PySide6.QtCore import QObject, QTimer, Signal

from ..color.models import Color, WhiteTarget
from ..config.schema import AppConfig, DeviceConfig, PerLampEffect
from ..config.store import ConfigStore
from ..diagnostics.logger import setup_logging
from ..engine.manual_engine import ManualLightController
from ..lamps.manager import LampManager

logger = logging.getLogger("airam_lights.ui.manual")


class ManualController(QObject):
    lampsChanged = Signal()
    logChanged = Signal()

    def __init__(self):
        super().__init__()
        self.log_buffer = setup_logging()
        self.config_store = ConfigStore()
        self.config: AppConfig = self.config_store.load()

        self.lamp_manager = LampManager(
            self.config.network, self.config.color_mapping.smoothing.min_change_threshold
        )
        self.lamp_manager.load_devices(self.config.devices)

        # Constructing ManualLightController also restores + re-pushes
        # whichever static color/white-balance was last applied (see
        # ManualStateConfig) - the app visually matches where you left it.
        self.manual = ManualLightController(self.config, self.lamp_manager)

        # The Devices & Setup tab (reused as-is from the music app) expects
        # `controller.engine.latest_lamp_colors` for its lamp-color swatches.
        # This app has no visualization engine, so provide the same shape
        # backed by the manual engine's own last-pushed colors - initialized
        # via _sync_preview_colors() so it's correct immediately regardless
        # of whether RGB or White was restored above.
        self.engine = SimpleNamespace(latest_lamp_colors={})
        self._sync_preview_colors()

        self._status_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="manual-lamp-status"
        )

        self._chase_timer = QTimer(self)
        self._chase_timer.timeout.connect(self._on_chase_tick)
        self._chase_timer.start(33)  # ~30 Hz; a cheap no-op while chase is disabled

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_lamp_status)
        self._status_timer.start(4000)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self.logChanged.emit)
        self._log_timer.start(1000)

        # Belt-and-suspenders persistence: settings are already saved on a
        # clean exit (see shutdown()), but a periodic autosave means nothing
        # from the session is lost even if the app is killed abruptly
        # (crash, Task Manager, power loss) instead of closed normally.
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(20_000)

    def _on_chase_tick(self) -> None:
        self.manual.tick()
        self._sync_preview_colors()

    def _sync_preview_colors(self) -> None:
        """Keeps controller.engine.latest_lamp_colors (used by the reused
        DevicesTab's swatches) showing whichever mode is actually active -
        real RGB colors in RGB mode, or an approximate preview derived from
        the real brightness/temp targets in White mode."""
        if self.manual.mode == "white":
            self.engine.latest_lamp_colors = {
                device_id: target.to_preview_color() for device_id, target in self.manual.last_white_targets.items()
            }
        else:
            self.engine.latest_lamp_colors = self.manual.last_colors

    # -- devices (same interface the reused DevicesTab expects) -----------------------

    def add_device(self, device_config: DeviceConfig) -> None:
        self.lamp_manager.add_device(device_config)
        self.lampsChanged.emit()

    def remove_device(self, device_id: str) -> None:
        self.lamp_manager.remove_device(device_id)
        self.config.per_lamp_effects.pop(device_id, None)
        self.lampsChanged.emit()

    def get_or_create_effect(self, device_id: str) -> PerLampEffect:
        effect = self.config.per_lamp_effects.get(device_id)
        if effect is None:
            effect = PerLampEffect(device_id=device_id)
            self.config.per_lamp_effects[device_id] = effect
        return effect

    def _refresh_lamp_status(self) -> None:
        self.lamp_manager.refresh_all_status(self._status_executor)
        QTimer.singleShot(1200, self.lampsChanged.emit)

    # -- manual color / chase ----------------------------------------------------------

    def set_color_for_selected(self, color: Color) -> None:
        self.manual.set_color_for_selected(color)
        self._sync_preview_colors()

    def set_black_for_selected(self) -> None:
        self.manual.set_black_for_selected()
        self._sync_preview_colors()

    def set_white_for_selected(self, brightness: float, temp: float) -> None:
        self.manual.set_white_for_selected(brightness, temp)
        self._sync_preview_colors()

    def set_mix_for(self, device_ids, brightness: float, temp: float, color: Color) -> None:
        self.manual.set_mix_for(device_ids, brightness, temp, color)
        self._sync_preview_colors()

    def apply_config_changes(self) -> None:
        self.manual.apply_config(self.config)

    # -- persistence / shutdown --------------------------------------------------------

    def save_config(self, quiet: bool = False) -> None:
        self.config.devices = [d.config for d in self.lamp_manager.devices.values()]
        self.config_store.save(self.config)
        if quiet:
            logger.debug("Configuration auto-saved")
        else:
            logger.info("Configuration saved")

    def _autosave(self) -> None:
        try:
            self.save_config(quiet=True)
        except Exception:
            logger.exception("Periodic autosave failed")

    def shutdown(self) -> None:
        try:
            self.lamp_manager.shutdown()
            self._status_executor.shutdown(wait=False, cancel_futures=True)
            self.save_config()
        except Exception:
            logger.exception("Error during shutdown")
