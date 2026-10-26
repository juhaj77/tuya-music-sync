"""The pulse sequencer's dark sweep - a dark spot running fast round the
lamps in Chase order, each lamp dipping toward black in turn - and the
saturation that follows a dip: a dark pulse's or the dark sweep's colour can
wash out as it dims and come back as it brightens (dark_pulse_desaturate,
dark_sweep_desaturate).

- PulseSequencer: places dark sweeps by their own settings, never at the
  same time as a color sweep (they share the info panel's Sweep lane).
- VisualizationEngine: dims each Chase order position in turn on the dark
  sweep's attack/release, its white flashes with it, and desaturates.
- The info panel's timeline: a black bar on the Sweep lane.

No bulbs, sockets or audio: fake lamp manager, fake clock (see test_strobe).
"""
import os
import random
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.color.models import Color  # noqa: E402
from airam_lights.config.schema import BeatSyncModeConfig, PulseSequencerConfig  # noqa: E402
from airam_lights.effects.pulse_sequencer import PulseSequencer  # noqa: E402
from airam_lights.engine.visualization_engine import _dip  # noqa: E402
from airam_lights.ui.widgets.info_panel import InfoPanel  # noqa: E402
from test_strobe import TICK, _make_engine, _run_sequencer, _seq_cfg  # noqa: E402


def _dark_cfg(**kw):
    base = dict(
        strobe_enabled=False, dark_sweep_enabled=True, dark_sweep_placement="bars", dark_sweep_chance=1.0,
        dark_sweep_min_gap_bars=1, dark_sweep_min_level="calm", dark_sweep_beats=1.0,
    )
    base.update(kw)
    return _seq_cfg(**base)


def _kind(events, kind):
    return [e for e in events if e.kind == kind]


# -- settings ------------------------------------------------------------------------------------


def test_dark_sweep_and_desaturate_are_off_by_default_and_survive_a_save_and_load():
    assert PulseSequencerConfig().dark_sweep_enabled is False
    assert PulseSequencerConfig().dark_sweep_desaturate == 0.0
    assert BeatSyncModeConfig().dark_pulse_desaturate == 0.0
    cfg = PulseSequencerConfig(
        dark_sweep_enabled=True, dark_sweep_depth=0.7, dark_sweep_desaturate=1.0, dark_sweep_attack_ms=40.0,
        dark_sweep_release_ms=500.0, dark_sweep_beats=2.0, dark_sweep_rotators=2, dark_sweep_width=3,
        dark_sweep_direction="backward", dark_sweep_placement="phrase", dark_sweep_chance=0.7,
        dark_sweep_min_gap_bars=3, dark_sweep_min_level="high",
    )
    assert PulseSequencerConfig.from_dict(cfg.to_dict()) == cfg
    assert BeatSyncModeConfig.from_dict({"dark_pulse_desaturate": 0.6}).dark_pulse_desaturate == 0.6


# -- sequencer -----------------------------------------------------------------------------------


def test_dark_sweep_is_placed_by_its_own_settings():
    assert not _kind(_run_sequencer(PulseSequencer(_dark_cfg(dark_sweep_enabled=False), random.Random(1)), 9),
                     "dark_sweep")
    sweeps = _kind(_run_sequencer(PulseSequencer(_dark_cfg(), random.Random(1)), 9), "dark_sweep")
    assert len(sweeps) >= 7
    assert all(e.length == 1.0 and e.bar_step == 12 for e in sweeps)  # ends on the bar line
    two = _kind(_run_sequencer(PulseSequencer(_dark_cfg(dark_sweep_beats=2.0), random.Random(1)), 9), "dark_sweep")
    assert two and all(e.bar_step == 8 and e.length == 2.0 for e in two)
    phrase = PulseSequencer(_dark_cfg(dark_sweep_placement="phrase"), random.Random(1))
    assert all(e.phrase_bar == 3 for e in _kind(_run_sequencer(phrase, 13), "dark_sweep"))
    gap = PulseSequencer(_dark_cfg(dark_sweep_min_gap_bars=3), random.Random(1))
    starts = [e.step for e in _kind(_run_sequencer(gap, 13), "dark_sweep")]
    assert all(b - a >= 3 * 16 for a, b in zip(starts, starts[1:]))
    # In Beat Sync White's dark-only sequencing too (it dims the white there) - but no color sweep.
    seq = PulseSequencer(_dark_cfg(
        color_sweep_enabled=True, color_sweep_placement="beats", color_sweep_chance=1.0, color_sweep_min_gap_bars=1,
        color_sweep_min_level="calm",
    ), random.Random(1))
    events, pos = [], 0.1
    while pos < 36.0:
        events += seq.tick(pos, 0.5, pos, 0.03, 3, 4, dark_only=True)
        pos += 0.125
    assert len(_kind(events, "dark_sweep")) >= 7 and not _kind(events, "sweep")


