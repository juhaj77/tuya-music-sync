"""Colors and white flashes through the bulb's real-time control datapoint
(DP 28): payload format, no work_mode switching for white, the periodic
DP 24 write the stuck check relies on, and the stuck check itself in that
mode. No network involved."""
from types import SimpleNamespace

import tinytuya

import airam_lights.lamps.manager as manager_module
from airam_lights.color.models import Color, WhiteTarget
from airam_lights.config.schema import NetworkConfig
from airam_lights.lamps.manager import _DP24_SYNC_S, _STUCK_CHECKS, LampWorker
from airam_lights.lamps.tuya_device import LampDevice, LampStatus


def test_control_payload_format():
    assert LampDevice.control_payload("direct", 240, 1000, 1000) == "000f003e803e800000000"
    assert LampDevice.control_payload("gradient", 0, 0, 0, 1000, 0) == "100000000000003e80000"


class _Bulb:
    """Stands in for tinytuya.BulbDevice: records every write."""

    def __init__(self, bulb_type="B"):
        self.bulb_configured = True
        self.bulb_type = bulb_type
        self.writes = []

    def set_value(self, dp, value, nowait=False):
        self.writes.append((dp, value))

    def set_colour(self, r, g, b, nowait=False):
        self.writes.append((24, (r, g, b)))

    def set_mode(self, mode, nowait=False):
        self.writes.append((21, mode))

    def set_colourtemp_percentage(self, p, nowait=False):
        self.writes.append((23, p))

    def set_brightness_percentage(self, p, nowait=False):
        self.writes.append((22, p))

    def status(self):
        return {"dps": {}}


def _device(bulb_type="B"):
    dev = LampDevice.__new__(LampDevice)
    dev.config = SimpleNamespace(id="dev1", name="KEV", ip="10.0.0.2")
    dev.status = LampStatus()
    dev._bulb = _Bulb(bulb_type)
    dev._connect_error = None
    import threading

    dev._bulb_lock = threading.Lock()
    dev._white_detect_attempted = True
    return dev


def test_direct_color_and_white_go_to_dp28_without_mode_switches():
    dev = _device()
    worker = LampWorker(dev, NetworkConfig(lamp_transitions="direct"), 0.0)
    worker._send_color(Color(0.0, 0.0, 1.0))
    worker._send_white(WhiteTarget(brightness=0.8, temp=1.0))
    worker._send_color(Color(1.0, 0.0, 0.0))
    dps = [dp for dp, _ in dev._bulb.writes]
    assert dps.count(21) == 1  # only the one-time colour-mode switch at the start, never "white"
    assert (21, "white") not in dev._bulb.writes
    assert 22 not in dps and 23 not in dps
    dp28 = [v for dp, v in dev._bulb.writes if dp == 28]
    assert dp28[0] == "000f003e803e800000000"  # blue, jump
    assert dp28[1] == "0000000000000032003e8"  # white: brightness 800, cool
    assert dp28[2] == "0000003e803e800000000"  # red


def test_legacy_and_non_v2_bulbs_keep_the_old_path():
    for transitions, bulb_type in (("legacy", "B"), ("direct", "A")):
        dev = _device(bulb_type)
        worker = LampWorker(dev, NetworkConfig(lamp_transitions=transitions), 0.0)
        worker._send_color(Color(0.0, 1.0, 0.0))
        worker._send_white(WhiteTarget(brightness=1.0, temp=0.0))
        dps = [dp for dp, _ in dev._bulb.writes]
        assert 28 not in dps
        assert (21, "white") in dev._bulb.writes and 24 in dps


def test_dp24_is_written_now_and_then_for_the_stuck_check(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    dev = _device()
    worker = LampWorker(dev, NetworkConfig(lamp_transitions="direct"), 0.0)
    for i in range(60):  # 6 s of sends at 10/s
        worker._send_color(Color.from_hsv(i * 20 % 360, 1.0, 1.0))
        clock[0] += 0.1
    dp24 = [v for dp, v in dev._bulb.writes if dp == 24]
    assert len(dp24) == 2  # at the start and ~_DP24_SYNC_S later
    assert _DP24_SYNC_S == 5.0


class _StuckV2Lamp:
    """Direct-mode lamp that answers status but ignores everything."""

    def __init__(self):
        self.config = SimpleNamespace(id="dev1", name="KEV", ip="10.0.0.2")
        self.status = LampStatus(online=True)
        self.shown = Color(0.5, 0.5, 0.5)

    def uses_control_dp(self, transition):
        return transition != "legacy"

    def ensure_colour_mode(self):
        pass

    def set_color(self, r, g, b, wait_for_ack=False, transition="legacy"):
        return 1.0

    def reconnect(self):
        pass

    def dps(self):
        return {"21": "colour", "24": tinytuya.BulbDevice.rgb_to_hexvalue(*self.shown.to_rgb255(), "hsv16")}


def test_stuck_check_works_in_direct_mode(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    lamp = _StuckV2Lamp()
    worker = LampWorker(lamp, NetworkConfig(lamp_transitions="direct"), 0.0)
    for check in range(_STUCK_CHECKS + 2):
        for i in range(40):  # 4 s of varied sends between status checks
            worker._send_color(Color.from_hsv((check * 40 + i * 9) % 360, 1.0, 1.0))
            clock[0] += 0.1
        worker.check_following(lamp.dps())
        if worker._stuck_pending:
            break
    assert worker._stuck_pending


def test_following_lamp_in_direct_mode_is_not_flagged(monkeypatch):
    """A lamp whose DP 24 follows the periodic writes (as a working bulb does)."""
    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    lamp = _StuckV2Lamp()

    def set_color(r, g, b, wait_for_ack=False, transition="legacy"):
        if transition == "legacy":
            lamp.shown = Color(r / 255.0, g / 255.0, b / 255.0)
        return 1.0

    lamp.set_color = set_color
    worker = LampWorker(lamp, NetworkConfig(lamp_transitions="direct"), 0.0)
    for check in range(10):
        for i in range(40):
            worker._send_color(Color.from_hsv((check * 40 + i * 9) % 360, 1.0, 1.0))
            clock[0] += 0.1
        worker.check_following(lamp.dps())
    assert not worker._stuck_pending


def test_white_with_colour_underneath_goes_out_in_one_command():
    dev = _device()
    worker = LampWorker(dev, NetworkConfig(lamp_transitions="direct"), 0.0)
    worker._send_white(WhiteTarget(brightness=0.5, temp=0.0, under=Color(0.0, 0.0, 0.5)))
    dp28 = [v for dp, v in dev._bulb.writes if dp == 28]
    assert dp28 == ["000f003e801f601f40000"]  # blue at ~half (128/255) + white 500, warm
