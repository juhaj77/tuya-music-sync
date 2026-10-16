"""The info panel along the bottom of the main window, under the tabs (so it
never scrolls away): what the music sync is hearing and doing right now.

- First row: the beat clock - beats of the bar (the current one lit, the
  downbeat bigger), tempo and lock state - then the phrase position,
  loudness, drums share, the note and key, and the colour mode.
- Second row, while the pulse sequencer runs: a timeline of the last two
  bars on the clock's grid, scrolling left as the music plays - white
  flashes, dark pulses, strobes and color sweeps in their own lanes, the
  bar lines, the phrase starts - and a line of text naming the patterns
  playing and the latest pulses.

Reads the engine's state (VisualizationEngine.latest_clock_beat,
sequencer_status, seq_position, seq_events, ...) on its own timer; it
never changes anything.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...effects.pulse_sequencer import release_level
from .. import theme

_MODE_NAMES = {
    "beat_sync": "Beat Sync",
    "beat_sync_white": "Beat Sync White",
    "peak_flash": "Peak Flash",
    "rgb_freq": "RGB Frequency",
    "hsv_music": "HSV Music",
    "custom": "Custom",
}
_BG = QColor(theme.INPUT)
_BORDER = QColor(theme.BORDER)
_TEXT = QColor(theme.MUTED)
_DIM = QColor(theme.DIM)
_BEAT_LINE = QColor(theme.BORDER_SOFT)
_BAR_LINE = QColor("#3a4454")
_PHRASE = QColor(theme.WARN)
_NOW = QColor(235, 238, 244)
_WHITE = QColor(245, 247, 250)
_FLASH_STROBE = QColor(255, 213, 74)
_DARK = QColor(124, 104, 255)
_STROBE = QColor(77, 208, 225)
_LANES = ("White", "Dark", "Strobe", "Sweep")
_LANE_LABEL_W = 52
_WINDOW_BARS = 2
_NOW_AT = 0.85  # where "now" sits across the timeline: the rest shows the beats coming up


class BeatDots(QWidget):
    """The beats of the bar as dots: the current one lit (brightest right on
    the beat, then easing off), the downbeat drawn bigger."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._beats = 4
        self._current: Optional[int] = None
        self._beat_count = -1
        self._beat_at = 0.0

    def set_state(self, beats_per_bar: int, current: Optional[int], beat_count: int) -> None:
        if beat_count != self._beat_count:
            self._beat_count = beat_count
            self._beat_at = time.perf_counter()
        width = 18 * max(1, beats_per_bar) + 4
        if self.width() != width:
            self.setFixedWidth(width)
        self._beats, self._current = max(1, beats_per_bar), current
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        glow = max(0.35, 1.0 - (time.perf_counter() - self._beat_at) / 0.3)
        cy = self.height() / 2.0
        for i in range(self._beats):
            r = 6.5 if i == 0 else 4.5
            cx = 11.0 + 18.0 * i
            if i == self._current:
                color = QColor(_PHRASE) if i == 0 else QColor(_NOW)
                color.setAlphaF(glow)
                painter.setBrush(color)
                painter.setPen(Qt.NoPen)
            else:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(_DIM, 1.2))
            painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))