def test_color_and_dark_sweeps_never_run_at_once():
    cfg = _dark_cfg(
        color_sweep_enabled=True, color_sweep_placement="beats", color_sweep_chance=1.0, color_sweep_min_gap_bars=1,
        color_sweep_min_level="calm", color_sweep_beats=2.0,
    )
    events = _run_sequencer(PulseSequencer(cfg, random.Random(1)), 13)
    spans = [(e.step, e.step + e.length * 4, e.kind) for e in events if e.kind in ("sweep", "dark_sweep")]
    assert {k for _, _, k in spans} == {"sweep", "dark_sweep"}
    spans.sort()
    for (a0, a1, _), (b0, b1, _) in zip(spans, spans[1:]):
        assert a1 <= b0


# -- engine --------------------------------------------------------------------------------------


def _make_dark_engine(monkeypatch, **kw):
    settings = dict(
        strobe_enabled=False, dark_sweep_enabled=True, dark_sweep_placement="bars", dark_sweep_chance=1.0,
        dark_sweep_min_gap_bars=1, dark_sweep_min_level="calm", dark_sweep_beats=1.0,
        dark_sweep_attack_ms=1.0, dark_sweep_release_ms=10.0,  # next to instant: easy to see where it is
    )
    transitions = kw.pop("transitions", "direct")
    settings.update(kw)
    engine, clock, _ = _make_engine(monkeypatch, transitions=transitions, **settings)
    bs = engine.config.color_mapping.beat_sync
    bs.white_pulse_enabled = False  # only the RGB colour to look at
    bs.fade_brightness = False  # the colour at full brightness all the time: a dim lamp is the sweep
    return engine, clock


def _value(color):
    return color.to_hsv()[2]


def _record(engine, clock, seconds):
    """Per tick: (clock position in beats, the lamps the sweep has dimmed below half)."""
    out = []
    for _ in range(int(seconds / TICK)):
        engine.tick_visual()
        dark = {lamp for lamp, color in engine.latest_lamp_colors.items() if _value(color) < 0.5}
        if engine._seq_position is not None:
            out.append((engine._seq_position, dark))
        clock.now += TICK
    return out


def _laps(ticks):
    laps, current = [], []
    for _, dark in ticks:
        if dark:
            for lamp in sorted(dark):
                if not current or current[-1] != lamp:
                    current.append(lamp)
        elif current:
            laps.append(current)
            current = []
    return laps


def test_engine_runs_the_dark_spot_round_the_chase_order_in_the_bars_last_beat(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch)
    ticks = _record(engine, clock, 20.0)
    laps = _laps(ticks)
    assert len(laps) >= 3 and all(lap == ["a", "b", "c", "d"] for lap in laps)
    for position, dark in ticks:
        if dark:
            assert 3.0 - 0.1 <= position % 4.0 < 4.0
            assert len(dark) == 1
    assert any(not dark for _, dark in ticks)  # the show is back in between


def test_engine_dark_sweep_backward_with_two_rotators(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch, dark_sweep_rotators=2, dark_sweep_direction="backward")
    seen = []
    for _, dark in _record(engine, clock, 20.0):
        if dark and (not seen or seen[-1] != dark):
            seen.append(dark)
    assert seen[:2] == [{"a", "c"}, {"b", "d"}]


