"""Pillow drawing for the floating widget. Supersampled for smooth rings and edges."""

from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SS = 3
KEY = "#0b0b0c"  # Window transparency key; never used inside the widget.
BG = "#1c1c1e"
TRACK = "#3a3a3c"
TEXT = "#f2f2f7"
MUTED = "#a1a1a6"
# On glass the background can be light, so secondary text and tracks are translucent white.
GLASS_MUTED = (255, 255, 255, 184)
GLASS_TRACK = (255, 255, 255, 64)
# Over light glass the pastel colours lose contrast: deeper tones, dark tracks, no shadow.
GLASS_ACCENT_ON_LIGHT = {"claude": "#C2410C", "codex": "#0B7A5C"}
GLASS_TRACK_ON_LIGHT = (0, 0, 0, 46)
ERROR = "#f09595"
COLORS = {"claude": "#F0997B", "codex": "#5DCAA5"}
WARNING = "#EF9F27"
CRITICAL = "#E24B4A"
NAMES = {"claude": "Claude", "codex": "Codex"}
RADIUS = 22
COMPACT = (124, 56)
SINGLE_COMPACT = 56
EXPANDED_WIDTH = 236
PAD = 14
DAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
_fonts = {}


def font(size, weight="regular"):
    key = (size, weight)
    if key not in _fonts:
        name = {"regular": "segoeui.ttf", "medium": "seguisb.ttf"}[weight]
        try:
            _fonts[key] = ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


@dataclass(frozen=True)
class Row:
    label: str
    percent: float
    reset: str
    remaining: str


@dataclass(frozen=True)
class Provider:
    key: str
    percent: float | None  # Shown in the compact ring.
    rows: tuple
    age: str
    message: str = ""
    loading: bool = False


def countdown(resets_at, now):
    remaining = resets_at - now
    if remaining <= 0:
        return "pendiente"
    minutes = max(1, math.ceil(remaining / 60))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    return f"en {days} d {hours} h" if days else f"en {hours} h {minutes} min" if hours else f"en {minutes} min"


def reset_label(resets_at, now):
    if resets_at is None:
        return "Reinicio no informado"
    try:
        when, today = datetime.fromtimestamp(resets_at), datetime.fromtimestamp(now)
    except (ValueError, OSError, OverflowError):
        return "Reinicio no informado"
    days = (when.date() - today.date()).days
    if days == 0:
        return f"Reinicia {when:%H:%M}"
    if 0 < days < 7:
        return f"Reinicia {DAYS[when.weekday()]} {when:%H:%M}"
    return f"Reinicia {when:%d/%m %H:%M}"


def age_label(observed_at, now):
    seconds = max(0, int(now - observed_at))
    if seconds < 60:
        return f"hace {seconds} s"
    if seconds < 3600:
        return f"hace {seconds // 60} min"
    return f"hace {seconds // 3600} h"


def level_color(provider_key, percent):
    if percent is not None and percent >= 95:
        return CRITICAL
    if percent is not None and percent >= 80:
        return WARNING
    return COLORS[provider_key]


def _canvas(size, mode="colorkey"):
    if mode == "layered":
        image = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    elif mode == "colorkey":
        image = Image.new("RGB", (size[0] * SS, size[1] * SS), KEY)
    else:
        raise ValueError("Modo de renderizado no válido.")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size[0] * SS - 1, size[1] * SS - 1), shape_radius(size) * SS, fill=BG)
    return image, draw


def shape_radius(size):
    """Corner radius in logical pixels: a circle for one service, rounded corners otherwise."""
    if tuple(size) == (SINGLE_COMPACT, SINGLE_COMPACT):
        return min(size) // 2
    return min(RADIUS, min(size) // 2)


def compact_size(count, vertical):
    if count == 1:
        return SINGLE_COMPACT, SINGLE_COMPACT
    if count == 2:
        return COMPACT[::-1] if vertical else COMPACT
    raise ValueError("La vista compacta admite uno o dos servicios.")


SCALE = 1.0  # Display scaling (DPI / 96), set by the UI.


def scaled(size):
    return tuple(max(1, round(v * SCALE)) for v in size)


def _finish(image, size):
    # BOX keeps pixels outside the shape exactly KEY, so they stay transparent.
    return image.resize(scaled(size), Image.BOX)


def background(size, mode="colorkey"):
    image, _ = _canvas(size, mode)
    return _finish(image, size)


def _draw_text(draw, point, value, typeface, color, anchor="la", shadow=False):
    if shadow:
        # A thin 1 px drop shadow: a blurred stroke made the text look out of focus.
        x, y = point
        draw.text((x, y + SS), value, font=typeface, fill=(0, 0, 0, 110), anchor=anchor)
    draw.text(point, value, font=typeface, fill=color, anchor=anchor)


def _ring(draw, cx, cy, percent, color, text_color, shadow=False, track=TRACK):
    s = SS
    r, width = 17 * s, 4 * s
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.ellipse(box, outline=track, width=width)
    if percent:
        end = -90 + 360 * min(percent, 100) / 100
        draw.arc(box, -90, end, fill=color, width=width)
        # Round caps: Pillow arcs end square.
        for angle in (-90, end):
            a = math.radians(angle)
            px, py = cx + (r - width / 2) * math.cos(a), cy + (r - width / 2) * math.sin(a)
            draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)
    label = "–" if percent is None else f"{percent:.0f}"
    _draw_text(draw, (cx, cy), label, font(12 * s, "medium"), text_color, "mm", shadow)


