"""A lamp that retrying alone can't bring back (stuck lamp, new IP, new
key after re-pairing): LampWorker parks it as dormant after a bounded number
of reconnects, probes it periodically, applies the matching automatic fix,
and puts it back in the show once it answers. No network involved."""
from types import SimpleNamespace

import pytest

import airam_lights.lamps.manager as manager_module
from airam_lights.color.models import Color
from airam_lights.config.schema import NetworkConfig
from airam_lights.lamps.discovery import DiscoveredDevice
from airam_lights.lamps.manager import _DORMANT_AFTER_RECONNECTS, _RECONNECT_THRESHOLD, LampManager, LampWorker
from airam_lights.lamps.tuya_device import LampConnectionError, LampStatus


class _BrokenDevice:
    """Fails every send with `code` until `fixed` is set (or its config
    matches `good_ip` / `good_key`, when given)."""

    def __init__(self, code="914", good_ip=None, good_key=None):
        self.config = SimpleNamespace(id="dev1", name="OV", ip="10.0.0.5", version="3.4", local_key="old")
        self.status = LampStatus()
        self.code = code
        self.good_ip = good_ip
        self.good_key = good_key
        self.fixed = False
        self.sends = 0

    def _ok(self):
        return (
            self.fixed
            or (self.good_ip is not None and self.config.ip == self.good_ip)
            or (self.good_key is not None and self.config.local_key == self.good_key)
        )

    def ensure_colour_mode(self):
        pass

    def set_color(self, r, g, b, wait_for_ack=False):
        if not self._ok():
            raise LampConnectionError("broken", self.code)
        self.sends += 1
        return 5.0

    def reconnect(self):
        pass

    def reconfigure(self, config):
        self.config = config

    def probe(self):
        return None if self._ok() else LampConnectionError("broken", self.code)


def _fail_until_dormant(worker):
    for _ in range(_RECONNECT_THRESHOLD * _DORMANT_AFTER_RECONNECTS):
        worker._send_color(Color(0.5, 0.5, 0.5))
    assert worker.dormant


def _worker(device):
    return LampWorker(device, NetworkConfig(), 0.01)


def test_goes_dormant_after_bounded_reconnects_and_rejoins_when_fixed():
    device = _BrokenDevice()
    worker = _worker(device)
    _fail_until_dormant(worker)

    worker._probe_and_recover()
    assert worker.dormant  # still broken

    device.fixed = True  # e.g. power-cycled at the wall
    worker._probe_and_recover()
    assert not worker.dormant
    assert worker._consecutive_failures == 0
    worker._send_color(Color(0.1, 0.2, 0.3))
    assert device.sends == 1


def test_follows_a_new_ip_address(monkeypatch):
    device = _BrokenDevice(code="905", good_ip="10.0.0.99")
    worker = _worker(device)
    _fail_until_dormant(worker)
    monkeypatch.setattr(
        manager_module, "find_device_address",
        lambda device_id: DiscoveredDevice(ip="10.0.0.99", device_id=device_id, version="3.4"),
    )
    worker._probe_and_recover()
    assert not worker.dormant
    assert device.config.ip == "10.0.0.99"


def test_fetches_a_changed_key_from_the_cloud(monkeypatch):
    device = _BrokenDevice(code="914", good_key="new")
    worker = _worker(device)
    _fail_until_dormant(worker)
    monkeypatch.setattr(manager_module.cloud_keys, "fetch_local_keys", lambda ids: {i: "new" for i in ids})
    worker._probe_and_recover()
    assert not worker.dormant
    assert device.config.local_key == "new"


def test_cloud_key_check_is_rate_limited(monkeypatch):
    device = _BrokenDevice(code="914")
    worker = _worker(device)
    _fail_until_dormant(worker)
    calls = []
    monkeypatch.setattr(manager_module.cloud_keys, "fetch_local_keys", lambda ids: calls.append(ids) or {})
    worker._probe_and_recover()
    worker._probe_and_recover()
    assert len(calls) == 1


def test_manager_refresh_keys_reports_and_applies(monkeypatch):
    manager = LampManager(NetworkConfig())
    ok = SimpleNamespace(config=SimpleNamespace(name="OO", local_key="same"), reconfigure=lambda c: None)
    changed_calls = []
    changed = SimpleNamespace(config=SimpleNamespace(name="OV", local_key="old"), reconfigure=changed_calls.append)
    manager.devices = {"a": ok, "b": changed, "c": SimpleNamespace(config=SimpleNamespace(name="X", local_key="k"))}
    monkeypatch.setattr(manager_module.cloud_keys, "fetch_local_keys", lambda ids: {"a": "same", "b": "new"})
    report = manager.refresh_keys_from_cloud()
    assert report == {"OO": "unchanged", "OV": "updated", "X": "not found"}
    assert changed.config.local_key == "new" and changed_calls


def test_missing_credentials_is_a_readable_error(monkeypatch):
    monkeypatch.setattr(manager_module.cloud_keys, "load_credentials", lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        manager_module.cloud_keys.fetch_local_keys(["x"])