def test_engine_leaves_a_tail_and_every_lamp_comes_back(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch, dark_sweep_release_ms=300.0, dark_sweep_min_gap_bars=16)
    tail = False
    for _ in range(int(12.0 / TICK)):
        engine.tick_visual()
        colors = engine.latest_lamp_colors
        if _value(colors["d"]) < 0.1:  # the spot on the last lamp...
            tail = tail or 0.1 < _value(colors["a"]) < 0.95  # ...with the first still on its way back
        clock.now += TICK
    assert tail
    # Long after it: every lamp back at full and nothing left over.
    assert all(_value(c) > 0.99 for c in engine.latest_lamp_colors.values())
    assert engine._dark_sweep is None and not engine._dark_sweep_amounts


def test_engine_dark_sweep_depth_and_desaturate(monkeypatch):
    for desaturate in (0.0, 1.0):
        engine, clock = _make_dark_engine(monkeypatch, dark_sweep_depth=0.5, dark_sweep_desaturate=desaturate)
        dimmed = []
        for _ in range(int(12.0 / TICK)):
            engine.tick_visual()
            amounts = engine._dark_sweep_amounts
            for lamp, smoother in amounts.items():
                if smoother.value > 0.99:
                    dimmed.append(engine.latest_lamp_colors[lamp].to_hsv())
            clock.now += TICK
        assert dimmed
        for _, saturation, value in dimmed:
            assert value == pytest.approx(0.5, abs=0.01)  # depth 0.5: half the light
            assert saturation == pytest.approx(1.0 - desaturate, abs=0.02)


def test_a_white_flash_dims_with_the_dark_spot(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch)
    engine.config.color_mapping.beat_sync.white_pulse_enabled = True
    sq = engine.config.sequencer
    sq.white_pattern, sq.group_walk, sq.min_group_gap_ms = "beats", "all", 0.0
    sq.white_accent_focus = sq.white_build = 0.0  # a flash on every beat, the bar's last one too
    checked = 0
    for _ in range(int(16.0 / TICK)):
        engine.tick_visual()
        targets = engine.latest_lamp_white_targets
        dark = {lamp for lamp, s in engine._dark_sweep_amounts.items() if s.value > 0.95}
        lit = [t.brightness for lamp, t in targets.items() if lamp not in dark and lamp != "d"]
        for lamp in dark & set(targets) - {"d"}:  # (d has a white multiplier of its own)
            if lit and max(lit) > 0.05:
                assert targets[lamp].brightness < 0.1 * max(lit)
                checked += 1
        clock.now += TICK
    assert checked


def test_test_button_plays_four_dark_sweeps_even_without_the_sequencer(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch, dark_sweep_enabled=False)
    engine.config.sequencer.enabled = False
    for _ in range(int(1.0 / TICK)):
        engine.tick_visual()
        clock.now += TICK
    message = engine.trigger_dark_sweep_test()
    assert message.startswith("Playing the dark sweep 4 times in a row: 1 beats")
    assert "1 rotator(s), width 1, forward, depth 1" in message
    order = []
    for _ in range(int(3.0 / TICK)):
        engine.tick_visual()
        for lamp in sorted(lamp for lamp, c in engine.latest_lamp_colors.items() if _value(c) < 0.5):
            if not order or order[-1] != lamp:
                order.append(lamp)
        clock.now += TICK
    assert order == ["a", "b", "c", "d"] * 4
    assert engine._dark_sweep is None
    engine.running = False
    assert engine.trigger_dark_sweep_test().startswith("Not played: start the visualizer")


def test_dark_sweep_dims_beat_sync_whites_white(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch)
    engine.config.color_mapping.mode = "beat_sync_white"
    bsw = engine.config.color_mapping.beat_sync_white
    bsw.flash_brightness = bsw.sustain_brightness = 0.8  # steady white: a dim lamp is the sweep
    order, checked = [], 0
    for _ in range(int(20.0 / TICK)):
        engine.tick_visual()
        targets = engine.latest_lamp_white_targets
        assert set(targets) == {"a", "b", "c", "d"} and not engine.latest_lamp_white_targets["a"].under
        dark = sorted(lamp for lamp, target in targets.items() if target.brightness < 0.4)
        for lamp in dark:
            if not order or order[-1] != lamp:
                order.append(lamp)
            checked += 1
            assert engine.latest_lamp_colors[lamp].to_hsv()[2] < 0.4  # the preview swatch too
        clock.now += TICK
    assert checked and order[:8] == ["a", "b", "c", "d"] * 2


