"""The Real Time tab: the stage, the tools, the look and the pads drive the
engine's Real Time layer (offscreen Qt, a scratch settings folder, lamps
that are never sent anything - see test_realtime for the layer itself)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.config.schema import DeviceConfig  # noqa: E402
from airam_lights.ui import theme  # noqa: E402

LAMPS = ["lamp0", "lamp1", "lamp2", "lamp3"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """The main window on the Real Time tab, with four lamps that only
    record what they would be sent."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from airam_lights.ui.main_window import MainWindow

    theme.apply_theme(qapp)
    window = MainWindow()
    controller = window.controller
    for timer in (controller._status_timer, controller._realtime_timer):
        timer.stop()
    manager = controller.lamp_manager
    window.sent = []
    monkeypatch.setattr(manager, "push_colors", lambda colors: window.sent.append(("colour", dict(colors))))
    monkeypatch.setattr(manager, "push_white_targets", lambda targets: window.sent.append(("white", dict(targets))))
    monkeypatch.setattr(manager, "start_strobe", lambda bursts: window.sent.append(("strobe", dict(bursts))))
    for index, device_id in enumerate(LAMPS):
        controller.add_device(DeviceConfig(id=device_id, name=f"Lamp {index}", ip="192.0.2.1", local_key="0" * 16))
        controller.get_or_create_effect(device_id).chase_order = index
    window.resize(1000, 750)
    window.tabs.setCurrentIndex(4)
    window.show()
    window.activateWindow()
    qapp.processEvents()
    try:
        yield window
    finally:
        controller.shutdown()
        window.close()
        qapp.processEvents()


def _tab(window):
    return window.tabs.widget(4)


def _at(stage, lamp) -> QPoint:
    return stage._to_pixels(*stage.layer.position(lamp)).toPoint()


def test_the_tab_sits_after_the_settings_tabs_and_survives_a_preset_load(window, qapp):
    from airam_lights.ui.tabs.realtime_tab import RealtimeTab

    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["Visualizer", "Devices && Setup", "Color Mapping", "Global Effects", "Real Time", "Diagnostics"]
    tab = _tab(window)
    assert isinstance(tab, RealtimeTab)
    window.controller.replace_config_settings()  # a preset load rebuilds the settings tabs by index
    qapp.processEvents()
    assert window.tabs.widget(4) is tab and window.tabs.tabText(3) == "Global Effects"


def test_every_lamp_gets_a_place_on_the_stage(window):
    positions = window.controller.config.realtime.positions
    assert set(positions) == set(LAMPS)
    assert len({tuple(place) for place in positions.values()}) == len(LAMPS)
    window.grab()  # the stage paints with the lamps on it


def test_dragging_the_brush_over_a_lamp_paints_it(window, qapp):
    tab = _tab(window)
    stage, engine = tab.stage, window.controller.engine
    assert not engine.realtime.active
    target = _at(stage, "lamp1")
    QTest.mousePress(stage, Qt.LeftButton, Qt.NoModifier, target + QPoint(-40, 0))
    QTest.mouseMove(stage, target)
    assert engine.realtime.active and engine.realtime.pointer_state()[0] == "brush"
    engine.tick_realtime_idle()  # the show is stopped: the layer plays alone
    kind, colors = window.sent[-1]
    assert kind == "colour" and "lamp1" in colors and max(colors["lamp1"].to_rgb255()) > 100
    assert engine.realtime.ink_levels()["lamp1"][0] > 0.5
    QTest.mouseRelease(stage, Qt.LeftButton, Qt.NoModifier, target)
    assert engine.realtime.pointer_state() is None
    window.grab()


def test_a_right_click_drops_a_ripple(window):
    tab = _tab(window)
    layer = window.controller.engine.realtime
    QTest.mouseClick(tab.stage, Qt.RightButton, Qt.NoModifier, _at(tab.stage, "lamp2"))
    assert len(layer.wave_shapes()) == 1 and layer.pointer_state() is None


