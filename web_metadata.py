"""Metadata sources that don't require Google OAuth.

`fetch_metadata_web(video_id)` returns a dict shaped like the YouTube
Data API v3 `videos.list` response (subset of fields), so downstream
code (`mcp_server.process_video`, the tray app) can use it as a
drop-in replacement for `YouTubeClient.fetch_metadata`.

Strategy:
  1. Try yt-dlp's `extract_info` — if cookies are configured this gives
     us the rich metadata (description, duration, view/like/comment
     counts, tags, chapters, language). If yt-dlp hits YouTube's
     bot-protection without cookies, this raises and we fall through.
  2. Fall back to YouTube's `oembed` endpoint — public, no auth, never
     bot-blocked, but only returns title, author, and thumbnail.

Either way the caller gets a usable metadata dict. Less rich than the
Data API but enough for an agent to reason about the video.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _ssl_context() -> ssl.SSLContext:
    """Frozen Python (py2app) has no system trust store, so use certifi
    which is already a transitive dep via requests/google-auth."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _seconds_to_iso8601_duration(secs: int) -> str:
    """123 → 'PT2M3S'. Matches what the Data API returns under
    contentDetails.duration."""
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    out = "PT"
    if h:
        out += f"{h}H"
    if m:
        out += f"{m}M"
    if s or (not h and not m):
        out += f"{s}S"
    return out


def _ytdlp_upload_date_to_iso(d: Optional[str]) -> Optional[str]:
    """yt-dlp returns YYYYMMDD with no time. We only get a date so we
    pin it at midnight UTC, same calendar day. Better than null."""
    if not d or len(d) != 8:
        return None
    try:
        dt = datetime(int(d[0:4]), int(d[4:6]), int(d[6:8]), tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _convert_thumbnails(thumbs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """yt-dlp thumbnails list → Data API thumbnails dict shape."""
    out: Dict[str, Any] = {}
    name_for_size = [
        ("default", 120),
        ("medium", 320),
        ("high", 480),
        ("standard", 640),
        ("maxres", 1280),
    ]
    chosen: List[Optional[Dict[str, Any]]] = [None] * len(name_for_size)
    for t in thumbs or []:
        w = t.get("width") or 0
        if not w:
            continue
        for i, (_name, target) in enumerate(name_for_size):
            existing = chosen[i]
            if w >= target and (
                existing is None or w < (existing.get("width") or 0)
            ):
                chosen[i] = t
    for (name, _t), entry in zip(name_for_size, chosen):
        if entry:
            out[name] = {
                "url": entry.get("url"),
                "width": entry.get("width"),
                "height": entry.get("height"),
            }
    return out


def _ytdlp_to_api_shape(info: Dict[str, Any]) -> Dict[str, Any]:
    """yt-dlp's extract_info dict → YouTube Data API v3 videos.list shape.
    Fields not present in yt-dlp output are simply omitted."""
    snippet = {
        "title": info.get("title"),
        "description": info.get("description") or "",
        "channelTitle": info.get("channel") or info.get("uploader"),
        "channelId": info.get("channel_id") or info.get("uploader_id"),
        "publishedAt": _ytdlp_upload_date_to_iso(info.get("upload_date")),
        "tags": info.get("tags") or [],
        "categoryId": (info.get("categories") or [None])[0],
        "defaultLanguage": info.get("language"),
        "thumbnails": _convert_thumbnails(info.get("thumbnails") or []),
    }
    content = {"duration": _seconds_to_iso8601_duration(info.get("duration") or 0)}
    stats = {}
    if info.get("view_count") is not None:
        stats["viewCount"] = str(info["view_count"])
    if info.get("like_count") is not None:
        stats["likeCount"] = str(info["like_count"])
    if info.get("comment_count") is not None:
        stats["commentCount"] = str(info["comment_count"])
    return {
        "id": info.get("id"),
        "snippet": snippet,
        "contentDetails": content,
        "statistics": stats,
        "_source": "ytdlp",
    }


def _oembed_to_api_shape(video_id: str, oembed: Dict[str, Any]) -> Dict[str, Any]:
    """The oembed payload only has title/author/thumbnail. Fields we
    don't have are intentionally absent from the returned dict so
    downstream code can detect 'standard fallback, no description' via
    `metadata['_source'] == 'oembed'`."""
    return {
        "id": video_id,
        "snippet": {
            "title": oembed.get("title"),
            "channelTitle": oembed.get("author_name"),
            # oembed gives author_url like https://www.youtube.com/@handle
            "channelId": None,
            "channelUrl": oembed.get("author_url"),
            "description": "",
            "thumbnails": {
                "default": {
                    "url": oembed.get("thumbnail_url"),
                    "width": oembed.get("thumbnail_width"),
                    "height": oembed.get("thumbnail_height"),
                }
            } if oembed.get("thumbnail_url") else {},
        },
        "contentDetails": {},
        "statistics": {},
        "_source": "oembed",
    }


def _fetch_oembed(video_id: str) -> Dict[str, Any]:
    url = (
        "https://www.youtube.com/oembed?"
        + urllib.parse.urlencode({
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "format": "json",
        })
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; YT-sub)"},
    )
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
        return json.load(resp)


def _fetch_ytdlp(video_id: str) -> Dict[str, Any]:
    import yt_dlp

    from config import get_cookies_file, get_ytdlp_browser

    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignore_no_formats_error": True,
        "allow_unplayable_formats": True,
    }
    cookies_file = get_cookies_file()
    browser = get_ytdlp_browser()
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)

    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def fetch_metadata_web(video_id: str) -> Dict[str, Any]:
    """Try yt-dlp first (rich metadata when cookies are around), fall
    back to oembed (lean but always works). Raises only if both fail."""
    ytdlp_err: Optional[str] = None
    try:
        info = _fetch_ytdlp(video_id)
        return _ytdlp_to_api_shape(info)
    except Exception as e:
        ytdlp_err = str(e)

    try:
        oembed = _fetch_oembed(video_id)
        result = _oembed_to_api_shape(video_id, oembed)
        if ytdlp_err:
            result["_ytdlp_error"] = ytdlp_err
        return result
    except Exception as e:
        raise RuntimeError(
            f"web metadata failed: ytdlp={ytdlp_err}; oembed={e}"
        ) from e
