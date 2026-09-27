"""Local (LAN) control of a single Airam/Tuya bulb via tinytuya.

IMPORTANT (see DEVICE_NOTES.md): this module never hardcodes datapoint IDs.
All colour/brightness/power control goes through `tinytuya.BulbDevice`, which
reads the device's own `status()` response and adapts to whichever DP layout
(type A/B/C) the physical bulb actually reports. `refresh_status()` also
stores the *raw* dps dictionary so the diagnostics UI can show you exactly
what the bulb sent back, instead of trusting an assumption blindly.

All methods here are blocking (tinytuya is a synchronous socket library).
Callers must run them off the UI thread - see `lamps/manager.py`, which runs
one worker thread per lamp for exactly this reason.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from ..config.schema import DeviceConfig

logger = logging.getLogger("airam_lights.lamps")


# What tinytuya's numeric error codes mean in practice for a bulb that was
# working before, and what to do about it.
_ERROR_HINTS = {
    "914": "the lamp rejected the connection handshake (Err 914: key or protocol version). If the "
    "key is still valid, the lamp's local connection is stuck - switch the lamp off and on at the "
    "wall switch. If it was re-paired in the phone app, its key changed: use 'Refresh keys from "
    "Tuya cloud' in the Devices tab",
    "904": "unexpected reply from the lamp (Err 904) - usually a wrong protocol version in the "
    "Devices tab",
    "901": "cannot connect to the lamp at this IP (Err 901) - it's off, or its IP address changed",
    "902": "the lamp didn't answer in time (Err 902) - weak Wi-Fi or the lamp is busy",
    "905": "the lamp is unreachable (Err 905) - it's off, out of Wi-Fi range, or its IP changed",
}

# Codes meaning "nothing answers at this address" - worth looking for the
# lamp at a new IP (DHCP may have handed it a different one).
UNREACHABLE_CODES = {"901", "905"}
KEY_CODES = {"914"}


def describe_tuya_error(code: Optional[str], message: str) -> str:
    return _ERROR_HINTS.get(str(code), message) if code else message


class LampConnectionError(RuntimeError):
    """A send failed because the lamp can't be talked to at all (as opposed
    to a single command being rejected) - carries tinytuya's error code."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


@dataclass
class LampStatus:
    online: bool = False
    raw_dps: dict = field(default_factory=dict)
    bulb_type: Optional[str] = None
    last_latency_ms: Optional[float] = None
    last_error: Optional[str] = None
    last_updated: Optional[float] = None
    error_code: Optional[str] = None  # tinytuya "Err" code of the last failed status(), e.g. "914"


