"""Music-driven hue step: the signals (dsp.hue_step), Chase using the step,
and the engine wiring it up."""
import math

import numpy as np
import pytest

from airam_lights.audio.capture import AudioCapture
from airam_lights.color.models import Color
from airam_lights.config.schema import AppConfig, ChaseEffectConfig, PerLampEffect
from airam_lights.dsp.fft_engine import FFTEngine
from airam_lights.dsp.hue_step import AdaptiveRange, HueStepSignals
from airam_lights.dsp.pitch import PitchState
from airam_lights.effects.chase import ChaseAnimator
from airam_lights.engine import visualization_engine as ve_module
from airam_lights.engine.visualization_engine import VisualizationEngine

TICK = 1.0 / 30.0


def _hue_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def test_adaptive_range_uses_the_signal_s_own_range():
    r = AdaptiveRange(min_span=0.05)
    for _ in range(60):
        r.update(0.3, TICK)
        r.update(0.5, TICK)
    assert r.update(0.5, TICK) == pytest.approx(1.0)
    assert r.update(0.3, TICK) == pytest.approx(0.0)
    assert r.update(0.4, TICK) == pytest.approx(0.5, abs=0.05)


def test_adaptive_range_reads_a_steady_signal_as_mid_range():
    r = AdaptiveRange(min_span=0.05)
    values = [r.update(0.4 + 0.001 * (i % 2), TICK) for i in range(100)]
    assert all(0.4 < v < 0.6 for v in values[10:])


def test_adaptive_range_forgets_an_old_peak():
    r = AdaptiveRange(min_span=0.05, decay_s=2.0)
    r.update(1.0, TICK)
    for _ in range(int(20.0 / TICK)):
        r.update(0.2 + 0.1 * math.sin(_), TICK)
    assert r.high < 0.4


def _settle(signals, loudness, centroid, pitch=None, seconds=3.0):
    for _ in range(int(seconds / TICK)):
        signals.update(loudness, centroid, pitch, TICK)


def test_intensity_and_brightness_follow_the_music():
    s = HueStepSignals()
    _settle(s, 0.3, 300.0)
    _settle(s, 0.6, 4000.0)  # the range now spans quiet/dark .. loud/bright
    assert s.value("intensity") > 0.9 and s.value("brightness") > 0.9
    _settle(s, 0.3, 300.0)
    assert s.value("intensity") < 0.1 and s.value("brightness") < 0.1
    assert s.value("intensity_brightness") < 0.1
    assert s.value("fixed") is None


def test_pitch_cross_fades_by_confidence():
    s = HueStepSignals()
    _settle(s, 0.3, 300.0)
    mix = s.mix
    clear = PitchState(pitch_class=6, tonic=0, tension=0.95, confidence=1.0, variety=1.0)
    lost = PitchState(pitch_class=6, tonic=0, tension=0.95, confidence=0.0, variety=1.0)
    half = PitchState(pitch_class=6, tonic=0, tension=0.95, confidence=0.5, variety=1.0)
    s.update(0.3, 300.0, clear, TICK)
    assert s.value("pitch") == pytest.approx(0.95)
    s.update(0.3, 300.0, lost, TICK)
    assert s.value("pitch") == pytest.approx(s.mix, abs=1e-6)
    s.update(0.3, 300.0, half, TICK)
    assert s.value("pitch") == pytest.approx(0.5 * 0.95 + 0.5 * s.mix, abs=1e-6)
    assert abs(s.mix - mix) < 0.05


def test_chase_uses_the_music_step_in_hue_shift_relative():
    cfg = ChaseEffectConfig(
        enabled=True, width=0.3, intensity=0.0, color_mode="hue_shift_relative", hue_shift_step_deg=15.0
    )
    animator = ChaseAnimator(cfg)
    groups = [["a"], ["b"]]
    base = {"a": Color.from_hsv(100.0, 1.0, 0.5), "b": Color.from_hsv(200.0, 1.0, 0.5)}
    animator.position = 0.0
    assert abs(_hue_diff(animator.apply(dict(base), groups)["a"].to_hsv()[0], 115.0)) < 1.0
    animator.hue_step_override = 70.0
    assert abs(_hue_diff(animator.apply(dict(base), groups)["a"].to_hsv()[0], 170.0)) < 1.0
    animator.hue_step_override = None
    assert abs(_hue_diff(animator.apply(dict(base), groups)["a"].to_hsv()[0], 115.0)) < 1.0


