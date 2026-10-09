"""Pure image operations for composing the widget's liquid-glass surface."""

from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


SUPERSAMPLE = 3
REFRACTION_WIDTH = 14
REFRACTION_SHIFT = 13
BRIGHTNESS_THRESHOLD = 0.6
LIGHT_SURFACE = 0.5


@dataclass(frozen=True)
class Tint:
    color: tuple = (18, 18, 22)
    opacity: float = 0.08
    bright_minimum: float = 0.08


def effective_opacity(background, tint):
    """Raise the tint floor on bright backgrounds to keep content readable."""
    if not isinstance(tint, Tint):
        raise TypeError("El tinte no es válido.")
    if not 0 <= tint.opacity <= 1 or not 0 <= tint.bright_minimum <= 1:
        raise ValueError("La opacidad del cristal no es válida.")
    if _luminance(background) > BRIGHTNESS_THRESHOLD:
        return max(tint.opacity, tint.bright_minimum)
    return tint.opacity


def _luminance(image):
    sample = image.convert("RGB").resize((16, 16), Image.Resampling.BOX)
    pixels = np.asarray(sample, dtype=np.float32) / 255
    return float(np.mean(pixels[..., 0] * 0.2126 + pixels[..., 1] * 0.7152 + pixels[..., 2] * 0.0722))


def surface_is_light(background, tint):
    """True when the composed glass will look light, so content needs deep colours."""
    opacity = effective_opacity(background, tint)
    tint_luminance = (0.2126 * tint.color[0] + 0.7152 * tint.color[1] + 0.0722 * tint.color[2]) / 255
    return (1 - opacity) * _luminance(background) + opacity * tint_luminance > LIGHT_SURFACE


@lru_cache(maxsize=32)
def _rounded_mask(size, radius):
    width, height = size
    scale = SUPERSAMPLE
    mask = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=max(0, round(radius * scale)), fill=255,
    )
    return mask.resize(size, Image.Resampling.BOX)


@lru_cache(maxsize=32)
def _refraction_indices(width, height, radius, scale):
    """Source row and column for each pixel; depends only on the shape, so it is cached."""
    x, y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    r = min(max(0, radius), width / 2, height / 2)
    left, right, top, bottom = 0.0, width - 1.0, 0.0, height - 1.0
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    qx = np.abs(x - center_x) - (width / 2 - r)
    qy = np.abs(y - center_y) - (height / 2 - r)
    outside = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    inside = np.minimum(np.maximum(qx, qy), 0)
    signed_distance = outside + inside - r
    edge_distance = -signed_distance

    core_x = np.clip(x, left + r, right - r)
    core_y = np.clip(y, top + r, bottom - r)
    normal_x, normal_y = x - core_x, y - core_y
    normal_length = np.hypot(normal_x, normal_y)
    np.divide(normal_x, normal_length, out=normal_x, where=normal_length > 0)
    np.divide(normal_y, normal_length, out=normal_y, where=normal_length > 0)

    band = REFRACTION_WIDTH * scale
    amount = REFRACTION_SHIFT * scale * np.square(np.maximum(0, 1 - edge_distance / max(1, band)))
    amount[(edge_distance < 0) | (edge_distance >= band)] = 0
    source_x = np.clip(np.rint(x - normal_x * amount), 0, width - 1).astype(np.intp)
    source_y = np.clip(np.rint(y - normal_y * amount), 0, height - 1).astype(np.intp)
    return source_y, source_x


def _refraction(image, radius, scale=1.0):
    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
    height, width = pixels.shape[:2]
    source_y, source_x = _refraction_indices(width, height, round(float(radius), 2), round(float(scale), 3))
    return Image.fromarray(pixels[source_y, source_x], "RGB")


def _blur_and_saturate(background, scale=1.0):
    width, height = background.size
    half = (max(1, width // 2), max(1, height // 2))
    reduced = background.resize(half, Image.Resampling.BOX)
    blurred = reduced.filter(ImageFilter.GaussianBlur(3.5 * scale))
    blurred = blurred.resize((width, height), Image.Resampling.BILINEAR)
    return ImageEnhance.Color(blurred).enhance(1.7)


@lru_cache(maxsize=32)
def _highlights(size, radius):
    width, height = size
    y = np.arange(height, dtype=np.float32)
    alpha = np.clip(1 - y / max(1, height * 0.38), 0, 1) * 0.28
    gradient = np.zeros((height, width, 4), dtype=np.uint8)
    gradient[..., :3] = 255
    gradient[..., 3] = np.rint(alpha[:, None] * 255).astype(np.uint8)
    image = Image.fromarray(gradient, "RGBA")

    scale = SUPERSAMPLE
    detail = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(detail)
    inset = max(1, scale)
    draw.rounded_rectangle(
        (inset, inset, width * scale - inset - 1, height * scale - inset - 1),
        radius=max(0, round(radius * scale)), outline=(255, 255, 255, 56), width=scale,
    )
    draw.line((radius * scale, inset, (width - radius) * scale, inset),
              fill=(255, 255, 255, 140), width=scale)
    draw.line((radius * scale, height * scale - inset, (width - radius) * scale,
               height * scale - inset), fill=(255, 255, 255, 31), width=scale)
    return Image.alpha_composite(image, detail.resize(size, Image.Resampling.LANCZOS))


def compose(background, size, tint, radius, scale=1.0):
    """Return an RGBA liquid-glass image composed from an RGB screen capture."""
    width, height = (int(value) for value in size)
    if width <= 0 or height <= 0:
        raise ValueError("El tamaño del cristal no es válido.")
    if not isinstance(tint, Tint) or len(tint.color) != 3 or any(
            not isinstance(value, int) or not 0 <= value <= 255 for value in tint.color):
        raise ValueError("El tinte del cristal no es válido.")
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("El radio del cristal no es válido.")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("La escala del cristal no es válida.")

    source = background.convert("RGB")
    if source.size != (width, height):
        source = source.resize((width, height), Image.Resampling.BILINEAR)
    radius = round(min(float(radius), width / 2, height / 2), 2)
    blurred = _blur_and_saturate(source, scale)
    refracted = _refraction(blurred, radius, scale)
    opacity = effective_opacity(blurred, tint)
    overlay = Image.new("RGBA", (width, height), (*tint.color, round(opacity * 255)))
    result = Image.alpha_composite(refracted.convert("RGBA"), overlay)
    result = Image.alpha_composite(result, _highlights((width, height), radius))
    result.putalpha(_rounded_mask((width, height), radius))
    return result
