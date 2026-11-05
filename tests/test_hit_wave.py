"""Beat Sync's Across the lamps (effects/hit_wave.py): a new colour travels along
the Chase order on the hits, so nothing moves on a beat with no hit. Pure wave
tests, then the engine on the shared clock with a fake kick (see
test_rhythm_engine), with what plays on top of it.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import airam_lights.engine.visualization_engine as ve_module  # noqa: E402
from airam_lights.config.schema import BeatSyncModeConfig, PerLampEffect  # noqa: E402
from airam_lights.effects.hit_wave import HitWave, WaveColor  # noqa: E402

from test_rhythm_engine import DEVICES, PERIOD, TICK, _make_engine, _run  # noqa: E402


def _wave(positions=8):
    wave = HitWave()
    wave.resize(positions, WaveColor(0.0), 0.0, None, 0)
    return wave


def _hues(wave):
    return [place.color.hue for place in wave.places]


def _distance(a, b):
    """Signed: from a to b, the short way round."""
    return ((b - a + 180.0) % 360.0) - 180.0


# -- the wave ----------------------------------------------------------------------------------------


def test_a_colour_takes_wave_length_hits_from_the_first_lamp_to_the_last():
    wave = _wave(8)
    wave.flash(1.0, 4.0, WaveColor(100.0), 1.0)
    assert _hues(wave) == [100.0] * 2 + [0.0] * 6
    wave.flash(1.0, 4.0, None, 2.0)
    assert _hues(wave) == [100.0] * 4 + [0.0] * 4
    wave.flash(1.0, 4.0, None, 3.0)
    assert _hues(wave) == [100.0] * 6 + [0.0] * 2
    wave.flash(1.0, 4.0, None, 4.0)
    assert _hues(wave) == [100.0] * 8


def test_wave_length_one_is_every_lamp_on_the_same_hit_however_soft():
    wave = _wave(8)
    wave.flash(0.3, 1.0, WaveColor(100.0), 1.0)
    assert _hues(wave) == [100.0] * 8


def test_the_first_lamp_gets_the_colour_on_its_hit_even_on_a_long_wave():
    wave = _wave(2)
    wave.flash(0.3, 8.0, WaveColor(100.0), 1.0)
    assert _hues(wave) == [100.0, 0.0]
    for t in range(7):
        assert _hues(wave)[1] == 0.0
        wave.flash(1.0, 8.0, None, 2.0 + t)
    assert _hues(wave) == [100.0, 100.0]


def test_a_soft_hit_moves_it_as_little_as_it_counts():
    wave = _wave(8)
    wave.flash(1.0, 4.0, WaveColor(100.0), 1.0)
    wave.flash(0.5, 4.0, None, 2.0)  # counts a quarter: half a lamp on
    assert _hues(wave).count(100.0) == 2
    wave.flash(0.5, 4.0, None, 3.0)
    assert _hues(wave).count(100.0) == 3


def test_a_colour_on_every_hit_moves_down_the_line():
    wave = _wave(8)
    for hit in range(1, 4):
        wave.flash(1.0, 4.0, WaveColor(float(hit)), float(hit))
    assert _hues(wave) == [3.0, 3.0, 2.0, 2.0, 1.0, 1.0, 0.0, 0.0]


def test_nothing_piles_up():
    wave = _wave(8)
    for hit in range(500):
        wave.flash(1.0, 4.0, WaveColor(float(hit)), float(hit))
        assert len(wave._waves) <= 4


def test_a_shorter_wave_length_finishes_what_is_on_its_way():
    wave = _wave(8)
    wave.flash(1.0, 8.0, WaveColor(100.0), 1.0)
    wave.flash(1.0, 1.0, None, 2.0)
    assert _hues(wave) == [100.0] * 8 and wave._waves == []


def test_a_lamp_counts_its_flashes_from_when_the_colour_reached_it():
    wave = _wave(2)
    wave.flash(1.0, 2.0, WaveColor(100.0), 1.0)
    first, second = wave.places
    assert (first.arrived_at, first.flashes) == (1.0, 0)
    assert (second.arrived_at, second.flashes) == (None, 1)
    wave.flash(1.0, 2.0, None, 2.0)
    assert (first.arrived_at, first.flashes) == (1.0, 1)
    assert (second.arrived_at, second.flashes) == (2.0, 0)


def test_settings_round_trip_and_start_as_the_whole_room_at_once():
    assert BeatSyncModeConfig.from_dict({}).wave_hits == 1.0
    assert BeatSyncModeConfig.from_dict({}).spread_deg == 0.0
    cfg = BeatSyncModeConfig(wave_hits=3.5, spread_deg=60.0)
    assert BeatSyncModeConfig.from_dict(cfg.to_dict()) == cfg
    assert BeatSyncModeConfig.from_dict({"wave_hits": 0.25}).wave_hits == 1.0


# -- in the engine -------------------------------------------------------------------------------------


def _engine(monkeypatch, configure=None, **beat_sync):
    def setup(config):
        for key, value in {"hue_attack_ms": 5.0, **beat_sync}.items():
            setattr(config.color_mapping.beat_sync, key, value)
        if configure is not None:
            configure(config)

    engine, clock, lamps = _make_engine(monkeypatch, setup)
    _run(engine, clock, 6.0, lambda: None)  # let the clock lock and the hits settle
    return engine, clock, lamps


def _set_kick(monkeypatch, clock, kicks):
    """The beat band from now on: a kick on the beats k with kicks(k), else flat."""

    def energy(frame, lo, hi):
        beats = (clock.now - 1000.0) / PERIOD
        k = round(beats)
        return 0.9 if kicks(k) and abs(beats - k) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", energy)


def _lamp_hues(engine):
    return [engine.latest_lamp_colors[d].to_hsv()[0] for d in DEVICES]


def _flashed(engine, clock):
    return engine.last_beat_time == clock.now


def _moved(last, hues):
    """Whether a lamp's hue moved (the brightness falling after a flash doesn't count)."""
    return any(abs(_distance(a, b)) > 1.0 for a, b in zip(last, hues))


def test_by_default_every_lamp_shows_the_room_colour(monkeypatch):
    engine, clock, _ = _engine(monkeypatch)
    room = []

    def on_tick():
        hues = _lamp_hues(engine)
        assert len({round(h, 6) for h in hues}) == 1
        room.append(engine.latest_band3_levels["hue"])

    _run(engine, clock, 3.0, on_tick)
    assert max(room) - min(room) > 1.0


def test_the_colours_move_only_on_the_flashes(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, wave_hits=3.0, hue_attack_ms=0.0)
    moves, flashes = [], []
    last = _lamp_hues(engine)

    def on_tick():
        nonlocal last
        hues = _lamp_hues(engine)
        if _moved(last, hues):
            moves.append(clock.now)
        if _flashed(engine, clock):
            flashes.append(clock.now)
        last = hues

    _run(engine, clock, 5.0, on_tick)
    assert len(flashes) >= 8
    assert moves and set(moves) <= set(flashes)


def test_a_beat_with_no_hit_moves_nothing(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, wave_hits=4.0, hue_attack_ms=0.0)
    _set_kick(monkeypatch, clock, lambda k: k % 2 == 0)  # a half-time kick: the clock keeps every beat
    _run(engine, clock, 8.0, lambda: None)  # the beats learn the new pattern
    beats = moved_on_a_quiet_beat = moves = 0
    last = _lamp_hues(engine)

    def on_tick():
        nonlocal beats, moved_on_a_quiet_beat, moves, last
        hues = _lamp_hues(engine)
        beats += engine.latest_clock_beat.is_beat
        if _moved(last, hues):
            moves += 1
            moved_on_a_quiet_beat += not _flashed(engine, clock)
        last = hues

    _run(engine, clock, 8.0, on_tick)
    assert beats >= 14
    assert 5 <= moves <= beats * 0.6  # about every other beat
    assert moved_on_a_quiet_beat == 0


def test_a_breakdown_leaves_the_wave_standing_until_the_music_comes_back(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, wave_hits=8.0, hue_change_hits=1.0, hue_attack_ms=0.0)
    _set_kick(monkeypatch, clock, lambda k: False)
    _run(engine, clock, 2.0, lambda: None)  # (a beat that still expected a hit flashes once more)
    standing = _lamp_hues(engine)
    assert len({round(h, 3) for h in standing}) > 1  # a colour was on its way

    def on_tick():
        assert _lamp_hues(engine) == pytest.approx(standing, abs=0.5)

    _run(engine, clock, 5.0, on_tick)
    _set_kick(monkeypatch, clock, lambda k: True)
    _run(engine, clock, 2.0, lambda: None)
    assert _lamp_hues(engine) != pytest.approx(standing, abs=0.5)


def test_it_runs_along_the_chase_order_and_reverse_turns_it_round(monkeypatch):
    def first_changed(reverse):
        def configure(config):
            config.chase.reverse = reverse

        # One colour crossing at a time: the first change lands on one end of the room.
        engine, clock, _ = _engine(monkeypatch, configure, wave_hits=4.0, hue_change_hits=4.0, hue_attack_ms=0.0)
        out = []
        last = _lamp_hues(engine)

        def on_tick():
            nonlocal last
            hues = _lamp_hues(engine)
            changed = [i for i, (a, b) in enumerate(zip(last, hues)) if abs(_distance(a, b)) > 1.0]
            if changed:
                out.append(changed)
            last = hues

        _run(engine, clock, 6.0, on_tick)
        return out

    forward = first_changed(False)
    # One lamp per hit, in the order - the other way round with Reverse direction.
    assert any(forward[i:i + 4] == [[0], [1], [2], [3]] for i in range(len(forward)))
    backward = first_changed(True)
    assert any(backward[i:i + 4] == [[3], [2], [1], [0]] for i in range(len(backward)))


def test_hue_spread_fans_the_lamps_out_around_the_room_colour(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, spread_deg=90.0)
    _run(engine, clock, 0.2, lambda: None)
    hues = _lamp_hues(engine)
    room = engine.latest_band3_levels["hue"]
    assert [_distance(room, h) for h in hues] == pytest.approx([-45.0, -15.0, 15.0, 45.0], abs=2.0)


def test_lamps_outside_the_order_go_with_its_first_position(monkeypatch):
    def configure(config):
        config.per_lamp_effects["d"] = PerLampEffect(device_id="d", chase_order=None)

    engine, clock, _ = _engine(monkeypatch, configure, spread_deg=90.0)
    hues = _lamp_hues(engine)
    assert hues[3] == pytest.approx(hues[0]) and hues[0] != pytest.approx(hues[2], abs=10.0)


def test_with_no_chase_order_every_lamp_shows_the_room_colour(monkeypatch):
    def configure(config):
        config.per_lamp_effects.clear()

    engine, clock, _ = _engine(monkeypatch, configure, spread_deg=90.0, wave_hits=4.0)

    def on_tick():
        assert len({round(h, 6) for h in _lamp_hues(engine)}) == 1

    _run(engine, clock, 2.0, on_tick)


def test_the_flash_stays_on_every_lamp_at_once(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, wave_hits=8.0, spread_deg=120.0)

    def on_tick():
        values = [engine.latest_lamp_colors[d].to_hsv()[2] for d in DEVICES]
        assert max(values) - min(values) < 1e-6

    _run(engine, clock, 3.0, on_tick)


@pytest.mark.parametrize("timing", ["beat", "decay"])
def test_a_lamp_not_reached_yet_glides_on_from_its_own_colour(monkeypatch, timing):
    """Without a jump back: the room's glide starts over on each change, and a
    lamp still on the colour before mustn't go with it."""
    engine, clock, _ = _engine(
        monkeypatch, wave_hits=4.0, hue_mode="step", hue_step_deg=60.0, hue_glide_deg=60.0, glide_hue=True,
        hue_glide_timing=timing, hue_attack_ms=20.0,
    )
    moved = 0.0
    last = _lamp_hues(engine)

    def on_tick():
        nonlocal moved, last
        hues = _lamp_hues(engine)
        for a, b in zip(last, hues):
            assert _distance(a, b) > -1.0
            moved += _distance(a, b)
        last = hues

    _run(engine, clock, 5.0, on_tick)
    assert moved > 4 * 300.0  # all four lamps kept moving on: 60 degrees a beat, about 10 beats


