"""The per-operating-system parts: which audio library is used where, the
settings folder, and the sleep lock.

Everything here runs on any system: the platform is switched with
monkeypatch, and the audio libraries themselves (PyAudioWPatch on Windows,
soundcard on Linux) are replaced by small fakes - so the Windows code path
is exercised on Linux and the Linux one on Windows, without either library
or any audio hardware.
"""
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

from airam_lights import keep_awake
from airam_lights.audio import soundcard_backend
from airam_lights.audio.capture import AudioCapture, AudioCaptureError
from airam_lights.audio.devices import audio_backend, list_loopback_devices, list_microphone_devices
from airam_lights.config.store import default_config_dir, default_config_path
from airam_lights.lamps.cloud_keys import credential_paths

SAMPLERATE = 48000


def _peak_hz(capture: AudioCapture) -> float:
    samples = capture.read_latest(8192)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.argmax(spectrum)) * capture.samplerate / len(samples)


def _wait_for(condition, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


# -- which library on which system -----------------------------------------------------------


def test_backend_follows_the_operating_system(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert audio_backend() == "wasapi"
    for platform in ("linux", "darwin", "freebsd14"):
        monkeypatch.setattr(sys, "platform", platform)
        assert audio_backend() == "soundcard"


# -- Linux: PulseAudio / PipeWire through `soundcard` ------------------------------------------


class _FakeRecorder:
    def __init__(self, source, samplerate, channels, blocksize):
        self.source, self.samplerate, self.channels, self.blocksize = source, samplerate, channels, blocksize
        self._frame = 0

    def __enter__(self):
        self.source.open_recorders += 1
        return self

    def __exit__(self, *exc):
        self.source.open_recorders -= 1

    def record(self, numframes):
        time.sleep(numframes / self.samplerate / 4)  # real time, sped up
        t = (self._frame + np.arange(numframes)) / self.samplerate
        self._frame += numframes
        mono = 0.5 * np.sin(2 * np.pi * self.source.tone_hz * t)
        return np.repeat(mono[:, None], self.channels, axis=1).astype(np.float32)  # frames x channels


class _FakeSource:
    def __init__(self, id, name, isloopback, channels=2, tone_hz=1000.0):
        self.id, self.name, self.isloopback, self.channels, self.tone_hz = id, name, isloopback, channels, tone_hz
        self.open_recorders = 0
        self.recorder_args = None

    def recorder(self, samplerate, channels=None, blocksize=None):
        self.recorder_args = dict(samplerate=samplerate, channels=channels, blocksize=blocksize)
        return _FakeRecorder(self, samplerate, channels, blocksize)


@pytest.fixture
def linux(monkeypatch):
    """A Linux machine with two outputs (speakers = the default, a headset)
    and one real microphone, seen through a fake `soundcard` module.
    PyAudioWPatch is made un-importable: this path must never touch it."""
    sources = [
        _FakeSource("alsa_input.usb-mic", "USB Microphone", False, channels=1, tone_hz=300.0),
        _FakeSource("alsa_output.speakers.monitor", "Monitor of Speakers", True, tone_hz=1000.0),
        _FakeSource("alsa_output.headset.monitor", "Monitor of Headset", True, tone_hz=2000.0),
    ]
    fake = types.ModuleType("soundcard")
    fake.all_microphones = lambda include_loopback=False, exclude_monitors=True: [
        s for s in sources if include_loopback or not s.isloopback
    ]
    fake.default_speaker = lambda: types.SimpleNamespace(id="alsa_output.speakers", name="Speakers")
    fake.default_microphone = lambda: sources[0]

    def get_microphone(id, include_loopback=False, exclude_monitors=True):
        return next(s for s in sources if s.id == id)

    fake.get_microphone = get_microphone
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "soundcard", fake)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)
    return sources


def test_linux_lists_the_outputs_monitors_as_loopback_devices(linux):
    devices = list_loopback_devices()
    assert [(d.name, d.device_id, d.is_default) for d in devices] == [
        ("Monitor of Speakers", "alsa_output.speakers.monitor", True),
        ("Monitor of Headset", "alsa_output.headset.monitor", False),
    ]
    assert all(d.samplerate == SAMPLERATE and d.channels == 2 for d in devices)
    microphones = list_microphone_devices()
    assert [(d.name, d.is_default, d.channels) for d in microphones] == [("USB Microphone", True, 1)]
    # One index space for both lists, so a saved index can't mean two devices.
    indexes = [d.index for d in devices + microphones]
    assert len(set(indexes)) == len(indexes)


def test_linux_captures_what_the_default_output_is_playing(linux):
    capture = AudioCapture(block_size=1024)
    capture.start()
    try:
        assert capture.is_running()
        assert (capture.device_name, capture.samplerate, capture.channels) == ("Monitor of Speakers", SAMPLERATE, 2)
        assert linux[1].recorder_args == dict(samplerate=SAMPLERATE, channels=2, blocksize=1024)
        assert _wait_for(lambda: capture.has_data() and capture.get_callback_rate_hz() > 0)
        assert _wait_for(lambda: abs(_peak_hz(capture) - 1000.0) < 10.0)  # down-mixed to mono, in the ring buffer
        rms, peak = capture.get_level()
        assert peak == pytest.approx(0.5, abs=0.02) and rms == pytest.approx(0.5 / np.sqrt(2), abs=0.02)
    finally:
        capture.stop()
    assert not capture.is_running()
    assert _wait_for(lambda: linux[1].open_recorders == 0)  # the recording was closed
    assert _wait_for(lambda: not [t for t in threading.enumerate() if t.name == "audio-capture"])


