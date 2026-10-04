"""Draws a member's name into the Apple Wallet strip image.

Wallet forces every field it draws over a strip image to white system-font text,
which is unreadable on the light card themes. The only way to get the card's own
typeface and colour on the pass face is to bake the text into the strip itself,
so each pass ships a strip rendered for that member.
"""

import io
import logging
import os
from functools import lru_cache
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
PASS_DIR = os.path.join(ASSETS_DIR, "gdg.pass")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")

# Strip geometry in points; Wallet draws a storeCard strip at 375 x 144.
STRIP_WIDTH = 375
STRIP_HEIGHT = 144
SCALES: Dict[str, int] = {"": 1, "@2x": 2, "@3x": 3}
RENDER_SCALE = 3

ROLE_SIZE = 13.5
ROLE_BASELINE = 62
ROLE_OPACITY = 0.8
NAME_SIZE = 23
NAME_MIN_SIZE = 14
NAME_BASELINE = 96
TEXT_MAX_WIDTH = 343


@lru_cache(maxsize=None)
def _band_art(theme_id: str) -> Image.Image:
    return Image.open(os.path.join(PASS_DIR, f"band-{theme_id}@3x.png")).convert("RGBA")


@lru_cache(maxsize=None)
def _font(weight: str, size_px: int) -> ImageFont.FreeTypeFont:
    # RAQM is asked for explicitly: the basic layout engine would draw Arabic as
    # disconnected, left-to-right letters instead of failing.
    return ImageFont.truetype(
        os.path.join(FONTS_DIR, f"Tajawal-{weight}.ttf"), size_px, layout_engine=ImageFont.Layout.RAQM
    )


def _fit_name(draw: ImageDraw.ImageDraw, name: str) -> Tuple[str, ImageFont.FreeTypeFont]:
    """Shrinks the name until it fits the strip, then trims it as a last resort."""
    max_width = TEXT_MAX_WIDTH * RENDER_SCALE
    size = NAME_SIZE * RENDER_SCALE
    font = _font("Bold", size)
    while draw.textlength(name, font=font) > max_width and size > NAME_MIN_SIZE * RENDER_SCALE:
        size -= RENDER_SCALE
        font = _font("Bold", size)
    while len(name) > 1 and draw.textlength(name, font=font) > max_width:
        name = name[:-2].rstrip() + "…"
    return name, font


def render_name_band(
    theme_id: str, role_title: str, full_name: str, text_rgb: Tuple[int, int, int]
) -> Optional[Dict[str, bytes]]:
    """
    Returns the strip PNG for each scale suffix ("", "@2x", "@3x") with the role
    and name drawn over the theme's band art, or None when the text can't be drawn
    (Pillow's RAQM layout needs the system libfribidi) so the caller can fall back
    to fields Wallet draws itself.
    """
    try:
        band = _band_art(theme_id).copy()
        text = Image.new("RGBA", band.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(text)
        centre = STRIP_WIDTH * RENDER_SCALE / 2

        draw.text(
            (centre, ROLE_BASELINE * RENDER_SCALE),
            role_title,
            font=_font("Medium", round(ROLE_SIZE * RENDER_SCALE)),
            fill=(*text_rgb, round(255 * ROLE_OPACITY)),
            anchor="ms",
        )
        name, name_font = _fit_name(draw, full_name)
        draw.text((centre, NAME_BASELINE * RENDER_SCALE), name, font=name_font, fill=(*text_rgb, 255), anchor="ms")

        rendered = Image.alpha_composite(band, text).convert("RGB")
    except Exception:
        logger.exception("Could not draw the name into the Apple Wallet strip; falling back to native fields")
        return None

    images: Dict[str, bytes] = {}
    for suffix, scale in SCALES.items():
        size = (STRIP_WIDTH * scale, STRIP_HEIGHT * scale)
        image = rendered if size == rendered.size else rendered.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        images[suffix] = buffer.getvalue()
    return images