def test_chase_uses_the_music_step_in_hue_shift():
    cfg = ChaseEffectConfig(
        enabled=True, width=0.3, intensity=0.0, color_mode="hue_shift", custom_hue_deg=0.0,
        custom_saturation=1.0, hue_shift_step_deg=15.0,
    )
    animator = ChaseAnimator(cfg)
    groups = [["a"], ["b"], ["c"]]
    base = {k: Color.from_hsv(0.0, 1.0, 0.5) for k in "abc"}
    animator.hue_step_override = 60.0
    animator.position = 2.0
    assert abs(_hue_diff(animator.apply(dict(base), groups)["c"].to_hsv()[0], 120.0)) < 1.0


def test_hue_step_settings_survive_a_save():
    cfg = ChaseEffectConfig(hue_step_source="pitch", hue_step_min_deg=5.0, hue_step_max_deg=120.0)
    back = ChaseEffectConfig.from_dict(cfg.to_dict())
    assert (back.hue_step_source, back.hue_step_min_deg, back.hue_step_max_deg) == ("pitch", 5.0, 120.0)
    old = ChaseEffectConfig.from_dict({"color_mode": "hue_shift_relative"})
    assert old.hue_step_source == "fixed"


class _Lamps:
    def selected_device_ids(self):
        return ["a", "b", "c"]

    def push_colors(self, colors):
        pass

    def push_white_targets(self, targets):
        pass


def _chord_audio(samplerate, n):
    t = np.arange(n) / samplerate
    out = np.zeros(n)
    for midi in (60, 64, 67, 72):
        f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        for h in range(1, 6):
            out += 0.6 ** (h - 1) * np.sin(2 * np.pi * f0 * h * t)
    return (0.1 * out).astype(np.float32)


@pytest.mark.parametrize("source", ["intensity", "pitch"])
def test_engine_drives_the_chase_step_from_the_music(monkeypatch, source):
    config = AppConfig()
    config.color_mapping.mode = "hsv_music"
    config.group_switch.enabled = False
    config.chase.enabled = True
    config.chase.color_mode = "hue_shift_relative"
    config.chase.hue_step_source = source
    config.chase.hue_step_min_deg = 10.0
    config.chase.hue_step_max_deg = 100.0
    for i, dev in enumerate(["a", "b", "c"]):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i)
    audio = AudioCapture()
    sr = audio.samplerate
    monkeypatch.setattr(audio, "read_latest", lambda n: _chord_audio(sr, n))
    engine = VisualizationEngine(config, audio, _Lamps())
    engine.running = True
    engine._raw_frame = FFTEngine(sr, 2048).compute(_chord_audio(sr, 2048))
    for _ in range(90):
        engine.tick_visual()
    status = engine.hue_step_status
    assert status is not None and status["source"] == source
    assert 10.0 <= status["step_deg"] <= 100.0
    assert engine._chase_animator.hue_step_override == pytest.approx(status["step_deg"])
    if source == "pitch":
        assert status["pitch"].note_name in ("C", "E", "G")

    config.chase.hue_step_source = "fixed"
    engine.tick_visual()
    assert engine.hue_step_status is None
    assert engine._chase_animator.hue_step_override is None


def test_a_droning_note_hands_over_to_intensity_brightness():
    s = HueStepSignals()
    _settle(s, 0.3, 300.0)
    drone = PitchState(pitch_class=6, tonic=0, tension=0.95, confidence=1.0, variety=0.0)
    s.update(0.3, 300.0, drone, TICK)
    assert s.pitch_weight == 0.0
    assert s.value("pitch") == pytest.approx(s.mix, abs=1e-6)


def test_absolute_hue_spreads_the_key_s_notes_over_the_whole_range():
    s = HueStepSignals()
    # C major: F C G D A E B from 0 to 1; F# (outside the key) at the B end, Bb at the F end.
    for name, pc, expected in (("F", 5, 0.0), ("C", 0, 1 / 6), ("G", 7, 2 / 6), ("B", 11, 1.0), ("F#", 6, 1.0), ("Bb", 10, 0.0)):
        s.update(0.3, 300.0, PitchState(pitch_class=pc, tonic=0, confidence=1.0, variety=1.0), TICK)
        assert s.value("pitch", pitch_as="note") == pytest.approx(expected), name


