"""The Visualizer tab's spectrum bars: the lowest bars are narrower than an
FFT bin (2048 points at 48 kHz = 23 Hz per bin) and must not show as gaps."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from airam_lights.ui.widgets.spectrum_widget import SpectrumWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _frame(mags):
    freqs = np.fft.rfftfreq(2048, d=1.0 / 48000)
    return SimpleNamespace(freqs=freqs, magnitude_db=mags(freqs))


def test_no_dark_bars_in_a_flat_spectrum(qapp):
    widget = SpectrumWidget()
    widget.set_frame(_frame(lambda f: np.full(len(f), -30.0)))
    assert np.allclose(widget._levels, widget._levels[0]) and widget._levels[0] > 0.3


def test_bars_with_bins_are_their_mean_and_the_rest_interpolated(qapp):
    widget = SpectrumWidget()
    frame = _frame(lambda f: -90.0 + 60.0 * (f < 200))  # loud bass, silent above
    db = widget._bar_db(frame)
    assert db[:8].min() > -40  # every bass bar lit, with or without a bin of its own
    assert db[-1] == pytest.approx(-90.0)
