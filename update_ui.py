"""Native NSAlert-based update dialog.

rumps.alert is fine for one-liners, but the update prompt is the most
visible UI surface we ship — worth dropping down to AppKit for:
  - the app icon (not the system default),
  - a scrollable release-notes accessory pulled from the GitHub release body,
  - explicit primary button and clean three-button layout.

Must be invoked on the main thread (menu callbacks are fine).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleInformational,
    NSAlertThirdButtonReturn,
    NSApplication,
    NSBezelBorder,
    NSFont,
    NSImage,
    NSMakeRect,
    NSScrollView,
    NSTextView,
)


INSTALL = "install"
LATER = "later"
OPEN_RELEASE = "open_release"


def _clean_release_body(body: str, max_len: int = 2400) -> str:
    """Lightly clean GitHub release-notes markdown for plain-text display.

    We don't actually render markdown — just strip the noisier syntax so
    NSTextView renders something readable. Fenced code blocks are
    dropped because in this project they're build artefacts (DMG SHAs,
    commit hashes) the end user doesn't need."""
    if not body:
        return ""
    in_fence = False
    out_lines = []
    for line in body.strip().splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.lstrip().startswith("#"):
            heading = line.lstrip("# ").rstrip()
            if heading:
                out_lines.append(heading)
            continue
        out_lines.append(line)
    s = "\n".join(out_lines).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s


def show_update_dialog(
    current_version: str,
    latest_version: str,
    release_body: str = "",
    icon_path: Optional[Path] = None,
) -> str:
    """Three-button NSAlert with release-notes accessory.
    Returns INSTALL / LATER / OPEN_RELEASE."""
    # We're an LSUIElement — without this the alert can come up
    # behind whatever window currently has focus.
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    alert = NSAlert.alloc().init()
    alert.setAlertStyle_(NSAlertStyleInformational)
    alert.setMessageText_(f"YT-sub v{latest_version} is available")
    alert.setInformativeText_(f"You're currently on v{current_version}.")

    # Order matters: first button = NSAlertFirstButtonReturn (1000),
    # is the default action, responds to ⏎.
    install_btn = alert.addButtonWithTitle_("Install update")
    alert.addButtonWithTitle_("Later")            # NSAlertSecondButtonReturn (1001)
    alert.addButtonWithTitle_("Open release page")  # NSAlertThirdButtonReturn (1002)
    # ⎋ should map to Later, not Cancel-by-default which would be Install.
    alert.buttons()[1].setKeyEquivalent_("\x1b")

    if icon_path is not None and Path(icon_path).exists():
        img = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if img:
            alert.setIcon_(img)

    body = _clean_release_body(release_body)
    if body:
        sv = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 200))
        sv.setHasVerticalScroller_(True)
        sv.setBorderType_(NSBezelBorder)
        sv.setAutohidesScrollers_(True)
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 200))
        tv.setEditable_(False)
        tv.setRichText_(False)
        tv.setString_(body)
        tv.setFont_(NSFont.systemFontOfSize_(12))
        tv.setTextContainerInset_((6, 6))
        sv.setDocumentView_(tv)
        alert.setAccessoryView_(sv)

    # Keep install_btn as the keyboard-focused default. AppKit already
    # does this for the first button — the local variable is just so
    # the static-analysis bot doesn't whine about an unused return.
    _ = install_btn

    response = alert.runModal()
    if response == NSAlertFirstButtonReturn:
        return INSTALL
    if response == NSAlertThirdButtonReturn:
        return OPEN_RELEASE
    return LATER
