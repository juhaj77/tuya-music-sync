"""The pulse sequencer's strobe: a rare burst of cool-white flashes on every
lamp at once, on top of the show.

- strobe_plan: the flash rate is a subdivision of the beat under the Max
  rate and the lamp command rate.
- PulseSequencer: where and how often a strobe is announced, and that it has
  the white flashes to itself while it runs.
- LampWorker: plays a burst at the burst's own times (the same for every
  lamp), keeps the current colour under the flashes, ignores it unless the
  lamp is on the real-time control datapoint in "direct" mode.
- VisualizationEngine: turns the sequencer's event into one burst for all
  lamps, on the beat grid, only with lamp_transitions "direct".

No bulbs, sockets or audio: fake devices, fake lamp manager, fake clock.
"""
import random
import time
from types import SimpleNamespace

import pytest

import airam_lights.engine.visualization_engine as ve_module
from airam_lights.audio.capture import AudioCapture
from airam_lights.color.models import Color, StrobeBurst, strobe_wave_level
from airam_lights.config.schema import AppConfig, NetworkConfig, PerLampEffect, PulseSequencerConfig
from airam_lights.effects.pulse_sequencer import STROBE_SUBDIVISIONS, PulseSequencer, strobe_plan, strobe_steps
from airam_lights.engine.visualization_engine import VisualizationEngine
from airam_lights.lamps.manager import LampWorker
from airam_lights.lamps.tuya_device import LampStatus

# -- strobe_plan ---------------------------------------------------------------------------


def test_plan_uses_sixteenths_at_dance_tempo():
    beat = 60.0 / 128.0
    flashes, period, on_s = strobe_plan(beat, 1.0, max_hz=10.0, duty=0.5, command_rate_hz=20.0)
    assert flashes == 4
    assert period == pytest.approx(beat / 4)
    assert on_s == pytest.approx(period / 2)


def test_plan_falls_back_to_a_slower_subdivision_at_fast_tempo():
    beat = 60.0 / 174.0  # 16ths would be 11.6 per second
    flashes, period, _ = strobe_plan(beat, 1.0, max_hz=10.0, duty=0.5, command_rate_hz=20.0)
    assert flashes == 3  # triplets: 8.7 per second
    assert period == pytest.approx(beat / 3)


def test_plan_respects_the_lamp_command_rate():
    beat = 60.0 / 128.0
    # 10 commands/s = at most 5 flashes/s, whatever Max rate says.
    flashes, period, _ = strobe_plan(beat, 1.0, max_hz=10.0, duty=0.5, command_rate_hz=10.0)
    assert flashes == 2 and 1.0 / period <= 5.0
    # A short on-time makes the on part the bottleneck: 0.25 * period >= 1/20 s.
    _, period, on_s = strobe_plan(beat, 1.0, max_hz=10.0, duty=0.25, command_rate_hz=20.0)
    assert on_s >= 1.0 / 20.0 - 1e-9


def test_plan_never_breaks_its_limits_at_any_tempo():
    for bpm in range(60, 201):
        for beats in (0.5, 1.0):
            for duty in (0.3, 0.5, 0.7):
                plan = strobe_plan(60.0 / bpm, beats, max_hz=9.0, duty=duty, command_rate_hz=30.0)
                if plan is None:
                    continue
                flashes, period, on_s = plan
                assert flashes >= 2
                assert 1.0 / period <= 9.0 + 1e-6
                assert min(on_s, period - on_s) >= 1.0 / 30.0 - 1e-9
                assert flashes * period == pytest.approx(beats * 60.0 / bpm)  # fills the burst exactly
                assert round(60.0 / bpm / period) in STROBE_SUBDIVISIONS


def test_plan_gives_up_when_nothing_fits():
    assert strobe_plan(60.0 / 160.0, 0.5, max_hz=10.0) is None  # two flashes in half a beat = 10.7/s
    assert strobe_plan(None, 1.0, max_hz=10.0) is None
    assert strobe_plan(0.5, 1.0, max_hz=3.0) is None


def test_plan_goes_faster_with_a_higher_ceiling_and_command_rate():
    beat = 60.0 / 128.0
    flashes, period, _ = strobe_plan(beat, 1.0, max_hz=15.0, duty=0.5, command_rate_hz=30.0)
    assert flashes == 6 and 1.0 / period == pytest.approx(12.8)  # 16th triplets
    # The same ceiling at 20 commands/s: still 16ths.
    flashes, period, _ = strobe_plan(beat, 1.0, max_hz=15.0, duty=0.5, command_rate_hz=20.0)
    assert flashes == 4 and 1.0 / period == pytest.approx(8.533, rel=1e-3)