def test_tools_looks_and_the_palette_are_picked_on_the_tab(window, qapp):
    tab = _tab(window)
    cfg = window.controller.config.realtime
    tab.tool_buttons["curtain"].click()
    assert cfg.tool == "curtain"
    # The curtain has its width and stretch where the brush has its radius.
    assert tab.radius_slider.isHidden() and not tab.curtain_slider.isHidden() and not tab.stretch_slider.isHidden()
    tab.curtain_slider._spin.setValue(1.5)
    tab.stretch_slider._spin.setValue(0.7)
    assert cfg.curtain_width == pytest.approx(1.5) and cfg.curtain_stretch_s == pytest.approx(0.7)
    tab.curtain_slider._spin.setValue(3.0)  # all the way up: no back edge, nothing to stretch
    assert not tab.stretch_slider.isEnabled()
    tab.tool_buttons["brush"].click()
    assert not tab.radius_slider.isHidden() and tab.curtain_slider.isHidden() and tab.stretch_slider.isHidden()
    tab.look_buttons["hue_shift"].click()
    assert cfg.look == "hue_shift"
    assert not tab.hue_slider.isHidden() and tab.temp_slider.isHidden()
    assert not tab.palette_editor.isEnabled()  # Complement is made of the show's colour, not the palette
    tab.look_buttons["flicker"].click()
    chip = next(c for c in tab.palette_editor.preset_chips if c.name == "Fire")
    chip.click()
    assert cfg.palette == ["#ff1e00", "#ff7a00", "#ffd000"]
    assert window.controller.engine.realtime.style.look == "flicker"
    assert len(window.controller.engine.realtime.style.palette) == 3
    tab.radius_slider._spin.setValue(0.5)
    tab.latch_checkbox.setChecked(True)
    assert cfg.radius == pytest.approx(0.5) and cfg.latch and not tab.trail_slider.isEnabled()


