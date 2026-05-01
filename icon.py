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
    """Bundle the iconset into a .icns via iconutil. If the iconset
    already has all expected PNGs (e.g. provided by a designer) we use
    them as-is and never overwrite. Only missing sizes get rendered
    from the placeholder Cocoa drawing. Regenerates .icns whenever any
    iconset PNG is newer than the cached .icns."""
    icns_path = _assets_dir() / "yt_icon.icns"
    iconset_dir = _assets_dir() / "yt_icon.iconset"

    spec = []
    for size in (16, 32, 128, 256, 512):
        spec.append((iconset_dir / f"icon_{size}x{size}.png", size))
        spec.append((iconset_dir / f"icon_{size}x{size}@2x.png", size * 2))

    if icns_path.exists() and iconset_dir.exists():
        icns_mtime = icns_path.stat().st_mtime
        newer = any(
            p.exists() and p.stat().st_mtime > icns_mtime for p, _ in spec
        )
        if not newer:
            return icns_path

    iconset_dir.mkdir(parents=True, exist_ok=True)
    icns_path.parent.mkdir(parents=True, exist_ok=True)
    for path, px in spec:
        if not path.exists():
            _render_png(path, px)

    if icns_path.exists():
        icns_path.unlink()
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(icns_path)],
        check=True,
    )
    return icns_path