def test_a_minor_key_uses_its_relative_major_s_notes():
    s = HueStepSignals()
    for pc_major, pc_minor in ((0, 9), (9, 6)):  # C major / A minor, A major / F# minor
        s.update(0.3, 300.0, PitchState(pitch_class=4, tonic=pc_major, confidence=1.0, variety=1.0), TICK)
        major = s.value("pitch", pitch_as="note")
        s.update(0.3, 300.0, PitchState(pitch_class=4, tonic=pc_minor, minor=True, confidence=1.0, variety=1.0), TICK)
        assert s.value("pitch", pitch_as="note") == pytest.approx(major)


def test_without_a_key_the_absolute_circle_of_fifths():
    s = HueStepSignals()
    for pc, expected in ((0, 0.0), (7, 1 / 12), (2, 2 / 12), (5, 11 / 12)):
        s.update(0.3, 300.0, PitchState(pitch_class=pc, confidence=1.0, variety=1.0), TICK)
        assert s.value("pitch", pitch_as="note") == pytest.approx(expected)


class _FakeClock:
    """Advances TICK per tick_visual() call, so the engine sees real-time dt."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _beat_sync_engine(monkeypatch, glide=False):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock)
    monkeypatch.setattr(ve_module.time, "time", clock)
    config = AppConfig()
    config.color_mapping.mode = "beat_sync"
    config.rhythm.shared_clock = False
    bs = config.color_mapping.beat_sync
    bs.hue_mode = "spectrum"
    bs.spectrum_source = "intensity_brightness"
    bs.dark_pulse_enabled = False
    bs.white_pulse_enabled = False
    bs.hue_change_dynamic = False  # the faked beats come with no hits (pure tones)
    bs.hue_attack_ms = 0.0
    bs.glide_hue = glide
    bs.hue_glide_deg = 60.0
    bs.hue_glide_timing = "decay"
    config.color_mapping.hsv.hue_min_deg = 0.0
    config.color_mapping.hsv.hue_max_deg = 240.0
    config.chase.enabled = False
    config.group_switch.enabled = False
    audio = AudioCapture()
    engine = VisualizationEngine(config, audio, _Lamps())
    engine.running = True
    tick = engine.tick_visual

    def tick_and_advance():
        tick()
        clock.now += TICK

    engine.tick_visual = tick_and_advance
    return engine, audio.samplerate


def test_beat_sync_spectrum_spreads_over_the_whole_hue_range(monkeypatch):
    # Classical-like music: the centroid only wanders between 1.5 and 3 kHz,
    # which the old absolute 20 Hz..16 kHz scale put all in one turquoise slice.
    engine, sr = _beat_sync_engine(monkeypatch)
    beat = {"now": False}
    monkeypatch.setattr(engine._beat_detector, "update", lambda e, t: beat["now"])
    hues = []
    fft = FFTEngine(sr, 2048)
    t = np.arange(2048) / sr
    for k in range(600):
        hz = 1500.0 if (k // 45) % 2 == 0 else 3000.0
        engine._raw_frame = fft.compute((0.2 * np.sin(2 * np.pi * hz * t)).astype(np.float32))
        beat["now"] = k % 15 == 0
        engine.tick_visual()
        if beat["now"] and k > 150:
            hues.append(engine._beat_target_hue)
    assert max(hues) - min(hues) > 150.0


def test_beat_sync_spectrum_glide_moves_the_way_the_music_went(monkeypatch):
    # Hue range 0..240, glide 60 deg = a quarter of the range per beat.
    engine, sr = _beat_sync_engine(monkeypatch, glide=True)
    beat = {"now": False}
    music = {"x": 0.25}
    monkeypatch.setattr(engine._beat_detector, "update", lambda e, t: beat["now"])
    monkeypatch.setattr(engine, "_spectrum_x", lambda cfg: music["x"])
    engine._raw_frame = FFTEngine(sr, 2048).compute(np.zeros(2048, dtype=np.float32))

    def beat_then_glide(x):
        music["x"] = x
        beat["now"] = True
        engine.tick_visual()
        beat["now"] = False
        landed = engine._beat_target_hue
        for _ in range(45):  # the glide runs its course (timing "decay")
            engine.tick_visual()
        return landed, engine._hue_smoother_beat.value

    beat_then_glide(0.25)
    # The music's color went up (60 -> 144 deg): the glide carries on upward,
    # the whole glide distance, even though the music's color holds still.
    landed, glided = beat_then_glide(0.6)
    assert landed == pytest.approx(144.0)
    assert glided == pytest.approx(204.0, abs=1.5)
    # Down again (144 -> 96): the glide heads down.
    landed, glided = beat_then_glide(0.4)
    assert glided == pytest.approx(36.0, abs=1.5)
    # Near the bottom, with no room left downward: it turns round instead.
    landed, glided = beat_then_glide(0.1)
    assert landed == pytest.approx(24.0)
    assert glided == pytest.approx(84.0, abs=1.5)


def test_beat_sync_step_follows_the_music(monkeypatch):
    engine, sr = _beat_sync_engine(monkeypatch)
    bs = engine.config.color_mapping.beat_sync
    bs.hue_mode = "step"
    bs.hue_step_source = "intensity"
    bs.hue_step_min_deg = 20.0
    bs.hue_step_max_deg = 150.0
    beat = {"now": False}
    music = {"value": 0.0}
    monkeypatch.setattr(engine._beat_detector, "update", lambda e, t: beat["now"])
    monkeypatch.setattr(engine._hue_step_signals, "value", lambda source, pitch_as="tension": music["value"])
    engine._raw_frame = FFTEngine(sr, 2048).compute(np.zeros(2048, dtype=np.float32))
    steps = []
    for value in (0.0, 1.0, 0.5, 0.5):
        music["value"] = value
        before = engine._beat_hue_cursor
        beat["now"] = True
        engine.tick_visual()
        beat["now"] = False
        engine.tick_visual()
        steps.append((engine._beat_hue_cursor - before) % 360.0)
    assert steps == pytest.approx([20.0, 150.0, 85.0, 85.0])
    assert engine.beat_hue_step_now == pytest.approx(85.0)

    bs.hue_step_source = "fixed"
    before = engine._beat_hue_cursor
    beat["now"] = True
    engine.tick_visual()
    assert (engine._beat_hue_cursor - before) % 360.0 == pytest.approx(bs.hue_step_deg)


def test_beat_sync_step_settings_survive_a_save():
    from airam_lights.config.schema import BeatSyncModeConfig

    cfg = BeatSyncModeConfig(hue_step_source="pitch", hue_step_min_deg=10.0, hue_step_max_deg=170.0)
    back = BeatSyncModeConfig.from_dict(cfg.to_dict())
    assert (back.hue_step_source, back.hue_step_min_deg, back.hue_step_max_deg) == ("pitch", 10.0, 170.0)
    assert BeatSyncModeConfig.from_dict({}).hue_step_source == "fixed"


def test_engine_drives_the_group_switch_step_from_the_music(monkeypatch):
    config = AppConfig()
    config.color_mapping.mode = "hsv_music"
    config.chase.enabled = False
    config.group_switch.enabled = True
    config.group_switch.color_mode = "hue_shift_relative"
    config.group_switch.hue_step_source = "pitch"
    config.group_switch.hue_step_min_deg = 10.0
    config.group_switch.hue_step_max_deg = 100.0
    for i, dev in enumerate(["a", "b", "c"]):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, effect_group=i)
    audio = AudioCapture()
    sr = audio.samplerate
    monkeypatch.setattr(audio, "read_latest", lambda n: _chord_audio(sr, n))
    engine = VisualizationEngine(config, audio, _Lamps())
    engine.running = True
    engine._raw_frame = FFTEngine(sr, 2048).compute(_chord_audio(sr, 2048))
    for _ in range(90):
        engine.tick_visual()
    status = engine.group_hue_step_status
    assert engine.hue_step_status is None
    assert status is not None and status["source"] == "pitch"
    assert 10.0 <= status["step_deg"] <= 100.0
    assert engine._group_switch_animator.hue_step_override == pytest.approx(status["step_deg"])
    assert status["pitch"].note_name in ("C", "E", "G")

    # Beat Sync White: the overlays don't run, so neither does their music step.
    config.color_mapping.mode = "beat_sync_white"
    engine.tick_visual()
    assert engine.group_hue_step_status is None
    assert engine.latest_pitch is None
