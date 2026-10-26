"""The Global Effects tab: the shared beat clock, Chase / Group Switch and the
pulses - Dark pulses, White pulses and the Pulse sequencer - one set of
settings for every colour mode, with what the active mode doesn't use greyed
out or hidden; Beat Sync White's own pulse settings on the Color Mapping tab
and its 'Change temperature' (offscreen Qt, a scratch settings folder -
nothing of the user's is touched)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.config.schema import AppConfig  # noqa: E402
from airam_lights.ui.tabs.pulses_panel import MODE_NAMES  # noqa: E402

MODES = ("beat_sync", "beat_sync_white", "peak_flash", "hsv_music", "band_colors")


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


def _effects(window, qapp, mode):
    """The Global Effects tab, shown with `mode` picked on the Visualizer tab."""
    window.tabs.setCurrentIndex(0)
    window.tabs.widget(0)._mode_buttons[mode].setChecked(True)
    window.tabs.setCurrentIndex(3)
    qapp.processEvents()
    return window.tabs.widget(3)


def _color_mapping(window, qapp, mode=None):
    """The Color Mapping tab, shown (on `mode`'s page)."""
    if mode is not None:
        window.controller.showModeSettings.emit(mode)
    else:
        window.tabs.setCurrentIndex(2)
    qapp.processEvents()
    return window.tabs.widget(2)


def test_the_pulses_follow_the_active_mode_not_the_page_shown(window, qapp):
    for mode in MODES:
        effects = _effects(window, qapp, mode)
        assert MODE_NAMES[mode] in effects.pulses_block.description.text()
    effects = _effects(window, qapp, "beat_sync")
    assert not effects.pulses.beat_dark_prob_slider.isHidden()  # Beat Sync's per-beat probability
    _color_mapping(window, qapp, "band_colors")  # looking at another mode's page changes nothing
    window.tabs.setCurrentIndex(3)
    qapp.processEvents()
    assert not effects.pulses.beat_dark_prob_slider.isHidden()
    assert not window.tabs.widget(2).findChildren(type(effects.pulses))  # none left on Color Mapping


def test_the_pulse_settings_are_one_set_for_every_mode(window, qapp):
    sq = window.controller.config.sequencer
    pulses = _effects(window, qapp, "band_colors").pulses
    pulses.seq_white_density_slider._spin.setValue(0.33)
    pulses.beat_dark_duration_slider._spin.setValue(123.0)
    assert sq.white_density == pytest.approx(0.33)
    assert window.controller.config.color_mapping.beat_sync.dark_pulse_duration_ms == pytest.approx(123.0)
    pulses = _effects(window, qapp, "peak_flash").pulses
    assert pulses.seq_white_density_slider.value() == pytest.approx(0.33)
    pulses.seq_enabled_checkbox.setChecked(False)  # one switch for every mode
    assert sq.enabled is False
    pulses = _effects(window, qapp, "beat_sync").pulses
    assert not pulses.seq_enabled_checkbox.isChecked()
    assert pulses.beat_dark_duration_slider.value() == pytest.approx(123.0)


def test_beat_syncs_per_beat_pulse_settings_only_show_in_beat_sync(window, qapp):
    per_beat = ("beat_dark_trigger_row", "beat_dark_prob_slider", "beat_white_target_box", "beat_white_trigger_row",
                "beat_white_pulse_prob_slider")
    for mode in MODES:
        pulses = _effects(window, qapp, mode).pulses
        for name in per_beat:
            assert getattr(pulses, name).isHidden() == (mode != "beat_sync"), (mode, name)
    # The 'Rotating' choice is the sequencer's walk's too, so it stays everywhere.
    assert not pulses.beat_white_rotators_combo.isHidden()


def test_the_other_modes_use_the_sequencer_without_follow_the_shared_clock(window, qapp):
    config = window.controller.config
    effects = _effects(window, qapp, "beat_sync")
    pulses = effects.pulses
    effects.rhythm.rhythm_shared_checkbox.setChecked(False)  # the pulses catch up at once
    assert config.sequencer.enabled and not config.rhythm.shared_clock
    assert not pulses.seq_enabled_checkbox.isEnabled() and "shared beat clock" in pulses.seq_note.text()
    pulses = _effects(window, qapp, "peak_flash").pulses
    assert pulses.seq_enabled_checkbox.isEnabled() and pulses.seq_white_combo.isEnabled()
    assert pulses.beat_white_pulse_duration_slider.isEnabled() and pulses.beat_dark_depth_slider.isEnabled()
    effects.rhythm.rhythm_lock_checkbox.setChecked(False)  # no grid for the pulses - in any mode
    assert not pulses.seq_enabled_checkbox.isEnabled() and "Lock to tempo" in pulses.seq_note.text()
    assert not pulses.beat_dark_depth_slider.isEnabled() and "Pulse sequencer" in pulses.beat_dark_note.text()


def test_without_the_sequencer_the_other_modes_grey_the_pulse_look_out(window, qapp):
    pulses = _effects(window, qapp, "hsv_music").pulses
    pulses.seq_enabled_checkbox.setChecked(False)
    for widget in (pulses.beat_dark_enabled_checkbox, pulses.beat_dark_duration_slider,
                   pulses.beat_white_pulse_enabled_checkbox, pulses.beat_white_pulse_duration_slider,
                   pulses.beat_white_rotators_combo):
        assert not widget.isEnabled()
    assert "only the Pulse sequencer" in pulses.beat_white_note.text()
    pulses = _effects(window, qapp, "beat_sync").pulses  # Beat Sync still rolls its own per-beat pulses
    assert pulses.beat_dark_duration_slider.isEnabled() and pulses.beat_white_pulse_duration_slider.isEnabled()


def test_the_strobe_follows_the_lamp_transitions_set_on_color_mapping(window, qapp):
    pulses = _effects(window, qapp, "beat_sync").pulses
    assert pulses.seq_strobe_checkbox.isEnabled()
    cmt = _color_mapping(window, qapp)
    cmt.transitions_combo.setCurrentText("legacy")
    pulses = _effects(window, qapp, "beat_sync").pulses
    assert not pulses.seq_strobe_checkbox.isEnabled() and "Color Mapping tab" in pulses.seq_strobe_note.text()


def test_beat_sync_white_uses_only_the_dark_pulses(window, qapp):
    config = window.controller.config
    assert config.sequencer.enabled and config.rhythm.shared_clock and config.rhythm.tempo_lock  # (fresh install)
    pulses = _effects(window, qapp, "beat_sync_white").pulses
    assert "only its dark pulses" in pulses.seq_note.text()
    assert pulses.seq_dark_combo.isEnabled() and pulses.seq_phrase_combo.isEnabled()
    assert pulses.beat_dark_depth_slider.isEnabled()
    for widget in (pulses.seq_white_combo, pulses.seq_walk_combo, pulses.seq_dynamics_slider,
                   pulses.seq_strobe_checkbox, pulses.seq_flash_strobe_checkbox, pulses.seq_sweep_checkbox,
                   pulses.beat_white_pulse_enabled_checkbox, pulses.beat_white_pulse_duration_slider,
                   pulses.beat_dark_desaturate_slider):
        assert not widget.isEnabled()
    assert "no saturation" in pulses.beat_dark_desaturate_slider.toolTip()
    # The dark sweep dims its white - all but the desaturate, white has no saturation.
    pulses.seq_dark_sweep_checkbox.setChecked(True)
    assert pulses.seq_dark_sweep_checkbox.isEnabled() and pulses.seq_dark_sweep_depth_slider.isEnabled()
    assert not pulses.seq_dark_sweep_desaturate_slider.isEnabled()
    assert "no saturation" in pulses.seq_dark_sweep_desaturate_slider.toolTip()
    assert "Beat Sync White" in pulses.seq_strobe_checkbox.toolTip()
    # Its own per-beat dark pulses (on its Color Mapping page) step aside while the sequencer places them...
    cmt = _color_mapping(window, qapp, "beat_sync_white")
    assert not cmt.bsw_dark_prob_slider.isEnabled() and cmt.bsw_dark_seq_note.isVisibleTo(cmt)
    pulses = _effects(window, qapp, "beat_sync_white").pulses
    pulses.seq_enabled_checkbox.setChecked(False)
    # ...and are back without it, while the shared Dark pulses look is then unused here.
    assert not pulses.beat_dark_depth_slider.isEnabled()
    cmt = _color_mapping(window, qapp, "beat_sync_white")
    assert cmt.bsw_dark_prob_slider.isEnabled() and not cmt.bsw_dark_seq_note.isVisibleTo(cmt)
    # Another mode gets the sequencer's white settings back.
    pulses = _effects(window, qapp, "beat_sync_white").pulses
    pulses.seq_enabled_checkbox.setChecked(True)
    pulses = _effects(window, qapp, "hsv_music").pulses
    assert pulses.seq_white_combo.isEnabled() and pulses.beat_white_pulse_enabled_checkbox.isEnabled()
    assert pulses.seq_dark_sweep_checkbox.isEnabled() and pulses.beat_dark_desaturate_slider.isEnabled()


def test_beat_sync_white_detector_greys_out_on_the_shared_clock(window, qapp):
    cmt = _color_mapping(window, qapp, "beat_sync_white")
    assert window.controller.config.rhythm.shared_clock
    assert not cmt.bsw_sensitivity_slider.isEnabled()
    _effects(window, qapp, "beat_sync_white").rhythm.rhythm_shared_checkbox.setChecked(False)
    cmt = _color_mapping(window, qapp)  # catches up with the Rhythm box when it's shown
    assert cmt.bsw_sensitivity_slider.isEnabled()


def test_change_temperature_every_n_beats(window, qapp):
    config = window.controller.config
    cmt = _color_mapping(window, qapp, "beat_sync_white")
    combo = cmt.bsw_temp_every_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 2, 4, 8, 16]  # 4/4: in step with the bar
    combo.setCurrentIndex(1)
    assert config.color_mapping.beat_sync_white.temp_every_n_beats == 2
    assert AppConfig.from_dict(config.to_dict()).color_mapping.beat_sync_white.temp_every_n_beats == 2
    assert not cmt.bsw_temp_every_note.isVisibleTo(cmt)  # the clock with Lock to tempo: counted from the bar
    _effects(window, qapp, "beat_sync_white").rhythm.rhythm_lock_checkbox.setChecked(False)
    cmt = _color_mapping(window, qapp)
    assert cmt.bsw_temp_every_note.isVisibleTo(cmt) and "bar start" in cmt.bsw_temp_every_note.text()


