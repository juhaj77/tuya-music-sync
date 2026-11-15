"""Real Time tab: the show played by hand.

The other tabs set up a show that runs by itself; nothing on this one is a
setting of it. It's an instrument laid over the running show (or over
darkness while the show is stopped): what's played lasts as long as it's
played and is never saved (effects/realtime.py, the engine's last step).

- The stage (widgets/stage.py): every lamp where it stands in the room.
  Arrange them once by dragging; then the pointer is a tool - a brush that
  leaves a trail and keeps gliding when thrown, a curtain pulled across the
  stage, ripples, a spot.
- The look, on the right: what the tools and pads paint with - an
  adjustable palette as a gradient or one of its animations, the show's own
  colour turned round the wheel, frozen, white or dark.
- The pads, along the bottom: holds (on while held, latched with Shift+click
  or a right-click) and one-shots, each with a keyboard key - the Strobe and
  the Chase-order sweeps that the Global Effects tab only has Test buttons
  for among them.

The keyboard plays the pads wherever the focus is on this tab, except in a
number box (see eventFilter).
"""
from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...effects.realtime import layout as stage_layout
from .. import theme
from ..controller import AppController
from ..widgets.pad_button import PadButton
from ..widgets.palette_editor import PaletteEditor
from ..widgets.param_slider import FloatSlider
from ..widgets.stage import RADIUS_RANGE, TRAIL_RANGE, StageCanvas



def _code(key) -> int:
    """A Qt.Key as the plain number QKeyEvent.key() gives."""
    return int(getattr(key, "value", key))


# (key, tool, name, what the hint line says while it's picked)
_TOOLS = [
    (Qt.Key_Z, "brush", "Brush",
     "Brush - drag over the lamps: they take the look and keep it for the Trail time. Let go while moving and "
     "it glides on. Wheel: radius · Shift+wheel: trail · Ctrl+drag: erase held paint · right-click: a ripple."),
    (Qt.Key_X, "curtain", "Curtain",
     "Curtain - press and pull: the look follows the pointer like a curtain drawn from where you pressed, in any "
     "direction; pull back to open it. Softness is how soft its edge is."),
    (Qt.Key_C, "ripple", "Ripple",
     "Ripple - click: a ring of the look spreads from that point, as fast as Wave length. Drag to leave a row "
     "of them."),
    (Qt.Key_V, "spot", "Spot",
     "Spot - press: only the lamps under the pointer keep the show, the rest take the look - with Blackout a "
     "spotlight to point with. Wheel: radius."),
]

# (look, name, tooltip)
_LOOKS = [
    ("palette", "Palette",
     "The palette as a gradient - under the brush from its middle to its rim, behind the curtain from its edge "
     "back, through a wave from front to back - drifting round at Speed."),
    ("plasma", "Plasma", "The palette's colours flowing over the stage in slow swirls, each lamp where it stands."),
    ("rainbow", "Rainbow", "Every hue, turning round the pointer (the palette isn't used)."),
    ("flicker", "Flicker", "Each lamp flickers on its own between the palette's colours - fire with the Fire "
     "palette, northern lights with Ocean."),
    ("sparkle", "Sparkle", "The show stays, and lamps twinkle in the palette's colours on top of it."),
    ("strobe", "Strobe", "Flashes through the palette's colours, on and off - on the 16ths at Speed 1, in time "
     "with the beat clock when it's locked."),
    ("hue_shift", "Complement", "The show's own colour turned round the colour wheel by Hue turn - 180 is the "
     "complementary colour. The music's flashes and fades stay as they are."),
    ("freeze", "Freeze", "The lamp keeps the colour it had when it was touched, while the show goes on around it."),
    ("white", "White", "The lamps' own white LEDs, warm or cool, over the show's colour."),
    ("blackout", "Blackout", "Dark."),
]

