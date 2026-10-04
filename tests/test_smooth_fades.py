"""Smoother fades on the lamps' real-time control datapoint (DP 28):

- hue, saturation and value go out at the datapoint's own resolution, not
  rounded through 8-bit RGB first;
- lamp_transitions "smooth": big changes jump, small steps use the bulb's
  own short fade, so the bulb glides from one command to the next;
- a hue crossing 0/360 is interpolated the short way round in the history
  the per-lamp phase offset samples from.

No network, bulbs or audio: tinytuya.BulbDevice is a recorder.
"""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from airam_lights.color.models import Color, WhiteTarget
from airam_lights.config.schema import INSTANT_TRANSITIONS, LAMP_TRANSITIONS, NetworkConfig
from airam_lights.engine.visualization_engine import TimeSeriesBuffer
from airam_lights.lamps.manager import _SMOOTH_MAX_STEP, LampWorker
from airam_lights.lamps.tuya_device import LampDevice, LampStatus


class _Bulb:
    def __init__(self):
        self.bulb_configured = True
        self.bulb_type = "B"
        self.writes = []

    def set_value(self, dp, value, nowait=False):
        self.writes.append((dp, value))

    def status(self):
        return {"dps": {}}


def _worker(transitions):
    dev = LampDevice.__new__(LampDevice)
    dev.config = SimpleNamespace(id="dev1", name="KEV", ip="10.0.0.2")
    dev.status = LampStatus()
    dev._bulb = _Bulb()
    dev._connect_error = None
    dev._bulb_lock = threading.Lock()
    dev._white_detect_attempted = True
    return LampWorker(dev, NetworkConfig(lamp_transitions=transitions), 0.0), dev._bulb


def _decode(payload):
    """(mode, hue, saturation, value, white brightness, white temperature)"""
    return (payload[0],) + tuple(int(payload[i:i + 4], 16) for i in range(1, 21, 4))


# -- full resolution ----------------------------------------------------------------------------


def test_colours_go_out_at_the_datapoints_own_resolution():
    worker, bulb = _worker("direct")
    # 8-bit RGB would carry these as value 298 and 102, and the hue as 201.
    worker._send_color(Color.from_hsv(200.4, 1.0, 0.3))
    worker._send_color(Color.from_hsv(200.4, 1.0, 0.1))
    assert [_decode(v)[1:4] for _, v in bulb.writes] == [(200, 1000, 300), (200, 1000, 100)]


def test_a_slow_fade_no_longer_makes_the_hue_wobble():
    worker, bulb = _worker("direct")
    for step in range(60):  # a brightness fade at one hue, down to dim
        worker._send_color(Color.from_hsv(57.4, 1.0, 0.5 - step * 0.007))
    hues = {_decode(v)[1] for _, v in bulb.writes}
    values = [_decode(v)[3] for _, v in bulb.writes]
    assert hues == {57}
    assert all(a > b for a, b in zip(values, values[1:]))  # every step is its own level, none repeated


# -- lamp_transitions "smooth" -------------------------------------------------------------------


def test_smooth_is_a_real_time_transition():
    assert "smooth" in LAMP_TRANSITIONS and "smooth" in INSTANT_TRANSITIONS and "gradient" not in INSTANT_TRANSITIONS
    assert NetworkConfig.from_dict({"lamp_transitions": "smooth"}).lamp_transitions == "smooth"
    assert NetworkConfig.from_dict({"lamp_transitions": "nonsense"}).lamp_transitions == "direct"
    assert NetworkConfig().lamp_transitions == "direct"  # the default stays as it was


