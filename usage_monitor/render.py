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
ERROR = "#f09595"
COLORS = {"claude": "#F0997B", "codex": "#5DCAA5"}
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


def _canvas(size):
    image = Image.new("RGB", (size[0] * SS, size[1] * SS), KEY)
    draw = ImageDraw.Draw(image)
    radius = min(size) // 2 if size == (SINGLE_COMPACT, SINGLE_COMPACT) else min(RADIUS, min(size) // 2)
    draw.rounded_rectangle((0, 0, size[0] * SS - 1, size[1] * SS - 1), radius * SS, fill=BG)
    return image, draw


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


def background(size):
    image, _ = _canvas(size)
    return _finish(image, size)


def _ring(draw, cx, cy, percent, color, text_color):
    s = SS
    r, width = 17 * s, 4 * s
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.ellipse(box, outline=TRACK, width=width)
    if percent:
        end = -90 + 360 * min(percent, 100) / 100
        draw.arc(box, -90, end, fill=color, width=width)
        # Round caps: Pillow arcs end square.
        for angle in (-90, end):
            a = math.radians(angle)
            px, py = cx + (r - width / 2) * math.cos(a), cy + (r - width / 2) * math.sin(a)
            draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=color)
    label = "–" if percent is None else f"{percent:.0f}"
    draw.text((cx, cy), label, font=font(12 * s, "medium"), fill=text_color, anchor="mm")


def compact(providers, vertical):
    size = compact_size(len(providers), vertical)
    image, draw = _canvas(size)
    step = 48 * SS
    for index, provider in enumerate(providers):
        offset = (index - (len(providers) - 1) / 2) * step
        cx = size[0] * SS / 2 + (0 if vertical else offset)
        cy = size[1] * SS / 2 + (offset if vertical else 0)
        _ring(draw, cx, cy, provider.percent, COLORS[provider.key], MUTED if provider.loading else TEXT)
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


def _layout(providers, draw=None):
    """Lay out the expanded card; with draw=None only measures. Units are 1x pixels."""
    s, y = SS, PAD
    inner = EXPANDED_WIDTH - PAD * 2

    def text(x, top, value, size, color, weight="regular", anchor="la"):
        if draw:
            draw.text((x * s, top * s), value, font=font(size * s, weight), fill=color, anchor=anchor)

    for index, provider in enumerate(providers):
        if index:
            if draw:
                draw.rectangle((PAD * s, (y + 2) * s, (EXPANDED_WIDTH - PAD) * s, (y + 2) * s + 1), fill=TRACK)
            y += 12
        text(PAD, y, NAMES[provider.key], 13, TEXT, "medium")
        text(EXPANDED_WIDTH - PAD, y + 2, "Actualizando…" if provider.loading else provider.age, 11, MUTED, anchor="ra")
        y += 20
        if provider.message:
            for line in _wrap(provider.message, font(11), inner):
                text(PAD, y, line, 11, ERROR if not provider.rows else MUTED)
                y += 15
            y += 4
        for row_index, row in enumerate(provider.rows):
            if row_index:
                y += 6
            text(PAD, y, row.label, 12, TEXT)
            text(EXPANDED_WIDTH - PAD, y, f"{row.percent:.0f} %", 12, TEXT, anchor="ra")
            y += 19
            if draw:
                left, right = PAD * s, (EXPANDED_WIDTH - PAD) * s
                draw.rounded_rectangle((left, y * s, right, (y + 4) * s), 2 * s, fill=TRACK)
                filled = left + (right - left) * min(row.percent, 100) / 100
                if filled - left >= 4 * s:
                    draw.rounded_rectangle((left, y * s, filled, (y + 4) * s), 2 * s, fill=COLORS[provider.key])
            y += 8
            text(PAD, y, row.reset, 11, MUTED)
            text(EXPANDED_WIDTH - PAD, y, row.remaining, 11, MUTED, anchor="ra")
            y += 15
        y += 6
    return y + PAD - 6


def expanded_size(providers):
    return EXPANDED_WIDTH, _layout(providers)


def expanded(providers):
    size = expanded_size(providers)
    image, draw = _canvas(size)
    _layout(providers, draw)
    return _finish(image, size)


def on_stage(image, stage_size, box):
    """Paste a deformed copy of the widget on a transparent stage of fixed size."""
    stage = Image.new("RGB", stage_size, KEY)
    x, y, w, h = (round(v) for v in box)
    stage.paste(image.resize((max(1, w), max(1, h)), Image.BILINEAR), (x, y))
    return stage