class SequencerTimeline(QWidget):
    """The last two bars of the pulse sequencer's pulses on the clock's grid
    (see the module docstring)."""

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.beats_per_bar = 4
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, _BG)
        small = QFont(painter.font())
        small.setPointSizeF(max(6.5, small.pointSizeF() - 1.5))
        painter.setFont(small)

        lane_h = (rect.height() - 4) / len(_LANES)
        for i, name in enumerate(_LANES):
            painter.setPen(_DIM)
            painter.drawText(QRectF(4, 2 + i * lane_h, _LANE_LABEL_W - 6, lane_h), Qt.AlignVCenter, name)
        left = float(_LANE_LABEL_W)
        width = max(1.0, rect.width() - left - 2)

        now = self.engine.seq_position
        status = self.engine.sequencer_status
        if now is None or status is None:
            painter.setPen(_DIM)
            painter.drawText(
                QRectF(left, 0, width, rect.height()), Qt.AlignCenter, "Waiting for the beat clock to lock..."
            )
            self._border(painter, rect)
            return

        bpb = max(1, self.beats_per_bar)
        window = _WINDOW_BARS * bpb
        start = now - _NOW_AT * window
        scale = width / window

        def x_of(pos: float) -> float:
            return left + (pos - start) * scale

        painter.setClipRect(QRectF(left, 0, width + 2, rect.height()))
        # The grid: beats, bar lines, and the phrase's first bar line.
        phrase_bars = max(1, int(status["phrase_bars"]))
        bar_now = math.floor(now / bpb)
        phrase_start_bar = bar_now - int(status["phrase_bar"])
        for beat in range(math.ceil(start), math.floor(start + window) + 1):
            x = x_of(beat)
            if beat % bpb:
                painter.setPen(QPen(_BEAT_LINE, 1.0))
            elif (beat // bpb - phrase_start_bar) % phrase_bars == 0:
                painter.setPen(QPen(_PHRASE, 1.6))
            else:
                painter.setPen(QPen(_BAR_LINE, 1.2))
            painter.drawLine(int(x), 1, int(x), rect.height() - 2)

        for e in list(self.engine.seq_events):
            pos, length = e["pos"], max(0.0, e["length"])
            if pos > now + 0.5 or pos + length < start:
                continue  # from before the grid jumped, or scrolled out
            lane = _LANES.index({"white": "White", "dark": "Dark", "strobe": "Strobe", "sweep": "Sweep"}[e["kind"]])
            top = 2 + lane * lane_h + 2
            h = lane_h - 4
            x = x_of(pos)
            w = max(3.0, length * scale)
            if e["kind"] == "white":
                self._draw_white(painter, e, x, top, h, scale)
            elif e["kind"] == "dark":
                painter.fillRect(QRectF(x, top, w, h), _DARK)
            elif e["kind"] == "strobe":
                painter.setPen(QPen(_STROBE, 1.0))
                painter.drawRect(QRectF(x, top, w, h))
                step = max(2.0, scale / 4.0)
                stripe = x
                while stripe < x + w:
                    painter.drawLine(int(stripe), int(top), int(stripe), int(top + h))
                    stripe += step
            else:  # sweep: the sweep's colour, brightening along it like the run round the lamps
                hue = int(e.get("hue", 200.0)) % 360
                segments = 8
                for k in range(segments):
                    color = QColor.fromHsv(hue, 230, 120 + int(135 * (k + 1) / segments))
                    painter.fillRect(QRectF(x + w * k / segments, top, w / segments + 0.5, h), color)

        painter.setPen(QPen(_NOW, 1.5))
        nx = x_of(now)
        painter.drawLine(int(nx), 1, int(nx), rect.height() - 2)
        painter.setClipping(False)
        self._border(painter, rect)

    @staticmethod
    def _draw_white(painter: QPainter, e: dict, x: float, top: float, h: float, scale: float) -> None:
        """A white flash as its envelope: up over the attack, flat through
        the hold (its duration), down over the release along its release
        curve - as tall as it is bright. The walk's flashes carry their
        group's number; a flash on every lamp (the phrase start) is outlined
        in gold; a flash strobe is yellow."""
        attack, hold, release = (max(0.0, e.get(k, 0.0)) * scale for k in ("attack", "hold", "release"))
        if attack + hold + release < 3.0:
            hold = 3.0  # too short to see its shape: at least a sliver
        bottom = top + h
        peak = bottom - h * max(0.25, min(1.0, e["brightness"]))
        points = [QPointF(x, bottom), QPointF(x + attack, peak), QPointF(x + attack + hold, peak)]
        curve = e.get("curve", "linear")
        for k in range(1, 9):
            t = k / 8.0
            points.append(QPointF(x + attack + hold + release * t, bottom - (bottom - peak) * release_level(t, curve)))
        points.append(QPointF(x + attack + hold + release, bottom))
        everywhere = e["group"] is None or e["reason"] == "phrase"
        fill = QColor(_FLASH_STROBE) if e.get("flash_strobe") else QColor(_WHITE)
        fill.setAlphaF(0.75 if e.get("flash_strobe") else 0.55)
        painter.setBrush(fill)
        painter.setPen(QPen(_PHRASE if everywhere else fill.lighter(), 1.0))
        painter.drawPolygon(QPolygonF(points))
        painter.setBrush(Qt.NoBrush)
        if not everywhere:
            painter.setPen(_TEXT)
            painter.drawText(QRectF(x + 2, top - 3, 24, h * 0.5), Qt.AlignLeft, str(e["group"] + 1))

    @staticmethod
    def _border(painter: QPainter, rect) -> None:
        painter.setPen(_BORDER)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))


