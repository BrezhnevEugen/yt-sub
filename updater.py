"""Self-update mechanism for YT-sub.

Flow:
  1. Download the .dmg asset from the GitHub release.
  2. hdiutil-mount it read-only.
  3. Copy YT-sub.app from the mount into a temp dir.
  4. Detach the DMG.
  5. Write a tiny shell-script relauncher; spawn it detached.
  6. Caller (app.py) terminates the running process.
  7. The relauncher waits for our PID to die, removes the old bundle,
     moves the new one into place, strips the quarantine xattr, and
     `open`s the replaced bundle.

Why not Sparkle: Sparkle is the macOS standard for in-app updates, but it
ships as an ObjC framework and doesn't bundle cleanly via py2app.
Hand-rolling this is ~150 LOC and avoids the framework dependency.
Gatekeeper still verifies notarization on first launch of the swapped
bundle, so we don't need to re-verify signatures ourselves.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import certifi


class UpdateError(Exception):
    pass


ProgressCb = Callable[[int, int], None]  # (downloaded_bytes, total_bytes)


def current_bundle_path() -> Path:
    """Absolute path of the running .app bundle. Raises UpdateError if
    we're not inside one (e.g. running `python app.py` from a checkout)."""
    exe = Path(sys.executable).resolve()
    for i in range(len(exe.parts) - 1, -1, -1):
        if exe.parts[i].endswith(".app"):
            return Path(*exe.parts[: i + 1])
    raise UpdateError(
        "Not running from an .app bundle — self-update is only supported "
        "in the packaged version."
    )


def _pick_dmg_url(release: dict) -> Optional[str]:
    for a in release.get("assets") or []:
        name = a.get("name") or ""
        if name.lower().endswith(".dmg"):
            return a.get("browser_download_url")
    return None


