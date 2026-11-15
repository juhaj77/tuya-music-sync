"""The Real Time layer: what the operator plays by hand over the show.

- RealtimeLayer (effects/realtime.py): the pointer tools (brush, curtain,
  ripple, spot), the trail, held paint, the thrown brush, the one-shots and
  the holds, and the looks they paint with.
- VisualizationEngine: lays it over the finished show as the last step,
  fits it to the white LEDs, leaves the show untouched while nothing is
  played, plays it alone while the show is stopped, and the Strobe pad.
- LampWorker: an empty burst stops a strobe that's playing.

No bulbs, sockets or audio: fake lamp manager, fake clock (see test_strobe).
"""
import random
import time

import pytest

import airam_lights.effects.realtime as rt
from airam_lights.color.models import Color, StrobeBurst
from airam_lights.config.schema import AppConfig, RealtimeConfig
from airam_lights.effects.realtime import (
    HOLDS,
    LOOKS,
    ONE_SHOTS,
    PALETTES,
    RealtimeLayer,
    Style,
    gradient_at,
    hex_to_rgb,
    layout,
    palette_at,
    rgb_to_hex,
    strobe_per_beat,
)
from test_strobe import DEVICES, PERIOD, TICK, _FakeDevice, _make_engine, _run_engine, _start_worker

BLUE = Color(0.0, 0.0, 0.6)
RED = Color(1.0, 0.0, 0.0)
GREEN = Color(0.0, 1.0, 0.0)
PINK = hex_to_rgb("#ff2d95")  # the default palette's first colour
LAMPS = "abcd"


class _Clock:
    now = 100.0


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(rt.time, "perf_counter", lambda: c.now)
    return c


def _layer(**settings) -> RealtimeLayer:
    """Four lamps in a row across a stage twice as wide as it's high: 0.54 stage
    heights apart, so the default brush (radius 0.28) reaches one at a time."""
    base = dict(speed=0.0, music=0.0, throw=False)
    base.update(settings)
    config = RealtimeConfig(**base)
    config.positions = {"a": [0.1, 0.5], "b": [0.37, 0.5], "c": [0.64, 0.5], "d": [0.91, 0.5]}
    layer = RealtimeLayer(config, random.Random(1))
    layer.aspect = 2.0
    return layer


def _tick(layer, clock, base=BLUE, dt=1.0 / 30.0, **kw):
    clock.now += dt
    colors = {lamp: base for lamp in LAMPS}
    frame = layer.apply(colors, clock.now, dt, **kw)
    return colors, frame


def _run(layer, clock, seconds, **kw):
    out = None
    for _ in range(int(round(seconds * 30))):
        out = _tick(layer, clock, **kw)
    return out


def _rgb(color: Color):
    return (color.r, color.g, color.b)


# -- nothing played ---------------------------------------------------------------------------


def test_an_idle_layer_leaves_the_show_alone(clock):
    layer = _layer()
    assert not layer.active
    colors, frame = _tick(layer, clock)
    assert all(colors[lamp] is BLUE for lamp in LAMPS)
    assert frame.coverage == {} and frame.whites == {}


# -- the brush ---------------------------------------------------------------------------------


def test_brush_paints_the_lamp_under_it(clock):
    layer = _layer()
    layer.pointer_down("brush", 0.1, 0.5)
    assert layer.active
    colors, frame = _tick(layer, clock)
    assert _rgb(colors["a"]) == pytest.approx(PINK)  # the gradient's middle: the first colour
    assert frame.coverage == {"a": pytest.approx(1.0)}
    assert all(colors[lamp] is BLUE for lamp in "bcd")


def test_brush_fades_toward_its_rim(clock):
    layer = _layer(softness=1.0, radius=0.6)
    layer.pointer_down("brush", 0.1, 0.5)  # b is 0.54 away: near the rim
    _, frame = _tick(layer, clock)
    assert frame.coverage["a"] == pytest.approx(1.0)
    assert 0.0 < frame.coverage["b"] < 0.2
    hard = _layer(softness=0.0, radius=0.6)
    hard.pointer_down("brush", 0.1, 0.5)
    _, frame = _tick(hard, clock)
    assert frame.coverage["b"] == pytest.approx(1.0)  # a hard edge: in full up to the rim