class LampDevice:
    """Thin, defensive wrapper around tinytuya.BulbDevice for one physical bulb."""

    def __init__(self, config: DeviceConfig):
        self.config = config
        self.status = LampStatus()
        self._bulb = None
        self._connect_error: Optional[str] = None
        # tinytuya.BulbDevice keeps mutable, non-thread-safe internal state
        # (bulb_configured, dpset, its persistent socket) - this device gets
        # called both from its own LampWorker thread (color/white commands)
        # and from a separate status-refresh ThreadPoolExecutor (Diagnostics
        # tab / periodic polling, see LampManager.refresh_all_status()).
        # Without serializing access, two threads touching the same
        # BulbDevice at once can interleave its internal bulb-type/DP-range
        # detection and produce exactly the kind of intermittent, seemingly
        # random "Bulb not configured" / internal AttributeError-style
        # failures seen in practice - confirmed by these only ever
        # surfacing on the more state-heavy WHITE work_mode call path.
        self._bulb_lock = threading.Lock()
        # See set_white() - only ever force one blocking status() call for
        # detection, even if it doesn't resolve bulb_configured. Retrying it
        # on every single failed white-mode attempt (this device gets called
        # roughly every backoff interval, forever, for a bulb that never
        # detects) was hammering the persistent socket hard enough in
        # practice to apparently degrade the connection for that device
        # entirely - RGB colour stopped working too, not just white mode.
        self._white_detect_attempted = False
        self._build()

    def _build(self) -> None:
        try:
            import tinytuya
        except ImportError as e:
            self._connect_error = "tinytuya is not installed (pip install tinytuya)"
            logger.error(self._connect_error)
            return
        try:
            # tinytuya expects `version` as a float (e.g. 3.3, not "3.3"). Passing a
            # string here silently breaks its internal protocol-version comparisons,
            # which shows up as status()/set_colour() failing even with a correct
            # local_key - confirmed while testing against real Airam bulbs.
            #
            # connection_retry_limit/connection_retry_delay deliberately override
            # tinytuya's own defaults (5 retries, 5s delay between each) down to a
            # single, fast-failing attempt. With the defaults, ONE call against an
            # unreachable/unresponsive bulb can block for ~30+ seconds (5 attempts
            # x up to connection_timeout, plus 4x the retry delay in between) - and
            # since that call runs synchronously on this device's own LampWorker
            # thread, the whole thread (every command for THIS lamp, including
            # reverting a true-white pulse back to RGB) is stuck for that entire
            # window. LampWorker already has its own non-blocking, exponential
            # backoff between attempts (see manager.py), so tinytuya retrying
            # internally on top of that only adds a long, blocking pile-up for no
            # benefit - let each individual attempt fail fast instead, and leave
            # the actual retry pacing to our own layer.
            self._bulb = tinytuya.BulbDevice(
                dev_id=self.config.id,
                address=self.config.ip,
                local_key=self.config.local_key,
                version=float(self.config.version),
                connection_retry_limit=1,
                connection_retry_delay=0,
            )
            self._bulb.set_socketPersistent(True)
            self._bulb.set_socketTimeout(2.0)
        except Exception as e:
            self._connect_error = f"Failed to initialize device object: {e}"
            logger.exception("Error creating tinytuya.BulbDevice for %s", self.config.name)

    def reconfigure(self, config: DeviceConfig) -> None:
        """Called when IP/local_key/version change (user edit, or automatic
        recovery from a worker/cloud-key thread - hence the lock)."""
        with self._bulb_lock:
            self.config = config
            self._white_detect_attempted = False
            self._build()

    def reconnect(self) -> None:
        """Tears down and rebuilds the tinytuya connection (a fresh socket,
        fresh detection state) without touching self.config. A bulb's
        persistent socket (socketPersistent=True) can silently die - a
        Wi-Fi blip, the bulb itself dropping the connection - and tinytuya
        doesn't notice or reconnect on its own; every further send on that
        dead socket just keeps failing until something forces a new one.
        Called by LampWorker after enough CONSECUTIVE failures (see
        LampWorker._consecutive_failures) - the same effective fix as power-
        cycling the bulb, but from the app side, so a session doesn't need a
        physical power-cycle to recover a stuck-offline lamp."""
        with self._bulb_lock:
            self._white_detect_attempted = False
            self._build()

    # -- read ---------------------------------------------------------------

    def refresh_status(self) -> LampStatus:
        """Blocking. Queries the bulb's current state and updates self.status.

        Raises the underlying exception after recording it in self.status, so
        callers running this in a background thread/executor can log it, and
        the diagnostics page can also read the human-readable last_error.
        """
        if self._bulb is None:
            self.status = LampStatus(online=False, last_error=self._connect_error or "device not initialized")
            return self.status

        t0 = time.perf_counter()
        try:
            with self._bulb_lock:
                result = self._bulb.status()
            latency_ms = (time.perf_counter() - t0) * 1000.0

            if isinstance(result, dict) and "dps" in result:
                self.status = LampStatus(
                    online=True,
                    raw_dps=dict(result["dps"]),
                    bulb_type=getattr(self._bulb, "bulb_type", None),
                    last_latency_ms=latency_ms,
                    last_error=None,
                    last_updated=time.time(),
                )
            elif isinstance(result, dict) and result.get("Error"):
                code = str(result.get("Err")) if result.get("Err") else None
                self.status = LampStatus(
                    online=False,
                    last_error=describe_tuya_error(code, str(result.get("Error"))),
                    last_latency_ms=latency_ms,
                    last_updated=time.time(),
                    error_code=code,
                )
            else:
                self.status = LampStatus(
                    online=False,
                    last_error=f"Unexpected response shape: {result!r}",
                    last_latency_ms=latency_ms,
                    last_updated=time.time(),
                )
            return self.status
        except Exception as e:
            self.status = LampStatus(online=False, last_error=str(e), last_updated=time.time())
            logger.warning("status() failed for '%s' (%s): %s", self.config.name, self.config.ip, e)
            raise

    def probe(self) -> Optional[LampConnectionError]:
        """Blocking connectivity check (one status() call). None if the lamp
        answered, otherwise the reason. Never raises."""
        try:
            status = self.refresh_status()
        except Exception as e:
            return LampConnectionError(str(e))
        if status.online:
            return None
        return LampConnectionError(status.last_error or "no answer", status.error_code)

    def _ensure_detected(self) -> None:
        """tinytuya only learns the bulb's DP layout from a successful
        status() reply - until then every command fails with a bare "Bulb not
        configured", hiding the real problem (bad key, wrong IP, stuck lamp).
        Ask once up front and raise the actual reason. Caller holds _bulb_lock."""
        if getattr(self._bulb, "bulb_configured", True):
            return
        result = self._bulb.status()
        if isinstance(result, dict) and result.get("Error"):
            code = str(result.get("Err")) if result.get("Err") else None
            self.status.error_code = code
            raise LampConnectionError(describe_tuya_error(code, str(result.get("Error"))), code)

    # -- write ----------------------------------------------------------------

    def ensure_colour_mode(self) -> None:
        """Some Tuya bulbs ignore colour DPs unless work_mode is set to
        'colour' first. Safe/cheap to call once after connecting."""
        if self._bulb is None:
            return
        try:
            with self._bulb_lock:
                self._bulb.set_mode("colour")
        except Exception:
            logger.debug("set_mode('colour') not supported/failed for %s (may be fine)", self.config.name)

    def set_color(self, r: int, g: int, b: int, wait_for_ack: bool = False) -> float:
        """r,g,b in 0..255. Returns latency in ms. Raises on failure."""
        if self._bulb is None:
            raise RuntimeError(self._connect_error or "device not initialized")
        t0 = time.perf_counter()
        with self._bulb_lock:
            self._ensure_detected()
            self._bulb.set_colour(r, g, b, nowait=not wait_for_ack)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        self.status.last_latency_ms = latency_ms
        self.status.online = True
        self.status.last_error = None
        self.status.error_code = None
        return latency_ms

    def set_brightness_percent(self, percent: float, wait_for_ack: bool = False) -> float:
        if self._bulb is None:
            raise RuntimeError(self._connect_error or "device not initialized")
        t0 = time.perf_counter()
        with self._bulb_lock:
            self._bulb.set_brightness_percentage(max(0, min(100, round(percent))), nowait=not wait_for_ack)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        self.status.last_latency_ms = latency_ms
        return latency_ms

    def ensure_white_mode(self) -> None:
        """Counterpart to ensure_colour_mode() - switches work_mode (DP 21)
        to 'white' so DP 22 (bright_value_v2) and DP 23 (temp_value_v2) take
        effect instead of the RGB colour DP. Safe/cheap to call once."""
        if self._bulb is None:
            return
        try:
            with self._bulb_lock:
                self._bulb.set_mode("white")
        except Exception:
            logger.debug("set_mode('white') not supported/failed for %s (may be fine)", self.config.name)

    def set_white(self, brightness_percent: float, temp_percent: float, wait_for_ack: bool = False) -> float:
        """brightness_percent, temp_percent in 0..100 (temp: 0=warmest,
        100=coolest). Uses tinytuya's own percentage-based colourtemp/
        brightness setters (documented BulbDevice methods) rather than
        writing raw DP values, consistent with how set_color() above goes
        through tinytuya's set_colour() instead of a hand-rolled DP 24
        payload - see DEVICE_NOTES.md for what's confirmed vs. assumed about
        this specific call combination on the Airam bulbs.

        NOT YET independently confirmed against the physical bulbs (unlike
        set_color(), which was verified via tools/phase1_test.py) - run a
        manual white-balance test before relying on this for anything
        important, and update DEVICE_NOTES.md with the result."""
        if self._bulb is None:
            raise RuntimeError(self._connect_error or "device not initialized")
        t0 = time.perf_counter()
        # tinytuya's set_colourtemp_percentage()/set_brightness_percentage()
        # both need self._bulb.bulb_configured (its own DP-layout detection,
        # populated from a status() response) before they can compute a raw
        # DP value from a percentage. They try to self-detect via
        # detect_bulb(nowait=...), but with nowait=True (our default here,
        # via wait_for_ack=False) that detection silently no-ops if there is
        # no cached status yet - e.g. the very first white-mode command ever
        # sent to this device - and then raises "Bulb not configured, cannot
        # determine value ranges." Force one blocking status() call up front
        # in exactly that situation, so detection actually happens instead
        # of being skipped - but only ONCE ever for this device (see
        # self._white_detect_attempted), not on every call: a bulb whose
        # response never lets tinytuya classify it would otherwise get this
        # forced status() call again on every single retry forever, which in
        # practice was enough to degrade its persistent connection entirely.
        # Held for the whole detect+set sequence below, not just each
        # individual call - otherwise a status-refresh on another thread
        # could slip in between the detect and the two DP writes and hand
        # this bulb_configured/dpset state to a different call halfway
        # through, which is exactly the kind of interleaving that produced
        # the intermittent "Bulb not configured" / internal errors this
        # lock exists to prevent (see the comment on self._bulb_lock).
        with self._bulb_lock:
            if not self._white_detect_attempted and not getattr(self._bulb, "bulb_configured", False):
                self._white_detect_attempted = True
                self._bulb.status()

            # Each DP is attempted independently (not short-circuited by the
            # other failing) - some bulb variants only implement one of the
            # two (e.g. a colour-temp-only or brightness-only DP layout),
            # and a tinytuya-internal error on one (seen in practice: a bare
            # "NoneType has no len()" from inside its own DP-range lookup,
            # on top of the "Bulb not configured" case handled above)
            # shouldn't also block a DP that actually works on that bulb.
            errors = []
            try:
                self._bulb.set_colourtemp_percentage(max(0, min(100, round(temp_percent))), nowait=not wait_for_ack)
            except Exception as e:
                errors.append(f"colourtemp: {e}")
            try:
                self._bulb.set_brightness_percentage(
                    max(0, min(100, round(brightness_percent))), nowait=not wait_for_ack
                )
            except Exception as e:
                errors.append(f"brightness: {e}")

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self.status.last_latency_ms = latency_ms
        if errors:
            self.status.last_error = "; ".join(errors)
            raise RuntimeError("; ".join(errors))
        self.status.online = True
        self.status.last_error = None
        return latency_ms

    def turn_on(self, wait_for_ack: bool = False) -> None:
        if self._bulb is None:
            raise RuntimeError(self._connect_error or "device not initialized")
        with self._bulb_lock:
            self._bulb.turn_on(nowait=not wait_for_ack)

    def turn_off(self, wait_for_ack: bool = False) -> None:
        if self._bulb is None:
            raise RuntimeError(self._connect_error or "device not initialized")
        with self._bulb_lock:
            self._bulb.turn_off(nowait=not wait_for_ack)
