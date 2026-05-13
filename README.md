# YT-sub

A small macOS menu-bar utility that pulls **metadata + transcript** for any YouTube video, and exposes the same capability to AI agents over **MCP** so they can summarize, translate or quote videos on demand.

```
▶  YouTube URL  →  ~/YT-sub/output/<videoId>/
                      ├── metadata.json   (snippet · contentDetails · stats)
                      ├── transcript.json (timed segments)
                      └── transcript.txt  (plain text)
```

Releases live on [GitHub Releases](https://github.com/BrezhnevEugen/yt-sub/releases). Per-version notes are in [CHANGELOG.md](./CHANGELOG.md).

## Components

- **Tray app** (`app.py`) — runs as a menu-bar icon (no Dock), provides URL input, Google OAuth, statistics, agent integration helpers. The menu-bar icon is a system-style template silhouette, auto-tinted for dark/light bar.
- **MCP server** (`mcp_server.py`) — exposes 9 tools to any MCP host (Claude Code, Claude Desktop, Cursor, Cline, Windsurf, Continue…):
  - **Video pipeline** — `process_video(url_or_id, include_segments=False)`, `list_processed_videos()`, `get_processed_video(video_id, include_segments=False)`.
  - **Cookie controls** — `set_cookies_browser(browser)` / `get_cookies_browser()`, `set_cookies_file(path)` / `get_cookies_file()`. Used by agents to recover from YouTube IP-block / bot-protection without bouncing back to the user.
  - **Diagnostics** — `get_stats()`, `get_version()`.
- **Skill** (`skill/SKILL.md`) — Claude Code skill with triggers (RU/EN keywords + every YouTube URL shape) so agents auto-invoke the tools when relevant. Also documents the bot-protect → `set_cookies_*` recovery flow.

## Why two transcript backends

`transcript.py` tries `youtube-transcript-api` first (fast, pure HTTP). YouTube blocks that endpoint aggressively, so on any failure it falls back to `yt-dlp`, which uses the same player API the browser hits and is rarely blocked. Result: works on residential IPs without proxies.

## Install

### Option A: download the DMG (recommended)

A signed and notarized DMG is published on GitHub Releases — no Python or developer tools required.

1. Grab `YT-sub-x.y.z.dmg` from the [latest release](https://github.com/BrezhnevEugen/yt-sub/releases).
2. Open the DMG and drag **YT-sub.app** into **Applications**.
3. Launch it from Spotlight or `/Applications`. Look for the ▶ silhouette in the menu bar — it's a template image, so it shows dark on a light bar and white on a dark bar.

The bundle ships its own Python interpreter and all dependencies (~219 MB unpacked, ~89 MB compressed). Gatekeeper accepts it without prompts because it's notarized.

After install, the app silently checks for updates ~5s after each launch (throttled to once per 6 hours, hits the GitHub Releases API). If a newer version is published you get a system notification; otherwise nothing — never an alert, never blocks startup. Manual check is in **About → Check for updates…**.

### Option B: install from source

Requires macOS 11+, Python 3.10+, and a Google Cloud OAuth client.

```bash
git clone https://github.com/BrezhnevEugen/yt-sub.git
cd yt-sub
./install.sh --login        # install + auto-start on every login
```

`install.sh` does everything:

- creates `.venv` and installs dependencies
- renders the template menu-bar PNG and bundles the iconset (designer-provided) into a multi-resolution `.icns`
- builds and code-signs `YT-sub.app` (auto-detects your `Developer ID Application:` identity, falls back to ad-hoc if none)
- copies it to `/Applications`
- registers a per-user **LaunchAgent** at `~/Library/LaunchAgents/com.brezhnev.yt-sub.plist` — that's the canonical launcher (handles auto-restart on crash and, with `--login`, auto-start on login)
- kicks off the tray immediately
- a single-instance guard at startup writes `~/.config/yt-sub/yt-sub.pid` and refuses to start twice; cleaned via `atexit`

Other forms:

```bash
./install.sh                      # install, manual start only
./install.sh --login --notarize   # full Apple notarization (no Gatekeeper prompt anywhere)
./install.sh --uninstall          # stop + remove app + LaunchAgent
./install.sh --help               # all flags
```

### Optional: full notarization

If you have an Apple Developer ID and want the `.app` to pass Gatekeeper without any user prompt (useful when distributing the bundle), set up a notarytool keychain profile **once**:

```bash
xcrun notarytool store-credentials yt-sub-notarize \
    --apple-id     your-apple-id@example.com \
    --team-id      <YOUR_TEAM_ID> \
    --password     <app-specific-password>
```

Generate the app-specific password at [appleid.apple.com](https://appleid.apple.com/) → Sign-In and Security → App-Specific Passwords. Find your team ID in the Developer ID identity name (the parenthesised code from `security find-identity -v -p codesigning`).

Then run `./install.sh --login --notarize` — Apple scans + signs the bundle, the installer staples the ticket so it works offline.

In Google Cloud Console:

1. Enable **YouTube Data API v3** (APIs & Services → Library).
2. Create credentials → **OAuth client ID** → application type **Desktop app** → download JSON.
3. While the OAuth consent screen is in **Testing**, add your Google email under **Test users** (otherwise sign-in returns `403: access_denied`).

In the tray menu (▶ icon):

1. **Load client_secret.json…** — pick the file you just downloaded; it gets copied to `~/.config/yt-sub/client_secret.json`.
2. **Sign in with Google** — runs the OAuth loopback flow in the browser; token cached at `~/.config/yt-sub/token.json`.
3. **Process URL…** — paste any YouTube URL; result lands in `~/YT-sub/output/<videoId>/` and you get a notification.

## Wiring up agents

The tray menu has one-click installers:

- **Copy MCP config** — JSON for any MCP host. Paste into:
  - Claude Desktop → `~/Library/Application Support/Claude/claude_desktop_config.json`
  - Claude Code → `claude mcp add-json yt-sub '<paste>'` (or merge into `~/.claude.json` / project `.mcp.json`)
  - Cursor / Cline / Windsurf / Continue → their respective MCP configs
- **Install skill (~/.claude)** — drops `SKILL.md` into `~/.claude/skills/yt-sub/` (Claude Code, user-global).
- **Install skill in project…** — folder picker. Writes the skill in three formats so any agent in that project picks it up:
  - `.claude/skills/yt-sub/SKILL.md` (Claude Code project skills)
  - `.cursor/rules/yt-sub.mdc` (Cursor project rules)
  - appends a `## yt-sub …` section to `AGENTS.md` (universal — read by Aider and a growing set of agents)
- **Copy skill to clipboard** — paste anywhere a system prompt or rules file is accepted (Claude Desktop project instructions, custom GPTs, OpenAI Assistants, etc.).

After wiring, restart your agent host once. Then the agent will call `process_video` automatically whenever the user mentions a YouTube URL or asks for a transcript.

## Quickstart: cookies.txt for transcripts

**TL;DR for the impatient:**

1. Open Chrome (or any Chromium browser), install [«Get cookies.txt LOCALLY»](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).
2. Open `https://www.youtube.com` and **make sure you are signed in**.
3. Click the extension icon → **Export** → `youtube.com_cookies.txt` lands in `~/Downloads/`.
4. In the YT-sub tray menu (▶ icon): **Cookies for yt-dlp → Load cookies.txt…** → pick the file.

After this, transcripts work for any YouTube video. Re-export when YouTube starts rejecting again (usually weeks later). Full background and edge cases below.

### Optional: install Deno for full YouTube extractor support

Since yt-dlp 2025.11.12, YouTube requires an external JavaScript runtime for the n-parameter signature solving used on newer videos. Without it, some videos silently come back with missing audio formats or "signature extraction failed" errors. **Most videos still work** because yt-dlp falls back to other clients, but the failures are unpredictable.

If you hit them, install Deno once:

```sh
brew install deno
```

YT-sub itself doesn't bundle Deno (would double the DMG size for a feature most users never need). The bundled yt-dlp picks it up from `$PATH` automatically — no YT-sub setting to change.

## Bypassing YouTube's bot-protection (cookies)

Sometimes both transcript backends hit YouTube's IP-based blocking ("Sign in to confirm you're not a bot" / "YouTube is blocking requests from your IP"). This happens to residential IPs that look suspicious to Google — too many requests, datacenter IP ranges, or just bad luck. Pass real browser cookies from a logged-in YouTube session and YouTube treats the request as authenticated, which clears the block.

### Two options in the tray menu under **Cookies for yt-dlp**

**Option A — Browser cookies (`chrome` / `safari` / `firefox` / …)** is the zero-setup option, but it's flaky on macOS:

- **Safari** sits in a TCC-sandboxed container; without granting Full Disk Access to YT-sub.app, reading `~/Library/Containers/com.apple.Safari/.../Cookies.binarycookies` returns `Operation not permitted`.
- **Chrome 130+** ships App-Bound Encryption — yt-dlp can read the SQLite DB but can't decrypt the cookie values, so YouTube still rejects the request as unauthenticated. Brave / Edge / Arc / Chromium are all affected.
- **Firefox** is the only Chromium-free option that mostly works out of the box, if you have it installed and logged in.

**Option B — Manual `cookies.txt` (recommended)** sidesteps all three issues. Any browser exports a Netscape-format text file that yt-dlp consumes directly.

### How to export cookies.txt

**Chrome / Edge / Brave / Arc / any Chromium browser:**

1. Install the [**Get cookies.txt LOCALLY**](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension from the Chrome Web Store.
2. Open `https://www.youtube.com` and **make sure you are signed in**. The export only contains auth cookies if there's an active session.
3. Click the extensions icon (puzzle piece, top-right of Chrome) → **Get cookies.txt LOCALLY** → **Export**. The file downloads to `~/Downloads/youtube.com_cookies.txt`.
4. In the YT-sub tray menu (▶ icon): **Cookies for yt-dlp → Load cookies.txt…** and pick the downloaded file.

The file gets copied to `~/.config/yt-sub/cookies.txt`; the original in Downloads can be deleted. The checkmark on **Load cookies.txt…** indicates it's active. **Clear cookies.txt** removes it and falls back to whatever browser source is selected (or anonymous).

**Firefox:** install the [**cookies.txt** add-on by Lennon Hill](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/), same flow.

**Safari:** there is no first-class extension. The realistic options are (a) install Chrome just for the export, (b) use Develop → Show Web Inspector → Storage → Cookies → manually copy youtube.com cookies and convert to Netscape format (tedious), or (c) grant Full Disk Access to YT-sub.app and use the Safari browser-cookies setting.

### Lifecycle

A cookies.txt is valid as long as the YouTube session it came from is alive. Typical lifetime is weeks. When transcripts start failing with bot-protect again, log in to YouTube in the browser again (or just refresh the page) and re-export. The tray's **Load cookies.txt…** overwrites the old file in place.

### Tip: separate "donor" account

Cookies authenticate as a real Google account; YouTube has been known to ban accounts seen scraping. If you process a lot of videos, log a throwaway Google account into a private/incognito window of the browser, navigate to YouTube, and export cookies from there — your main account stays clean. This is the same caveat the `youtube-transcript-api` error message warns about.

### Same controls for agents

Agents call the MCP server. The relevant tools are:

- `set_cookies_browser(browser)` — `"chrome" | "safari" | …` or null to disable.
- `set_cookies_file(path)` — absolute path to a cookies.txt; the file is copied into `~/.config/yt-sub/cookies.txt`.
- `get_cookies_browser()` / `get_cookies_file()` — read current state.

A configured cookies file overrides the browser source.

## Tray menu structure

```
YT-sub vX.Y.Z — YouTube metadata + transcripts        (header, disabled)
Signed in · N videos · Mh Ks                          (status, disabled)
─
Process URL…
─
Account ▸    Load client_secret.json…  ✓
             Sign in with Google       ✓
             Sign out
             ─
             Auto-start on login       (toggle, ✓ when enabled)
Cookies for yt-dlp ▸  Load cookies.txt…  Clear cookies.txt
                      ─
                      (disabled) / chrome / safari / firefox / brave / edge / chromium / arc
Output ▸     Open last result · Open output folder · Statistics
Agents ▸     Copy MCP config · Install skill (~/.claude) · Install skill in project… · Copy skill to clipboard
About ▸      Check for updates…
             ─
             Open repository on GitHub
─
Quit
```

Checkmarks in the *Account* and *Cookies* submenus reflect actual on-disk / in-memory state, so you can tell at a glance whether the OAuth client is loaded, whether you're signed in, and which cookies source is active.

## Statistics

The tray menu has a **Statistics** entry showing:

- Videos processed · with transcript · unique channels
- Total video duration (sum of `contentDetails.duration`)
- Transcript word/char totals
- Last processed video

The same is exposed to agents via the `get_stats` MCP tool.

## File layout

```
~/.config/yt-sub/
  client_secret.json   # your Google OAuth client (gitignored at source)
  token.json           # cached refresh token (gitignored at source)

~/YT-sub/output/<videoId>/
  metadata.json        # full YouTube Data API response
  transcript.json      # [{text, start, duration}, …]
  transcript.txt       # plain text, no timing
  transcript.error.txt # only if both backends failed
```

## Roadmap

- [ ] WebShare proxy support in `transcript.py` (option to keep `youtube-transcript-api` working when `yt-dlp` becomes the slow path).
- [ ] Optional Whisper-based fallback for videos with no captions at all.
- [ ] cross-platform packaging (the tray code is macOS-only via `rumps`/PyObjC).
- [ ] **Control Center widget on macOS Tahoe** (Swift `ControlWidget` app extension). The Tahoe `WidgetKit` Control API is Swift-only and lives as an `.appex` plugin with App Group / URL-scheme IPC to the Python tray. ~4–6 h of work; deferred until benefit > friction.

## License

MIT.
