"""A lamp that answers status queries but stopped following commands:
LampWorker notices (reported state vs. recently sent colors), reconnects
once, then parks it with a clear reason, and only lets it back in once it
demonstrably obeys again. No network involved."""
from types import SimpleNamespace

import tinytuya

from airam_lights.color.models import Color
from airam_lights.config.schema import NetworkConfig
from airam_lights.lamps.manager import _STUCK_CHECKS, LampManager, LampWorker
from airam_lights.lamps.tuya_device import LampStatus, parse_reported_state, reported_matches

RED = Color(1.0, 0.0, 0.0)


def _dps(color):
    return {"21": "colour", "24": tinytuya.BulbDevice.rgb_to_hexvalue(*color.to_rgb255(), "hsv16")}


GREY = Color(0.5, 0.5, 0.5)  # frozen color that none of the show's saturated colors match


class _Lamp:
    """Answers status; applies colors only while `following`."""

    def __init__(self, following=True, shown=GREY):
        self.config = SimpleNamespace(id="dev1", name="OV", ip="10.0.0.5")
        self.status = LampStatus(online=True)
        self.following = following
        self.shown = shown
        self.reconnects = 0

    def ensure_colour_mode(self):
        pass

    def set_color(self, r, g, b, wait_for_ack=False):
        if self.following:
            self.shown = Color(r / 255.0, g / 255.0, b / 255.0)
        return 1.0

    def reconnect(self):
        self.reconnects += 1

    def refresh_status(self):
        self.status = LampStatus(online=True, raw_dps=_dps(self.shown))
        return self.status

    def probe(self):
        return None  # it always answers


def _play(worker, n=8):
    """Send a burst of clearly different colors."""
    for i in range(n):
        worker._send_color(Color.from_hsv((i * 47) % 360, 1.0, 1.0))


def _check(worker, lamp):
    worker.check_following(lamp.refresh_status().raw_dps)


def test_reported_state_parsing():
    assert parse_reported_state({"21": "white"}) == ("white",)
    assert parse_reported_state({"21": "scene"}) is None
    rep = parse_reported_state(_dps(RED))
    assert reported_matches(rep, "colour", RED.to_hsv())
    assert not reported_matches(rep, "colour", Color(0.0, 0.5, 1.0).to_hsv())


def test_following_lamp_is_never_flagged():
    lamp = _Lamp(following=True)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    for _ in range(10):
        _play(worker)
        _check(worker, lamp)
    assert not worker._stuck_pending and not worker.dormant


def test_idle_lamp_is_not_judged():
    lamp = _Lamp(following=False)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    worker._send_color(Color(0.2, 0.4, 0.9))
    worker._send_color(Color(0.9, 0.4, 0.2))  # a couple of sends: too idle to judge
    for _ in range(10):
        _check(worker, lamp)
    assert not worker._stuck_pending


def test_stuck_lamp_reconnects_then_is_parked_with_a_reason():
    lamp = _Lamp(following=False)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    worker.context_provider = lambda: "show: mode=beat_sync, glide_hue=beat"

    # +1: the very first check has nothing to compare "unchanged" against yet.
    for _ in range(_STUCK_CHECKS + 1):
        _play(worker)
        _check(worker, lamp)
    assert worker._stuck_pending
    worker._handle_stuck()
    assert lamp.reconnects == 1 and not worker.dormant  # first: try a fresh connection

    for _ in range(_STUCK_CHECKS + 1):
        _play(worker)
        _check(worker, lamp)
    worker._handle_stuck()
    assert worker.dormant and worker._dormant_stuck
    assert "off and on" in worker.dormant_reason
    report = worker.diagnostics_report()
    assert "commands/s" in report and "glide_hue=beat" in report


def test_stuck_lamp_rejoins_only_once_it_obeys_again():
    lamp = _Lamp(following=False)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    worker._stuck_reconnected = True
    worker._stuck_pending = True
    worker._handle_stuck()
    assert worker.dormant

    worker._probe_and_recover()  # answers, but still ignores the test color
    assert worker.dormant

    lamp.following = True  # power-cycled
    worker._probe_and_recover()
    assert not worker.dormant


def test_status_refresh_shows_a_parked_lamp_as_offline_with_the_reason():
    lamp = _Lamp(following=False)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    worker._stuck_reconnected = True
    worker._stuck_pending = True
    worker._handle_stuck()
    LampManager._safe_refresh(lamp, worker)
    assert lamp.status.online is False
    assert "off and on" in lamp.status.last_error


def test_frozen_on_a_color_the_show_keeps_revisiting_is_still_caught(monkeypatch):
    """Frozen on red while the show cycles through red too: the checks where
    red happens to be in the recent commands are neither for nor against it,
    so it's still caught - it just takes a few more checks."""
    import airam_lights.lamps.manager as manager_module

    clock = [1000.0]
    monkeypatch.setattr(manager_module.time, "perf_counter", lambda: clock[0])
    lamp = _Lamp(following=False, shown=RED)
    worker = LampWorker(lamp, NetworkConfig(), 0.0)
    for i in range(3 * _STUCK_CHECKS):
        clock[0] += 4.0  # status checks are ~4 s apart
        if i % 2:
            _play(worker)  # includes red
        else:
            for k in range(8):
                worker._send_color(Color.from_hsv(100 + k * 20, 1.0, 1.0))  # no red
        _check(worker, lamp)
        if worker._stuck_pending:
            break
    assert worker._stuck_pending
