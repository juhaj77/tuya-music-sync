"""Which layers run in which colour mode - one place for the rules the engine
follows and the UI greys settings out by, so a setting is shown as unused
exactly when the engine ignores it.

Pure functions of the config, no Qt and no audio: the Visualizer tab's Quick
Controls and the Color Mapping tab import them as well as the engine.
"""
from __future__ import annotations

from typing import Optional

from ..config.schema import AppConfig

# The modes that flash on beats of their own - the ones the Rhythm box's
# "follow the shared clock" switch is about.
BEAT_MODES = ("beat_sync", "beat_sync_white")


def sequencer_enabled_for(config: AppConfig, mode: Optional[str] = None) -> bool:
    """Whether the pulse sequencer is switched on for `mode` (default: the
    current colour mode). It runs in every colour mode; in Beat Sync and
    Beat Sync White only while they follow the shared beat clock, so their
    beat flashes and the sequencer's pulses go by the same beats. Even then it
    places nothing until the clock has locked onto the tempo (Lock to tempo)."""
    mode = mode or config.color_mapping.mode
    return config.sequencer.enabled and (mode not in BEAT_MODES or config.rhythm.shared_clock)


def sequencer_dark_only(mode: str) -> bool:
    """Beat Sync White drives the lamps' white LEDs already, so it takes only
    the sequencer's dark pulses - no white flashes, strobes or colour sweeps."""
    return mode == "beat_sync_white"


def clock_in_use(config: AppConfig, mode: Optional[str] = None) -> bool:
    """Whether anything follows the shared beat clock in `mode` (default: the
    current one), i.e. whether the engine runs it: Beat Sync / Beat Sync
    White following it, the pulse sequencer, Chase / Group Switch on speed
    source 'clock'."""
    mode = mode or config.color_mapping.mode
    return (
        (config.rhythm.shared_clock and mode in BEAT_MODES)
        or sequencer_enabled_for(config, mode)
        or (config.chase.enabled and config.chase.sync_mode == "clock")
        or (config.group_switch.enabled and config.group_switch.sync_mode == "clock")
    )
