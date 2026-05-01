from __future__ import annotations

import subprocess
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSETS_DIR / "yt_icon.png"
ICNS_PATH = ASSETS_DIR / "yt_icon.icns"
ICONSET_DIR = ASSETS_DIR / "yt_icon.iconset"


def _render_png(out_path: Path, size: int) -> None:
    from AppKit import (
        NSBezierPath,
        NSBitmapImageRep,
        NSColor,
        NSImage,
        NSMakeRect,
        NSMakeSize,
        NSPNGFileType,
    )
    from Foundation import NSPoint

    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()

    rect = NSMakeRect(0, size * 0.22, size, size * 0.56)
    body = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        rect, size * 0.14, size * 0.14
    )
    NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.0, 0.0, 1.0).set()
    body.fill()

    cx, cy = size / 2.0, size / 2.0
    triangle = NSBezierPath.bezierPath()
    triangle.moveToPoint_(NSPoint(cx - size * 0.10, cy - size * 0.13))
    triangle.lineToPoint_(NSPoint(cx - size * 0.10, cy + size * 0.13))
    triangle.lineToPoint_(NSPoint(cx + size * 0.15, cy))
    triangle.closePath()
    NSColor.whiteColor().set()
    triangle.fill()

    img.unlockFocus()

    tiff = img.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
    png_data.writeToFile_atomically_(str(out_path), True)


def ensure_icon() -> Path:
    """Render the menu-bar icon as a 36px PNG. Idempotent."""
    if ICON_PATH.exists():
        return ICON_PATH
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    _render_png(ICON_PATH, 36)
    return ICON_PATH


def ensure_icns() -> Path:
    """Render a multi-resolution iconset and bundle it as a .icns via
    iconutil. Idempotent."""
    if ICNS_PATH.exists():
        return ICNS_PATH

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    if ICONSET_DIR.exists():
        for f in ICONSET_DIR.iterdir():
            f.unlink()
    else:
        ICONSET_DIR.mkdir(parents=True, exist_ok=True)

    # Apple iconset spec: 16, 32, 128, 256, 512 + @2x variants.
    for size in (16, 32, 128, 256, 512):
        _render_png(ICONSET_DIR / f"icon_{size}x{size}.png", size)
        _render_png(ICONSET_DIR / f"icon_{size}x{size}@2x.png", size * 2)

    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(ICNS_PATH)],
        check=True,
    )
    return ICNS_PATH
