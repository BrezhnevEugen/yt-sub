---
name: yt-sub
description: Fetch YouTube video metadata and transcript (subtitles) via the yt-sub MCP server. Use when the user provides a YouTube URL or 11-char video id and wants to summarize, transcribe, translate, quote, analyze, or otherwise work with the video's content. Triggers on YouTube URLs (youtube.com/watch, youtu.be, youtube.com/shorts, youtube.com/embed) and on words like "транскрипция", "субтитры", "captions", "transcript", "обработай ролик", "о чём это видео".
---

# yt-sub

Skill for working with the `yt-sub` MCP server, which exposes three tools for fetching YouTube data. The user has already authorized Google OAuth via the YT-sub tray app; this skill assumes the same credentials are reused.

## When to use

Trigger on:
- A YouTube URL: `youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/shorts/…`, `youtube.com/embed/…`, `m.youtube.com/…`, `music.youtube.com/…`
- A bare 11-character video id
- User intent like: get transcript / subtitles / captions, summarize a video, "что в ролике", "транскрипция", "о чём говорят", quote/extract from a video, fetch video metadata

## Tools

The `yt-sub` MCP server provides:

- **`process_video(url_or_id, include_segments=False)`** — primary action. Fetches metadata via YouTube Data API v3 and transcript via youtube-transcript-api, saves to `~/YT-sub/output/<videoId>/`, and returns metadata fields, `transcript_text` (plain), `output_dir`, and `transcript_error` if subtitles were unavailable. Pass `include_segments=True` to also return timed segments (`[{text, start, duration}, …]`).
- **`list_processed_videos()`** — list previously cached videos (newest first).
- **`get_processed_video(video_id, include_segments=False)`** — re-read a cached video from disk without hitting YouTube again.
- **`get_stats()`** — aggregate counters over the cache: number of videos, unique channels, total video duration, transcript word/char totals, last processed video. Use when the user asks "сколько роликов я обработал", "статистика", "how many videos", etc.
- **`get_cookies_browser()`** — read which browser yt-dlp uses for cookies (or null if disabled).
- **`set_cookies_browser(browser)`** — point yt-dlp at a browser's cookie jar (`"chrome" | "safari" | "firefox" | "brave" | "edge" | "chromium" | "arc"`), or pass null/empty to disable. Persists in `~/.config/yt-sub/config.json`.
- **`set_cookies_file(path)`** / **`get_cookies_file()`** — point yt-dlp at a Netscape-format `cookies.txt` (the file is copied into `~/.config/yt-sub/cookies.txt` so the original can move). Use this when browser-cookies fail: Safari is TCC-sandboxed on macOS, Chrome 130+ uses App-Bound Encryption that yt-dlp cannot decrypt, Firefox may not be installed. Pass null/empty to clear. **A configured cookies file overrides the browser cookies setting.**

## Workflow

1. Extract URL or video id from the user's message.
2. If the video has likely been processed before, prefer `list_processed_videos` then `get_processed_video` to save a quota hit. Otherwise call `process_video`.
3. If the response is `{"error": "not_signed_in", …}`, tell the user to open the YT-sub tray app (🎬 icon in the macOS menu bar) and choose **Sign in with Google**, then stop. Do not retry until they confirm.
4. If the response is `{"error": "invalid_url", …}`, ask the user for a valid YouTube URL or 11-char video id.
5. If `transcript_error` is set, subtitles are not available — say so explicitly and proceed with metadata only.
6. Use the `transcript_text` for whatever the user asked (summarize, translate, extract quotes, find timestamps, etc.).

### When transcripts fail with bot-protection

`transcript_error` mentioning *"YouTube is blocking requests from your IP"*, *"Sign in to confirm you're not a bot"*, or *"Operation not permitted"* (Safari TCC) means YouTube refused the un-authenticated request. Recovery flow:

1. Call `get_cookies_file()` and `get_cookies_browser()` to see the current state.
2. If neither is set, ask the user to either pick a cookies.txt via the tray (**Cookies for yt-dlp → Load cookies.txt…**) or call `set_cookies_browser("firefox")` if they have Firefox logged in. Point them at the README section *"Bypassing YouTube's bot-protection (cookies)"* for the export how-to: <https://github.com/BrezhnevEugen/yt-sub#bypassing-youtubes-bot-protection-cookies>.
3. After the user reports they've loaded cookies (or you've called `set_cookies_file(path)` with a path they handed you), retry `process_video`. The cookies.txt path **overrides** browser source when both are configured.
4. If it still fails, the cookies.txt is likely from a logged-out session — ask them to verify they're signed in to YouTube in the browser before re-exporting.

Do **not** repeatedly retry `process_video` without changing state — every attempt counts toward the IP-block budget.

## Output dir layout

`~/YT-sub/output/<videoId>/` contains:
- `metadata.json` — full YouTube Data API response (snippet/contentDetails/statistics/status/topicDetails)
- `transcript.json` — timed segments (only if subtitles were available)
- `transcript.txt` — plain text (only if subtitles were available)
- `transcript.error.txt` — present only if the subtitles fetch failed (with the reason)

For follow-up questions about the same video in later turns, call `get_processed_video(video_id)` instead of `process_video` — it reads from disk and skips both API calls.

## Quoting / timestamps

If the user asks for quotes or timestamps, request `include_segments=True`. Each segment has `start` (seconds) and `duration`. Convert to `mm:ss` or `hh:mm:ss` for display, and link as `https://youtu.be/<videoId>?t=<int(start)>s`.