def test_a_fast_swipe_between_two_ticks_catches_every_lamp_it_crossed(clock):
    layer = _layer()
    layer.pointer_down("brush", 0.0, 0.5)
    layer.pointer_move(1.0, 0.5)  # right across, before the engine ticks again
    _, frame = _tick(layer, clock)
    assert set(frame.coverage) == set(LAMPS)
    assert all(level == pytest.approx(1.0) for level in frame.coverage.values())


def test_the_trail_fades_over_the_trail_time(clock):
    layer = _layer(trail_s=0.3)
    layer.pointer_down("brush", 0.1, 0.5)
    _tick(layer, clock)
    layer.pointer_up()
    _, frame = _run(layer, clock, 0.15)
    assert frame.coverage["a"] == pytest.approx(0.5, abs=0.12)
    colors, frame = _run(layer, clock, 0.3)
    assert frame.coverage == {} and colors["a"] is BLUE
    assert not layer.active  # nothing left: the show goes out untouched again


def test_hold_paint_stays_with_the_look_it_was_made_with(clock):
    layer = _layer(latch=True)
    layer.pointer_down("brush", 0.1, 0.5)
    _tick(layer, clock)
    layer.pointer_up()
    layer.config.look = "blackout"  # the next stroke's look
    layer.pointer_down("brush", 0.91, 0.5)
    _tick(layer, clock)
    layer.pointer_up()
    colors, frame = _run(layer, clock, 3.0)
    assert _rgb(colors["a"]) == pytest.approx(PINK)  # still the palette it was painted with
    assert _rgb(colors["d"]) == (0.0, 0.0, 0.0)
    assert layer.ink_levels()["a"] == (0.0, pytest.approx(1.0))
    layer.clear()
    colors, _ = _run(layer, clock, 0.5)
    assert colors["a"] is BLUE and not layer.active


def test_ctrl_drag_erases_held_paint(clock):
    layer = _layer(latch=True)
    layer.pointer_down("brush", 0.0, 0.5)
    layer.pointer_move(1.0, 0.5)
    _tick(layer, clock)
    layer.pointer_up()
    layer.pointer_down("brush", 0.37, 0.5, erase=True)
    _, frame = _tick(layer, clock)
    layer.pointer_up()
    assert "b" not in frame.coverage and set(frame.coverage) == {"a", "c", "d"}


def test_a_thrown_brush_glides_on_bounces_and_stops(clock):
    layer = _layer(throw=True, trail_s=0.2)
    layer.pointer_down("brush", 0.02, 0.5)
    for x in (0.08, 0.14):
        clock.now += 0.02
        layer.pointer_move(x, 0.5)
    layer.pointer_up()  # let go at 3 stage widths a second
    assert len(layer.glider_points()) == 1
    painted, xs = set(), []
    for _ in range(45):
        _, frame = _tick(layer, clock)
        painted |= set(frame.coverage)
        xs += [x for x, _ in layer.glider_points()]
    assert painted == set(LAMPS)  # it crossed the stage by itself
    assert max(xs) > 0.9 and xs[-1] < max(xs)  # ...and came back off the far wall
    _run(layer, clock, 8.0)
    assert layer.glider_points() == [] and not layer.active


def test_a_slow_release_throws_nothing(clock):
    layer = _layer(throw=True)
    layer.pointer_down("brush", 0.1, 0.5)
    clock.now += 0.1
    layer.pointer_move(0.11, 0.5)
    layer.pointer_up()
    assert layer.glider_points() == []


# -- the curtain, the spot, the ripple -----------------------------------------------------------


def test_curtain_covers_what_is_behind_its_edge(clock):
    layer = _layer(softness=0.0)
    layer.pointer_down("curtain", 0.0, 0.5)
    layer.pointer_move(0.5, 0.5)  # pulled from the left edge to the middle
    colors, frame = _tick(layer, clock)
    assert set(frame.coverage) == {"a", "b"}
    assert _rgb(colors["b"]) != _rgb(colors["a"])  # the gradient runs from the edge back to the start
    layer.pointer_move(0.2, 0.5)  # pulled back: it opens again
    _run(layer, clock, 1.0)
    _, frame = _tick(layer, clock)
    assert set(frame.coverage) == {"a"}


