# YT-sub

A small macOS menu-bar utility that pulls **metadata + transcript** for any YouTube video, and exposes the same capability to AI agents over **MCP** so they can summarize, translate or quote videos on demand.

```
🎬  YouTube URL  →  ~/YT-sub/output/<videoId>/
                       ├── metadata.json   (snippet · contentDetails · stats)
                       ├── transcript.json (timed segments)
                       └── transcript.txt  (plain text)
```

## Components

- **Tray app** (`app.py`) — runs as a menu-bar icon (no Dock), provides URL input, Google OAuth, statistics, agent integration helpers.
- **MCP server** (`mcp_server.py`) — exposes 4 tools to any MCP host (Claude Code, Claude Desktop, Cursor, Cline, Windsurf, Continue…):
  - `process_video(url_or_id, include_segments=False)` — fetch + cache + return summary
  - `list_processed_videos()` — list cached videos
  - `get_processed_video(video_id, include_segments=False)` — read from cache, no network
  - `get_stats()` — aggregate counters over the cache
- **Skill** (`skill/SKILL.md`) — Claude Code skill with triggers (RU/EN keywords + every YouTube URL shape) so agents auto-invoke the tools when relevant.

## Why two transcript backends

`transcript.py` tries `youtube-transcript-api` first (fast, pure HTTP). YouTube blocks that endpoint aggressively, so on any failure it falls back to `yt-dlp`, which uses the same player API the browser hits and is rarely blocked. Result: works on residential IPs without proxies.

## Setup

Requires macOS, Python 3.10+, and a Google Cloud OAuth client.

```bash
git clone <this repo>
cd YT-sub
./run.sh                # creates .venv, installs deps, launches the tray
```

In Google Cloud Console:

1. Enable **YouTube Data API v3** (APIs & Services → Library).
2. Create credentials → **OAuth client ID** → application type **Desktop app** → download JSON.
3. While the OAuth consent screen is in **Testing**, add your Google email under **Test users** (otherwise sign-in returns `403: access_denied`).

In the tray menu (🎬):

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

## License

MIT.