class InfoPanel(QFrame):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setFrameShape(QFrame.StyledPanel)
        self.setProperty("role", "panel")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 8)
        root.setSpacing(4)

        row = QHBoxLayout()
        self.beat_dots = BeatDots()
        row.addWidget(self.beat_dots)
        self.clock_label = QLabel()
        self.clock_label.setTextFormat(Qt.RichText)
        row.addWidget(self.clock_label, stretch=1)
        root.addLayout(row)

        self.sequencer_box = QWidget()
        seq_layout = QVBoxLayout(self.sequencer_box)
        seq_layout.setContentsMargins(0, 0, 0, 0)
        seq_layout.setSpacing(2)
        self.timeline = SequencerTimeline(controller.engine)
        self.timeline.setToolTip(
            "The pulse sequencer, the last two bars on the beat clock's grid (now = the bright line, the "
            "beats coming up to its right). White: the white flashes as their envelope - up over the "
            "attack, flat through the duration, down over the release along its curve, as tall as they "
            "are bright; the walk's with their group's number, outlined in gold when every lamp flashes "
            "(the phrase start), yellow when played as a flash strobe. Dark: the dark pulses. Strobe: the bursts. Sweep: the color sweeps, in their "
            "colour. Grey lines are bar lines, the golden one a phrase start."
        )
        seq_layout.addWidget(self.timeline)
        self.sequencer_label = QLabel()
        self.sequencer_label.setTextFormat(Qt.RichText)
        seq_layout.addWidget(self.sequencer_label)
        root.addWidget(self.sequencer_box)

        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(33)
        self._refresh()

    def _refresh(self) -> None:
        engine = self.controller.engine
        config = self.controller.config
        bpb = max(1, config.rhythm.beats_per_bar)
        running = engine.running
        beat = engine.latest_clock_beat if running and engine.clock_active else None
        locked = beat is not None and (beat.locked or beat.coasting or beat.source in ("flow", "free"))
        self.beat_dots.set_state(bpb, beat.bar_position if locked else None, beat.beat_count if beat else -1)

        status = engine.sequencer_status
        self.sequencer_box.setVisible(status is not None)
        if status is not None:
            self.timeline.beats_per_bar = bpb
            self.timeline.update()

        self._ticks += 1
        if self._ticks % 6 != 1:  # the text ~5 times a second is plenty
            return
        self.clock_label.setText(self._clock_text(engine, config, beat, bpb))
        if status is not None:
            self.sequencer_label.setText(self._sequencer_text(engine, status))

    @staticmethod
    def _clock_text(engine, config, beat, bpb: int) -> str:
        mode = _MODE_NAMES.get(config.color_mapping.mode, config.color_mapping.mode)
        if not engine.running:
            return f"<span style='color:{theme.MUTED}'>Stopped - press Start on the Visualizer tab. Mode: {mode}</span>"
        parts = []
        if beat is None:
            parts.append(f"<span style='color:{theme.MUTED}'>Beat clock idle (nothing follows it in this mode)</span>")
        else:
            tempo = f"<b>{beat.bpm:.0f} BPM</b>" if beat.bpm else "<b>finding tempo...</b>"
            if beat.coasting:
                lock = "coasting on the last tempo"
            elif beat.source == "flow":
                lock = "following the notes (no beat)"
            elif beat.source == "free":
                lock = "free pace (no steady tempo)"
            else:
                lock = "locked" if beat.locked else "not locked"
            parts.append(f"{tempo} {lock}, beat {beat.bar_position + 1}/{bpb}")
        status = engine.sequencer_status
        if status is not None:
            parts.append(
                f"phrase bar {status['phrase_bar'] + 1}/{status['phrase_bars']}, "
                f"loudness <b>{status['level']}</b> ({status['intensity']:.0%})"
            )
        features = engine.latest_features
        if beat is not None and features.harmonic is not None:
            parts.append(f"drums {features.percussive_share:.0%}")
        pitch = engine.latest_pitch
        if pitch is not None and pitch.pitch_class is not None:
            parts.append(f"note <b>{pitch.note_name}</b> in {pitch.key_name} (clear {pitch.confidence:.0%})")
        parts.append(f"mode {mode}")
        return "  &middot;  ".join(parts)

    @staticmethod
    def _sequencer_text(engine, status: dict) -> str:
        sq = engine.config.sequencer
        parts = [f"Sequencer: white <b>{status['white_pattern']}</b>", f"dark {' + '.join(status['dark_patterns'])}"]
        latest = {}
        for e in engine.seq_events:
            latest[e["kind"]] = e
        white = latest.get("white")
        if white is not None:
            if white["group"] is None:
                where = "every lamp"
            else:
                where = f"group {white['group'] + 1}"
            what = "flash strobe" if white.get("flash_strobe") else "white"
            parts.append(f"last {what}: {where} ({white['reason']})")
        if sq.strobe_enabled and status.get("strobe"):
            parts.append(f"last strobe: {status['strobe']['flashes']} flashes at {status['strobe']['hz']:.1f}/s")
        if sq.color_sweep_enabled and "sweep" in latest:
            parts.append(f"last sweep: {latest['sweep']['length']:g} beats ({latest['sweep']['reason']})")
        return f"<span style='color:{theme.MUTED}'>" + "  &middot;  ".join(parts) + "</span>"