def test_curtain_works_in_any_direction(clock):
    layer = _layer(softness=0.0)
    layer.pointer_down("curtain", 1.0, 0.5)
    layer.pointer_move(0.5, 0.5)  # from the right this time
    _, frame = _tick(layer, clock)
    assert set(frame.coverage) == {"c", "d"}


def test_curtain_stays_with_hold_paint(clock):
    layer = _layer(softness=0.0, latch=True)
    layer.pointer_down("curtain", 0.0, 0.5)
    layer.pointer_move(0.5, 0.5)
    _tick(layer, clock)
    layer.pointer_up()
    _, frame = _run(layer, clock, 2.0)
    assert set(frame.coverage) == {"a", "b"}


def test_spot_keeps_the_show_only_under_the_pointer(clock):
    layer = _layer(softness=0.0, look="blackout")
    layer.pointer_down("spot", 0.37, 0.5)
    colors, frame = _tick(layer, clock)
    assert colors["b"] is BLUE
    assert all(_rgb(colors[lamp]) == (0.0, 0.0, 0.0) for lamp in "acd")
    layer.pointer_move(0.64, 0.5)  # the spot moves on: b goes dark, c comes back
    colors, _ = _run(layer, clock, 1.0)
    assert colors["c"] is BLUE and _rgb(colors["b"]) == (0.0, 0.0, 0.0)


def test_ripple_spreads_from_where_it_was_dropped(clock):
    layer = _layer()
    layer.pointer_down("ripple", 0.1, 0.5)
    layer.pointer_up()
    first = {}
    for step in range(30):
        _, frame = _tick(layer, clock)
        for lamp in frame.coverage:
            first.setdefault(lamp, step)
    assert [lamp for lamp, _ in sorted(first.items(), key=lambda item: item[1])] == list(LAMPS)
    assert not layer.active  # one beat at 120 BPM, and it's gone


# -- one-shots ----------------------------------------------------------------------------------


def test_sweeps_cross_the_stage_their_own_way(clock):
    for kind, order in (("sweep_right", "abcd"), ("sweep_left", "dcba")):
        layer = _layer()
        layer.trigger(kind)
        first = {}
        for step in range(30):
            _, frame = _tick(layer, clock)
            for lamp in frame.coverage:
                first.setdefault(lamp, step)
        assert "".join(sorted(first, key=first.get)) == order
        assert not layer.active


def test_open_and_close_mirror_about_the_middle(clock):
    for kind, first_pair, last_pair in (("open", "bc", "ad"), ("close", "ad", "bc")):
        layer = _layer()
        layer.trigger(kind)
        first = {}
        for step in range(30):
            _, frame = _tick(layer, clock)
            for lamp in frame.coverage:
                first.setdefault(lamp, step)
        assert max(first[lamp] for lamp in first_pair) < min(first[lamp] for lamp in last_pair), kind


def test_every_one_shot_touches_every_lamp_and_ends(clock):
    for kind in ONE_SHOTS:
        layer = _layer()
        layer.trigger(kind)
        touched = set()
        for _ in range(30):
            _, frame = _tick(layer, clock)
            touched |= set(frame.coverage)
        assert touched == set(LAMPS), kind
        assert not layer.active, kind


def test_wave_length_follows_the_tempo(clock):
    layer = _layer(wave_beats=2.0)
    _tick(layer, clock, beat_period=0.4, beats=3.0)
    layer.trigger("flash")
    assert layer._waves[-1].duration == pytest.approx(0.8)
    slow = _layer(wave_beats=2.0)
    slow.trigger("flash")  # no tempo: as if at 120 BPM
    assert slow._waves[-1].duration == pytest.approx(1.0)


def test_snap_to_beat_waits_for_the_next_half_beat(clock):
    layer = _layer(snap_to_beat=True)
    _tick(layer, clock, beat_period=0.5, beats=10.8)  # 0.2 beats before the next half beat
    layer.trigger("flash")
    assert layer._waves[-1].t0 - clock.now == pytest.approx(0.1)
    _, frame = _tick(layer, clock, beat_period=0.5, beats=10.87)
    assert frame.coverage == {}  # not yet
    _, frame = _run(layer, clock, 0.1, beat_period=0.5, beats=11.05)
    assert set(frame.coverage) == set(LAMPS)
    # Pressed only just late: it goes at once.
    _tick(layer, clock, beat_period=0.5, beats=12.1)
    layer.trigger("flash")
    assert layer._waves[-1].t0 == pytest.approx(clock.now)
    # No tempo: nothing to wait for.
    free = _layer(snap_to_beat=True)
    free.trigger("flash")
    assert free._waves[-1].t0 == pytest.approx(clock.now)