def test_the_first_lamp_glides_as_the_room_does(monkeypatch):
    engine, clock, _ = _engine(
        monkeypatch, wave_hits=4.0, hue_mode="step", hue_step_deg=60.0, hue_glide_deg=60.0, glide_hue=True,
    )

    def on_tick():
        assert _distance(engine.latest_band3_levels["hue"], _lamp_hues(engine)[0]) == pytest.approx(0.0, abs=1.0)

    _run(engine, clock, 3.0, on_tick)


def test_a_white_flash_keeps_each_lamps_own_colour_under_it(monkeypatch):
    engine, clock, lamps = _engine(
        monkeypatch, spread_deg=120.0, white_pulse_enabled=True, white_pulse_probability=1.0,
    )
    lamps.white_calls.clear()
    _run(engine, clock, 3.0, lambda: None)
    whole_room = [call for call in lamps.white_calls if len(call) == len(DEVICES)]
    assert whole_room
    hues = [whole_room[0][d].under.to_hsv()[0] for d in DEVICES]
    assert _distance(hues[0], hues[3]) == pytest.approx(120.0, abs=10.0)


def test_chase_group_switch_and_the_sequencer_play_on_top(monkeypatch):
    def configure(config):
        config.chase.enabled = config.group_switch.enabled = True
        config.network.lamp_transitions = "direct"
        sq = config.sequencer
        sq.enabled = True
        sq.white_pattern, sq.white_density, sq.white_accent_focus, sq.white_build = "beats", 1.0, 0.0, 0.0
        sq.dark_pattern, sq.dark_density = "before_backbeats", 1.0
        sq.phrase_accent = sq.fills = sq.drop_detection = False
        sq.min_group_gap_ms = 0.0
        sq.double_chance = 0.0

    engine, clock, lamps = _engine(
        monkeypatch, configure, wave_hits=3.0, spread_deg=60.0, dark_pulse_enabled=True, white_pulse_enabled=True,
    )
    seen = set()

    def on_tick():
        assert set(engine.latest_lamp_colors) == set(DEVICES)
        seen.add(tuple(round(c.to_hsv()[0]) for c in engine.latest_lamp_colors.values()))

    _run(engine, clock, 8.0, on_tick)
    assert engine._sequencing and len(seen) > 10
    assert {"white", "dark"} <= {event["kind"] for event in engine.seq_events}
    assert lamps.white_calls


