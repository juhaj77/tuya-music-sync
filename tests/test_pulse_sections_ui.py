"""The Color Mapping tab's shared pulse sections - Dark pulses, White pulses
and the Pulse sequencer - at the bottom of every mode page, one set of
settings for all of them, with what a mode doesn't use greyed out or hidden;
and Beat Sync White's 'Change temperature' (offscreen Qt, a scratch settings
folder - nothing of the user's is touched)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.config.schema import AppConfig  # noqa: E402

MODES = ("beat_sync", "beat_sync_white", "peak_flash", "rgb_freq", "hsv_music", "custom")


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


def _tab(window, qapp, mode):
    """The Color Mapping tab, showing `mode`'s page."""
    window.controller.showModeSettings.emit(mode)
    qapp.processEvents()
    return window.tabs.widget(2)


def test_the_pulse_sections_sit_at_the_bottom_of_every_mode_page(window, qapp):
    for mode in MODES:
        cmt = _tab(window, qapp, mode)
        page = cmt._mode_pages[mode]
        layout = page.layout()
        assert cmt.pulse_box.parentWidget() is page and cmt.pulse_box.isVisible()
        # The last thing on the page, above its closing stretch.
        assert layout.indexOf(cmt.pulse_box) == layout.count() - 2
        assert layout.itemAt(layout.count() - 1).spacerItem() is not None


def test_the_pulse_settings_are_one_set_for_every_mode(window, qapp):
    sq = window.controller.config.sequencer
    cmt = _tab(window, qapp, "rgb_freq")
    cmt.seq_white_density_slider._spin.setValue(0.33)
    cmt.beat_dark_duration_slider._spin.setValue(123.0)
    assert sq.white_density == pytest.approx(0.33)
    assert window.controller.config.color_mapping.beat_sync.dark_pulse_duration_ms == pytest.approx(123.0)
    cmt = _tab(window, qapp, "peak_flash")
    assert cmt.seq_white_density_slider.value() == pytest.approx(0.33)
    cmt.seq_enabled_checkbox.setChecked(False)  # one switch for every mode
    assert sq.enabled is False
    cmt = _tab(window, qapp, "beat_sync")
    assert not cmt.seq_enabled_checkbox.isChecked()
    assert cmt.beat_dark_duration_slider.value() == pytest.approx(123.0)


def test_beat_syncs_per_beat_pulse_settings_only_show_on_its_page(window, qapp):
    per_beat = ("beat_dark_trigger_row", "beat_dark_prob_slider", "beat_white_target_box", "beat_white_trigger_row",
                "beat_white_pulse_prob_slider")
    for mode in MODES:
        cmt = _tab(window, qapp, mode)
        for name in per_beat:
            assert getattr(cmt, name).isHidden() == (mode != "beat_sync"), (mode, name)
    # The 'Rotating' choice is the sequencer's walk's too, so it stays everywhere.
    assert not cmt.beat_white_rotators_combo.isHidden()


def test_the_other_modes_use_the_sequencer_without_follow_the_shared_clock(window, qapp):
    config = window.controller.config
    cmt = _tab(window, qapp, "beat_sync")
    cmt.rhythm_shared_checkbox.setChecked(False)
    assert config.sequencer.enabled and not config.rhythm.shared_clock
    assert not cmt.seq_enabled_checkbox.isEnabled() and "shared beat clock" in cmt.seq_note.text()
    cmt = _tab(window, qapp, "peak_flash")
    assert cmt.seq_enabled_checkbox.isEnabled() and cmt.seq_white_combo.isEnabled()
    assert cmt.beat_white_pulse_duration_slider.isEnabled() and cmt.beat_dark_depth_slider.isEnabled()
    cmt.rhythm_lock_checkbox.setChecked(False)  # no grid for the pulses - in any mode
    assert not cmt.seq_enabled_checkbox.isEnabled() and "Lock to tempo" in cmt.seq_note.text()
    assert not cmt.beat_dark_depth_slider.isEnabled() and "Pulse sequencer" in cmt.beat_dark_note.text()


