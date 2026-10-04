"""PulseSequencer: patterns on the 16th grid, group walk, doubles, phrase
fills/breaths/accents, loudness-driven density and drop re-alignment."""
import random

from airam_lights.config.schema import PulseSequencerConfig
from airam_lights.effects.pulse_sequencer import PulseSequencer

TICK_BEATS = 0.25 / 2  # two ticks per 16th


def _run(seq, bars, energy=0.5, start_beat=0.0, groups=3, bpb=4, now0=0.0):
    """Walks the position forward bar by bar; returns [(position, event)]."""
    out = []
    pos = start_beat
    now = now0
    while pos < start_beat + bars * bpb:
        for e in seq.tick(pos, energy, now, 0.03, groups, bpb):
            out.append((pos, e))
        pos += TICK_BEATS
        now += 0.03
    return out


def _cfg(**kw):
    base = dict(
        enabled=True, white_pattern="beats", white_density=1.0, group_walk="forward", double_chance=0.0,
        dark_pattern="off", phrase_bars=1, fills=False, phrase_accent=False, drop_detection=False,
        white_accent_focus=0.0, white_build=0.0, white_repeat=False,
    )
    base.update(kw)
    return PulseSequencerConfig(**base)


def test_white_walks_forward_through_groups_on_the_beats():
    seq = PulseSequencer(_cfg(), random.Random(1))
    events = _run(seq, 2)
    whites = [(p, e) for p, e in events if e.kind == "white"]
    assert len(whites) >= 7  # every beat (the very first step only syncs)
    for p, _ in whites:
        assert abs(p - round(p)) < 0.2  # on the beats
    groups = [e.group for _, e in whites]
    for a, b in zip(groups, groups[1:]):
        assert b == (a + 1) % 3


def test_offbeats_and_syncopation_land_between_beats():
    for pattern, allowed in (("offbeats", {2}), ("syncopated", {0, 3, 6})):
        seq = PulseSequencer(_cfg(white_pattern=pattern), random.Random(1))
        for p, e in _run(seq, 2):
            step = int(p * 4 + 1e-6)
            modulo = 4 if pattern == "offbeats" else 8
            assert step % modulo in allowed, (pattern, p)


def test_double_repeats_the_same_group_an_eighth_later():
    seq = PulseSequencer(_cfg(white_pattern="downbeats", double_chance=1.0), random.Random(1))
    whites = [(p, e) for p, e in _run(seq, 3, start_beat=0.1) if e.kind == "white"]
    doubles = [(p, e) for p, e in whites if e.reason == "double"]
    assert doubles
    for p, e in doubles:
        original = [x for x in whites if x[1].reason == "pattern" and abs((p - x[0]) - 0.5) < 0.2]
        assert original and original[0][1].group == e.group


