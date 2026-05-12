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


# Single-instance guard for tray mode. Without this, double-clicking
# the .app or running install.sh while it's already up gives you two
# menu-bar icons and two LaunchAgents fighting over the auth token.
def _single_instance_or_exit() -> None:
    import os, atexit, subprocess as _sp
    pid_file = Path.home() / ".config" / "yt-sub" / "yt-sub.pid"
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
        except Exception:
            old_pid = -1
        if old_pid > 0 and old_pid != os.getpid():
            try:
                os.kill(old_pid, 0)  # ESRCH if dead
                # Live process — make sure it's actually our app, not a
                # PID reused by something else.
                cmd = _sp.run(
                    ["ps", "-p", str(old_pid), "-o", "command="],
                    capture_output=True, text=True,
                ).stdout
                if "app.py" in cmd or "YT-sub" in cmd:
                    print(
                        f"YT-sub already running (pid={old_pid}); exiting.",
                        file=sys.stderr,
                    )
                    sys.exit(0)
            except OSError:
                pass  # stale pid, fall through and overwrite
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    def _cleanup():
        try:
            if pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except Exception:
            pass

    atexit.register(_cleanup)


_single_instance_or_exit()


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


def _resource_dir() -> Path:
    """Where bundled data files (skill/, assets/) live. NSBundle's
    resourcePath is correct in py2app builds; in source mode we just
    use the project directory."""
    if _is_frozen():
        try:
            from AppKit import NSBundle
            rp = NSBundle.mainBundle().resourcePath()
            if rp:
                return Path(rp)
        except Exception:
            pass
    return Path(__file__).resolve().parent


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
            template=True,
            quit_button=None,
        )
        self.client = YouTubeClient()
        self._last_output: Optional[Path] = None
        self._busy = False
        self._pending_release: Optional[dict] = None

        # Header + status (both disabled, just informative).
        self._header = rumps.MenuItem(
            f"YT-sub v{__version__} — YouTube metadata + transcripts"
        )
        self._status = rumps.MenuItem("…")

        # Top-level primary action.
        self._mi_process = rumps.MenuItem("Process URL…", callback=self.process_url)

        # Account submenu.
        self._mi_load_secret = rumps.MenuItem(
            "Load client_secret.json…", callback=self.load_client_secret
        )
        self._mi_signin = rumps.MenuItem("Sign in with Google", callback=self.sign_in)
        self._mi_signout = rumps.MenuItem("Sign out", callback=self.sign_out)
        self._mi_login_toggle = rumps.MenuItem(
            "Auto-start on login", callback=self.toggle_login_item
        )
        # Metadata source picker (top of Account so it's the first decision).
        self._mi_md_standard = rumps.MenuItem(
            "Standard (no setup)", callback=self.set_metadata_backend_standard
        )
        self._mi_md_advanced = rumps.MenuItem(
            "Advanced (YouTube API)", callback=self.set_metadata_backend_advanced
        )
        md_menu = rumps.MenuItem("Metadata source")
        md_menu.add(self._mi_md_standard)
        md_menu.add(self._mi_md_advanced)

        # Transcript fallback (Whisper) picker.
        self._mi_wh_off = rumps.MenuItem(
            "Off", callback=self.set_whisper_backend_off
        )
        self._mi_wh_groq = rumps.MenuItem(
            "Whisper (Groq)", callback=self.set_whisper_backend_groq
        )
        self._mi_groq_key = rumps.MenuItem(
            "Set Groq API key…", callback=self.set_groq_api_key_menu
        )
        wh_menu = rumps.MenuItem("Transcript fallback")
        wh_menu.add(self._mi_wh_off)
        wh_menu.add(self._mi_wh_groq)
        wh_menu.add(rumps.separator)
        wh_menu.add(self._mi_groq_key)

        account_menu = rumps.MenuItem("Account")
        account_menu.add(md_menu)
        account_menu.add(wh_menu)
        account_menu.add(rumps.separator)
        account_menu.add(self._mi_load_secret)
        account_menu.add(self._mi_signin)
        account_menu.add(self._mi_signout)
        account_menu.add(rumps.separator)
        account_menu.add(self._mi_login_toggle)

        # Cookies submenu (mostly preserved from before).
        self._cookies_menu = rumps.MenuItem("Cookies for yt-dlp")
        self._cookies_items = {}
        load_cookies = rumps.MenuItem(
            "Load cookies.txt…", callback=self.load_cookies_file
        )
        clear_cookies = rumps.MenuItem(
            "Clear cookies.txt", callback=self.clear_cookies_file
        )
        self._cookies_items["__load"] = load_cookies
        self._cookies_items["__clear"] = clear_cookies
        self._cookies_menu.add(load_cookies)
        self._cookies_menu.add(clear_cookies)
        self._cookies_menu.add(rumps.separator)
        for label in ("(disabled)",) + yt_config.SUPPORTED_BROWSERS:
            mi = rumps.MenuItem(label, callback=self.set_cookies_browser)
            self._cookies_items[label] = mi
            self._cookies_menu.add(mi)

        # Output submenu.
        self._mi_open_last = rumps.MenuItem("Open last result", callback=self.open_last)
        self._mi_open_output = rumps.MenuItem(
            "Open output folder", callback=self.open_output
        )
        self._mi_stats = rumps.MenuItem("Statistics", callback=self.show_stats)
        output_menu = rumps.MenuItem("Output")
        output_menu.add(self._mi_open_last)
        output_menu.add(self._mi_open_output)
        output_menu.add(self._mi_stats)

        # Agents submenu.
        agents_menu = rumps.MenuItem("Agents")
        agents_menu.add(rumps.MenuItem("Copy MCP config", callback=self.copy_mcp_config))
        agents_menu.add(rumps.MenuItem("Install skill (~/.claude)", callback=self.install_skill_global))
        agents_menu.add(rumps.MenuItem("Install skill in project…", callback=self.install_skill_in_project))
        agents_menu.add(rumps.MenuItem("Copy skill to clipboard", callback=self.copy_skill_to_clipboard))

        # About submenu.
        about_menu = rumps.MenuItem("About")
        about_menu.add(rumps.MenuItem("Check for updates…", callback=self.check_for_updates))
        about_menu.add(rumps.separator)
        about_menu.add(rumps.MenuItem("Open repository on GitHub", callback=self.open_repository))

        # Top-level: only sections + Process URL + Quit.
        self.menu = [
            self._header,
            self._status,
            None,
            self._mi_process,
            None,
            account_menu,
            self._cookies_menu,
            output_menu,
            agents_menu,
            about_menu,
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._refresh_menu()

        # Fire a silent update check 5s after startup. Throttled to
        # once per 6 hours via last_update_check_at in config.
        _t = threading.Timer(5.0, self._autocheck_updates_background)
        _t.daemon = True
        _t.start()

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
        self._mi_load_secret.state = 1 if has_secret else 0
        self._mi_signin.state = 1 if signed_in else 0
        self._mi_signin.set_callback(
            self.sign_in if (has_secret and not signed_in) else None
        )
        self._mi_signout.set_callback(self.sign_out if signed_in else None)
        self._mi_process.set_callback(
            self.process_url if (signed_in and not self._busy) else None
        )
        self._mi_open_last.set_callback(self.open_last if self._last_output else None)
        try:
            self._mi_login_toggle.state = 1 if LAUNCH_AGENT_PATH.exists() else 0
        except Exception:
            pass
        try:
            backend = yt_config.get_metadata_backend()
            self._mi_md_standard.state = 1 if backend == "standard" else 0
            self._mi_md_advanced.state = 1 if backend == "advanced" else 0
            wb = yt_config.get_whisper_backend()
            self._mi_wh_off.state = 1 if wb == "none" else 0
            self._mi_wh_groq.state = 1 if wb == "groq" else 0
            self._mi_groq_key.state = 1 if yt_config.get_groq_api_key() else 0
        except Exception:
            pass
        try:
            cookies_file_active = yt_config.get_cookies_file() is not None
            self._cookies_items["__load"].state = 1 if cookies_file_active else 0
            self._cookies_items["__clear"].set_callback(
                self.clear_cookies_file if cookies_file_active else None
            )
            current = yt_config.get_ytdlp_browser()
            for label, mi in self._cookies_items.items():
                if label.startswith("__"):
                    continue
                is_active = (
                    not cookies_file_active and (
                        (label == "(disabled)" and current is None) or label == current
                    )
                )
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
        src = _resource_dir() / "skill" / "SKILL.md"
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
        src = _resource_dir() / "skill" / "SKILL.md"
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

    def set_metadata_backend_standard(self, _) -> None:
        yt_config.set_metadata_backend("standard")
        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "Metadata source: Standard",
            "yt-dlp + oEmbed, no Google OAuth needed.",
        )

    def set_metadata_backend_advanced(self, _) -> None:
        yt_config.set_metadata_backend("advanced")
        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "Metadata source: Advanced",
            "YouTube Data API — sign in if you haven't yet.",
        )

    def set_whisper_backend_off(self, _) -> None:
        yt_config.set_whisper_backend(None)
        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "Transcript fallback: Off",
            "Videos without subtitles will return a transcript error.",
        )

    def set_whisper_backend_groq(self, _) -> None:
        yt_config.set_whisper_backend("groq")
        self._refresh_menu()
        if not yt_config.get_groq_api_key():
            rumps.alert(
                title="Groq API key not set",
                message=(
                    "Whisper fallback via Groq is selected, but no API key "
                    "is configured. Use 'Set Groq API key…' to add one. "
                    "Get a free key at https://console.groq.com/keys."
                ),
            )
        else:
            rumps.notification(
                "YT-sub",
                "Transcript fallback: Whisper (Groq)",
                "Videos without subtitles will be transcribed via Groq.",
            )

    def set_groq_api_key_menu(self, _) -> None:
        current = yt_config.get_groq_api_key() or ""
        masked_default = current  # plain text — rumps Window has no secure field
        w = rumps.Window(
            title="Groq API key",
            message=(
                "Paste your Groq API key (get one free at "
                "https://console.groq.com/keys). Leave empty to clear."
            ),
            default_text=masked_default,
            ok="Save",
            cancel="Cancel",
            dimensions=(360, 22),
        )
        resp = w.run()
        if not resp.clicked:
            return
        new_key = (resp.text or "").strip()
        yt_config.set_groq_api_key(new_key or None)
        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "Groq API key " + ("saved" if new_key else "cleared"),
            "",
        )

    def set_cookies_browser(self, sender) -> None:
        label = sender.title
        browser = None if label == "(disabled)" else label
        yt_config.set_ytdlp_browser(browser)
        self._refresh_menu()
        msg = f"yt-dlp will use cookies from {browser}" if browser else "yt-dlp cookies disabled"
        rumps.notification("YT-sub", "Cookie source updated", msg)

    def load_cookies_file(self, _) -> None:
        script = (
            'try\n'
            '  set f to choose file with prompt '
            '"Select cookies.txt (Netscape format, exported from your browser)" '
            'of type {"txt", "public.plain-text"}\n'
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
        if not src.exists() or not src.is_file():
            rumps.alert(title="File not found", message=str(src))
            return

        try:
            head = src.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception as e:
            rumps.alert(title="Cannot read file", message=str(e))
            return

        looks_like_cookies = (
            "# Netscape HTTP Cookie File" in head
            or ".youtube.com\t" in head
            or "youtube.com" in head.lower()
        )
        if not looks_like_cookies:
            rumps.alert(
                title="Doesn't look like a cookies.txt",
                message=(
                    "The file does not contain Netscape header or any "
                    "youtube.com lines. Will save anyway, but yt-dlp may "
                    "fail.\n\nExport tip: install \"Get cookies.txt LOCALLY\" "
                    "browser extension, open youtube.com, click the extension "
                    "→ Export."
                ),
            )

        yt_config.MANAGED_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, yt_config.MANAGED_COOKIES_FILE)
        yt_config.set_cookies_file(str(yt_config.MANAGED_COOKIES_FILE))
        self._refresh_menu()
        rumps.notification(
            "YT-sub",
            "cookies.txt loaded",
            "yt-dlp will now use this file (overrides browser cookies)",
        )

    def clear_cookies_file(self, _) -> None:
        yt_config.set_cookies_file(None)
        try:
            if yt_config.MANAGED_COOKIES_FILE.exists():
                yt_config.MANAGED_COOKIES_FILE.unlink()
        except Exception:
            pass
        self._refresh_menu()
        rumps.notification("YT-sub", "cookies.txt cleared", "")

    def open_repository(self, _) -> None:
        subprocess.run(["open", "https://github.com/BrezhnevEugen/yt-sub"])

    @staticmethod
    def _fetch_latest_release() -> dict:
        """Hit GitHub's latest-release endpoint. Raises on any failure;
        returns the parsed JSON dict on success. py2app's bundled Python
        has no link to the system trust store, so we route SSL through
        certifi's CA bundle (transitively bundled via requests/google-auth)."""
        import ssl
        import urllib.request
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        req = urllib.request.Request(
            "https://api.github.com/repos/BrezhnevEugen/yt-sub/releases/latest",
            headers={
                "User-Agent": f"YT-sub/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return json.load(resp)

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        def parts(s: str):
            return tuple(int(x) for x in s.split(".") if x.isdigit())
        return parts(latest) > parts(current)

    def check_for_updates(self, _) -> None:
        # Reuse the release dict cached by the background autocheck so
        # we don't re-hit the API for the same answer.
        data = getattr(self, "_pending_release", None)
        if not data:
            try:
                data = self._fetch_latest_release()
            except Exception as e:
                rumps.alert(title="Update check failed", message=str(e))
                return

        latest_raw = (data.get("tag_name") or "").lstrip("v").strip()
        if not latest_raw:
            rumps.alert(title="Update check failed", message="No releases found")
            return

        if not self._is_newer(latest_raw, __version__):
            self._pending_release = None
            rumps.alert(
                title="You're up to date",
                message=f"v{__version__} is the latest version.",
            )
            return

        release_url = data.get("html_url") or "https://github.com/BrezhnevEugen/yt-sub/releases"
        # rumps.alert returns: 1 = ok, 0 = cancel, -1 = other.
        response = rumps.alert(
            title=f"Update available: v{latest_raw}",
            message=(
                f"You have v{__version__}.\n\n"
                "Install update — download the DMG, swap the app, and relaunch automatically.\n"
                "Open release page — read changelog / download manually."
            ),
            ok="Install update",
            cancel="Later",
            other="Open release page",
        )
        if response == 1:
            threading.Thread(
                target=self._do_self_update, args=(data,), daemon=True
            ).start()
        elif response == -1:
            subprocess.run(["open", release_url])

    def _do_self_update(self, release: dict) -> None:
        """Daemon-thread: download, swap, relaunch. UI calls (alert,
        Window) are NOT safe off the main thread — only use
        rumps.notification() here."""
        import os as _os
        import signal as _signal
        import time as _time

        from updater import UpdateError, install_update

        tag = (release.get("tag_name") or "").lstrip("v") or "?"
        try:
            self.title = "↓ Updating…"
            rumps.notification(
                "YT-sub", "Downloading update…", f"v{tag}"
            )
            install_update(release)
            rumps.notification(
                "YT-sub", "Update ready", "Restarting YT-sub…"
            )
            # The relauncher is now waiting on our PID. Give the
            # notification a moment to surface, then exit cleanly so
            # rumps shuts down its run loop and the relauncher can
            # swap the bundle.
            _time.sleep(1.0)
            _os.kill(_os.getpid(), _signal.SIGTERM)
        except UpdateError as e:
            self.title = None
            rumps.notification("YT-sub", "Update failed", str(e)[:200])
        except Exception as e:
            self.title = None
            rumps.notification(
                "YT-sub", "Update failed", f"Unexpected: {str(e)[:180]}"
            )

    def _autocheck_updates_background(self) -> None:
        """Silent update check on startup. Throttled to once per 6h via
        last_update_check_at in config. Shows a system notification only
        when a newer release exists; never alerts; swallows network
        errors so offline starts stay clean."""
        import time

        cfg = yt_config.load()
        last = float(cfg.get("last_update_check_at") or 0)
        if time.time() - last < 6 * 3600:
            return

        try:
            data = self._fetch_latest_release()
        except Exception:
            return  # offline / rate-limited / GitHub down — try again later

        cfg = yt_config.load()
        cfg["last_update_check_at"] = time.time()
        yt_config.save(cfg)

        latest_raw = (data.get("tag_name") or "").lstrip("v").strip()
        if latest_raw and self._is_newer(latest_raw, __version__):
            # Cache the release dict so the next manual "Check for
            # updates…" can offer Install immediately without re-hitting
            # the API.
            self._pending_release = data
            try:
                rumps.notification(
                    "YT-sub update available",
                    f"v{latest_raw} (you have v{__version__})",
                    "Open About → Check for updates… to install.",
                )
            except Exception:
                pass

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

            backend = yt_config.get_metadata_backend()
            try:
                if backend == "advanced":
                    if not self.client.is_authenticated():
                        rumps.notification(
                            "YT-sub",
                            "Sign in required",
                            "Advanced backend needs Google OAuth — switch to Standard or sign in.",
                        )
                        return
                    metadata = self.client.fetch_metadata(video_id)
                else:
                    from web_metadata import fetch_metadata_web
                    metadata = fetch_metadata_web(video_id)
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
