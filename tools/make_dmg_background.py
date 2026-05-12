"""Render the DMG installer background.

Outputs a 1080x760 px PNG (@2x of the 540x380 point Finder window) at
`assets/dmg-background.png`. Drawing uses Pillow — added to
requirements.txt as a build-time-only dep.

Design notes:
  • Vertical gradient, off-white → cool light gray.
  • "INSTALL" eyebrow caps in muted red above the main title — small
    Apple-installer touch.
  • Soft red halos under each icon slot so the Finder-drawn icons feel
    "landed" rather than floating on a flat background.
  • Triple-chevron arrow with an alpha trail (leftmost faint, rightmost
    solid) for a clearer sense of direction than a single arrowhead.
  • Title / subtitle centered, system font, restrained weights.

Re-run this one-shot whenever the design changes; the resulting PNG is
committed and consumed by `dmg_settings.py` at release time.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


# Logical (point) canvas — half the dmgbuild window_rect size so the
# painted graphics occupy the *upper* half of the Finder window, and
# the two real icons (placed by Finder at y=200 in window points) sit
# in the empty lower half without crowding into the background art.
WIDTH = 270
HEIGHT = 190
SCALE = 2  # Retina output (final PNG = 540×380 px)

# Brand red (matches the app icon).
BRAND_R, BRAND_G, BRAND_B = 217, 56, 41


def _system_font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """Best-effort load of SF Pro / Helvetica with the right weight."""
    for path in (
        "/System/Library/Fonts/SFNS.ttf",          # SF Pro variable
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if not Path(path).exists():
            continue
        try:
            font = ImageFont.truetype(path, size=size)
        except OSError:
            continue
        try:
            variant = {
                "bold": "Bold",
                "semibold": "Semibold",
                "medium": "Medium",
                "light": "Light",
                "regular": "Regular",
            }.get(weight, "Regular")
            font.set_variation_by_name(variant)
        except Exception:
            pass
        return font
    return ImageFont.load_default()


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    y: float,
    font: ImageFont.FreeTypeFont,
    fill: tuple,
    tracking_px: float,
) -> float:
    """Pillow has no letter-spacing API — render glyph by glyph with a
    manual advance so caps can breathe. Returns total drawn width."""
    cur_x = x
    for ch in text:
        draw.text((cur_x, y), ch, fill=fill, font=font)
        cur_x += draw.textlength(ch, font=font) + tracking_px
    return cur_x - tracking_px - x


def _draw_halo(
    base: Image.Image,
    center_x: int,
    center_y: int,
    radius: int,
    color: tuple,
    blur_px: int,
) -> None:
    """Composite a soft red glow centered at (center_x, center_y) onto
    `base`. The glow is a filled circle on a transparent layer that we
    then GaussianBlur and paste back — sharper falloff than a hand-rolled
    radial gradient and effectively free."""
    pad = radius + blur_px * 2
    layer = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse(
        (pad - radius, pad - radius, pad + radius, pad + radius),
        fill=color,
    )
    layer = layer.filter(ImageFilter.GaussianBlur(blur_px))
    base.alpha_composite(layer, (center_x - pad, center_y - pad))


def _draw_chevron(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    size: int,
    width: int,
    color: tuple,
) -> None:
    """Right-pointing chevron centered at (cx, cy), wing-length `size`."""
    # Top wing.
    draw.line(
        [(cx - size, cy - size), (cx, cy)],
        fill=color,
        width=width,
    )
    # Bottom wing.
    draw.line(
        [(cx, cy), (cx - size, cy + size)],
        fill=color,
        width=width,
    )


def _draw(width: int, height: int, scale: int) -> Image.Image:
    W, H = width * scale, height * scale
    img = Image.new("RGBA", (W, H), (255, 255, 255, 255))

    # 1. Vertical gradient (off-white at top → cool light gray at bottom).
    top = (251, 251, 252)
    bot = (231, 232, 240)
    pixels = img.load()
    for y in range(H):
        t = y / (H - 1)
        r = round(top[0] * (1 - t) + bot[0] * t)
        g = round(top[1] * (1 - t) + bot[1] * t)
        b = round(top[2] * (1 - t) + bot[2] * t)
        for x in range(W):
            pixels[x, y] = (r, g, b, 255)

    # Finder stretches the 540×380 px background to fill the 540×380
    # pt window 1:1, so layout coords double as window-point coords.
    # Icons sit at window pt (130, 200) and (410, 200), glyphs span
    # roughly y = 136–264. Keep all text above y = 130 so it doesn't
    # crash into the icon tops, and put the chevron arrow on the icon
    # center line (y = 200) right at the horizontal midpoint (x = 270).

    draw = ImageDraw.Draw(img)

    # 2. Eyebrow caps — small tracked "INSTALL" in muted brand-red.
    eyebrow_font = _system_font(int(7 * scale), weight="bold")
    eyebrow = "INSTALL"
    tracking = 2 * scale
    naive_w = draw.textlength(eyebrow, font=eyebrow_font)
    total_w = naive_w + tracking * (len(eyebrow) - 1)
    _draw_tracked(
        draw,
        eyebrow,
        (W - total_w) / 2,
        14 * scale,
        eyebrow_font,
        (BRAND_R - 5, BRAND_G + 5, BRAND_B + 5, 220),
        tracking,
    )

    # 3. Title "YT-sub" — sits snug under the eyebrow.
    title_font = _system_font(18 * scale, weight="bold")
    title = "YT-sub"
    tw = draw.textlength(title, font=title_font)
    draw.text(
        ((W - tw) / 2, 22 * scale),
        title,
        fill=(34, 34, 38, 255),
        font=title_font,
    )

    # 4. Subtitle in a lighter weight so it visually recedes.
    sub_font = _system_font(10 * scale, weight="light")
    sub = "Drag YT-sub to Applications to install"
    sw = draw.textlength(sub, font=sub_font)
    draw.text(
        ((W - sw) / 2, 44 * scale),
        sub,
        fill=(97, 97, 105, 255),
        font=sub_font,
    )

    # 5. Triple chevron arrow on the icon center line (window pt y=200),
    #    horizontally centered between the two icons (window pt x=270),
    #    with an alpha trail (leftmost faint → rightmost solid).
    chevron_cy = 100 * scale  # window pt y=200 = icon center
    chevron_size = 6 * scale
    chevron_width = 3 * scale
    chevron_step = 12 * scale
    # Three chevrons centered on x=W/2. Middle chevron at the midpoint;
    # rightmost = midpoint + step.
    rightmost_cx = int(W / 2 + chevron_step)
    for i, alpha in enumerate((90, 160, 235)):
        cx = rightmost_cx - (2 - i) * chevron_step
        _draw_chevron(
            draw,
            cx,
            chevron_cy,
            chevron_size,
            chevron_width,
            (BRAND_R, BRAND_G, BRAND_B, alpha),
        )

    return img


def main(out_path: Path) -> None:
    img = _draw(WIDTH, HEIGHT, SCALE)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    print(
        f"Wrote {out_path} ({out_path.stat().st_size:,} bytes, "
        f"{WIDTH * SCALE}×{HEIGHT * SCALE} @2x of {WIDTH}×{HEIGHT})"
    )


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/dmg-background.png")
    main(out)
