"""Prevents the PC from going to sleep while one of these apps is running:
on Windows with the standard Win32 `SetThreadExecutionState` API, on Linux
with a `systemd-inhibit` sleep lock (see _linux_prevent_sleep below).

Why this exists: if the whole system suspends (sleep/hibernate), EVERY
process is paused at the hardware level - CPU, network, timers, all of it.
There is no way for a Python/Qt app to "keep working through" an actual
system sleep; the only real option is to ask Windows not to go there in the
first place while the app needs to keep controlling the lamps.

This only blocks *automatic, idle-timeout* sleep - it does not override an
explicit user action (Start menu -> Sleep, closing a laptop lid per its own
power-plan setting), which is the correct, expected behavior for this API
(the same one video players and similar "keep the PC awake" apps use).

The display is deliberately still allowed to turn off / the session to lock
(ES_DISPLAY_REQUIRED is NOT set) - only full system suspend is prevented,
since a sleeping display doesn't stop the app from controlling lamps.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

logger = logging.getLogger("airam_lights.keep_awake")

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

# Linux: the `systemd-inhibit` process holding the sleep lock (None = no lock held).
_linux_inhibitor: Optional[subprocess.Popen] = None


def _linux_prevent_sleep() -> bool:
    """Takes a logind "sleep" inhibitor lock by keeping a `systemd-inhibit`
    process running - the same mechanism media players and installers use,
    with no extra Python package. Like the Windows hold it only stops the
    system from suspending; the display can still turn off.

    The command it wraps (`tail --pid`) ends when this app does, however it
    ends - so a crash can't leave the lock behind. Returns False (and holds
    nothing) where there is no systemd-logind, e.g. in a container."""
    global _linux_inhibitor
    if _linux_inhibitor is not None and _linux_inhibitor.poll() is None:
        return True
    if shutil.which("systemd-inhibit") is None or shutil.which("tail") is None:
        logger.info("systemd-inhibit not found - the PC may still auto-sleep while the app is running")
        return False
    try:
        process = subprocess.Popen(
            [
                "systemd-inhibit", "--what=sleep", "--who=Airam Music Lights",
                "--why=Controlling the lamps", "--mode=block",
                "tail", f"--pid={os.getpid()}", "-f", "/dev/null",
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)  # it exits right away when logind refuses or isn't there
        if process.poll() is not None:
            logger.warning("systemd-inhibit could not take a sleep lock - the PC may still auto-sleep")
            return False
        _linux_inhibitor = process
        logger.info("System auto-sleep prevented while this app is running (display can still turn off)")
        return True
    except Exception:
        logger.exception("Could not run systemd-inhibit - the PC may still auto-sleep")
        return False


def _linux_allow_sleep() -> None:
    global _linux_inhibitor
    process, _linux_inhibitor = _linux_inhibitor, None
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except Exception:
        logger.exception("Could not release the sleep-prevention hold")


def prevent_sleep() -> bool:
    """Call once at startup. Returns True if the hold was applied (False if
    the Win32 call fails, on Linux without systemd-logind, and always on
    other platforms)."""
    if sys.platform.startswith("linux"):
        return _linux_prevent_sleep()
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        result = ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        if result == 0:
            logger.warning("SetThreadExecutionState returned failure - the PC may still auto-sleep")
            return False
        logger.info("System auto-sleep prevented while this app is running (display can still turn off)")
        return True
    except Exception:
        logger.exception("Could not call SetThreadExecutionState - the PC may still auto-sleep")
        return False


def allow_sleep() -> None:
    """Call once on shutdown to release the hold, restoring normal
    power-management behavior."""
    if sys.platform.startswith("linux"):
        _linux_allow_sleep()
        return
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        logger.exception("Could not release the sleep-prevention hold")
