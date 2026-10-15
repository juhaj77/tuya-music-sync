"""The pulse sequencer's color sweep: now and then a colour of its own runs
fast round the lamps in Chase order, on the beat grid.

- PulseSequencer: where and how often a sweep is announced, and that it
  leaves the strobe's stretch alone.
- VisualizationEngine: paints the sweep's colour on each Chase order
  position in turn, over the sweep's length on the clock, with its own
  rotators and direction - Chase itself can stay off.

No bulbs, sockets or audio: fake lamp manager, fake clock (see test_strobe).
"""
import random

from airam_lights.color.models import Color
from airam_lights.config.schema import PulseSequencerConfig
from airam_lights.effects.pulse_sequencer import PulseSequencer
from test_strobe import PERIOD, TICK, _make_engine, _run_sequencer, _seq_cfg

GREEN = Color.from_hsv(120.0, 1.0, 1.0)


def _sweep_cfg(**kw):
    base = dict(
        strobe_enabled=False, color_sweep_enabled=True, color_sweep_placement="bars", color_sweep_chance=1.0,
        color_sweep_min_gap_bars=1, color_sweep_min_level="calm", color_sweep_beats=1.0,
    )
    base.update(kw)
    return _seq_cfg(**base)


def _sweeps(events):
    return [e for e in events if e.kind == "sweep"]


# -- sequencer ---------------------------------------------------------------------------------


def test_color_sweep_is_off_by_default():
    assert PulseSequencerConfig().color_sweep_enabled is False
    events = _run_sequencer(PulseSequencer(_sweep_cfg(color_sweep_enabled=False), random.Random(1)), 9)
    assert not _sweeps(events)


def test_sweep_ends_on_the_bar_line():
    sweeps = _sweeps(_run_sequencer(PulseSequencer(_sweep_cfg(), random.Random(1)), 9))
    assert len(sweeps) >= 7
    for e in sweeps:
        assert e.length == 1.0
        assert e.bar_step == 12  # the last beat of a 4/4 bar


def test_two_beat_sweep_fills_the_second_half_of_the_bar():
    sweeps = _sweeps(_run_sequencer(PulseSequencer(_sweep_cfg(color_sweep_beats=2.0), random.Random(1)), 9))
    assert sweeps and all(e.bar_step == 8 and e.length == 2.0 for e in sweeps)


def test_sweep_placement_phrase_only_runs_into_a_new_phrase():
    sweeps = _sweeps(_run_sequencer(PulseSequencer(_sweep_cfg(color_sweep_placement="phrase"), random.Random(1)), 13))
    assert len(sweeps) == 3
    assert all(e.phrase_bar == 3 and e.bar_step == 12 for e in sweeps)  # phrase_bars 4: its last bar


def test_sweep_placement_beats_starts_on_beats_and_stays_in_the_bar():
    cfg = _sweep_cfg(color_sweep_placement="beats", color_sweep_min_gap_bars=0, color_sweep_beats=2.0)
    sweeps = _sweeps(_run_sequencer(PulseSequencer(cfg, random.Random(1)), 9))
    assert sweeps
    assert {e.bar_step for e in sweeps} <= {0, 4, 8}  # never 12: it would run past the bar line
    for a, b in zip(sweeps, sweeps[1:]):
        assert b.step - a.step >= 8  # the previous one is over first


def test_sweep_min_gap_level_and_chance():
    sweeps = _sweeps(_run_sequencer(PulseSequencer(_sweep_cfg(color_sweep_min_gap_bars=3), random.Random(1)), 13))
    assert all(b.step - a.step >= 3 * 16 for a, b in zip(sweeps, sweeps[1:]))
    quiet = PulseSequencer(_sweep_cfg(color_sweep_min_level="peak"), random.Random(1))
    assert not _sweeps(_run_sequencer(quiet, 9))  # a steady level never counts as the loudest
    never = PulseSequencer(_sweep_cfg(color_sweep_chance=0.0), random.Random(1))
    assert not _sweeps(_run_sequencer(never, 9))


def test_sweep_never_shares_the_strobes_stretch():
    cfg = _sweep_cfg(strobe_enabled=True, strobe_placement="bars", strobe_min_gap_bars=2, strobe_beats=1.0)
    events = _run_sequencer(PulseSequencer(cfg, random.Random(1)), 13)
    strobes = [e.step + 1 for e in events if e.kind == "strobe"]  # announced a 16th early
    sweeps = _sweeps(events)
    assert strobes and sweeps
    for start in strobes:
        assert not [e for e in sweeps if e.step < start + 4 and start < e.step + 4]


def test_color_sweep_settings_survive_a_save_and_load():
    cfg = PulseSequencerConfig(
        color_sweep_enabled=True, color_sweep_hue_deg=30.0, color_sweep_saturation=0.8,
        color_sweep_brightness=0.6, color_sweep_beats=2.0, color_sweep_rotators=2, color_sweep_width=3,
        color_sweep_direction="backward", color_sweep_placement="phrase", color_sweep_chance=0.7,
        color_sweep_min_gap_bars=3, color_sweep_min_level="high",
    )
    assert PulseSequencerConfig.from_dict(cfg.to_dict()) == cfg
    assert PulseSequencerConfig.from_dict({}).color_sweep_enabled is False