def test_the_lamps_start_from_the_room_colour_when_beat_sync_comes_back(monkeypatch):
    engine, clock, _ = _engine(monkeypatch, wave_hits=4.0)
    engine.config.color_mapping.mode = "peak_flash"
    _run(engine, clock, 0.2, lambda: None)
    engine.config.color_mapping.mode = "beat_sync"
    engine.tick_visual()
    assert len({round(place.color.hue, 6) for place in engine._beat_wave.places}) == 1


# -- the settings page ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from airam_lights.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w.controller.shutdown()
    w.close()


def test_the_page_sets_it_and_says_when_there_is_no_order_to_go_along(window):
    cmt = window.tabs.widget(2)
    config = window.controller.config
    bs = config.color_mapping.beat_sync
    config.per_lamp_effects.clear()
    cmt._update_beat_states()
    assert not cmt.beat_wave_slider.isEnabled() and not cmt.beat_spread_slider.isEnabled()
    assert "Chase order" in cmt.beat_lamps_note.text()

    for i, dev in enumerate("ab"):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i)
    cmt._update_beat_states()
    assert cmt.beat_wave_slider.isEnabled() and cmt.beat_spread_slider.isEnabled()
    assert cmt.beat_lamps_note.isHidden()
    cmt.beat_wave_slider._spin.setValue(4.0)
    cmt.beat_spread_slider._spin.setValue(70.0)
    assert bs.wave_hits == 4.0 and bs.spread_deg == 70.0
