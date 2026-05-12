"""Whisper transcription fallback for videos that have no YouTube subtitles.

Currently supports Groq's hosted Whisper (whisper-large-v3-turbo). Picked
over local faster-whisper / openai-whisper because it adds zero install
weight (a single HTTP call via httpx, which is already bundled) and is
fast — typically <5s for a ~10-min audio clip.

Trade-off: requires a Groq API key (set via the tray or
`set_groq_api_key` MCP tool) and the audio must fit under Groq's 25 MB
upload cap. We deliberately ask yt-dlp for low-bitrate audio (itag 139,
48 kbps m4a) to keep ~25 min videos comfortably under that limit.
"""
from __future__ import annotations

import ssl
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import certifi


GROQ_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
GROQ_FILE_LIMIT_BYTES = 25 * 1024 * 1024


class WhisperError(Exception):
    pass


def _download_audio(video_id: str, dest: Path) -> Path:
    """Download a low-bitrate audio track for video_id. Returns the file
    path. Raises WhisperError on any failure."""
    import yt_dlp

    from config import get_cookies_file, get_ytdlp_browser

    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_file = get_cookies_file()
    browser = get_ytdlp_browser()

    opts = {
        # 139 = m4a 48 kbps (smallest), 140 = m4a 128 kbps, then any.
        "format": "139/140/bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif browser:
        opts["cookiesfrombrowser"] = (browser,)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        raise WhisperError(f"audio download failed: {e}") from e

    files = sorted(dest.glob(f"{video_id}.*"))
    if not files:
        raise WhisperError("no audio file produced by yt-dlp")
    return files[0]


def _groq_post(audio_path: Path, api_key: str, model: str) -> Dict:
    import httpx

    size = audio_path.stat().st_size
    if size > GROQ_FILE_LIMIT_BYTES:
        raise WhisperError(
            f"audio is {size / 1e6:.1f} MB — exceeds Groq's 25 MB upload "
            f"cap. Try a shorter video or use a different backend."
        )

    ctx = ssl.create_default_context(cafile=certifi.where())
    with audio_path.open("rb") as fh:
        files = {"file": (audio_path.name, fh, "audio/mp4")}
        data = {
            "model": model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            with httpx.Client(timeout=300.0, verify=ctx) as client:
                r = client.post(
                    GROQ_TRANSCRIPTIONS_URL,
                    data=data,
                    files=files,
                    headers=headers,
                )
        except httpx.HTTPError as e:
            raise WhisperError(f"Groq request failed: {e}") from e

    if r.status_code != 200:
        raise WhisperError(f"Groq returned {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except Exception as e:
        raise WhisperError(f"Groq response parse failed: {e}") from e


def transcribe_with_groq(
    video_id: str, api_key: str, model: Optional[str] = None
) -> List[Dict]:
    """Download audio for video_id and transcribe via Groq Whisper.
    Returns the same {text, start, duration} segment shape as the
    youtube-transcript-api / yt-dlp paths. Raises WhisperError on failure."""
    if not api_key:
        raise WhisperError("Groq API key not configured")

    with tempfile.TemporaryDirectory() as tmpdir:
        audio = _download_audio(video_id, Path(tmpdir))
        body = _groq_post(audio, api_key, model or DEFAULT_GROQ_MODEL)

    segments = body.get("segments") or []
    out: List[Dict] = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        start = float(s.get("start", 0) or 0)
        end = float(s.get("end", start) or start)
        out.append(
            {
                "text": text,
                "start": start,
                "duration": max(0.0, end - start),
            }
        )
    if not out:
        raise WhisperError("Groq returned no segments")
    return out