# -- engine ------------------------------------------------------------------------------------


def _make_sweep_engine(monkeypatch, **kw):
    settings = dict(
        strobe_enabled=False, color_sweep_enabled=True, color_sweep_hue_deg=120.0, color_sweep_placement="bars",
        color_sweep_chance=1.0, color_sweep_min_gap_bars=1, color_sweep_min_level="calm", color_sweep_beats=1.0,
    )
    settings.update(kw)
    engine, clock, lamps = _make_engine(monkeypatch, **settings)
    engine.config.color_mapping.beat_sync.white_pulse_enabled = False  # only the RGB colour to look at
    return engine, clock


def _record_sweeps(engine, clock, seconds):
    """Per tick: (clock position in beats, lamps showing the sweep colour)."""
    out = []
    for _ in range(int(seconds / TICK)):
        engine.tick_visual()
        lit = {lamp for lamp, color in engine.latest_lamp_colors.items() if color == GREEN}
        if engine._seq_position is not None:
            out.append((engine._seq_position, lit))
        clock.now += TICK
    return out


def _laps(ticks):
    """The lamps lit, in order of lighting, for each sweep (a run of ticks with any lit)."""
    laps, current = [], []
    for _, lit in ticks:
        if lit:
            for lamp in sorted(lit):
                if not current or current[-1] != lamp:
                    current.append(lamp)
        elif current:
            laps.append(current)
            current = []
    return laps


def test_engine_runs_the_sweep_round_the_chase_order_in_the_bars_last_beat(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch)
    assert engine.config.chase.enabled is False  # needs none of Chase's settings
    ticks = _record_sweeps(engine, clock, 20.0)
    laps = _laps(ticks)
    assert len(laps) >= 3
    for lap in laps:
        assert lap == ["a", "b", "c", "d"]
    for position, lit in ticks:
        if lit:
            assert 3.0 - 0.1 <= position % 4.0 < 4.0  # the bar's last beat
            assert len(lit) == 1


def test_engine_sweep_backward_with_two_rotators(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_rotators=2, color_sweep_direction="backward")
    ticks = _record_sweeps(engine, clock, 20.0)
    lit_sets = [lit for _, lit in ticks if lit]
    assert lit_sets
    # 4 lamps, 2 rotators from opposite sides: a+c, then d+b - every lamp once per sweep.
    seen = []
    for lit in lit_sets:
        if not seen or seen[-1] != lit:
            seen.append(lit)
    assert seen[:2] == [{"a", "c"}, {"b", "d"}]
    assert all(len(lit) == 2 for lit in lit_sets)


def _distinct(ticks):
    seen = []
    for _, lit in ticks:
        if lit and (not seen or seen[-1] != lit):
            seen.append(lit)
    return seen


def test_engine_sweep_width_lights_a_band_of_neighbours(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_width=2, color_sweep_beats=2.0)
    ticks = [t for t in _record_sweeps(engine, clock, 20.0) if t[0] >= 12.0]  # the clock has settled
    # The band slides in, moves round and slides out: every lamp lit for two of its five shares.
    assert _distinct(ticks)[:5] == [{"a"}, {"a", "b"}, {"b", "c"}, {"c", "d"}, {"d"}]
    for position, lit in ticks:
        if lit:
            assert 2.0 - 0.1 <= position % 4.0 < 4.0  # still within the sweep's 2 beats


def test_engine_sweep_width_with_two_rotators(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_width=2, color_sweep_rotators=2, color_sweep_beats=2.0)
    # 2 positions per rotator's stretch: the band is the whole stretch in the middle.
    ticks = [t for t in _record_sweeps(engine, clock, 20.0) if t[0] >= 12.0]
    assert _distinct(ticks)[:3] == [{"a", "c"}, {"a", "b", "c", "d"}, {"b", "d"}]


def test_engine_skips_no_lamp_on_a_fast_sweep(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_beats=0.5)
    # A 16th per lamp at this tempo is about one tick: none may be skipped.
    assert PERIOD / 8 < 2 * TICK
    laps = _laps(_record_sweeps(engine, clock, 20.0))
    assert laps and all(set(lap) == {"a", "b", "c", "d"} for lap in laps)


def test_engine_shows_no_sweep_when_off(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_enabled=False)
    assert not [lit for _, lit in _record_sweeps(engine, clock, 10.0) if lit]


def test_engine_sweep_colour_stays_exact_over_chase_and_group_switch(monkeypatch):
    engine, clock = _make_sweep_engine(monkeypatch, color_sweep_width=2)
    cfg = engine.config
    cfg.chase.enabled = True
    cfg.chase.color_mode = "hue_shift_relative"
    cfg.group_switch.enabled = True
    cfg.group_switch.color_mode = "hue_shift"
    ticks = [t for t in _record_sweeps(engine, clock, 20.0) if t[0] >= 12.0]
    laps = _laps(ticks)
    # Every lamp shows exactly the sweep colour in turn, with neither overlay shifting it.
    assert len(laps) >= 2 and all(set(lap) == {"a", "b", "c", "d"} for lap in laps)
    # ...and the overlays' own colours come back once it's past.
    assert any(not lit for _, lit in ticks)
