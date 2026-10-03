"""Audio devices on Linux (and any other non-Windows system) through the
`soundcard` package, which talks to PulseAudio - or to PipeWire's PulseAudio
server (pipewire-pulse), which is what current desktop distributions run.

"What you hear" works there without any extra setup: every output (sink)
has a *monitor* source carrying whatever is being played on it, and
`soundcard` lists those as loopback microphones. That makes them the direct
counterpart of the WASAPI loopback devices devices.py uses on Windows.

`soundcard` connects to the sound server the moment it's imported, and
raises if there is none - so it's only ever imported through load(), never
at module level, and a missing package or server becomes a readable message
instead of a crash at startup.

A device's `index` here is its position among all of the server's sources
(monitors included), so loopback and microphone indexes never collide - like
the PortAudio indexes on Windows, it can shift when devices come and go.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from .devices import InputDeviceInfo, LoopbackDeviceInfo

logger = logging.getLogger("airam_lights.audio")

# The sound server resamples to whatever rate a recording asks for, so there
# is no per-device "native" rate to read - this is simply what we ask for.
SAMPLERATE = 48000


class SoundcardUnavailable(RuntimeError):
    """The `soundcard` package or the sound server can't be used - the message says why."""


def load():
    """Returns the imported `soundcard` module, or raises SoundcardUnavailable."""
    try:
        import soundcard
    except ImportError as e:
        raise SoundcardUnavailable(
            "The 'soundcard' package is not installed. Run: pip install soundcard"
        ) from e
    except Exception as e:  # no sound server, or libpulse itself is missing
        raise SoundcardUnavailable(
            "Could not connect to the sound server - PulseAudio, or PipeWire with pipewire-pulse, "
            f"has to be running ({type(e).__name__}: {e})"
        ) from e
    return soundcard


def _sources(soundcard) -> list:
    return list(soundcard.all_microphones(include_loopback=True))


def list_loopback_devices() -> List[LoopbackDeviceInfo]:
    """The monitor sources: one per output. The default one is the monitor
    of the default output - what the speakers/headphones are playing."""
    try:
        soundcard = load()
    except SoundcardUnavailable as e:
        logger.error("%s - cannot enumerate loopback devices", e)
        return []
    devices: List[LoopbackDeviceInfo] = []
    try:
        default_id: Optional[str] = None
        try:
            default_id = f"{soundcard.default_speaker().id}.monitor"
        except Exception:
            logger.warning("No default output device reported by the sound server")
        for index, source in enumerate(_sources(soundcard)):
            if not source.isloopback:
                continue
            devices.append(
                LoopbackDeviceInfo(
                    index=index,
                    name=source.name,
                    samplerate=SAMPLERATE,
                    channels=max(1, int(source.channels)),
                    is_default=(source.id == default_id),
                    device_id=source.id,
                )
            )
    except Exception:
        logger.exception("Failed to enumerate loopback (monitor) devices")
    return devices


def list_microphone_devices() -> List[InputDeviceInfo]:
    """Real recording devices - the monitor sources are left to list_loopback_devices()."""
    try:
        soundcard = load()
    except SoundcardUnavailable as e:
        logger.error("%s - cannot enumerate microphone devices", e)
        return []
    devices: List[InputDeviceInfo] = []
    try:
        default_id: Optional[str] = None
        try:
            default_id = soundcard.default_microphone().id
        except Exception:
            logger.warning("No default input device reported by the sound server")
        for index, source in enumerate(_sources(soundcard)):
            if source.isloopback:
                continue
            devices.append(
                InputDeviceInfo(
                    index=index,
                    name=source.name,
                    samplerate=SAMPLERATE,
                    channels=max(1, int(source.channels)),
                    is_default=(source.id == default_id),
                    device_id=source.id,
                )
            )
    except Exception:
        logger.exception("Failed to enumerate microphone devices")
    return devices


def open_source(device_id: str):
    """The `soundcard` microphone object (monitor sources included) with this id."""
    return load().get_microphone(device_id, include_loopback=True)
