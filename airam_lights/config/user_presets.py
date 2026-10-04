"""User-saved presets: snapshots of the full visualization setup.

A preset captures the color mapping, the Chase and Group Switch overlays'
global settings, the rhythm/sequencer settings, the lamp command rate and
transitions (how smooth fades and hue glides can look depends on them) AND
each lamp's place in those overlays (chase order, chase dwell, Group Switch
group) - all of it, so loading a preset later fully restores the look, not
just the color mode.
"""
from __future__ import annotations

from .schema import (
    AppConfig,
    ChaseEffectConfig,
    ColorMappingConfig,
    GroupSwitchEffectConfig,
    NetworkConfig,
    PerLampEffect,
    PulseSequencerConfig,
    RhythmConfig,
)

# The network settings that shape the look; the rest of NetworkConfig is
# about the connection, not the show.
_NETWORK_KEYS = ("lamp_command_rate_hz", "lamp_transitions")

# The per-lamp fields that define a lamp's place in Chase / Group Switch.
_LAYOUT_DEFAULTS = {"chase_order": None, "chase_dwell_mult": 1.0, "effect_group": None}


def snapshot_preset(cfg: AppConfig) -> dict:
    return {
        "color_mapping": cfg.color_mapping.to_dict(),
        "chase": cfg.chase.to_dict(),
        "group_switch": cfg.group_switch.to_dict(),
        "rhythm": cfg.rhythm.to_dict(),
        "sequencer": cfg.sequencer.to_dict(),
        "network": {key: getattr(cfg.network, key) for key in _NETWORK_KEYS},
        "per_lamp_layout": {
            device_id: {key: getattr(effect, key) for key in _LAYOUT_DEFAULTS}
            for device_id, effect in cfg.per_lamp_effects.items()
            if any(getattr(effect, key) != default for key, default in _LAYOUT_DEFAULTS.items())
        },
    }


def _effect(cfg: AppConfig, device_id: str) -> PerLampEffect:
    eff = cfg.per_lamp_effects.get(device_id)
    if eff is None:
        eff = PerLampEffect(device_id=device_id)
        cfg.per_lamp_effects[device_id] = eff
    return eff


def apply_preset(cfg: AppConfig, data: dict) -> None:
    """Writes a saved preset into `cfg` in place."""
    if "color_mapping" not in data:
        # Backward compatibility: presets saved before the Chase overlay
        # existed stored a bare color_mapping dict directly.
        cfg.color_mapping = ColorMappingConfig.from_dict(data)
        return

    cfg.color_mapping = ColorMappingConfig.from_dict(data["color_mapping"])
    cfg.chase = ChaseEffectConfig.from_dict(data.get("chase", {}))
    # Presets saved before these existed keep the current settings.
    if "group_switch" in data:
        cfg.group_switch = GroupSwitchEffectConfig.from_dict(data["group_switch"])
    if "rhythm" in data:
        cfg.rhythm = RhythmConfig.from_dict(data["rhythm"])
    if "sequencer" in data:
        cfg.sequencer = PulseSequencerConfig.from_dict(data["sequencer"])
    if "network" in data:
        network = NetworkConfig.from_dict({**cfg.network.to_dict(), **data["network"]})
        for key in _NETWORK_KEYS:
            setattr(cfg.network, key, getattr(network, key))

    if "per_lamp_layout" in data:
        # The preset holds the whole layout: lamps it doesn't list were in
        # neither Chase nor Group Switch when it was saved.
        layout = data["per_lamp_layout"]
        for device_id in set(cfg.per_lamp_effects) | set(layout):
            values = layout.get(device_id, {})
            effect = _effect(cfg, device_id)
            for key, default in _LAYOUT_DEFAULTS.items():
                setattr(effect, key, values.get(key, default))
    else:
        # Older presets only stored the chase order of lamps that had one.
        for device_id, chase_order in data.get("per_lamp_chase_orders", {}).items():
            _effect(cfg, device_id).chase_order = chase_order
