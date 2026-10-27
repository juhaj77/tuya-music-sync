"""The dark theme, the toggle switch and the collapsible sections, and that
the whole main window still builds on them (offscreen Qt, a scratch
settings folder - nothing of the user's is touched)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.ui import theme  # noqa: E402
from airam_lights.ui.widgets.musical import set_active  # noqa: E402
from airam_lights.ui.widgets.section import CollapsibleSection  # noqa: E402
from airam_lights.ui.widgets.toggle_switch import ToggleSwitch  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """The app's settings folder (config.json, ui.ini) under a temp dir."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


# -- theme ---------------------------------------------------------------------------------


def test_theme_applies_without_style_sheet_warnings(qapp):
    messages = []
    previous = qInstallMessageHandler(
        lambda kind, context, text: messages.append(text) if kind != QtMsgType.QtDebugMsg else None
    )
    try:
        theme.apply_theme(qapp)
        qapp.processEvents()
    finally:
        qInstallMessageHandler(previous)
    assert not [m for m in messages if "stylesheet" in m.lower() or "css" in m.lower()], messages
    assert qapp.styleSheet()
    assert os.path.exists(theme.asset_url("check.svg")[4:-1])  # the icon files are where the sheet says


# -- toggle switch ---------------------------------------------------------------------------


def test_toggle_switch_has_a_check_boxs_api(qapp):
    switch = ToggleSwitch(False)
    seen = []
    switch.toggled.connect(seen.append)
    looks = []
    switch.lookChanged.connect(lambda: looks.append(True))
    switch.setChecked(True)
    assert switch.isChecked() and seen == [True]
    switch.click()
    assert not switch.isChecked() and seen == [True, False]
    before = len(looks)
    switch.setToolTip("why")
    switch.setEnabled(False)
    assert len(looks) >= before + 2  # the section mirrors both onto its header


# -- collapsible section ----------------------------------------------------------------------


def test_section_opens_and_closes_from_its_header(qapp, settings_dir):
    section = CollapsibleSection("Dark pulses", key="test/dark", checkable=True, checked=True, tooltip="dip")
    section.show()
    assert not section.is_expanded()  # starts closed, even with the feature on
    assert section.toggle.isChecked()
    section._header.clicked.emit()
    assert section.is_expanded() and not section.content_widget().isHidden()
    section.toggle_expanded()
    assert not section.is_expanded() and section.content_widget().isHidden()


def test_section_remembers_whether_it_was_open(qapp, settings_dir):
    first = CollapsibleSection("Strobe", key="test/strobe", checkable=True, checked=False)
    assert not first.is_expanded()  # off -> starts closed
    first.set_expanded(True)
    again = CollapsibleSection("Strobe", key="test/strobe", checkable=True, checked=False)
    assert again.is_expanded()  # ...but was left open last time
    assert (settings_dir / "AiramMusicLights" / "ui.ini").exists() or any(settings_dir.rglob("ui.ini"))
    plain = CollapsibleSection("Global", key="test/global", expanded=True)
    assert plain.is_expanded() and plain.toggle is None


def test_section_header_mirrors_the_switch(qapp, settings_dir):
    section = CollapsibleSection("White pulses", checkable=True, checked=True, tooltip="flash")
    assert section._header.toolTip() == "flash"
    assert section._title.property("off") == "false"
    section.toggle.setChecked(False)
    assert section._title.property("off") == "true"
    set_active([section.toggle], False, "The sequencer is off.")
    qapp.processEvents()
    assert section._header.toolTip().startswith("Not used right now") and "sequencer" in section._header.toolTip()
    assert not section._title.isEnabled()
    set_active([section.toggle], True, "")
    qapp.processEvents()
    assert section._header.toolTip() == "flash" and section._title.isEnabled()


# -- the real windows ----------------------------------------------------------------------------


def test_main_window_builds_on_the_sections(qapp, settings_dir):
    from airam_lights.ui.main_window import MainWindow

    theme.apply_theme(qapp)
    window = MainWindow()
    try:
        window.show()
        qapp.processEvents()
        assert window.tabs.tabText(3) == "Global Effects"
        color = window.tabs.widget(2)  # Color Mapping: each mode's own settings
        titles = {s._title.text() for s in color.findChildren(CollapsibleSection)}
        assert {"Beat detection", "Colour on the hit", "Between hits", "Global", "Presets"} <= titles
        assert not {"Rhythm · shared beat clock", "Dark pulses", "Pulse sequencer"} & titles
        effects = window.tabs.widget(3)  # Global Effects: what every mode shares, in three blocks
        blocks = [effects.clock_block, effects.overlays_block, effects.pulses_block]
        for block, expected in zip(blocks, (
            {"Rhythm · shared beat clock"},
            {"Chase · rotating light", "Group Switch · one group at a time"},
            {"Dark pulses", "White pulses", "Pulse sequencer", "Strobe", "Flash strobe", "Color sweep", "Dark sweep"},
        )):
            assert {s._title.text() for s in block.findChildren(CollapsibleSection)} == expected
        layout = effects.clock_block.parentWidget().layout()
        assert [layout.indexOf(b) for b in blocks] == [0, 1, 2]  # the beat clock first
        # Each block has a tint of its own.
        backgrounds = {theme.BLOCK_TINTS[b.property("tint")][0] for b in blocks}
        assert len(backgrounds) == 3
        for block in blocks:
            assert block.grab().toImage().pixelColor(block.width() // 2, 3).name() == theme.BLOCK_TINTS[
                block.property("tint")][0]
        # The feature switches are the sections' toggles, and still drive the config.
        pulses = effects.pulses
        sq = window.controller.config.sequencer
        pulses.seq_sweep_checkbox.setChecked(not sq.color_sweep_enabled)
        assert sq.color_sweep_enabled == pulses.seq_sweep_checkbox.isChecked()
        pulses.seq_dark_sweep_checkbox.setChecked(not sq.dark_sweep_enabled)
        assert sq.dark_sweep_enabled == pulses.seq_dark_sweep_checkbox.isChecked()
        pulses.seq_dark_sweep_desaturate_slider._spin.setValue(0.8)
        assert sq.dark_sweep_desaturate == pytest.approx(0.8)
        pulses.beat_dark_desaturate_slider._spin.setValue(0.4)
        assert window.controller.config.color_mapping.beat_sync.dark_pulse_desaturate == pytest.approx(0.4)
        overlays = effects.overlays
        assert overlays.chase_enabled_checkbox is overlays.chase_box.toggle
        overlays.chase_enabled_checkbox.setChecked(False)
        assert window.controller.config.chase.enabled is False
    finally:
        window.controller.shutdown()
        window.close()


def test_manual_window_builds_too(qapp, settings_dir):
    from airam_lights.ui.manual_main_window import ManualMainWindow

    window = ManualMainWindow()
    try:
        window.show()
        qapp.processEvents()
    finally:
        window.controller.shutdown()
        window.close()
