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
