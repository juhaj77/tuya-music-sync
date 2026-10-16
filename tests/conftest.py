"""Shared test setup."""
import random

import pytest

from airam_lights.effects import pulse_sequencer


@pytest.fixture(autouse=True)
def _seeded_random(monkeypatch):
    """Every test starts from the same random state. The engine rolls its
    chances with the global `random`, and a PulseSequencer made without an
    rng of its own (as the engine makes it) gets a fresh one seeded by the
    OS - so a test's outcome depended on how many numbers earlier tests had
    drawn, or on nothing at all, and failed now and then for no reason of its
    own. Both start from a fixed seed here."""
    random.seed(1234)
    original_init = pulse_sequencer.PulseSequencer.__init__

    def seeded_init(self, config, rng=None):
        original_init(self, config, rng if rng is not None else random.Random(5678))

    monkeypatch.setattr(pulse_sequencer.PulseSequencer, "__init__", seeded_init)
    yield
