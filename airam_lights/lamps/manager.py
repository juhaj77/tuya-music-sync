"""Multi-lamp manager: one independent worker thread per lamp.

Design notes (see README.md "Network / performance"):

- Each lamp gets its own daemon thread with a single-slot "latest target"
  mailbox (not a queue) - if several color updates arrive before the worker
  gets to send, only the *latest* is ever sent. This is exactly the coalescing
  behavior the spec asks for: never build a backlog, never spam the bulb.
- Each worker independently rate-limits itself to `lamp_command_rate_hz` and
  skips sends whose color barely changed (`min_change_threshold`), so a lamp
  that's essentially steady stops generating network traffic even though the
  visual engine is still running at its own (usually higher) update rate.
- A failing/slow lamp only affects its own thread - it can never block or
  slow down the other 7 lamps, or the UI.
- This uses plain `threading`, not asyncio: tinytuya is a blocking socket
  library, and mixing asyncio with PySide6's event loop adds real complexity
  for no benefit here, since each lamp already gets full concurrency via its
  own OS thread.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..color.models import Color, WhiteTarget
from ..config.schema import DeviceConfig, NetworkConfig
from . import cloud_keys
from .discovery import DiscoveredDevice, find_device_address, scan_network
from .tuya_device import KEY_CODES, UNREACHABLE_CODES, LampDevice

logger = logging.getLogger("airam_lights.lamps")

# After this many CONSECUTIVE white-mode send failures (see
# LampWorker._consecutive_white_failures), a device is marked white-mode
# unsupported and stops attempting white sends until the cooldown below
# elapses - see the comment on that field.
_WHITE_UNSUPPORTED_THRESHOLD = 5

# How long a device sits out white-mode attempts after being marked
# unsupported, before ONE retry is allowed again (a plain circuit breaker,
# not a permanent give-up). A genuinely incompatible bulb just re-trips this
# every ~30s at negligible cost either way; the point is a lamp that failed
# 5 times in a row purely from a transient blip (Wi-Fi jitter, a brief
# contention window) isn't locked out of the effect for the rest of a long
# session, which in practice looked like "most lamps stop doing the white
# flash" after running for a while - each one only needed bad luck on 5
# white attempts, at some point, to drop out permanently.
_WHITE_RETRY_COOLDOWN_S = 30.0

# After this many CONSECUTIVE failures of ANY kind (RGB colour or white -
# see LampWorker._consecutive_failures), the worker rebuilds the tinytuya
# connection from scratch (see LampDevice.reconnect()) instead of continuing
# to retry over what may be a dead persistent socket. Deliberately higher
# than _WHITE_UNSUPPORTED_THRESHOLD above - white-only failures should give
# up on white specifically first, without necessarily meaning the whole
# connection is dead, so this only fires once failures are clearly not
# limited to just the white-mode DP layout issue that threshold covers.
_RECONNECT_THRESHOLD = 8

# If this many reconnects in a row haven't brought the lamp back, stop
# retrying at the (backed-off) send rate and park the lamp in a "dormant"
# state: it's skipped by the show, and a single connectivity probe runs every
# _DORMANT_PROBE_INTERVAL_S instead - with automatic fixes for the two causes
# that don't go away on their own (a new IP from DHCP, a new local_key after
# re-pairing). The moment a probe succeeds, the lamp rejoins the show.
_DORMANT_AFTER_RECONNECTS = 2
_DORMANT_PROBE_INTERVAL_S = 15.0
_REDISCOVER_INTERVAL_S = 120.0
_CLOUD_KEY_INTERVAL_S = 600.0


@dataclass
class WorkerStats:
    commands_sent: int = 0
    commands_skipped_unchanged: int = 0
    commands_failed: int = 0
    last_send_time: Optional[float] = None


class LampWorker(threading.Thread):
    def __init__(self, device: LampDevice, network_cfg: NetworkConfig, min_change_threshold: float):
        super().__init__(daemon=True, name=f"lamp-worker-{device.config.name}")
        self.device = device
        self.network_cfg = network_cfg
        self.min_change_threshold = min_change_threshold
        self.stats = WorkerStats()

        # Exactly one of these two is live at a time - setting one clears the
        # other, so a lamp switching between RGB and White modes never sends
        # a stale target from the mode it just left.
        self._target_color: Optional[Color] = None
        self._target_white: Optional[WhiteTarget] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_sent_color: Optional[Color] = None
        self._last_sent_white: Optional[WhiteTarget] = None
        self._last_send_time = 0.0
        self._consecutive_failures = 0
        self._colour_mode_ensured = False
        self._white_mode_ensured = False
        # Separate from _consecutive_failures above (which also counts RGB
        # failures and resets on ANY successful send, RGB included) - some
        # bulbs' WHITE work_mode command sequence fails every single time
        # (seen in practice: tinytuya can never detect their DP layout well
        # enough to compute a white-mode value, seemingly a genuine per-bulb
        # firmware/model difference, not a transient network hiccup), while
        # their RGB colour path works fine and keeps succeeding/resetting
        # the shared counter. Give up specifically on white sends for this
        # device after enough CONSECUTIVE white failures, so a chronically
        # unsupported bulb logs one clear warning instead of retrying (and
        # warning) forever, and simply keeps whatever it was last showing
        # for the remainder of white-mode requests instead of erroring.
        self._consecutive_white_failures = 0
        self._white_unsupported = False
        self._white_retry_after = 0.0  # perf_counter() timestamp - see _WHITE_RETRY_COOLDOWN_S

        # See _DORMANT_AFTER_RECONNECTS.
        self._reconnects_without_success = 0
        self.dormant = False
        self._next_probe_at = 0.0
        self._next_rediscover_at = 0.0
        self._next_cloud_key_at = 0.0

    def set_target(self, color: Color) -> None:
        with self._lock:
            self._target_color = color
            self._target_white = None
        self._wake.set()

    def set_white_target(self, target: WhiteTarget) -> None:
        with self._lock:
            self._target_white = target
            self._target_color = None
        self._wake.set()

    def update_network_config(self, network_cfg: NetworkConfig, min_change_threshold: float) -> None:
        self.network_cfg = network_cfg
        self.min_change_threshold = min_change_threshold

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _effective_interval(self) -> float:
        base = 1.0 / max(self.network_cfg.lamp_command_rate_hz, 0.1)
        if self.network_cfg.auto_backoff and self._consecutive_failures > 0:
            # Simple capped exponential backoff: 2x, 4x, 8x ... up to 16x the
            # configured interval, so a lamp that's offline doesn't get hammered.
            backoff = min(2 ** self._consecutive_failures, 16)
            return base * backoff
        return base

    def run(self) -> None:
        while not self._stop.is_set():
            got_signal = self._wake.wait(timeout=0.5)
            if self._stop.is_set():
                break
            if self.dormant:
                self._wake.clear()
                if time.perf_counter() >= self._next_probe_at:
                    self._probe_and_recover()
                continue
            if not got_signal:
                continue
            self._wake.clear()

            with self._lock:
                color, white = self._target_color, self._target_white

            if color is None and white is None:
                continue

            interval = self._effective_interval()
            elapsed = time.perf_counter() - self._last_send_time
            if elapsed < interval:
                time.sleep(interval - elapsed)
                # Newer targets may have arrived while we slept - use them.
                with self._lock:
                    color, white = self._target_color, self._target_white

            if white is not None:
                if self._white_unsupported and time.perf_counter() < self._white_retry_after:
                    continue
                if self._last_sent_white is not None and white.distance(self._last_sent_white) < self.min_change_threshold:
                    self.stats.commands_skipped_unchanged += 1
                    continue
                self._send_white(white)
            elif color is not None:
                if self._last_sent_color is not None and color.distance(self._last_sent_color) < self.min_change_threshold:
                    self.stats.commands_skipped_unchanged += 1
                    continue
                self._send_color(color)

    def _on_send_success(self) -> None:
        self._last_send_time = time.perf_counter()
        self.stats.commands_sent += 1
        self.stats.last_send_time = self._last_send_time
        self._consecutive_failures = 0
        self._reconnects_without_success = 0
        self.device.status.online = True
        self.device.status.last_error = None

    def _on_send_failure(self, e: Exception) -> None:
        # Must also stamp _last_send_time, exactly like the success path -
        # _effective_interval()'s backoff is entirely driven by elapsed time
        # since this timestamp, so leaving it frozen at its last SUCCESS (or
        # the 0.0 default, if there never was one) makes every subsequent
        # attempt see a huge "elapsed", permanently defeating the interval
        # check and retrying at the engine's full push rate instead of
        # backing off - exactly what turns one failing lamp into a log-spam
        # retry storm instead of a graceful, increasingly-spaced-out retry.
        self._last_send_time = time.perf_counter()
        self.stats.commands_failed += 1
        self._consecutive_failures += 1
        self.device.status.online = False
        self.device.status.last_error = str(e)
        # Only the first failure of a streak is worth a warning - the same
        # message repeated at every backoff step just buries everything else.
        log = logger.warning if self._consecutive_failures == 1 and self._reconnects_without_success == 0 else logger.debug
        log("Failed to send to '%s' (%s): %s", self.device.config.name, self.device.config.ip, e)

        if self._consecutive_failures >= _RECONNECT_THRESHOLD and self._reconnects_without_success + 1 >= _DORMANT_AFTER_RECONNECTS:
            self._enter_dormant(e)
            return

        if self._consecutive_failures >= _RECONNECT_THRESHOLD:
            # A bulb's persistent socket can silently die (Wi-Fi blip, the
            # bulb itself dropping the connection) without tinytuya noticing
            # or reconnecting on its own - every further send on that dead
            # socket just keeps failing the same way forever. Previously the
            # only fix was power-cycling the physical bulb (which forces it
            # to accept a fresh connection); do the app-side equivalent
            # instead - tear down and rebuild the tinytuya connection, then
            # give this device a clean slate to try again with.
            logger.info(
                "'%s' (%s) failed %d times in a row - reconnecting (rebuilding the connection)...",
                self.device.config.name, self.device.config.ip, self._consecutive_failures,
            )
            self.device.reconnect()
            self._reconnects_without_success += 1
            self._consecutive_failures = 0
            self._colour_mode_ensured = False
            self._white_mode_ensured = False
            self._last_sent_color = None
            self._last_sent_white = None
            # Also worth another shot at white specifically - a device given
            # up on earlier may simply have been suffering from this same
            # dead-connection problem the whole time, not a genuine per-bulb
            # DP-layout incompatibility.
            self._consecutive_white_failures = 0
            self._white_unsupported = False

    # -- dormant: a lamp that won't come back by just retrying ----------------------

    def _enter_dormant(self, error: Exception) -> None:
        self.dormant = True
        self._next_probe_at = time.perf_counter() + _DORMANT_PROBE_INTERVAL_S
        logger.warning(
            "'%s' (%s) is not responding: %s. Leaving it out of the show and checking it every %ds - "
            "it rejoins automatically as soon as it answers; the other lamps keep playing.",
            self.device.config.name, self.device.config.ip, error, int(_DORMANT_PROBE_INTERVAL_S),
        )

    def _leave_dormant(self) -> None:
        self.dormant = False
        self._consecutive_failures = 0
        self._reconnects_without_success = 0
        self._colour_mode_ensured = False
        self._white_mode_ensured = False
        self._last_sent_color = None
        self._last_sent_white = None
        self._consecutive_white_failures = 0
        self._white_unsupported = False
        logger.warning("'%s' (%s) is responding again - back in the show.", self.device.config.name, self.device.config.ip)
        self._wake.set()

    def _probe_and_recover(self) -> None:
        """One dormant-state check: probe, and if it still fails, try the
        automatic fix that matches the error (new IP / new key)."""
        now = time.perf_counter()
        self._next_probe_at = now + _DORMANT_PROBE_INTERVAL_S
        self.device.reconnect()
        problem = self.device.probe()
        if problem is None:
            self._leave_dormant()
            return
        logger.debug("'%s' still not responding: %s", self.device.config.name, problem)

        if problem.code in UNREACHABLE_CODES and now >= self._next_rediscover_at:
            self._next_rediscover_at = now + _REDISCOVER_INTERVAL_S
            if self._try_new_address():
                return
        if problem.code in KEY_CODES and now >= self._next_cloud_key_at:
            self._next_cloud_key_at = now + _CLOUD_KEY_INTERVAL_S
            self._try_new_key()

    def _reconfigure_and_probe(self) -> bool:
        self.device.reconfigure(self.device.config)
        if self.device.probe() is None:
            self._leave_dormant()
            return True
        return False

    def _try_new_address(self) -> bool:
        cfg = self.device.config
        found = find_device_address(cfg.id)
        if found is None or (found.ip == cfg.ip and found.version == cfg.version):
            return False
        logger.warning(
            "'%s' found on the network at %s (protocol %s) instead of %s - updating its settings.",
            cfg.name, found.ip, found.version, cfg.ip,
        )
        cfg.ip, cfg.version = found.ip, found.version
        return self._reconfigure_and_probe()

    def _try_new_key(self) -> bool:
        cfg = self.device.config
        try:
            key = cloud_keys.fetch_local_keys([cfg.id]).get(cfg.id)
        except RuntimeError as e:
            logger.info("Couldn't check '%s''s key in the Tuya cloud: %s", cfg.name, e)
            return False
        if not key or key == cfg.local_key:
            logger.info(
                "'%s''s key in the Tuya cloud is unchanged - the lamp itself is stuck; switching it off "
                "and on at the wall usually fixes this.", cfg.name,
            )
            return False
        logger.warning("'%s''s local key changed in the Tuya cloud (re-paired?) - updating it.", cfg.name)
        cfg.local_key = key
        return self._reconfigure_and_probe()

    def _send_color(self, color: Color) -> None:
        try:
            if not self._colour_mode_ensured:
                self.device.ensure_colour_mode()
                self._colour_mode_ensured = True
                self._white_mode_ensured = False
            r, g, b = color.to_rgb255()
            self.device.set_color(r, g, b, wait_for_ack=False)
            self._last_sent_color = color
            self._last_sent_white = None
            self._on_send_success()
        except Exception as e:
            self._on_send_failure(e)

    def _send_white(self, target: WhiteTarget) -> None:
        try:
            if not self._white_mode_ensured:
                self.device.ensure_white_mode()
                self._white_mode_ensured = True
                self._colour_mode_ensured = False
            self.device.set_white(target.brightness * 100.0, target.temp * 100.0, wait_for_ack=False)
            self._last_sent_white = target
            self._last_sent_color = None
            self._consecutive_white_failures = 0
            self._white_unsupported = False
            self._on_send_success()
        except Exception as e:
            self._consecutive_white_failures += 1
            if self._consecutive_white_failures >= _WHITE_UNSUPPORTED_THRESHOLD:
                self._white_unsupported = True
                self._white_retry_after = time.perf_counter() + _WHITE_RETRY_COOLDOWN_S
                logger.warning(
                    "'%s' (%s) failed WHITE work_mode %d times in a row - pausing white-mode commands "
                    "for this device for %ds (RGB colour is unaffected); will try again automatically "
                    "after that. Last error: %s",
                    self.device.config.name, self.device.config.ip, self._consecutive_white_failures,
                    int(_WHITE_RETRY_COOLDOWN_S), e,
                )
            self._on_send_failure(e)


class LampManager:
    """Owns all configured lamps and their worker threads."""

    def __init__(self, network_cfg: NetworkConfig, min_change_threshold: float = 0.015):
        self.network_cfg = network_cfg
        self.min_change_threshold = min_change_threshold
        self.devices: Dict[str, LampDevice] = {}
        self.workers: Dict[str, LampWorker] = {}

    # -- configuration ---------------------------------------------------------

    def load_devices(self, device_configs: List[DeviceConfig]) -> None:
        for dc in device_configs:
            self.add_device(dc)

    def add_device(self, dc: DeviceConfig) -> LampDevice:
        if dc.id in self.devices:
            self.remove_device(dc.id)
        dev = LampDevice(dc)
        worker = LampWorker(dev, self.network_cfg, self.min_change_threshold)
        worker.start()
        self.devices[dc.id] = dev
        self.workers[dc.id] = worker
        logger.info("Added lamp '%s' (%s, id=%s)", dc.name, dc.ip, dc.id)
        return dev

    def remove_device(self, device_id: str) -> None:
        worker = self.workers.pop(device_id, None)
        if worker:
            worker.stop()
        self.devices.pop(device_id, None)

    def update_device_config(self, dc: DeviceConfig) -> None:
        dev = self.devices.get(dc.id)
        if dev is None:
            self.add_device(dc)
            return
        dev.reconfigure(dc)

    def dormant_device_ids(self) -> List[str]:
        return [device_id for device_id, w in self.workers.items() if w.dormant]

    def refresh_keys_from_cloud(self) -> Dict[str, str]:
        """Blocking: looks up every lamp's current local_key in the Tuya
        cloud and applies the ones that changed. Returns {name: "updated" |
        "unchanged" | "not found"}. Raises RuntimeError if the cloud can't be
        used at all (no credentials, login failed)."""
        keys = cloud_keys.fetch_local_keys(list(self.devices))
        report: Dict[str, str] = {}
        for device_id, dev in self.devices.items():
            key = keys.get(device_id)
            if key is None:
                report[dev.config.name] = "not found"
            elif key == dev.config.local_key:
                report[dev.config.name] = "unchanged"
            else:
                dev.config.local_key = key
                dev.reconfigure(dev.config)
                worker = self.workers.get(device_id)
                if worker is not None:
                    worker._next_probe_at = 0.0  # a dormant lamp gets re-checked right away
                    worker._wake.set()
                report[dev.config.name] = "updated"
                logger.warning("Updated '%s''s local key from the Tuya cloud.", dev.config.name)
        return report

    def set_network_config(self, network_cfg: NetworkConfig, min_change_threshold: float) -> None:
        self.network_cfg = network_cfg
        self.min_change_threshold = min_change_threshold
        for w in self.workers.values():
            w.update_network_config(network_cfg, min_change_threshold)

    # -- selection ---------------------------------------------------------------

    def selected_device_ids(self) -> List[str]:
        return [d.config.id for d in self.devices.values() if d.config.enabled and d.config.selected]

    def set_selected(self, device_id: str, selected: bool) -> None:
        dev = self.devices.get(device_id)
        if dev:
            dev.config.selected = selected

    def select_all(self) -> None:
        for dev in self.devices.values():
            dev.config.selected = True

    def clear_selection(self) -> None:
        for dev in self.devices.values():
            dev.config.selected = False

    def apply_group(self, group_device_ids: List[str]) -> None:
        group_set = set(group_device_ids)
        for dev in self.devices.values():
            dev.config.selected = dev.config.id in group_set

    # -- runtime -------------------------------------------------------------------

    def push_colors(self, colors: Dict[str, Color]) -> None:
        """Send one target RGB color per device id (work_mode='colour').
        Called at the configured visual_update_hz from the engine - each
        worker independently decides whether/when to actually transmit it."""
        for device_id, color in colors.items():
            worker = self.workers.get(device_id)
            if worker is not None:
                worker.set_target(color)

    def push_white_targets(self, targets: Dict[str, WhiteTarget]) -> None:
        """Same as push_colors(), but for the bulb's WHITE work_mode
        (brightness + color temperature) - e.g. the Beat Sync White mode."""
        for device_id, target in targets.items():
            worker = self.workers.get(device_id)
            if worker is not None:
                worker.set_white_target(target)

    def refresh_all_status(self, executor) -> None:
        """Submits a status() refresh for every device to the given
        concurrent.futures executor so slow/offline lamps don't block others."""
        for dev in self.devices.values():
            executor.submit(self._safe_refresh, dev)

    @staticmethod
    def _safe_refresh(dev: LampDevice) -> None:
        try:
            dev.refresh_status()
        except Exception:
            pass  # already recorded on dev.status by refresh_status()

    def discover(self, timeout: float = 8.0) -> List[DiscoveredDevice]:
        return scan_network(timeout)

    def shutdown(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=1.0)