# -- holds ---------------------------------------------------------------------------------------


def test_blackout_eases_in_holds_and_lets_go(clock):
    layer = _layer()
    layer.hold("blackout", True)
    colors, frame = _run(layer, clock, 0.1)
    assert all(_rgb(colors[lamp]) == (0.0, 0.0, 0.0) for lamp in LAMPS)
    assert all(level == pytest.approx(1.0) for level in frame.coverage.values())
    assert layer.held() == ["blackout"]
    layer.hold("blackout", False)
    colors, _ = _run(layer, clock, 0.3)
    assert colors["a"] is BLUE and not layer.active


def test_freeze_holds_what_the_lamps_showed(clock):
    layer = _layer()
    layer.hold("freeze", True)
    _run(layer, clock, 0.1, base=RED)
    colors, _ = _run(layer, clock, 0.5, base=GREEN)  # the show has moved on
    assert all(_rgb(colors[lamp]) == _rgb(RED) for lamp in LAMPS)


def test_invert_shows_the_complementary_colour(clock):
    layer = _layer()
    layer.hold("invert", True)
    colors, _ = _run(layer, clock, 0.2, base=RED)
    assert _rgb(colors["a"]) == pytest.approx((0.0, 1.0, 1.0))


def test_flood_spreads_the_gradient_across_the_stage(clock):
    layer = _layer()
    layer.hold("flood", True)
    colors, _ = _run(layer, clock, 0.2)
    palette = layer.style.palette
    assert _rgb(colors["a"]) == pytest.approx(gradient_at(palette, 0.1))
    assert _rgb(colors["d"]) == pytest.approx(gradient_at(palette, 0.91))


def test_blinder_lights_the_white_leds_over_the_colour(clock):
    layer = _layer(white_temp=0.25)
    layer.hold("blinder", True)
    colors, frame = _run(layer, clock, 0.1)
    assert frame.whites == {lamp: (pytest.approx(1.0), 0.25) for lamp in LAMPS}
    assert all(colors[lamp] is BLUE for lamp in LAMPS) and frame.coverage == {}
    layer.hold("blackout", True)  # blackout goes over everything, the white too
    _, frame = _run(layer, clock, 0.1)
    assert frame.whites == {}


def test_storm_pops_lamps_at_random(clock):
    layer = _layer(speed=1.0)  # a pop on every 16th
    layer.hold("storm", True)
    touched, most_at_once = set(), 0
    for _ in range(90):
        _, frame = _tick(layer, clock)
        touched |= set(frame.coverage)
        most_at_once = max(most_at_once, sum(1 for level in frame.coverage.values() if level > 0.8))
    assert touched == set(LAMPS) and most_at_once < len(LAMPS)
    layer.hold("storm", False)
    _run(layer, clock, 1.0)
    assert not layer.active


def test_orbit_goes_round_the_lamps(clock):
    layer = _layer()
    layer.config.positions = {"a": [0.5, 0.1], "b": [0.9, 0.5], "c": [0.5, 0.9], "d": [0.1, 0.5]}
    layer.hold("orbit", True)
    order = []
    for _ in range(60):  # a round is a bar: 2 s at 120 BPM
        _, frame = _tick(layer, clock)
        peak = max(frame.coverage, key=frame.coverage.get, default=None)
        if peak is not None and frame.coverage[peak] > 0.6 and (not order or order[-1] != peak):
            order.append(peak)
    assert "".join(order) in "bcdabcdabcd" and len(order) >= 4  # clockwise, one after the other


def test_holds_only_take_known_kinds(clock):
    layer = _layer()
    layer.hold("nonsense", True)
    assert not layer.active
    for kind in HOLDS:
        layer.hold(kind, True)
    assert sorted(layer.held()) == sorted(HOLDS)


# -- looks ---------------------------------------------------------------------------------------


