"""Loopback ("what you hear") and microphone audio capture.

Runs entirely on the audio library's own thread (never on the Qt UI thread):
on Windows a PyAudioWPatch (WASAPI) callback thread, on Linux a reader
thread on a PulseAudio / PipeWire recording through the `soundcard` package
- see devices.audio_backend() for which one a system gets. Captured samples
are down-mixed to mono and written into a small ring buffer that the DSP
engine reads from at its own pace - this decouples the audio capture rate
from the FFT/visual update rate, as required.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from .devices import (
    InputDeviceInfo,
    LoopbackDeviceInfo,
    audio_backend,
    list_loopback_devices,
    list_microphone_devices,
)

logger = logging.getLogger("airam_lights.audio")


class AudioCaptureError(RuntimeError):
    """Raised when loopback capture cannot be started, with a user-facing message."""


class AudioCapture:
    """Captures audio - either loopback ("what you hear": WASAPI loopback on
    Windows, an output's monitor source on Linux) or a real
    microphone/recording device - into a ring buffer.

    Usage:
        cap = AudioCapture(device_index=None)
        cap.start()
        ...
        samples = cap.read_latest(2048)   # most recent N mono samples, float32 [-1, 1]
        rms, peak = cap.get_level()
        ...
        cap.stop()
    """

    def __init__(
        self,
        device_index: Optional[int] = None,
        source: str = "loopback",
        mic_device_index: Optional[int] = None,
        mic_gain: float = 1.0,
        history_seconds: float = 2.0,
        block_size: int = 1024,
    ):
        self.device_index = device_index
        self.source = source  # "loopback" | "microphone"
        self.mic_device_index = mic_device_index
        self.mic_gain = mic_gain
        self.block_size = block_size
        self.history_seconds = history_seconds

        self.samplerate: int = 48000
        self.channels: int = 2
        self.device_name: str = ""

        self._pa = None
        self._stream = None
        # "soundcard" backend only: the thread reading the recording, and the event that ends it.
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop: Optional[threading.Event] = None
        self._buffer: Optional[np.ndarray] = None
        self._write_pos = 0
        self._filled = False
        self._lock = threading.Lock()

        self._level_rms = 0.0
        self._level_peak = 0.0
        self._callback_count = 0
        self._callback_times: list = []
        self._last_error: Optional[str] = None
        self._running = False

    # -- device selection ---------------------------------------------------

    def _resolve_device(self) -> "LoopbackDeviceInfo | InputDeviceInfo":
        if self.source == "microphone":
            return self._resolve_microphone()
        return self._resolve_loopback()

    def _resolve_loopback(self) -> LoopbackDeviceInfo:
        devices = list_loopback_devices()
        if not devices:
            if audio_backend() != "wasapi":
                raise AudioCaptureError(
                    "No loopback (monitor) device found. Make sure PulseAudio, or PipeWire with "
                    "pipewire-pulse, is running with an output device, and that the 'soundcard' "
                    "package is installed (pip install soundcard)."
                )
            raise AudioCaptureError(
                "No WASAPI loopback device found. Make sure Windows has an active "
                "playback device and PyAudioWPatch is installed correctly."
            )
        if self.device_index is None:
            for d in devices:
                if d.is_default:
                    return d
            return devices[0]
        for d in devices:
            if d.index == self.device_index:
                return d
        raise AudioCaptureError(
            f"Configured loopback device index {self.device_index} is no longer "
            f"available. Pick a device again in Audio settings."
        )

    def _resolve_microphone(self) -> InputDeviceInfo:
        devices = list_microphone_devices()
        if not devices:
            if audio_backend() != "wasapi":
                raise AudioCaptureError(
                    "No microphone/recording device found. Check the system's sound settings for an "
                    "enabled, connected recording device (and that PulseAudio / PipeWire is running)."
                )
            raise AudioCaptureError(
                "No microphone/recording device found. Check Windows' Sound settings "
                "for an enabled, connected recording device."
            )
        if self.mic_device_index is None:
            for d in devices:
                if d.is_default:
                    return d
            return devices[0]
        for d in devices:
            if d.index == self.mic_device_index:
                return d
        raise AudioCaptureError(
            f"Configured microphone device index {self.mic_device_index} is no "
            f"longer available. Pick a device again in Audio settings."
        )

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        if audio_backend() != "wasapi":
            self._start_soundcard()
            return
        try:
            import pyaudiowpatch as pyaudio
        except ImportError as e:
            raise AudioCaptureError(
                "PyAudioWPatch is not installed. Run: pip install PyAudioWPatch"
            ) from e

        info = self._resolve_device()
        self.samplerate = info.samplerate
        self.channels = max(1, info.channels)
        self.device_name = info.name

        buf_len = int(self.samplerate * self.history_seconds)
        self._buffer = np.zeros(buf_len, dtype=np.float32)
        self._write_pos = 0
        self._filled = False

        self._pa = pyaudio.PyAudio()
        try:
            self._stream = self._pa.open(
                format=pyaudio.paFloat32,
                channels=self.channels,
                rate=self.samplerate,
                frames_per_buffer=self.block_size,
                input=True,
                input_device_index=info.index,
                stream_callback=self._on_audio,
            )
            self._stream.start_stream()
        except Exception as e:
            self._pa.terminate()
            self._pa = None
            raise AudioCaptureError(f"Failed to open {self.source} stream on '{info.name}': {e}") from e

        self._running = True
        logger.info(
            "Audio capture started (source=%s): device='%s' rate=%d ch=%d block=%d",
            self.source,
            self.device_name,
            self.samplerate,
            self.channels,
            self.block_size,
        )

    # -- Linux: PulseAudio / PipeWire through `soundcard` ------------------------

    def _start_soundcard(self) -> None:
        """start() for the "soundcard" backend. That library has no callback
        API - a recording is read block by block - so a small reader thread
        does what PortAudio's callback thread does on Windows, and feeds the
        same ring buffer."""
        from . import soundcard_backend

        try:
            soundcard_backend.load()
        except soundcard_backend.SoundcardUnavailable as e:
            raise AudioCaptureError(str(e)) from e

        info = self._resolve_device()
        self.samplerate = info.samplerate
        self.channels = max(1, info.channels)
        self.device_name = info.name

        buf_len = int(self.samplerate * self.history_seconds)
        self._buffer = np.zeros(buf_len, dtype=np.float32)
        self._write_pos = 0
        self._filled = False

        stop = threading.Event()
        opened = threading.Event()
        failure: list = []
        thread = threading.Thread(
            target=self._soundcard_reader, args=(info.device_id, stop, opened, failure),
            name="audio-capture", daemon=True,
        )
        thread.start()
        if not opened.wait(timeout=5.0) or failure:
            stop.set()
            reason = failure[0] if failure else "the sound server did not answer"
            raise AudioCaptureError(f"Failed to open {self.source} stream on '{info.name}': {reason}")

        self._reader_thread = thread
        self._reader_stop = stop
        self._running = True
        logger.info(
            "Audio capture started (source=%s): device='%s' rate=%d ch=%d block=%d",
            self.source,
            self.device_name,
            self.samplerate,
            self.channels,
            self.block_size,
        )

    def _soundcard_reader(self, device_id: str, stop: threading.Event, opened: threading.Event, failure: list) -> None:
        try:
            import warnings

            from . import soundcard_backend

            source = soundcard_backend.open_source(device_id)
            # A late read is reported as a "data discontinuity" warning on
            # every block - harmless for a visualizer, endless in the log.
            warnings.filterwarnings("ignore", message=".*data discontinuity.*")
            with source.recorder(
                samplerate=self.samplerate, channels=self.channels, blocksize=self.block_size
            ) as recorder:
                opened.set()
                while not stop.is_set():
                    # Blocks until a whole block has arrived - which, while
                    # nothing at all is playing, can be until the music
                    # starts again (an idle output delivers no data, same as
                    # WASAPI loopback). Hence the second stop check: a
                    # recording that was stopped during the wait must not
                    # write into the buffer of whatever was started since.
                    block = recorder.record(numframes=self.block_size)  # float32, frames x channels
                    if stop.is_set():
                        break
                    self._process_block(np.ascontiguousarray(block, dtype=np.float32).reshape(-1))
        except Exception as e:
            if not opened.is_set():
                failure.append(e)
                opened.set()
            elif not stop.is_set():
                logger.exception("Audio capture stopped unexpectedly")
                self._last_error = f"Audio capture stopped: {e}"
                self._running = False

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._reader_stop is not None:
            # Not waited for longer than this: during silence the reader sits
            # in a blocking read (see _soundcard_reader) and only notices the
            # stop once sound arrives again - it then closes its recording
            # and ends by itself, without touching the buffer.
            self._reader_stop.set()
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=0.3)
            self._reader_thread = None
            self._reader_stop = None
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            logger.exception("Error while stopping audio stream")
        finally:
            self._stream = None
            if self._pa is not None:
                try:
                    self._pa.terminate()
                except Exception:
                    logger.exception("Error terminating PyAudio")
                self._pa = None
        logger.info("Audio capture stopped")

    def is_running(self) -> bool:
        return self._running

    # -- callback (runs on PortAudio's internal thread) ------------------------

    def _on_audio(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        self._process_block(in_data)
        return (None, pyaudio.paContinue)

    def _process_block(self, in_data) -> None:
        """One block of interleaved float32 samples, from either backend
        (PortAudio's raw bytes, or an array): down-mix to mono, write it to
        the ring buffer, update the level meter."""
        try:
            samples = np.frombuffer(in_data, dtype=np.float32)
            if self.channels > 1:
                samples = samples.reshape(-1, self.channels).mean(axis=1)
            if self.source == "microphone" and self.mic_gain != 1.0:
                # Mics are typically much quieter than a loopback tap -
                # this is the "sensitivity" knob for that. Clip afterward:
                # a gain high enough to make a quiet mic usable can easily
                # push loud transients past +-1.0, and the DSP downstream
                # assumes samples stay in that range.
                samples = np.clip(samples * self.mic_gain, -1.0, 1.0)
            n = len(samples)

            with self._lock:
                buf = self._buffer
                buf_len = len(buf)
                end = self._write_pos + n
                if end <= buf_len:
                    buf[self._write_pos:end] = samples
                else:
                    first = buf_len - self._write_pos
                    buf[self._write_pos:] = samples[:first]
                    buf[: end - buf_len] = samples[first:]
                    self._filled = True
                self._write_pos = end % buf_len
                if end >= buf_len:
                    self._filled = True

                self._level_rms = float(np.sqrt(np.mean(samples**2))) if n else self._level_rms
                self._level_peak = float(np.max(np.abs(samples))) if n else self._level_peak

            self._callback_count += 1
            now = time.perf_counter()
            self._callback_times.append(now)
            if len(self._callback_times) > 200:
                self._callback_times = self._callback_times[-100:]
        except Exception:
            logger.exception("Error in audio callback")
            self._last_error = "Audio callback error - see log"

    # -- consumer API (called from the DSP/engine thread) -----------------------

    def read_latest(self, n_samples: int) -> np.ndarray:
        """Return the most recent `n_samples` mono samples, oldest first.

        Returns a zero-filled array if not enough data has been captured yet.
        """
        with self._lock:
            if self._buffer is None:
                return np.zeros(n_samples, dtype=np.float32)
            buf = self._buffer
            buf_len = len(buf)
            n = min(n_samples, buf_len)
            end = self._write_pos
            start = (end - n) % buf_len
            if start < end:
                chunk = buf[start:end].copy()
            else:
                chunk = np.concatenate([buf[start:], buf[:end]])
            if n < n_samples:
                pad = np.zeros(n_samples - n, dtype=np.float32)
                chunk = np.concatenate([pad, chunk])
            return chunk

    def get_level(self) -> "tuple[float, float]":
        """Return (rms, peak) of the most recent audio block, linear 0..1."""
        with self._lock:
            return self._level_rms, self._level_peak

    def get_callback_rate_hz(self) -> float:
        times = self._callback_times[-50:]
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    def has_data(self) -> bool:
        with self._lock:
            return self._filled or self._write_pos > 0
