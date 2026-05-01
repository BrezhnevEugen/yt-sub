from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _assets_dir() -> Path:
    """Where bundled assets live. In a py2app bundle they're under
    Contents/Resources/assets/ (placed there by DATA_FILES); in source
    mode they're alongside icon.py."""
    if getattr(sys, "frozen", False):
        try:
            from AppKit import NSBundle
            rp = NSBundle.mainBundle().resourcePath()
            if rp:
                bundled = Path(rp) / "assets"
                if bundled.exists() or not (Path(__file__).resolve().parent / "assets").exists():
                    return bundled
        except Exception:
            pass
    return Path(__file__).resolve().parent / "assets"


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
    """Return path to the menu-bar icon PNG. If it already exists (e.g.
    bundled into the .app via DATA_FILES, or rendered during a previous
    run in source mode) just return it. Otherwise render fresh."""
    icon_path = _assets_dir() / "yt_icon.png"
    if icon_path.exists():
        return icon_path
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    _render_png(icon_path, 36)
    return icon_path


def ensure_icns() -> Path:
    """Render a multi-resolution iconset and bundle it as a .icns via
    iconutil. Idempotent. Used by release.sh; not called from a frozen
    bundle, only at build time from source."""
    icns_path = _assets_dir() / "yt_icon.icns"
    if icns_path.exists():
        return icns_path

    iconset_dir = _assets_dir() / "yt_icon.iconset"
    icns_path.parent.mkdir(parents=True, exist_ok=True)
    if iconset_dir.exists():
        for f in iconset_dir.iterdir():
            f.unlink()
    else:
        iconset_dir.mkdir(parents=True, exist_ok=True)

    # Apple iconset spec: 16, 32, 128, 256, 512 + @2x variants.
    for size in (16, 32, 128, 256, 512):
        _render_png(iconset_dir / f"icon_{size}x{size}.png", size)
        _render_png(iconset_dir / f"icon_{size}x{size}@2x.png", size * 2)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    return icns_path
