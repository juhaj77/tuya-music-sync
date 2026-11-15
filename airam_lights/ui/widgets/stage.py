"""The Real Time tab's stage: every lamp where it stands in the room, glowing
in the colour it shows right now - and the surface the pointer tools are
played on (effects/realtime.py does the work; this draws it and hands it the
pointer).

Two ways to use it:

- Play (the default): the pointer is the tool picked on the tab - press and
  drag over the lamps. The wheel sets the brush's radius (with Shift: the
  trail), Ctrl+drag erases held paint, a right-click drops a ripple.
- Arrange: drag each lamp to its place. The lamp being held lights up in
  the room, so it's easy to tell which is which.
"""
from __future__ import annotations

import math
import time
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...color.models import Color
from ...effects.realtime import RealtimeLayer, hex_to_rgb
from .. import theme

_MARGIN = 6
_GRAB_PX = 26.0  # how near a lamp a press must be to pick it up
_EDGE = 0.03  # lamps stay this far inside the stage
RADIUS_RANGE = (0.05, 1.2)
TRAIL_RANGE = (0.0, 5.0)


def _qcolor(color: Color, alpha: int = 255) -> QColor:
    r, g, b = color.to_rgb255()
    return QColor(r, g, b, alpha)


class StageCanvas(QWidget):
    settingsNudged = Signal()  # the wheel changed the radius or the trail - the sliders catch up
    layoutChanged = Signal()  # a lamp was moved

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.arrange = False
        self._hover: Optional[QPointF] = None
        self._pressed = False
        self._drag_lamp: Optional[str] = None
        self._drag_offset = QPointF(0.0, 0.0)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(33)
        self._update_cursor()

    def sizeHint(self) -> QSize:
        return QSize(640, 360)

    @property
    def layer(self) -> RealtimeLayer:
        return self.controller.engine.realtime

    def set_arrange(self, arrange: bool) -> None:
        self.arrange = arrange
        self.let_go()
        self._update_cursor()
        self.update()

    def _update_cursor(self) -> None:
        self.setCursor(Qt.OpenHandCursor if self.arrange else Qt.CrossCursor)

    def _refresh(self) -> None:
        if self.isVisible():
            self.update()

    # -- geometry ---------------------------------------------------------------------------

    def stage_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(_MARGIN, _MARGIN, -_MARGIN, -_MARGIN)

    def _to_stage(self, point: QPointF) -> Tuple[float, float]:
        stage = self.stage_rect()
        x = (point.x() - stage.left()) / max(1.0, stage.width())
        y = (point.y() - stage.top()) / max(1.0, stage.height())
        return max(-0.25, min(1.25, x)), max(-0.25, min(1.25, y))

    def _to_pixels(self, x: float, y: float) -> QPointF:
        stage = self.stage_rect()
        return QPointF(stage.left() + x * stage.width(), stage.top() + y * stage.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        stage = self.stage_rect()
        self.layer.aspect = max(0.2, stage.width() / max(1.0, stage.height()))

    def lamps(self) -> Dict[str, object]:
        """The lamps on the stage: every enabled device."""
        return {
            device_id: device for device_id, device in self.controller.lamp_manager.devices.items()
            if device.config.enabled
        }

    def _lamp_at(self, point: QPointF) -> Optional[str]:
        best, best_distance = None, _GRAB_PX
        for device_id in self.lamps():
            place = self._to_pixels(*self.layer.position(device_id))
            distance = math.hypot(place.x() - point.x(), place.y() - point.y())
            if distance < best_distance:
                best, best_distance = device_id, distance
        return best

    # -- the pointer --------------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        point = event.position()
        self._hover = point
        if self.arrange:
            lamp = self._lamp_at(point) if event.button() == Qt.LeftButton else None
            if lamp is not None:
                self._drag_lamp = lamp
                self._drag_offset = self._to_pixels(*self.layer.position(lamp)) - point
                self.layer.identify = lamp
                self.setCursor(Qt.ClosedHandCursor)
            return
        x, y = self._to_stage(point)
        if event.button() == Qt.RightButton:
            self.layer.trigger("ripple", origin=(x, y))
        elif event.button() == Qt.LeftButton:
            erase = bool(event.modifiers() & Qt.ControlModifier)
            self.layer.pointer_down(self.controller.config.realtime.tool, x, y, erase=erase)
            self._pressed = True
        self.update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        self._hover = point
        if self._drag_lamp is not None:
            x, y = self._to_stage(point + self._drag_offset)
            if event.modifiers() & Qt.ShiftModifier:  # snap to a grid
                x, y = round(x * 20.0) / 20.0, round(y * 20.0) / 20.0
            x, y = max(_EDGE, min(1.0 - _EDGE, x)), max(_EDGE, min(1.0 - _EDGE, y))
            self.controller.config.realtime.positions[self._drag_lamp] = [x, y]
        elif self._pressed:
            self.layer.pointer_move(*self._to_stage(point))
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.let_go()
        self.update()

    def let_go(self) -> None:
        """The button came up (or the stage lost the pointer): the stroke or the lamp in hand is let go of."""
        if self._drag_lamp is not None:
            self._drag_lamp = None
            self.layer.identify = None
            self._update_cursor()
            self.layoutChanged.emit()
        if self._pressed:
            self._pressed = False
            self.layer.pointer_up()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def hideEvent(self, event) -> None:
        self.let_go()
        super().hideEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        steps = (delta.y() or delta.x()) / 120.0
        if self.arrange or steps == 0.0:
            return
        cfg = self.controller.config.realtime
        if event.modifiers() & Qt.ShiftModifier:
            trail = cfg.trail_s * 1.25 ** steps if cfg.trail_s > 0.04 else (0.1 if steps > 0 else 0.0)
            cfg.trail_s = 0.0 if trail < 0.05 else min(TRAIL_RANGE[1], trail)
        else:
            cfg.radius = max(RADIUS_RANGE[0], min(RADIUS_RANGE[1], cfg.radius * 1.12 ** steps))
        self.settingsNudged.emit()
        event.accept()
        self.update()

    # -- drawing --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stage = self.stage_rect()
        layer = self.layer
        engine = self.controller.engine
        active = layer.active

        painter.setPen(QPen(QColor(theme.LIVE if active else theme.BORDER), 1.0))
        painter.setBrush(QColor(theme.STAGE))
        painter.drawRoundedRect(stage, 10.0, 10.0)
        painter.setClipRect(stage.adjusted(1, 1, -1, -1))
        self._draw_grid(painter, stage)
        self._draw_curtain(painter, stage)
        self._draw_waves(painter, stage)

        colors = engine.latest_lamp_colors if engine.running else self.controller.lamp_manager.shown_colors()
        scale = max(0.7, min(1.6, stage.height() / 360.0))
        ink = layer.ink_levels()
        effects = self.controller.config.per_lamp_effects
        label_font = QFont(self.font())
        label_font.setPointSizeF(max(7.0, theme.FONT_SIZE_PT - 2.0))
        painter.setFont(label_font)
        for device_id, device in self.lamps().items():
            centre = self._to_pixels(*layer.position(device_id))
            selected = device.config.selected
            color = colors.get(device_id) if selected else None
            self._draw_lamp(painter, centre, color, selected, scale, device_id == self._drag_lamp)
            live, held = ink.get(device_id, (0.0, 0.0))
            self._draw_ink(painter, centre, live, held, scale)
            name = device.config.name
            effect = effects.get(device_id)
            if self.arrange and effect is not None and effect.chase_order is not None:
                name = f"{name}  ·  {effect.chase_order}"
            painter.setPen(QColor(theme.TEXT if self.arrange else theme.MUTED))
            box = QRectF(centre.x() - 70.0, centre.y() + 17.0 * scale, 140.0, 16.0)
            painter.drawText(box, Qt.AlignHCenter | Qt.AlignTop, name)

        for x, y in layer.glider_points():
            self._draw_brush(painter, self._to_pixels(x, y), stage, pressed=True, ghost=True)
        if self._hover is not None and not self.arrange:
            self._draw_tool(painter, self._hover, stage)
        painter.setClipping(False)
        self._draw_badge(painter, stage, active)

    @staticmethod
    def _draw_grid(painter: QPainter, stage: QRectF) -> None:
        painter.setPen(QPen(QColor(theme.BORDER_SOFT), 1.5))
        columns = 16
        step = stage.width() / columns
        rows = max(1, int(round(stage.height() / step)))
        for i in range(1, columns):
            for j in range(1, rows):
                painter.drawPoint(QPointF(stage.left() + i * step, stage.top() + j * stage.height() / rows))

    def _draw_curtain(self, painter: QPainter, stage: QRectF) -> None:
        state = self.layer.pointer_state()
        if state is None or state[0] != "curtain":
            return
        _, x, y, ax, ay, _ = state
        here, start = self._to_pixels(x, y), self._to_pixels(ax, ay)
        dx, dy = here.x() - start.x(), here.y() - start.y()
        if math.hypot(dx, dy) < 4.0:
            return
        far = stage.width() + stage.height()
        painter.save()
        painter.translate(here)
        painter.rotate(math.degrees(math.atan2(dy, dx)))
        shade = QColor(theme.LIVE)
        shade.setAlpha(26)
        painter.fillRect(QRectF(-far, -far, far, 2.0 * far), shade)  # behind the edge: covered
        painter.setPen(QPen(QColor(theme.LIVE), 1.5))
        painter.drawLine(QPointF(0.0, -far), QPointF(0.0, far))
        painter.restore()
        painter.setPen(QPen(QColor(theme.LIVE_DIM), 1.0, Qt.DashLine))
        painter.drawLine(start, here)

    def _draw_waves(self, painter: QPainter, stage: QRectF) -> None:
        height = stage.height()
        painter.setBrush(Qt.NoBrush)
        for shape in self.layer.wave_shapes(time.perf_counter()):
            r, g, b = shape["rgb"]
            color = QColor(round(r * 255), round(g * 255), round(b * 255), int(40 + 150 * shape["fade"]))
            painter.setPen(QPen(color, 2.0))
            origin = self._to_pixels(shape["x"], shape["y"])
            front = shape["front"] * height
            kind = shape["kind"]
            if kind == "radial":
                painter.drawEllipse(origin, front, front)
            elif kind == "implode":
                radius = shape["reach"] * height - front
                if radius > 0.0:
                    painter.drawEllipse(origin, radius, radius)
            elif kind in ("open", "close"):
                offset = front if kind == "open" else shape["reach"] * height - front
                for side in (-1.0, 1.0):
                    x = origin.x() + side * offset
                    painter.drawLine(QPointF(x, stage.top()), QPointF(x, stage.bottom()))
            else:  # "linear": a front square to its direction
                x = origin.x() + shape["dx"] * front
                painter.drawLine(QPointF(x, stage.top()), QPointF(x, stage.bottom()))

    @staticmethod
    def _draw_lamp(
        painter: QPainter, centre: QPointF, color: Optional[Color], selected: bool, scale: float, held: bool
    ) -> None:
        core = 11.0 * scale
        if color is not None:
            level = max(color.r, color.g, color.b)
            if level > 0.02:
                # The glow: as wide and as strong as the lamp is bright, in its hue at full strength.
                k = 1.0 / level
                hue = Color(color.r * k, color.g * k, color.b * k)
                reach = core * (2.2 + 3.6 * level)
                glow = QRadialGradient(centre, reach)
                glow.setColorAt(0.0, _qcolor(hue, int(60 + 150 * level)))
                glow.setColorAt(0.45, _qcolor(hue, int(20 + 60 * level)))
                glow.setColorAt(1.0, _qcolor(hue, 0))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(glow))
                painter.drawEllipse(centre, reach, reach)
            painter.setBrush(_qcolor(color))
        else:
            painter.setBrush(QColor(theme.CARD))
        outline = QColor("#ffffff") if held else QColor(theme.BORDER if selected else theme.DIM)
        painter.setPen(QPen(outline, 2.0 if held else 1.2, Qt.SolidLine if selected else Qt.DotLine))
        painter.drawEllipse(centre, core, core)

    @staticmethod
    def _draw_ink(painter: QPainter, centre: QPointF, live: float, held: float, scale: float) -> None:
        """A pink ring round a painted lamp: thin and whole for held paint,
        a thick arc as long as the stroke's trail is strong."""
        ring = 16.0 * scale
        box = QRectF(centre.x() - ring, centre.y() - ring, 2.0 * ring, 2.0 * ring)
        painter.setBrush(Qt.NoBrush)
        if held > 0.01:
            color = QColor(theme.LIVE)
            color.setAlpha(int(60 + 150 * held))
            painter.setPen(QPen(color, 1.2))
            painter.drawEllipse(box)
        if live > 0.01:
            painter.setPen(QPen(QColor(theme.LIVE), 2.4, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(box, 90 * 16, -int(360 * 16 * min(1.0, live)))

    def _draw_tool(self, painter: QPainter, point: QPointF, stage: QRectF) -> None:
        tool = self.controller.config.realtime.tool
        if tool in ("brush", "spot"):
            self._draw_brush(painter, point, stage, pressed=self._pressed, spot=tool == "spot")
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.LIVE), 1.4))
        if tool == "ripple":
            for radius in (5.0, 11.0):
                painter.drawEllipse(point, radius, radius)
        elif not self._pressed:  # the curtain, before it's pulled
            painter.drawLine(point + QPointF(0.0, -12.0), point + QPointF(0.0, 12.0))
            painter.drawLine(point + QPointF(-5.0, 0.0), point + QPointF(5.0, 0.0))

    def _draw_brush(
        self, painter: QPainter, point: QPointF, stage: QRectF, pressed: bool, spot: bool = False,
        ghost: bool = False,
    ) -> None:
        cfg = self.controller.config.realtime
        radius = cfg.radius * stage.height()
        if not spot:
            # The palette as the brush lays it: the first colour in the middle, the last at the rim.
            stops = [hex_to_rgb(c) for c in cfg.palette] or [(1.0, 1.0, 1.0)]
            if cfg.look not in ("palette", "plasma", "flicker", "sparkle", "strobe"):
                stops = [hex_to_rgb(theme.LIVE)]
            fill = QRadialGradient(point, radius)
            strength = 80 if pressed else 30
            for index, (r, g, b) in enumerate(stops):
                at = 0.0 if len(stops) == 1 else 0.9 * index / (len(stops) - 1)
                fill.setColorAt(at, QColor(round(r * 255), round(g * 255), round(b * 255), strength))
            r, g, b = stops[-1]
            fill.setColorAt(1.0, QColor(round(r * 255), round(g * 255), round(b * 255), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawEllipse(point, radius, radius)
        painter.setBrush(Qt.NoBrush)
        rim = QColor("#ffffff" if pressed and not ghost else theme.LIVE)
        painter.setPen(QPen(rim, 1.5, Qt.DashLine if spot else Qt.SolidLine))
        painter.drawEllipse(point, radius, radius)
        if cfg.softness > 0.02 and not ghost:
            inner = radius * (1.0 - cfg.softness)
            painter.setPen(QPen(QColor(theme.LIVE_DIM), 1.0, Qt.DotLine))
            painter.drawEllipse(point, inner, inner)

    def _draw_badge(self, painter: QPainter, stage: QRectF, active: bool) -> None:
        engine = self.controller.engine
        font = theme.title_font(self.font())
        painter.setFont(font)
        painter.setPen(QColor(theme.LIVE if active else theme.DIM))
        painter.drawText(stage.adjusted(12, 8, -12, -8), Qt.AlignLeft | Qt.AlignTop, "● Live" if active else "○ Live")
        period = engine.realtime_beat_period()
        if self.arrange:
            text = "Arrange"
        elif not engine.running:
            text = "Show stopped · over darkness"
        elif period:
            text = f"{60.0 / period:.0f} BPM"
        else:
            text = "No tempo · as if 120 BPM"
        painter.setPen(QColor(theme.WARN if self.arrange else theme.MUTED))
        painter.drawText(stage.adjusted(12, 8, -12, -8), Qt.AlignRight | Qt.AlignTop, text)
