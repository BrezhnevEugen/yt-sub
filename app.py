from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

# Headless MCP mode — single binary, two entry points. Enter this branch
# *before* importing rumps / AppKit so the MCP host doesn't start a GUI
# event loop in the agent subprocess.
if "--mcp" in sys.argv:
    from mcp_server import mcp
    mcp.run()
    sys.exit(0)

if "--version" in sys.argv or "-V" in sys.argv:
    from version import __version__
    print(f"YT-sub {__version__}")
    sys.exit(0)

import rumps

try:
    from AppKit import NSBundle
    info = NSBundle.mainBundle().infoDictionary()
    info["LSUIElement"] = "1"
except Exception:
    pass

from storage import OUTPUT_DIR, CLIENT_SECRET_PATH, TOKEN_PATH
from youtube_client import YouTubeClient, AuthError
from transcript import fetch_transcript, TranscriptError, parse_video_id
from icon import ensure_icon
from stats import compute_stats, format_stats
import config as yt_config
from version import __version__


LAUNCH_AGENT_LABEL = "com.brezhnev.yt-sub"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False) is not False


def _bundle_launcher() -> Optional[Path]:
    """Path to <YT-sub.app>/Contents/MacOS/YT-sub when running inside a
    py2app bundle, else None. Uses NSBundle so it stays correct even if
    sys.executable points at the embedded Python framework."""
    if not _is_frozen():
        return None
    try:
        from AppKit import NSBundle
        bp = NSBundle.mainBundle().bundlePath()
        if bp:
            return Path(bp) / "Contents" / "MacOS" / "YT-sub"
    except Exception:
        pass
    return Path(sys.executable).resolve().parent.parent / "MacOS" / "YT-sub"


def _agent_command() -> tuple[str, list[str]]:
    """Return (command, args) for the MCP host config and LaunchAgent —
    different paths for source install vs DMG-bundled install."""
    if _is_frozen():
        launcher = _bundle_launcher()
        return str(launcher), ["--mcp"]
    project = Path(__file__).resolve().parent
    return str(project / ".venv" / "bin" / "python"), [str(project / "mcp_server.py")]


BUSY_SUFFIX = "…"


