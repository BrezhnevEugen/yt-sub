---
name: yt-sub
description: Fetch YouTube video metadata and transcript (subtitles) via the yt-sub MCP server. Use when the user provides a YouTube URL or 11-char video id and wants to summarize, transcribe, translate, quote, analyze, or otherwise work with the video's content. Triggers on YouTube URLs (youtube.com/watch, youtu.be, youtube.com/shorts, youtube.com/embed) and on words like "транскрипция", "субтитры", "captions", "transcript", "обработай ролик", "о чём это видео".
---

# yt-sub

Skill for working with the `yt-sub` MCP server, which fetches YouTube metadata + transcripts and caches them under `~/YT-sub/output/<videoId>/`.

## When to use

Trigger on:
- A YouTube URL: `youtube.com/watch?v=…`, `youtu.be/…`, `youtube.com/shorts/…`, `youtube.com/embed/…`, `m.youtube.com/…`, `music.youtube.com/…`
- A bare 11-character video id
- User intent like: get transcript / subtitles / captions, summarize a video, "что в ролике", "транскрипция", "о чём говорят", quote/extract from a video, fetch video metadata

## Tools

- **`process_video(url_or_id, include_segments=False)`** — primary action. Fetches metadata + transcript, saves to `~/YT-sub/output/<videoId>/`, returns metadata fields, `transcript_text`, `output_dir`, `transcript_error`. Pass `include_segments=True` for timed segments (`[{text, start, duration}, …]`) — needed when you plan to cite timestamps.
- **`process_playlist(playlist, limit=50, skip_cached=True)`** — batch-process a playlist URL, a watch URL with `&list=...`, or a comma/newline-separated list of video URLs. Already-cached videos are skipped by default. Returns `{processed, skipped_cached, failed}` buckets — use this for "обработай этот плейлист", "summarize this series", channel deep-dives, etc., instead of looping `process_video` yourself.
- **`get_channel_info(channel, limit=10)`** — resolve a channel (handle like `@mkbhd`, bare handle, channel id `UC...`, or full URL) and return channel metadata + the latest `limit` videos with `video_id`, `title`, `duration`, `view_count`, `upload_date`. Hand the video ids straight to `process_video` / `process_playlist` for "что вышло на канале X на этой неделе" follow-ups.
- **`list_processed_videos()`** — list cached videos (newest first).
- **`get_processed_video(video_id, include_segments=False)`** — re-read a cached video from disk without hitting YouTube.
- **`search_transcript(video_id, query, max_results=10)`** — substring search over a cached transcript. Returns matched segments with `mm:ss` timestamps and clickable `youtu.be/<id>?t=<sec>s` URLs. Use for "where does he talk about X?" and exact-quote-with-citation tasks; cheaper than dumping the whole transcript back to the model.
- **`get_stats()`** — counts, unique channels, total duration, transcript word/char totals, last processed video, server version.
- **`get_metadata_backend()`** / **`set_metadata_backend(backend)`** — switch between `"standard"` (no OAuth — yt-dlp + oEmbed) and `"advanced"` (YouTube Data API v3 over OAuth, precise stats). Auto-detected based on whether `~/.config/yt-sub/client_secret.json` exists.
- **`get_whisper_backend()`** / **`set_whisper_backend(backend)`** / **`set_groq_api_key(key)`** — configure the transcript fallback used when a video has no subtitles. `"groq"` transcribes audio via Groq's Whisper API (requires API key, 25 MB upload cap → ~25 min audio). `"none"` (default) returns a `transcript_error` instead.
- **`get_cookies_browser()`** / **`set_cookies_browser(browser)`** — point yt-dlp at a browser's cookie jar (`"chrome" | "safari" | "firefox" | "brave" | "edge" | "chromium" | "arc"`), or pass null/empty to disable.
- **`get_cookies_file()`** / **`set_cookies_file(path)`** — point yt-dlp at a Netscape-format `cookies.txt`. Wins over browser cookies when both are set. Use this when browser cookies fail (Safari TCC sandbox, Chrome 130+ App-Bound Encryption, Firefox not installed).

## Workflow