def download_dmg(url: str, dest: Path, progress: Optional[ProgressCb] = None) -> Path:
    """Download `url` to `dest`. py2app's bundled Python has no link to
    the system trust store; route SSL through certifi."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers={"User-Agent": "YT-sub-Updater"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with dest.open("wb") as f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got, total)
    return dest


def _hdiutil_attach(dmg_path: Path) -> Path:
    """Mount DMG read-only and return the mount point."""
    out = subprocess.check_output(
        [
            "hdiutil", "attach", "-nobrowse", "-noverify",
            "-noautoopen", "-readonly", "-plist", str(dmg_path),
        ]
    )
    plist = plistlib.loads(out)
    for entity in plist.get("system-entities", []):
        mp = entity.get("mount-point")
        if mp:
            return Path(mp)
    raise UpdateError("hdiutil attach succeeded but yielded no mount point")


def _hdiutil_detach(mount_point: Path) -> None:
    subprocess.run(
        ["hdiutil", "detach", "-quiet", str(mount_point)],
        check=False,
    )


def copy_app_from_dmg(dmg_path: Path, dest_dir: Path) -> Path:
    """Mount DMG, copy the single *.app bundle out of it into dest_dir,
    unmount. Returns path of the copied bundle."""
    mp = _hdiutil_attach(dmg_path)
    try:
        apps = list(mp.glob("*.app"))
        if not apps:
            raise UpdateError(f"No .app inside DMG (mount: {mp})")
        src = apps[0]
        target = dest_dir / src.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target, symlinks=True)
        return target
    finally:
        _hdiutil_detach(mp)


_FAIL_ALERT_RO = (
    "Could not replace YT-sub.app in place. The bundle lives in a "
    "read-only or admin-owned directory. Move it to /Applications or "
    "~/Applications, then retry the update."
)
_FAIL_ALERT_MV = (
    "Installed the new bundle into a backup location but could not "
    "move it into place. Restored the previous version. Try updating "
    "again or download the DMG manually from GitHub."
)


def _write_relauncher(current_bundle: Path, new_bundle: Path, pid: int) -> Path:
    script = (
        "#!/bin/sh\n"
        "# YT-sub self-updater. Waits for the running process to exit,\n"
        "# swaps the bundle, and re-launches. Rolls back on failure so\n"
        "# the user is never left without an .app on disk.\n"
        f"PID={pid}\n"
        f"OLD_APP={shlex.quote(str(current_bundle))}\n"
        f"NEW_APP={shlex.quote(str(new_bundle))}\n"
        'BACKUP="${OLD_APP}.old.$$"\n'
        "\n"
        "# Wait up to 60s for the running process to exit.\n"
        "for i in $(seq 1 120); do\n"
        '    if ! ps -p "$PID" > /dev/null 2>&1; then break; fi\n'
        "    sleep 0.5\n"
        "done\n"
        "\n"
        '# Strip the quarantine xattr so Launch Services skips the prompt.\n'
        '/usr/bin/xattr -dr com.apple.quarantine "$NEW_APP" 2>/dev/null\n'
        "\n"
        '# Step 1: rename the old bundle out of the way (atomic).\n'
        'if ! mv "$OLD_APP" "$BACKUP" 2>/dev/null; then\n'
        "    /usr/bin/osascript -e "
        f"{shlex.quote('display alert \"YT-sub update failed\" message ' + repr(_FAIL_ALERT_RO) + ' as critical')}"
        " >/dev/null 2>&1\n"
        '    [ -d "$OLD_APP" ] && /usr/bin/open "$OLD_APP"\n'
        '    rm -f "$0"\n'
        "    exit 1\n"
        "fi\n"
        "\n"
        '# Step 2: move the new bundle into place. On failure, restore.\n'
        'if ! mv "$NEW_APP" "$OLD_APP" 2>/dev/null; then\n'
        '    mv "$BACKUP" "$OLD_APP" 2>/dev/null\n'
        "    /usr/bin/osascript -e "
        f"{shlex.quote('display alert \"YT-sub update failed\" message ' + repr(_FAIL_ALERT_MV) + ' as critical')}"
        " >/dev/null 2>&1\n"
        '    [ -d "$OLD_APP" ] && /usr/bin/open "$OLD_APP"\n'
        '    rm -f "$0"\n'
        "    exit 1\n"
        "fi\n"
        "\n"
        '# Step 3: drop the backup and relaunch.\n'
        'rm -rf "$BACKUP" 2>/dev/null\n'
        '/usr/bin/open "$OLD_APP"\n'
        'rm -f "$0"\n'
    )
    fd, path = tempfile.mkstemp(prefix="yt-sub-updater-", suffix=".sh")
    os.close(fd)
    p = Path(path)
    p.write_text(script, encoding="utf-8")
    p.chmod(0o755)
    return p


def install_update(release: dict, progress: Optional[ProgressCb] = None) -> None:
    """Drive the whole flow. On success, returns after the detached
    relauncher script is running; the caller is then responsible for
    terminating the current process so the relauncher can swap bundles.

    Raises UpdateError on any failure (network, mount, no DMG asset,
    not running inside a bundle, etc.)."""
    current = current_bundle_path()

    dmg_url = _pick_dmg_url(release)
    if not dmg_url:
        raise UpdateError("Latest release has no .dmg asset")

    workdir = Path(tempfile.mkdtemp(prefix="yt-sub-update-"))
    dmg_path = workdir / "update.dmg"
    try:
        download_dmg(dmg_url, dmg_path, progress=progress)
    except Exception as e:
        raise UpdateError(f"download failed: {e}") from e

    try:
        new_bundle = copy_app_from_dmg(dmg_path, workdir)
    except Exception as e:
        raise UpdateError(f"DMG extraction failed: {e}") from e

    script = _write_relauncher(current, new_bundle, os.getpid())
    try:
        subprocess.Popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        raise UpdateError(f"failed to spawn relauncher: {e}") from e
