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


def test_flash_strobe_shows_on_the_strobe_lane_and_the_bars_strobe_cuts_it(monkeypatch):
    from test_strobe import _make_flash_engine

    engine, clock, lamps = _make_flash_engine(monkeypatch)
    _run(engine, clock, 20.0)
    flash = [e for e in engine.seq_events if e["kind"] == "flash_strobe"]
    assert flash
    for e in flash:
        assert len(e["levels"]) >= 2 and e["flash_period"] > 0.0
        assert e["length"] == pytest.approx(len(e["levels"]) * e["flash_period"])
        # Its flashes follow the white flash's envelope: up over the attack, down over the release.
        assert max(e["levels"]) == pytest.approx(1.0) and e["levels"][-1] < 1.0
    # A strobe starting in the middle of one cuts it there.
    flash[-1]["length"] = 2.0
    engine._cut_flash_strobes_at(flash[-1]["pos"] + 0.5)
    assert flash[-1]["length"] == pytest.approx(0.5)
    engine._cut_flash_strobes_at(flash[-1]["pos"] + 3.0)  # one after it ended: untouched
    assert flash[-1]["length"] == pytest.approx(0.5)


def test_timeline_paints_flash_strobes_under_the_strobe(qapp, monkeypatch):
    from test_strobe import _make_flash_engine

    engine, clock, lamps = _make_flash_engine(monkeypatch)
    _run(engine, clock, 10.0)
    engine.running = True
    pos = engine.seq_position
    engine.seq_events.append({"pos": pos - 1.0, "kind": "strobe", "group": None, "length": 1.0,
                              "reason": "bar", "brightness": 1.0})
    panel = InfoPanel(SimpleNamespace(engine=engine, config=engine.config))
    panel._timer.stop()
    panel.resize(1200, 140)
    panel._ticks = 0
    panel._refresh()
    assert not panel.timeline.grab().isNull()  # paints every kind without errors


# -- warm/cool and hybrid white flashes ------------------------------------------------------------


def test_hybrid_flash_glides_its_temperature_over_the_whole_flash(monkeypatch):
    from test_strobe import _make_flash_engine

    engine, clock, lamps = _make_flash_engine(monkeypatch)
    sq, bs = engine.config.sequencer, engine.config.color_mapping.beat_sync
    sq.flash_strobe_enabled = False
    bs.white_pulse_hybrid_chance = 1.0
    temps = {}  # lamp -> temperatures seen through its current flash
    for _ in range(int(10.0 / TICK)):
        engine.tick_visual()
        for lamp, target in engine.latest_lamp_white_targets.items():
            temps.setdefault(lamp, []).append(target.temp)
        clock.now += TICK
    whites = [e for e in engine.seq_events if e["kind"] == "white"]
    assert whites and all(e["temp_end"] is not None for e in whites)
    assert all({e["temp"], e["temp_end"]} == {0.0, 1.0} for e in whites)  # from one end to the other
    # On the lamps the temperature moves through the in-between values.
    seen = [t for ts_ in temps.values() for t in ts_]
    assert any(0.2 < t < 0.8 for t in seen)


def test_plain_flashes_keep_one_temperature_and_log_it(monkeypatch):
    from test_strobe import _make_flash_engine

    engine, clock, lamps = _make_flash_engine(monkeypatch)
    engine.config.sequencer.flash_strobe_enabled = False
    engine.config.color_mapping.beat_sync.white_pulse_hybrid_chance = 0.0
    engine.config.color_mapping.beat_sync.white_pulse_temp_mode = "alternate"
    _run(engine, clock, 10.0)
    whites = [e for e in engine.seq_events if e["kind"] == "white"]
    assert whites and all(e["temp_end"] is None for e in whites)
    assert {e["temp"] for e in whites} == {0.0, 1.0}  # warm and cool both shown


def test_hybrid_per_beat_flash_without_the_sequencer(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, strobe_enabled=False)
    engine.config.sequencer.enabled = False
    bs = engine.config.color_mapping.beat_sync
    bs.white_pulse_probability, bs.white_pulse_trigger = 1.0, "random"
    bs.white_pulse_duration_ms, bs.white_pulse_release_ms = 200.0, 200.0
    bs.white_pulse_hybrid_chance = 1.0
    seen = []
    for _ in range(int(8.0 / TICK)):
        engine.tick_visual()
        seen.extend(t.temp for t in engine.latest_lamp_white_targets.values())
        clock.now += TICK
    assert seen and any(0.2 < t < 0.8 for t in seen)  # gliding, not just the two ends


def test_hybrid_temperature_helpers():
    from airam_lights.engine.visualization_engine import VisualizationEngine

    cfg = SimpleNamespace(white_pulse_hybrid_chance=1.0)
    assert VisualizationEngine._hybrid_temps(cfg, 0.8) == (1.0, 0.0)  # came out cool: cool -> warm
    assert VisualizationEngine._hybrid_temps(cfg, 0.2) == (0.0, 1.0)  # warm -> cool
    assert VisualizationEngine._hybrid_temps(SimpleNamespace(white_pulse_hybrid_chance=0.0), 0.8) == (0.8, None)
    glide = VisualizationEngine._glide_temp
    shape = (0.1, 0.2, 0.2, "linear")  # light: 0.05 + 0.2 + 0.1 = 0.35 -> half of it out at 0.225 s
    assert glide(1.0, 0.0, 0.0, shape) == 1.0
    assert glide(1.0, 0.0, 0.225, shape) == pytest.approx(0.5)
    assert glide(1.0, 0.0, 9.0, shape) == 0.0 and glide(0.7, None, 0.2, shape) == 0.7


def test_light_share_is_the_exact_area_under_the_envelope():
    import numpy as np

    from airam_lights.effects.pulse_sequencer import light_share, pulse_envelope

    for curve in ("linear", "ease_in", "ease_out", "ease_in_out"):
        a, h, r = 0.06, 0.1, 0.5
        ts_ = np.linspace(0.0, a + h + r, 20001)
        levels = np.array([0.0 if t < 1e-12 else (t / a if t < a else pulse_envelope(t, a, h, r, curve)) for t in ts_])
        areas = np.concatenate(([0.0], np.cumsum((levels[1:] + levels[:-1]) / 2.0 * np.diff(ts_))))
        for i in range(0, len(ts_), 2000):
            assert light_share(ts_[i], a, h, r, curve) == pytest.approx(areas[i] / areas[-1], abs=1e-3), (curve, ts_[i])
    # Most of a long-tailed flash's change happens while it's bright: half the
    # light is out well before half the time with ease_out.
    t_half = next(t for t in np.linspace(0, 0.66, 6601) if light_share(t, 0.06, 0.1, 0.5, "ease_out") >= 0.5)
    assert t_half < 0.66 / 3