def test_smooth_jumps_on_big_changes_and_lets_the_bulb_fade_small_steps():
    worker, bulb = _worker("smooth")
    worker._send_color(Color.from_hsv(0.0, 1.0, 0.4))   # first command: nothing to fade from
    worker._send_color(Color.from_hsv(0.0, 1.0, 1.0))   # the beat's flash: a big step up
    worker._send_color(Color.from_hsv(0.0, 1.0, 0.94))  # the fade after it: small steps...
    worker._send_color(Color.from_hsv(6.0, 1.0, 0.9))   # ...with the hue on the move too
    worker._send_color(Color.from_hsv(12.0, 1.0, 0.86))
    worker._send_color(Color.from_hsv(12.0, 1.0, 0.1))  # a dark pulse cutting in: big again
    assert [_decode(v)[0] for _, v in bulb.writes] == ["0", "0", "1", "1", "1", "0"]


def test_smooth_keeps_white_flashes_crisp_and_fades_their_tail():
    worker, bulb = _worker("smooth")
    colour = Color.from_hsv(120.0, 1.0, 0.5)
    worker._send_color(colour)
    worker._send_white(WhiteTarget(0.08, 1.0, under=colour))  # lighting up, however little: at once
    worker._send_white(WhiteTarget(0.30, 1.0, under=colour))  # still rising
    worker._send_white(WhiteTarget(0.24, 1.0, under=colour))  # the tail: the bulb fades it
    worker._send_white(WhiteTarget(0.10, 1.0, under=colour))
    worker._send_color(colour)                                # white off again: a small last step
    assert [_decode(v)[0] for _, v in bulb.writes] == ["0", "0", "0", "1", "1", "1"]
    # A white flash dropping a long way in one command still jumps.
    worker._send_white(WhiteTarget(0.9, 1.0, under=colour))
    worker._send_white(WhiteTarget(0.9 - _SMOOTH_MAX_STEP - 0.05, 1.0, under=colour))
    assert [_decode(v)[0] for _, v in bulb.writes[-2:]] == ["0", "0"]


def test_direct_and_gradient_do_not_decide_per_command():
    for transitions, mode in (("direct", "0"), ("gradient", "1")):
        worker, bulb = _worker(transitions)
        worker._send_color(Color.from_hsv(0.0, 1.0, 0.4))
        worker._send_color(Color.from_hsv(0.0, 1.0, 1.0))
        worker._send_color(Color.from_hsv(0.0, 1.0, 0.95))
        assert [_decode(v)[0] for _, v in bulb.writes] == [mode] * 3


# -- history interpolation ----------------------------------------------------------------------------


def test_history_interpolates_a_hue_the_short_way_round():
    history = TimeSeriesBuffer(circular_index=0)  # [hue, value, saturation]
    history.push(10.0, np.array([350.0, 0.2, 1.0]))
    history.push(10.1, np.array([10.0, 0.6, 1.0]))
    hue, value, saturation = history.sample_at(10.05)
    assert hue == pytest.approx(0.0, abs=1e-6) or hue == pytest.approx(360.0, abs=1e-6)  # not 180
    assert value == pytest.approx(0.4) and saturation == pytest.approx(1.0)
    hue, _, _ = history.sample_at(10.025)
    assert hue == pytest.approx(355.0)
    # Other histories (plain levels) stay plain linear.
    levels = TimeSeriesBuffer()
    levels.push(1.0, np.array([350.0]))
    levels.push(2.0, np.array([10.0]))
    assert levels.sample_at(1.5)[0] == pytest.approx(180.0)


# -- the colour under a white flash follows the overlays ----------------------------------------------

import airam_lights.engine.visualization_engine as ve_module  # noqa: E402
from airam_lights.audio.capture import AudioCapture  # noqa: E402
from airam_lights.config.schema import AppConfig, PerLampEffect  # noqa: E402
from airam_lights.effects.chase import get_chase_groups, get_group_switch_groups  # noqa: E402
from airam_lights.engine.visualization_engine import VisualizationEngine  # noqa: E402

PERIOD = 0.5  # 120 BPM
TICK = 1.0 / 30.0
DEVICES = ["a", "b", "c", "d"]


class _Lamps:
    def __init__(self):
        self.white_targets = {}

    def selected_device_ids(self):
        return list(DEVICES)

    def push_colors(self, colors):
        pass

    def push_white_targets(self, targets):
        self.white_targets = dict(targets)


