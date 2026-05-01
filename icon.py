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


def _render_menu_template_png(out_path: Path, size: int) -> None:
    """Monochrome silhouette of the menu-bar icon — solid black on
    transparent alpha at exactly `size` x `size` pixels. macOS treats
    template images as silhouettes and auto-tints them for dark/light
    menu bar themes."""
    from AppKit import (
        NSBezierPath,
        NSBitmapImageRep,
        NSCalibratedRGBColorSpace,
        NSColor,
        NSGraphicsContext,
        NSMakeRect,
        NSPNGFileType,
    )
    from Foundation import NSPoint

    # Allocate an exact-pixel bitmap directly (NSImage.lockFocus would
    # double the backing store on Retina displays).
    rep = (
        NSBitmapImageRep.alloc()
        .initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, size, size, 8, 4, True, False,
            NSCalibratedRGBColorSpace, 0, 32,
        )
    )
    NSGraphicsContext.saveGraphicsState()
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)

    # Geometry from yt_icon_menu.svg (44×44 viewBox), scaled to size.
    # System-style filled silhouette: solid rounded rect with a
    # triangle-shaped hole, drawn as a single path with even-odd
    # winding so the inner subpath subtracts from the outer.
    f = size / 44.0
    from AppKit import NSWindingRuleEvenOdd

    silhouette = NSBezierPath.bezierPath()
    silhouette.appendBezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(6 * f, 6 * f, 32 * f, 32 * f), 9 * f, 9 * f
    )
    # Play triangle pointing right (Cocoa is y-up, mirror from SVG).
    silhouette.moveToPoint_(NSPoint(18 * f, 30 * f))
    silhouette.lineToPoint_(NSPoint(32 * f, 22 * f))
    silhouette.lineToPoint_(NSPoint(18 * f, 14 * f))
    silhouette.closePath()
    silhouette.setWindingRule_(NSWindingRuleEvenOdd)

    NSColor.blackColor().set()
    silhouette.fill()

    NSGraphicsContext.restoreGraphicsState()

    png_data = rep.representationUsingType_properties_(NSPNGFileType, None)
    png_data.writeToFile_atomically_(str(out_path), True)


def ensure_icon() -> Path:
    """Render the menu-bar icon as a template (monochrome) PNG. macOS
    auto-tints templates to match the menu bar background (dark/light).
    Always re-renders so the file is in sync with the geometry defined
    in code (cheap — Cocoa drawing at 22/44px is sub-millisecond)."""
    p1 = _assets_dir() / "yt_icon.png"
    p2 = _assets_dir() / "yt_icon@2x.png"
    p1.parent.mkdir(parents=True, exist_ok=True)
    _render_menu_template_png(p1, 22)
    _render_menu_template_png(p2, 44)
    return p1


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