# (key, text on the key, kind, name, tooltip)
_HOLD_PADS = [
    (Qt.Key_1, "1", "strobe", "Strobe",
     "Cool-white flashes on every lamp, as fast as Strobe rate allows - on a subdivision of the beat while the "
     "beat clock is locked. The lamps play it at exact times, like the sequencer's strobe. Needs Lamp "
     "transitions 'direct' or 'smooth'. At most 20 seconds in one go.\n\nFlashing light at these rates can "
     "trigger seizures in people with photosensitive epilepsy."),
    (Qt.Key_2, "2", "blinder", "Blinder", "Every lamp's white LEDs at full (White warm/cool sets the tone)."),
    (Qt.Key_3, "3", "blackout", "Blackout", "Every lamp dark. The show keeps running underneath."),
    (Qt.Key_4, "4", "freeze", "Freeze", "The lamps hold what they show this instant."),
    (Qt.Key_5, "5", "flood", "Flood", "The look on every lamp, the gradient running across the stage."),
    (Qt.Key_6, "6", "invert", "Invert", "The whole show in its complementary colours."),
    (Qt.Key_7, "7", "color_strobe", "Color strobe",
     "Every lamp flashing through the palette's colours - works with any Lamp transitions.\n\nFlashing light "
     "can trigger seizures in people with photosensitive epilepsy."),
    (Qt.Key_8, "8", "storm", "Storm", "Lamps popping at random in the look, on the 16ths at Speed 1."),
    (Qt.Key_9, "9", "orbit", "Orbit", "A comet of the look circling the lamps, once a bar at Wave length 1."),
]
_TAP_PADS = [
    (Qt.Key_Q, "Q", "sweep_left", "Sweep ←", "A band of the look crossing the stage from right to left."),
    (Qt.Key_W, "W", "sweep_right", "Sweep →", "A band of the look crossing the stage from left to right."),
    (Qt.Key_E, "E", "burst", "Burst", "A ring of the look spreading from the middle of the stage."),
    (Qt.Key_R, "R", "implode", "Implode", "A ring of the look closing in on the middle of the stage."),
    (Qt.Key_T, "T", "close", "Close", "Two bands closing in from both sides."),
    (Qt.Key_Y, "Y", "open", "Open", "Two bands opening from the middle to both sides."),
    (Qt.Key_U, "U", "flash", "Flash", "Every lamp flashes the look and fades. Space does the same."),
    (Qt.Key_I, "I", "chase_sweep", "Chase sweep",
     "The sequencer's Color sweep, once round the Chase order, with its own settings (Global Effects tab)."),
    (Qt.Key_O, "O", "dark_sweep", "Dark sweep",
     "The sequencer's Dark sweep, once round the Chase order, with its own settings (Global Effects tab)."),
]
_OWN_LIGHT_LOOKS = ("palette", "plasma", "rainbow", "flicker", "strobe")
_PALETTE_LOOKS = ("palette", "plasma", "flicker", "sparkle", "strobe")
_ANIMATED_LOOKS = ("palette", "plasma", "rainbow", "flicker", "sparkle", "strobe")