def test_palette_is_a_gradient_with_two_ends_and_a_loop_when_it_drifts():
    palette = (RED, GREEN, BLUE) = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    assert gradient_at(palette, 0.0) == pytest.approx(RED)
    assert gradient_at(palette, 0.5) == pytest.approx(GREEN)
    assert gradient_at(palette, 1.0) == pytest.approx(BLUE)
    assert palette_at(palette, 1.0) == pytest.approx(RED)  # round the loop and back
    assert palette_at(palette, 5.0 / 6.0) == pytest.approx((0.5, 0.0, 0.5))
    assert palette_at(((0.2, 0.3, 0.4),), 0.7) == (0.2, 0.3, 0.4)
    assert rgb_to_hex(hex_to_rgb("#ff8000")) == "#ff8000" and hex_to_rgb("junk") == (1.0, 1.0, 1.0)
    for colors in PALETTES.values():
        assert 1 <= len(colors) <= rt.MAX_PALETTE_COLORS


def test_music_pulse_makes_the_look_as_bright_as_the_show(clock):
    layer = _layer(music=1.0)
    layer.hold("flood", True)
    dim, _ = _run(layer, clock, 0.2, base=Color(0.0, 0.0, 0.25))
    bright, _ = _run(layer, clock, 0.2, base=Color(0.0, 0.0, 1.0))
    assert max(_rgb(dim["a"])) == pytest.approx(0.25 * max(_rgb(bright["a"])))
    # The show stopped: there's nothing to follow, the look is at its own brightness.
    alone, _ = _run(layer, clock, 0.2, base=Color.black(), show_running=False)
    assert max(_rgb(alone["a"])) == pytest.approx(max(_rgb(bright["a"])))


def test_brightness_and_the_shows_master_brightness_scale_the_look(clock):
    layer = _layer(brightness=0.5)
    layer.hold("flood", True)
    colors, _ = _run(layer, clock, 0.2, master=0.5)
    assert max(_rgb(colors["a"])) == pytest.approx(0.25 * max(gradient_at(layer.style.palette, 0.1)))


def test_complement_turns_the_shows_colour_and_keeps_its_brightness(clock):
    layer = _layer(look="hue_shift", hue_shift_deg=120.0)
    layer.pointer_down("brush", 0.1, 0.5)
    colors, _ = _tick(layer, clock, base=Color(0.5, 0.0, 0.0))
    assert _rgb(colors["a"]) == pytest.approx((0.0, 0.5, 0.0))


def test_complement_turns_part_of_the_way_at_a_soft_edge(clock):
    layer = _layer(look="hue_shift", softness=1.0, radius=0.6)
    layer.pointer_down("brush", 0.1, 0.5)  # b is near the rim
    colors, frame = _tick(layer, clock, base=RED)
    assert _rgb(colors["a"]) == pytest.approx((0.0, 1.0, 1.0))
    hue, saturation, value = colors["b"].to_hsv()
    assert 0.0 < hue < 40.0 and saturation == pytest.approx(1.0) and value == pytest.approx(1.0)  # no grey


def test_strobe_look_flashes_on_the_beats_subdivisions(clock):
    layer = _layer(look="strobe", speed=1.0)
    layer.hold("flood", True)
    _run(layer, clock, 0.2)
    lit = []
    for sixteenth in range(8):  # 16ths at speed 1: on for the first half of each
        for part in (0.1, 0.6):
            colors, _ = _tick(layer, clock, beat_period=0.5, beats=20.0 + (sixteenth + part) / 4.0)
            lit.append(max(_rgb(colors["a"])) > 0.0)
    assert lit == [True, False] * 8
    assert strobe_per_beat(1.0, 0.5) == 4 and strobe_per_beat(0.5, 0.5) == 2 and strobe_per_beat(2.0, 0.5) == 4
    assert strobe_per_beat(1.0, 0.3) == 3  # 200 BPM: 16ths would be 13 a second


def test_every_look_renders(clock):
    for look in LOOKS:
        layer = _layer(look=look, speed=1.0, music=0.5)
        layer.hold("flood", True)
        layer.trigger("burst")
        layer.pointer_down("brush", 0.37, 0.5)
        for _ in range(20):
            colors, frame = _tick(layer, clock)
            for color in colors.values():
                assert all(0.0 <= channel <= 1.0 for channel in _rgb(color)), look
            for level, temp in frame.whites.values():
                assert 0.0 <= level <= 1.0 and 0.0 <= temp <= 1.0
    assert Style.from_config(RealtimeConfig(look="palette", palette=[])).palette == ((1.0, 1.0, 1.0),)