def test_linux_captures_a_chosen_output_or_the_microphone(linux):
    headset = [d for d in list_loopback_devices() if not d.is_default][0]
    capture = AudioCapture(device_index=headset.index)
    capture.start()
    try:
        assert capture.device_name == "Monitor of Headset"
        assert _wait_for(lambda: abs(_peak_hz(capture) - 2000.0) < 10.0)
    finally:
        capture.stop()
    capture = AudioCapture(source="microphone", mic_gain=1.5)
    capture.start()
    try:
        assert (capture.device_name, capture.channels) == ("USB Microphone", 1)
        assert _wait_for(lambda: abs(_peak_hz(capture) - 300.0) < 10.0)
        assert _wait_for(lambda: capture.get_level()[1] == pytest.approx(0.75, abs=0.03))  # the mic gain applies
    finally:
        capture.stop()


def test_linux_restart_does_not_leave_an_old_reader_writing(linux):
    capture = AudioCapture()
    capture.start()
    assert _wait_for(capture.has_data)
    capture.stop()
    capture.device_index = [d for d in list_loopback_devices() if not d.is_default][0].index
    capture.start()
    try:
        assert _wait_for(lambda: abs(_peak_hz(capture) - 2000.0) < 10.0)
        assert _wait_for(lambda: linux[1].open_recorders == 0)
        time.sleep(0.2)
        assert abs(_peak_hz(capture) - 2000.0) < 10.0  # only the headset's tone, nothing mixed in
    finally:
        capture.stop()


def test_linux_says_what_is_missing(linux, monkeypatch):
    with pytest.raises(AudioCaptureError, match="index 99 is no longer available"):
        AudioCapture(device_index=99).start()
    # The package isn't installed.
    monkeypatch.setitem(sys.modules, "soundcard", None)
    assert list_loopback_devices() == [] and list_microphone_devices() == []
    with pytest.raises(AudioCaptureError, match="pip install soundcard"):
        AudioCapture().start()

    # Installed, but there's no sound server: importing it raises.
    def no_server():
        raise soundcard_backend.SoundcardUnavailable("Could not connect to the sound server - PulseAudio ...")

    monkeypatch.setattr(soundcard_backend, "load", no_server)
    assert list_loopback_devices() == []
    with pytest.raises(AudioCaptureError, match="sound server"):
        AudioCapture().start()


def test_linux_reports_a_recording_that_cannot_be_opened(linux):
    def broken(samplerate, channels=None, blocksize=None):
        raise RuntimeError("Stream creation failed")

    linux[1].recorder = broken
    capture = AudioCapture()
    with pytest.raises(AudioCaptureError, match="Failed to open loopback stream on 'Monitor of Speakers'.*Stream creation failed"):
        capture.start()
    assert not capture.is_running()


# -- Windows: WASAPI through PyAudioWPatch (unchanged behaviour) ---------------------------------


class _FakeStream:
    def __init__(self):
        self.started = self.stopped = self.closed = False

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakePyAudio:
    """PyAudioWPatch's PyAudio: two host APIs (MME, WASAPI), speakers with
    their WASAPI loopback twin, and a microphone."""

    devices = [
        {"index": 0, "name": "Speakers (MME)", "hostApi": 0, "maxInputChannels": 0, "defaultSampleRate": 44100.0},
        {"index": 1, "name": "Microphone", "hostApi": 1, "maxInputChannels": 1, "defaultSampleRate": 48000.0},
        {"index": 2, "name": "Speakers [Loopback]", "hostApi": 1, "maxInputChannels": 2,
         "defaultSampleRate": 44100.0, "isLoopbackDevice": True},
        {"index": 3, "name": "Speakers", "hostApi": 1, "maxInputChannels": 0, "defaultSampleRate": 44100.0},
    ]
    instances = []

    def __init__(self):
        self.opened = None
        self.stream = None
        self.terminated = False
        _FakePyAudio.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.terminated = True

    def get_default_wasapi_loopback(self):
        return self.devices[2]

    def get_default_input_device_info(self):
        return self.devices[1]

    def get_host_api_info_by_type(self, host_api_type):
        assert host_api_type == "WASAPI"
        return {"index": 1}

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def open(self, **kwargs):
        self.opened = kwargs
        self.stream = _FakeStream()
        return self.stream

    def terminate(self):
        self.terminated = True


@pytest.fixture
def windows(monkeypatch):
    """A Windows machine seen through a fake `pyaudiowpatch` module.
    soundcard is made un-importable: this path must never touch it."""
    fake = types.ModuleType("pyaudiowpatch")
    fake.PyAudio = _FakePyAudio
    fake.paFloat32, fake.paContinue, fake.paWASAPI = "float32", "continue", "WASAPI"
    _FakePyAudio.instances = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", fake)
    monkeypatch.setitem(sys.modules, "soundcard", None)
    return fake


