from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)


class TranscriptError(Exception):
    pass


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PREFERRED_LANGS: Tuple[str, ...] = ("ru-orig", "ru", "en-orig", "en")


def parse_video_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if _VIDEO_ID_RE.match(s):
        return s

    try:
        u = urlparse(s)
    except ValueError as e:
        raise ValueError(f"Cannot parse URL: {e}") from e

    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    vid = ""
    if host == "youtu.be":
        vid = u.path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if u.path == "/watch":
            vid = parse_qs(u.query).get("v", [""])[0]
        else:
            parts = u.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
                vid = parts[1]

    if not _VIDEO_ID_RE.match(vid):
        raise ValueError(f"Cannot extract video id from: {url_or_id}")
    return vid


def _try_primary(video_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    api = YouTubeTranscriptApi()
    try:
        return api.fetch(video_id, languages=_PREFERRED_LANGS).to_raw_data(), None
    except NoTranscriptFound:
        pass
    except (TranscriptsDisabled, VideoUnavailable) as e:
        return None, type(e).__name__
    except Exception as e:
        return None, f"primary: {e}"

    try:
        transcripts = api.list(video_id)
    except Exception as e:
        return None, f"list: {e}"

    pools = (
        [t for t in transcripts if not t.is_generated],
        [t for t in transcripts if t.is_generated],
    )
    last_err = "no transcripts found"
    for pool in pools:
        for t in pool:
            try:
                return t.fetch().to_raw_data(), None
            except Exception as e:
                last_err = f"fetch {t.language_code}: {e}"
                continue
    return None, last_err


def _parse_json3(text: str) -> List[Dict]:
    data = json.loads(text)
    out: List[Dict] = []
    for ev in data.get("events", []) or []:
        segs = ev.get("segs")
        if not segs:
            continue
        line = "".join(s.get("utf8", "") for s in segs).strip()
        if not line:
            continue
        out.append(
            {
                "text": line,
                "start": (ev.get("tStartMs", 0) or 0) / 1000.0,
                "duration": (ev.get("dDurationMs", 0) or 0) / 1000.0,
            }
        )
    return out


def _try_ytdlp(video_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    import yt_dlp

    from config import get_cookies_file, get_ytdlp_browser

    url = f"https://www.youtube.com/watch?v={video_id}"
    cookies_file = get_cookies_file()
    browser = get_ytdlp_browser()

    # Step 1: discover what subtitle languages this video actually has,
    # so we ask yt-dlp to download exactly one language. Otherwise it
    # tries each lang in subtitleslangs and a single 429 from the
    # timedtext endpoint on a missing language can poison the request.
    discover_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignore_no_formats_error": True,
        "allow_unplayable_formats": True,
    }
    if cookies_file:
        discover_opts["cookiefile"] = cookies_file
    elif browser:
        discover_opts["cookiesfrombrowser"] = (browser,)

    try:
        with yt_dlp.YoutubeDL(discover_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return None, f"yt-dlp discover: {e}"

    manual_langs = set((info.get("subtitles") or {}).keys())
    auto_langs = set((info.get("automatic_captions") or {}).keys())
    chosen_lang: Optional[str] = None
    for lang in _PREFERRED_LANGS:
        if lang in manual_langs or lang in auto_langs:
            chosen_lang = lang
            break
    if not chosen_lang:
        # Take any available language as last resort.
        any_lang = next(iter(manual_langs), None) or next(iter(auto_langs), None)
        if not any_lang:
            return None, "yt-dlp: no subtitles available for this video"
        chosen_lang = any_lang

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [chosen_lang],
            "subtitlesformat": "json3",
            "outtmpl": str(tmp / "%(id)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            # We only want subtitles. Without this flag yt-dlp still
            # runs video-format selection and aborts with "Requested
            # format is not available" on some videos (region/age
            # restrictions, cookies for a different locale, etc).
            "ignore_no_formats_error": True,
            "allow_unplayable_formats": True,
            # YouTube serves subtitles from a separate timedtext endpoint
            # that rate-limits aggressively. Retry with exponential
            # backoff on HTTP 429.
            "retries": 10,
            "fragment_retries": 10,
            "retry_sleep_functions": {
                "http": lambda n: min(2 ** n, 30),
                "fragment": lambda n: min(2 ** n, 30),
            },
            # Browser-y User-Agent so the timedtext request looks like
            # YouTube's own player rather than a CLI tool.
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Safari/605.1.15"
                ),
            },
        }
        # cookies.txt path wins over browser-cookies — browsers on macOS
        # often hit TCC sandboxing (Safari) or App-Bound Encryption
        # (Chrome 130+), while a manually exported cookies.txt always
        # works.
        if cookies_file:
            opts["cookiefile"] = cookies_file
        elif browser:
            opts["cookiesfrombrowser"] = (browser,)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            return None, f"yt-dlp: {e}"

        # Look for the chosen language first, then any fallback.
        candidates = (
            f"*.{chosen_lang}.json3",
            f"*.{chosen_lang}-*.json3",
        )
        for pattern in candidates:
            for f in sorted(tmp.glob(pattern)):
                try:
                    return _parse_json3(f.read_text(encoding="utf-8")), None
                except Exception as e:
                    return None, f"yt-dlp parse {f.name}: {e}"
        for f in sorted(tmp.glob("*.json3")):
            try:
                return _parse_json3(f.read_text(encoding="utf-8")), None
            except Exception as e:
                return None, f"yt-dlp parse {f.name}: {e}"

    return None, "yt-dlp: no subtitles in preferred languages"


def _try_whisper(video_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Last-resort fallback: download audio and transcribe via a Whisper
    backend. Returns (segments, None) on success, (None, reason) on
    failure or if disabled."""
    from config import get_groq_api_key, get_whisper_backend

    backend = get_whisper_backend()
    if backend == "none":
        return None, "disabled"
    if backend == "groq":
        key = get_groq_api_key()
        if not key:
            return None, "groq: no api key (set GROQ_API_KEY or use the tray)"
        try:
            from whisper_client import transcribe_with_groq
        except Exception as e:
            return None, f"whisper import: {e}"
        try:
            return transcribe_with_groq(video_id, key), None
        except Exception as e:
            return None, f"groq: {e}"
    return None, f"unknown whisper backend: {backend}"


def fetch_transcript(video_id: str) -> List[Dict]:
    primary, primary_err = _try_primary(video_id)
    if primary is not None:
        return primary

    # Video is gone / region-blocked / private — no point downloading audio.
    if primary_err == "VideoUnavailable":
        raise TranscriptError(primary_err)

    # yt-dlp subtitle path makes sense unless we already know the channel
    # disabled captions entirely.
    ytdlp_err: Optional[str]
    if primary_err == "TranscriptsDisabled":
        ytdlp_err = "skipped: TranscriptsDisabled"
    else:
        ytdlp, ytdlp_err = _try_ytdlp(video_id)
        if ytdlp is not None:
            return ytdlp

    whisper_segments, whisper_err = _try_whisper(video_id)
    if whisper_segments is not None:
        return whisper_segments

    raise TranscriptError(
        f"primary={primary_err or 'unknown'} | "
        f"yt-dlp={ytdlp_err or 'failed'} | "
        f"whisper={whisper_err or 'disabled'}"
    )