def test_the_wheel_sets_the_radius_and_shift_wheel_the_trail(window, qapp):
    tab = _tab(window)
    cfg = window.controller.config.realtime
    before_radius, before_trail = cfg.radius, cfg.trail_s

    def wheel(modifiers):
        point = QPointF(_at(tab.stage, "lamp0"))
        event = QWheelEvent(
            point, tab.stage.mapToGlobal(point), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, modifiers,
            Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(tab.stage, event)

    wheel(Qt.NoModifier)
    assert cfg.radius > before_radius and cfg.trail_s == before_trail
    assert tab.radius_slider.value() == pytest.approx(cfg.radius, abs=0.01)
    wheel(Qt.ShiftModifier)
    assert cfg.trail_s > before_trail
    assert tab.trail_slider.value() == pytest.approx(cfg.trail_s, abs=0.01)
    # With the curtain picked the wheel is its width.
    tab.tool_buttons["curtain"].click()
    radius, width = cfg.radius, cfg.curtain_width
    wheel(Qt.NoModifier)
    assert cfg.radius == radius and cfg.curtain_width == pytest.approx(width * 1.12)
    assert tab.curtain_slider.value() == pytest.approx(cfg.curtain_width, abs=0.01)


def test_wave_length_slider_reaches_eight_bars_with_room_for_the_short_ones(window):
    tab = _tab(window)
    cfg = window.controller.config.realtime
    slider = tab.wave_slider
    slider._slider.setValue(0)
    assert cfg.wave_beats == pytest.approx(0.25)
    slider._slider.setValue(1000)
    assert cfg.wave_beats == pytest.approx(32.0)
    slider._slider.setValue(500)  # half way along: the middle of the ratios, not of the numbers
    assert cfg.wave_beats == pytest.approx((0.25 * 32.0) ** 0.5, abs=0.01)
    slider._spin.setValue(4.0)  # typed: the slider follows
    assert cfg.wave_beats == pytest.approx(4.0) and slider._slider.value() == 571
    slider.set_value(1.0)
    assert slider._slider.value() == 286 and slider.value() == pytest.approx(1.0)


def test_arranging_moves_a_lamp_and_lights_it_up(window, qapp):
    tab = _tab(window)
    stage, layer = tab.stage, window.controller.engine.realtime
    tab.arrange_button.setChecked(True)
    assert stage.arrange and tab.layout_buttons[0].isVisibleTo(tab) and not tab.pads["flood"].isEnabled()
    start = _at(stage, "lamp2")
    before = list(layer.position("lamp2"))
    QTest.mousePress(stage, Qt.LeftButton, Qt.NoModifier, start)
    assert layer.identify == "lamp2"  # the lamp in hand lights up in the room
    QTest.mouseMove(stage, start + QPoint(60, 30))
    QTest.mouseRelease(stage, Qt.LeftButton, Qt.NoModifier, start + QPoint(60, 30))
    after = layer.position("lamp2")
    assert layer.identify is None and layer.pointer_state() is None  # nothing was painted
    assert after[0] > before[0] and after[1] > before[1]
    tab.layout_buttons[1].click()  # Line: in the Chase order, left to right
    xs = [layer.position(lamp)[0] for lamp in LAMPS]
    assert xs == sorted(xs) and len({layer.position(lamp)[1] for lamp in LAMPS}) == 1
    tab.arrange_button.setChecked(False)
    assert tab.pads["flood"].isEnabled() and not tab.layout_buttons[0].isVisibleTo(tab)


def test_hold_pads_are_on_while_held_and_latch_with_shift(window):
    tab = _tab(window)
    layer = window.controller.engine.realtime
    pad = tab.pads["blackout"]
    centre = pad.rect().center()
    QTest.mousePress(pad, Qt.LeftButton, Qt.NoModifier, centre)
    assert layer.held() == ["blackout"] and pad.is_on() and not pad.is_latched()
    QTest.mouseRelease(pad, Qt.LeftButton, Qt.NoModifier, centre)
    assert layer.held() == [] and not pad.is_on()
    QTest.mouseClick(pad, Qt.LeftButton, Qt.ShiftModifier, centre)
    assert layer.held() == ["blackout"] and pad.is_latched()  # stays on after the click
    QTest.mouseClick(pad, Qt.LeftButton, Qt.NoModifier, centre)
    assert layer.held() == [] and not pad.is_latched()
    QTest.mouseClick(tab.pads["flood"], Qt.RightButton, Qt.NoModifier, centre)
    assert layer.held() == ["flood"]
    tab.clear()  # Esc lets go of latched pads too
    assert layer.held() == [] and not tab.pads["flood"].is_on()


def test_tap_pads_fire_one_shots_and_the_sequencers_sweeps(window, monkeypatch):
    tab = _tab(window)
    engine = window.controller.engine
    tab.pads["sweep_right"].key_down()
    tab.pads["burst"].key_down()
    assert [shape["kind"] for shape in engine.realtime.wave_shapes()] == ["linear", "radial"]
    played = []
    monkeypatch.setattr(engine, "trigger_color_sweep_test", lambda repeats: played.append(("color", repeats)) or "ok")
    monkeypatch.setattr(
        engine, "trigger_dark_sweep_test",
        lambda repeats: played.append(("dark", repeats)) or "Not played: start the visualizer first.",
    )
    tab.pads["chase_sweep"].key_down()
    assert played == [("color", 1)] and tab.status_label.isHidden()
    tab.pads["dark_sweep"].key_down()
    assert played[-1] == ("dark", 1)
    assert not tab.status_label.isHidden() and tab.status_label.text().startswith("Not played")


def test_the_strobe_pad_plays_and_stops_the_engines_strobe(window):
    tab = _tab(window)
    window.controller.config.network.lamp_transitions = "direct"
    pad = tab.pads["strobe"]
    pad.key_down()
    kind, bursts = window.sent[-1]
    assert kind == "strobe" and set(bursts) == set(LAMPS) and bursts["lamp0"].flashes > 10
    assert tab.status_label.text().startswith("Strobe: ")
    pad.key_up()
    kind, bursts = window.sent[-1]
    assert kind == "strobe" and all(burst.flashes == 0 for burst in bursts.values())
    assert tab.status_label.isHidden()
    # Bulbs that can't: the pad says why and doesn't stay down.
    window.controller.config.network.lamp_transitions = "legacy"
    count = len(window.sent)
    pad.key_down()
    assert len(window.sent) == count and not pad.is_on()
    assert tab.status_label.text().startswith("Strobe not played")


def test_the_keyboard_plays_the_pads_wherever_the_focus_is(window, qapp):
    tab = _tab(window)
    layer = window.controller.engine.realtime
    if not window.isActiveWindow():
        pytest.skip("this platform can't activate a window")
    tab.speed_slider._slider.setFocus()  # a slider was touched last
    QTest.keyPress(window, Qt.Key_3)
    assert layer.held() == ["blackout"] and tab.pads["blackout"].is_on()
    QTest.keyPress(window, Qt.Key_5)  # two holds at once
    assert sorted(layer.held()) == ["blackout", "flood"]
    QTest.keyRelease(window, Qt.Key_3)
    QTest.keyRelease(window, Qt.Key_5)
    assert layer.held() == []
    QTest.keyClick(window, Qt.Key_U)
    QTest.keyClick(window, Qt.Key_Space)
    assert len(layer._waves) == 2 and all(wave.kind == "flash" for wave in layer._waves)
    QTest.keyClick(window, Qt.Key_X)
    assert window.controller.config.realtime.tool == "curtain" and tab.tool_buttons["curtain"].isChecked()
    QTest.keyClick(window, Qt.Key_Escape)
    assert layer._waves == []
    # In a number box the keys are numbers.
    tab.radius_slider._spin.setFocus()
    QTest.keyPress(tab.radius_slider._spin, Qt.Key_3)
    QTest.keyRelease(tab.radius_slider._spin, Qt.Key_3)
    assert layer.held() == []


def test_leaving_the_tab_lets_go_of_what_was_held(window, qapp):
    tab = _tab(window)
    layer = window.controller.engine.realtime
    stage = tab.stage
    QTest.mousePress(stage, Qt.LeftButton, Qt.NoModifier, _at(stage, "lamp0"))
    tab.pads["freeze"].key_down()
    assert layer.pointer_state() is not None and layer.held() == ["freeze"]
    window.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert layer.pointer_state() is None and layer.held() == []
