from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

import config
from stats import compute_stats
from storage import OUTPUT_DIR
from transcript import TranscriptError, fetch_transcript, parse_video_id
from version import __version__
from web_metadata import fetch_metadata_web
from youtube_client import AuthError, YouTubeClient

mcp = FastMCP("yt-sub")


def _client() -> YouTubeClient:
    """Always read the latest token from disk. The MCP subprocess can be
    long-lived; the user may sign in/out via the tray app at any time."""
    return YouTubeClient()


def _summarize(metadata: Dict[str, Any]) -> Dict[str, Any]:
    snippet = metadata.get("snippet", {}) or {}
    content = metadata.get("contentDetails", {}) or {}
    stats = metadata.get("statistics", {}) or {}
    return {
        "video_id": metadata.get("id"),
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "channel_id": snippet.get("channelId"),
        "published_at": snippet.get("publishedAt"),
        "description": snippet.get("description"),
        "tags": snippet.get("tags", []),
        "category_id": snippet.get("categoryId"),
        "default_language": snippet.get("defaultLanguage")
        or snippet.get("defaultAudioLanguage"),
        "duration": content.get("duration"),
        "view_count": stats.get("viewCount"),
        "like_count": stats.get("likeCount"),
        "comment_count": stats.get("commentCount"),
    }