def test_phrase_fill_breath_and_accent():
    seq = PulseSequencer(
        _cfg(white_pattern="downbeats", phrase_bars=4, fills=True, phrase_accent=True, dark_pattern="auto"),
        random.Random(1),
    )
    events = _run(seq, 9, start_beat=0.1)
    # Fill: several white flashes in the second half of bars 3 and 7 (0-based).
    fill = [p for p, e in events if e.reason == "fill"]
    assert fill and all(int(p // 4) % 4 == 3 and (p % 4) >= 2 for p in fill)
    # Breath: a dark pulse on the last 16th before each phrase start...
    breaths = [p for p, e in events if e.kind == "dark" and e.reason == "phrase"]
    assert breaths and all(abs((p % 16) - 15.75) < 0.2 for p in breaths)
    # ...then every lamp flashes on the phrase's first beat.
    accents = [e for p, e in events if e.reason == "phrase" and e.kind == "white"]
    assert accents and all(e.group is None for e in accents)


def test_auto_pattern_gets_busier_when_the_music_gets_louder():
    seq = PulseSequencer(_cfg(white_pattern="auto"), random.Random(1))
    _run(seq, 8, energy=0.5)  # history: steady level
    quiet = len([e for _, e in _run(seq, 2, energy=0.3, start_beat=32.1, now0=10) if e.kind == "white"])
    assert seq.level == "calm"
    loud = len([e for _, e in _run(seq, 2, energy=0.9, start_beat=40.1, now0=20) if e.kind == "white"])
    assert seq.level == "peak"
    assert loud > 2 * quiet


def test_a_drop_restarts_the_phrase():
    seq = PulseSequencer(_cfg(white_pattern="off", phrase_bars=8, drop_detection=True), random.Random(1))
    _run(seq, 6, energy=0.2)  # quiet intro, 6 bars
    _run(seq, 1, energy=0.2, start_beat=24.1, now0=30)
    # Loud from bar 7 on: that bar should become the phrase start.
    _run(seq, 1, energy=0.95, start_beat=28.1, now0=40)
    assert seq.phrase_bar == 0


def test_grid_jump_resyncs_without_a_burst():
    seq = PulseSequencer(_cfg(white_pattern="sixteenths"), random.Random(1))
    _run(seq, 1)
    assert seq.tick(40.0, 0.5, 5.0, 0.03, 3, 4) == []  # jumped ahead 36 beats: no flood of events
    assert seq.tick(None, 0.5, 5.1, 0.03, 3, 4) == []  # clock lost lock


def test_metric_weight_follows_the_bar_hierarchy():
    from airam_lights.effects.pulse_sequencer import metric_weight

    w = [metric_weight(step, 16) for step in range(16)]
    assert w[0] == 1.0  # downbeat
    assert w[8] == 0.65  # beat 3, the bar's middle
    assert w[4] == w[12] == 0.35  # beats 2 and 4
    assert w[2] == w[6] == 0.15  # the "ands"
    assert w[1] == w[3] == 0.0  # 16ths
    assert metric_weight(4, 12) == metric_weight(8, 12) == 0.35  # 3/4: no middle beat


def test_phrase_progress_builds_up_and_releases():
    from airam_lights.effects.pulse_sequencer import phrase_progress

    values = [phrase_progress(bar, 4, step, 16) for bar in range(4) for step in range(16)]
    assert values[0] == 1.0  # the phrase's first step: the release
    assert values[1] < 0.05 and all(a < b for a, b in zip(values[1:], values[2:]))
    assert values[-1] > 0.95


def test_events_carry_their_position_in_bar_and_phrase():
    seq = PulseSequencer(_cfg(white_pattern="offbeats", phrase_bars=4), random.Random(1))
    whites = [(p, e) for p, e in _run(seq, 3, start_beat=0.1) if e.kind == "white"]
    assert whites
    for p, e in whites:
        assert e.bar_step == int(p * 4 + 1e-6) % 16
        assert e.steps_per_bar == 16 and e.phrase_bars == 4


def test_flash_shape_follows_beat_weight_loudness_and_phrase():
    from airam_lights.effects.pulse_sequencer import flash_shape

    downbeat = flash_shape("pattern", 0, 16, 0.8, 1.0)
    offbeat = flash_shape("pattern", 2, 16, 0.8, 1.0)
    sixteenth = flash_shape("pattern", 3, 16, 0.8, 1.0)
    assert downbeat[0] > offbeat[0] > sixteenth[0]  # brighter on heavier beats
    assert downbeat[2] > offbeat[2] > sixteenth[2]  # and held longer
    assert abs(downbeat[2] / sixteenth[2] - 4.0) < 1e-9  # 2x vs 0.5x at full amount

    quiet, loud = flash_shape("pattern", 0, 16, 0.1, 1.0), flash_shape("pattern", 0, 16, 0.9, 1.0)
    assert loud[0] > quiet[0] and loud[1] < quiet[1]  # louder: brighter and a sharper attack

    phrase = flash_shape("phrase", 0, 16, 0.5, 1.0)
    assert phrase[0] == 1.0 and phrase[3] > downbeat[3]  # the phrase start fades slowest

    early_fill, late_fill = flash_shape("fill", 8, 16, 0.5, 1.0), flash_shape("fill", 14, 16, 0.5, 1.0)
    assert late_fill[0] > early_fill[0] and late_fill[2] < 1.0  # short, brightening toward the phrase

    assert flash_shape("pattern", 3, 16, 0.2, 0.0) == (1.0, 1.0, 1.0, 1.0)  # amount 0: all identical
    half = flash_shape("pattern", 3, 16, 0.8, 0.5)
    assert sixteenth[2] < half[2] < 1.0


def test_events_carry_their_shape_only_with_dynamics_on():
    for dynamics in (True, False):
        seq = PulseSequencer(_cfg(white_pattern="sixteenths", pulse_dynamics=dynamics), random.Random(1))
        whites = [e for _, e in _run(seq, 2, start_beat=0.1) if e.kind == "white"]
        holds = {round(e.hold, 6) for e in whites}
        assert (len(holds) > 1) == dynamics


# -- thinning: accent focus, phrase build, repeated groove ------------------------------------


def test_white_chance_thins_from_the_light_end_of_the_bar():
    from airam_lights.effects.pulse_sequencer import metric_weight, white_chance

    chances = [white_chance(1.0, metric_weight(s, 16), 1.0, 0.0, 0, 1) for s in range(16)]
    assert chances[0] == 1.0  # the downbeat stays
    assert chances[1] == 0.0  # a 16th between beats goes
    assert chances[8] > chances[4] > chances[2] > chances[1]  # beat 3 > beat 2 > "and" > 16th
    # No focus: every step just the density.
    assert all(white_chance(0.7, metric_weight(s, 16), 0.0, 0.0, 0, 1) == 0.7 for s in range(16))


def test_white_chance_builds_up_through_the_phrase():
    from airam_lights.effects.pulse_sequencer import white_chance

    by_bar = [white_chance(1.0, 0.0, 0.0, 1.0, bar, 8) for bar in range(8)]
    assert by_bar[0] == 0.0  # the phrase starts sparse
    assert by_bar == sorted(by_bar)  # and fills in
    assert by_bar[-1] == 1.0  # the last bar at full density
    assert white_chance(1.0, 0.0, 0.0, 1.0, 0, 1) == 1.0  # a 1-bar phrase has nothing to build


def test_accent_focus_flashes_fewer_sixteenths_but_keeps_the_beats():
    def counts(focus):
        seq = PulseSequencer(_cfg(white_pattern="sixteenths", white_accent_focus=focus), random.Random(3))
        whites = [p for p, e in _run(seq, 16) if e.kind == "white"]
        on_beat = sum(1 for p in whites if int(p * 4 + 1e-6) % 4 == 0)
        return len(whites), on_beat

    total_free, beats_free = counts(0.0)
    total_focused, beats_focused = counts(1.0)
    assert total_focused < total_free * 0.4
    assert beats_focused > total_focused * 0.5  # mostly on the beats now


def test_repeated_groove_is_the_same_in_every_bar_of_a_phrase():
    seq = PulseSequencer(
        _cfg(white_pattern="sixteenths", white_density=0.5, phrase_bars=4, white_repeat=True,
             double_chance=0.3),
        random.Random(5),
    )
    bars = {}
    for p, e in _run(seq, 12):
        if e.kind == "white":
            step = int(p * 4 + 1e-6)
            bars.setdefault(step // 16, set()).add((step % 16, e.reason))
    # Bars 4..7 are one whole phrase: the same steps flash in each of them.
    assert bars[4] and bars[4] == bars[5] == bars[6] == bars[7]
    # The next phrase is a new variation.
    assert bars[8] != bars[4]


def test_repeated_groove_with_build_only_adds_flashes():
    seq = PulseSequencer(
        _cfg(white_pattern="sixteenths", white_density=1.0, phrase_bars=8, white_repeat=True,
             white_accent_focus=0.0, white_build=1.0),
        random.Random(2),
    )
    bars = {}
    for p, e in _run(seq, 16):
        if e.kind == "white":
            step = int(p * 4 + 1e-6)
            bars.setdefault(step // 16, set()).add(step % 16)
    phrase = [bars.get(b, set()) for b in range(8, 16)]
    for earlier, later in zip(phrase, phrase[1:]):
        assert earlier <= later
    assert len(phrase[0]) < len(phrase[-1])


# -- white release curves ---------------------------------------------------------------------


def test_release_curves_start_full_end_dark_and_differ_in_between():
    from airam_lights.effects.pulse_sequencer import release_level

    for curve in ("linear", "ease_in", "ease_out", "ease_in_out"):
        assert release_level(0.0, curve) == 1.0
        assert release_level(1.0, curve) == 0.0
        levels = [release_level(i / 20, curve) for i in range(21)]
        assert levels == sorted(levels, reverse=True)  # only ever fades
    assert release_level(0.5, "linear") == 0.5
    assert release_level(0.5, "ease_in") > 0.5 > release_level(0.5, "ease_out")  # lingers vs. drops
    assert release_level(0.5, "ease_in_out") == 0.5
    assert release_level(0.2, "ease_in_out") > release_level(0.2, "linear")  # lingers at first
    assert release_level(0.8, "ease_in_out") < release_level(0.8, "linear")  # soft landing
    assert release_level(0.5, "something old") == 0.5  # unknown = linear


def test_dynamic_release_curve_follows_the_music():
    from airam_lights.effects.pulse_sequencer import release_curve_for

    loud = 0.8
    assert release_curve_for("phrase", 0, 16, loud) == "ease_in_out"
    assert release_curve_for("fill", 12, 16, loud) == "ease_out"
    assert release_curve_for("double", 2, 16, loud) == "ease_out"
    assert release_curve_for("pattern", 0, 16, loud) == "ease_in"  # downbeat
    assert release_curve_for("pattern", 8, 16, loud) == "ease_in"  # the bar's middle beat
    assert release_curve_for("pattern", 4, 16, loud) == "ease_out"  # beat 2
    assert release_curve_for("pattern", 3, 16, loud) == "ease_out"  # a 16th
    assert release_curve_for("pattern", 0, 16, 0.1) == "ease_in_out"  # quiet: soft breaths


def test_sequencer_white_events_carry_their_release_curve():
    seq = PulseSequencer(
        _cfg(white_pattern="beats", phrase_bars=4, fills=True, phrase_accent=True), random.Random(1)
    )
    whites = [e for _, e in _run(seq, 8, energy=0.5) if e.kind == "white"]
    by_reason = {}
    for e in whites:
        by_reason.setdefault(e.reason, set()).add(e.release_curve)
    assert by_reason["phrase"] == {"ease_in_out"}
    assert by_reason["fill"] == {"ease_out"}


def test_white_walks_backward_through_groups():
    seq = PulseSequencer(_cfg(group_walk="backward"), random.Random(1))
    groups = [e.group for _, e in _run(seq, 2) if e.kind == "white"]
    assert len(groups) >= 7
    assert groups[0] == 2  # starts from the last group
    for a, b in zip(groups, groups[1:]):
        assert b == (a - 1) % 3
