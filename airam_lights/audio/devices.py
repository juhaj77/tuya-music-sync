"""Enumeration of loopback ("what you hear") and microphone devices.

Which audio library is used depends on the operating system - see
audio_backend():

- Windows: WASAPI. Plain PortAudio/sounddevice does not expose WASAPI
  loopback endpoints. We use PyAudioWPatch, a maintained PortAudio fork that
  adds them: every WASAPI render (output) device gets a matching loopback
  *input* device that yields whatever is being played on it. See
  DEVICE_NOTES.md for why this library was chosen over the alternatives.
- Linux (and anything else): PulseAudio / PipeWire through the `soundcard`
  package, where every output has a "monitor" source that does the same job
  - see soundcard_backend.py.

Each library is imported only inside the functions that use it, and only on
the system it belongs to - so neither has to be installed (or even exist)
on the other one.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("airam_lights.audio")


def audio_backend() -> str:
    """Which audio library this system uses: "wasapi" (PyAudioWPatch) on
    Windows, "soundcard" (PulseAudio / PipeWire) everywhere else."""
    return "wasapi" if sys.platform == "win32" else "soundcard"


@dataclass
class LoopbackDeviceInfo:
    index: int
    name: str
    samplerate: int
    channels: int
    is_default: bool = False
    device_id: Optional[str] = None  # the sound server's own name for it ("soundcard" backend only)


@dataclass
class InputDeviceInfo:
    index: int
    name: str
    samplerate: int
    channels: int
    is_default: bool = False
    device_id: Optional[str] = None  # see LoopbackDeviceInfo.device_id


def list_loopback_devices() -> List[LoopbackDeviceInfo]:
    """Return all loopback-capable devices currently available (Windows:
    WASAPI loopback devices; Linux: the outputs' monitor sources).

    Safe to call even if no audio backend is available (e.g. missing driver) -
    returns an empty list and logs the reason instead of raising, so the UI
    can show "no loopback device found" instead of crashing on startup.
    """
    if audio_backend() != "wasapi":
        from . import soundcard_backend

        return soundcard_backend.list_loopback_devices()
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        logger.error("PyAudioWPatch is not installed - cannot enumerate loopback devices")
        return []

    devices: List[LoopbackDeviceInfo] = []
    try:
        with pyaudio.PyAudio() as p:
            default_index: Optional[int] = None
            try:
                default_speakers = p.get_default_wasapi_loopback()
                default_index = default_speakers["index"]
            except OSError:
                logger.warning("No default WASAPI loopback device reported by the OS")

            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("hostApi") != wasapi_info["index"]:
                    continue
                if not info.get("isLoopbackDevice"):
                    continue
                devices.append(
                    LoopbackDeviceInfo(
                        index=i,
                        name=info["name"],
                        samplerate=int(info["defaultSampleRate"]),
                        channels=int(info["maxInputChannels"]),
                        is_default=(i == default_index),
                    )
                )
    except Exception:
        logger.exception("Failed to enumerate WASAPI loopback devices")

    return devices


def list_microphone_devices() -> List[InputDeviceInfo]:
    """Return real microphone/line-in recording devices (WASAPI host API,
    explicitly excluding the loopback pseudo-devices list_loopback_devices()
    returns) - lets the "Microphone" audio source list actual recording
    hardware, for testing how the lights react to real room/ambient sound
    instead of only to whatever's currently playing through Windows.

    Same safe-on-missing-driver behavior as list_loopback_devices(): returns
    an empty list and logs the reason instead of raising."""
    if audio_backend() != "wasapi":
        from . import soundcard_backend

        return soundcard_backend.list_microphone_devices()
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        logger.error("PyAudioWPatch is not installed - cannot enumerate microphone devices")
        return []

    devices: List[InputDeviceInfo] = []
    try:
        with pyaudio.PyAudio() as p:
            default_index: Optional[int] = None
            try:
                default_index = p.get_default_input_device_info()["index"]
            except OSError:
                logger.warning("No default input device reported by the OS")

            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("hostApi") != wasapi_info["index"]:
                    continue
                if info.get("isLoopbackDevice"):
                    continue
                if int(info.get("maxInputChannels", 0)) <= 0:
                    continue
                devices.append(
                    InputDeviceInfo(
                        index=i,
                        name=info["name"],
                        samplerate=int(info["defaultSampleRate"]),
                        channels=int(info["maxInputChannels"]),
                        is_default=(i == default_index),
                    )
                )
    except Exception:
        logger.exception("Failed to enumerate microphone devices")

    return devices