def test_freeze_brush_keeps_the_colour_it_touched(clock):
    layer = _layer(look="freeze", latch=True)
    layer.pointer_down("brush", 0.1, 0.5)
    _tick(layer, clock, base=RED)
    _tick(layer, clock, base=RED)
    layer.pointer_up()
    colors, _ = _run(layer, clock, 1.0, base=GREEN)
    assert _rgb(colors["a"]) == _rgb(RED) and _rgb(colors["b"]) == _rgb(GREEN)


# -- the stage -----------------------------------------------------------------------------------


def test_layouts_follow_the_chase_order():
    order = {"a": 2, "b": 0, "c": None, "d": 1}
    line = layout("line", list(LAMPS), order)
    assert sorted(line, key=lambda lamp: line[lamp][0]) == ["b", "d", "a", "c"]  # no order: last
    circle = layout("circle", list(LAMPS), order)
    assert circle["b"] == pytest.approx([0.5, 0.14])  # the first at the top
    grid = layout("grid", list("abcdefgh"), {})
    assert len({tuple(place) for place in grid.values()}) == 8
    for places in (line, circle, grid):
        assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in places.values())
    assert layout("circle", ["x"]) == {"x": [0.5, 0.5]} and layout("line", []) == {}


def test_new_lamps_get_a_place_without_moving_the_others():
    layer = RealtimeLayer(RealtimeConfig())
    assert layer.ensure_positions(list(LAMPS), {})
    before = {lamp: list(place) for lamp, place in layer.config.positions.items()}
    assert set(before) == set(LAMPS)
    assert not layer.ensure_positions(list(LAMPS), {})
    assert layer.ensure_positions(list(LAMPS) + ["new"], {})
    assert {lamp: layer.config.positions[lamp] for lamp in LAMPS} == before
    assert layer.config.positions["new"][1] > 0.85  # along the bottom edge, to be dragged from there
    assert layer.position("unknown") == (0.5, 0.5)


def test_settings_survive_a_save_and_bad_values_dont():
    cfg = AppConfig()
    cfg.realtime.positions = {"a": [0.25, 0.75]}
    cfg.realtime.palette = ["#112233", "#445566"]
    cfg.realtime.look, cfg.realtime.tool, cfg.realtime.latch = "plasma", "curtain", True
    again = AppConfig.from_dict(cfg.to_dict())
    assert again.realtime == cfg.realtime
    assert AppConfig.from_dict({}).realtime == RealtimeConfig()  # a config from before the tab
    odd = RealtimeConfig.from_dict({
        "look": "nope", "tool": "nope", "palette": [], "positions": {"a": [3.0, -1.0], "b": "x"}, "radius": "0.5",
    })
    assert odd.look == "palette" and odd.tool == "brush" and odd.palette == RealtimeConfig().palette
    assert odd.positions == {"a": [1.0, 0.0]} and odd.radius == 0.5


# -- engine ----------------------------------------------------------------------------------------


def _engine(monkeypatch, **kw):
    """test_strobe's engine (Beat Sync, the sequencer's white flashes on every
    16th) without its strobe, recording the colours it sends."""
    engine, clock, lamps = _make_engine(monkeypatch, strobe_enabled=False, **kw)
    lamps.color_calls = []
    lamps.push_colors = lambda colors: lamps.color_calls.append((clock.now, dict(colors)))
    engine.realtime.config.speed = 0.0
    engine.realtime.config.music = 0.0
    return engine, clock, lamps


def test_engine_show_is_untouched_while_nothing_is_played(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)

    def never(*args, **kw):
        raise AssertionError("the layer was laid over a show nobody is playing on")

    monkeypatch.setattr(engine.realtime, "apply", never)
    _run_engine(engine, clock, 6.0)
    assert lamps.color_calls and lamps.white_calls
    assert not engine.realtime.active and engine.realtime.config.positions == {}  # not even looked at
    # ...but it's told where the beat is, so the first pad pressed can snap to it.
    assert engine.realtime._beat_period == pytest.approx(PERIOD, rel=0.05)
    engine.running = False
    engine.tick_realtime_idle()
    assert engine.realtime._beat_period is None  # the show stopped: no beat to wait for