def test_long_bursts_are_filled_at_the_same_rate():
    beat = 60.0 / 128.0
    for beats, expected in ((2.0, 8), (4.0, 16)):
        flashes, period, _ = strobe_plan(beat, beats, max_hz=10.0, duty=0.5, command_rate_hz=20.0)
        assert flashes == expected and period == pytest.approx(beat / 4)


def test_strobe_length_is_between_half_a_beat_and_the_bar():
    assert strobe_steps(0.5, 16) == 2 and strobe_steps(1.0, 16) == 4 and strobe_steps(4.0, 16) == 16
    assert strobe_steps(0.1, 16) == 2
    assert strobe_steps(4.0, 12) == 12  # 3/4: the bar is the limit
    assert strobe_steps(8.0, 16) == 16


def test_burst_times():
    burst = StrobeBurst(start=10.0, period_s=0.1, on_s=0.04, flashes=3, brightness=0.5)
    assert burst.on_time(2) == pytest.approx(10.2)
    assert burst.off_time(2) == pytest.approx(10.24)
    assert burst.end == pytest.approx(10.24)
    assert burst.is_on(10.01) and burst.is_on(10.13)
    assert not burst.is_on(10.05) and not burst.is_on(9.99) and not burst.is_on(10.3)


def test_wave_swells_from_dark_to_full_and_back():
    for wave in ("linear", "bezier"):
        for flashes in (3, 4, 6, 8, 16, 24):
            levels = [strobe_wave_level(i, flashes, wave) for i in range(flashes)]
            assert max(levels) == pytest.approx(1.0)  # the middle is at full brightness
            assert levels == pytest.approx(levels[::-1])  # same shape up and down
            half = levels[: (flashes + 1) // 2]
            assert half == sorted(half) and len(set(half)) == len(half)  # each flash brighter than the one before
            assert 0.0 < levels[0] <= 0.34  # starts near dark, but not invisible
    assert [strobe_wave_level(i, 4, "linear") for i in range(4)] == pytest.approx([1 / 3, 1.0, 1.0, 1 / 3])
    # The longer the burst, the darker it starts.
    assert strobe_wave_level(0, 16, "linear") < 0.1
    # bezier: the same ends and peak, but darker near the ends and brighter near the middle.
    linear = [strobe_wave_level(i, 16, "linear") for i in range(16)]
    bezier = [strobe_wave_level(i, 16, "bezier") for i in range(16)]
    assert bezier[1] < linear[1] and bezier[6] > linear[6]


def test_no_wave_means_every_flash_at_full_brightness():
    assert [strobe_wave_level(i, 8, "off") for i in range(8)] == [1.0] * 8
    assert [strobe_wave_level(i, 8, "something else") for i in range(8)] == [1.0] * 8
    assert [strobe_wave_level(i, 2, "linear") for i in range(2)] == [1.0, 1.0]  # too few flashes to shape
    burst = StrobeBurst(0.0, 0.1, 0.05, 4, 0.6, wave="linear")
    assert [burst.brightness_at(i) for i in range(4)] == pytest.approx([0.2, 0.6, 0.6, 0.2])
    assert [burst.index_at(t) for t in (-1.0, 0.01, 0.15, 0.39, 5.0)] == [0, 0, 1, 3, 3]


# -- PulseSequencer ------------------------------------------------------------------------

TICK_BEATS = 0.25 / 2  # two ticks per 16th


def _run_sequencer(seq, bars, energy=0.5, bpb=4):
    out = []
    pos, now = 0.1, 0.0
    while pos < bars * bpb:
        for e in seq.tick(pos, energy, now, 0.03, 3, bpb):
            out.append(e)
        pos += TICK_BEATS
        now += 0.03
    return out


def _seq_cfg(**kw):
    base = dict(
        enabled=True, white_pattern="sixteenths", white_density=1.0, group_walk="forward", double_chance=0.0,
        dark_pattern="off", phrase_bars=4, fills=False, phrase_accent=False, drop_detection=False,
        white_accent_focus=0.0, white_build=0.0, white_repeat=False,
        strobe_enabled=True, strobe_placement="phrase", strobe_chance=1.0, strobe_min_gap_bars=1,
        strobe_min_level="calm", strobe_beats=1.0,
    )
    base.update(kw)
    return PulseSequencerConfig(**base)


def test_strobe_is_off_by_default():
    assert PulseSequencerConfig().strobe_enabled is False
    events = _run_sequencer(PulseSequencer(_seq_cfg(strobe_enabled=False), random.Random(1)), 9)
    assert not [e for e in events if e.kind == "strobe"]


def test_strobe_fills_the_last_beat_of_each_phrase_and_silences_white_there():
    events = _run_sequencer(PulseSequencer(_seq_cfg(), random.Random(1)), 12)
    strobes = [e for e in events if e.kind == "strobe"]
    assert len(strobes) == 3  # bars 3, 7 and 11
    for e in strobes:
        # Announced one 16th early: on step 11, for the beat starting on step 12.
        assert e.bar_step == 11 and e.lead == pytest.approx(0.25) and e.length == pytest.approx(1.0)
        assert e.phrase_bar == 3 and e.reason == "phrase" and e.group is None
        assert (e.step // 16) % 4 == 3
    strobe_bars = {e.step // 16 for e in strobes}
    whites = [e for e in events if e.kind == "white"]
    for e in whites:
        if e.step // 16 in strobe_bars:
            assert e.bar_step < 12, "no white flashes under the strobe"
    # ...while the other bars keep theirs.
    assert any(e.bar_step >= 12 for e in whites if e.step // 16 not in strobe_bars)


def test_no_white_flash_on_the_downbeat_right_after_a_strobe():
    # The phrase start's all-lamps flash right after the strobe would read as
    # one more strobe flash that came late - that downbeat stays without one.
    events = _run_sequencer(PulseSequencer(_seq_cfg(phrase_accent=True), random.Random(1)), 13)
    strobe_ends = {e.step + 1 + 4 for e in events if e.kind == "strobe"}  # announced a 16th early, 1 beat long
    assert strobe_ends == {64, 128, 192}
    whites = {e.step: e for e in events if e.kind == "white"}
    for end in strobe_ends:
        assert end not in whites
    # Without a strobe before it, the phrase start keeps its flash.
    no_strobe = _run_sequencer(PulseSequencer(_seq_cfg(phrase_accent=True, strobe_enabled=False), random.Random(1)), 9)
    assert any(e.kind == "white" and e.reason == "phrase" and e.step == 64 for e in no_strobe)
    # ...and so does a downbeat that isn't right after one.
    assert whites[16].bar_step == 0


def test_half_beat_strobe_starts_on_the_last_eighth():
    events = _run_sequencer(PulseSequencer(_seq_cfg(strobe_beats=0.5), random.Random(1)), 8)
    strobes = [e for e in events if e.kind == "strobe"]
    assert strobes and all(e.bar_step == 13 and e.length == pytest.approx(0.5) for e in strobes)


def test_placement_bars_and_half_phrase():
    every_bar = [e for e in _run_sequencer(PulseSequencer(_seq_cfg(strobe_placement="bars"), random.Random(1)), 8)
                 if e.kind == "strobe"]
    assert len(every_bar) == 8
    half = [e for e in _run_sequencer(PulseSequencer(_seq_cfg(strobe_placement="half_phrase"), random.Random(1)), 8)
            if e.kind == "strobe"]
    assert [e.phrase_bar for e in half] == [1, 3, 1, 3]
    assert [e.reason for e in half] == ["half_phrase", "phrase"] * 2


def test_min_gap_keeps_strobes_apart():
    cfg = _seq_cfg(strobe_placement="bars", strobe_min_gap_bars=3)
    bars = [e.step // 16 for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 12) if e.kind == "strobe"]
    assert bars == [0, 3, 6, 9]


def test_chance_makes_it_rarer():
    cfg = _seq_cfg(strobe_placement="bars", strobe_chance=0.0)
    assert not [e for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 12) if e.kind == "strobe"]
    cfg = _seq_cfg(strobe_placement="bars", strobe_chance=0.5)
    some = [e for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 64) if e.kind == "strobe"]
    assert 10 < len(some) < 54


def test_no_strobe_while_the_music_is_quiet():
    # A steady level is "calm" for the loudness tracker (nothing in the last
    # half minute was quieter), so asking for at least "high" never fires.
    cfg = _seq_cfg(strobe_placement="bars", strobe_min_level="high")
    assert not [e for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 12) if e.kind == "strobe"]


def test_two_beat_strobe_fills_the_second_half_of_the_bar():
    events = _run_sequencer(PulseSequencer(_seq_cfg(strobe_beats=2.0), random.Random(1)), 12)
    strobes = [e for e in events if e.kind == "strobe"]
    assert len(strobes) == 3
    assert all(e.bar_step == 7 and e.length == pytest.approx(2.0) and e.phrase_bar == 3 for e in strobes)
    strobe_bars = {e.step // 16 for e in strobes}
    assert all(e.bar_step < 8 for e in events if e.kind == "white" and e.step // 16 in strobe_bars)


def test_whole_bar_strobe_is_announced_on_the_last_sixteenth_of_the_bar_before():
    events = _run_sequencer(PulseSequencer(_seq_cfg(strobe_beats=4.0), random.Random(1)), 12)
    strobes = [e for e in events if e.kind == "strobe"]
    # The phrase's last bars are 3, 7 and 11: each is announced at the very end of the bar before it.
    assert [(e.step // 16, e.bar_step) for e in strobes] == [(2, 15), (6, 15), (10, 15)]
    assert all(e.length == pytest.approx(4.0) and e.reason == "phrase" for e in strobes)
    whites = [e for e in events if e.kind == "white"]
    assert not [e for e in whites if e.step // 16 in (3, 7, 11)]  # the whole bar belongs to the strobe
    assert [e for e in whites if e.step // 16 == 4]  # ...and the new phrase gets its flashes back


def test_half_bars_placement_allows_two_strobes_per_bar():
    cfg = _seq_cfg(strobe_placement="half_bars", strobe_min_gap_bars=0)
    strobes = [e for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 8) if e.kind == "strobe"]
    assert len(strobes) == 16
    assert {e.bar_step for e in strobes} == {3, 11}  # into beat 3, and into the next bar
    assert {e.reason for e in strobes} == {"half_bar", "bar", "phrase"}
    # A minimum gap of one bar brings it back to one per bar.
    cfg = _seq_cfg(strobe_placement="half_bars", strobe_min_gap_bars=1)
    starts = [e.step + 1 for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 8) if e.kind == "strobe"]
    assert len(starts) == 8 and all(b - a >= 16 for a, b in zip(starts, starts[1:]))
    # Longer than half a bar: only the bar-line ones fit, and they never overlap.
    cfg = _seq_cfg(strobe_placement="half_bars", strobe_min_gap_bars=0, strobe_beats=4.0)
    starts = [e.step + 1 for e in _run_sequencer(PulseSequencer(cfg, random.Random(1)), 8) if e.kind == "strobe"]
    assert starts == [16 * bar for bar in range(1, 9)]  # every bar from the second on, back to back


def test_strobe_settings_survive_a_save_and_load():
    cfg = PulseSequencerConfig(
        strobe_enabled=True, strobe_placement="bars", strobe_chance=0.25, strobe_min_gap_bars=12,
        strobe_min_level="peak", strobe_beats=0.5, strobe_max_hz=7.5, strobe_duty=0.35, strobe_brightness=0.8,
        strobe_wave="bezier",
    )
    assert PulseSequencerConfig.from_dict(cfg.to_dict()) == cfg
    assert PulseSequencerConfig.from_dict({}) == PulseSequencerConfig()  # settings saved by an older version


# -- LampWorker ----------------------------------------------------------------------------


class _FakeDevice:
    """Stands in for LampDevice: records what was sent and when."""

    def __init__(self, control_dp=True):
        self.config = SimpleNamespace(name="Fake", ip="10.0.0.1")
        self.status = LampStatus()
        self.sent = []  # (perf_counter, "white" | "colour", payload)
        self._control_dp = control_dp

    def uses_control_dp(self, transition):
        return self._control_dp and transition != "legacy"

    def ensure_white_mode(self):
        pass

    def ensure_colour_mode(self):
        pass

    def set_white(self, brightness_percent, temp_percent, wait_for_ack=False, transition="legacy", under_rgb=None):
        self.sent.append((time.perf_counter(), "white", (round(brightness_percent), round(temp_percent), under_rgb)))
        return 1.0

    def set_color(self, r, g, b, wait_for_ack=False, transition="legacy"):
        self.sent.append((time.perf_counter(), "colour", (r, g, b)))
        return 1.0

    def reconnect(self):
        pass


def _start_worker(device, transitions="direct"):
    worker = LampWorker(device, NetworkConfig(lamp_transitions=transitions, lamp_command_rate_hz=20.0), 0.01)
    worker.start()
    return worker


def _stop(*workers):
    for w in workers:
        w.stop()
    for w in workers:
        w.join(timeout=2.0)
        assert not w.is_alive()


def test_workers_play_a_burst_together_at_its_own_times():
    devices = [_FakeDevice(), _FakeDevice()]
    workers = [_start_worker(d) for d in devices]
    try:
        for w in workers:
            w.set_target(Color(1.0, 0.0, 0.0))
        time.sleep(0.1)  # the plain colour goes out first
        burst = StrobeBurst(start=time.perf_counter() + 0.1, period_s=0.12, on_s=0.06, flashes=3, brightness=0.6)
        for w in workers:
            w.start_strobe(burst)
        for w in workers:
            w.set_target(Color(0.0, 0.0, 1.0))  # the show moves on: not sent on its own, but shown under the flashes
        time.sleep(0.1 + 3 * 0.12 + 0.15)
    finally:
        _stop(*workers)
    expected = [(burst.on_time(i), "white") for i in range(3)] + [(burst.off_time(i), "colour") for i in range(3)]
    expected.sort()
    for device in devices:
        played = [s for s in device.sent if s[0] >= burst.start - 0.02]
        assert [kind for _, kind, _ in played] == ["white", "colour"] * 3
        for (at, kind, payload), (due, _) in zip(played, expected):
            assert abs(at - due) < 0.04, (kind, at - due)
            if kind == "white":
                assert payload == (60, 100, (0, 0, 255))  # cool white over the current colour
            else:
                assert payload == (0, 0, 255)
        # Nothing was sent between the burst being handed over and its first flash.
        assert not [s for s in device.sent if burst.start - 0.09 < s[0] < burst.start - 0.02]
    # Both lamps sent every on/off at (nearly) the same instant.
    for a, b in zip(*[[s for s in d.sent if s[0] >= burst.start - 0.02] for d in devices]):
        assert abs(a[0] - b[0]) < 0.04


def test_worker_plays_the_brightness_wave():
    device = _FakeDevice()
    worker = _start_worker(device)
    try:
        worker.start_strobe(StrobeBurst(time.perf_counter() + 0.03, 0.06, 0.03, 4, 0.9, wave="linear"))
        time.sleep(0.03 + 4 * 0.06 + 0.1)
    finally:
        _stop(worker)
    brightness = [payload[0] for _, kind, payload in device.sent if kind == "white"]
    assert brightness == [30, 90, 90, 30]  # percent: dim, full, full, dim


def test_worker_returns_to_the_show_after_a_burst():
    device = _FakeDevice()
    worker = _start_worker(device)
    try:
        worker.set_target(Color(1.0, 0.0, 0.0))
        worker.start_strobe(StrobeBurst(time.perf_counter() + 0.03, 0.08, 0.04, 2, 0.5))
        time.sleep(0.3)
        worker.set_target(Color(0.0, 1.0, 0.0))
        time.sleep(0.2)
    finally:
        _stop(worker)
    assert device.sent[-1][1:] == ("colour", (0, 255, 0))
    assert worker._strobe is None


def test_worker_skips_flashes_it_is_too_late_for():
    device = _FakeDevice()
    worker = _start_worker(device)
    try:
        # Handed over 1.5 periods late: the first two flashes are gone, the third still plays on time.
        burst = StrobeBurst(time.perf_counter() - 0.18, 0.12, 0.06, 3, 0.5)
        worker.start_strobe(burst)
        time.sleep(0.3)
    finally:
        _stop(worker)
    assert [kind for _, kind, _ in device.sent] == ["white", "colour"]
    assert abs(device.sent[0][0] - burst.on_time(2)) < 0.04


@pytest.mark.parametrize("transitions, control_dp", [("gradient", True), ("legacy", True), ("direct", False)])
def test_worker_ignores_a_burst_without_instant_transitions(transitions, control_dp):
    device = _FakeDevice(control_dp=control_dp)
    worker = _start_worker(device, transitions)
    try:
        worker.start_strobe(StrobeBurst(time.perf_counter() + 0.02, 0.06, 0.03, 2, 0.5))
        time.sleep(0.25)
    finally:
        _stop(worker)
    assert device.sent == []
    assert worker._strobe is None


# -- VisualizationEngine ---------------------------------------------------------------------

PERIOD = 0.5  # 120 BPM
TICK = 1.0 / 30.0
DEVICES = ["a", "b", "c", "d"]


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def perf_counter(self):
        return self.now

    def time(self):
        return self.now


class _FakeLampManager:
    def __init__(self):
        self.strobes = []  # (time handed over, {device_id: StrobeBurst})
        self.white_calls = []
        self.clock = None

    def selected_device_ids(self):
        return list(DEVICES)

    def push_colors(self, colors):
        pass

    def push_white_targets(self, targets):
        self.white_calls.append((self.clock.now, dict(targets)))

    def start_strobe(self, bursts):
        self.strobes.append((self.clock.now, dict(bursts)))


def _make_engine(monkeypatch, transitions="direct", **strobe):
    clock = _FakeClock()
    monkeypatch.setattr(ve_module.time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(ve_module.time, "time", clock.time)

    def kick_energy(frame, lo, hi):
        phase = ((clock.now - 1000.0) / PERIOD) % 1.0
        return 0.9 if min(phase, 1.0 - phase) * PERIOD < TICK / 2 else 0.4

    monkeypatch.setattr(ve_module, "band_energy", kick_energy)

    config = AppConfig()
    config.color_mapping.mode = "beat_sync"
    config.rhythm.shared_clock = True
    config.rhythm.lead_ms = 0.0
    config.network.lamp_transitions = transitions
    config.network.lamp_command_rate_hz = 20.0
    bs = config.color_mapping.beat_sync
    bs.dark_pulse_enabled = False
    bs.white_pulse_enabled = True
    config.chase.enabled = False
    config.group_switch.enabled = False
    sq = config.sequencer
    sq.enabled = True
    sq.white_pattern = "sixteenths"
    sq.white_density = 1.0
    sq.dark_pattern = "off"
    sq.phrase_accent = False
    sq.fills = False
    sq.strobe_enabled = True
    sq.strobe_placement = "bars"
    sq.strobe_chance = 1.0
    sq.strobe_min_gap_bars = 2
    sq.strobe_min_level = "calm"
    for name, value in strobe.items():
        setattr(sq, name, value)
    for i, dev in enumerate(DEVICES):
        config.per_lamp_effects[dev] = PerLampEffect(device_id=dev, chase_order=i, effect_group=i % 2)
    config.per_lamp_effects["d"].white_pulse_brightness_mult = 0.5

    lamps = _FakeLampManager()
    lamps.clock = clock
    engine = VisualizationEngine(config, AudioCapture(), lamps)
    engine.running = True
    engine._raw_frame = object()
    return engine, clock, lamps


def _run_engine(engine, clock, seconds):
    for _ in range(int(seconds / TICK)):
        engine.tick_visual()
        clock.now += TICK


def test_engine_hands_every_lamp_the_same_burst_on_the_beat(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch)
    _run_engine(engine, clock, 20.0)
    assert len(lamps.strobes) >= 3
    for handed_over, bursts in lamps.strobes:
        assert set(bursts) == set(DEVICES)
        first = bursts["a"]
        # 120 BPM, 20 commands/s: 16ths, 4 flashes of 62.5 ms on / 62.5 ms off.
        assert first.flashes == 4
        assert first.period_s == pytest.approx(PERIOD / 4, rel=0.05)
        assert first.on_s == pytest.approx(first.period_s / 2)
        assert first.temp == 1.0 and first.brightness == pytest.approx(0.6)
        for burst in bursts.values():
            assert (burst.start, burst.period_s, burst.on_s, burst.flashes) == (
                first.start, first.period_s, first.on_s, first.flashes)
        assert bursts["d"].brightness == pytest.approx(0.3)  # the per-lamp white multiplier
        # Handed over ahead of time, and starting on a beat (the kicks are at multiples of PERIOD).
        assert 0.0 < first.start - handed_over <= PERIOD / 4 + 1e-6
        off_beat = ((first.start - 1000.0) / PERIOD + 0.5) % 1.0 - 0.5
        assert abs(off_beat) < 0.12, off_beat
    starts = [b["a"].start for _, b in lamps.strobes]
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 2 * 4 * PERIOD - 0.2  # the minimum gap of 2 bars


def test_engine_keeps_other_white_flashes_out_of_the_burst(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch)
    _run_engine(engine, clock, 20.0)
    assert lamps.strobes and lamps.white_calls  # the sequencer's own white flashes do run otherwise
    for _, bursts in lamps.strobes:
        burst = bursts["a"]
        during = [t for t, targets in lamps.white_calls if targets and burst.start <= t < burst.end]
        assert not during


def test_engine_plays_no_strobe_without_direct_transitions(monkeypatch):
    for transitions in ("gradient", "legacy"):
        engine, clock, lamps = _make_engine(monkeypatch, transitions=transitions)
        _run_engine(engine, clock, 12.0)
        assert lamps.strobes == []
        assert engine.trigger_strobe_test().startswith("Not played")


def test_engine_shows_the_strobe_in_the_preview_colours(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch)
    lit, dark = [], []
    for _ in range(int(20.0 / TICK)):
        engine.tick_visual()
        if lamps.strobes:
            burst = lamps.strobes[-1][1]["a"]
            if burst.start <= clock.now < burst.end:
                (lit if burst.is_on(clock.now) else dark).append(min(engine.latest_lamp_colors["a"].to_rgb255()))
        clock.now += TICK
    assert lit and dark
    assert min(lit) > max(dark)  # white on top lifts every channel


def test_test_button_plays_a_burst_right_away(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, strobe_enabled=False, strobe_beats=0.5)
    message = engine.trigger_strobe_test()  # no tempo yet: as if at 120 BPM
    assert message.startswith("Played 2 flashes at 8.0 per second")
    (_, bursts), = lamps.strobes
    assert set(bursts) == set(DEVICES)
    assert bursts["a"].start == pytest.approx(clock.now + 0.15)


def test_engine_plays_long_bursts(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, strobe_beats=2.0)
    _run_engine(engine, clock, 20.0)
    assert lamps.strobes
    for _, bursts in lamps.strobes:
        burst = bursts["a"]
        assert burst.flashes == 8 and burst.period_s == pytest.approx(PERIOD / 4, rel=0.05)
        assert burst.end - burst.start == pytest.approx(2 * PERIOD - burst.period_s / 2, rel=0.05)
        assert not [t for t, targets in lamps.white_calls if targets and burst.start <= t < burst.end]


def test_test_button_says_when_the_command_rate_holds_the_rate_down(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, strobe_max_hz=15.0, strobe_beats=4.0)
    message = engine.trigger_strobe_test()  # 120 BPM, 20 commands/s: 16ths, though 15/s would allow 16th triplets
    assert message.startswith("Played 16 flashes at 8.0 per second")
    assert "would allow 12.0 per second" in message and "at least 24/s (now 20/s)" in message
    engine.config.network.lamp_command_rate_hz = 30.0
    message = engine.trigger_strobe_test()
    assert message.startswith("Played 24 flashes at 12.0 per second") and "would allow" not in message


def test_engine_hands_over_the_brightness_wave(monkeypatch):
    engine, clock, lamps = _make_engine(monkeypatch, strobe_beats=2.0, strobe_wave="bezier")
    _run_engine(engine, clock, 20.0)
    assert lamps.strobes
    for _, bursts in lamps.strobes:
        assert all(b.wave == "bezier" for b in bursts.values())
        levels = [bursts["a"].brightness_at(i) for i in range(bursts["a"].flashes)]
        assert max(levels) == pytest.approx(0.6) and levels[0] < 0.1 and levels[-1] < 0.1
        # The per-lamp white multiplier scales the whole wave.
        assert bursts["d"].brightness_at(3) == pytest.approx(0.5 * bursts["a"].brightness_at(3))
    assert AppConfig().sequencer.strobe_wave == "off"  # an option, not the default


def test_strobe_also_plays_with_smooth_transitions(monkeypatch):
    """"smooth" only softens small steps; a strobe's flashes still jump."""
    device = _FakeDevice()
    worker = _start_worker(device, "smooth")
    try:
        worker.start_strobe(StrobeBurst(time.perf_counter() + 0.03, 0.06, 0.03, 2, 0.5))
        time.sleep(0.25)
    finally:
        _stop(worker)
    assert [kind for _, kind, _ in device.sent] == ["white", "colour", "white", "colour"]

    engine, clock, lamps = _make_engine(monkeypatch, transitions="smooth")
    _run_engine(engine, clock, 12.0)
    assert lamps.strobes
    assert engine.trigger_strobe_test().startswith("Played")
