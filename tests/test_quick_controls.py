"""The Visualizer tab's Quick Controls: the selected mode's own settings,
switched with the mode, each kept in step with its copy on the Color Mapping
tab (offscreen Qt, a scratch settings folder - nothing of the user's is
touched)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.ui.quick_settings import CLOCK_SENSITIVITY, QUICK_SETTINGS, get_value  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from airam_lights.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w.controller.shutdown()
    w.close()


def test_every_quick_setting_exists_in_the_config(window):
    for mode, settings in QUICK_SETTINGS.items():
        for setting in settings + [CLOCK_SENSITIVITY]:
            assert isinstance(get_value(window.controller.config, setting.path), float), (mode, setting.path)


def test_quick_controls_follow_the_mode(window):
    vis = window.tabs.widget(0)
    for mode in QUICK_SETTINGS:
        vis._mode_buttons[mode].setChecked(True)
        assert window.controller.config.color_mapping.mode == mode
        assert vis.mode_settings_stack.currentWidget() is vis._mode_settings_pages[mode]


def test_quick_slider_and_color_mapping_slider_follow_each_other(window):
    vis, cmt = window.tabs.widget(0), window.tabs.widget(2)
    bs = window.controller.config.color_mapping.beat_sync

    vis.quick_sliders[("beat_sync", "color_mapping.beat_sync.hue_attack_ms")].set_value(0)  # (set_value doesn't emit)
    vis.quick_sliders[("beat_sync", "color_mapping.beat_sync.hue_attack_ms")]._spin.setValue(222.0)
    assert bs.hue_attack_ms == pytest.approx(222.0)
    assert cmt.beat_hue_attack_slider.value() == pytest.approx(222.0)

    cmt.beat_sustain_slider._spin.setValue(0.4)
    assert bs.sustain_brightness == pytest.approx(0.4)
    assert vis.quick_sliders[("beat_sync", "color_mapping.beat_sync.sustain_brightness")].value() == pytest.approx(0.4)

    # A later edit elsewhere on the Color Mapping page writes the whole page
    # back - the quick change must survive it.
    cmt.beat_min_interval_slider._spin.setValue(80.0)
    assert bs.hue_attack_ms == pytest.approx(222.0)


def test_smoothing_is_on_each_level_mode_page_and_shared(window):
    vis, cmt = window.tabs.widget(0), window.tabs.widget(2)
    sm = window.controller.config.color_mapping.smoothing

    cmt.rgb_attack_slider._spin.setValue(150.0)
    assert sm.attack_ms == pytest.approx(150.0)
    for slider in (cmt.custom_attack_slider, cmt.hsv_attack_slider,
                   vis.quick_sliders[("rgb_freq", "color_mapping.smoothing.attack_ms")],
                   vis.quick_sliders[("hsv_music", "color_mapping.smoothing.attack_ms")]):
        assert slider.value() == pytest.approx(150.0)

    vis.quick_sliders[("custom", "color_mapping.smoothing.release_ms")]._spin.setValue(700.0)
    assert sm.release_ms == pytest.approx(700.0)
    assert cmt.hsv_release_slider.value() == pytest.approx(700.0)


def test_sensitivity_is_per_mode_now(window):
    vis = window.tabs.widget(0)
    cm = window.controller.config.color_mapping
    before = cm.hsv.sensitivity
    vis.quick_sliders[("rgb_freq", "color_mapping.rgb.sensitivity")]._spin.setValue(2.5)
    assert cm.rgb.sensitivity == pytest.approx(2.5)
    assert cm.hsv.sensitivity == before


def test_more_settings_link_opens_the_mode_page(window):
    window.controller.showModeSettings.emit("peak_flash")
    cmt = window.tabs.widget(2)
    assert window.tabs.currentWidget() is cmt
    assert cmt.sub_tabs.currentWidget() is cmt._mode_pages["peak_flash"]


def test_links_survive_the_color_mapping_tab_being_rebuilt(window, qapp):
    vis = window.tabs.widget(0)
    cm = window.controller.config.color_mapping
    cm.peak_flash.flash_release_ms = 900.0
    window.controller.replace_config_settings()  # a preset load
    qapp.processEvents()
    qapp.sendPostedEvents(None, 0)  # the old tab's deleteLater
    qapp.processEvents()
    assert vis.quick_sliders[("peak_flash", "color_mapping.peak_flash.flash_release_ms")].value() == pytest.approx(900.0)

    new_cmt = window.tabs.widget(2)
    vis.quick_sliders[("peak_flash", "color_mapping.peak_flash.flash_release_ms")]._spin.setValue(400.0)
    assert new_cmt.peak_flash_release_slider.value() == pytest.approx(400.0)


def test_beat_clock_sensitivity_is_in_every_mode_and_linked_to_the_rhythm_box(window):
    vis, cmt = window.tabs.widget(0), window.tabs.widget(2)
    vis.clock_sensitivity_slider._spin.setValue(1.3)
    assert window.controller.config.rhythm.sensitivity == pytest.approx(1.3)
    assert cmt.rhythm_sensitivity_slider.value() == pytest.approx(1.3)
    cmt.rhythm_sensitivity_slider._spin.setValue(1.2)
    assert vis.clock_sensitivity_slider.value() == pytest.approx(1.2)
    assert vis.clock_sensitivity_slider.parent() is not vis.mode_settings_stack.currentWidget()


def test_the_unused_sensitivity_is_greyed_out(window):
    vis = window.tabs.widget(0)
    config = window.controller.config
    own = vis.quick_sliders[("beat_sync", "color_mapping.beat_sync.sensitivity")]

    vis._mode_buttons["beat_sync"].setChecked(True)
    config.rhythm.shared_clock = True
    vis._update_sensitivity_states()
    assert vis.clock_sensitivity_slider.isEnabled() and not own.isEnabled()

    config.rhythm.shared_clock = False
    vis._update_sensitivity_states()
    assert own.isEnabled()

    config.chase.enabled = config.group_switch.enabled = False
    vis._mode_buttons["rgb_freq"].setChecked(True)  # nothing follows the clock now
    assert not vis.clock_sensitivity_slider.isEnabled()
    config.chase.enabled, config.chase.sync_mode = True, "clock"
    vis.hide(); vis.show()  # (coming back to the tab after changing it elsewhere)
    assert vis.clock_sensitivity_slider.isEnabled()
