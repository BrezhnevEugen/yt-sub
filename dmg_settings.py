"""dmgbuild settings.

Invoked from release.sh:
    .venv/bin/dmgbuild \
        -s dmg_settings.py \
        -D app=dist/YT-sub.app \
        "YT-sub <version>" \
        dist/YT-sub-<version>.dmg

The file is exec()'d by dmgbuild with `defines` (the -D values) and a
few other names pre-bound. The variable names below are recognized by
dmgbuild — see https://dmgbuild.readthedocs.io/en/latest/settings.html.
"""
from __future__ import annotations

import os.path


# --- Sources --------------------------------------------------------

# The .app to ship. Passed in from release.sh via -D app=...
application = defines.get("app", "dist/YT-sub.app")  # noqa: F821 — `defines` is provided by dmgbuild
appname = os.path.basename(application)  # e.g. "YT-sub.app"

# Files copied into the DMG. Anything not listed is omitted; a side
# effect of using dmgbuild is we no longer need a separate staging
# directory like the old hdiutil-based path did.
files = [application]

# Symlinks created inside the DMG. The classic /Applications shortcut
# next to the app gives the user a drop target right there in the
# Finder window.
symlinks = {"Applications": "/Applications"}


# --- Disk image format ----------------------------------------------

# UDZO = compressed read-only. Matches what the previous hdiutil call
# produced, so the on-disk size doesn't regress.
format = "UDZO"
size = None  # let dmgbuild pick — auto-sizes from the staged content

# Sign the DMG ourselves after the fact (in release.sh) so dmgbuild
# doesn't need access to the keychain identity.


# --- Window / icon view --------------------------------------------

# Use Icon View with a fixed layout — the whole point of this rewrite
# is to render the install instructions as part of the disk image
# rather than relying on Finder defaults.
default_view = "icon-view"
include_icon_view_settings = "auto"
include_list_view_settings = "auto"

# Window position (origin from top-left of screen) and size (in points).
window_rect = ((100, 120), (540, 380))

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
sidebar_width = 180

# Icon view settings — `icon_size` is the displayed pixel size; the
# arrangement_y/x grid is unused since we set explicit positions.
icon_size = 128
text_size = 13
arrange_by = None

# Icon coordinates (origin top-left, in points within the window).
# These match the slots designed in assets/dmg-background.png:
#   • the .app icon sits to the left of the red arrow,
#   • the Applications shortcut sits to the right of it,
# both centered on y=200.
icon_locations = {
    appname: (130, 200),
    "Applications": (410, 200),
}

# Background image. dmgbuild accepts PNG / JPEG / TIFF. The PNG is a
# 1080x760 image (the @2x of the 540x380 point window); Finder scales
# it to fit, giving a crisp render on Retina.
background = "assets/dmg-background.png"

# NOTE: dmgbuild 1.6.7 doesn't actually invoke any post-mount
# AppleScript (no support in core.py), so we can't bless the layout
# from within dmgbuild itself. release.sh does the bless pass after
# dmgbuild — mount RW, run dmg_setup.applescript, re-compress — so
# modern Finder honours the window bounds + hidden chrome.
