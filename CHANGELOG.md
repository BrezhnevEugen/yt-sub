# Changelog

All notable changes to YT-sub. Format roughly follows [Keep a Changelog](https://keepachangelog.com/), versioned per [SemVer](https://semver.org/) (we're 0.x — anything can break between minor bumps until 1.0).

## [Unreleased]

## [0.1.12] — 2026-05-02

### Added
- **Auto-check for updates on startup.** A daemon thread fires 5s after launch, hits the GitHub Releases API (via the `certifi` CA bundle so the frozen Python's missing trust store doesn't bite), and posts a system notification only if a newer version is published. Throttled to once per 6 hours via `last_update_check_at` in `~/.config/yt-sub/config.json`. Silent on offline / rate-limited / GitHub-down — never blocks the UI, never alerts.
- **CHANGELOG.md** (this file).

### Changed
- The manual *About → Check for updates…* and the new background check share `_fetch_latest_release()` and `_is_newer()` helpers, so the SSL-context plumbing lives in one place.

## [0.1.11] — 2026-05-02

### Changed
- **Monochrome system-style template icon for the menu bar.** Filled rounded rect with a play-triangle-shaped hole, drawn at exact pixel sizes (22×22 + 44×44) via direct `NSBitmapImageRep` allocation so Retina doesn't double the backing store. macOS template-tints it for dark/light bar — light bar shows dark, dark bar shows white, like Wi-Fi / Battery / Sound. `rumps.App` now passes `template=True`.

## [0.1.10] — 2026-05-02

### Changed
- Initial template-icon experiment — outline-only silhouette. Superseded the same day by 0.1.11's filled system-style version.

## [0.1.9] — 2026-05-02

### Added
- **Designer-provided icon set replaces the placeholder.** Vector source (`yt_icon.svg`, `yt_icon_menu.svg`), full Apple iconset (16/32/128/256/512 + @2x), Big Sur+ squircle canvas with ~10 % padding, soft outer drop shadow, gradient body `#da372b → #b3231a` (sidesteps literal YouTube `#FF0000`), subtitle bars + speech-bubble tail signaling transcript extraction.

### Changed
- `icon.py ensure_icns()` no longer wipes the iconset directory at each call. Only renders missing sizes (designer-provided files stay untouched), and rebuilds the `.icns` when any iconset PNG is newer than the cached `.icns`.
- `setup.py DATA_FILES` bundles `yt_icon@2x.png` so AppKit serves the retina menu-bar variant.

### Removed
- 5 MB `assets/Image.png` design mockup (kept out of the repo via `.gitignore`).

## [0.1.8] — 2026-05-02

### Fixed
- **`CERTIFICATE_VERIFY_FAILED` on update check inside the DMG bundle.** py2app's frozen Python has no link to the macOS system trust store. Builds the SSL context with `cafile=certifi.where()` instead. `setup.py` lists `certifi` explicitly so modulegraph can't miss it.

## [0.1.7] — 2026-05-02

### Added
- **About submenu** with *Check for updates…* and *Open repository on GitHub*.
- **Single-instance guard.** Writes `~/.config/yt-sub/yt-sub.pid` on startup, checks `os.kill(pid, 0)` plus `ps` command-line on subsequent starts; aborts with a stderr message if another instance is already running. Cleaned via `atexit`. Skipped for `--mcp` / `--version` modes.

### Fixed
- **Menu-bar icon disappeared in DMG installs.** `Path(__file__).parent` for `icon.py` resolves inside `python312.zip` in a py2app bundle, so the asset path was wrong. `_assets_dir()` now uses `NSBundle.mainBundle().resourcePath()` when frozen, falls back to the project dir in source mode.
- Audited *Copy skill to clipboard* — bundled `SKILL.md` matches source byte-for-byte. Hardened the lookup to use `_resource_dir()` regardless of how py2app rearranges the entry script.

## [0.1.6] — 2026-05-02

### Changed
- **Tidy tray menu.** Top level now: header, status, *Process URL…*, then `Account ▸ / Cookies for yt-dlp ▸ / Output ▸ / Agents ▸`, then `Quit`. Was 14 flat items. `_refresh_menu` now mutates items via stored `MenuItem` references because dict lookup `self.menu["title"]` only walks the top level.

## [0.1.5] — 2026-05-01

### Fixed
- **Long-tail YouTube transcript fetch.** Two-pass `yt-dlp`: a metadata-only `extract_info` first to discover available subtitle languages, then a second call asking for exactly one. Avoids the case where passing `('ru', 'en')` made yt-dlp fetch both, and a single 429 from the timedtext endpoint poisoned the whole download.
- `_PREFERRED_LANGS` reordered to `('ru-orig', 'ru', 'en-orig', 'en')` — YouTube serves the original auto-caption as `<lang>-orig` and a translated variant as the bare code; picking `-orig` first avoids the round-trip through Google's translate pipeline that triggers extra rate-limit pressure.
- `ignore_no_formats_error=True` / `allow_unplayable_formats=True` so yt-dlp does not abort with *Requested format is not available* when video format selection fails for region-restricted videos. We never download video bytes here.
- `retries=10` plus exponential backoff `retry_sleep_functions` for HTTP/fragment paths; Safari 17 User-Agent so the timedtext request looks like the real player.

## [0.1.4] — 2026-05-01

### Changed
- **Visible state in the menu** — `Load client_secret.json…` shows a checkmark when the file exists, `Sign in with Google` shows a checkmark when authenticated. Eliminates the constant "do I have to re-load it?" question.
- README gains a one-screen *Quickstart: cookies.txt for transcripts* block above the longer bot-protection section.

## [0.1.3] — 2026-05-01

### Added
- **Manual `cookies.txt` support** to bypass browser-cookie limitations on macOS (Safari TCC sandbox, Chrome 130+ App-Bound Encryption, missing Firefox). Tray submenu *Cookies for yt-dlp* gains *Load cookies.txt…* (native file picker, validates Netscape-format, copies into `~/.config/yt-sub/cookies.txt`) and *Clear cookies.txt*. The cookies file overrides the browser source when both are configured. MCP tools `set_cookies_file(path)` / `get_cookies_file()` for agents.

## [0.1.2] — 2026-05-01

### Added
- **Cookies from browser for yt-dlp.** New tray submenu *yt-dlp cookies from…* with `chrome / safari / firefox / brave / edge / chromium / arc / (disabled)`, persisted in `~/.config/yt-sub/config.json`, shared between the tray and the MCP server. MCP tools `set_cookies_browser(browser)` / `get_cookies_browser()`. Sidesteps *"Sign in to confirm you're not a bot"* errors when YouTube IP-blocks the anonymous transcript endpoints.
- **Version everywhere.** `version.py` is the single source of truth (read by `setup.py`, `release.sh`, `app.py`, `mcp_server.py`). `--version` / `-V` flag prints to stdout and exits. Tray header now shows `YT-sub vX.Y.Z — …`. Statistics dialog title is versioned. New MCP tool `get_version()`; `get_stats()` includes a `version` key.

## [0.1.1] — 2026-05-01

### Changed
- **Single binary, two entry points.** `app.py` dispatches to `mcp.run()` when `--mcp` is in argv, before any GUI imports. The DMG-bundled MCP config now points at `Contents/MacOS/YT-sub --mcp` (was generating broken paths to `.venv/bin/python` and `mcp_server.py` inside the bundle).
- `mcp_server` no longer caches the `YouTubeClient` at module level. A `_client()` factory re-reads `~/.config/yt-sub/token.json` on every call, so signing in via the tray after the MCP subprocess started actually works.

### Added
- **Auto-start on login** toggle in the tray menu — writes/removes `~/Library/LaunchAgents/com.brezhnev.yt-sub.plist` via `launchctl`, pointing at the bundle launcher (DMG install) or the source venv (source install). Menu item shows a checkmark when active.

### Fixed
- `install.sh --uninstall` now removes `/Applications/YT-sub.app` whether it's a symlink (source install) or a directory (DMG copy).

## [0.1.0] — 2026-05-01

### Added

Initial release. Self-contained macOS tray utility for YouTube metadata and transcripts, plus an MCP server so AI agents (Claude Code, Claude Desktop, Cursor, Cline, Windsurf, Continue, …) can call into it.

- **Tray app**: menu-bar interface, no Dock icon (`LSUIElement=true`), built on `rumps`/`PyObjC`.
- **YouTube Data API v3** via Google OAuth: full metadata (title / description / duration / stats / tags / topics).
- **Transcript fetch**: `youtube-transcript-api` first, `yt-dlp` fallback when YouTube IP-blocks the anonymous endpoints. Subtitles extracted as JSON3 timed segments and a plain-text variant.
- **Two-pass output**: `~/YT-sub/output/<videoId>/{metadata,transcript}.json` + `transcript.txt`.
- **Statistics dialog** — videos cached, unique channels, total video duration, transcript word/char totals.
- **MCP server** with four tools: `process_video`, `list_processed_videos`, `get_processed_video`, `get_stats`.
- **Claude Code skill** (`SKILL.md`) with RU/EN trigger keywords and YouTube-URL patterns so the agent calls `process_video` automatically.
- **Wiring helpers** in the tray: *Copy MCP config*, *Install skill (~/.claude)*, *Install skill in project…* (writes both `.claude/skills/yt-sub/SKILL.md` and `.cursor/rules/yt-sub.mdc`, appends to `AGENTS.md`), *Copy skill to clipboard*.
- **`install.sh`** — one-command source setup: creates venv, generates icons, builds and signs `YT-sub.app`, copies into `/Applications`, registers a per-user LaunchAgent, kicks off the tray. `--login` enables auto-start, `--notarize` submits to Apple's notary service, `--uninstall` reverses everything.
- **`release.sh`** — full DMG release pipeline: py2app build, sign every Mach-O leaf with the Developer ID Application identity, notarize the bundle, build/sign/notarize the DMG, staple, optionally `gh release create`.

[Unreleased]: https://github.com/BrezhnevEugen/yt-sub/compare/v0.1.12...HEAD
[0.1.12]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.12
[0.1.11]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.11
[0.1.10]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.10
[0.1.9]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.9
[0.1.8]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.8
[0.1.7]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.7
[0.1.6]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.6
[0.1.5]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.5
[0.1.4]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.4
[0.1.3]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.3
[0.1.2]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.2
[0.1.1]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.1
[0.1.0]: https://github.com/BrezhnevEugen/yt-sub/releases/tag/v0.1.0