class YTSubApp(rumps.App):
    def __init__(self) -> None:
        try:
            icon_path = str(ensure_icon())
        except Exception:
            icon_path = None
        super().__init__(
            "YT-sub",
            title=None,
            icon=icon_path,
            template=False,
            quit_button=None,
        )
        self.client = YouTubeClient()
        self._last_output: Optional[Path] = None
        self._busy = False

        self._header = rumps.MenuItem(
            f"YT-sub v{__version__} — YouTube metadata + transcripts"
        )
        self._status = rumps.MenuItem("…")

        self._cookies_menu = rumps.MenuItem("yt-dlp cookies from…")
        self._cookies_items = {}
        for label in ("(disabled)",) + yt_config.SUPPORTED_BROWSERS:
            mi = rumps.MenuItem(label, callback=self.set_cookies_browser)
            self._cookies_items[label] = mi
            self._cookies_menu.add(mi)

        self.menu = [
            self._header,
            self._status,
            None,
            rumps.MenuItem("Process URL…", callback=self.process_url),
            None,
            rumps.MenuItem("Load client_secret.json…", callback=self.load_client_secret),
            rumps.MenuItem("Sign in with Google", callback=self.sign_in),
            rumps.MenuItem("Sign out", callback=self.sign_out),
            self._cookies_menu,
            None,
            rumps.MenuItem("Open last result", callback=self.open_last),
            rumps.MenuItem("Open output folder", callback=self.open_output),
            rumps.MenuItem("Statistics", callback=self.show_stats),
            None,
            rumps.MenuItem("Auto-start on login", callback=self.toggle_login_item),
            rumps.MenuItem("Copy MCP config", callback=self.copy_mcp_config),
            rumps.MenuItem("Install skill (~/.claude)", callback=self.install_skill_global),
            rumps.MenuItem("Install skill in project…", callback=self.install_skill_in_project),
            rumps.MenuItem("Copy skill to clipboard", callback=self.copy_skill_to_clipboard),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._refresh_menu()

    def _status_text(self) -> str:
        if self._busy:
            auth = "Processing…"
        elif self.client.is_authenticated():
            auth = "Signed in"
        elif CLIENT_SECRET_PATH.exists():
            auth = "Not signed in"
        else:
            auth = "Setup required"
        try:
            s = compute_stats()
        except Exception:
            return auth
        if s["videos"]:
            return f"{auth}  ·  {s['videos']} videos  ·  {s['total_duration_human']}"
        return auth

    def _refresh_menu(self) -> None:
        signed_in = self.client.is_authenticated()
        has_secret = CLIENT_SECRET_PATH.exists()
        self.menu["Sign in with Google"].set_callback(
            self.sign_in if (has_secret and not signed_in) else None
        )
        self.menu["Sign out"].set_callback(self.sign_out if signed_in else None)
        self.menu["Process URL…"].set_callback(
            self.process_url if (signed_in and not self._busy) else None
        )
        self.menu["Open last result"].set_callback(
            self.open_last if self._last_output else None
        )
        try:
            on = LAUNCH_AGENT_PATH.exists()
            self.menu["Auto-start on login"].state = 1 if on else 0
        except Exception:
            pass
        try:
            current = yt_config.get_ytdlp_browser()
            for label, mi in self._cookies_items.items():
                is_active = (label == "(disabled)" and current is None) or label == current
                mi.state = 1 if is_active else 0
        except Exception:
            pass
        try:
            self._status.title = self._status_text()
        except Exception:
            pass

    def load_client_secret(self, _) -> None:
        script = (
            'try\n'
            '  set f to choose file with prompt '
            '"Select client_secret.json from Google Cloud Console" of type {"json", "public.json"}\n'
            '  return POSIX path of f\n'
            'on error number -128\n'
            '  return ""\n'
            'end try'
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        path_str = result.stdout.strip()
        if not path_str:
            return

        src = Path(path_str)
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:
            rumps.alert(title="Invalid JSON", message=f"Cannot parse file:\n{e}")
            return

        section = data.get("installed") or data.get("web")
        if not section or not section.get("client_id") or not section.get("client_secret"):
            rumps.alert(
                title="Not an OAuth client_secret.json",
                message=(
                    "Expected a Google OAuth client JSON with an "
                    "\"installed\" or \"web\" section containing client_id "
                    "and client_secret.\n\nDownload it from Google Cloud Console "
                    "→ APIs & Services → Credentials → OAuth client ID "
                    "(Application type: Desktop app)."
                ),
            )
            return

        replaced = CLIENT_SECRET_PATH.exists()
        CLIENT_SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, CLIENT_SECRET_PATH)
        if TOKEN_PATH.exists():
            TOKEN_PATH.unlink()
            self.client.sign_out()

        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "Credentials replaced" if replaced else "Credentials loaded",
            "Now choose Sign in with Google",
        )

    def sign_in(self, _) -> None:
        if not CLIENT_SECRET_PATH.exists():
            rumps.alert(
                title="Missing OAuth credentials",
                message=(
                    "Use \"Load client_secret.json…\" in the menu to pick the "
                    "OAuth client JSON.\n\nGet one from Google Cloud Console → "
                    "APIs & Services → Credentials → OAuth client ID "
                    "(Application type: Desktop app). Make sure YouTube Data "
                    "API v3 is enabled."
                ),
            )
            return
        try:
            self.client.sign_in()
            rumps.notification("YT-sub", "Signed in", "Authenticated with Google")
        except Exception as e:
            rumps.alert(title="Sign-in failed", message=str(e))
        finally:
            self._refresh_menu()

    def sign_out(self, _) -> None:
        self.client.sign_out()
        self._refresh_menu()
        rumps.notification("YT-sub", "Signed out", "")

    def open_output(self, _) -> None:
        subprocess.run(["open", str(OUTPUT_DIR)])

    def copy_mcp_config(self, _) -> None:
        command, args = _agent_command()
        config = {
            "mcpServers": {
                "yt-sub": {
                    "command": command,
                    "args": args,
                }
            }
        }
        text = json.dumps(config, indent=2, ensure_ascii=False)
        subprocess.run(["pbcopy"], input=text, text=True)
        rumps.notification(
            "YT-sub",
            "MCP config copied",
            "Paste into Claude Desktop / Claude Code MCP config",
        )

    def toggle_login_item(self, _) -> None:
        if LAUNCH_AGENT_PATH.exists():
            self._disable_login_item()
        else:
            self._enable_login_item()
        self._refresh_menu()

    def _enable_login_item(self) -> None:
        if _is_frozen():
            launcher = _bundle_launcher()
            program_args = [str(launcher)]
            working_dir = str(launcher.parent)
        else:
            project = Path(__file__).resolve().parent
            program_args = [
                str(project / ".venv" / "bin" / "python"),
                str(project / "app.py"),
            ]
            working_dir = str(project)

        log_file = str(Path.home() / "Library" / "Logs" / "yt-sub.log")
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)

        args_xml = "\n    ".join(f"<string>{a}</string>" for a in program_args)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    {args_xml}
  </array>
  <key>WorkingDirectory</key><string>{working_dir}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <key>Crashed</key><true/>
    <key>SuccessfulExit</key><false/>
  </dict>
  <key>StandardOutPath</key><string>{log_file}</string>
  <key>StandardErrorPath</key><string>{log_file}</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"""
        LAUNCH_AGENT_PATH.write_text(plist, encoding="utf-8")
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCH_AGENT_PATH)],
            capture_output=True,
        )
        rumps.notification("YT-sub", "Auto-start on login: ON", "Will run on every login")

    def _disable_login_item(self) -> None:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{LAUNCH_AGENT_LABEL}"],
            capture_output=True,
        )
        if LAUNCH_AGENT_PATH.exists():
            LAUNCH_AGENT_PATH.unlink()
        rumps.notification("YT-sub", "Auto-start on login: OFF", "")

    def _skill_full(self) -> str:
        src = Path(__file__).resolve().parent / "skill" / "SKILL.md"
        return src.read_text(encoding="utf-8")

    def _split_frontmatter(self, text: str) -> tuple[dict, str]:
        if not text.startswith("---"):
            return {}, text
        end = text.find("\n---", 3)
        if end == -1:
            return {}, text
        fm_raw = text[3:end].strip("\n")
        body = text[end + 4 :].lstrip("\n")
        meta: dict = {}
        for line in fm_raw.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
        return meta, body

    def _cursor_rule(self) -> str:
        meta, body = self._split_frontmatter(self._skill_full())
        desc = meta.get("description", "")
        return f"---\ndescription: {desc}\nalwaysApply: false\n---\n\n{body}"

    def install_skill_global(self, _) -> None:
        src = Path(__file__).resolve().parent / "skill" / "SKILL.md"
        if not src.exists():
            rumps.alert(title="Skill source missing", message=f"Not found: {src}")
            return
        dest_dir = Path.home() / ".claude" / "skills" / "yt-sub"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "SKILL.md"
        replaced = dest.exists()
        shutil.copyfile(src, dest)
        rumps.notification(
            "YT-sub",
            "Skill replaced" if replaced else "Skill installed",
            "~/.claude/skills/yt-sub/SKILL.md",
        )

    def install_skill_in_project(self, _) -> None:
        script = (
            'try\n'
            '  set f to choose folder with prompt '
            '"Select project root for yt-sub agent rules"\n'
            '  return POSIX path of f\n'
            'on error number -128\n'
            '  return ""\n'
            'end try'
        )
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True
        )
        project = result.stdout.strip()
        if not project:
            return

        project_path = Path(project)
        if not project_path.is_dir():
            rumps.alert(title="Not a folder", message=str(project_path))
            return

        skill_full = self._skill_full()
        _, skill_body = self._split_frontmatter(skill_full)
        cursor_rule = self._cursor_rule()
        written: list[str] = []

        claude_skill = project_path / ".claude" / "skills" / "yt-sub" / "SKILL.md"
        claude_skill.parent.mkdir(parents=True, exist_ok=True)
        claude_skill.write_text(skill_full, encoding="utf-8")
        written.append(".claude/skills/yt-sub/SKILL.md")

        cursor_path = project_path / ".cursor" / "rules" / "yt-sub.mdc"
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text(cursor_rule, encoding="utf-8")
        written.append(".cursor/rules/yt-sub.mdc")

        agents = project_path / "AGENTS.md"
        section = (
            "\n\n## yt-sub (YouTube metadata + transcript MCP)\n\n" + skill_body
        )
        marker = "## yt-sub (YouTube metadata + transcript MCP)"
        if agents.exists():
            existing = agents.read_text(encoding="utf-8")
            if marker not in existing:
                agents.write_text(existing.rstrip() + section, encoding="utf-8")
                written.append("AGENTS.md (appended)")
            else:
                written.append("AGENTS.md (already present)")
        else:
            agents.write_text("# Agent instructions" + section, encoding="utf-8")
            written.append("AGENTS.md (created)")

        rumps.notification(
            "YT-sub", "Skill installed in project", " · ".join(written)
        )

    def copy_skill_to_clipboard(self, _) -> None:
        subprocess.run(["pbcopy"], input=self._skill_full(), text=True)
        rumps.notification(
            "YT-sub",
            "Skill copied",
            "Paste into your agent's rules file or system prompt",
        )

    def open_last(self, _) -> None:
        if self._last_output and self._last_output.exists():
            subprocess.run(["open", str(self._last_output)])

    def process_url(self, _) -> None:
        window = rumps.Window(
            message="Paste a YouTube video URL",
            title="YT-sub",
            default_text="",
            ok="Process",
            cancel="Cancel",
            dimensions=(380, 24),
        )
        response = window.run()
        if not response.clicked:
            return
        url = response.text.strip()
        if not url:
            return
        threading.Thread(target=self._process, args=(url,), daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.title = BUSY_SUFFIX if busy else None
        self._refresh_menu()

    def set_cookies_browser(self, sender) -> None:
        label = sender.title
        browser = None if label == "(disabled)" else label
        yt_config.set_ytdlp_browser(browser)
        self._refresh_menu()
        msg = f"yt-dlp will use cookies from {browser}" if browser else "yt-dlp cookies disabled"
        rumps.notification("YT-sub", "Cookie source updated", msg)

    def show_stats(self, _) -> None:
        try:
            stats = compute_stats()
            rumps.alert(
                title=f"YT-sub v{__version__}",
                message=format_stats(stats),
            )
        except Exception as e:
            rumps.alert(title="Stats failed", message=str(e))

    def _process(self, url: str) -> None:
        self._set_busy(True)
        try:
            try:
                video_id = parse_video_id(url)
            except ValueError as e:
                rumps.notification("YT-sub", "Invalid URL", str(e))
                return

            try:
                metadata = self.client.fetch_metadata(video_id)
            except AuthError:
                rumps.notification("YT-sub", "Sign in required", "Use the menu to sign in")
                return
            except Exception as e:
                rumps.notification("YT-sub", "Metadata fetch failed", str(e))
                return

            transcript = None
            transcript_error = None
            try:
                transcript = fetch_transcript(video_id)
            except TranscriptError as e:
                transcript_error = str(e)

            out_dir = OUTPUT_DIR / video_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if transcript is not None:
                (out_dir / "transcript.json").write_text(
                    json.dumps(transcript, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                plain = "\n".join(seg.get("text", "") for seg in transcript)
                (out_dir / "transcript.txt").write_text(plain, encoding="utf-8")
            else:
                (out_dir / "transcript.error.txt").write_text(
                    transcript_error or "unknown", encoding="utf-8"
                )

            self._last_output = out_dir
            title = metadata.get("snippet", {}).get("title", video_id)
            msg = "Saved" if transcript is not None else f"No transcript: {transcript_error}"
            rumps.notification("YT-sub", title, msg)
        finally:
            self._set_busy(False)


if __name__ == "__main__":
    YTSubApp().run()
