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

import collections
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from ..color.models import Color, StrobeBurst, WhiteTarget
from ..config.schema import DeviceConfig, NetworkConfig
from . import cloud_keys
from .discovery import DiscoveredDevice, find_device_address, scan_network
from .tuya_device import KEY_CODES, UNREACHABLE_CODES, LampDevice, parse_reported_state, reported_matches

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

# "Stuck" lamps: some bulbs get into a state where they still answer status
# queries (so they look online) but silently ignore every control command -
# and because colour commands are sent without waiting for an
# acknowledgement, nothing on our side fails either. The phone app keeps
# working (it goes through the Tuya cloud) and only switching the lamp off
# and on fixes it. We catch it by comparing what the lamp REPORTS it's showing
# (the periodic status refresh) with what we sent it recently: if it hasn't
# changed at all while commands kept going out, and on this many of those
# checks it matched none of the recent commands, it's not following.
_STUCK_CHECKS = 3
_STUCK_COMPARE_WINDOW_S = 3.0  # "recent" = sent within this long before the status reply
_STUCK_MIN_SENDS = 5  # fewer commands than this in the window = too idle to judge
_SENT_LOG_S = 60.0  # how much send history to keep, for the diagnostics report

# With lamp_transitions "direct"/"gradient", colors go to the real-time
# control datapoint (DP 28), which the bulb doesn't report back in status and
# doesn't keep over a power cut. Nothing else is written then - not the
# colour DP 24 nor work_mode - so the bulb's saved state stays what it was
# before the show, and switching it off and on at the wall brings that back.
# The stuck check can't see DP 28 sends, so in that mode it simply never
# fires (no bulb has got stuck on DP 28 so far); it still runs for "legacy".


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
        # A strobe burst waiting to be / being played (see _run_strobe). While
        # it runs, the targets above are only read for the colour to keep
        # showing under the flashes - nothing else is sent.
        self._strobe: Optional[StrobeBurst] = None
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
        self.dormant_reason = ""

        # See _STUCK_CHECKS. _sent_log: (time, "colour"|"white", hsv or None,
        # reported_in_status) for every successful send in the last
        # _SENT_LOG_S seconds - the last field is False for DP 28 sends,
        # which the bulb's status never reflects.
        self._sent_log: Deque[Tuple[float, str, Optional[tuple], bool]] = collections.deque()
        self._sent_log_lock = threading.Lock()
        self._suspicious_checks = 0
        self._last_reported: Optional[tuple] = None
        self._stuck_pending = False
        self._stuck_reconnected = False  # already tried a reconnect for the current stuck episode
        self._dormant_stuck = False  # dormant because it stopped following (vs. not answering at all)
        self._connected_since = time.perf_counter()
        self.context_provider: Optional[Callable[[], str]] = None  # what the show was doing, for the report
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

    def start_strobe(self, burst: StrobeBurst) -> None:
        """Hands this lamp a strobe burst to play at the burst's own times."""
        with self._lock:
            self._strobe = burst
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
            if self._stuck_pending:
                self._handle_stuck()
            if self.dormant:
                self._wake.clear()
                with self._lock:
                    self._strobe = None
                if time.perf_counter() >= self._next_probe_at:
                    self._probe_and_recover()
                continue
            if not got_signal:
                continue
            self._wake.clear()

            with self._lock:
                color, white, strobe = self._target_color, self._target_white, self._strobe

            if strobe is not None:
                self._run_strobe(strobe)
                continue

            if color is None and white is None:
                continue

            interval = self._effective_interval()
            elapsed = time.perf_counter() - self._last_send_time
            if elapsed < interval:
                time.sleep(interval - elapsed)
                # Newer targets may have arrived while we slept - use them.
                with self._lock:
                    color, white, strobe = self._target_color, self._target_white, self._strobe
                if strobe is not None:
                    # A strobe arrived meanwhile: it goes first - sending this
                    # colour now could push the strobe's first flash late.
                    self._run_strobe(strobe)
                    continue

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

    # -- strobe ------------------------------------------------------------------------

    def _run_strobe(self, burst: StrobeBurst) -> None:
        """Plays a strobe burst on this lamp: the white LEDs on and off at
        the burst's own absolute times, the colour the show is on right now
        kept underneath. Every lamp gets the same times and sends each
        on/off the moment it's due (not at its next command-rate slot), so
        the lamps flash together - which is the whole point of a strobe.
        The command rate still holds: the engine only makes bursts whose on
        and off parts are at least one command interval long (see
        pulse_sequencer.strobe_plan), and nothing else is sent meanwhile.

        A flash this lamp is too late for is skipped rather than played
        late, so it falls back in step with the others. Only for bulbs on
        the real-time control datapoint in its instant mode (see
        NetworkConfig.lamp_transitions "direct") - others ignore the burst."""
        try:
            if not self._strobe_allowed():
                return
            for index in range(burst.flashes):
                on_at, off_at = burst.on_time(index), burst.off_time(index)
                if time.perf_counter() > on_at + 0.5 * burst.on_s:
                    continue
                if not self._sleep_until(on_at):
                    return
                if not self._send_strobe_edge(burst, True, index):
                    return
                self._sleep_until(off_at)  # even when stopping: never leave the white on
                if not self._send_strobe_edge(burst, False, index) or self._stop.is_set():
                    return
        finally:
            with self._lock:
                if self._strobe is burst:
                    self._strobe = None
            self._wake.set()  # back to the normal show, with whatever the target is now

    def _strobe_allowed(self) -> bool:
        return (
            self.network_cfg.lamp_transitions == "direct"
            and not self.dormant
            and self._consecutive_failures == 0
            and self._uses_control_dp()
        )

    def _sleep_until(self, t: float) -> bool:
        """False if the worker was stopped before `t` (perf_counter time)."""
        while not self._stop.is_set():
            remaining = t - time.perf_counter()
            if remaining <= 0.0:
                return True
            time.sleep(min(remaining, 0.02))
        return False

    def _send_strobe_edge(self, burst: StrobeBurst, on: bool, index: int) -> bool:
        with self._lock:
            color, white = self._target_color, self._target_white
        if color is None and white is not None:
            color = white.under
        if color is None:
            color = self._last_sent_color or Color.black()
        try:
            rgb = color.to_rgb255()
            if on:
                self.device.set_white(
                    burst.brightness_at(index) * 100.0, burst.temp * 100.0, wait_for_ack=False,
                    transition="direct", under_rgb=rgb,
                )
                self._log_send("white", None, reported=False)
            else:
                self.device.set_color(*rgb, wait_for_ack=False, transition="direct")
                self._log_send("colour", color.to_hsv(), reported=False)
            self._last_sent_color = color
            self._last_sent_white = None
            self._on_send_success()
            return True
        except Exception as e:
            # Whatever the lamp shows now is unknown - make sure the next
            # normal colour is really sent (it also switches the white off).
            self._last_sent_color = None
            self._last_sent_white = None
            self._on_send_failure(e)
            return False

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

    def _enter_dormant(self, error: Exception, stuck: bool = False) -> None:
        self.dormant = True
        self._dormant_stuck = stuck
        self.dormant_reason = str(error)
        self._next_probe_at = time.perf_counter() + _DORMANT_PROBE_INTERVAL_S
        logger.warning(
            "'%s' (%s) is not responding: %s. Leaving it out of the show and checking it every %ds - "
            "it rejoins automatically as soon as it answers; the other lamps keep playing.",
            self.device.config.name, self.device.config.ip, error, int(_DORMANT_PROBE_INTERVAL_S),
        )

    def _leave_dormant(self) -> None:
        self.dormant = False
        self._dormant_stuck = False
        self.dormant_reason = ""
        self._stuck_reconnected = False
        self._suspicious_checks = 0
        self._last_reported = None
        self._connected_since = time.perf_counter()
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
            # Answering isn't enough for a lamp that stopped following
            # commands - it answered all along. Check it actually obeys.
            if self._dormant_stuck and not self._verify_following():
                logger.debug("'%s' answers but still doesn't follow commands", self.device.config.name)
                return
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

    # -- stuck: answers, but doesn't follow commands (see _STUCK_CHECKS) --------------------

    def _log_send(self, kind: str, hsv: Optional[tuple], reported: bool = True) -> None:
        now = time.perf_counter()
        with self._sent_log_lock:
            self._sent_log.append((now, kind, hsv, reported))
            while self._sent_log and self._sent_log[0][0] < now - _SENT_LOG_S:
                self._sent_log.popleft()

    def check_following(self, raw_dps: dict, now: Optional[float] = None) -> None:
        """Called after each status refresh with the lamp's reported state.
        Flags the lamp as stuck (handled on this worker's own thread) after
        _STUCK_CHECKS suspicious checks in a row."""
        if self.dormant:
            return
        reported = parse_reported_state(raw_dps)
        if reported is None:
            return
        now = time.perf_counter() if now is None else now
        with self._sent_log_lock:
            recent = [e for e in self._sent_log if e[3] and now - _STUCK_COMPARE_WINDOW_S <= e[0] <= now]
        previous, self._last_reported = self._last_reported, reported
        if previous is None:
            return  # first look (or first after a reconnect): nothing to compare with yet
        if reported != previous:
            # What it shows changed: it's alive and following. A later episode starts over.
            self._suspicious_checks = 0
            self._stuck_reconnected = False
            return
        # Unchanged since the last check. That only counts against it if
        # plenty of commands went out meanwhile and none of them is what it
        # shows - a match can be a coincidence (a frozen lamp showing one of
        # the colors the show keeps coming back to), so it doesn't clear the
        # count either; only a change does.
        if len(recent) < _STUCK_MIN_SENDS:
            return  # too idle to tell
        if any(reported_matches(reported, kind, hsv) for _, kind, hsv, _ in recent):
            return
        self._suspicious_checks += 1
        if self._suspicious_checks >= _STUCK_CHECKS:
            self._suspicious_checks = 0
            self._stuck_pending = True
            self._wake.set()

    def diagnostics_report(self, now: Optional[float] = None) -> str:
        """What this lamp was put through recently - to see what stuck lamps
        have in common (command load, white switching, time connected)."""
        now = time.perf_counter() if now is None else now
        with self._sent_log_lock:
            entries = list(self._sent_log)
        span = max(1.0, min(_SENT_LOG_S, now - entries[0][0])) if entries else _SENT_LOG_S
        colour = sum(1 for _, kind, _, _ in entries if kind == "colour")
        white = len(entries) - colour
        switches = sum(1 for a, b in zip(entries, entries[1:]) if a[1] != b[1])
        minutes = (now - self._connected_since) / 60.0
        context = ""
        if self.context_provider is not None:
            try:
                context = "; " + self.context_provider()
            except Exception:
                pass
        return (
            f"{len(entries) / span:.1f} commands/s over the last {span:.0f}s ({colour} colour, {white} white, "
            f"{switches} colour<->white switches), connected for {minutes:.1f} min{context}"
        )

    def _handle_stuck(self) -> None:
        self._stuck_pending = False
        name, ip = self.device.config.name, self.device.config.ip
        report = self.diagnostics_report()
        if not self._stuck_reconnected:
            logger.warning(
                "'%s' (%s) is not following commands - it answers status queries but keeps showing the "
                "same color whatever it's sent. Rebuilding its connection. [%s]", name, ip, report,
            )
            self._stuck_reconnected = True
            self.device.reconnect()
            self._connected_since = time.perf_counter()
            self._last_reported = None
            self._colour_mode_ensured = False
            self._white_mode_ensured = False
            self._last_sent_color = None  # resend even an unchanged color
            self._last_sent_white = None
            self._wake.set()
            return
        self._enter_dormant(
            RuntimeError(
                "it answers but no longer follows commands, even after a reconnect - its local connection is "
                "stuck; switch the lamp off and on at the wall switch"
            ),
            stuck=True,
        )
        logger.warning("'%s' stuck-lamp report: [%s]", name, report)

    def _verify_following(self) -> bool:
        """Send one color and read back what the lamp reports - does it obey?
        The test color must differ from what it's showing already, or a lamp
        frozen on that very color would look obedient."""
        with self._lock:
            target = self._target_color
        try:
            before = parse_reported_state(self.device.refresh_status().raw_dps)
        except Exception:
            return False
        candidates = [c for c in (target, Color(1.0, 0.0, 0.0), Color(0.0, 0.3, 1.0)) if c is not None]
        color = next(
            (c for c in candidates if before is None or not reported_matches(before, "colour", c.to_hsv())),
            candidates[-1],
        )
        try:
            self.device.ensure_colour_mode()
            r, g, b = color.to_rgb255()
            self.device.set_color(r, g, b, wait_for_ack=True)  # DP 24: the one status reports back
            time.sleep(0.4)
            status = self.device.refresh_status()
        except Exception:
            return False
        reported = parse_reported_state(status.raw_dps)
        return reported is not None and reported_matches(reported, "colour", color.to_hsv())

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

    def _uses_control_dp(self) -> bool:
        check = getattr(self.device, "uses_control_dp", None)
        return bool(check and check(self.network_cfg.lamp_transitions))

    def _send_color(self, color: Color) -> None:
        try:
            r, g, b = color.to_rgb255()
            if self._uses_control_dp():
                # DP 28 only - see the DP 28 note next to _STUCK_CHECKS.
                self.device.set_color(r, g, b, wait_for_ack=False, transition=self.network_cfg.lamp_transitions)
                self._log_send("colour", color.to_hsv(), reported=False)
            else:
                if not self._colour_mode_ensured:
                    self.device.ensure_colour_mode()
                    self._colour_mode_ensured = True
                    self._white_mode_ensured = False
                self.device.set_color(r, g, b, wait_for_ack=False)
                self._log_send("colour", color.to_hsv())
            self._last_sent_color = color
            self._last_sent_white = None
            self._on_send_success()
        except Exception as e:
            self._on_send_failure(e)

    def _send_white(self, target: WhiteTarget) -> None:
        try:
            if self._uses_control_dp():
                # DP 28 lights the white LEDs directly - no work_mode switch
                # there and back, so one command per flash instead of 4-5.
                self.device.set_white(
                    target.brightness * 100.0, target.temp * 100.0, wait_for_ack=False,
                    transition=self.network_cfg.lamp_transitions,
                    under_rgb=target.under.to_rgb255() if target.under is not None else None,
                )
                self._log_send("white", None, reported=False)
            else:
                if not self._white_mode_ensured:
                    self.device.ensure_white_mode()
                    self._white_mode_ensured = True
                    self._colour_mode_ensured = False
                self.device.set_white(target.brightness * 100.0, target.temp * 100.0, wait_for_ack=False)
                self._log_send("white", None)
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

    # Optional: returns a one-line summary of what the show is doing (mode,
    # effects, command rate) - included in stuck-lamp diagnostics reports.
    context_provider: Optional[Callable[[], str]] = None

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
        worker.context_provider = self._context
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

    def _context(self) -> str:
        provider = self.context_provider
        return provider() if provider is not None else ""

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

    def start_strobe(self, bursts: Dict[str, StrobeBurst]) -> None:
        """Starts a strobe burst (see LampWorker._run_strobe) on each given
        device id. All bursts of one strobe share the same times, so the
        lamps flash together; only the brightness may differ per lamp."""
        for device_id, burst in bursts.items():
            worker = self.workers.get(device_id)
            if worker is not None:
                worker.start_strobe(burst)

    def refresh_all_status(self, executor) -> None:
        """Submits a status() refresh for every device to the given
        concurrent.futures executor so slow/offline lamps don't block others."""
        for device_id, dev in self.devices.items():
            executor.submit(self._safe_refresh, dev, self.workers.get(device_id))

    @staticmethod
    def _safe_refresh(dev: LampDevice, worker: Optional["LampWorker"] = None) -> None:
        try:
            dev.refresh_status()
        except Exception:
            pass  # already recorded on dev.status by refresh_status()
        if worker is None:
            return
        if dev.status.online:
            worker.check_following(dev.status.raw_dps)
        if worker.dormant:
            # Show why it's out of the show, even if it answered this status query.
            dev.status.online = False
            dev.status.last_error = worker.dormant_reason

    def discover(self, timeout: float = 8.0) -> List[DiscoveredDevice]:
        return scan_network(timeout)

    def shutdown(self) -> None:
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.join(timeout=1.0)