def test_without_the_sequencer_the_other_modes_grey_the_pulse_look_out(window, qapp):
    cmt = _tab(window, qapp, "hsv_music")
    cmt.seq_enabled_checkbox.setChecked(False)
    for widget in (cmt.beat_dark_enabled_checkbox, cmt.beat_dark_duration_slider, cmt.beat_white_pulse_enabled_checkbox,
                   cmt.beat_white_pulse_duration_slider, cmt.beat_white_rotators_combo):
        assert not widget.isEnabled()
    assert "only the Pulse sequencer" in cmt.beat_white_note.text()
    cmt = _tab(window, qapp, "beat_sync")  # Beat Sync still rolls its own per-beat pulses
    assert cmt.beat_dark_duration_slider.isEnabled() and cmt.beat_white_pulse_duration_slider.isEnabled()


def test_beat_sync_white_page_uses_only_the_dark_pulses(window, qapp):
    config = window.controller.config
    assert config.sequencer.enabled and config.rhythm.shared_clock and config.rhythm.tempo_lock  # (fresh install)
    cmt = _tab(window, qapp, "beat_sync_white")
    assert "only its dark pulses" in cmt.seq_note.text()
    assert cmt.seq_dark_combo.isEnabled() and cmt.seq_phrase_combo.isEnabled() and cmt.beat_dark_depth_slider.isEnabled()
    for widget in (cmt.seq_white_combo, cmt.seq_walk_combo, cmt.seq_dynamics_slider, cmt.seq_strobe_checkbox,
                   cmt.seq_flash_strobe_checkbox, cmt.seq_sweep_checkbox, cmt.seq_dark_sweep_checkbox,
                   cmt.beat_white_pulse_enabled_checkbox, cmt.beat_white_pulse_duration_slider,
                   cmt.beat_dark_desaturate_slider):
        assert not widget.isEnabled()
    assert "no saturation" in cmt.beat_dark_desaturate_slider.toolTip()
    assert "Beat Sync White" in cmt.seq_strobe_checkbox.toolTip()
    # Its own per-beat dark pulses step aside while the sequencer places them...
    assert not cmt.bsw_dark_prob_slider.isEnabled() and cmt.bsw_dark_seq_note.isVisibleTo(cmt)
    cmt.seq_enabled_checkbox.setChecked(False)
    # ...and are back without it, while the shared Dark pulses look is then unused here.
    assert cmt.bsw_dark_prob_slider.isEnabled() and not cmt.bsw_dark_seq_note.isVisibleTo(cmt)
    assert not cmt.beat_dark_depth_slider.isEnabled()
    # Leaving the page puts the sequencer's white settings back.
    cmt.seq_enabled_checkbox.setChecked(True)
    cmt = _tab(window, qapp, "custom")
    assert cmt.seq_white_combo.isEnabled() and cmt.beat_white_pulse_enabled_checkbox.isEnabled()
    assert cmt.seq_dark_sweep_checkbox.isEnabled() and cmt.beat_dark_desaturate_slider.isEnabled()


def test_beat_sync_white_detector_greys_out_on_the_shared_clock(window, qapp):
    cmt = _tab(window, qapp, "beat_sync_white")
    assert window.controller.config.rhythm.shared_clock
    assert not cmt.bsw_sensitivity_slider.isEnabled()
    cmt.rhythm_shared_checkbox.setChecked(False)
    assert cmt.bsw_sensitivity_slider.isEnabled()


def test_change_temperature_every_n_beats(window, qapp):
    config = window.controller.config
    cmt = _tab(window, qapp, "beat_sync_white")
    combo = cmt.bsw_temp_every_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 2, 4, 8, 16]  # 4/4: in step with the bar
    combo.setCurrentIndex(1)
    assert config.color_mapping.beat_sync_white.temp_every_n_beats == 2
    assert AppConfig.from_dict(config.to_dict()).color_mapping.beat_sync_white.temp_every_n_beats == 2
    assert not cmt.bsw_temp_every_note.isVisibleTo(cmt)  # the clock with Lock to tempo: counted from the bar
    cmt.rhythm_lock_checkbox.setChecked(False)
    assert cmt.bsw_temp_every_note.isVisibleTo(cmt) and "bar start" in cmt.bsw_temp_every_note.text()


def test_the_sections_survive_a_preset_load(window, qapp):
    cmt = _tab(window, qapp, "peak_flash")
    window.controller.replace_config_settings()  # (a preset load rebuilds the tab)
    qapp.processEvents()
    qapp.sendPostedEvents(None, 0)
    qapp.processEvents()
    cmt = _tab(window, qapp, "rgb_freq")
    assert cmt.pulse_box.parentWidget() is cmt._mode_pages["rgb_freq"] and cmt.pulse_box.isVisible()
    assert cmt.seq_enabled_checkbox.isEnabled()