1. Extract URL or video id from the user's message.
2. If the video is likely cached already, prefer `list_processed_videos` then `get_processed_video`. Otherwise call `process_video`. When the user is going to ask for quotes or timestamped highlights, set `include_segments=True` up front so you don't need a second round-trip.
3. Handle errors:
   - `{"error": "invalid_url", …}` — ask for a valid URL or 11-char video id.
   - `{"error": "not_signed_in", …}` — only happens in **advanced** metadata backend. Either tell the user to open the YT-sub tray app and choose **Sign in with Google**, or call `set_metadata_backend("standard")` to switch to the no-OAuth path and retry once.
4. If `transcript_error` is set, subtitles are not available. Two options:
   - **Proceed with metadata only** — say "(no transcript available)" and summarize from title/description/tags. Honest fallback for short or simple cases.
   - **Offer Whisper fallback** — if the user cares about the content (long lecture, talk, podcast without captions), call `get_whisper_backend()`. If it returns `"none"`, mention that they can enable Whisper transcription via Groq: `set_whisper_backend("groq")` + `set_groq_api_key("...")` (free key at https://console.groq.com/keys). After enabling, retry `process_video`. **Don't enable it silently — the audio is uploaded to a third party, the user should decide.**
5. Use `transcript_text` / `transcript_segments` for whatever the user asked.

## Output template for summaries

When the user asks for a summary, recap, or "о чём ролик", **default to this shape** (don't ask permission, just deliver it):

```
**[Title]** — [Channel] · [duration if known]

**TL;DR.** One or two sentences capturing the actual claim of the video.

**Key points**
- [[mm:ss](https://youtu.be/<id>?t=<sec>s)] — concrete idea, named tool, fact, or quoted line. Not a generic placeholder.
- [[mm:ss](https://youtu.be/<id>?t=<sec>s)] — next.
- … 5–10 bullets for a typical 15–40 min video; fewer for shorts, more for >1h.

**Takeaway / so what.** One line on why the video matters, who it's for, or what to do with it.
```

Rules:
- **Always link timestamps**, even if the user didn't explicitly ask — they make the summary verifiable and skimmable, which is the whole reason to use this tool over watching.
- Pick timestamps where the idea is actually *introduced*, not random hits — use `transcript_segments` and quote/paraphrase the segment near `start`.
- Use `mm:ss` for videos under an hour, `h:mm:ss` for longer.
- Keep bullets concrete and specific. "He discusses pricing" is dead weight; "Pricing tier breakdown — $9 hobby / $29 team / $99 ent" earns its row.
- If transcript is missing, fall back to metadata-only: title, channel, description, tags, duration. Say "(no transcript available)" once at the top and skip the timestamped bullets — don't fake them.

For follow-up questions about the same video in later turns, call `get_processed_video(video_id)` to skip the API calls, or `search_transcript(video_id, query)` to grab quotes on a specific topic.

## Bot-protection recovery (cookies)

`transcript_error` mentioning *"YouTube is blocking requests from your IP"*, *"Sign in to confirm you're not a bot"*, or *"Operation not permitted"* (Safari TCC) means YouTube refused the un-authenticated request.

1. Call `get_cookies_file()` and `get_cookies_browser()` to see the current state.
2. If neither is set, ask the user to either pick a cookies.txt via the tray (**Cookies for yt-dlp → Load cookies.txt…**) or call `set_cookies_browser("firefox")` if they have Firefox logged in. Point them at the README section *"Bypassing YouTube's bot-protection (cookies)"*: <https://github.com/BrezhnevEugen/yt-sub#bypassing-youtubes-bot-protection-cookies>.
3. After the user reports cookies are loaded, retry `process_video`. The cookies.txt path **overrides** browser source.
4. If it still fails, the cookies.txt is likely from a logged-out session — ask them to confirm they're signed in to YouTube in the browser before re-exporting.

Do **not** repeatedly retry `process_video` without changing state — every attempt counts toward the IP-block budget.

## Output dir layout

`~/YT-sub/output/<videoId>/`:
- `metadata.json` — full metadata response (shape depends on backend)
- `transcript.json` — timed segments (only if subtitles were available)
- `transcript.txt` — plain text
- `transcript.error.txt` — only if subtitles fetch failed (with the reason)