def _engine(monkeypatch, configure):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ve_module.time, "perf_counter", lambda: clock.now)
    monkeypatch.setattr(ve_module.time, "time", lambda: clock.now)

    def kick_energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", kick_energy)
    config = AppConfig()
    config.color_mapping.mode = "beat_sync"
    config.color_mapping.saturation = 1.0
    config.network.lamp_transitions = "direct"
    config.rhythm.shared_clock = False
    config.sequencer.enabled = False
    bs = config.color_mapping.beat_sync
    bs.sensitivity, bs.min_interval_ms, bs.min_energy = 1.05, 0.0, 0.0
    bs.saturation = 1.0
    bs.dark_pulse_enabled = False
    bs.white_pulse_enabled = True
    bs.white_pulse_probability = 1.0  # every beat, every lamp
    bs.white_pulse_target = "all"
    bs.white_pulse_duration_ms, bs.white_pulse_attack_ms, bs.white_pulse_release_ms = 120.0, 20.0, 150.0
    config.chase.enabled = False
    config.group_switch.enabled = False
    for i, dev in enumerate(DEVICES):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)
    configure(config)
    lamps = _Lamps()
    engine = VisualizationEngine(config, AudioCapture(), lamps)
    engine.running = True
    engine._raw_frame = object()
    return engine, clock, lamps


def _hue_gap(a, b):
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def test_colour_under_a_white_flash_follows_group_switch(monkeypatch):
    def configure(c):
        gs = c.group_switch
        gs.enabled, gs.color_mode, gs.intensity, gs.sync_mode, gs.speed_rotations_per_s = True, "complementary", 0.0, "off", 0.2

    engine, clock, lamps = _engine(monkeypatch, configure)
    checked = {"active": 0, "other": 0}
    for _ in range(int(12.0 / TICK)):
        lamps.white_targets = {}
        engine.tick_visual()
        own_hue = engine.latest_band3_levels["hue"]
        groups = get_group_switch_groups(engine.config.per_lamp_effects, DEVICES)
        active = engine._group_switch_animator.active_device_ids(groups)
        for lamp, target in lamps.white_targets.items():
            if target.under is None or target.under.to_hsv()[2] < 0.02:
                continue  # too dark under the flash's peak to have a hue
            expected = (own_hue + 180.0) % 360.0 if lamp in active else own_hue
            assert _hue_gap(target.under.to_hsv()[0], expected) < 1.0, (lamp, lamp in active)
            checked["active" if lamp in active else "other"] += 1
        clock.now += TICK
    assert checked["active"] > 20 and checked["other"] > 20


def test_colour_under_a_white_flash_follows_chase(monkeypatch):
    def configure(c):
        ch = c.chase
        ch.enabled, ch.color_mode, ch.intensity, ch.sync_mode = True, "complementary", 0.0, "off"
        ch.speed_rotations_per_s, ch.width, ch.num_rotators = 0.2, 1.5, 1

    engine, clock, lamps = _engine(monkeypatch, configure)
    checked = shifted = 0
    for _ in range(int(12.0 / TICK)):
        lamps.white_targets = {}
        engine.tick_visual()
        own_hue = engine.latest_band3_levels["hue"]
        groups = get_chase_groups(engine.config.per_lamp_effects, DEVICES)
        weights = engine._chase_animator._target_weights(len(groups))
        for index, group in enumerate(groups):
            for lamp in group:
                target = lamps.white_targets.get(lamp)
                if target is None or target.under is None or target.under.to_hsv()[2] < 0.02:
                    continue
                assert _hue_gap(target.under.to_hsv()[0], (own_hue + 180.0 * weights[index]) % 360.0) < 1.0, lamp
                checked += 1
                shifted += weights[index] > 0.3
        clock.now += TICK
    assert checked > 40 and shifted > 10  # including lamps the highlight had clearly recoloured