def test_windows_still_lists_wasapi_devices(windows):
    loopback = list_loopback_devices()
    assert [(d.index, d.name, d.samplerate, d.channels, d.is_default) for d in loopback] == [
        (2, "Speakers [Loopback]", 44100, 2, True)
    ]
    microphones = list_microphone_devices()
    assert [(d.index, d.name, d.samplerate, d.channels, d.is_default) for d in microphones] == [
        (1, "Microphone", 48000, 1, True)
    ]


def test_windows_still_captures_through_the_portaudio_callback(windows):
    capture = AudioCapture(block_size=512)
    capture.start()
    pa = _FakePyAudio.instances[-1]
    assert capture.is_running() and pa.stream.started
    assert (capture.device_name, capture.samplerate, capture.channels) == ("Speakers [Loopback]", 44100, 2)
    callback = pa.opened.pop("stream_callback")
    assert pa.opened == dict(
        format="float32", channels=2, rate=44100, frames_per_buffer=512, input=True, input_device_index=2
    )
    assert not [t for t in threading.enumerate() if t.name == "audio-capture"]  # no reader thread on Windows

    # PortAudio hands the callback raw interleaved float32 bytes.
    frame = 0
    for _ in range(40):
        t = (frame + np.arange(512)) / 44100
        frame += 512
        mono = 0.5 * np.sin(2 * np.pi * 1000.0 * t)
        block = np.repeat(mono[:, None], 2, axis=1).astype(np.float32)
        assert callback(block.tobytes(), 512, None, None) == (None, "continue")
    assert capture.has_data()
    assert abs(_peak_hz(capture) - 1000.0) < 10.0
    assert capture.get_level()[1] == pytest.approx(0.5, abs=0.02)

    capture.stop()
    assert not capture.is_running()
    assert pa.stream.stopped and pa.stream.closed and pa.terminated


def test_windows_says_when_pyaudiowpatch_is_missing(windows, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)
    assert list_loopback_devices() == []
    with pytest.raises(AudioCaptureError, match="PyAudioWPatch is not installed"):
        AudioCapture().start()


# -- settings folder ----------------------------------------------------------------------------------


def test_settings_folder_on_windows_is_appdata_as_before(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # ignored on Windows
    assert default_config_dir() == tmp_path / "Roaming" / "AiramMusicLights"
    assert default_config_path() == tmp_path / "Roaming" / "AiramMusicLights" / "config.json"
    monkeypatch.delenv("APPDATA")
    assert default_config_dir() == Path.home() / "AppData" / "Roaming" / "AiramMusicLights"


def test_settings_folder_on_linux_is_the_xdg_config_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))  # ignored on Linux
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert default_config_dir() == Path.home() / ".config" / "AiramMusicLights"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_config_dir() == tmp_path / "xdg" / "AiramMusicLights"
    # The setup wizard's cloud credentials are looked for there too.
    assert tmp_path / "xdg" / "AiramMusicLights" / "tinytuya.json" in credential_paths()


# -- sleep lock on Linux ------------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, exits_at_once=False):
        self.returncode = 1 if exits_at_once else None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def linux_inhibit(monkeypatch):
    calls = []
    state = types.SimpleNamespace(calls=calls, exits_at_once=False, installed=True, process=None)

    def popen(command, **kwargs):
        calls.append(command)
        state.process = _FakeProcess(state.exits_at_once)
        return state.process

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keep_awake, "_linux_inhibitor", None)
    monkeypatch.setattr(keep_awake.subprocess, "Popen", popen)
    monkeypatch.setattr(keep_awake.shutil, "which", lambda name: f"/usr/bin/{name}" if state.installed else None)
    monkeypatch.setattr(keep_awake.time, "sleep", lambda seconds: None)
    return state


def test_linux_sleep_lock_is_held_by_systemd_inhibit(linux_inhibit):
    import os

    assert keep_awake.prevent_sleep() is True
    (command,) = linux_inhibit.calls
    assert command[0] == "systemd-inhibit" and "--what=sleep" in command and "--mode=block" in command
    # It watches this very process, so the lock can't outlive the app.
    assert command[-4:] == ["tail", f"--pid={os.getpid()}", "-f", "/dev/null"]
    assert keep_awake.prevent_sleep() is True and len(linux_inhibit.calls) == 1  # already held: not taken twice
    keep_awake.allow_sleep()
    assert linux_inhibit.process.terminated
    keep_awake.allow_sleep()  # nothing held any more: harmless


def test_linux_sleep_lock_fails_quietly(linux_inhibit):
    linux_inhibit.installed = False  # no systemd on this machine
    assert keep_awake.prevent_sleep() is False and linux_inhibit.calls == []
    linux_inhibit.installed = True
    linux_inhibit.exits_at_once = True  # logind refused, or isn't running
    assert keep_awake.prevent_sleep() is False
    keep_awake.allow_sleep()