class RealtimeTab(QWidget):
    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._keys_down: set = set()
        cfg = controller.config.realtime

        root = QHBoxLayout(self)
        root.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(6)
        root.addLayout(left, stretch=1)

        # -- the tools, over the stage ----------------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: Dict[str, QPushButton] = {}
        for key, tool, name, hint in _TOOLS:
            button = self._chip(f"{name}  {chr(_code(key))}", hint)
            button.clicked.connect(lambda _checked=False, t=tool: self._set_tool(t))
            self.tool_group.addButton(button)
            self.tool_buttons[tool] = button
            bar.addWidget(button)
        bar.addSpacing(10)
        self.latch_checkbox = QCheckBox("Hold paint")
        self.latch_checkbox.setChecked(cfg.latch)
        self.latch_checkbox.setToolTip(
            "What the brush, the curtain and the spot lay stays on the lamps until Clear (Esc), each stroke with "
            "the look it was made with - paint one corner in Fire and another in Ocean. Ctrl+drag with the "
            "brush erases. Off: a stroke fades over the Trail time."
        )
        self.latch_checkbox.toggled.connect(lambda on: self._set("latch", on))
        bar.addWidget(self.latch_checkbox)
        self.throw_checkbox = QCheckBox("Throw")
        self.throw_checkbox.setChecked(cfg.throw)
        self.throw_checkbox.setToolTip(
            "The brush let go of while moving keeps gliding the way it was going, bounces off the stage's "
            "edges and slows to a stop. Throw several."
        )
        self.throw_checkbox.toggled.connect(lambda on: self._set("throw", on))
        bar.addWidget(self.throw_checkbox)
        bar.addStretch(1)
        self.layout_buttons: List[QPushButton] = []
        for kind in ("circle", "line", "grid"):
            button = QPushButton(kind.capitalize())
            button.setToolTip(f"Places the lamps in a {kind}, in the Chase order (Devices & Setup tab).")
            button.clicked.connect(lambda _checked=False, k=kind: self._auto_layout(k))
            button.hide()
            self.layout_buttons.append(button)
            bar.addWidget(button)
        self.arrange_button = QPushButton("Arrange lamps")
        self.arrange_button.setCheckable(True)
        self.arrange_button.setToolTip(
            "Drag each lamp to where it stands in the room (Shift: snap to a grid). The lamp you hold lights "
            "up white, so you can tell which one it is. Done once - the places are remembered."
        )
        self.arrange_button.toggled.connect(self._set_arrange)
        bar.addWidget(self.arrange_button)
        self.clear_button = QPushButton("Clear  Esc")
        self.clear_button.setToolTip("Wipes the stage: held paint fades out, latched pads let go, waves stop.")
        self.clear_button.clicked.connect(self.clear)
        bar.addWidget(self.clear_button)
        left.addLayout(bar)

        self.stage = StageCanvas(controller)
        self.stage.settingsNudged.connect(self._sync_nudged)
        left.addWidget(self.stage, stretch=1)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        left.addWidget(self.hint_label)

        # -- the pads ------------------------------------------------------------------------
        self.pads: Dict[str, PadButton] = {}
        self._pad_keys: Dict[int, PadButton] = {}
        pad_grid = QGridLayout()
        pad_grid.setSpacing(5)
        for row, (label, pads, hold) in enumerate((("Hold", _HOLD_PADS, True), ("Tap", _TAP_PADS, False))):
            caption = QLabel(label)
            caption.setFont(theme.title_font(caption.font()))
            caption.setStyleSheet(f"color: {theme.LIVE if hold else theme.TITLE};")
            pad_grid.addWidget(caption, row, 0)
            for column, (key, key_text, kind, name, tooltip) in enumerate(pads):
                pad = PadButton(name, key_text, hold=hold, tooltip=tooltip)
                if hold:
                    pad.engaged.connect(lambda on, k=kind: self._on_hold(k, on))
                else:
                    pad.fired.connect(lambda k=kind: self._on_tap(k))
                self.pads[kind] = pad
                self._pad_keys[_code(key)] = pad
                pad_grid.addWidget(pad, row, column + 1)
        self._pad_keys[_code(Qt.Key_Space)] = self.pads["flash"]
        left.addLayout(pad_grid)

        pad_settings = QHBoxLayout()
        pad_settings.setSpacing(14)
        self.snap_checkbox = QCheckBox("Snap to beat")
        self.snap_checkbox.setChecked(cfg.snap_to_beat)
        self.snap_checkbox.setToolTip(
            "The tap pads and ripples wait for the next half beat of the beat clock, so a press a little early "
            "still lands on the music (one only just late goes at once). Without a locked clock they go at once."
        )
        self.snap_checkbox.toggled.connect(lambda on: self._set("snap_to_beat", on))
        pad_settings.addWidget(self.snap_checkbox)
        self.wave_slider = self._slider(
            "Wave length", 0.25, 4.0, "wave_beats", suffix=" beats", label_width=80,
            tooltip="How long a sweep, burst, ripple or flash takes, in beats of the beat clock (at 120 BPM while "
            "it has no tempo) - and how many bars the Orbit takes to go round.",
        )
        self.wave_slider._spin.setFixedWidth(96)
        pad_settings.addWidget(self.wave_slider, stretch=1)
        self.strobe_slider = self._slider(
            "Strobe rate", 2.0, 15.0, "strobe_hz", decimals=1, suffix=" /s", label_width=80,
            tooltip="The Strobe pad's ceiling on flashes a second. With the beat clock locked the rate is the "
            "fastest subdivision of the beat under it. The Lamp command rate limits it too: a flash is two "
            "commands, so 20 commands/s allow 10 flashes a second.",
        )
        pad_settings.addWidget(self.strobe_slider, stretch=1)
        left.addLayout(pad_settings)

        self.status_label = QLabel()
        self.status_label.setProperty("role", "note")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        left.addWidget(self.status_label)

        # -- the look, on the right -------------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(336)
        panel = QWidget()
        scroll.setWidget(panel)
        side = QVBoxLayout(panel)
        side.setContentsMargins(0, 0, 6, 0)
        side.setSpacing(8)
        root.addWidget(scroll)

        look_box = QGroupBox("Look")
        look_layout = QVBoxLayout(look_box)
        look_grid = QGridLayout()
        look_grid.setSpacing(5)
        self.look_group = QButtonGroup(self)
        self.look_buttons: Dict[str, QPushButton] = {}
        for index, (look, name, tooltip) in enumerate(_LOOKS):
            button = self._chip(name, tooltip)
            button.clicked.connect(lambda _checked=False, value=look: self._set_look(value))
            self.look_group.addButton(button)
            self.look_buttons[look] = button
            look_grid.addWidget(button, index // 3, index % 3)
        look_layout.addLayout(look_grid)
        self.palette_editor = PaletteEditor(cfg.palette)
        self.palette_editor.changed.connect(lambda colors: self._set("palette", list(colors)))
        look_layout.addWidget(self.palette_editor)
        side.addWidget(look_box)

        feel_box = QGroupBox("Brush && look")
        feel_layout = QVBoxLayout(feel_box)
        feel_layout.setSpacing(4)
        self.radius_slider = self._slider(
            "Radius", RADIUS_RANGE[0], RADIUS_RANGE[1], "radius",
            tooltip="How far the brush (and the spot) reaches, in stage heights. The mouse wheel over the stage "
            "changes it.",
        )
        self.softness_slider = self._slider(
            "Softness", 0.0, 1.0, "softness",
            tooltip="0: every lamp inside the radius gets the look in full. 1: it fades from the middle out. "
            "Also how soft the curtain's edge is.",
        )
        self.trail_slider = self._slider(
            "Trail", TRAIL_RANGE[0], TRAIL_RANGE[1], "trail_s", suffix=" s",
            tooltip="How long a lamp keeps the look after the pointer has left it - a comet's tail behind the "
            "brush. Shift + the mouse wheel over the stage changes it. Not used with Hold paint.",
        )
        self.speed_slider = self._slider(
            "Speed", 0.25, 4.0, "speed", suffix=" x",
            tooltip="How fast the look moves: the palette's drift, the plasma, the flicker, the sparkle and the "
            "strobe (16ths at 1, 8ths at 0.5), and how often the Storm pops.",
        )
        self.music_slider = self._slider(
            "Music pulse", 0.0, 1.0, "music",
            tooltip="How much the look's brightness follows the show under it. 0: the look at its own steady "
            "brightness. 1: as bright as the show has the lamp at that moment - it flashes and fades with the "
            "music, in the look's colours.",
        )
        self.brightness_slider = self._slider(
            "Brightness", 0.0, 1.0, "brightness",
            tooltip="The look's own brightness (the Visualizer tab's Brightness applies on top) - and the White "
            "look's level.",
        )
        self.hue_slider = self._slider(
            "Hue turn", 0.0, 360.0, "hue_shift_deg", decimals=0, suffix="°",
            tooltip="Complement: how far round the colour wheel the show's colour is turned. 180 = the "
            "complementary colour, 120 / 240 = a third of the way.",
        )
        self.temp_slider = self._slider(
            "White warm/cool", 0.0, 1.0, "white_temp",
            tooltip="The White look's and the Blinder pad's colour temperature: 0 = warmest, 1 = coolest.",
        )
        for slider in (self.radius_slider, self.softness_slider, self.trail_slider, self.speed_slider,
                       self.music_slider, self.brightness_slider, self.hue_slider, self.temp_slider):
            feel_layout.addWidget(slider)
        side.addWidget(feel_box)
        side.addStretch(1)

        self.tool_buttons[cfg.tool].setChecked(True)
        self.look_buttons[cfg.look].setChecked(True)
        self._update_states()
        controller.lampsChanged.connect(self._place_lamps)
        controller.runningChanged.connect(lambda _running: self._update_states())
        self._place_lamps()

    # -- building blocks ------------------------------------------------------------------------

    @staticmethod
    def _chip(text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", "chip")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.NoFocus)  # a click never takes the keyboard from the pads
        button.setToolTip(tooltip)
        return button

    def _slider(
        self, label: str, minimum: float, maximum: float, attr: str, decimals: int = 2, suffix: str = "",
        tooltip: str = "", label_width: int = 104,
    ) -> FloatSlider:
        slider = FloatSlider(
            label, minimum, maximum, getattr(self.controller.config.realtime, attr), decimals=decimals,
            suffix=suffix, tooltip=tooltip,
        )
        slider._label.setMinimumWidth(label_width)
        slider._spin.setFixedWidth(78)
        slider.valueChanged.connect(lambda value, a=attr: self._set(a, value))
        return slider

    # -- settings -------------------------------------------------------------------------------

    def _set(self, attr: str, value) -> None:
        setattr(self.controller.config.realtime, attr, value)
        self._update_states()
        self.stage.update()

    def _set_tool(self, tool: str) -> None:
        self.controller.config.realtime.tool = tool
        self.tool_buttons[tool].setChecked(True)
        self._update_states()

    def _set_look(self, look: str) -> None:
        self.controller.config.realtime.look = look
        self._update_states()

    def _sync_nudged(self) -> None:
        """The mouse wheel over the stage changed the radius or the trail."""
        cfg = self.controller.config.realtime
        self.radius_slider.set_value(cfg.radius)
        self.trail_slider.set_value(cfg.trail_s)

    def _update_states(self) -> None:
        """The hint line, and which of the look's settings the picked look uses."""
        cfg = self.controller.config.realtime
        arrange = self.arrange_button.isChecked()
        if arrange:
            self.hint_label.setText(
                "Arrange - drag each lamp to where it stands in the room (Shift: snap to a grid). The lamp you "
                "hold lights up white. Circle, Line and Grid place them all in the Chase order."
            )
        else:
            self.hint_label.setText(next(hint for _, tool, _, hint in _TOOLS if tool == cfg.tool))
        look = cfg.look
        self.palette_editor.setEnabled(look in _PALETTE_LOOKS)
        self.hue_slider.setVisible(look == "hue_shift")
        self.temp_slider.setVisible(look == "white")
        self.speed_slider.setEnabled(look in _ANIMATED_LOOKS)
        self.music_slider.setEnabled(look in _OWN_LIGHT_LOOKS and self.controller.is_running())
        self.brightness_slider.setEnabled(look in _OWN_LIGHT_LOOKS or look in ("white", "sparkle"))
        self.trail_slider.setEnabled(not cfg.latch)
        self.softness_slider.setEnabled(cfg.tool != "ripple")
        self.radius_slider.setEnabled(cfg.tool in ("brush", "spot"))
        self.throw_checkbox.setEnabled(cfg.tool == "brush")

    # -- the stage's layout -----------------------------------------------------------------------

    def _chase_orders(self) -> Dict[str, object]:
        return {device_id: effect.chase_order for device_id, effect in self.controller.config.per_lamp_effects.items()}

    def _place_lamps(self) -> None:
        """Every lamp gets a place on the stage (new ones too)."""
        self.stage.layer.ensure_positions(list(self.stage.lamps()), self._chase_orders())
        self.stage.update()

    def _auto_layout(self, kind: str) -> None:
        places = stage_layout(kind, list(self.stage.lamps()), self._chase_orders())
        self.controller.config.realtime.positions.update(places)
        self.stage.update()

    def _set_arrange(self, arrange: bool) -> None:
        self.stage.set_arrange(arrange)
        self.arrange_button.setText("Done arranging" if arrange else "Arrange lamps")
        for button in self.layout_buttons:
            button.setVisible(arrange)
        # The tools make way for the layout buttons: nothing is played while arranging.
        for widget in (*self.tool_buttons.values(), self.latch_checkbox, self.throw_checkbox, self.clear_button):
            widget.setVisible(not arrange)
        for pad in self.pads.values():
            pad.release()
            pad.setEnabled(not arrange)
        self._update_states()

    # -- the pads ---------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _on_hold(self, kind: str, on: bool) -> None:
        engine = self.controller.engine
        if kind == "strobe":
            message = engine.realtime_strobe(on)
            if on:
                self._say(message)
                if message.startswith("Strobe not played"):
                    self.pads["strobe"].release()
            elif self.status_label.text().startswith("Strobe: "):
                self._say("")  # (why it wasn't played stays up)
            return
        engine.realtime.hold(kind, on)

    def _on_tap(self, kind: str) -> None:
        engine = self.controller.engine
        if kind == "chase_sweep":
            self._say(self._sweep_line(engine.trigger_color_sweep_test(1)))
        elif kind == "dark_sweep":
            self._say(self._sweep_line(engine.trigger_dark_sweep_test(1)))
        else:
            engine.realtime.trigger(kind)

    @staticmethod
    def _sweep_line(message: str) -> str:
        """Only what went wrong is worth a line here (the Test buttons say what they played)."""
        return message if message.startswith("Not played") else ""

    def clear(self) -> None:
        """Esc: everything played by hand goes."""
        for pad in self.pads.values():
            pad.release()
        self.controller.engine.realtime.clear()
        self._say("")

    # -- the keyboard -----------------------------------------------------------------------------
    #
    # The pads are played from the keyboard wherever the focus is on this
    # tab, so a filter on the whole application while the tab is showing -
    # a key event otherwise goes to whichever slider was touched last.

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_lamps()
        self._update_states()
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event) -> None:
        QApplication.instance().removeEventFilter(self)
        self._let_go_of_keys()
        super().hideEvent(event)

    def _let_go_of_keys(self) -> None:
        self._keys_down.clear()
        for pad in self.pads.values():
            pad.release_momentary()

    def _typing(self) -> bool:
        """Whether the keys are going into a number box or a text field."""
        focus = QApplication.focusWidget()
        return isinstance(focus, (QAbstractSpinBox, QLineEdit, QTextEdit, QPlainTextEdit, QComboBox))

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind in (QEvent.WindowDeactivate, QEvent.ApplicationDeactivate):
            # The key or button up would go to another window: nothing stays stuck on.
            self._let_go_of_keys()
            self.stage.let_go()
            return False
        if kind not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if not self.isVisible() or not self.window().isActiveWindow():
            return False
        key = event.key()
        if kind == QEvent.KeyRelease:
            if event.isAutoRepeat() or key not in self._keys_down:
                return key in self._keys_down
            self._keys_down.discard(key)
            pad = self._pad_keys.get(key)
            if pad is not None:
                pad.key_up()
            return True
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier) or self._typing():
            return False
        if key in self._keys_down:
            return True  # held down: the key repeating, or the same press seen on its way up the widgets
        handled = self._key_pressed(key)
        if handled:
            self._keys_down.add(key)
        return handled

    def _key_pressed(self, key: int) -> bool:
        if key == _code(Qt.Key_Escape):
            self.clear()
            return True
        if self.arrange_button.isChecked():
            return False
        pad = self._pad_keys.get(key)
        if pad is not None:
            pad.key_down()
            return True
        for tool_key, tool, _, _ in _TOOLS:
            if key == _code(tool_key):
                self._set_tool(tool)
                return True
        return False
