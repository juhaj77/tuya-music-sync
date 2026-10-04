"""Colors and white flashes through the bulb's real-time control datapoint
(DP 28): payload format, and that nothing else is written in that mode (no
work_mode, no DP 24), so the bulb's saved state survives the show. No
network involved."""
from types import SimpleNamespace

import tinytuya

import airam_lights.lamps.manager as manager_module
from airam_lights.color.models import Color, WhiteTarget
from airam_lights.config.schema import NetworkConfig
from airam_lights.lamps.manager import _STUCK_CHECKS, LampWorker
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
    assert set(dps) == {28}  # no work_mode (21), white (22/23) or colour (24) writes
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


def test_saved_state_is_never_touched_over_a_long_show(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    dev = _device()
    worker = LampWorker(dev, NetworkConfig(lamp_transitions="direct"), 0.0)
    for i in range(600):  # 60 s of sends at 10/s
        worker._send_color(Color.from_hsv(i * 20 % 360, 1.0, 1.0))
        clock[0] += 0.1
    assert {dp for dp, _ in dev._bulb.writes} == {28}


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


def test_stuck_check_stays_quiet_in_direct_mode(monkeypatch):
    """DP 28 sends can't be checked against the lamp's status - a lamp
    whose reported colour never changes must not be flagged for it."""
    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    lamp = _StuckV2Lamp()
    worker = LampWorker(lamp, NetworkConfig(lamp_transitions="direct"), 0.0)
    for check in range(_STUCK_CHECKS + 5):
        for i in range(40):  # 4 s of varied sends between status checks
            worker._send_color(Color.from_hsv((check * 40 + i * 9) % 360, 1.0, 1.0))
            clock[0] += 0.1
        worker.check_following(lamp.dps())
    assert not worker._stuck_pending


def test_white_with_colour_underneath_goes_out_in_one_command():
    dev = _device()
    worker = LampWorker(dev, NetworkConfig(lamp_transitions="direct"), 0.0)
    worker._send_white(WhiteTarget(brightness=0.5, temp=0.0, under=Color(0.0, 0.0, 0.5)))
    dp28 = [v for dp, v in dev._bulb.writes if dp == 28]
    # Blue at exactly half (value 500 - not rounded through 8-bit RGB to 128/255 = 502) + white 500, warm.
    assert dp28 == ["000f003e801f401f40000"]