def compact(providers, vertical, mode="colorkey"):
    size = compact_size(len(providers), vertical)
    image, draw = _canvas(size, mode)
    step = 48 * SS
    for index, provider in enumerate(providers):
        offset = (index - (len(providers) - 1) / 2) * step
        cx = size[0] * SS / 2 + (0 if vertical else offset)
        cy = size[1] * SS / 2 + (offset if vertical else 0)
        _ring(draw, cx, cy, provider.percent, level_color(provider.key, provider.percent),
              MUTED if provider.loading else TEXT)
    return _finish(image, size)


def _wrap(text, typeface, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and typeface.getlength(candidate) > width:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + ([line] if line else [])


def _layout(providers, draw=None, shadow=False, muted=MUTED, track=TRACK, accent=None):
    """Lay out the expanded card; with draw=None only measures. Units are 1x pixels."""
    s, y = SS, PAD
    inner = EXPANDED_WIDTH - PAD * 2

    def text(x, top, value, size, color, weight="regular", anchor="la"):
        if draw:
            _draw_text(draw, (x * s, top * s), value, font(size * s, weight), color, anchor, shadow)

    for index, provider in enumerate(providers):
        if index:
            if draw:
                draw.rectangle((PAD * s, (y + 2) * s, (EXPANDED_WIDTH - PAD) * s, (y + 2) * s + 1), fill=track)
            y += 12
        # On glass, white text vanishes over light backgrounds: use each service's colour.
        highlight = accent[provider.key] if accent else TEXT
        text(PAD, y, NAMES[provider.key], 13, highlight, "medium")
        text(EXPANDED_WIDTH - PAD, y + 2, "Actualizando…" if provider.loading else provider.age, 11, muted, anchor="ra")
        y += 20
        if provider.message:
            for line in _wrap(provider.message, font(11), inner):
                text(PAD, y, line, 11, ERROR if not provider.rows else muted)
                y += 15
            y += 4
        for row_index, row in enumerate(provider.rows):
            if row_index:
                y += 6
            text(PAD, y, row.label, 12, TEXT)
            text(EXPANDED_WIDTH - PAD, y, f"{row.percent:.0f} %", 12, highlight, anchor="ra")
            y += 19
            if draw:
                left, right = PAD * s, (EXPANDED_WIDTH - PAD) * s
                draw.rounded_rectangle((left, y * s, right, (y + 4) * s), 2 * s, fill=track)
                filled = left + (right - left) * min(row.percent, 100) / 100
                if filled - left >= 4 * s:
                    draw.rounded_rectangle((left, y * s, filled, (y + 4) * s), 2 * s,
                                           fill=level_color(provider.key, row.percent))
            y += 8
            text(PAD, y, row.reset, 11, muted)
            text(EXPANDED_WIDTH - PAD, y, row.remaining, 11, muted, anchor="ra")
            y += 15
        y += 6
    return y + PAD - 6


def expanded_size(providers):
    return EXPANDED_WIDTH, _layout(providers)


def expanded(providers, mode="colorkey"):
    size = expanded_size(providers)
    image, draw = _canvas(size, mode)
    _layout(providers, draw)
    return _finish(image, size)


def content(providers, vertical=False, expanded_view=False, light=False):
    """Draw only the widget content on transparency for composition over a glass surface."""
    size = expanded_size(providers) if expanded_view else compact_size(len(providers), vertical)
    image = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = GLASS_ACCENT_ON_LIGHT if light else COLORS
    if expanded_view:
        _layout(providers, draw, shadow=not light, muted=GLASS_MUTED,
                track=GLASS_TRACK_ON_LIGHT if light else GLASS_TRACK,
                accent=GLASS_ACCENT_ON_LIGHT if light else COLORS)
    else:
        step = 48 * SS
        for index, provider in enumerate(providers):
            offset = (index - (len(providers) - 1) / 2) * step
            cx = size[0] * SS / 2 + (0 if vertical else offset)
            cy = size[1] * SS / 2 + (offset if vertical else 0)
            _ring(draw, cx, cy, provider.percent, level_color(provider.key, provider.percent),
                  GLASS_MUTED if provider.loading else accent[provider.key], shadow=not light,
                  track=GLASS_TRACK_ON_LIGHT if light else GLASS_TRACK)
    return _finish(image, size)


def on_stage(image, stage_size, box, mode="colorkey"):
    """Paste a deformed copy of the widget on a transparent stage of fixed size."""
    if mode == "layered":
        stage = Image.new("RGBA", stage_size, (0, 0, 0, 0))
    elif mode == "colorkey":
        stage = Image.new("RGB", stage_size, KEY)
    else:
        raise ValueError("Modo de renderizado no válido.")
    x, y, w, h = (round(v) for v in box)
    resized = image.resize((max(1, w), max(1, h)), Image.BILINEAR)
    if mode == "layered":
        stage.alpha_composite(resized.convert("RGBA"), (x, y))
    else:
        stage.paste(resized, (x, y))
    return stage
