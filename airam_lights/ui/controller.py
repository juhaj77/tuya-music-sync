"""Non-UI application controller.

Owns configuration, audio capture, the lamp manager and the visualization
engine, and drives them from three independent Qt timers matching the three
independently-configurable rates the spec calls for:

- analysis timer  -> engine.tick_analysis()   (audio_config.analysis_update_hz)
- visual timer    -> engine.tick_visual()     (network_config.visual_update_hz)
- lamp command rate is enforced inside LampManager's per-lamp worker threads
  (network_config.lamp_command_rate_hz), not here.

A separate, independent UI-refresh timer pulls the latest results for
display; it never drives the engine itself, so closing/hiding a tab can
never change visualization timing.

All Qt widgets talk to this controller instead of touching audio/lamps/
engine objects directly, which keeps the UI code declarative and testable.
"""
from __future__ import annotations

import concurrent.futures
import logging

from PySide6.QtCore import QObject, QTimer, Signal

from ..audio.capture import AudioCapture, AudioCaptureError
from ..config.schema import AppConfig, DeviceConfig, PerLampEffect
from ..config.store import ConfigStore
from ..diagnostics.logger import get_log_buffer, setup_logging
from ..engine.visualization_engine import VisualizationEngine
from ..lamps.manager import LampManager

logger = logging.getLogger("airam_lights.ui")


