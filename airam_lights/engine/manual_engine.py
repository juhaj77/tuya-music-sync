"""Standalone (no-audio) manual light controller.

Lets you push a static color (RGB, or brightness+temperature for the WHITE
work_mode) to whichever lamps are currently selected, and optionally run the
matching Chase overlay - driven by its own timer instead of audio analysis.
Shares `effects/chase.py` with `engine/visualization_engine.py`, so a fix or
feature there (like lamp-position grouping) applies here too automatically.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from ..color.models import Color, WhiteTarget
from ..config.schema import AppConfig
from ..effects.ambient import AmbientAnimator
from ..effects.chase import ChaseAnimator, WhiteChaseAnimator, get_chase_group_dwell_weights, get_chase_groups
from ..lamps.manager import LampManager

logger = logging.getLogger("airam_lights.engine.manual")

_DEFAULT_CHASE_BACKDROP = Color(0.18, 0.18, 0.18)  # dim gray so a freshly-enabled RGB chase has something to boost
_DEFAULT_WHITE_BACKDROP = WhiteTarget(0.18, 0.5)  # dim, neutral so a freshly-enabled white chase has something to boost


class ManualLightController:
    def __init__(self, config: AppConfig, lamp_manager: LampManager):
        self.config = config
        self.lamp_manager = lamp_manager

        self.mode = "rgb"  # "rgb" | "white" - which kind of target this app is currently driving

        self.base_colors: Dict[str, Color] = {}
        self.base_white_targets: Dict[str, WhiteTarget] = {}
        self.last_colors: Dict[str, Color] = {}
        self.last_white_targets: Dict[str, WhiteTarget] = {}

        self.chase_animator = ChaseAnimator(config.chase)
        self.white_chase_animator = WhiteChaseAnimator(config.white_chase)
        self.ambient_animator = AmbientAnimator(config.ambient_scene)
        self._last_tick_time: Optional[float] = None

        self.restore_last_state()

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self.chase_animator.update_config(config.chase)
        self.white_chase_animator.update_config(config.white_chase)
        self.ambient_animator.update_config(config.ambient_scene)

    # -- persisted static color/white balance --------------------------------------

    def restore_last_state(self) -> None:
        """Re-applies whatever static color/white-balance was last picked
        (see config.schema.ManualStateConfig) - called once at startup so
        the app visually matches where you left it, not just its settings.
        Populates the base for EVERY known lamp (not only the currently
        selected ones), since selection itself can change independently;
        actually pushing to the network still only reaches the selection."""
        ms = self.config.manual_state
        all_ids = list(self.lamp_manager.devices.keys())
        if not all_ids:
            return
        if ms.last_mode == "mix":
            # Set group by group, so each lamp gets back its own - and all of
            # them, not just the current selection (which is only whichever
            # group was picked last).
            self.mode = "white"
            targets = {
                device_id: self._mix_target_from_state(ms.mix_targets[device_id])
                for device_id in all_ids
                if device_id in ms.mix_targets
            }
            self.base_white_targets.update(targets)
            self._send_mix(targets)
        elif ms.last_mode == "white":
            self.mode = "white"
            target = WhiteTarget(ms.last_white_brightness, ms.last_white_temp).clamped()
            for device_id in all_ids:
                self.base_white_targets[device_id] = target
            self._push_white(dict(self.base_white_targets))
        else:
            self.mode = "rgb"
            color = Color(ms.last_color_r, ms.last_color_g, ms.last_color_b).clamped()
            for device_id in all_ids:
                self.base_colors[device_id] = color
            self._push_rgb(dict(self.base_colors))

    def _remember_color(self, color: Color) -> None:
        ms = self.config.manual_state
        ms.last_mode = "rgb"
        ms.last_color_r, ms.last_color_g, ms.last_color_b = color.r, color.g, color.b

    def _remember_white(self, brightness: float, temp: float) -> None:
        ms = self.config.manual_state
        ms.last_mode = "white"
        ms.last_white_brightness, ms.last_white_temp = brightness, temp

    def _forget_mix(self, device_ids) -> None:
        """A plain colour or white applied over a lamp replaces its Color +
        White setting, so a later restart doesn't bring the old mix back."""
        for device_id in device_ids:
            self.config.manual_state.mix_targets.pop(device_id, None)

    @staticmethod
    def _mix_target_from_state(values: Dict[str, float]) -> WhiteTarget:
        under = Color(values["r"], values["g"], values["b"]).clamped()
        return WhiteTarget(values["brightness"], values["temp"], under=under).clamped()

    # -- static color control ---------------------------------------------------

    def set_color_for_selected(self, color: Color) -> None:
        self.mode = "rgb"
        self._remember_color(color)
        selected = self.lamp_manager.selected_device_ids()
        self._forget_mix(selected)
        for device_id in selected:
            self.base_colors[device_id] = color
        self._push_rgb(self.base_colors)

    def set_black_for_selected(self) -> None:
        # Deliberately does NOT update manual_state - "off" is a power
        # state, not a color preference, and shouldn't overwrite the color
        # you'll want restored the next time you turn the lamps on.
        self.mode = "rgb"
        for device_id in self.lamp_manager.selected_device_ids():
            self.base_colors[device_id] = Color.black()
        self._push_rgb(self.base_colors)

    def set_white_for_selected(self, brightness: float, temp: float) -> None:
        self.mode = "white"
        self._remember_white(brightness, temp)
        target = WhiteTarget(brightness, temp).clamped()
        selected = self.lamp_manager.selected_device_ids()
        self._forget_mix(selected)
        for device_id in selected:
            self.base_white_targets[device_id] = target
        self._push_white(self.base_white_targets)

    def set_mix_for(self, device_ids, brightness: float, temp: float, color: Color) -> None:
        """Color + White: the white LEDs (brightness + temperature) and the
        RGB LEDs (`color`) lit at the same time - e.g. blue under cool white,
        red under warm white. Goes to `device_ids` directly, selected or not,
        so a saved lamp group can be set without changing the selection.

        Only bulbs driven through the real-time control datapoint (DP 28,
        NetworkConfig.lamp_transitions other than "legacy") show both at
        once; with "legacy" the bulb shows just the white."""
        self.mode = "white"
        target = WhiteTarget(brightness, temp, under=color.clamped()).clamped()
        ms = self.config.manual_state
        ms.last_mode = "mix"
        targets = {}
        for device_id in device_ids:
            ms.mix_targets[device_id] = {
                "brightness": target.brightness, "temp": target.temp,
                "r": target.under.r, "g": target.under.g, "b": target.under.b,
            }
            self.base_white_targets[device_id] = target
            targets[device_id] = target
        self._send_mix(targets)

    def _send_mix(self, targets: Dict[str, WhiteTarget]) -> None:
        # A white command can't switch the white LEDs fully off (DP 28 keeps
        # them at its 1 % minimum), so white at zero goes as a plain colour.
        colors = {d: t.under for d, t in targets.items() if t.brightness <= 0.0 and t.under is not None}
        whites = {d: t for d, t in targets.items() if d not in colors}
        if colors:
            self.lamp_manager.push_colors(colors)
        if whites:
            self.lamp_manager.push_white_targets(whites)
        self.last_white_targets = {**self.last_white_targets, **targets}

    def _push_rgb(self, colors: Dict[str, Color]) -> None:
        selected = set(self.lamp_manager.selected_device_ids())
        to_send = {device_id: color for device_id, color in colors.items() if device_id in selected}
        if to_send:
            self.last_colors = to_send
            self.lamp_manager.push_colors(to_send)

    def _push_white(self, targets: Dict[str, WhiteTarget]) -> None:
        selected = set(self.lamp_manager.selected_device_ids())
        to_send = {device_id: target for device_id, target in targets.items() if device_id in selected}
        if to_send:
            self.last_white_targets = to_send
            self.lamp_manager.push_white_targets(to_send)

    # -- chase animation (RGB or White, depending on self.mode) ----------------------

    def tick(self) -> None:
        now = time.perf_counter()
        dt = now - self._last_tick_time if self._last_tick_time else 1.0 / 30.0
        self._last_tick_time = now

        # Ambient Scenes take priority when enabled - it's its own animation
        # loop, mutually exclusive with Chase (a lamp can only run one
        # PC-driven animation at a time; the UI keeps this clear via the
        # per-scene Enabled checkboxes).
        if self.config.ambient_scene.enabled:
            self._tick_ambient(dt)
            return

        if self.mode == "white":
            self._tick_white_chase(dt)
        else:
            self._tick_rgb_chase(dt)

    def _tick_ambient(self, dt: float) -> None:
        selected_ids = self.lamp_manager.selected_device_ids()
        if not selected_ids:
            return
        self.ambient_animator.tick(dt)

        if self.ambient_animator.output_kind() == "white":
            self.mode = "white"
            targets: Dict[str, WhiteTarget] = {}
            for device_id in selected_ids:
                effect = self.config.per_lamp_effects.get(device_id)
                offset_ms = effect.phase_offset_ms if effect else 0.0
                targets[device_id] = self.ambient_animator.compute_for_lamp(offset_ms)
            self._push_white(targets)
        else:
            self.mode = "rgb"
            colors: Dict[str, Color] = {}
            for device_id in selected_ids:
                effect = self.config.per_lamp_effects.get(device_id)
                offset_ms = effect.phase_offset_ms if effect else 0.0
                colors[device_id] = self.ambient_animator.compute_for_lamp(offset_ms)
            self._push_rgb(colors)

    def _tick_rgb_chase(self, dt: float) -> None:
        if not self.config.chase.enabled:
            return
        selected_ids = self.lamp_manager.selected_device_ids()
        groups = get_chase_groups(self.config.per_lamp_effects, selected_ids)
        if len(groups) < 2:
            return
        for device_id in selected_ids:
            self.base_colors.setdefault(device_id, _DEFAULT_CHASE_BACKDROP)
        dwell_weights = get_chase_group_dwell_weights(self.config.per_lamp_effects, groups)
        self.chase_animator.tick(dt, num_positions=len(groups), dwell_weights=dwell_weights)  # no audio -> always constant speed
        colors = self.chase_animator.apply(dict(self.base_colors), groups)
        self._push_rgb(colors)

    def _tick_white_chase(self, dt: float) -> None:
        if not self.config.white_chase.enabled:
            return
        selected_ids = self.lamp_manager.selected_device_ids()
        groups = get_chase_groups(self.config.per_lamp_effects, selected_ids)
        if len(groups) < 2:
            return
        for device_id in selected_ids:
            self.base_white_targets.setdefault(device_id, _DEFAULT_WHITE_BACKDROP)
        dwell_weights = get_chase_group_dwell_weights(self.config.per_lamp_effects, groups)
        self.white_chase_animator.tick(dt, num_positions=len(groups), dwell_weights=dwell_weights)
        targets = self.white_chase_animator.apply(dict(self.base_white_targets), groups)
        self._push_white(targets)
