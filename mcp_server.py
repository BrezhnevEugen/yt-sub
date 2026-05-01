from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from stats import compute_stats
from storage import OUTPUT_DIR
from transcript import TranscriptError, fetch_transcript, parse_video_id
from youtube_client import AuthError, YouTubeClient

mcp = FastMCP("yt-sub")
_client = YouTubeClient()


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


@mcp.tool()
def process_video(
    url_or_id: str, include_segments: bool = False
) -> Dict[str, Any]:
    """
    Fetch metadata and transcript for a YouTube video and save them to
    ~/YT-sub/output/<videoId>/. Requires that the user has signed in via
    the YT-sub tray app (Sign in with Google).

    Args:
        url_or_id: A YouTube video URL (watch / youtu.be / shorts / embed) or 11-char video id.
        include_segments: If True, also return timed transcript segments in the response.

    Returns a dict with metadata fields, transcript_text, output_dir, and
    transcript_error if subtitles were unavailable.
    """
    try:
        video_id = parse_video_id(url_or_id)
    except ValueError as e:
        return {"error": "invalid_url", "message": str(e)}

    if not _client.is_authenticated():
        return {
            "error": "not_signed_in",
            "message": "Open the YT-sub tray app and use Sign in with Google.",
        }

    try:
        metadata = _client.fetch_metadata(video_id)
    except AuthError as e:
        return {"error": "auth", "message": str(e)}
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


@mcp.tool()
def get_stats() -> Dict[str, Any]:
    """
    Aggregate statistics over all videos cached under ~/YT-sub/output/:
    counts, unique channels, total video duration, transcript word/char totals,
    and the most recently processed video.
    """
    return compute_stats()


if __name__ == "__main__":
    mcp.run()
