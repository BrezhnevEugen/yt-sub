from __future__ import annotations

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_PATH = ASSETS_DIR / "yt_icon.png"


def ensure_icon() -> Path:
    """Render a small YouTube-style icon (red rounded rect + white play
    triangle) and save it as a PNG. Idempotent."""
    if ICON_PATH.exists():
        return ICON_PATH

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    from AppKit import (
        NSImage,
        NSColor,
        NSBezierPath,
        NSMakeRect,
        NSMakeSize,
        NSBitmapImageRep,
        NSPNGFileType,
    )
    from Foundation import NSPoint

    size = 36
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
    png_data.writeToFile_atomically_(str(ICON_PATH), True)
    return ICON_PATH