def test_test_buttons_in_beat_sync_white(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch, dark_sweep_enabled=False)
    engine.config.color_mapping.mode = "beat_sync_white"
    engine.config.color_mapping.beat_sync_white.sustain_brightness = 0.8
    for _ in range(int(1.0 / TICK)):
        engine.tick_visual()
        clock.now += TICK
    assert engine.trigger_color_sweep_test().startswith("Not played: Beat Sync White")
    assert engine.trigger_dark_sweep_test().startswith("Playing the dark sweep 4 times")
    dimmed = set()
    for _ in range(int(3.0 / TICK)):
        engine.tick_visual()
        dimmed |= {lamp for lamp, t in engine.latest_lamp_white_targets.items() if t.brightness < 0.4}
        clock.now += TICK
    assert dimmed == {"a", "b", "c", "d"}


# -- the dip's saturation ------------------------------------------------------------------------


def test_dip_washes_the_colour_out_with_the_light():
    red = Color(1.0, 0.0, 0.0)
    assert _dip(red, 0.5, 0.0) == Color(0.5, 0.0, 0.0)
    hue, saturation, value = _dip(red, 0.5, 0.5).to_hsv()
    assert hue == pytest.approx(0.0) and saturation == pytest.approx(0.5) and value == pytest.approx(0.5)
    assert _dip(red, 0.0, 1.0).to_hsv()[1:] == (0.0, 0.0)


def test_sequencer_dark_pulse_desaturates_in_the_other_modes(monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch)
    bs = engine.config.color_mapping.beat_sync
    bs.dark_pulse_depth, bs.dark_pulse_desaturate = 0.5, 1.0
    engine._beat_dark_until = clock.now + 1.0
    colors = {"a": Color(1.0, 0.0, 0.0)}
    engine._apply_pulses(colors, 0.1, clock.now)
    _, saturation, value = colors["a"].to_hsv()
    assert value == pytest.approx(0.5, abs=0.01) and saturation < 0.01


def test_beat_sync_dark_pulse_desaturates(monkeypatch):
    for desaturate in (0.0, 1.0):
        engine, clock = _make_dark_engine(monkeypatch, dark_sweep_enabled=False)
        engine.config.sequencer.enabled = False  # Beat Sync's own per-beat dark pulses
        bs = engine.config.color_mapping.beat_sync
        bs.dark_pulse_enabled, bs.dark_pulse_probability, bs.dark_pulse_duration_ms = True, 1.0, 200.0
        bs.dark_pulse_depth, bs.dark_pulse_desaturate = 0.5, desaturate
        saturations = []
        for _ in range(int(4.0 / TICK)):
            engine.tick_visual()
            saturations.append(engine.latest_band3_levels["saturation"])
            clock.now += TICK
        assert min(saturations) == pytest.approx(1.0 - desaturate, abs=0.05)
        assert max(saturations) == pytest.approx(1.0)


# -- info panel ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_info_panel_draws_the_dark_sweep_as_a_black_bar_on_the_sweep_lane(qapp, monkeypatch):
    engine, clock = _make_dark_engine(monkeypatch)
    for _ in range(int(10.0 / TICK)):
        engine.tick_visual()
        clock.now += TICK
    assert {e["kind"] for e in engine.seq_events} == {"dark_sweep"}
    panel = InfoPanel(SimpleNamespace(engine=engine, config=engine.config))
    panel._timer.stop()
    panel.resize(1200, 140)
    panel._ticks = 0
    panel._refresh()
    assert "last dark sweep: 1 beats (bars)" in panel.sequencer_label.text()
    timeline = panel.timeline
    image = timeline.grab().toImage()
    lane_h = (image.height() - 4) / 4
    black = {
        y for y in range(image.height()) for x in range(0, image.width(), 2)
        if QColor(image.pixel(x, y)) == QColor(0, 0, 0)
    }
    assert black and all(2 + 3 * lane_h <= y < 2 + 4 * lane_h for y in black)  # the bottom lane: Sweep