def test_engine_lays_the_layer_over_the_finished_show(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    _run_engine(engine, clock, 3.0)
    engine.realtime.ensure_positions(DEVICES, {})
    engine.realtime.config.look = "blackout"
    x, y = engine.realtime.position("b")
    engine.realtime.config.softness = 0.0
    engine.realtime.config.radius = 0.1
    engine.realtime.pointer_down("brush", x, y)
    lamps.color_calls.clear()
    lamps.white_calls.clear()
    _run_engine(engine, clock, 2.0)
    sent = [colors for _, colors in lamps.color_calls]
    whites = [targets for _, targets in lamps.white_calls]
    assert any("b" in colors for colors in sent)
    assert all(_rgb(colors["b"]) == (0.0, 0.0, 0.0) for colors in sent if "b" in colors)
    assert any(max(_rgb(colors["a"])) > 0.0 for colors in sent if "a" in colors)  # the others play on
    assert whites and all("b" not in targets for targets in whites)  # no white flash through the blackout
    assert engine.latest_lamp_colors["b"].to_rgb255() == (0, 0, 0)  # what the stage and the tiles show


def test_engine_blinder_respects_the_true_white_multiplier(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.config.color_mapping.beat_sync.white_pulse_enabled = False
    _run_engine(engine, clock, 1.0)
    engine.realtime.hold("blinder", True)
    lamps.white_calls.clear()
    _run_engine(engine, clock, 0.5)
    targets = lamps.white_calls[-1][1]
    assert set(targets) == set(DEVICES)
    assert targets["a"].brightness == pytest.approx(1.0) and targets["d"].brightness == pytest.approx(0.5)
    assert targets["a"].under is not None  # the colour keeps showing under the white
    engine.realtime.hold("blinder", False)
    _run_engine(engine, clock, 0.3)  # it fades out...
    lamps.white_calls.clear()
    _run_engine(engine, clock, 1.0)
    assert lamps.white_calls == []  # ...and the lamps are back on plain colour


def test_engine_legacy_bulbs_drop_the_white_flash_where_the_layer_covers(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, transitions="legacy")
    _run_engine(engine, clock, 3.0)
    assert any(targets for _, targets in lamps.white_calls)
    engine.realtime.hold("flood", True)
    _run_engine(engine, clock, 0.3)
    lamps.white_calls.clear()
    _run_engine(engine, clock, 2.0)
    assert not any(targets for _, targets in lamps.white_calls)


def test_engine_colours_beat_sync_white_through_the_rgb_leds(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.config.color_mapping.mode = "beat_sync_white"
    _run_engine(engine, clock, 1.0)
    assert lamps.white_calls and not lamps.color_calls  # the show: white only
    engine.realtime.hold("flood", True)
    _run_engine(engine, clock, 0.2)
    lamps.white_calls.clear()
    _run_engine(engine, clock, 0.5)
    sent = lamps.color_calls[-1][1]
    assert set(sent) == set(DEVICES) and all(max(_rgb(color)) > 0.5 for color in sent.values())
    assert lamps.white_calls == []  # fully covered: the white is out, the lamps are sent plain colour
    # Half way in or out, the white dims and the colour shows under it.
    engine.realtime.hold("flood", False)
    lamps.white_calls.clear()
    _run_engine(engine, clock, 0.07)
    mixed = [targets for _, targets in lamps.white_calls if targets]
    assert mixed and all(t.under is not None and max(_rgb(t.under)) > 0.0 for t in mixed[0].values())


def test_engine_plays_the_layer_alone_while_the_show_is_stopped(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.running = False
    engine.tick_realtime_idle()
    assert lamps.color_calls == []  # nothing played: nothing sent
    engine.realtime.ensure_positions(DEVICES, {})
    engine.realtime.config.radius, engine.realtime.config.trail_s = 0.1, 0.1
    engine.realtime.pointer_down("brush", *engine.realtime.position("c"))
    engine.tick_realtime_idle()
    assert list(lamps.color_calls[-1][1]) == ["c"]  # only the lamp it touches
    assert max(_rgb(lamps.color_calls[-1][1]["c"])) > 0.5
    engine.realtime.pointer_up()
    for _ in range(12):
        clock.now += TICK
        engine.tick_realtime_idle()
    assert lamps.color_calls[-1][1] == {"c": Color.black()}  # one black when it lets go
    count = len(lamps.color_calls)
    for _ in range(10):
        clock.now += TICK
        engine.tick_realtime_idle()
    assert len(lamps.color_calls) == count and not engine.realtime.active


def test_engine_idle_tick_stays_out_of_a_running_show(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.realtime.hold("flood", True)
    engine.tick_realtime_idle()
    assert lamps.color_calls == []


def test_engine_moving_a_lamp_on_the_stage_lights_it_up(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.running = False
    engine.realtime.identify = "b"
    engine.tick_realtime_idle()
    sent = lamps.color_calls[-1][1]
    assert list(sent) == ["b"] and min(_rgb(sent["b"])) > 0.4


# -- the Strobe pad ----------------------------------------------------------------------------------


def test_strobe_pad_plays_until_it_is_let_go(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.running = False  # works with the show stopped
    message = engine.realtime_strobe(True)
    assert message.startswith("Strobe: 9.0 flashes a second")
    _, bursts = lamps.strobes[-1]
    assert set(bursts) == set(DEVICES)
    first = bursts["a"]
    assert first.period_s == pytest.approx(1.0 / 9.0) and first.on_s == pytest.approx(0.5 / 9.0)
    assert 15.0 < first.flashes * first.period_s <= 20.0  # never more than 20 s in one go
    assert bursts["d"].brightness == pytest.approx(0.5)  # the per-lamp white multiplier
    assert engine.realtime_strobe(False) == ""
    _, stop = lamps.strobes[-1]
    assert set(stop) == set(DEVICES) and all(burst.flashes == 0 for burst in stop.values())
    assert engine._strobe_burst is None
    count = len(lamps.strobes)
    engine.realtime_strobe(False)  # let go twice: nothing more to stop
    assert len(lamps.strobes) == count


def test_strobe_pad_is_limited_by_the_lamp_command_rate(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    engine.running = False
    engine.config.realtime.strobe_hz = 15.0  # 20 commands/s: 10 flashes a second at most
    engine.realtime_strobe(True)
    assert lamps.strobes[-1][1]["a"].period_s == pytest.approx(0.1)


def test_strobe_pad_follows_the_beat_when_the_clock_is_locked(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch)
    _run_engine(engine, clock, 6.0)
    assert engine.realtime_beat_period() == pytest.approx(PERIOD, rel=0.05)
    message = engine.realtime_strobe(True)
    burst = lamps.strobes[-1][1]["a"]
    assert burst.period_s == pytest.approx(PERIOD / 4, rel=0.05) and "4 to the beat" in message  # 16ths
    on_grid = ((burst.start - 1000.0) / (PERIOD / 4) + 0.5) % 1.0 - 0.5
    assert abs(on_grid) < 0.2
    # While it plays, the white LEDs are the strobe's: no white flashes of the show's.
    lamps.white_calls.clear()
    _run_engine(engine, clock, 2.0)
    assert all(at < burst.start for at, _ in lamps.white_calls)
    engine.realtime_strobe(False)


def test_strobe_pad_needs_instant_lamp_transitions(monkeypatch):
    engine, clock, lamps = _engine(monkeypatch, transitions="legacy")
    assert engine.realtime_strobe(True).startswith("Strobe not played")
    assert lamps.strobes == []


def test_an_empty_burst_stops_a_strobe_on_the_lamp():
    device = _FakeDevice()
    worker = _start_worker(device)
    try:
        start = time.perf_counter() + 0.05
        worker.start_strobe(StrobeBurst(start, 0.1, 0.05, 100, 1.0))  # ten seconds' worth
        time.sleep(0.4)
        worker.start_strobe(StrobeBurst(time.perf_counter(), 0.1, 0.05, 0, 0.0))
        time.sleep(0.25)
        sent = len(device.sent)
        assert 4 <= sent <= 12
        assert device.sent[-1][1] == "colour"  # the white is off
        time.sleep(0.4)
        assert len(device.sent) == sent  # ...and stays off
    finally:
        worker.stop()
        worker.join(timeout=1.0)