def test_the_color_mapping_tab_is_as_tall_as_the_mode_page_shown(window, qapp):
    """A short mode page doesn't get the tallest page's height (a long empty
    space above the Global box)."""
    cmt = _color_mapping(window, qapp, "beat_sync")  # (its sections start closed)
    short, short_hfw = cmt.sub_tabs.sizeHint().height(), cmt.sub_tabs.heightForWidth(900)
    cmt = _color_mapping(window, qapp, "peak_flash")
    tall, tall_hfw = cmt.sub_tabs.sizeHint().height(), cmt.sub_tabs.heightForWidth(900)
    assert short < tall - 300 and short_hfw < tall_hfw - 300  # (the latter for the word-wrapped notes)
    cmt = _color_mapping(window, qapp, "beat_sync")
    assert cmt.sub_tabs.sizeHint().height() == short and cmt.sub_tabs.heightForWidth(900) == short_hfw
    # The Global box follows right under the page, not under the tallest page's height.
    qapp.processEvents()
    assert cmt.sub_tabs.height() <= short_hfw


def test_the_blocks_survive_a_preset_load(window, qapp):
    _effects(window, qapp, "peak_flash")
    window.controller.replace_config_settings()  # (a preset load rebuilds the settings tabs)
    qapp.processEvents()
    qapp.sendPostedEvents(None, 0)
    qapp.processEvents()
    effects = _effects(window, qapp, "band_colors")
    assert window.tabs.tabText(3) == "Global Effects"
    assert effects.pulses.seq_enabled_checkbox.isEnabled() and effects.pulses.isVisible()
    assert effects.rhythm.isVisible() and effects.overlays.isVisible()
