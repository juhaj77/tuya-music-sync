"""The pulses, shared by every colour mode: the Dark pulses, the White
pulses and the Pulse sequencer (with its Strobe, Flash strobe, Color sweep
and Dark sweep) - one set of settings for every mode, so a mode can be tried
without tuning them all over again. On the Global Effects tab.

What the active colour mode doesn't use is greyed out, and what it never has
(Beat Sync's own per-beat probability, trigger and lamps) hidden - for the
mode picked on the Visualizer tab, so the Global Effects tab refreshes it
whenever it's shown (update_states).

The Dark and White pulses' look belongs to Beat Sync's config (they're its
pulses' look), which the sequencer uses in every mode."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...color.models import STROBE_WAVES
from ...config.schema import (
    INSTANT_TRANSITIONS,
    PULSE_TRIGGERS,
    RELEASE_CURVES,
    WHITE_TEMP_MODES,
    AppConfig,
    PulseSequencerConfig,
)
from ...effects.chase import get_chase_groups, get_group_switch_groups
from ...effects.pulse_sequencer import (
    COLOR_SWEEP_COLORS,
    COLOR_SWEEP_DIRECTIONS,
    COLOR_SWEEP_PLACEMENTS,
    DARK_PATTERNS,
    FLASH_STROBE_PLACEMENTS,
    GROUP_WALKS,
    LEVELS,
    STROBE_PLACEMENTS,
    WHITE_PATTERNS,
)
from ...engine.rules import BEAT_MODES, sequencer_dark_only
from ..controller import AppController
from ..widgets.hue_slider import HueSlider
from ..widgets.musical import PHRASE_LENGTHS, choice_combo, inactive_note, labeled_combo, set_active, show_note
from ..widgets.param_slider import FloatSlider
from ..widgets.section import CollapsibleSection

MODE_NAMES = {
    "beat_sync": "Beat Sync",
    "beat_sync_white": "Beat Sync White",
    "peak_flash": "Peak Flash",
    "rgb_freq": "RGB Frequency",
    "hsv_music": "HSV Music",
    "custom": "Custom",
}


def sequencer_state(config: AppConfig, mode: str) -> "tuple[bool, bool, str]":
    """The pulse sequencer in `mode` (see engine.rules): (can it run there,
    does it place the pulses there - once the clock locks, why not)."""
    rh = config.rhythm
    if mode in BEAT_MODES and not rh.shared_clock:
        return False, False, (
            f"The sequencer needs {MODE_NAMES.get(mode, mode)} to follow the shared beat clock - turn on "
            "'Beat Sync (and Beat Sync White) follow the shared clock' in the Rhythm box at the top."
        )
    if not rh.tempo_lock:
        return False, False, (
            "The sequencer places the pulses on the beat clock's grid - turn on Lock to tempo in the "
            "Rhythm box at the top."
        )
    if not config.sequencer.enabled:
        return True, False, "The sequencer is off."
    return True, True, ""


class PulsesPanel(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        bs = controller.config.color_mapping.beat_sync
        pulse_layout = QVBoxLayout(self)
        pulse_layout.setContentsMargins(0, 0, 0, 0)

        def pulse_section(title: str, key: str, **kw) -> CollapsibleSection:
            section = CollapsibleSection(title, key=f"beat/{key}", **kw)
            pulse_layout.addWidget(section)
            return section

        dark_section = pulse_section(
            "Dark pulses", "dark", checkable=True, checked=bs.dark_pulse_enabled,
            tooltip="A brief dip toward black - a rhythm-synced pause before the next hit. In Beat Sync on a "
            "random share of beats (probability and trigger below), or wherever the Pulse sequencer puts "
            "them; in the other modes only from the Pulse sequencer (Beat Sync White's own per-beat ones "
            "are on its Color Mapping page). The look below is the same in every mode. White pulses are unaffected "
            "either way.",
        )
        layout = dark_section.content_layout()
        self.beat_dark_enabled_checkbox = dark_section.toggle
        self.beat_dark_enabled_checkbox.toggled.connect(self._on_pulse_changed)
        self.beat_dark_trigger_combo, self.beat_dark_trigger_row = self._trigger_combo(
            layout, "Dark pulse on:", bs.dark_pulse_trigger
        )
        self.beat_dark_note = dark_section.note

        self.beat_dark_prob_slider = FloatSlider(
            "Dark pulse probability", 0.0, 1.0, bs.dark_pulse_probability, decimals=2,
            tooltip="Chance a given beat gets this dark dip instead of flashing immediately - "
            "0 = never, 1 = every beat.",
        )
        self.beat_dark_duration_slider = FloatSlider(
            "Dark pulse duration", 10.0, 500.0, bs.dark_pulse_duration_ms, decimals=0, suffix=" ms",
            tooltip="How long brightness is held down near black before the delayed flash actually happens.",
        )
        self.beat_dark_depth_slider = FloatSlider(
            "Dark pulse depth", 0.0, 1.0, bs.dark_pulse_depth, decimals=2,
            tooltip="How far toward black the dip goes - 1.0 = fully black for the duration above, "
            "lower = a partial dim instead of a total blackout.",
        )
        self.beat_dark_attack_slider = FloatSlider(
            "Dark pulse attack", 1.0, 300.0, bs.dark_pulse_attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast brightness snaps down into the dip when the pulse starts - low = an "
            "instant cut to black, right on the beat.",
        )
        self.beat_dark_release_slider = FloatSlider(
            "Dark pulse release", 10.0, 1000.0, bs.dark_pulse_release_ms, decimals=0, suffix=" ms",
            tooltip="How slowly brightness eases back out of the dip once the pulse's duration ends - "
            "higher means a longer visible fade back before/into the delayed flash.",
        )
        self.beat_dark_desaturate_slider = FloatSlider(
            "Dark pulse desaturate", 0.0, 1.0, bs.dark_pulse_desaturate, decimals=2,
            tooltip="The colour's saturation moves with the dip: it falls as the light dims into the dip "
            "(over the attack) and comes back as the light returns (over the release). 0 = the colour "
            "keeps its saturation. 1 = the saturation reaches zero just as the brightness does (with "
            "depth 1) - the colour washes out to white as it fades away, and comes back from white. "
            "Not in Beat Sync White (white has no saturation).",
        )
        for w in (
            self.beat_dark_prob_slider,
            self.beat_dark_duration_slider,
            self.beat_dark_depth_slider,
            self.beat_dark_attack_slider,
            self.beat_dark_release_slider,
            self.beat_dark_desaturate_slider,
        ):
            w.valueChanged.connect(self._on_pulse_changed)
            layout.addWidget(w)

        white_section = pulse_section(
            "White pulses", "white", checkable=True, checked=bs.white_pulse_enabled,
            tooltip="A brief flash of the lamp's own white LEDs - e.g. a hi-hat/cymbal accent. In Beat Sync on "
            "a subset of beats (probability and trigger below), or wherever the Pulse sequencer puts them; "
            "in Peak Flash, RGB Frequency, HSV Music and Custom only from the Pulse sequencer; never in "
            "Beat Sync White, which is white already. Always the real white LEDs, never white mixed from "
            "RGB, so the colored LEDs keep all their output for color. The look below is the same in every "
            "mode. Independent of dark pulses - both, either, or neither can happen on a hit.",
        )
        layout = white_section.content_layout()
        self.beat_white_pulse_enabled_checkbox = white_section.toggle
        self.beat_white_pulse_enabled_checkbox.toggled.connect(self._on_pulse_changed)

        white_target_row = QHBoxLayout()
        # "True white lamps" is for Beat Sync's per-beat flashes only - its own
        # box, so it can go on the other modes' pages (see _update_pulse_states).
        self.beat_white_target_box = QWidget()
        target_box_layout = QHBoxLayout(self.beat_white_target_box)
        target_box_layout.setContentsMargins(0, 0, 0, 0)
        target_box_layout.addWidget(QLabel("True white lamps:"))
        self.beat_white_pulse_target_combo = QComboBox()
        self.beat_white_pulse_target_combo.addItems(["all", "chase", "group", "rotate"])
        self.beat_white_pulse_target_combo.setCurrentText(bs.white_pulse_target)
        self.beat_white_pulse_target_combo.setToolTip(
            "Which lamps a white flash lands on. all "
            "(default): every selected lamp at once. chase: the lamps the Chase highlight is on at that "
            "moment - if Chase moves more than one lamp between flashes, the white skips lamps. group: "
            "Group Switch's currently active group. rotate: the white gets its own rotation through the "
            "Chase order ('Chase order' on the Devices & Setup tab) - every flash moves exactly one lamp on, "
            "so none is skipped however fast Chase moves; it turns the same way as Chase. The lamps are "
            "picked when the flash starts and kept for its whole duration. With too few positions for "
            "the chosen option, every lamp flashes."
        )
        self.beat_white_pulse_target_combo.currentTextChanged.connect(self._on_pulse_changed)
        target_box_layout.addWidget(self.beat_white_pulse_target_combo)
        target_box_layout.addSpacing(16)
        white_target_row.addWidget(self.beat_white_target_box)
        white_target_row.addWidget(QLabel("Rotating:"))
        self.beat_white_rotators_combo = choice_combo(
            [(1, "1 lamp"), (2, "2 opposite lamps"), (3, "3 evenly spaced"), (4, "4 evenly spaced")],
            bs.white_pulse_rotators,
            "How many lamps flash white at once as the white rotates, evenly spaced around the loop - "
            "2 = two lamps on opposite sides, 3 = a third of the loop apart. Used by 'rotate' above and "
            "by the Pulse sequencer's walk.",
        )
        self.beat_white_rotators_combo.currentIndexChanged.connect(self._on_pulse_changed)
        white_target_row.addWidget(self.beat_white_rotators_combo)
        white_target_row.addStretch(1)
        layout.addLayout(white_target_row)
        self.beat_white_rotators_note = inactive_note()
        layout.addWidget(self.beat_white_rotators_note)
        self.beat_white_trigger_combo, self.beat_white_trigger_row = self._trigger_combo(
            layout, "White pulse on:", bs.white_pulse_trigger
        )
        self.beat_white_note = white_section.note

        self.beat_white_pulse_prob_slider = FloatSlider(
            "White pulse probability", 0.0, 1.0, bs.white_pulse_probability, decimals=2,
            tooltip="Chance a given beat gets a white flash - 0 = never, 1 = every beat.",
        )
        self.beat_white_pulse_duration_slider = FloatSlider(
            "White pulse duration", 10.0, 500.0, bs.white_pulse_duration_ms, decimals=0, suffix=" ms",
            tooltip="How long the lamp is held on its white LEDs before switching back to color.",
        )
        self.beat_white_pulse_attack_slider = FloatSlider(
            "White pulse attack", 1.0, 300.0, bs.white_pulse_attack_ms, decimals=0, suffix=" ms",
            tooltip="How long the white flash takes to rise to full brightness - low = a hard flash right "
            "on the beat, higher = it swells in. (With 'legacy' Lamp transitions the bulb fades on its own "
            "and this only sets the timing.)",
        )
        self.beat_white_pulse_release_slider = FloatSlider(
            "White pulse release", 10.0, 1000.0, bs.white_pulse_release_ms, decimals=0, suffix=" ms",
            tooltip="How long the white flash takes to fade out after its hold, before the lamp is back on "
            "color. Attack + duration + release is the whole flash. (With 'legacy' Lamp transitions the "
            "bulb fades on its own and this only sets the timing.)",
        )
        self.beat_white_pulse_white_brightness_slider = FloatSlider(
            "White pulse brightness", 0.0, 1.0, bs.white_pulse_white_brightness, decimals=2,
            tooltip="Brightness of the white LEDs during the flash. 1.0 = strongest possible intensity.",
        )
        self.beat_white_pulse_cool_ratio_slider = FloatSlider(
            "White pulse cool ratio", 0.0, 1.0, bs.white_pulse_cool_ratio, decimals=2,
            tooltip="Each flash independently rolls warm vs "
            "cool white using this as the chance of landing on cool, so flashes vary beat to beat "
            "instead of always looking the same. 0.0 = always warm, 1.0 = always cool, 0.5 (default) = "
            "a roughly even, unpredictable mix. The choice is made once per flash and held for its "
            "whole duration, not re-rolled mid-flash.",
        )
        for w in (
            self.beat_white_pulse_prob_slider,
            self.beat_white_pulse_duration_slider,
            self.beat_white_pulse_attack_slider,
            self.beat_white_pulse_release_slider,
            self.beat_white_pulse_white_brightness_slider,
            self.beat_white_pulse_cool_ratio_slider,
        ):
            w.valueChanged.connect(self._on_pulse_changed)
            layout.addWidget(w)
        self.beat_white_release_curve_combo = labeled_combo(
            layout, "White release curve:", RELEASE_CURVES, bs.white_pulse_release_curve,
            "The shape of the fade over White pulse release - shows best with a longer release "
            "(a few hundred ms). linear: a straight fade. ease_in: starts slowly - the flash lingers "
            "near full, then drops away at the end. ease_out: drops fast, then a long soft tail, like a "
            "struck drum or cymbal. ease_in_out: an S-curve - lingers, falls, then lands softly. "
            "dynamic: picked per flash by where it falls in the music - ease_in_out for the phrase "
            "start and in quiet parts, ease_in (hangs on like a held note) on the heavy beats, "
            "ease_out (percussive) on the lighter ones, the fills and the doubles. "
            "For the Pulse sequencer's flashes, with Lamp transitions other than 'legacy'.",
        )
        self.beat_white_release_curve_combo.currentTextChanged.connect(self._on_pulse_changed)
        self.beat_white_temp_combo = labeled_combo(
            layout, "Warm/cool:", WHITE_TEMP_MODES, bs.white_pulse_temp_mode,
            "How each flash's white is chosen between warm and cool. random: rolled per flash with the "
            "cool ratio above. bar: by the weight of the beat - the downbeat coolest, the bar's middle "
            "beat half-cool, other beats warmer, flashes between beats warmest - so heavy beats stand out "
            "from light ones. alternate: cool, warm, cool, warm. loudness: warm in the quiet parts of the "
            "song, cool in the loud ones. phrase: cools down over each phrase toward its end, the new "
            "phrase's first flash is cool, then back to warm (phrase length: see the Pulse sequencer).",
        )
        self.beat_white_temp_combo.currentTextChanged.connect(self._on_pulse_changed)
        self.beat_white_hybrid_slider = FloatSlider(
            "Hybrid warm/cool", 0.0, 1.0, bs.white_pulse_hybrid_chance, decimals=2,
            tooltip="Chance a flash is a hybrid: its colour temperature glides from one end to the other - "
            "following the flash's light, not the clock: halfway through its light, halfway through the "
            "change, so it happens while the flash is bright. A flash Warm/cool picked cool glides from "
            "cool to warm, a warm one from warm to cool. 0 = never, 1 = every flash. A flash "
            "strobe keeps one temperature. The info panel's timeline shows each flash in its warm/cool, "
            "a hybrid as a gradient.",
        )
        self.beat_white_hybrid_slider.valueChanged.connect(self._on_pulse_changed)
        layout.addWidget(self.beat_white_hybrid_slider)
        self.beat_white_temp_note = inactive_note()
        layout.addWidget(self.beat_white_temp_note)

        pulse_layout.addWidget(self._build_sequencer_box(controller.config.sequencer))
        self.update_states()

    # -- which settings are in use right now ---------------------------------------------

    def update_states(self) -> None:
        """The Dark pulses / White pulses / Pulse sequencer sections, for the
        active colour mode: Beat Sync rolls per-beat pulses of its own too,
        the other modes get only the sequencer's, Beat Sync White only its
        dark ones. What the mode never has is hidden (Beat Sync's per-beat
        probability, trigger and lamps), what it doesn't use right now greyed
        out."""
        cfg = self.controller.config
        bs, rh, sq = cfg.color_mapping.beat_sync, cfg.rhythm, cfg.sequencer
        mode = cfg.color_mapping.mode
        name = MODE_NAMES.get(mode, mode)
        beat_sync = mode == "beat_sync"
        dark_only = sequencer_dark_only(mode)
        shared = rh.shared_clock
        can_run, sequencing, seq_off = sequencer_state(cfg, mode)
        dark, white = bs.dark_pulse_enabled, bs.white_pulse_enabled
        temp_mode = bs.white_pulse_temp_mode
        legacy = cfg.network.lamp_transitions == "legacy"
        dark_only_reason = f"{name} takes only the dark pulses from the sequencer."

        for widget in (self.beat_dark_trigger_row, self.beat_dark_prob_slider, self.beat_white_target_box,
                       self.beat_white_trigger_row, self.beat_white_pulse_prob_slider):
            widget.setVisible(beat_sync)  # Beat Sync's per-beat pulses - no other mode has them

        dark_look = [self.beat_dark_duration_slider, self.beat_dark_depth_slider, self.beat_dark_attack_slider,
                     self.beat_dark_release_slider, self.beat_dark_desaturate_slider]
        white_look = [self.beat_white_pulse_duration_slider, self.beat_white_pulse_attack_slider,
                      self.beat_white_pulse_release_slider, self.beat_white_pulse_white_brightness_slider,
                      self.beat_white_temp_combo, self.beat_white_hybrid_slider]

        if beat_sync:
            seq_reason = "The Pulse sequencer (below) places the pulses."
            no_clock_reason = "Accent/downbeat need the shared beat clock (Rhythm box)."
            set_active([self.beat_dark_enabled_checkbox, self.beat_white_pulse_enabled_checkbox], True, "")
            for enabled, prob, trigger, look, note, kind in (
                (dark, self.beat_dark_prob_slider, self.beat_dark_trigger_combo, dark_look, self.beat_dark_note,
                 "Dark"),
                (white, self.beat_white_pulse_prob_slider, self.beat_white_trigger_combo, white_look,
                 self.beat_white_note, "White"),
            ):
                off_reason = f"{kind} pulses are switched off."
                set_active(look, enabled, off_reason)
                set_active([prob], enabled and not sequencing, off_reason if not enabled else seq_reason)
                set_active(
                    [trigger], enabled and shared and not sequencing,
                    off_reason if not enabled else (seq_reason if sequencing else no_clock_reason),
                )
                if enabled and sequencing:
                    show_note(note, f"{kind} pulses are placed by the Pulse sequencer, so the probability and "
                              "trigger here are greyed out; durations and strength still apply.")
                elif enabled and not shared:
                    show_note(note, "'accent' and 'downbeat' triggers need the shared beat clock (Rhythm box) - "
                              "without it every beat rolls the probability.")
                else:
                    show_note(note, "")
            set_active(
                [self.beat_white_pulse_target_combo], white and not sequencing,
                "White pulses are switched off." if not white else seq_reason + " Its group walk decides the lamps.",
            )
            if white and temp_mode in ("bar", "phrase") and not shared:
                show_note(self.beat_white_temp_note, f"Warm/cool '{temp_mode}' needs the shared beat clock (Rhythm "
                          "box) to know where the bar is - until then each flash is rolled with the cool ratio.")
            else:
                show_note(self.beat_white_temp_note, "")
            white_used = white  # by the per-beat flashes or the sequencer's
            white_off = "" if white else "White pulses are switched off."
            rotating = (bs.white_pulse_target == "rotate") if not sequencing else (sq.group_walk != "all")
        else:
            # Here every pulse comes from the sequencer.
            if sequencing:
                only_seq = ""
            elif can_run:
                only_seq = f"In {name} only the Pulse sequencer (below) places these - it's off."
            else:
                only_seq = f"In {name} only the Pulse sequencer places these. {seq_off}"
            dark_note = only_seq
            if dark_only and not sequencing:
                dark_note = (
                    "In Beat Sync White these shape only the Pulse sequencer's dark pulses (its own per-beat "
                    "ones are on its Color Mapping page). " + seq_off
                )
            set_active([self.beat_dark_enabled_checkbox], sequencing, dark_note)
            set_active(dark_look, sequencing and dark, dark_note or "Dark pulses are switched off.")
            if dark_only:
                set_active([self.beat_dark_desaturate_slider], False, "Not used in Beat Sync White - white has no saturation.")
            show_note(self.beat_dark_note, dark_note)
            if dark_only:
                white_off = "Not used in Beat Sync White - it's white already, and takes only the sequencer's dark pulses."
                set_active([self.beat_white_pulse_enabled_checkbox] + white_look, False, white_off)
                show_note(self.beat_white_note, "Not used in Beat Sync White (it's white already).")
                white_used = False
            else:
                white_off = only_seq or ("" if white else "White pulses are switched off.")
                set_active([self.beat_white_pulse_enabled_checkbox], sequencing, only_seq)
                set_active(white_look, sequencing and white, white_off)
                show_note(self.beat_white_note, only_seq)
                white_used = sequencing and white
            show_note(self.beat_white_temp_note, "")  # the sequencer's flashes always know their place in the bar
            rotating = sq.group_walk != "all"

        # The release curve shapes only the sequencer's flashes (the per-beat ones fade out exponentially).
        set_active(
            [self.beat_white_release_curve_combo], white_used and sequencing and not legacy,
            white_off or (
                "The release curve shapes the Pulse sequencer's flashes (the per-beat ones fade out "
                "exponentially). " + seq_off if not sequencing else
                "With 'legacy' Lamp transitions the bulb fades on its own."
            ),
        )
        set_active(
            [self.beat_white_pulse_cool_ratio_slider], white_used and temp_mode == "random",
            white_off or f"Warm/cool is '{temp_mode}', not random.",
        )
        self._show_rotators_warning(white_used and rotating, sequencing)
        set_active(
            [self.beat_white_rotators_combo], white_used and rotating,
            white_off or (
                "The sequencer's Group walk is 'all' - every lamp flashes." if sequencing else
                "True white lamps isn't 'rotate'."
            ),
        )

        # -- the sequencer --------------------------------------------------------------
        set_active([self.seq_enabled_checkbox], can_run, seq_off)
        if not can_run:
            show_note(self.seq_note, seq_off)
        elif dark_only and sq.enabled:
            show_note(self.seq_note, "Beat Sync White takes only its dark pulses.")
        else:
            show_note(self.seq_note, "")
        white_controls = [
            self.seq_white_combo, self.seq_walk_combo, self.seq_walk_positions_combo, self.seq_white_density_slider,
            self.seq_double_slider, self.seq_focus_slider, self.seq_build_slider, self.seq_repeat_checkbox,
            self.seq_gap_slider, self.seq_dynamics_checkbox,
        ]
        # The phrase, fills, accents and drops place dark pulses too.
        dark_controls = [
            self.seq_dark_combo, self.seq_dark_density_slider, self.seq_dark_length_slider, self.seq_phrase_combo,
            self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox,
        ]
        set_active(white_controls, sequencing and not dark_only, seq_off or dark_only_reason)
        set_active(dark_controls, sequencing, seq_off)
        set_active(
            [self.seq_dynamics_slider], sequencing and not dark_only and sq.pulse_dynamics,
            seq_off or (dark_only_reason if dark_only else "Pulse dynamics is off."),
        )
        if beat_sync and shared and white and temp_mode == "phrase":
            set_active([self.seq_phrase_combo], True, "")  # the per-beat flashes' warm/cool 'phrase' uses it too

        # Strobe: placed by the sequencer, and only with instant lamp transitions.
        direct = cfg.network.lamp_transitions in INSTANT_TRANSITIONS
        not_direct = "The strobe needs Lamp transitions 'direct' or 'smooth' (Color Mapping tab, Global box)."
        extras = sequencing and not dark_only  # the strobes and the sweep: white light and RGB colour
        extras_off = seq_off or (dark_only_reason if dark_only else "")
        set_active([self.seq_strobe_checkbox], extras and direct, extras_off or not_direct)
        set_active(
            [self.seq_strobe_placement_combo, self.seq_strobe_level_combo, self.seq_strobe_chance_slider,
             self.seq_strobe_gap_slider, self.seq_strobe_length_combo, self.seq_strobe_wave_combo,
             self.seq_strobe_brightness_slider, self.seq_strobe_test_button],
            extras and direct and sq.strobe_enabled,
            extras_off or (not_direct if not direct else "The strobe is off."),
        )
        # The rate and on-time are the flash strobe's too.
        set_active(
            [self.seq_strobe_rate_slider, self.seq_strobe_duty_slider],
            extras and direct and (sq.strobe_enabled or sq.flash_strobe_enabled),
            extras_off or (not_direct if not direct else "The strobe and the flash strobe are off."),
        )
        set_active(
            [self.seq_flash_strobe_checkbox], extras and direct and white,
            extras_off or (not_direct if not direct else "White pulses are off."),
        )
        set_active(
            [self.seq_flash_strobe_placement_combo, self.seq_flash_strobe_level_combo,
             self.seq_flash_strobe_chance_slider, self.seq_flash_strobe_gap_slider,
             self.seq_flash_strobe_brightness_slider],
            extras and direct and white and sq.flash_strobe_enabled,
            extras_off or (not_direct if not direct else "White pulses are off." if not white else "Flash strobe is off."),
        )
        # Color sweep: on the RGB colour, so any lamp transitions do.
        set_active([self.seq_sweep_checkbox], extras, extras_off)
        set_active(
            [self.seq_sweep_color_combo, self.seq_sweep_saturation_slider, self.seq_sweep_brightness_slider,
             self.seq_sweep_length_combo, self.seq_sweep_rotators_spin, self.seq_sweep_width_spin,
             self.seq_sweep_direction_combo, self.seq_sweep_placement_combo, self.seq_sweep_level_combo,
             self.seq_sweep_chance_slider, self.seq_sweep_gap_slider, self.seq_sweep_test_button],
            extras and sq.color_sweep_enabled,
            extras_off or "Color sweep is off.",
        )
        set_active(
            [self.seq_sweep_hue_slider], extras and sq.color_sweep_enabled and sq.color_sweep_color == "custom",
            extras_off or ("Color sweep is off." if not sq.color_sweep_enabled else "Sweep colour is 'dynamic'."),
        )
        # Dark sweep: dims the RGB colour, so any lamp transitions do too.
        set_active([self.seq_dark_sweep_checkbox], extras, extras_off)
        set_active(
            [self.seq_dark_sweep_depth_slider, self.seq_dark_sweep_desaturate_slider,
             self.seq_dark_sweep_attack_slider, self.seq_dark_sweep_release_slider,
             self.seq_dark_sweep_length_combo, self.seq_dark_sweep_rotators_spin, self.seq_dark_sweep_width_spin,
             self.seq_dark_sweep_direction_combo, self.seq_dark_sweep_placement_combo,
             self.seq_dark_sweep_level_combo, self.seq_dark_sweep_chance_slider, self.seq_dark_sweep_gap_slider,
             self.seq_dark_sweep_test_button],
            extras and sq.dark_sweep_enabled,
            extras_off or "Dark sweep is off.",
        )
        if extras and not direct:
            show_note(self.seq_strobe_note, "The strobe needs Lamp transitions 'direct' or 'smooth' (Color Mapping "
                      "tab, Global box): only then do the bulbs take a change instantly and show white and colour "
                      "at the same time.")
        else:
            show_note(self.seq_strobe_note, "")

    def _show_rotators_warning(self, rotating: bool, sequencing: bool) -> None:
        """Warns when 'Rotating' covers every position the white walks
        through - then every flash lands on every lamp and nothing walks."""
        cfg = self.controller.config
        selected = self.controller.lamp_manager.selected_device_ids()
        if sequencing and cfg.sequencer.walk_positions != "chase_order":
            positions, kind = get_group_switch_groups(cfg.per_lamp_effects, selected), "Group Switch groups"
        else:
            positions, kind = get_chase_groups(cfg.per_lamp_effects, selected), "Chase positions"
        count = len(positions)
        if rotating and count >= 2 and cfg.color_mapping.beat_sync.white_pulse_rotators >= count:
            show_note(self.beat_white_rotators_note, f"Rotating {cfg.color_mapping.beat_sync.white_pulse_rotators} "
                      f"with {count} {kind}: every flash lands on all of them at once, so the white doesn't "
                      f"walk. Choose fewer than {count}.")
        else:
            show_note(self.beat_white_rotators_note, "")

    # -- building ------------------------------------------------------------------------

    @staticmethod
    def _trigger_combo(layout: QVBoxLayout, label: str, value: str) -> "tuple[QComboBox, QWidget]":
        """A pulse's trigger choice (Beat Sync's per-beat pulses) - and its row,
        which the other modes' pages hide."""
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(label))
        combo = QComboBox()
        combo.addItems(list(PULSE_TRIGGERS))
        combo.setCurrentText(value)
        combo.setToolTip(
            "Which beats may roll this pulse's probability at all. random: every beat. accent: only "
            "the hardest hits. downbeat: only bar starts. accent/downbeat need the shared beat clock "
            "(Rhythm box at the top) - without it they behave like random."
        )
        row.addWidget(combo)
        row.addStretch(1)
        layout.addWidget(row_widget)
        return combo, row_widget

    # -- pulse sequencer ---------------------------------------------------------------------

    def _build_sequencer_box(self, sq: PulseSequencerConfig) -> CollapsibleSection:
        box = CollapsibleSection(
            "Pulse sequencer", key="beat/sequencer", checkable=True, checked=sq.enabled,
            tooltip="White and dark pulses placed on musical positions - rhythm patterns, a white walk "
            "round the lamp groups, phrase fills and breaths, strobes and colour and dark sweeps - in every colour "
            "mode, with the same settings (Beat Sync White takes only the dark pulses). Follows the shared "
            "beat clock: needs Lock to tempo, and in Beat Sync / Beat Sync White 'follow the shared clock' "
            "(Rhythm box).",
        )
        layout = box.content_layout()
        intro = QLabel(
            "Places the white and dark pulses like a lighting operator would: on rhythm patterns in 16th "
            "notes (also between the beats), with the white flash walking from lamp group to lamp group "
            "(the Group Switch groups, 'Effect group' on the Devices & Setup tab), phrase fills and a dark "
            "breath before each new phrase, and busier patterns when the music gets louder. The same in every "
            "colour mode: these settings and the pulses' look (Dark pulses and White pulses above) are "
            "shared, so trying another mode keeps them - Beat Sync White takes only the dark pulses. Follows "
            "the shared beat clock (Rhythm box): needs Lock to tempo, and in Beat Sync and Beat Sync White "
            "'follow the shared clock'. Until the clock has locked, Beat Sync rolls its per-beat pulses "
            "(probability above) and the other modes have none."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.seq_enabled_checkbox = box.toggle
        self.seq_enabled_checkbox.toggled.connect(self._on_sequencer_changed)
        self.seq_note = box.note

        self.seq_white_combo = labeled_combo(
            layout, "White pattern:", WHITE_PATTERNS, sq.white_pattern,
            "Which 16ths of the bar get a white flash. auto: follows the loudness - downbeats when "
            "quiet, every beat in a normal groove, the 3-3-2 syncopation when it gets loud, the gallop "
            "at the loudest. downbeats: bar starts. beats: every beat. offbeats: the 'and' between "
            "beats. syncopated: 3-3-2 (tresillo). gallop: an 8th plus two 16ths. sixteenths: every 16th.",
        )
        self.seq_walk_combo = labeled_combo(
            layout, "Group walk:", GROUP_WALKS, sq.group_walk,
            "Where each white flash goes. forward: the next group each time, so the white travels "
            "around the room. backward: the same the other way round - e.g. against Group Switch's "
            "colors, which move forward unless Reverse is on. pingpong: back and forth. random: any "
            "other group. all: every lamp.",
        )
        self.seq_white_density_slider = FloatSlider(
            "White density", 0.0, 1.0, sq.white_density, decimals=2,
            tooltip="Chance each pattern step actually flashes - below 1 leaves gaps, so the pattern "
            "breathes instead of being mechanical.",
        )
        self.seq_double_slider = FloatSlider(
            "Double chance", 0.0, 1.0, sq.double_chance, decimals=2,
            tooltip="Chance a flash repeats in the same group an 8th later - a quick 'da-dam' in one "
            "spot among the walking flashes.",
        )
        self.seq_focus_slider = FloatSlider(
            "Accent focus", 0.0, 1.0, sq.white_accent_focus, decimals=2,
            tooltip="Thins the white flashes the way a drummer accents: the lightest positions go first - "
            "the 16ths between beats, then the 'ands', then beats 2 and 4, then beat 3 - and the downbeat "
            "stays. 0 = every pattern step equally likely (only White density), 1 = a step keeps only "
            "its weight's share: a 16th never flashes, an 'and' rarely, the downbeat always as often as "
            "White density says. Calms busy patterns (sixteenths, gallop) without losing the pulse.",
        )
        self.seq_build_slider = FloatSlider(
            "Phrase build", 0.0, 1.0, sq.white_build, decimals=2,
            tooltip="The phrase starts sparse and fills in bar by bar toward its end - tension building "
            "up to the fill and the phrase-start flash, like an arrangement does. Adds to Accent focus at "
            "the phrase's first bar, fading out by its last bar. 0 = the same density all through the "
            "phrase. Needs a phrase length of 2 bars or more.",
        )
        self.seq_gap_slider = FloatSlider(
            "Min gap per lamp", 100.0, 1000.0, sq.min_group_gap_ms, decimals=0, suffix=" ms",
            tooltip="A lamp never starts two white flashes closer than this. Switching to white and "
            "back takes several commands, so too little here can make lamps lag or get stuck.",
        )
        self.seq_walk_positions_combo = labeled_combo(
            layout, "Walk through:", ("groups", "chase_order"), sq.walk_positions,
            "What the walk steps through. groups: the Group Switch groups ('Effect group' on the "
            "Devices & Setup tab). chase_order: lamp by lamp in the Chase order ('Chase order'). How many "
            "lamps flash at once follows 'Rotating' next to True white lamps above.",
        )
        for combo in (self.seq_white_combo, self.seq_walk_combo, self.seq_walk_positions_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_dark_combo = labeled_combo(
            layout, "Dark pattern:", DARK_PATTERNS, sq.dark_pattern,
            "Where the dark pulses (a short dip toward black) go. before_downbeat: the last 16th "
            "before each bar - a breath, then the downbeat hits. before_phrase: only before a new "
            "phrase. before_backbeats: before beats 2 and 4 too. stutter: fast dark 16ths in the "
            "phrase-end fill. auto: more of these the louder the music is.",
        )
        self.seq_dark_density_slider = FloatSlider(
            "Dark density", 0.0, 1.0, sq.dark_density, decimals=2,
            tooltip="Chance each dark step actually happens (the breath before a phrase always does, "
            "when Phrase accents is on).",
        )
        self.seq_dark_length_slider = FloatSlider(
            "Dark length", 0.3, 3.0, sq.dark_length, decimals=2, suffix="x",
            tooltip="Multiplies the dark pulse duration set above - longer reads as a deeper breath.",
        )
        self.seq_dark_combo.currentTextChanged.connect(self._on_sequencer_changed)
        for w in (
            self.seq_white_density_slider,
            self.seq_focus_slider,
            self.seq_build_slider,
            self.seq_double_slider,
            self.seq_gap_slider,
            self.seq_dark_density_slider,
            self.seq_dark_length_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        self.seq_repeat_checkbox = QCheckBox("Repeat the groove (the same flashes in every bar of a phrase)")
        self.seq_repeat_checkbox.setChecked(sq.white_repeat)
        self.seq_repeat_checkbox.setToolTip(
            "Which pattern steps flash (and which get a double) is decided once per phrase and repeated "
            "in every bar, the way a drummer keeps a groove - so the eye can follow it. A new variation "
            "comes with the next phrase. Off: every step is rolled anew, so the gaps jump around from "
            "bar to bar - livelier, but restless. With Phrase build the groove grows bar by bar: "
            "flashes are added, none taken away."
        )
        self.seq_repeat_checkbox.toggled.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_repeat_checkbox)

        phrase_row = QHBoxLayout()
        phrase_row.addWidget(QLabel("Phrase length:"))
        self.seq_phrase_combo = choice_combo(
            PHRASE_LENGTHS, sq.phrase_bars,
            "Most pop and dance music is built from 4- or 8-bar phrases: something changes or a new "
            "part starts every 8 bars. The fill, breath and accent below mark those boundaries.",
        )
        self.seq_phrase_combo.currentIndexChanged.connect(self._on_sequencer_changed)
        phrase_row.addWidget(self.seq_phrase_combo)
        phrase_row.addStretch(1)
        layout.addLayout(phrase_row)

        self.seq_dynamics_checkbox = QCheckBox("Pulse dynamics (vary each white flash's shape with the music)")
        self.seq_dynamics_checkbox.setChecked(sq.pulse_dynamics)
        self.seq_dynamics_checkbox.setToolTip(
            "The white pulse settings above (brightness, attack, duration, release) become the baseline, "
            "and each flash is shaped by where it falls in the music: the downbeat long and bright, "
            "flashes between beats short and crisp, fills snappier and brighter toward the phrase start, "
            "the phrase start held longest with a slow fade, quiet parts dimmer and softer, loud parts "
            "full and sharp. Off: every flash has exactly the settings above."
        )
        self.seq_dynamics_checkbox.toggled.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_dynamics_checkbox)
        self.seq_dynamics_slider = FloatSlider(
            "Dynamics amount", 0.0, 1.0, sq.pulse_dynamics_amount, decimals=2,
            tooltip="How strongly the flashes vary: 0 = all identical, 1 = the full contrast (a downbeat "
            "flash then lasts about 4x as long as a 16th, and is almost twice as bright).",
        )
        self.seq_dynamics_slider.valueChanged.connect(self._on_sequencer_changed)
        layout.addWidget(self.seq_dynamics_slider)

        self.seq_fills_checkbox = QCheckBox("Fills (denser flashes at the end of each phrase)")
        self.seq_fills_checkbox.setChecked(sq.fills)
        self.seq_accent_checkbox = QCheckBox("Phrase accents (dark breath, then every lamp flashes on the new phrase)")
        self.seq_accent_checkbox.setChecked(sq.phrase_accent)
        self.seq_drop_checkbox = QCheckBox("Follow drops (a quiet-to-loud jump starts a new phrase right there)")
        self.seq_drop_checkbox.setChecked(sq.drop_detection)
        for cb in (self.seq_fills_checkbox, self.seq_accent_checkbox, self.seq_drop_checkbox):
            cb.toggled.connect(self._on_sequencer_changed)
            layout.addWidget(cb)
        self._build_strobe_controls(layout, sq)
        return box

    def _build_strobe_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Strobe", key="beat/strobe", checkable=True, checked=sq.strobe_enabled, nested=True,
            tooltip="Now and then a short cool-white burst on every lamp at once. "
            "A roll into the next phrase or bar: the end of a bar becomes a burst of fast cool-white "
            "flashes on every selected lamp at the same instant, on top of the show - the colours and the "
            "other pulses keep running underneath. Rare by default, so it stays an event; placement, "
            "loudness, chance and minimum gap below make it as frequent as you like, and Strobe length as "
            "long as a whole bar. The flash rate follows the tempo: the fastest subdivision of the beat "
            "under Max rate. Needs Lamp transitions 'direct' or 'smooth' (Color Mapping tab, Global box).\n\n"
            "Flashing light at these rates can trigger seizures in people with photosensitive epilepsy - "
            "the usual recommendation for venues is at most 4 flashes per second.",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro("Now and then a short cool-white burst on every lamp at once."))
        self.seq_strobe_checkbox = section.toggle
        self.seq_strobe_checkbox.toggled.connect(self._on_sequencer_changed)
        self.seq_strobe_note = section.note

        self.seq_strobe_placement_combo = labeled_combo(
            layout, "Strobe placement:", STROBE_PLACEMENTS, sq.strobe_placement,
            "Where a strobe may happen, from rarest to most frequent. phrase: only at the end of a phrase's "
            "last bar - the roll into the new phrase. half_phrase: also into the phrase's second half. "
            "bars: at the end of any bar. half_bars: also in the middle of each bar (up to twice per bar - "
            "set Strobe min gap to 0 for that; only with a Strobe length of half a bar or less).",
        )
        self.seq_strobe_level_combo = labeled_combo(
            layout, "Strobe from loudness:", LEVELS, sq.strobe_min_level,
            "Only when the music is at least this loud compared to the last half minute (the loudness "
            "the info panel shows). calm: always. groove: not in the quiet parts. "
            "high / peak: only in the loudest ones.",
        )
        length_row = QHBoxLayout()
        length_row.addWidget(QLabel("Strobe length:"))
        self.seq_strobe_length_combo = choice_combo(
            [(0.5, "half a beat"), (1.0, "1 beat"), (2.0, "2 beats"), (4.0, "4 beats")], sq.strobe_beats,
            "How much of the end of the bar the burst fills (at most the whole bar: 4 beats in 4/4). It "
            "stops at the bar line, so the downbeat that follows lands clean. Half a beat needs at least "
            "two flashes in it - at fast tempos that may not fit under Max rate, and then nothing is played.",
        )
        length_row.addWidget(self.seq_strobe_length_combo)
        length_row.addStretch(1)
        layout.addLayout(length_row)
        self.seq_strobe_wave_combo = labeled_combo(
            layout, "Strobe brightness wave:", STROBE_WAVES, sq.strobe_wave,
            "How the brightness moves over one burst. off: every flash at Strobe brightness. linear: the "
            "burst swells - the first flashes are nearly dark, each one brighter up to Strobe brightness "
            "in the middle, then back down to dark. bezier: the same with an eased S-curve (as in Chase's "
            "falloff) - it lingers near dark at the ends and near full in the middle. Shows best on longer "
            "bursts (2 or 4 beats): a 1-beat burst has only a few flashes to shape.",
        )
        for combo in (self.seq_strobe_placement_combo, self.seq_strobe_level_combo, self.seq_strobe_wave_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_strobe_length_combo.currentIndexChanged.connect(self._on_sequencer_changed)

        self.seq_strobe_chance_slider = FloatSlider(
            "Strobe chance", 0.0, 1.0, sq.strobe_chance, decimals=2,
            tooltip="Chance a bar that qualifies actually gets the strobe. 1 = every time (handy while "
            "trying it out), lower = rarer and less predictable.",
        )
        self.seq_strobe_gap_slider = FloatSlider(
            "Strobe min gap", 0.0, 32.0, float(sq.strobe_min_gap_bars), decimals=0, suffix=" bars",
            tooltip="Never two strobes closer than this many bars, whatever the chance says. 1 = at most "
            "one per bar; 0 = no minimum (with placement 'half_bars': two per bar).",
        )
        self.seq_strobe_rate_slider = FloatSlider(
            "Strobe max rate", 4.0, 15.0, sq.strobe_max_hz, decimals=1, suffix=" /s",
            tooltip="Ceiling on flashes per second. The actual rate is the fastest subdivision of the beat "
            "under this (8ths, triplets, 16ths, 16th triplets...): at 128 BPM 8.5 per second, or 12.8 once "
            "the ceiling is above that. The Lamp command rate limits it too: a flash is two commands, so "
            "20 commands/s allow at most 10 flashes/s and 15 flashes/s need 30 commands/s - the Test "
            "button says when that is what's holding the rate down.",
        )
        self.seq_strobe_duty_slider = FloatSlider(
            "Strobe on-time", 0.2, 0.8, sq.strobe_duty, decimals=2,
            tooltip="Share of each flash's period the white is on: 0.5 = on and off equally long, lower = "
            "shorter, sharper flashes. Away from 0.5 the shorter part needs a higher Lamp command rate "
            "(otherwise a slower subdivision is used).",
        )
        self.seq_strobe_brightness_slider = FloatSlider(
            "Strobe brightness", 0.05, 1.0, sq.strobe_brightness, decimals=2,
            tooltip="White brightness of the flashes (cool white - it lights up fastest). The per-lamp "
            "white pulse brightness multiplier applies here too.",
        )
        for w in (
            self.seq_strobe_chance_slider,
            self.seq_strobe_gap_slider,
            self.seq_strobe_rate_slider,
            self.seq_strobe_duty_slider,
            self.seq_strobe_brightness_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        test_row = QHBoxLayout()
        self.seq_strobe_test_button = QPushButton("Test strobe now")
        self.seq_strobe_test_button.setToolTip(
            "Plays one burst on the selected lamps right away with the length, max rate, on-time and "
            "brightness above - to see how it looks without waiting for the music to call for one."
        )
        self.seq_strobe_test_button.clicked.connect(self._on_strobe_test)
        test_row.addWidget(self.seq_strobe_test_button)
        self.seq_strobe_test_label = QLabel()
        self.seq_strobe_test_label.setWordWrap(True)
        test_row.addWidget(self.seq_strobe_test_label, stretch=1)
        layout.addLayout(test_row)

        self._build_flash_strobe_controls(parent_layout, sq)
        self._build_color_sweep_controls(parent_layout, sq)
        self._build_dark_sweep_controls(parent_layout, sq)

    def _build_flash_strobe_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Flash strobe", key="beat/flash_strobe", checkable=True, checked=sq.flash_strobe_enabled,
            nested=True,
            tooltip="Now and then one group's white flash becomes a short strobe. Along the white walk: now and then one of the walk's white flashes is played as a short "
            "strobe on just that lamp group instead - so as the white goes round the room, a strobe shows "
            "up here and there, like a glitch. With several White rotators only the walk's own group "
            "strobes; the others keep their plain white flash. It starts on the flash's beat (never the "
            "downbeat, which lands clean) and "
            "lasts as long as that white flash would (its attack + duration + release, as the pulse "
            "dynamics shaped it), stopping at the bar line at the latest. Its flashes follow the white "
            "flash's brightness: they rise over the attack, hold, and fade over the release along its "
            "release curve (times Flash strobe brightness below) - so it doesn't stand out from the white "
            "pulses. With a white flash shorter than two strobe flashes it stays a plain flash. "
            "The rate and on-time are the Strobe's (Strobe max rate, Strobe on-time above). Independent "
            "of the Strobe checkbox. Needs Lamp transitions 'direct' or 'smooth' (Color Mapping tab, Global box).\n\n"
            "Flashing light at these rates can trigger seizures in people with photosensitive epilepsy.",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro(
            "Now and then one group's white flash becomes a short strobe. It uses that white pulse's "
            "brightness curve: its flashes rise over the attack, stay full through the duration and fade "
            "over the release along the release curve (White pulses section), as the pulse dynamics shaped "
            "that flash - times Flash strobe brightness below."
        ))
        self.seq_flash_strobe_checkbox = section.toggle
        self.seq_flash_strobe_checkbox.toggled.connect(self._on_sequencer_changed)

        self.seq_flash_strobe_placement_combo = labeled_combo(
            layout, "Flash strobe placement:", FLASH_STROBE_PLACEMENTS, sq.flash_strobe_placement,
            "Which white flashes may become one. bar_end: only the flash on the bar's last beat - a roll "
            "into the next bar. beats: a flash on any beat but the downbeat (8th and 16th flashes in "
            "between never do).",
        )
        self.seq_flash_strobe_level_combo = labeled_combo(
            layout, "Flash strobe from loudness:", LEVELS, sq.flash_strobe_min_level,
            "Only when the music is at least this loud compared to the last half minute. calm: always. "
            "groove: not in the quiet parts. high / peak: only in the loudest ones.",
        )
        for combo in (self.seq_flash_strobe_placement_combo, self.seq_flash_strobe_level_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)

        self.seq_flash_strobe_chance_slider = FloatSlider(
            "Flash strobe chance", 0.0, 1.0, sq.flash_strobe_chance, decimals=2,
            tooltip="Chance a white flash that qualifies becomes a strobe. 1 = every time (handy while "
            "trying it out).",
        )
        self.seq_flash_strobe_gap_slider = FloatSlider(
            "Flash strobe min gap", 0.0, 16.0, float(sq.flash_strobe_min_gap_bars), decimals=0, suffix=" bars",
            tooltip="Never two flash strobes closer than this many bars, whatever the chance says. 0 = no "
            "minimum.",
        )
        self.seq_flash_strobe_brightness_slider = FloatSlider(
            "Flash strobe brightness", 0.3, 1.5, sq.flash_strobe_brightness, decimals=2, suffix=" x",
            tooltip="Brightness of its flashes relative to the white flash it replaces, moment by moment "
            "along that flash's attack, duration and release (it follows White brightness, the pulse "
            "dynamics and the per-lamp multiplier). 1 = just as bright, so it blends in with the white "
            "pulses; lower = subtler.",
        )
        for w in (
            self.seq_flash_strobe_chance_slider,
            self.seq_flash_strobe_gap_slider,
            self.seq_flash_strobe_brightness_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

    def _build_color_sweep_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Color sweep", key="beat/color_sweep", checkable=True, checked=sq.color_sweep_enabled, nested=True,
            tooltip="Now and then the colour below runs once round the lamps in Chase order ('Chase order' in the "
            "Devices & Setup tab - lamps without one are left out), fast: over the Sweep length, each position "
            "lit in turn for its share of it, on top of the show. Placed on the beat grid like the strobe: "
            "it ends on a bar line, so the downbeat lands clean, and never runs during the strobe (a strobe "
            "takes over from one still running). Uses only its own settings below - none of the Chase "
            "overlay's (Chase itself can stay off).",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro("Now and then a colour of its own runs fast round the lamps in Chase order."))
        self.seq_sweep_checkbox = section.toggle
        self.seq_sweep_checkbox.toggled.connect(self._on_sequencer_changed)

        self.seq_sweep_color_combo = labeled_combo(
            layout, "Sweep colour:", COLOR_SWEEP_COLORS, sq.color_sweep_color,
            "custom: the hue below. dynamic: a new colour for every sweep, one of the brightest the bulbs "
            "make (red, yellow, green, cyan, blue, magenta) - one of the two farthest from the colour the "
            "lamps show at that moment, so it stands out from the show, and never the previous sweep's. "
            "The info panel's timeline shows each sweep in its colour.",
        )
        self.seq_sweep_color_combo.currentTextChanged.connect(self._on_sequencer_changed)
        self.seq_sweep_hue_slider = HueSlider("Custom hue", sq.color_sweep_hue_deg)
        self.seq_sweep_saturation_slider = FloatSlider(
            "Sweep saturation", 0.0, 1.0, sq.color_sweep_saturation, decimals=2,
            tooltip="1 = the full colour, lower = paler (0 = white from the RGB LEDs).",
        )
        self.seq_sweep_brightness_slider = FloatSlider(
            "Sweep brightness", 0.1, 1.0, sq.color_sweep_brightness, decimals=2,
            tooltip="Brightness of the lit lamp.",
        )
        for w in (self.seq_sweep_hue_slider, self.seq_sweep_saturation_slider, self.seq_sweep_brightness_slider):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        (
            self.seq_sweep_length_combo, self.seq_sweep_rotators_spin, self.seq_sweep_width_spin,
            self.seq_sweep_direction_combo, self.seq_sweep_placement_combo, self.seq_sweep_level_combo,
            self.seq_sweep_chance_slider, self.seq_sweep_gap_slider, self.seq_sweep_test_button,
            self.seq_sweep_test_label,
        ) = self._build_sweep_motion_controls(layout, sq, "color_sweep", "Sweep", "lit", self._on_sweep_test)
        self.seq_sweep_test_button.setToolTip(
            "Plays the sweep 4 times in a row on the selected lamps right away, with the settings above (a "
            "new colour each time with 'dynamic') - to see how it looks without waiting for the music to "
            "call for one. Plays on top of the show, so start the visualizer first."
        )

    def _build_dark_sweep_controls(self, parent_layout: QVBoxLayout, sq: PulseSequencerConfig) -> None:
        section = CollapsibleSection(
            "Dark sweep", key="beat/dark_sweep", checkable=True, checked=sq.dark_sweep_enabled, nested=True,
            tooltip="Like the color sweep, but a dark spot runs round the lamps in Chase order: each lamp dips "
            "toward black in turn, like a dark pulse passing by - easier to see than a colour. On top of the "
            "show: a white flash on a lamp it darkens dims with it. Placed on the beat grid like the color "
            "sweep, with its own settings below; the two never run at the same time (they share the info "
            "panel's Sweep lane, where a dark sweep is a black bar), and neither runs during the strobe.",
        )
        parent_layout.addWidget(section)
        layout = section.content_layout()
        layout.addWidget(self._intro(
            "Now and then a dark spot runs fast round the lamps in Chase order - each lamp dims and comes back "
            "in turn."
        ))
        self.seq_dark_sweep_checkbox = section.toggle
        self.seq_dark_sweep_checkbox.toggled.connect(self._on_sequencer_changed)

        self.seq_dark_sweep_depth_slider = FloatSlider(
            "Dark sweep depth", 0.1, 1.0, sq.dark_sweep_depth, decimals=2,
            tooltip="How dark a lamp gets as the spot passes: 1 = black, lower = a partial dim.",
        )
        self.seq_dark_sweep_desaturate_slider = FloatSlider(
            "Dark sweep desaturate", 0.0, 1.0, sq.dark_sweep_desaturate, decimals=2,
            tooltip="The colour's saturation moves with the dimming: it falls as the lamp dims (over the "
            "attack) and comes back as the light returns (over the release). 0 = the colour keeps its "
            "saturation. 1 = the saturation reaches zero just as the brightness does (with depth 1) - the "
            "colour washes out to white as it fades away, and comes back from white.",
        )
        self.seq_dark_sweep_attack_slider = FloatSlider(
            "Dark sweep attack", 1.0, 300.0, sq.dark_sweep_attack_ms, decimals=0, suffix=" ms",
            tooltip="How fast a lamp dims as the spot reaches it - low = an instant cut.",
        )
        self.seq_dark_sweep_release_slider = FloatSlider(
            "Dark sweep release", 10.0, 1000.0, sq.dark_sweep_release_ms, decimals=0, suffix=" ms",
            tooltip="How slowly a lamp comes back once the spot has moved on: higher = a longer dark tail "
            "behind the spot (it can still be fading back after the sweep is over).",
        )
        for w in (
            self.seq_dark_sweep_depth_slider, self.seq_dark_sweep_desaturate_slider,
            self.seq_dark_sweep_attack_slider, self.seq_dark_sweep_release_slider,
        ):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        (
            self.seq_dark_sweep_length_combo, self.seq_dark_sweep_rotators_spin, self.seq_dark_sweep_width_spin,
            self.seq_dark_sweep_direction_combo, self.seq_dark_sweep_placement_combo,
            self.seq_dark_sweep_level_combo, self.seq_dark_sweep_chance_slider, self.seq_dark_sweep_gap_slider,
            self.seq_dark_sweep_test_button, self.seq_dark_sweep_test_label,
        ) = self._build_sweep_motion_controls(layout, sq, "dark_sweep", "Dark sweep", "dark", self._on_dark_sweep_test)
        self.seq_dark_sweep_test_button.setToolTip(
            "Plays the dark sweep 4 times in a row on the selected lamps right away, with the settings above - "
            "to see how it looks without waiting for the music to call for one. Plays on top of the show, so "
            "start the visualizer first."
        )

    def _build_sweep_motion_controls(self, layout: QVBoxLayout, sq: PulseSequencerConfig, prefix: str,
                                     name: str, lit: str, on_test) -> tuple:
        """The settings the color and dark sweep share, for the sequencer's
        `prefix`_* fields: length, rotators and width, direction, placement,
        loudness, chance, min gap and a Test button (calling `on_test`) with
        its result label. `name` starts the labels ("Sweep", "Dark sweep");
        `lit`: what a lamp the sweep is on is ("lit", "dark")."""
        noun = name.lower()
        length_row = QHBoxLayout()
        length_row.addWidget(QLabel(f"{name} length:"))
        length_combo = choice_combo(
            [(0.5, "half a beat"), (1.0, "1 beat"), (2.0, "2 beats"), (4.0, "4 beats")],
            getattr(sq, f"{prefix}_beats"),
            f"How long one {noun} round the lamps takes (at most the whole bar). Very short sweeps on many "
            f"lamps make each one {lit} for only a tick or two.",
        )
        length_combo.currentIndexChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(length_combo)
        length_row.addWidget(QLabel("Rotators:"))
        rotators_spin = QSpinBox()
        rotators_spin.setRange(1, 16)
        rotators_spin.setValue(getattr(sq, f"{prefix}_rotators"))
        rotators_spin.setToolTip(
            "How many run round at once, evenly spaced round the loop: 2 = from opposite sides, 3 = thirds. "
            f"Each covers its own stretch, so every lamp is still {lit} once per {noun}."
        )
        rotators_spin.valueChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(rotators_spin)
        length_row.addWidget(QLabel("Width:"))
        width_spin = QSpinBox()
        width_spin.setRange(1, 8)
        width_spin.setValue(getattr(sq, f"{prefix}_width"))
        width_spin.setSuffix(" spots")
        width_spin.setToolTip(
            f"How many neighbouring Chase order positions are {lit} at once: 1 = a single spot running round, "
            f"2 or more = a band of that many, easier to follow. Every lamp is still {lit} for the same share "
            f"of the {noun}, and the {noun} takes the same {name} length."
        )
        width_spin.valueChanged.connect(self._on_sequencer_changed)
        length_row.addWidget(width_spin)
        length_row.addStretch(1)
        layout.addLayout(length_row)

        direction_combo = labeled_combo(
            layout, f"{name} direction:", COLOR_SWEEP_DIRECTIONS, getattr(sq, f"{prefix}_direction"),
            "forward: up the Chase order. backward: down it.",
        )
        placement_combo = labeled_combo(
            layout, f"{name} placement:", COLOR_SWEEP_PLACEMENTS, getattr(sq, f"{prefix}_placement"),
            f"Where a {noun} may happen, from rarest to most frequent. phrase: ending on the bar line into a "
            "new phrase - a run into the phrase-start flash. bars: ending on any bar line. beats: starting on "
            "any beat (and over by the bar line).",
        )
        level_combo = labeled_combo(
            layout, f"{name} from loudness:", LEVELS, getattr(sq, f"{prefix}_min_level"),
            "Only when the music is at least this loud compared to the last half minute. calm: always. "
            "groove: not in the quiet parts. high / peak: only in the loudest ones.",
        )
        for combo in (direction_combo, placement_combo, level_combo):
            combo.currentTextChanged.connect(self._on_sequencer_changed)

        chance_slider = FloatSlider(
            f"{name} chance", 0.0, 1.0, getattr(sq, f"{prefix}_chance"), decimals=2,
            tooltip=f"Chance a spot that qualifies gets a {noun}. 1 = every time (handy while trying it out).",
        )
        gap_slider = FloatSlider(
            f"{name} min gap", 0.0, 16.0, float(getattr(sq, f"{prefix}_min_gap_bars")), decimals=0, suffix=" bars",
            tooltip=f"Never two {noun}s closer than this many bars, whatever the chance says. 0 = no minimum.",
        )
        for w in (chance_slider, gap_slider):
            w.valueChanged.connect(self._on_sequencer_changed)
            layout.addWidget(w)

        test_row = QHBoxLayout()
        test_button = QPushButton(f"Test {noun} now")
        test_button.clicked.connect(on_test)
        test_row.addWidget(test_button)
        test_label = QLabel()
        test_label.setWordWrap(True)
        test_row.addWidget(test_label, stretch=1)
        layout.addLayout(test_row)
        return (
            length_combo, rotators_spin, width_spin, direction_combo, placement_combo, level_combo,
            chance_slider, gap_slider, test_button, test_label,
        )

    def _on_sweep_test(self) -> None:
        self.seq_sweep_test_label.setText(self.controller.engine.trigger_color_sweep_test())

    def _on_dark_sweep_test(self) -> None:
        self.seq_dark_sweep_test_label.setText(self.controller.engine.trigger_dark_sweep_test())

    def _on_strobe_test(self) -> None:
        self.seq_strobe_test_label.setText(self.controller.engine.trigger_strobe_test())

    @staticmethod
    def _intro(text: str) -> QLabel:
        """A one-line explanation at the top of a section's settings."""
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    # -- handlers ------------------------------------------------------------------------

    def _on_pulse_changed(self, *_args) -> None:
        bs = self.controller.config.color_mapping.beat_sync
        bs.dark_pulse_enabled = self.beat_dark_enabled_checkbox.isChecked()
        bs.dark_pulse_probability = self.beat_dark_prob_slider.value()
        bs.dark_pulse_trigger = self.beat_dark_trigger_combo.currentText()
        bs.dark_pulse_duration_ms = self.beat_dark_duration_slider.value()
        bs.dark_pulse_depth = self.beat_dark_depth_slider.value()
        bs.dark_pulse_attack_ms = self.beat_dark_attack_slider.value()
        bs.dark_pulse_release_ms = self.beat_dark_release_slider.value()
        bs.dark_pulse_desaturate = self.beat_dark_desaturate_slider.value()
        bs.white_pulse_enabled = self.beat_white_pulse_enabled_checkbox.isChecked()
        bs.white_pulse_probability = self.beat_white_pulse_prob_slider.value()
        bs.white_pulse_trigger = self.beat_white_trigger_combo.currentText()
        bs.white_pulse_duration_ms = self.beat_white_pulse_duration_slider.value()
        bs.white_pulse_attack_ms = self.beat_white_pulse_attack_slider.value()
        bs.white_pulse_release_ms = self.beat_white_pulse_release_slider.value()
        bs.white_pulse_release_curve = self.beat_white_release_curve_combo.currentText()
        bs.white_pulse_target = self.beat_white_pulse_target_combo.currentText()
        bs.white_pulse_rotators = self.beat_white_rotators_combo.currentData()
        bs.white_pulse_white_brightness = self.beat_white_pulse_white_brightness_slider.value()
        bs.white_pulse_cool_ratio = self.beat_white_pulse_cool_ratio_slider.value()
        bs.white_pulse_temp_mode = self.beat_white_temp_combo.currentText()
        bs.white_pulse_hybrid_chance = self.beat_white_hybrid_slider.value()
        self.update_states()
        self.controller.apply_config_changes()

    def _on_sequencer_changed(self, *_args) -> None:
        sq = self.controller.config.sequencer
        sq.enabled = self.seq_enabled_checkbox.isChecked()
        sq.white_pattern = self.seq_white_combo.currentText()
        sq.group_walk = self.seq_walk_combo.currentText()
        sq.walk_positions = self.seq_walk_positions_combo.currentText()
        sq.white_density = self.seq_white_density_slider.value()
        sq.double_chance = self.seq_double_slider.value()
        sq.white_accent_focus = self.seq_focus_slider.value()
        sq.white_build = self.seq_build_slider.value()
        sq.white_repeat = self.seq_repeat_checkbox.isChecked()
        sq.min_group_gap_ms = self.seq_gap_slider.value()
        sq.dark_pattern = self.seq_dark_combo.currentText()
        sq.dark_density = self.seq_dark_density_slider.value()
        sq.dark_length = self.seq_dark_length_slider.value()
        sq.phrase_bars = self.seq_phrase_combo.currentData()
        sq.fills = self.seq_fills_checkbox.isChecked()
        sq.phrase_accent = self.seq_accent_checkbox.isChecked()
        sq.drop_detection = self.seq_drop_checkbox.isChecked()
        sq.pulse_dynamics = self.seq_dynamics_checkbox.isChecked()
        sq.pulse_dynamics_amount = self.seq_dynamics_slider.value()
        sq.strobe_enabled = self.seq_strobe_checkbox.isChecked()
        sq.strobe_placement = self.seq_strobe_placement_combo.currentText()
        sq.strobe_min_level = self.seq_strobe_level_combo.currentText()
        sq.strobe_beats = self.seq_strobe_length_combo.currentData()
        sq.strobe_chance = self.seq_strobe_chance_slider.value()
        sq.strobe_min_gap_bars = int(round(self.seq_strobe_gap_slider.value()))
        sq.strobe_max_hz = self.seq_strobe_rate_slider.value()
        sq.strobe_duty = self.seq_strobe_duty_slider.value()
        sq.strobe_brightness = self.seq_strobe_brightness_slider.value()
        sq.strobe_wave = self.seq_strobe_wave_combo.currentText()
        sq.flash_strobe_enabled = self.seq_flash_strobe_checkbox.isChecked()
        sq.flash_strobe_placement = self.seq_flash_strobe_placement_combo.currentText()
        sq.flash_strobe_min_level = self.seq_flash_strobe_level_combo.currentText()
        sq.flash_strobe_chance = self.seq_flash_strobe_chance_slider.value()
        sq.flash_strobe_min_gap_bars = int(round(self.seq_flash_strobe_gap_slider.value()))
        sq.flash_strobe_brightness = self.seq_flash_strobe_brightness_slider.value()
        sq.color_sweep_enabled = self.seq_sweep_checkbox.isChecked()
        sq.color_sweep_color = self.seq_sweep_color_combo.currentText()
        sq.color_sweep_hue_deg = self.seq_sweep_hue_slider.value()
        sq.color_sweep_saturation = self.seq_sweep_saturation_slider.value()
        sq.color_sweep_brightness = self.seq_sweep_brightness_slider.value()
        sq.color_sweep_beats = self.seq_sweep_length_combo.currentData()
        sq.color_sweep_rotators = self.seq_sweep_rotators_spin.value()
        sq.color_sweep_width = self.seq_sweep_width_spin.value()
        sq.color_sweep_direction = self.seq_sweep_direction_combo.currentText()
        sq.color_sweep_placement = self.seq_sweep_placement_combo.currentText()
        sq.color_sweep_min_level = self.seq_sweep_level_combo.currentText()
        sq.color_sweep_chance = self.seq_sweep_chance_slider.value()
        sq.color_sweep_min_gap_bars = int(round(self.seq_sweep_gap_slider.value()))
        sq.dark_sweep_enabled = self.seq_dark_sweep_checkbox.isChecked()
        sq.dark_sweep_depth = self.seq_dark_sweep_depth_slider.value()
        sq.dark_sweep_desaturate = self.seq_dark_sweep_desaturate_slider.value()
        sq.dark_sweep_attack_ms = self.seq_dark_sweep_attack_slider.value()
        sq.dark_sweep_release_ms = self.seq_dark_sweep_release_slider.value()
        sq.dark_sweep_beats = self.seq_dark_sweep_length_combo.currentData()
        sq.dark_sweep_rotators = self.seq_dark_sweep_rotators_spin.value()
        sq.dark_sweep_width = self.seq_dark_sweep_width_spin.value()
        sq.dark_sweep_direction = self.seq_dark_sweep_direction_combo.currentText()
        sq.dark_sweep_placement = self.seq_dark_sweep_placement_combo.currentText()
        sq.dark_sweep_min_level = self.seq_dark_sweep_level_combo.currentText()
        sq.dark_sweep_chance = self.seq_dark_sweep_chance_slider.value()
        sq.dark_sweep_min_gap_bars = int(round(self.seq_dark_sweep_gap_slider.value()))
        self.update_states()
        self.controller.apply_config_changes()
