"""The Real Time layer: the operator's hands on the lights.

Everything else in the app is a setting of a show that runs by itself. This
is the other thing - an instrument. What the operator does on the Real Time
tab lives here for as long as it lasts, and is laid over the show's colours
as the engine's very last step (VisualizationEngine._apply_realtime):

- the pointer tools, on a stage where every lamp has a place: a brush that
  leaves a fading trail (and keeps going when it's thrown), a curtain
  pulled across the stage, ripples, and a spot that keeps the show only
  where it points;
- one-shots (sweeps, bursts, a flash) and holds (flood, freeze, blackout,
  blinder...) - the pads;
- the look all of them paint with: an adjustable palette as a gradient, or
  an animation of it (plasma, flicker, sparkle, strobe), the show's own
  colour turned round the wheel, frozen, white or dark.

Nothing here changes a setting of the show, and nothing is remembered: when
the last trail has faded, `active` is False and the show goes out untouched.
Only the stage layout and the tools' settings are kept (RealtimeConfig).

Pure Python - no Qt, no lamps, no audio. Positions are 0..1 across the stage
(x from the left, y from the top); distances are in stage heights, so a
radius is the same on screen in every direction (`aspect` = width / height).
"""
from __future__ import annotations

import math
import random
import time
import zlib
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..color.models import Color, clip
from ..config.schema import REALTIME_LOOKS, REALTIME_TOOLS, RealtimeConfig

RGB = Tuple[float, float, float]
Stamp = Tuple[float, float, float]  # (how much, where on the gradient 0..1, angle)

LOOKS = REALTIME_LOOKS
TOOLS = REALTIME_TOOLS
ONE_SHOTS = ("sweep_right", "sweep_left", "burst", "implode", "close", "open", "flash")
HOLDS = ("flood", "invert", "color_strobe", "storm", "orbit", "freeze", "blinder", "blackout")

# Ready-made palettes for the palette editor.
PALETTES: Dict[str, Tuple[str, ...]] = {
    "Neon": ("#ff2d95", "#7a2bff", "#00e5ff"),
    "Sunset": ("#ff3d00", "#ff9e00", "#ff2d6f", "#6a00f4"),
    "Ocean": ("#0030ff", "#00a2ff", "#00ffc8"),
    "Fire": ("#ff1e00", "#ff7a00", "#ffd000"),
    "Ice": ("#0040ff", "#40c8ff", "#ffffff"),
    "Forest": ("#0b8f00", "#38d430", "#c8ff3a"),
    "Candy": ("#ff5fa2", "#ffd23f", "#3fd0ff", "#b56bff"),
    "Gold": ("#ff8a00", "#ffc400", "#fff1b0"),
    "RGB": ("#ff0000", "#00ff00", "#0000ff"),
    "Snow": ("#ffffff", "#bfe0ff"),
}
MAX_PALETTE_COLORS = 6

# The strobe look flashes on a subdivision of the beat, at most this often
# (a flash is an on and an off command, and the lamps take ~20 a second).
_STROBE_SUBDIVISIONS = (1, 2, 3, 4, 6, 8)
_STROBE_MAX_HZ = 10.0
_NO_TEMPO_BEAT_S = 0.5  # without a tempo: as if at 120 BPM

_TRAIL_MIN_S = 0.03
_CLEAR_FADE_S = 0.25
_MAX_WAVES = 24
_MAX_GLIDERS = 6
# A thrown brush: it must leave the hand at least this fast (stage heights a
# second) to fly, slows down like a puck on ice and stops under _GLIDE_STOP.
_THROW_MIN_SPEED = 0.7
_GLIDE_MAX_SPEED = 4.0
_GLIDE_FRICTION_S = 1.6
_GLIDE_STOP_SPEED = 0.35
_THROW_WINDOW_S = 0.12

# How fast a hold comes on and lets go, seconds: (attack, release).
_HOLD_TIMES = {"blackout": (0.03, 0.12), "blinder": (0.02, 0.15), "freeze": (0.001, 0.15)}
_HOLD_DEFAULT_TIMES = (0.04, 0.18)
# Bottom to top: what a later one shows covers the earlier ones.
_HOLD_ORDER = ("orbit", "flood", "color_strobe", "invert", "freeze", "blinder", "blackout")