def _process_video_by_id(
    video_id: str, include_segments: bool = False
) -> Dict[str, Any]:
    """Internal: video_id already validated. Same return shape as
    process_video(). Shared by process_video and process_playlist."""
    backend = config.get_metadata_backend()
    if backend == "advanced":
        client = _client()
        if not client.is_authenticated():
            return {
                "error": "not_signed_in",
                "message": (
                    "Advanced metadata backend (YouTube Data API) requires "
                    "OAuth. Either sign in via the YT-sub tray app, or "
                    "switch to standard metadata via "
                    "set_metadata_backend(\"standard\")."
                ),
            }
        try:
            metadata = client.fetch_metadata(video_id)
        except AuthError as e:
            return {"error": "auth", "message": str(e)}
        except Exception as e:
            return {"error": "metadata_fetch_failed", "message": str(e)}
    else:
        try:
            metadata = fetch_metadata_web(video_id)
        except Exception as e:
            return {"error": "metadata_fetch_failed", "message": str(e)}

    transcript: Optional[List[Dict[str, Any]]] = None
    transcript_error: Optional[str] = None
    try:
        transcript = fetch_transcript(video_id)
    except TranscriptError as e:
        transcript_error = str(e)

    out_dir = OUTPUT_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    transcript_text: Optional[str] = None
    if transcript is not None:
        (out_dir / "transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        transcript_text = "\n".join(seg.get("text", "") for seg in transcript)
        (out_dir / "transcript.txt").write_text(transcript_text, encoding="utf-8")
    else:
        (out_dir / "transcript.error.txt").write_text(
            transcript_error or "unknown", encoding="utf-8"
        )

    result = _summarize(metadata)
    result["output_dir"] = str(out_dir)
    result["transcript_text"] = transcript_text
    result["transcript_error"] = transcript_error
    if include_segments and transcript is not None:
        result["transcript_segments"] = transcript
    return result


@mcp.tool()
def process_video(
    url_or_id: str, include_segments: bool = False
) -> Dict[str, Any]:
    """
    Fetch metadata and transcript for a YouTube video and save them to
    ~/YT-sub/output/<videoId>/.

    Args:
        url_or_id: A YouTube video URL (watch / youtu.be / shorts / embed) or 11-char video id.
        include_segments: If True, also return timed transcript segments in the response.

    Returns a dict with metadata fields, transcript_text, output_dir, and
    transcript_error if subtitles were unavailable. With the 'standard'
    metadata backend (default in v0.1.13+), no Google sign-in is required.
    """
    try:
        video_id = parse_video_id(url_or_id)
    except ValueError as e:
        return {"error": "invalid_url", "message": str(e)}
    return _process_video_by_id(video_id, include_segments)


_LIST_SPLIT_RE = re.compile(r"[\s,]+")


def _parse_playlist_input(s: str, limit: int) -> Tuple[Optional[str], List[str]]:
    """Returns (playlist_url_to_resolve_or_None, list_of_direct_video_ids)."""
    s = (s or "").strip()
    if not s:
        return None, []
    parts = [p.strip() for p in _LIST_SPLIT_RE.split(s) if p.strip()]
    if len(parts) > 1:
        ids: List[str] = []
        for p in parts:
            try:
                ids.append(parse_video_id(p))
            except ValueError:
                continue
        return None, ids[:limit]
    if "/playlist" in s or "list=" in s:
        return s, []
    try:
        return None, [parse_video_id(s)]
    except ValueError:
        return s, []


def _resolve_playlist(playlist_url: str, limit: int) -> Tuple[List[str], Optional[str]]:
    import yt_dlp

    cookies_file = config.get_cookies_file()
    browser = config.get_ytdlp_browser()
    opts: Dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "playlistend": limit,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
    except Exception as e:
        return [], f"playlist fetch failed: {e}"
    entries = info.get("entries") or []
    ids: List[str] = []
    for e in entries[:limit]:
        vid = (e or {}).get("id")
        if vid and len(vid) == 11:
            ids.append(vid)
    return ids, None


@mcp.tool()
def process_playlist(
    playlist: str, limit: int = 50, skip_cached: bool = True
) -> Dict[str, Any]:
    """
    Process a YouTube playlist or a list of video URLs in one shot. Each
    resolved video is run through process_video (metadata + transcript,
    saved under ~/YT-sub/output/<videoId>/). Already-cached videos are
    skipped by default so re-running on the same playlist is cheap.

    Args:
        playlist: One of:
            - A playlist URL (`youtube.com/playlist?list=...`).
            - A watch URL with `&list=...`.
            - A newline- or comma-separated list of video URLs / 11-char ids.
            - A single video URL / id (degenerate case, equivalent to process_video).
        limit: Max videos to process (default 50, hard cap 200).
        skip_cached: If True (default), skip videos that already have
                     metadata.json on disk.

    Returns:
        {total, processed_count, skipped_count, failed_count,
         processed: [{video_id, title, has_transcript}, ...],
         skipped_cached: [{video_id}, ...],
         failed: [{video_id, error}, ...]}.
    """
    limit = max(1, min(int(limit or 50), 200))
    playlist_url, direct_ids = _parse_playlist_input(playlist, limit)
    if direct_ids:
        ids = direct_ids
    elif playlist_url:
        ids, err = _resolve_playlist(playlist_url, limit)
        if err:
            return {"error": "playlist_resolve_failed", "message": err}
    else:
        return {"error": "no_videos", "message": "No video ids resolved from input"}

    if not ids:
        return {"error": "no_videos", "message": "Playlist had no resolvable video ids"}

    processed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    failed: List[Dict[str, str]] = []
    for vid in ids:
        out_dir = OUTPUT_DIR / vid
        if skip_cached and (out_dir / "metadata.json").exists():
            skipped.append({"video_id": vid})
            continue
        try:
            r = _process_video_by_id(vid, include_segments=False)
        except Exception as e:
            failed.append({"video_id": vid, "error": str(e)})
            continue
        if "error" in r:
            failed.append(
                {
                    "video_id": vid,
                    "error": r.get("message") or r.get("error") or "unknown",
                }
            )
        else:
            processed.append(
                {
                    "video_id": vid,
                    "title": r.get("title"),
                    "has_transcript": r.get("transcript_text") is not None,
                }
            )

    return {
        "total": len(ids),
        "processed_count": len(processed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "processed": processed,
        "skipped_cached": skipped,
        "failed": failed,
    }


def _resolve_channel_url(channel: str) -> str:
    s = (channel or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return s if s.rstrip("/").endswith("/videos") else s.rstrip("/") + "/videos"
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}/videos"
    if s.startswith("UC") and len(s) >= 20:
        return f"https://www.youtube.com/channel/{s}/videos"
    return f"https://www.youtube.com/@{s}/videos"


@mcp.tool()
def get_channel_info(channel: str, limit: int = 10) -> Dict[str, Any]:
    """
    Resolve a YouTube channel and return channel metadata + its latest videos.
    Uses yt-dlp's flat extractor, so this works under both 'standard' and
    'advanced' metadata backends.

    Args:
        channel: handle (`@mkbhd`), bare handle (`mkbhd`), channel id
                 (`UC...`), or full channel URL.
        limit: cap on recent videos to list (default 10, hard cap 50).

    Returns:
        {channel_id, title, description, channel_url, videos_returned,
         videos: [{video_id, title, duration, view_count, upload_date, url}, ...]}
        or {error, message}.
    """
    import yt_dlp

    limit = max(1, min(int(limit or 10), 50))
    url = _resolve_channel_url(channel)
    if not url:
        return {"error": "invalid_channel", "message": "channel argument is empty"}

    cookies_file = config.get_cookies_file()
    browser = config.get_ytdlp_browser()
    opts: Dict[str, Any] = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "playlistend": limit,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"error": "channel_fetch_failed", "message": str(e)}

    entries = info.get("entries") or []
    videos: List[Dict[str, Any]] = []
    for e in entries[:limit]:
        e = e or {}
        vid = e.get("id")
        if not vid or len(vid) != 11:
            continue
        videos.append(
            {
                "video_id": vid,
                "title": e.get("title"),
                "duration": e.get("duration"),
                "view_count": e.get("view_count"),
                "upload_date": e.get("upload_date"),
                "url": f"https://youtu.be/{vid}",
            }
        )

    return {
        "channel_id": info.get("channel_id") or info.get("uploader_id"),
        "title": info.get("channel") or info.get("uploader") or info.get("title"),
        "description": info.get("description"),
        "channel_url": info.get("channel_url") or url.rstrip("/").rsplit("/videos", 1)[0],
        "videos_returned": len(videos),
        "videos": videos,
    }


@mcp.tool()
def list_processed_videos() -> List[Dict[str, Any]]:
    """List videos previously processed and cached under ~/YT-sub/output/."""
    items: List[Dict[str, Any]] = []
    if not OUTPUT_DIR.exists():
        return items
    for d in sorted(
        OUTPUT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        snippet = metadata.get("snippet", {}) or {}
        items.append(
            {
                "video_id": d.name,
                "title": snippet.get("title"),
                "channel": snippet.get("channelTitle"),
                "processed_at": datetime.fromtimestamp(
                    d.stat().st_mtime
                ).isoformat(),
                "has_transcript": (d / "transcript.txt").exists(),
            }
        )
    return items


@mcp.tool()
def get_processed_video(
    video_id: str, include_segments: bool = False
) -> Dict[str, Any]:
    """
    Read a previously processed video from disk without re-fetching from YouTube.

    Args:
        video_id: 11-char YouTube video id (the directory name under ~/YT-sub/output/).
        include_segments: If True, include timed transcript segments in the response.
    """
    out_dir = OUTPUT_DIR / video_id
    meta_path = out_dir / "metadata.json"
    if not meta_path.exists():
        return {
            "error": "not_found",
            "message": f"No saved data for video_id={video_id}",
        }

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    result = _summarize(metadata)
    result["output_dir"] = str(out_dir)

    txt = out_dir / "transcript.txt"
    result["transcript_text"] = (
        txt.read_text(encoding="utf-8") if txt.exists() else None
    )
    if include_segments:
        seg = out_dir / "transcript.json"
        if seg.exists():
            result["transcript_segments"] = json.loads(
                seg.read_text(encoding="utf-8")
            )
    err = out_dir / "transcript.error.txt"
    if err.exists():
        result["transcript_error"] = err.read_text(encoding="utf-8")
    return result


def _format_timestamp(seconds: float) -> str:
    secs = int(seconds)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@mcp.tool()
def search_transcript(
    video_id: str, query: str, max_results: int = 10
) -> Dict[str, Any]:
    """
    Search a cached transcript for a substring and return matching segments
    with mm:ss timestamps and ready-to-click youtu.be/<id>?t=<sec>s URLs.

    Use this to answer questions like "where does he talk about X?", to
    find exact quotes with citations, or to build a clickable index for a
    long video without re-summarizing the whole thing.

    Args:
        video_id: 11-char YouTube video id (the cache directory name under
                  ~/YT-sub/output/). Call `process_video` first if the video
                  has not been processed yet.
        query: case-insensitive substring to look for inside segment text.
        max_results: cap on returned matches (default 10).

    Returns:
        {video_id, query, match_count, matches: [{start, timestamp, url, text}, ...]}
        or {error: "not_processed" | "empty_query" | "read_failed", message}.
    """
    out_dir = OUTPUT_DIR / video_id
    seg_path = out_dir / "transcript.json"
    if not seg_path.exists():
        return {
            "error": "not_processed",
            "message": (
                f"No cached transcript for video_id={video_id}. "
                "Call process_video first."
            ),
        }
    try:
        segments = json.loads(seg_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": "read_failed", "message": str(e)}

    q = (query or "").strip().lower()
    if not q:
        return {"error": "empty_query", "message": "query must be non-empty"}

    matches: List[Dict[str, Any]] = []
    for seg in segments:
        text = seg.get("text", "") or ""
        if q not in text.lower():
            continue
        start = float(seg.get("start", 0) or 0)
        matches.append(
            {
                "start": start,
                "timestamp": _format_timestamp(start),
                "url": f"https://youtu.be/{video_id}?t={int(start)}s",
                "text": text,
            }
        )
        if len(matches) >= max_results:
            break

    return {
        "video_id": video_id,
        "query": query,
        "match_count": len(matches),
        "matches": matches,
    }


@mcp.tool()
def set_cookies_browser(browser: Optional[str] = None) -> Dict[str, Any]:
    """
    Configure which browser yt-dlp should read cookies from. Use this when
    `process_video` returns a transcript_error mentioning IP block, "Sign
    in to confirm you're not a bot", or similar — passing real browser
    cookies makes YouTube treat requests as a logged-in session.

    Args:
        browser: One of "chrome", "safari", "firefox", "brave", "edge",
                 "chromium", "arc". Pass null/empty to disable cookies and
                 fall back to anonymous fetching.

    The setting is persisted at ~/.config/yt-sub/config.json and is shared
    between this MCP server and the YT-sub tray app.
    """
    norm = (browser or "").strip().lower() or None
    if norm and norm not in config.SUPPORTED_BROWSERS:
        return {
            "error": "unsupported_browser",
            "message": f"Use one of: {', '.join(config.SUPPORTED_BROWSERS)} or null to disable.",
            "supported": list(config.SUPPORTED_BROWSERS),
        }
    config.set_ytdlp_browser(norm)
    return {
        "ok": True,
        "ytdlp_browser": norm,
        "message": (
            f"yt-dlp will now read cookies from {norm}."
            if norm else
            "yt-dlp cookies disabled."
        ),
    }


@mcp.tool()
def get_cookies_browser() -> Dict[str, Any]:
    """
    Return which browser yt-dlp is currently configured to read cookies
    from (or null if disabled), plus the list of supported browser names.
    """
    return {
        "ytdlp_browser": config.get_ytdlp_browser(),
        "supported": list(config.SUPPORTED_BROWSERS),
    }


@mcp.tool()
def set_cookies_file(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Point yt-dlp at a Netscape-format cookies.txt file (export from a
    browser via "Get cookies.txt LOCALLY" extension or similar). The
    file is copied into ~/.config/yt-sub/cookies.txt and survives the
    original being deleted. A configured file overrides the browser
    cookies setting. Pass null/empty to clear.

    Use this when browser cookies are unavailable: macOS TCC sandbox
    blocks Safari, Chrome 130+ uses App-Bound Encryption that yt-dlp
    can't decrypt, and Firefox may not be installed.

    Args:
        path: Absolute path to a cookies.txt on disk, or null to clear.
    """
    import shutil

    if not path:
        config.set_cookies_file(None)
        if config.MANAGED_COOKIES_FILE.exists():
            try:
                config.MANAGED_COOKIES_FILE.unlink()
            except Exception:
                pass
        return {"ok": True, "cookies_file": None}

    src = Path(path).expanduser()
    if not src.exists() or not src.is_file():
        return {
            "error": "not_found",
            "message": f"{src} does not exist or is not a file",
        }
    config.MANAGED_COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, config.MANAGED_COOKIES_FILE)
    config.set_cookies_file(str(config.MANAGED_COOKIES_FILE))
    return {
        "ok": True,
        "cookies_file": str(config.MANAGED_COOKIES_FILE),
        "message": "yt-dlp will now use this file (overrides browser cookies)",
    }


@mcp.tool()
def get_cookies_file() -> Dict[str, Any]:
    """Return the active cookies.txt path yt-dlp is using, or null."""
    return {"cookies_file": config.get_cookies_file()}


@mcp.tool()
def set_metadata_backend(backend: Optional[str] = None) -> Dict[str, Any]:
    """
    Switch which path fetches video metadata.
      "standard" — no Google OAuth required. Uses yt-dlp's extract_info
                   when cookies are available (full metadata), and falls
                   back to YouTube's public oEmbed endpoint (title +
                   channel + thumbnail, always works) otherwise.
      "advanced" — full YouTube Data API v3 over OAuth: precise view /
                   like / comment counts, structured topicDetails,
                   tags, status, etc. Requires the client_secret.json /
                   sign-in flow.
    Pass null/empty to clear the override and let YT-sub auto-detect
    based on whether OAuth credentials exist.
    """
    norm = (backend or "").strip().lower() or None
    if norm and norm not in config.METADATA_BACKENDS:
        return {
            "error": "unsupported_backend",
            "message": f"Use {' / '.join(config.METADATA_BACKENDS)} or null to auto-detect.",
            "supported": list(config.METADATA_BACKENDS),
        }
    config.set_metadata_backend(norm)
    return {
        "ok": True,
        "metadata_backend": config.get_metadata_backend(),
        "explicit": norm is not None,
    }


@mcp.tool()
def get_metadata_backend() -> Dict[str, Any]:
    """Return the active metadata backend (`standard` or `advanced`)
    and the list of supported values."""
    return {
        "metadata_backend": config.get_metadata_backend(),
        "supported": list(config.METADATA_BACKENDS),
    }


@mcp.tool()
def set_whisper_backend(backend: Optional[str] = None) -> Dict[str, Any]:
    """
    Configure the transcript fallback used when a video has no subtitles.

      "groq" — transcribe audio via Groq's Whisper API
               (whisper-large-v3-turbo). Requires a Groq API key (see
               `set_groq_api_key`). 25 MB upload cap on Groq's side; we
               request low-bitrate audio so ~25 min videos fit.
      "none" — disable fallback (default). Videos without captions return
               a `transcript_error`.

    Pass null/empty to clear (same as "none").
    """
    norm = (backend or "").strip().lower() or "none"
    if norm not in config.WHISPER_BACKENDS:
        return {
            "error": "unsupported_whisper_backend",
            "message": f"Use {' / '.join(config.WHISPER_BACKENDS)}",
            "supported": list(config.WHISPER_BACKENDS),
        }
    config.set_whisper_backend(None if norm == "none" else norm)
    return {
        "ok": True,
        "whisper_backend": config.get_whisper_backend(),
        "groq_api_key_set": bool(config.get_groq_api_key()),
    }


@mcp.tool()
def get_whisper_backend() -> Dict[str, Any]:
    """Return the active whisper fallback backend (`none` or `groq`),
    whether a Groq API key is currently configured, and the list of
    supported values."""
    return {
        "whisper_backend": config.get_whisper_backend(),
        "groq_api_key_set": bool(config.get_groq_api_key()),
        "supported": list(config.WHISPER_BACKENDS),
    }


@mcp.tool()
def set_groq_api_key(key: Optional[str] = None) -> Dict[str, Any]:
    """
    Set or clear the Groq API key used by the 'groq' whisper backend.
    Persisted at ~/.config/yt-sub/config.json. Pass null/empty to clear.

    Get a free key at https://console.groq.com/keys — generous free tier
    covers many hours of audio per day.

    For security, the key is never returned by `get_whisper_backend`; only
    a boolean `groq_api_key_set` flag is exposed.
    """
    config.set_groq_api_key((key or "").strip() or None)
    return {"ok": True, "groq_api_key_set": bool(config.get_groq_api_key())}


@mcp.tool()
def get_stats() -> Dict[str, Any]:
    """
    Aggregate statistics over all videos cached under ~/YT-sub/output/:
    counts, unique channels, total video duration, transcript word/char totals,
    and the most recently processed video. Also includes the YT-sub server
    version.
    """
    s = compute_stats()
    s["version"] = __version__
    return s


@mcp.tool()
def get_version() -> Dict[str, str]:
    """Return the YT-sub MCP server version string."""
    return {"version": __version__}


if __name__ == "__main__":
    mcp.run()
