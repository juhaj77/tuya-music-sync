"""The info panel along the bottom of the window, and the lamp list's live
colours and effect columns on the Devices & Setup tab.

- VisualizationEngine.seq_events: the pulses the sequencer really played,
  on the clock's grid, moved along when the grid jumps.
- LampWorker.shown_color: what the lamp was last sent, white as a preview.
- InfoPanel / LampTile: render and report changes (offscreen Qt).

No bulbs, sockets or audio: test_strobe's fake engine, devices and clock.
"""
import os
import random
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.color.models import Color, StrobeBurst, WhiteTarget  # noqa: E402
from airam_lights.config.schema import PerLampEffect  # noqa: E402
from airam_lights.ui.widgets.info_panel import InfoPanel  # noqa: E402
from airam_lights.ui.widgets.lamp_tile import LampTile  # noqa: E402
from test_strobe import TICK, _FakeDevice, _make_engine, _start_worker, _stop  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _run(engine, clock, seconds):
    for _ in range(int(seconds / TICK)):
        engine.tick_visual()
        clock.now += TICK


def _busy_engine(monkeypatch):
    random.seed(3)
    engine, clock, lamps = _make_engine(
        monkeypatch, white_pattern="beats", strobe_placement="bars", strobe_min_gap_bars=2,
        color_sweep_enabled=True, color_sweep_chance=1.0, color_sweep_min_gap_bars=1, color_sweep_min_level="calm",
    )
    engine.config.sequencer.dark_pattern = "before_backbeats"
    engine.config.sequencer.dark_density = 1.0
    engine.config.color_mapping.beat_sync.dark_pulse_enabled = True
    return engine, clock


# -- the engine's log of played pulses -----------------------------------------------------------


def test_engine_logs_every_kind_of_pulse_it_plays_on_the_grid(monkeypatch):
    engine, clock = _busy_engine(monkeypatch)
    _run(engine, clock, 12.0)
    kinds = {e["kind"] for e in engine.seq_events}
    assert kinds == {"white", "dark", "strobe", "sweep"}
    now = engine.seq_position
    assert now is not None
    for e in engine.seq_events:
        assert e["pos"] <= now + 0.5  # nothing logged ahead of the clock (beyond a strobe's announcement)


def test_logged_strobes_and_sweeps_never_overlap(monkeypatch):
    engine, clock = _busy_engine(monkeypatch)
    _run(engine, clock, 20.0)
    strobes = [(e["pos"], e["pos"] + e["length"]) for e in engine.seq_events if e["kind"] == "strobe"]
    sweeps = [(e["pos"], e["pos"] + e["length"]) for e in engine.seq_events if e["kind"] == "sweep"]
    assert strobes and sweeps
    for a0, a1 in strobes:
        for b0, b1 in sweeps:
            assert a1 <= b0 + 1e-6 or b1 <= a0 + 1e-6


def test_logged_pulses_move_along_when_the_grid_jumps(monkeypatch):
    engine, clock = _busy_engine(monkeypatch)
    _run(engine, clock, 8.0)
    entry = engine.seq_events[-1]
    before = entry["pos"]
    last = engine.seq_position
    engine._seq_position = last + 2.0  # as if the bar start had been re-estimated 2 beats earlier
    _run(engine, clock, TICK)
    assert entry["pos"] == pytest.approx(before - 2.0, abs=0.1)


def test_no_sequencer_status_outside_beat_sync(monkeypatch):
    engine, clock = _busy_engine(monkeypatch)
    _run(engine, clock, 4.0)
    assert engine.sequencer_status is not None
    engine.config.color_mapping.mode = "rgb_freq"
    assert engine.sequencer_status is None and engine.seq_position is None


# -- what the lamps show ---------------------------------------------------------------------------


def test_worker_remembers_what_the_lamp_was_last_sent():
    device = _FakeDevice()
    worker = _start_worker(device)
    try:
        assert worker.shown_color is None
        red = Color(1.0, 0.0, 0.0)
        worker.set_target(red)
        deadline = time.perf_counter() + 2.0
        while worker.shown_color != red and time.perf_counter() < deadline:
            time.sleep(0.01)
        assert worker.shown_color == red
        white = WhiteTarget(0.8, 1.0, under=Color(0.0, 0.0, 0.2))
        worker.set_white_target(white)
        deadline = time.perf_counter() + 2.0
        while worker.shown_color != white.to_preview_color() and time.perf_counter() < deadline:
            time.sleep(0.01)
        assert worker.shown_color == white.to_preview_color()
        # A strobe flash shows as its white over the colour, then the colour again.
        burst = StrobeBurst(time.perf_counter(), 0.1, 0.05, 1, 0.6)
        worker.set_target(red)
        worker._send_strobe_edge(burst, True, 0)
        assert worker.shown_color == WhiteTarget(0.6, burst.temp, red).to_preview_color()
        worker._send_strobe_edge(burst, False, 0)
        assert worker.shown_color == red
    finally:
        _stop(worker)


# -- widgets -----------------------------------------------------------------------------------------


def test_lamp_tile_shows_and_reports_the_effect_columns(qapp):
    tile = LampTile("dev", "Lamp")
    changes = []
    tile.effectChanged.connect(lambda *args: changes.append(args))
    tile.set_effect(PerLampEffect("dev", chase_order=3, chase_dwell_mult=2.0, effect_group=None,
                                  white_pulse_brightness_mult=0.5))
    assert changes == []  # showing the config isn't a change
    assert tile.chase_order_spin.value() == 3 and tile.effect_group_spin.text() == "Off"
    assert tile.white_mult_spin.value() == pytest.approx(0.5)
    tile.effect_group_spin.setValue(1)
    tile.chase_order_spin.setValue(-1)
    assert changes == [("dev", "effect_group", 1), ("dev", "chase_order", None)]


def test_info_panel_renders_the_clock_and_the_sequencer(qapp, monkeypatch):
    engine, clock = _busy_engine(monkeypatch)
    _run(engine, clock, 10.0)
    engine.running = True
    panel = InfoPanel(SimpleNamespace(engine=engine, config=engine.config))
    panel._timer.stop()
    panel.resize(1200, 140)
    panel._ticks = 0
    panel._refresh()
    assert "BPM" in panel.clock_label.text() and "phrase bar" in panel.clock_label.text()
    assert not panel.sequencer_box.isHidden()
    assert "Sequencer: white" in panel.sequencer_label.text()
    assert not panel.grab().isNull()  # paints without errors
    engine.config.sequencer.enabled = False
    engine._sequencing = False
    panel._ticks = 0
    panel._refresh()
    assert panel.sequencer_box.isHidden()