def hex_to_rgb(text: str) -> RGB:
    """'#ff8800' -> (1.0, 0.53, 0.0); anything unreadable is white."""
    text = (text or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return (1.0, 1.0, 1.0)


def rgb_to_hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{round(clip(c) * 255):02x}" for c in rgb[:3])


def palette_at(palette: Sequence[RGB], p: float) -> RGB:
    """The palette as a loop: `p` 0..1 goes round it once, from the first
    colour through the others and back to the first."""
    n = len(palette)
    if n == 0:
        return (1.0, 1.0, 1.0)
    if n == 1:
        return palette[0]
    p = (p % 1.0) * n
    i = int(p) % n
    f = p - int(p)
    a, b = palette[i], palette[(i + 1) % n]
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def gradient_at(palette: Sequence[RGB], u: float) -> RGB:
    """The palette as a gradient with two ends: `u` 0 is the first colour, 1 the last."""
    n = len(palette)
    if n < 2:
        return palette_at(palette, 0.0)
    return palette_at(palette, clip(u) * (n - 1) / n)


def _smooth(t: float) -> float:
    t = clip(t)
    return t * t * (3.0 - 2.0 * t)


def _hash01(n: int) -> float:
    """A fixed pseudo-random 0..1 for an integer."""
    n = (n * 374761393 + 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def _noise(seed: int, t: float) -> float:
    """Smooth 0..1 noise over time, a different one for every seed."""
    i = math.floor(t)
    f = _smooth(t - i)
    a, b = _hash01(seed * 7919 + i), _hash01(seed * 7919 + i + 1)
    return a + (b - a) * f


def _comet(u: float) -> float:
    """How bright a wave is across its band, `u` 0 at the front .. 1 at the
    back: it snaps on as the front arrives and fades out behind it."""
    if u <= 0.0 or u >= 1.0:
        return 0.0
    if u < 0.1:
        return u / 0.1
    return (1.0 - (u - 0.1) / 0.9) ** 1.3


def _edge(q: float, softness: float) -> float:
    """The brush's weight `q` (0 centre .. 1 rim) of the way out: full in
    the middle, falling off over the outer `softness` share of the radius."""
    if q >= 1.0:
        return 0.0
    softness = clip(softness)
    if softness <= 1e-6 or q <= 1.0 - softness:
        return 1.0
    return _smooth((1.0 - q) / softness)


def _segment_point(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> Tuple[float, float]:
    """The point of the segment (x0, y0)-(x1, y1) nearest to (px, py)."""
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return x0, y0
    t = clip(((px - x0) * dx + (py - y0) * dy) / length2)
    return x0 + dx * t, y0 + dy * t


def lamp_seed(device_id: str) -> int:
    """A lamp's own fixed number: its flicker and sparkle differ from its neighbours'."""
    return zlib.crc32(device_id.encode("utf-8")) & 0xFFFF


# -- the look -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    """What a stroke, a wave or a hold paints with - the settings of the
    moment it was made (a latched stroke keeps its own)."""

    look: str = "palette"
    palette: Tuple[RGB, ...] = ((1.0, 0.18, 0.58), (0.48, 0.17, 1.0), (0.0, 0.9, 1.0))
    speed: float = 1.0
    music: float = 0.3
    brightness: float = 1.0
    hue_shift_deg: float = 180.0
    white_temp: float = 1.0

    @classmethod
    def from_config(cls, cfg: RealtimeConfig) -> "Style":
        return cls(
            look=cfg.look if cfg.look in LOOKS else "palette",
            palette=tuple(hex_to_rgb(c) for c in cfg.palette[:MAX_PALETTE_COLORS]) or ((1.0, 1.0, 1.0),),
            speed=cfg.speed,
            music=clip(cfg.music),
            brightness=clip(cfg.brightness),
            hue_shift_deg=cfg.hue_shift_deg,
            white_temp=clip(cfg.white_temp),
        )


@dataclass
class _Moment:
    """One tick's clocks, the same for every lamp."""

    t: float  # seconds
    beats: float  # the beat clock's position, or seconds at 120 BPM
    beat_s: float
    master: float  # the show's Brightness (Quick Controls)
    show_running: bool


def strobe_per_beat(speed: float, beat_s: float) -> int:
    """The strobe look's flashes per beat: 16ths at speed 1, the nearest
    musical subdivision otherwise, slower while it would be more than the
    lamps can show."""
    wanted = 4.0 * max(0.05, speed)
    per_beat = min(_STROBE_SUBDIVISIONS, key=lambda s: abs(s - wanted))
    fits = [s for s in _STROBE_SUBDIVISIONS if s <= per_beat and s / beat_s <= _STROBE_MAX_HZ]
    return fits[-1] if fits else _STROBE_SUBDIVISIONS[0]


def render(
    style: Style, under: Color, show_level: Optional[float], u: float, angle: float, x: float, y: float,
    seed: int, frozen: Optional[Color], moment: _Moment,
) -> Tuple[Color, float]:
    """What `style` shows on one lamp: (its colour, its white LEDs' level
    0..1). `under`: the lamp's colour so far (the show's, and what's been
    laid on it already) - some looks are made of it. `show_level`: how bright
    the show itself has the lamp, for Music pulse (None = no show running).
    `u`: where the lamp is on the tool's gradient (the brush: centre to rim,
    a wave: front to back). `x`, `y`: where it stands on the stage."""
    look = style.look
    if look == "blackout":
        return Color.black(), 0.0
    if look == "white":
        return under, style.brightness
    if look == "freeze":
        return (frozen if frozen is not None else under), 0.0
    if look == "hue_shift":
        hue, saturation, value = under.to_hsv()
        return Color.from_hsv(hue + style.hue_shift_deg, saturation, value), 0.0

    t = moment.t * style.speed
    palette = style.palette
    if look == "sparkle":
        slot = moment.t * 5.0 * style.speed + _hash01(seed) * 7.0
        index = math.floor(slot)
        if _hash01(seed * 31 + index) >= 0.4:
            return under, 0.0
        glow = (1.0 - (slot - index)) ** 2
        rgb = palette_at(palette, _hash01(seed * 17 + index))
        k = style.brightness * moment.master
        return under.blend(Color(rgb[0] * k, rgb[1] * k, rgb[2] * k), glow), 0.0

    level = 1.0
    if look == "plasma":
        v = (
            math.sin(x * 6.0 + t * 1.3) + math.sin(y * 7.0 - t * 1.7)
            + math.sin((x + y) * 5.0 + t * 0.9) + math.sin(math.hypot(x - 0.5, y - 0.5) * 9.0 - t * 2.1)
        ) / 8.0 + 0.5
        rgb = palette_at(palette, v + u * 0.25)
    elif look == "rainbow":
        color = Color.from_hsv(math.degrees(angle) + u * 120.0 + t * 90.0, 1.0, 1.0)
        rgb = (color.r, color.g, color.b)
    elif look == "flicker":
        rgb = gradient_at(palette, 0.75 * _noise(seed, t * 7.0) + 0.25 * u)
        level = 0.3 + 0.7 * _noise(seed + 101, t * 11.0)
    elif look == "strobe":
        phase = moment.beats * strobe_per_beat(style.speed, moment.beat_s)
        if phase % 1.0 >= 0.5:
            return Color.black(), 0.0
        rgb = palette_at(palette, math.floor(phase) * 0.381966)  # the golden ratio: never the same twice running
    else:  # "palette": the gradient over the tool, drifting round with time
        n = max(1, len(palette))
        rgb = palette_at(palette, clip(u) * (n - 1) / n + t * 0.25)

    music = style.music if show_level is not None else 0.0
    k = level * style.brightness * moment.master * ((1.0 - music) + music * clip(show_level or 0.0))
    return Color(rgb[0] * k, rgb[1] * k, rgb[2] * k), 0.0


# -- what's on the stage --------------------------------------------------------------------


@dataclass
class _Ink:
    """One lamp's paint: the stroke under way (or its fading trail) and the
    latched paint under it, each with where on the gradient it was laid."""

    live: float = 0.0
    u: float = 0.0
    angle: float = 0.0
    frozen: Optional[Color] = None
    held: float = 0.0
    held_u: float = 0.0
    held_angle: float = 0.0
    held_style: Optional[Style] = None
    held_frozen: Optional[Color] = None


@dataclass
class _Pointer:
    tool: str
    x: float
    y: float
    ax: float  # where it was pressed (the curtain's far end)
    ay: float
    erase: bool = False
    stamps: Dict[str, Stamp] = field(default_factory=dict)  # the brush, since the last tick
    track: List[Tuple[float, float, float]] = field(default_factory=list)  # (time, x, y) lately
    ripple_at: Tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class _Glider:
    """A thrown brush, sliding on by itself."""

    x: float
    y: float
    vx: float  # positions a second
    vy: float


@dataclass
class _Wave:
    kind: str  # "radial" | "implode" | "linear" | "open" | "close" | "flash" | "pop"
    t0: float
    duration: float
    style: Style
    reach: float = 1.0  # how far the front travels, stage heights
    width: float = 0.4  # the band's thickness
    ox: float = 0.0  # in stage heights (x times the aspect)
    oy: float = 0.0
    dx: float = 1.0
    dy: float = 0.0
    lamp: Optional[str] = None  # "pop": the one lamp
    u: float = 0.0

    def progress(self, now: float) -> float:
        return (now - self.t0) / self.duration if self.duration > 0.0 else 1.0

    def weight(self, lamp: str, x: float, y: float, now: float) -> Optional[Tuple[float, float]]:
        """(how much, where on the gradient) for a lamp at (`x` in stage heights, `y`)."""
        p = self.progress(now)
        if p < 0.0 or p >= 1.0:
            return None
        if self.kind == "pop":
            return ((1.0 - p) ** 2, self.u) if lamp == self.lamp else None
        if self.kind == "flash":
            return (1.0 - p) ** 1.5, self.u
        if self.kind == "radial":
            q = math.hypot(x - self.ox, y - self.oy)
        elif self.kind == "implode":
            q = self.reach - math.hypot(x - self.ox, y - self.oy)
        elif self.kind == "open":
            q = abs(x - self.ox)
        elif self.kind == "close":
            q = self.reach - abs(x - self.ox)
        else:  # "linear"
            q = (x - self.ox) * self.dx + (y - self.oy) * self.dy
        behind = (self.reach + self.width) * p - q
        if behind <= 0.0 or behind >= self.width:
            return None
        u = behind / self.width
        return _comet(u), u


@dataclass
class _Hold:
    kind: str
    on: bool = True
    level: float = 0.0
    t0: float = 0.0
    frozen: Dict[str, Color] = field(default_factory=dict)
    next_pop: float = 0.0


@dataclass
class RealtimeFrame:
    """What the layer did to one tick's colours (changed in place)."""

    # Per lamp it touched, the share of the lamp's light that is the layer's
    # now, 0..1 - the engine dims the show's white flash on it by as much.
    coverage: Dict[str, float] = field(default_factory=dict)
    # Per lamp, its white LEDs: (level 0..1, warm 0 .. cool 1).
    whites: Dict[str, Tuple[float, float]] = field(default_factory=dict)


def layout(kind: str, ids: Sequence[str], order: Optional[Dict[str, Optional[int]]] = None) -> Dict[str, List[float]]:
    """Places for `ids` on the stage - "circle", "line" or "grid" - in the
    Chase order (`order`: lamp -> its Chase order or None), the lamps without
    one last."""
    order = order or {}
    ranked = sorted(ids, key=lambda i: (order.get(i) is None, order.get(i) or 0))
    n = len(ranked)
    places: Dict[str, List[float]] = {}
    if n == 0:
        return places
    if kind == "line":
        for k, lamp in enumerate(ranked):
            places[lamp] = [0.5 if n == 1 else 0.08 + 0.84 * k / (n - 1), 0.5]
    elif kind == "grid":
        columns = max(1, math.ceil(math.sqrt(n * 1.6)))
        rows = math.ceil(n / columns)
        for k, lamp in enumerate(ranked):
            row, column = divmod(k, columns)
            places[lamp] = [(column + 0.5) / columns, (row + 0.5) / rows]
    else:  # "circle": clockwise from the top
        for k, lamp in enumerate(ranked):
            turn = -math.pi / 2.0 + 2.0 * math.pi * k / n
            places[lamp] = [0.5, 0.5] if n == 1 else [0.5 + 0.38 * math.cos(turn), 0.5 + 0.36 * math.sin(turn)]
    return places


class RealtimeLayer:
    def __init__(self, config: RealtimeConfig, rng: Optional[random.Random] = None):
        self.config = config
        self.rng = rng or random.Random()
        self.aspect = 1.6  # the stage's width / height (the tab keeps it up to date)
        self.identify: Optional[str] = None  # a lamp to light up: the one being moved on the stage
        self._ink: Dict[str, _Ink] = {}
        self._pointer: Optional[_Pointer] = None
        self._gliders: List[_Glider] = []
        self._waves: List[_Wave] = []
        self._holds: Dict[str, _Hold] = {}
        self._clearing = False
        self._beat_period: Optional[float] = None
        self._beats: Optional[float] = None
        self._beats_at = 0.0
        self._style: Optional[Style] = None
        self._style_key: Optional[tuple] = None
        self._seeds: Dict[str, int] = {}

    # -- settings ---------------------------------------------------------------------------

    @property
    def style(self) -> Style:
        """The look the tools paint with right now (the tab's settings)."""
        cfg = self.config
        key = (cfg.look, tuple(cfg.palette), cfg.speed, cfg.music, cfg.brightness, cfg.hue_shift_deg, cfg.white_temp)
        if key != self._style_key:
            self._style_key, self._style = key, Style.from_config(cfg)
        return self._style  # type: ignore[return-value]

    @property
    def active(self) -> bool:
        """Whether there's anything to lay over the show at all."""
        return bool(
            self._pointer or self._ink or self._gliders or self._waves or self._holds or self.identify
        )

    # -- the stage ----------------------------------------------------------------------------

    def position(self, lamp: str) -> Tuple[float, float]:
        place = self.config.positions.get(lamp)
        if not place or len(place) < 2:
            return 0.5, 0.5
        return float(place[0]), float(place[1])

    def ensure_positions(self, ids: Sequence[str], order: Optional[Dict[str, Optional[int]]] = None) -> bool:
        """Gives every lamp of `ids` without a place one: on a circle in the
        Chase order when none has a place yet, otherwise along the bottom
        edge, to be dragged from there. True if any was placed."""
        positions = self.config.positions
        missing = [i for i in ids if i not in positions]
        if not missing:
            return False
        if len(missing) == len(ids):
            positions.update(layout("circle", ids, order))
            return True
        for lamp in missing:
            # The spot on the bottom edge with the most room round it.
            taken = [place for place in positions.values() if len(place) >= 2]
            spots = [0.06 + 0.88 * k / 15.0 for k in range(16)]
            x = max(spots, key=lambda s: min(
                [math.hypot((s - place[0]) * self.aspect, 0.92 - place[1]) for place in taken] or [abs(s - 0.5) * -1.0]
            ))
            positions[lamp] = [x, 0.92]
        return True

    def _lamps(self) -> Iterable[Tuple[str, float, float]]:
        for lamp, place in self.config.positions.items():
            if len(place) >= 2:
                yield lamp, float(place[0]), float(place[1])

    # -- the pointer ---------------------------------------------------------------------------

    def pointer_down(self, tool: str, x: float, y: float, erase: bool = False) -> None:
        now = time.perf_counter()
        self._clearing = False
        self._pointer = _Pointer(tool, x, y, x, y, erase=erase, track=[(now, x, y)])
        if tool == "ripple":
            self.trigger("ripple", origin=(x, y))
            self._pointer.ripple_at = (now, x, y)
        elif tool == "brush":
            self._stamp_brush(x, y, x, y, self._pointer.stamps)

    def pointer_move(self, x: float, y: float) -> None:
        pointer = self._pointer
        if pointer is None:
            return
        now = time.perf_counter()
        if pointer.tool == "brush":
            # Along the whole way from the last point: a fast swipe between
            # two ticks still catches every lamp it crossed.
            self._stamp_brush(pointer.x, pointer.y, x, y, pointer.stamps)
        elif pointer.tool == "ripple":
            at, rx, ry = pointer.ripple_at
            if now - at >= 0.09 and math.hypot((x - rx) * self.aspect, y - ry) >= 0.12:
                self.trigger("ripple", origin=(x, y))
                pointer.ripple_at = (now, x, y)
        pointer.x, pointer.y = x, y
        pointer.track.append((now, x, y))
        while len(pointer.track) > 2 and now - pointer.track[0][0] > _THROW_WINDOW_S:
            pointer.track.pop(0)

    def pointer_up(self) -> None:
        pointer, self._pointer = self._pointer, None
        if pointer is None:
            return
        if self.config.latch and not pointer.erase and pointer.tool in ("curtain", "spot"):
            self._commit_stroke()  # (the brush's paint stuck as it was laid)
        if pointer.tool == "brush" and self.config.throw and not pointer.erase:
            self._throw(pointer)

    def _throw(self, pointer: _Pointer) -> None:
        """The brush let go of while moving keeps going, as fast as it left the hand."""
        now = time.perf_counter()
        recent = [s for s in pointer.track if now - s[0] <= _THROW_WINDOW_S]
        if len(recent) < 2 or recent[-1][0] - recent[0][0] < 0.015:
            return
        (t0, x0, y0), (t1, x1, y1) = recent[0], recent[-1]
        vx, vy = (x1 - x0) / (t1 - t0), (y1 - y0) / (t1 - t0)
        speed = math.hypot(vx * self.aspect, vy)
        if speed < _THROW_MIN_SPEED:
            return
        if speed > _GLIDE_MAX_SPEED:
            vx, vy = vx * _GLIDE_MAX_SPEED / speed, vy * _GLIDE_MAX_SPEED / speed
        self._gliders.append(_Glider(pointer.x, pointer.y, vx, vy))
        del self._gliders[:-_MAX_GLIDERS]

    def _stamp_brush(self, x0: float, y0: float, x1: float, y1: float, into: Dict[str, Stamp]) -> None:
        """The brush dragged from (x0, y0) to (x1, y1): each lamp it passed
        over, with how much of it the lamp got."""
        aspect = self.aspect
        radius = max(0.01, self.config.radius)
        for lamp, lx, ly in self._lamps():
            cx, cy = _segment_point(lx * aspect, ly, x0 * aspect, y0, x1 * aspect, y1)
            distance = math.hypot(lx * aspect - cx, ly - cy)
            if distance >= radius:
                continue
            amount = _edge(distance / radius, self.config.softness)
            if amount > into.get(lamp, (0.0, 0.0, 0.0))[0]:
                into[lamp] = (amount, distance / radius, math.atan2(ly - cy, lx * aspect - cx))

    def _curtain_stamps(self, pointer: _Pointer) -> Dict[str, Stamp]:
        """The curtain: a straight edge through the pointer, square to the way
        it was pulled from where it was pressed - everything behind the edge
        is covered, the gradient running from the edge back to the start."""
        aspect = self.aspect
        dx, dy = (pointer.x - pointer.ax) * aspect, pointer.y - pointer.ay
        length = math.hypot(dx, dy)
        if length < 0.02:
            return {}
        nx, ny = dx / length, dy / length
        soft = max(0.02, 0.5 * clip(self.config.softness))  # the edge's width
        stamps: Dict[str, Stamp] = {}
        for lamp, lx, ly in self._lamps():
            ahead = (lx - pointer.x) * aspect * nx + (ly - pointer.y) * ny
            amount = _smooth(0.5 - ahead / soft)
            if amount > 0.0:
                stamps[lamp] = (amount, clip(-ahead / length), math.atan2(ny, nx))
        return stamps

    def _spot_stamps(self, pointer: _Pointer) -> Dict[str, Stamp]:
        """The spot: every lamp but the ones under the pointer."""
        aspect = self.aspect
        radius = max(0.01, self.config.radius)
        stamps: Dict[str, Stamp] = {}
        for lamp, lx, ly in self._lamps():
            ddx, ddy = (lx - pointer.x) * aspect, ly - pointer.y
            distance = math.hypot(ddx, ddy)
            amount = 1.0 - _edge(distance / radius, self.config.softness)
            if amount > 0.0:
                stamps[lamp] = (amount, clip(distance / max(1.0, aspect)), math.atan2(ddy, ddx))
        return stamps

    def _commit_stroke(self) -> None:
        """Latch: the stroke stays where it is, with the look it was made with."""
        style = self.style
        for ink in self._ink.values():
            if ink.live > 0.0 and ink.live >= ink.held:
                ink.held, ink.held_u, ink.held_angle = ink.live, ink.u, ink.angle
                ink.held_style, ink.held_frozen = style, ink.frozen
            ink.live, ink.frozen = 0.0, None

    # -- one-shots and holds ---------------------------------------------------------------------

    def trigger(self, kind: str, origin: Optional[Tuple[float, float]] = None, snap: Optional[bool] = None) -> None:
        """Starts a one-shot (ONE_SHOTS, or "ripple" from `origin`) with the
        look of this moment. `snap` (default: the Snap to beat setting): it
        waits for the next half beat, unless that was only just missed."""
        now = time.perf_counter()
        cfg = self.config
        if cfg.snap_to_beat if snap is None else snap:
            now += self._snap_delay(now)
        aspect = self.aspect
        duration = max(0.08, cfg.wave_beats * self._beat_s())
        wave = _Wave("linear", now, duration, self.style)
        if kind in ("ripple", "burst"):
            x, y = origin if origin is not None and kind == "ripple" else (0.5, 0.5)
            wave.kind, wave.ox, wave.oy = "radial", x * aspect, y
            wave.reach = max(math.hypot(cx * aspect - wave.ox, cy - wave.oy) for cx in (0.0, 1.0) for cy in (0.0, 1.0))
        elif kind == "implode":
            wave.kind, wave.ox, wave.oy = "implode", 0.5 * aspect, 0.5
            wave.reach = math.hypot(0.5 * aspect, 0.5)
        elif kind in ("open", "close"):
            wave.kind, wave.ox, wave.reach = kind, 0.5 * aspect, 0.5 * aspect
        elif kind == "flash":
            wave.kind = "flash"
        elif kind == "sweep_left":
            wave.ox, wave.dx, wave.reach = aspect, -1.0, aspect
        elif kind == "sweep_right":
            wave.reach = aspect
        else:
            return
        wave.width = 0.45 * wave.reach
        self._waves.append(wave)
        del self._waves[:-_MAX_WAVES]

    def hold(self, kind: str, on: bool) -> None:
        """A hold pad pressed (`on`) or let go: it eases in and out."""
        if kind not in HOLDS:
            return
        existing = self._holds.get(kind)
        if on:
            if existing is None or existing.level <= 0.0:
                self._holds[kind] = _Hold(kind, t0=time.perf_counter())
            else:
                existing.on = True
        elif existing is not None:
            existing.on = False

    def held(self) -> List[str]:
        return [kind for kind, hold in self._holds.items() if hold.on]

    def clear(self) -> None:
        """Wipes the stage: the latched paint fades out, everything in flight is dropped."""
        self._clearing = True
        self._waves.clear()
        self._gliders.clear()
        for ink in self._ink.values():
            ink.live, ink.frozen = 0.0, None

    def sync_clock(self, now: float, beat_period: Optional[float], beats: Optional[float]) -> None:
        """The beat clock's tempo and position at `now` (None while it isn't
        locked) - kept up to date by the engine while nothing is played too,
        so the first pad pressed already knows where the beat is."""
        self._beat_period, self._beats, self._beats_at = beat_period, beats, now

    def _beat_s(self) -> float:
        return self._beat_period or _NO_TEMPO_BEAT_S

    def _snap_delay(self, now: float) -> float:
        if self._beats is None or not self._beat_period:
            return 0.0
        half_beats = (self._beats + (now - self._beats_at) / self._beat_period) * 2.0
        into = half_beats % 1.0
        if into < 0.3:  # pressed a little late: it's this half beat's
            return 0.0
        return (1.0 - into) * 0.5 * self._beat_period

    # -- for the stage's own picture ---------------------------------------------------------------

    def ink_levels(self) -> Dict[str, Tuple[float, float]]:
        """Per painted lamp: (the stroke or its trail, the latched paint), 0..1."""
        return {lamp: (ink.live, ink.held) for lamp, ink in self._ink.items()}

    def pointer_state(self) -> Optional[Tuple[str, float, float, float, float, bool]]:
        p = self._pointer
        return None if p is None else (p.tool, p.x, p.y, p.ax, p.ay, p.erase)

    def glider_points(self) -> List[Tuple[float, float]]:
        return [(g.x, g.y) for g in self._gliders]

    def wave_shapes(self, now: Optional[float] = None) -> List[dict]:
        """The waves in flight: kind, origin (positions), how far the front is
        (stage heights), direction and the first palette colour."""
        now = time.perf_counter() if now is None else now
        shapes = []
        for wave in self._waves:
            p = wave.progress(now)
            if not 0.0 <= p < 1.0 or wave.kind in ("flash", "pop"):
                continue
            shapes.append({
                "kind": wave.kind, "x": wave.ox / self.aspect, "y": wave.oy, "dx": wave.dx, "dy": wave.dy,
                "front": (wave.reach + wave.width) * p, "reach": wave.reach, "fade": 1.0 - p,
                "rgb": wave.style.palette[0],
            })
        return shapes

    # -- the tick -------------------------------------------------------------------------------

    def apply(
        self, colors: Dict[str, Color], now: float, dt: float, beat_period: Optional[float] = None,
        beats: Optional[float] = None, master: float = 1.0, show_running: bool = True,
    ) -> RealtimeFrame:
        """Lays everything on the stage over `colors` (the lamps' colours of
        this tick), in place. `beat_period`, `beats`: the beat clock's tempo
        and position while it's locked - the one-shots' length and the strobe
        look go by it, by 120 BPM without. `master`: the show's Brightness."""
        self.sync_clock(now, beat_period, beats)
        beat_s = self._beat_s()
        moment = _Moment(now, beats if beats is not None else now / beat_s, beat_s, master, show_running)
        self._advance_ink(dt)
        self._advance_holds(now, dt, colors)
        self._waves = [w for w in self._waves if w.progress(now) < 1.0]

        frame = RealtimeFrame()
        if not self.active:
            return frame
        style = self.style
        aspect = self.aspect
        orbit = self._holds.get("orbit")
        centre = self._centre(colors) if orbit is not None else (0.5, 0.5)

        for lamp, base in list(colors.items()):
            x, y = self.position(lamp)
            seed = self._seeds.get(lamp)
            if seed is None:
                seed = self._seeds[lamp] = lamp_seed(lamp)
            show_level = max(base.r, base.g, base.b) if show_running else None
            color, keep, white, temp = base, 1.0, 0.0, 1.0

            def lay(with_style: Style, amount: float, u: float, angle: float, frozen: Optional[Color] = None):
                nonlocal color, keep, white, temp
                amount = clip(amount)
                if amount <= 0.002:
                    return
                if with_style.look == "hue_shift":
                    # Part way in (a soft edge, a fading trail) the hue is turned part of the
                    # way - a blend of a colour and its complement would pass through grey.
                    hue, saturation, value = color.to_hsv()
                    color = Color.from_hsv(hue + with_style.hue_shift_deg * amount, saturation, value)
                    keep *= 1.0 - amount
                    return
                shown, white_level = render(with_style, color, show_level, u, angle, x, y, seed, frozen, moment)
                if with_style.look == "white":
                    if amount * white_level > white:
                        white, temp = amount * white_level, with_style.white_temp
                    return
                if with_style.look == "blackout":
                    white *= 1.0 - amount
                color = color.blend(shown, amount)
                keep *= 1.0 - amount

            ink = self._ink.get(lamp)
            if ink is not None:
                if ink.held > 0.0 and ink.held_style is not None:
                    if ink.held_frozen is None:
                        ink.held_frozen = color
                    lay(ink.held_style, ink.held, ink.held_u, ink.held_angle, ink.held_frozen)
                if ink.live > 0.0:
                    if ink.frozen is None:
                        ink.frozen = color
                    lay(style, ink.live, ink.u, ink.angle, ink.frozen)
            for wave in self._waves:
                hit = wave.weight(lamp, x * aspect, y, now)
                if hit is not None:
                    lay(wave.style, hit[0], hit[1], math.atan2(y - 0.5, (x - 0.5) * aspect))
            for kind in _HOLD_ORDER:
                hold = self._holds.get(kind)
                if hold is None or hold.level <= 0.0:
                    continue
                angle = math.atan2(y - centre[1], (x - centre[0]) * aspect)
                if kind == "orbit":
                    # A comet going round the lamps' own middle, once a bar at Wave length 1.
                    round_s = max(0.3, 4.0 * beat_s * self.config.wave_beats)
                    behind = (2.0 * math.pi * (now - hold.t0) / round_s - angle) % (2.0 * math.pi)
                    arc = 0.4 * 2.0 * math.pi
                    if behind < arc:
                        lay(style, hold.level * _comet(behind / arc), behind / arc, angle)
                    continue
                if kind == "freeze" and lamp not in hold.frozen:
                    hold.frozen[lamp] = color
                lay(self._hold_style(kind, style), hold.level, x, angle, hold.frozen.get(lamp))
            if lamp == self.identify:
                glow = 0.75 + 0.25 * math.sin(now * 9.0)
                color, keep = Color(glow, glow, glow), 0.0

            if keep < 0.998:
                colors[lamp] = color
                frame.coverage[lamp] = 1.0 - keep
            if white > 0.01:
                frame.whites[lamp] = (white, temp)
        return frame

    @staticmethod
    def _hold_style(kind: str, style: Style) -> Style:
        if kind == "invert":
            return replace(style, look="hue_shift", hue_shift_deg=180.0)
        if kind == "color_strobe":
            return replace(style, look="strobe")
        if kind == "freeze":
            return replace(style, look="freeze")
        if kind == "blinder":
            return replace(style, look="white", brightness=1.0)
        if kind == "blackout":
            return replace(style, look="blackout")
        return style  # "flood": the look as it is

    def _centre(self, colors: Dict[str, Color]) -> Tuple[float, float]:
        places = [self.position(lamp) for lamp in colors]
        if not places:
            return 0.5, 0.5
        return sum(p[0] for p in places) / len(places), sum(p[1] for p in places) / len(places)

    def _advance_ink(self, dt: float) -> None:
        """The tools' work since the last tick, into the lamps' paint; what
        the pointer has left fades over the Trail time."""
        cfg = self.config
        pointer = self._pointer
        stamps: Dict[str, Stamp] = {}
        shape = False  # the curtain and the spot: the paint is exactly their shape while latching
        if pointer is not None:
            if pointer.tool == "brush":
                self._stamp_brush(pointer.x, pointer.y, pointer.x, pointer.y, pointer.stamps)
                stamps, pointer.stamps = pointer.stamps, {}
            elif pointer.tool == "curtain":
                stamps, shape = self._curtain_stamps(pointer), True
            elif pointer.tool == "spot":
                stamps, shape = self._spot_stamps(pointer), True
        self._advance_gliders(dt, stamps)

        erase = pointer is not None and pointer.erase
        latch = cfg.latch and not erase
        fade = dt / max(_TRAIL_MIN_S, cfg.trail_s)
        style = self.style
        for lamp in set(self._ink) | set(stamps):
            ink = self._ink.get(lamp)
            if ink is None:
                ink = self._ink[lamp] = _Ink()
            stamp = stamps.get(lamp)
            if erase:
                if stamp is not None:
                    ink.held = min(ink.held, 1.0 - stamp[0])
                    ink.live = min(ink.live, 1.0 - stamp[0])
                stamp = None
            if latch and shape:
                ink.live = 0.0
                if stamp is not None:
                    ink.live, ink.u, ink.angle = stamp
            elif latch and stamp is not None and not shape:
                # The brush with Hold paint on: it sticks as it's laid.
                if stamp[0] >= ink.held:
                    if ink.held <= 0.0 or ink.held_style != style:
                        ink.held_frozen = None  # a new coat: Freeze holds what's there now
                    ink.held, ink.held_u, ink.held_angle = stamp
                    ink.held_style = style
            else:
                level = max(0.0, ink.live - fade)
                if stamp is not None and stamp[0] >= level:
                    ink.live, ink.u, ink.angle = stamp
                else:
                    ink.live = level
            if self._clearing:
                ink.held = max(0.0, ink.held - dt / _CLEAR_FADE_S)
            if ink.live <= 0.0:
                ink.frozen = None
            if ink.held <= 0.0:
                ink.held_style = ink.held_frozen = None
            if ink.live <= 0.0 and ink.held <= 0.0:
                del self._ink[lamp]
        if self._clearing and not any(ink.held > 0.0 for ink in self._ink.values()):
            self._clearing = False

    def _advance_gliders(self, dt: float, stamps: Dict[str, Stamp]) -> None:
        slow = math.exp(-dt / _GLIDE_FRICTION_S)
        for glider in list(self._gliders):
            x, y = glider.x + glider.vx * dt, glider.y + glider.vy * dt
            if x < 0.0 or x > 1.0:  # off the wall and back
                x, glider.vx = (-x if x < 0.0 else 2.0 - x), -glider.vx
            if y < 0.0 or y > 1.0:
                y, glider.vy = (-y if y < 0.0 else 2.0 - y), -glider.vy
            x, y = clip(x), clip(y)
            self._stamp_brush(glider.x, glider.y, x, y, stamps)
            glider.x, glider.y = x, y
            glider.vx *= slow
            glider.vy *= slow
            if math.hypot(glider.vx * self.aspect, glider.vy) < _GLIDE_STOP_SPEED:
                self._gliders.remove(glider)

    def _advance_holds(self, now: float, dt: float, colors: Dict[str, Color]) -> None:
        for kind, hold in list(self._holds.items()):
            attack, release = _HOLD_TIMES.get(kind, _HOLD_DEFAULT_TIMES)
            if hold.on:
                hold.level = min(1.0, hold.level + dt / attack)
            else:
                hold.level = max(0.0, hold.level - dt / release)
                if hold.level <= 0.0:
                    del self._holds[kind]
                    continue
            if kind == "storm" and hold.on and now >= hold.next_pop and colors:
                # A lamp pops in a colour of the palette, on the 16ths at speed 1.
                beat_s = self._beat_s()
                hold.next_pop = now + beat_s / (4.0 * max(0.25, self.config.speed))
                lamps = list(colors)
                for lamp in self.rng.sample(lamps, max(1, len(lamps) // 4)):
                    self._waves.append(
                        _Wave("pop", now, max(0.12, 0.7 * beat_s), self.style, lamp=lamp, u=self.rng.random())
                    )
                del self._waves[:-_MAX_WAVES]
