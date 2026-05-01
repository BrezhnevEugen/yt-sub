from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict

from storage import OUTPUT_DIR

_DUR = re.compile(r"^P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _iso_to_secs(s: str) -> int:
    if not s:
        return 0
    m = _DUR.match(s)
    if not m:
        return 0
    h, mn, sec = m.groups()
    return int(h or 0) * 3600 + int(mn or 0) * 60 + int(sec or 0)


def _fmt_secs(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{h}h {m}m"
    if m >= 1:
        return f"{m}m {s}s"
    return f"{s}s"


def compute_stats() -> Dict[str, Any]:
    base = {
        "videos": 0,
        "with_transcript": 0,
        "channels": 0,
        "total_duration_secs": 0,
        "total_duration_human": "0s",
        "transcript_chars": 0,
        "transcript_words": 0,
        "last_title": None,
        "last_channel": None,
        "last_processed_at": None,
    }
    if not OUTPUT_DIR.exists():
        return base

    channels: set = set()
    last_mtime = 0.0

    for d in OUTPUT_DIR.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        base["videos"] += 1
        snippet = meta.get("snippet", {}) or {}
        content = meta.get("contentDetails", {}) or {}
        if snippet.get("channelTitle"):
            channels.add(snippet["channelTitle"])
        base["total_duration_secs"] += _iso_to_secs(content.get("duration", ""))

        txt_path = d / "transcript.txt"
        if txt_path.exists():
            base["with_transcript"] += 1
            t = txt_path.read_text(encoding="utf-8", errors="ignore")
            base["transcript_chars"] += len(t)
            base["transcript_words"] += len(t.split())

        mtime = meta_path.stat().st_mtime
        if mtime > last_mtime:
            last_mtime = mtime
            base["last_title"] = snippet.get("title")
            base["last_channel"] = snippet.get("channelTitle")

    base["channels"] = len(channels)
    base["total_duration_human"] = _fmt_secs(base["total_duration_secs"])
    if last_mtime:
        base["last_processed_at"] = datetime.fromtimestamp(last_mtime).isoformat(
            timespec="seconds"
        )
    return base


def format_stats(stats: Dict[str, Any]) -> str:
    lines = [
        f"Videos processed: {stats['videos']}",
        f"With transcript: {stats['with_transcript']}",
        f"Unique channels: {stats['channels']}",
        f"Total video duration: {stats['total_duration_human']}",
        f"Transcript words: {stats['transcript_words']:,}",
        f"Transcript characters: {stats['transcript_chars']:,}",
    ]
    if stats.get("last_title"):
        lines.append("")
        lines.append(f"Last: {stats['last_title']}")
        if stats.get("last_channel"):
            lines.append(f"Channel: {stats['last_channel']}")
        if stats.get("last_processed_at"):
            lines.append(f"At: {stats['last_processed_at']}")
    return "\n".join(lines)
