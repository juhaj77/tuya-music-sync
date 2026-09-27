"""Shared helpers for settings that only make sense musically in certain
values, and for controls that only matter in certain modes.

- Beat divisions ("every N beats"): offered as fixed choices that line up
  with the bar - in 4/4: every beat, every 2 beats (half bar), every bar,
  every 2 bars, every 4 bars - instead of a free number. A free number like
  3 in 4/4 drifts against the bar (step on beat 1, then 4, then 3...), which
  reads as random - exactly what the shared clock is there to avoid.
- Phrase lengths: 1, 2, 4, 8 or 16 bars, the lengths music is built from.
- Inactive controls: a setting that has no effect in the current mode is
  greyed out, with the reason in its tooltip and an explanatory note shown
  next to it - so changing it can't look like the app ignored the change.
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from PySide6.QtWidgets import QComboBox, QLabel, QWidget

Choice = Tuple[int, str]

_INACTIVE_TOOLTIP_PREFIX = "Not used right now: "


def beat_divisions(beats_per_bar: int) -> List[Choice]:
    """Every-N-beats choices that line up with a bar of `beats_per_bar`:
    the divisors of the bar, then 1, 2 and 4 whole bars."""
    bpb = max(1, int(beats_per_bar))
    values = [d for d in range(1, bpb) if bpb % d == 0]
    values += [bpb, 2 * bpb, 4 * bpb]
    choices = []
    for v in values:
        if v == 1:
            label = "every beat"
        elif v < bpb:
            label = f"every {v} beats ({v}/{bpb} bar)"
        elif v == bpb:
            label = f"every bar ({v} beats)"
        else:
            label = f"every {v // bpb} bars ({v} beats)"
        choices.append((v, label))
    return choices


PHRASE_LENGTHS: List[Choice] = [(1, "1 bar"), (2, "2 bars"), (4, "4 bars"), (8, "8 bars"), (16, "16 bars")]


def nearest_choice(choices: Sequence[Choice], value: int) -> int:
    return min((v for v, _ in choices), key=lambda v: (abs(v - value), v))


def choice_combo(choices: Sequence[Choice], value: int, tooltip: str = "") -> QComboBox:
    """A combo of fixed musical choices. A saved value that isn't one of them
    (e.g. from an older version) shows as the nearest choice - read the value
    back with currentData() and store it, so what's shown is what's used."""
    combo = QComboBox()
    for v, label in choices:
        combo.addItem(label, v)
    combo.setCurrentIndex([v for v, _ in choices].index(nearest_choice(choices, value)))
    if tooltip:
        combo.setToolTip(tooltip)
    return combo


def set_active(widgets: Iterable[QWidget], active: bool, reason: str) -> None:
    """Grey out (or re-enable) settings, putting the reason at the start of
    their tooltip while they're inactive."""
    for w in widgets:
        tooltip = w.toolTip()
        if tooltip.startswith(_INACTIVE_TOOLTIP_PREFIX):
            tooltip = tooltip.split("\n\n", 1)[1] if "\n\n" in tooltip else ""
        if not active:
            tooltip = f"{_INACTIVE_TOOLTIP_PREFIX}{reason}" + (f"\n\n{tooltip}" if tooltip else "")
        w.setToolTip(tooltip)
        w.setEnabled(active)


def inactive_note() -> QLabel:
    """An initially hidden note that says why the settings around it are greyed out."""
    label = QLabel()
    label.setWordWrap(True)
    label.setStyleSheet("color: #c8812a; font-style: italic;")
    label.hide()
    return label


def show_note(label: QLabel, text: str) -> None:
    label.setText(text)
    label.setVisible(bool(text))