class AppController(QObject):
    spectrumUpdated = Signal(object)  # dsp.fft_engine.SpectrumFrame
    levelUpdated = Signal(float, float)  # rms, peak
    band3Updated = Signal(dict)
    band8Updated = Signal(list)
    lampsChanged = Signal()  # device list / status changed - refresh lamp widgets
    runningChanged = Signal(bool)
    logChanged = Signal()
    audioError = Signal(str)
    configReplaced = Signal()  # many settings changed at once (preset) - rebuild the settings tabs

    def __init__(self):
        super().__init__()
        self.log_buffer = setup_logging()
        self.config_store = ConfigStore()
        self.config: AppConfig = self.config_store.load()

        self.audio = AudioCapture(
            device_index=self.config.audio.device_index,
            source=self.config.audio.source,
            mic_device_index=self.config.audio.mic_device_index,
            mic_gain=self.config.audio.mic_gain,
            block_size=self.config.audio.block_size,
        )
        self.lamp_manager = LampManager(
            self.config.network, self.config.color_mapping.smoothing.min_change_threshold
        )
        self.lamp_manager.load_devices(self.config.devices)
        self.lamp_manager.context_provider = self._show_context
        self.engine = VisualizationEngine(self.config, self.audio, self.lamp_manager)

        self._status_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="lamp-status"
        )

        self._analysis_timer = QTimer(self)
        self._analysis_timer.timeout.connect(self.engine.tick_analysis)
        self._visual_timer = QTimer(self)
        self._visual_timer.timeout.connect(self.engine.tick_visual)

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._on_ui_refresh)
        self._ui_timer.start(66)  # ~15 Hz UI refresh, independent of engine rate

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

    # -- audio ----------------------------------------------------------------

    def start_audio(self) -> bool:
        try:
            self.audio.start()
            return True
        except AudioCaptureError as e:
            logger.error(str(e))
            self.audioError.emit(str(e))
            return False

    def stop_audio(self) -> None:
        self.audio.stop()

    def set_audio_device(self, device_index) -> None:
        was_running = self.audio.is_running()
        self.audio.stop()
        self.audio.device_index = device_index
        self.config.audio.device_index = device_index
        if was_running:
            self.start_audio()

    def set_audio_source(self, source: str) -> None:
        """Switch between "loopback" (system playback) and "microphone"
        (a real recording device) - restarts capture on the new source if
        it was already running, exactly like set_audio_device()."""
        was_running = self.audio.is_running()
        self.audio.stop()
        self.audio.source = source
        self.config.audio.source = source
        if was_running:
            self.start_audio()

    def set_mic_device(self, mic_device_index) -> None:
        was_running = self.audio.is_running()
        self.audio.stop()
        self.audio.mic_device_index = mic_device_index
        self.config.audio.mic_device_index = mic_device_index
        if was_running:
            self.start_audio()

    def set_mic_gain(self, gain: float) -> None:
        self.audio.mic_gain = gain
        self.config.audio.mic_gain = gain

    # -- visualization lifecycle ------------------------------------------------

    def start_visualization(self) -> bool:
        if not self.audio.is_running():
            if not self.start_audio():
                return False
        self.engine.apply_config(self.config)
        self.engine.start()
        interval_analysis = max(1, int(1000 / max(self.config.audio.analysis_update_hz, 1.0)))
        self._analysis_timer.start(interval_analysis)
        self._visual_timer.start(self._visual_interval_ms())
        self.runningChanged.emit(True)
        return True

    def stop_visualization(self) -> None:
        self._analysis_timer.stop()
        self._visual_timer.stop()
        self.engine.stop()
        self.runningChanged.emit(False)

    def is_running(self) -> bool:
        return self.engine.running

    # -- config change plumbing --------------------------------------------------

    def _visual_interval_ms(self) -> int:
        """The color engine computes at least as often as lamps may be sent
        commands - otherwise a higher Lamp command rate would just resend the
        same colors."""
        net = self.config.network
        return max(1, int(1000 / max(net.visual_update_hz, net.lamp_command_rate_hz, 1.0)))

    def apply_config_changes(self) -> None:
        """Call after mutating self.config in place (sliders, mode changes,
        etc.) to propagate it to the engine and lamp manager."""
        self.engine.apply_config(self.config)
        if self._visual_timer.isActive() and self._visual_timer.interval() != self._visual_interval_ms():
            self._visual_timer.setInterval(self._visual_interval_ms())

    def replace_config_settings(self) -> None:
        """Like apply_config_changes(), after a change too broad for the
        widgets to follow one by one (e.g. loading a preset): also asks the
        main window to rebuild its settings tabs from the new values."""
        self.apply_config_changes()
        self.configReplaced.emit()

    def _show_context(self) -> str:
        """One line on what the show is doing, for stuck-lamp reports in the log."""
        cfg = self.config
        bs = cfg.color_mapping.beat_sync
        parts = [f"mode={cfg.color_mapping.mode}"]
        if cfg.color_mapping.mode == "beat_sync":
            parts.append(f"fade={'on' if bs.fade_brightness else 'off'}")
            parts.append(f"glide_hue={bs.hue_glide_timing if bs.glide_hue else 'off'}")
            parts.append(f"white_pulses={'on' if bs.white_pulse_enabled else 'off'}")
            parts.append(f"sequencer={'on' if cfg.sequencer.enabled and cfg.rhythm.shared_clock else 'off'}")
        parts.append(f"chase={cfg.chase.sync_mode if cfg.chase.enabled else 'off'}")
        parts.append(f"group_switch={cfg.group_switch.sync_mode if cfg.group_switch.enabled else 'off'}")
        parts.append(f"lamp_command_rate={cfg.network.lamp_command_rate_hz:g}/s")
        parts.append(f"transitions={cfg.network.lamp_transitions}")
        parts.append(f"min_change={cfg.color_mapping.smoothing.min_change_threshold:g}")
        return "show: " + ", ".join(parts)

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

    # -- lamps ------------------------------------------------------------------

    def add_device(self, device_config: DeviceConfig) -> None:
        self.lamp_manager.add_device(device_config)
        self.lampsChanged.emit()

    def remove_device(self, device_id: str) -> None:
        self.lamp_manager.remove_device(device_id)
        self.config.per_lamp_effects.pop(device_id, None)
        self.lampsChanged.emit()

    def get_or_create_effect(self, device_id: str) -> PerLampEffect:
        eff = self.config.per_lamp_effects.get(device_id)
        if eff is None:
            eff = PerLampEffect(device_id=device_id)
            self.config.per_lamp_effects[device_id] = eff
        return eff

    def _refresh_lamp_status(self) -> None:
        self.lamp_manager.refresh_all_status(self._status_executor)
        QTimer.singleShot(1200, self.lampsChanged.emit)

    # -- polling for UI display --------------------------------------------------

    def _on_ui_refresh(self) -> None:
        if self.audio.is_running():
            rms, peak = self.audio.get_level()
            self.levelUpdated.emit(rms, peak)
            if self.engine.latest_frame is not None:
                self.spectrumUpdated.emit(self.engine.latest_frame)
        self.band3Updated.emit(self.engine.latest_band3_levels)
        self.band8Updated.emit(self.engine.latest_band8_levels)

    # -- shutdown -----------------------------------------------------------------

    def shutdown(self) -> None:
        try:
            self.stop_visualization()
            self.stop_audio()
            self.lamp_manager.shutdown()
            self._status_executor.shutdown(wait=False, cancel_futures=True)
            self.save_config()
        except Exception:
            logger.exception("Error during shutdown")
